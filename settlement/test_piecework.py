from decimal import Decimal
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.test import TestCase

from config.testing import at, make_employee, make_single_dispatch, report_single
from dispatch.models import Dispatch
from masterdata.models import Employee
from reporting.models import WorkSession
from settlement.models import PieceworkAllocation, SettlementPeriod
from settlement.piecework import calculate_piecework
from timekeeping.models import AuxiliaryWorkCategory, AuxiliaryWorkLog


class PieceworkCalculationTests(TestCase):
    def setUp(self):
        self.bundle = make_single_dispatch()
        self.admin = make_employee("管理员", Employee.Role.ADMIN)
        self.period = SettlementPeriod.objects.create(year=2026, month=10)

    def price(self, price):
        engineer = make_employee("工艺", Employee.Role.PROCESS_ENGINEER)
        self.bundle["process"].unit_price = Decimal(price)
        self.bundle["process"].save(changed_by=engineer)

    def single(self, qty="2"):
        return report_single(
            self.bundle, at(2026, 11, 1, 2), at(2026, 11, 1, 3), qty=qty
        )

    def group(self):
        first = self.bundle["worker"]
        second, third = [
            make_employee(name, Employee.Role.WORKER, team=self.bundle["team"])
            for name in ("组员二", "组员三")
        ]
        dispatch = Dispatch.create_group(
            work_order=self.bundle["order"],
            process=self.bundle["process"],
            quantity=Decimal("10"),
            members=[first, second, third],
            assigned_by=self.bundle["leader"],
        )
        session = WorkSession.open(
            dispatch=dispatch, started_by=first, started_at=at(2026, 10, 31, 8)
        )
        return session, first, second, third

    def test_single_uses_approved_correction_and_frozen_price_with_half_up(self):
        self.price("0.25")
        report = self.single(qty="0.01")
        report.correct(
            corrected_qty=Decimal("0.02"),
            reason="核对有效量",
            corrected_by=self.bundle["leader"],
        )
        report.approve(reviewer=self.bundle["leader"])
        self.price("9.00")
        self.assertFalse(calculate_piecework(2026, 11))
        self.period.lock(self.admin)
        entry = PieceworkAllocation.objects.get(report=report)
        self.assertEqual(entry.quantity, Decimal("0.02"))
        self.assertEqual(entry.unit_price, Decimal("0.25"))
        self.assertEqual(entry.amount, Decimal("0.01"))
        self.assertEqual(entry.employee, self.bundle["worker"])
        self.assertIsNone(entry.group_seconds)

    def test_group_repeated_spans_and_leader_transfer_keep_tail_on_original_first(self):
        self.price("0.07")
        session, first, second, third = self.group()
        session.dispatch.transfer_leader(
            to_employee=second, transferred_at=at(2026, 10, 31, 8, 0, 1)
        )
        session.member_spans.get(employee=first).leave(
            at=at(2026, 10, 31, 8, 0, 1), recorded_by=second
        )
        session.member_spans.get(employee=third).leave(
            at=at(2026, 10, 31, 8, 0, 2), recorded_by=second
        )
        session.join_member(
            employee=third, joined_at=at(2026, 10, 31, 8, 0, 3), recorded_by=second
        )
        report = session.finish_and_report(
            completed_qty=Decimal("1"),
            reported_by=second,
            submitted_at=at(2026, 10, 31, 8, 0, 4),
        )
        report.approve(reviewer=self.bundle["leader"])
        self.period.lock(self.admin)
        rows = {
            row.employee_id: (row.group_seconds, row.amount_cents)
            for row in PieceworkAllocation.objects.filter(report=report)
        }
        self.assertEqual(rows, {first.pk: (1, 2), second.pk: (4, 3), third.pk: (3, 2)})

    def test_positive_group_without_time_blocks_lock_and_identifies_report(self):
        session, first, _, _ = self.group()
        report = session.finish_and_report(
            completed_qty=Decimal("1"),
            reported_by=first,
            submitted_at=session.started_at,
        )
        report.approve(reviewer=self.bundle["leader"])
        with self.assertRaisesMessage(
            ValidationError, f"报工 #{report.pk}：计件金额大于零"
        ):
            self.period.lock(self.admin)
        self.period.refresh_from_db()
        self.assertEqual(self.period.status, SettlementPeriod.Status.OPEN)
        self.assertFalse(self.period.events.exists())
        self.assertFalse(PieceworkAllocation.objects.exists())

    def test_zero_amount_and_zero_time_can_close(self):
        session, first, _, _ = self.group()
        report = session.finish_and_report(
            completed_qty=Decimal("0"),
            reported_by=first,
            submitted_at=session.started_at,
        )
        report.approve(reviewer=self.bundle["leader"])
        self.period.lock(self.admin)
        self.assertEqual(
            list(PieceworkAllocation.objects.values_list("amount_cents", flat=True)),
            [0, 0, 0],
        )

    def test_relock_keeps_archived_amount_and_creates_new_version(self):
        report = self.single()
        report.approve(reviewer=self.bundle["leader"])
        self.period.lock(self.admin)
        old = PieceworkAllocation.objects.get(report=report)
        self.period.unlock(self.admin, "补审数量")
        report.reverse_approval(actor=self.admin)
        report.correct(
            corrected_qty=Decimal("3"),
            reason="复核",
            corrected_by=self.bundle["leader"],
        )
        report.approve(reviewer=self.bundle["leader"])
        self.period.lock(self.admin)
        old.refresh_from_db()
        self.assertEqual(old.amount, Decimal("2.50"))
        new = PieceworkAllocation.objects.exclude(pk=old.pk).get(report=report)
        self.assertEqual(new.amount, Decimal("3.75"))
        self.assertGreater(new.lock_event_id, old.lock_event_id)

    def test_snapshot_write_failure_rolls_back_lock_event_and_auxiliary_summary(self):
        report = self.single()
        report.approve(reviewer=self.bundle["leader"])
        log = AuxiliaryWorkLog.start(
            employee=self.bundle["worker"],
            team=self.bundle["team"],
            category=AuxiliaryWorkCategory.objects.create(code="CLEAN", name="清理"),
            started_at=at(2026, 10, 31, 8),
        )
        log.submit(ended_at=at(2026, 10, 31, 9))
        log.approve(reviewer=self.bundle["leader"])
        with patch(
            "settlement.models.PieceworkAllocation.objects.bulk_create",
            side_effect=IntegrityError("快照保存失败"),
        ):
            with self.assertRaises(IntegrityError):
                self.period.lock(self.admin)
        self.period.refresh_from_db()
        self.assertEqual(self.period.status, SettlementPeriod.Status.OPEN)
        self.assertFalse(self.period.events.exists())
        self.assertFalse(self.period.auxiliary_hour_summaries.exists())
