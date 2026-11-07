from datetime import time
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase

from config.testing import at, make_employee, make_single_dispatch
from dispatch.models import Dispatch
from masterdata.models import Employee
from reporting.models import WorkSession
from settlement.models import SettlementPeriod
from timekeeping.models import AuxiliaryWorkCategory, AuxiliaryWorkLog


class RunningWorkLockTests(TestCase):
    def setUp(self):
        self.bundle = make_single_dispatch()
        self.admin = make_employee("管理员", Employee.Role.ADMIN)
        self.october = SettlementPeriod.objects.create(year=2026, month=10)
        self.november = SettlementPeriod.objects.create(year=2026, month=11)

    def open_session(self, *, start=None, dispatch=None):
        return WorkSession.open(
            dispatch=dispatch or self.bundle["dispatch"],
            started_by=self.bundle["worker"],
            started_at=start or at(2026, 10, 31, 20),
        )

    def assert_blocked(self, period, text):
        with self.assertRaises(ValidationError) as caught:
            period.lock(self.admin)
        self.assertIn(text, str(caught.exception))
        self.assertIn("先完工", str(caught.exception))
        period.refresh_from_db()
        self.assertEqual(period.status, SettlementPeriod.Status.OPEN)
        self.assertIsNone(period.locked_at)
        self.assertFalse(period.events.exists())
        self.assertFalse(period.auxiliary_hour_summaries.exists())

    def test_paused_production_blocks_until_finished_and_approved(self):
        session = self.open_session()
        session.pause(reason="待料", paused_at=at(2026, 10, 31, 21))
        self.assert_blocked(self.october, f"生产任务 #{session.dispatch_id}")
        report = session.finish_and_report(
            completed_qty=Decimal("2"),
            reported_by=self.bundle["worker"],
            submitted_at=at(2026, 11, 1, 6),
        )
        with self.assertRaisesMessage(ValidationError, "未审核报工"):
            self.october.lock(self.admin)
        report.approve(reviewer=self.bundle["leader"])
        self.october.lock(self.admin)
        self.assertEqual(self.october.status, SettlementPeriod.Status.LOCKED)

    def test_running_auxiliary_blocks_its_ownership_month_only(self):
        category = AuxiliaryWorkCategory.objects.create(code="CLEAN", name="清理")
        log = AuxiliaryWorkLog.start(
            employee=self.bundle["worker"],
            team=self.bundle["team"],
            category=category,
            started_at=at(2026, 11, 1, 2),
        )
        self.assert_blocked(self.october, f"辅助作业 #{log.pk}")
        self.november.lock(self.admin)
        log.submit(ended_at=at(2026, 11, 1, 6))
        with self.assertRaisesMessage(ValidationError, "未审核辅助工时"):
            self.october.lock(self.admin)
        log.approve(reviewer=self.bundle["leader"])
        self.october.lock(self.admin)
        self.assertEqual(self.october.status, SettlementPeriod.Status.LOCKED)

    def test_group_counts_as_unfinished_production(self):
        mate = make_employee("组员", Employee.Role.WORKER, team=self.bundle["team"])
        dispatch = Dispatch.create_group(
            work_order=self.bundle["order"],
            process=self.bundle["process"],
            quantity=Decimal("2"),
            members=[self.bundle["worker"], mate],
            assigned_by=self.bundle["leader"],
        )
        self.open_session(dispatch=dispatch)
        self.assert_blocked(self.october, f"生产任务 #{dispatch.pk}")

    def test_new_session_uses_snapshot_even_after_shift_change(self):
        self.open_session(start=at(2026, 11, 1, 2))
        center = self.bundle["center"]
        center.day_shift_start, center.day_shift_end = time(0), time(12)
        center.night_shift_start, center.night_shift_end = time(12), time(0)
        center.save()
        self.assert_blocked(self.october, "生产任务")
        self.november.lock(self.admin)

    def test_legacy_null_snapshot_uses_existing_shift_logic_across_year(self):
        session = self.open_session(start=at(2027, 1, 1, 2))
        WorkSession.objects.filter(pk=session.pk).update(ownership_date_snapshot=None)
        # 历史开工人已无班组时，沿用报工现有的工单工作中心班次回退。
        Employee.objects.filter(pk=self.bundle["worker"].pk).update(team=None)
        december = SettlementPeriod.objects.create(year=2026, month=12)
        january = SettlementPeriod.objects.create(year=2027, month=1)
        self.assert_blocked(december, "生产任务")
        january.lock(self.admin)
        session.refresh_from_db()
        self.assertIsNone(session.ownership_date_snapshot)

    def test_other_month_work_and_unstarted_dispatch_do_not_block(self):
        self.open_session(start=at(2026, 11, 2, 8))
        self.october.lock(self.admin)
        self.assertEqual(self.october.status, SettlementPeriod.Status.LOCKED)
