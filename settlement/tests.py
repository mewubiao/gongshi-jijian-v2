from decimal import Decimal

from django.contrib import admin
from django.core.exceptions import ValidationError
from django.test import TestCase

from config.testing import at, make_employee, make_single_dispatch, report_single
from dispatch.models import Dispatch
from erp.models import WorkOrder
from masterdata.models import Employee, Process, Product, Team, WorkCenter
from reporting.models import PieceReport, WorkSession
from settlement.models import SettlementLockEvent, SettlementPeriod


class SettlementTests(TestCase):
    def test_pending_report_blocks_lock_and_unlock_is_traced(self):
        bundle = make_single_dispatch(order_no="MO-SET")
        report = report_single(
            bundle,
            at(2026, 11, 1, 2, 0),
            at(2026, 11, 1, 6, 0),
        )
        self.assertEqual(report.ownership_date.isoformat(), "2026-10-31")
        admin_user = make_employee("管理员", Employee.Role.ADMIN)
        october = SettlementPeriod.objects.create(year=2026, month=10)
        november = SettlementPeriod.objects.create(year=2026, month=11)

        with self.assertRaises(ValidationError) as caught:
            october.lock(admin_user, at=at(2026, 11, 2, 9, 0))
        self.assertIn("未审核", str(caught.exception))
        november.lock(admin_user, at=at(2026, 11, 2, 9, 0))
        self.assertEqual(november.status, SettlementPeriod.Status.LOCKED)

        with self.assertRaises(ValidationError):
            october.lock(bundle["leader"], at=at(2026, 11, 2, 9, 30))

        report.approve(reviewer=bundle["leader"], at=at(2026, 11, 2, 10, 0))
        october.lock(admin_user, at=at(2026, 11, 2, 11, 0))
        report.refresh_from_db()
        with self.assertRaises(ValidationError):
            report.reverse_approval()
        with self.assertRaises(ValidationError):
            report.correct(
                reason="想改",
                corrected_by=bundle["leader"],
                corrected_qty=Decimal("1.00"),
            )

        with self.assertRaises(ValidationError):
            october.unlock(admin_user, "  ")
        october.unlock(admin_user, "补审一张", at=at(2026, 11, 2, 12, 0))
        event = october.events.get(action=SettlementLockEvent.Action.UNLOCK)
        self.assertEqual(event.actor, admin_user)
        self.assertEqual(event.occurred_at, at(2026, 11, 2, 12, 0))
        self.assertEqual(event.reason, "补审一张")
        self.assertEqual(october.status, SettlementPeriod.Status.OPEN)

        report.reverse_approval()
        report.refresh_from_db()
        self.assertEqual(report.status, PieceReport.Status.PENDING)
        with self.assertRaises(ValidationError):
            october.lock(admin_user)

    def test_withdrawn_and_rejected_reports_do_not_block_lock(self):
        bundle = make_single_dispatch(order_no="MO-DONE")
        report = report_single(
            bundle,
            at(2026, 10, 31, 9, 0),
            at(2026, 10, 31, 10, 0),
        )
        report.withdraw("报错了", at=at(2026, 10, 31, 11, 0))
        admin_user = make_employee("管理员", Employee.Role.ADMIN)
        period = SettlementPeriod.objects.create(year=2026, month=10)
        period.lock(admin_user, at=at(2026, 11, 1, 8, 0))
        self.assertEqual(period.events.get().action, SettlementLockEvent.Action.LOCK)

        period.unlock(admin_user, "再看一次", at=at(2026, 11, 1, 9, 0))
        session = WorkSession.open(
            dispatch=bundle["dispatch"],
            started_by=bundle["worker"],
            started_at=at(2026, 10, 31, 13, 0),
        )
        again = session.finish_and_report(
            completed_qty=Decimal("1"),
            reported_by=bundle["worker"],
            submitted_at=at(2026, 10, 31, 14, 0),
        )
        again.reject(
            reason="不对",
            rejected_by=bundle["leader"],
            at=at(2026, 10, 31, 15, 0),
        )
        period.lock(admin_user, at=at(2026, 11, 1, 10, 0))
        self.assertEqual(period.status, SettlementPeriod.Status.LOCKED)


class AdminRegistrationTests(TestCase):
    def test_domain_models_are_registered(self):
        models = [
            Employee,
            WorkCenter,
            Team,
            Product,
            Process,
            WorkOrder,
            Dispatch,
            WorkSession,
            PieceReport,
            SettlementPeriod,
        ]
        for model in models:
            self.assertTrue(
                admin.site.is_registered(model),
                model.__name__,
            )
