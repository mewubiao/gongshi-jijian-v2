from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase

from config.testing import at, make_center, make_employee, make_team
from masterdata.models import Employee
from settlement.hours import approved_auxiliary_minutes
from settlement.models import AuxiliaryHourSummary, SettlementPeriod
from timekeeping.models import AuxiliaryWorkCategory, AuxiliaryWorkLog


class AuxiliarySettlementTests(TestCase):
    def setUp(self):
        self.center = make_center(code="WC-AUX")
        self.leader = make_employee("组长", Employee.Role.TEAM_LEADER)
        self.team = make_team(self.center, self.leader, code="T-AUX")
        self.worker = make_employee(
            "工人",
            Employee.Role.WORKER,
            team=self.team,
        )
        self.other = make_employee(
            "另一人",
            Employee.Role.WORKER,
            team=self.team,
        )
        self.category = AuxiliaryWorkCategory.objects.create(
            code="CLEAN",
            name="清线",
        )
        self.admin = make_employee("管理员", Employee.Role.ADMIN)
        self.period = SettlementPeriod.objects.create(year=2026, month=10)

    def _submit(self, employee, start, end):
        log = AuxiliaryWorkLog.start(
            employee=employee,
            team=self.team,
            category=self.category,
            started_at=start,
        )
        log.submit(ended_at=end)
        return log

    def test_approved_hours_sum_by_employee_without_money(self):
        own = self._submit(
            self.worker,
            at(2026, 10, 31, 8, 0),
            at(2026, 10, 31, 9, 0),
        )
        own.unit_price = Decimal("3.50")
        own.save()
        own.correct(
            minutes=Decimal("30.0000"),
            reason="中途离开",
            corrected_by=self.leader,
            at=at(2026, 10, 31, 12, 0),
        )
        own.approve(reviewer=self.leader, at=at(2026, 10, 31, 12, 30))
        self.assertEqual(own.original_effective_minutes, Decimal("60.0000"))

        other = self._submit(
            self.other,
            at(2026, 11, 1, 2, 0),
            at(2026, 11, 1, 3, 0),
        )
        self.assertEqual(other.ownership_date.isoformat(), "2026-10-31")
        self.assertEqual(other.shift_name_snapshot, "夜班")
        other.approve(reviewer=self.leader, at=at(2026, 11, 1, 4, 0))

        november = self._submit(
            self.worker,
            at(2026, 11, 2, 8, 0),
            at(2026, 11, 2, 9, 0),
        )
        november.approve(reviewer=self.leader, at=at(2026, 11, 2, 10, 0))

        pending = self._submit(
            self.worker,
            at(2026, 10, 2, 8, 0),
            at(2026, 10, 2, 8, 30),
        )
        with self.assertRaises(ValidationError) as caught:
            self.period.lock(self.admin, at=at(2026, 11, 3, 9, 0))
        self.assertIn("辅助工时", str(caught.exception))

        pending.approve(reviewer=self.leader, at=at(2026, 11, 3, 9, 30))
        self.period.lock(self.admin, at=at(2026, 11, 3, 10, 0))

        rows = {
            row.employee_id: row
            for row in AuxiliaryHourSummary.objects.filter(period=self.period)
        }
        self.assertEqual(rows[self.worker.pk].total_minutes, Decimal("60.0000"))
        self.assertEqual(rows[self.other.pk].total_minutes, Decimal("60.0000"))
        self.assertIsNone(rows[self.worker.pk].unit_price)
        self.assertIsNone(rows[self.worker.pk].amount)
        self.assertEqual(
            approved_auxiliary_minutes(2026, 10)[self.worker.pk],
            Decimal("60.0000"),
        )
        self.assertNotIn(self.other.pk, approved_auxiliary_minutes(2026, 11))
        self.assertEqual(
            approved_auxiliary_minutes(2026, 11)[self.worker.pk],
            Decimal("60.0000"),
        )

        own.refresh_from_db()
        with self.assertRaises(ValidationError):
            own.work_note = "锁账后不能改"
            own.save()

    def test_rejected_and_withdrawn_hours_are_not_summarized(self):
        rejected = self._submit(
            self.worker,
            at(2026, 10, 3, 8, 0),
            at(2026, 10, 3, 9, 0),
        )
        rejected.reject(
            reason="不是辅助作业",
            rejected_by=self.leader,
            at=at(2026, 10, 3, 10, 0),
        )
        withdrawn = self._submit(
            self.other,
            at(2026, 10, 4, 8, 0),
            at(2026, 10, 4, 9, 0),
        )
        withdrawn.withdraw("报错了", at=at(2026, 10, 4, 10, 0))
        self.period.lock(self.admin, at=at(2026, 10, 5, 9, 0))
        self.assertFalse(
            AuxiliaryHourSummary.objects.filter(period=self.period).exists()
        )
