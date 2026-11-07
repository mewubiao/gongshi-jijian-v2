from datetime import timedelta
from decimal import Decimal

from django.test import TestCase

from config.testing import at, make_single_dispatch
from dispatch.models import Dispatch
from reporting.models import PieceReport, WorkSession


class AllocationTests(TestCase):
    def setUp(self):
        self.bundle = make_single_dispatch()

    def session(self, start):
        dispatch = Dispatch.create_single(
            work_order=self.bundle["order"],
            process=self.bundle["process"],
            quantity=Decimal("10"),
            assignee=self.bundle["worker"],
            assigned_by=self.bundle["leader"],
        )
        return WorkSession.open(
            dispatch=dispatch, started_by=self.bundle["worker"], started_at=start
        )

    def finish(self, session, end):
        return session.finish_and_report(
            completed_qty=Decimal("2"),
            reported_by=self.bundle["worker"],
            submitted_at=end,
        )

    def test_partial_overlap_is_split_and_first_snapshot_does_not_change(self):
        first = self.session(at(2026, 11, 7, 8))
        second = self.session(at(2026, 11, 7, 8, 30))
        report = self.finish(first, at(2026, 11, 7, 9))
        self.assertEqual(report.original_actual_seconds, 3600)
        self.assertEqual(report.allocated_actual_seconds, 2700)
        other = self.finish(second, at(2026, 11, 7, 9, 30))
        report.refresh_from_db()
        self.assertEqual(report.allocated_actual_seconds, 2700)
        self.assertEqual(other.allocated_actual_seconds, 2700)
        self.assertEqual(
            report.effective_actual_seconds() + other.effective_actual_seconds(), 5400
        )

    def test_paused_task_does_not_share_others_active_time(self):
        first = self.session(at(2026, 11, 7, 8))
        second = self.session(at(2026, 11, 7, 8))
        first.pause(reason="待料", paused_at=at(2026, 11, 7, 8, 20))
        first.resume(resumed_at=at(2026, 11, 7, 8, 40))
        one = self.finish(first, at(2026, 11, 7, 9))
        two = self.finish(second, at(2026, 11, 7, 9))
        self.assertEqual(
            (one.allocated_actual_seconds, two.allocated_actual_seconds), (1200, 2400)
        )

    def test_integer_remainder_goes_to_first_dispatch(self):
        first = self.session(at(2026, 11, 7, 8))
        second = self.session(at(2026, 11, 7, 8))
        one = self.finish(first, at(2026, 11, 7, 8, 0, 1))
        two = self.finish(second, at(2026, 11, 7, 8, 0, 1))
        self.assertEqual(
            (one.allocated_actual_seconds, two.allocated_actual_seconds), (1, 0)
        )

    def test_fractional_boundaries_carry_seconds_without_rewriting_earlier_report(self):
        start = at(2026, 11, 7, 8)
        first = self.session(start)
        second = self.session(start + timedelta(microseconds=600000))
        one = self.finish(first, start + timedelta(microseconds=1200000))
        two = self.finish(second, start + timedelta(seconds=2))
        one.refresh_from_db()
        self.assertEqual(
            (one.allocated_actual_seconds, two.allocated_actual_seconds), (1, 1)
        )

    def test_legacy_null_allocation_is_not_recomputed(self):
        session = self.session(at(2026, 11, 7, 8))
        report = self.finish(session, at(2026, 11, 7, 9))
        PieceReport.objects.filter(pk=report.pk).update(allocated_actual_seconds=None)
        report.refresh_from_db()
        self.assertEqual(report.effective_actual_seconds(), 3600)

    def test_rejected_resubmission_keeps_frozen_allocation(self):
        first = self.session(at(2026, 11, 7, 8))
        self.session(at(2026, 11, 7, 8))
        report = self.finish(first, at(2026, 11, 7, 9))
        report.reject(reason="改数量", rejected_by=self.bundle["leader"])
        revised = report.resubmit(
            completed_qty=Decimal("3"),
            reported_by=self.bundle["worker"],
            submitted_at=at(2026, 11, 7, 10),
        )
        self.assertEqual(revised.allocated_actual_seconds, 1800)
