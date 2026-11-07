from datetime import time
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase

from config.testing import at, make_employee, make_single_dispatch, report_single
from masterdata.models import Employee, Process
from reporting.models import WorkSession


class SessionBoundaryTests(TestCase):
    def test_finish_reads_submission_time_rate_even_when_process_was_cached(self):
        bundle = make_single_dispatch()
        session = WorkSession.open(
            dispatch=bundle["dispatch"],
            started_by=bundle["worker"],
            started_at=at(2026, 10, 31, 8),
        )
        self.assertEqual(session.dispatch.process.unit_price, Decimal("1.25"))
        engineer = make_employee("工艺", Employee.Role.PROCESS_ENGINEER)
        process = Process.objects.get(pk=bundle["process"].pk)
        process.unit_price = Decimal("3.50")
        process.standard_minutes = Decimal("45")
        process.save(changed_by=engineer)
        report = session.finish_and_report(
            completed_qty=Decimal("2"),
            reported_by=bundle["worker"],
            submitted_at=at(2026, 10, 31, 9),
        )
        self.assertEqual(report.frozen_unit_price, Decimal("3.50"))
        self.assertEqual(report.frozen_standard_minutes, Decimal("45"))

    def test_open_rejects_a_dispatch_that_already_has_a_pending_report(self):
        bundle = make_single_dispatch()
        report_single(bundle, at(2026, 10, 31, 8), at(2026, 10, 31, 9))
        with self.assertRaises(ValidationError):
            WorkSession.open(
                dispatch=bundle["dispatch"],
                started_by=bundle["worker"],
                started_at=at(2026, 10, 31, 10),
            )

    def test_opening_shift_ownership_is_not_changed_by_later_shift_edit(self):
        bundle = make_single_dispatch()
        session = WorkSession.open(
            dispatch=bundle["dispatch"],
            started_by=bundle["worker"],
            started_at=at(2026, 10, 31, 7, 30),
        )
        center = bundle["center"]
        center.day_shift_start = time(6)
        center.day_shift_end = time(22)
        center.night_shift_start = time(22)
        center.night_shift_end = time(6)
        center.save()
        session = WorkSession.objects.get(pk=session.pk)
        report = session.finish_and_report(
            completed_qty=Decimal("2"),
            reported_by=bundle["worker"],
            submitted_at=at(2026, 10, 31, 9),
        )
        self.assertEqual(report.ownership_date.isoformat(), "2026-10-30")
