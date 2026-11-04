from datetime import datetime, time
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase

from config.testing import (
    at,
    make_center,
    make_employee,
    make_order,
    make_process,
    make_product,
    make_single_dispatch,
    make_team,
    report_single,
)
from dispatch.models import Dispatch
from erp.models import WorkOrder
from masterdata.models import Employee
from reporting.models import PieceReport, WorkSession


class ReportingTests(TestCase):
    def test_report_freezes_price_and_standard_time_from_submit_not_dispatch(self):
        bundle = make_single_dispatch()
        engineer = make_employee("工艺", Employee.Role.PROCESS_ENGINEER)
        process = bundle["process"]
        process.unit_price = Decimal("2.50")
        process.standard_minutes = Decimal("40.00")
        process.save(changed_by=engineer, changed_at=at(2026, 10, 31, 9, 0))

        report = report_single(
            bundle,
            at(2026, 10, 31, 18, 0),
            at(2026, 10, 31, 19, 0),
            qty="12.00",
        )
        self.assertEqual(report.completed_qty, Decimal("12.00"))
        self.assertEqual(report.frozen_unit_price, Decimal("2.50"))
        self.assertEqual(report.frozen_standard_minutes, Decimal("40.00"))
        self.assertEqual(report.ownership_date.isoformat(), "2026-10-31")
        self.assertGreater(report.dispatch.quantity, Decimal("0"))
        self.assertGreater(report.completed_qty, report.dispatch.quantity)

        process.unit_price = Decimal("9.00")
        process.save(changed_by=engineer, changed_at=at(2026, 10, 31, 20, 0))
        report.refresh_from_db()
        self.assertEqual(report.frozen_unit_price, Decimal("2.50"))

        report.withdraw("选错任务", at=at(2026, 10, 31, 20, 30))
        again = WorkSession.open(
            dispatch=bundle["dispatch"],
            started_by=bundle["worker"],
            started_at=at(2026, 11, 1, 9, 0),
        ).finish_and_report(
            completed_qty=Decimal("1.00"),
            reported_by=bundle["worker"],
            submitted_at=at(2026, 11, 1, 10, 0),
        )
        self.assertEqual(again.frozen_unit_price, Decimal("9.00"))
        report.refresh_from_db()
        self.assertEqual(report.frozen_unit_price, Decimal("2.50"))
        self.assertEqual(report.status, PieceReport.Status.WITHDRAWN)

    def test_night_shift_ownership_follows_the_opening_day_even_if_team_overrides(self):
        bundle = make_single_dispatch()
        team = bundle["team"]
        team.day_shift_start = time(7, 0)
        team.day_shift_end = time(19, 0)
        team.night_shift_start = time(19, 0)
        team.night_shift_end = time(7, 0)
        team.save()

        night = report_single(
            bundle,
            at(2026, 11, 1, 6, 30),
            at(2026, 11, 1, 6, 45),
        )
        self.assertEqual(night.ownership_date.isoformat(), "2026-10-31")

        morning_bundle = make_single_dispatch(order_no="MO-MORNING")
        morning_team = morning_bundle["team"]
        morning_team.day_shift_start = time(7, 0)
        morning_team.day_shift_end = time(19, 0)
        morning_team.night_shift_start = time(19, 0)
        morning_team.night_shift_end = time(7, 0)
        morning_team.save()
        morning = report_single(
            morning_bundle,
            at(2026, 11, 1, 7, 30),
            at(2026, 11, 1, 8, 0),
        )
        self.assertEqual(morning.ownership_date.isoformat(), "2026-11-01")

    def test_withdraw_requires_a_reason_and_only_pending_reports(self):
        bundle = make_single_dispatch()
        report = report_single(
            bundle,
            at(2026, 10, 31, 9, 0),
            at(2026, 10, 31, 10, 0),
        )
        with self.assertRaises(ValidationError):
            report.withdraw("  ")
        report.approve(reviewer=bundle["leader"], at=at(2026, 10, 31, 11, 0))
        with self.assertRaises(ValidationError):
            report.withdraw("想撤回")

    def test_rejection_and_correction_keep_the_original_values(self):
        bundle = make_single_dispatch()
        report = report_single(
            bundle,
            at(2026, 10, 31, 9, 0),
            at(2026, 10, 31, 11, 0),
            qty="2.00",
        )
        original_seconds = report.original_actual_seconds
        self.assertEqual(original_seconds, 7200)

        report.correct(
            reason="件数点多了",
            corrected_by=bundle["leader"],
            corrected_qty=Decimal("3.00"),
            corrected_actual_seconds=3600,
            at=at(2026, 10, 31, 12, 0),
        )
        report.refresh_from_db()
        self.assertEqual(report.completed_qty, Decimal("2.00"))
        self.assertEqual(report.original_actual_seconds, 7200)
        self.assertEqual(report.effective_completed_qty(), Decimal("3.00"))
        self.assertEqual(report.effective_actual_seconds(), 3600)

        report.reject(
            reason="请改数量",
            rejected_by=bundle["leader"],
            at=at(2026, 10, 31, 13, 0),
        )
        self.assertEqual(report.rejections.get().reason, "请改数量")
        self.assertEqual(report.completed_qty, Decimal("2.00"))

        bundle["process"].unit_price = Decimal("8.00")
        engineer = make_employee("工艺", Employee.Role.PROCESS_ENGINEER)
        bundle["process"].save(changed_by=engineer, changed_at=at(2026, 10, 31, 13, 30))
        revised = report.resubmit(
            completed_qty=Decimal("4.00"),
            reported_by=bundle["worker"],
            submitted_at=at(2026, 10, 31, 14, 0),
        )
        report.refresh_from_db()
        self.assertEqual(report.status, PieceReport.Status.REJECTED)
        self.assertEqual(report.completed_qty, Decimal("2.00"))
        self.assertEqual(revised.previous, report)
        self.assertEqual(revised.completed_qty, Decimal("4.00"))
        self.assertEqual(revised.frozen_unit_price, Decimal("1.25"))
        self.assertEqual(revised.status, PieceReport.Status.PENDING)

    def test_team_leader_may_approve_their_own_dispatch(self):
        bundle = make_single_dispatch()
        dispatch = Dispatch.create_single(
            work_order=bundle["order"],
            process=bundle["process"],
            quantity=Decimal("1.00"),
            assignee=bundle["leader"],
            assigned_by=bundle["leader"],
            assigned_at=at(2026, 10, 31, 8, 0),
        )
        report = WorkSession.open(
            dispatch=dispatch,
            started_by=bundle["leader"],
            started_at=at(2026, 10, 31, 9, 0),
        ).finish_and_report(
            completed_qty=Decimal("1.00"),
            reported_by=bundle["leader"],
            submitted_at=at(2026, 10, 31, 10, 0),
        )
        report.approve(reviewer=bundle["leader"], at=at(2026, 10, 31, 11, 0))
        self.assertEqual(report.status, PieceReport.Status.APPROVED)
        self.assertEqual(report.approved_by, bundle["leader"])

    def test_pause_needs_a_reason_and_is_excluded_from_actual_seconds(self):
        bundle = make_single_dispatch()
        session = WorkSession.open(
            dispatch=bundle["dispatch"],
            started_by=bundle["worker"],
            started_at=at(2026, 10, 31, 8, 0),
        )
        with self.assertRaises(ValidationError):
            session.pause(reason="  ", paused_at=at(2026, 10, 31, 9, 0))
        session.pause(reason="换模", paused_at=at(2026, 10, 31, 9, 0))
        session.resume(resumed_at=at(2026, 10, 31, 10, 0))
        report = session.finish_and_report(
            completed_qty=Decimal("1.00"),
            reported_by=bundle["worker"],
            submitted_at=at(2026, 10, 31, 12, 0),
        )
        self.assertEqual(report.original_actual_seconds, 10800)
        self.assertEqual(session.pauses.get().reason, "换模")

    def test_parallel_singles_are_allowed_until_a_group_task_is_open(self):
        center = make_center(code="WC-PAR")
        leader = make_employee("组长", Employee.Role.TEAM_LEADER)
        team = make_team(center, leader)
        worker = make_employee("工人", Employee.Role.WORKER, team=team)
        mate = make_employee("同伴", Employee.Role.WORKER, team=team)
        product = make_product(code="P-PAR")
        process = make_process(product)
        order = make_order(product, center, order_no="MO-PAR", remaining_qty="50")
        first = Dispatch.create_single(
            work_order=order,
            process=process,
            quantity=Decimal("2"),
            assignee=worker,
            assigned_by=leader,
            assigned_at=at(2026, 10, 3, 8, 0),
        )
        second = Dispatch.create_single(
            work_order=order,
            process=process,
            quantity=Decimal("2"),
            assignee=worker,
            assigned_by=leader,
            assigned_at=at(2026, 10, 3, 8, 5),
        )
        WorkSession.open(
            dispatch=first,
            started_by=worker,
            started_at=at(2026, 10, 3, 8, 10),
        )
        WorkSession.open(
            dispatch=second,
            started_by=worker,
            started_at=at(2026, 10, 3, 8, 20),
        )
        group = Dispatch.create_group(
            work_order=order,
            process=process,
            quantity=Decimal("2"),
            members=[worker, mate],
            assigned_by=leader,
            assigned_at=at(2026, 10, 3, 8, 30),
        )
        with self.assertRaises(ValidationError) as caught:
            WorkSession.open(
                dispatch=group,
                started_by=worker,
                started_at=at(2026, 10, 3, 8, 40),
            )
        self.assertIn("未完工", str(caught.exception))

    def test_group_attendance_transfer_and_exclusive_membership(self):
        center = make_center(code="WC-GRP")
        leader = make_employee("班长", Employee.Role.TEAM_LEADER)
        team = make_team(center, leader)
        first = make_employee("甲", Employee.Role.WORKER, team=team)
        second = make_employee("乙", Employee.Role.WORKER, team=team)
        third = make_employee("丙", Employee.Role.WORKER, team=team)
        product = make_product(code="P-GRP")
        process = make_process(product, unit_price="3.30", standard_minutes="15.00")
        order = make_order(product, center, order_no="MO-GRP")
        dispatch = Dispatch.create_group(
            work_order=order,
            process=process,
            quantity=Decimal("4"),
            members=[first, second, third],
            assigned_by=leader,
            assigned_at=at(2026, 10, 4, 7, 0),
        )
        with self.assertRaises(ValidationError):
            WorkSession.open(
                dispatch=dispatch,
                started_by=leader,
                started_at=at(2026, 10, 4, 8, 0),
            )
        session = WorkSession.open(
            dispatch=dispatch,
            started_by=first,
            started_at=at(2026, 10, 4, 8, 0),
            present_employees=[first, second],
        )
        with self.assertRaises(ValidationError):
            dispatch.transfer_leader(
                to_employee=third,
                transferred_at=at(2026, 10, 4, 8, 30),
            )
        session.pause(reason="待料", paused_at=at(2026, 10, 4, 9, 0))
        session.resume(resumed_at=at(2026, 10, 4, 10, 0))
        first_span = session.member_spans.get(employee=first)
        with self.assertRaises(ValidationError) as caught:
            first_span.leave(at=at(2026, 10, 4, 10, 30), recorded_by=first)
        self.assertIn("移交", str(caught.exception))

        dispatch.transfer_leader(
            to_employee=second,
            transferred_at=at(2026, 10, 4, 10, 30),
        )
        first_span.leave(at=at(2026, 10, 4, 11, 0), recorded_by=second)
        session.join_member(
            employee=third,
            joined_at=at(2026, 10, 4, 10, 30),
            recorded_by=second,
        )
        other = Dispatch.create_single(
            work_order=order,
            process=process,
            quantity=Decimal("1"),
            assignee=first,
            assigned_by=leader,
            assigned_at=at(2026, 10, 4, 11, 0),
        )
        WorkSession.open(
            dispatch=other,
            started_by=first,
            started_at=at(2026, 10, 4, 11, 10),
        )
        blocked = Dispatch.create_single(
            work_order=order,
            process=process,
            quantity=Decimal("1"),
            assignee=second,
            assigned_by=leader,
            assigned_at=at(2026, 10, 4, 11, 0),
        )
        with self.assertRaises(ValidationError):
            WorkSession.open(
                dispatch=blocked,
                started_by=second,
                started_at=at(2026, 10, 4, 11, 20),
            )

        report = session.finish_and_report(
            completed_qty=Decimal("6"),
            reported_by=second,
            submitted_at=at(2026, 10, 4, 12, 0),
        )
        spans = {
            span.employee_id: span.frozen_actual_seconds
            for span in session.member_spans.all()
        }
        self.assertEqual(spans[first.pk], 7200)
        self.assertEqual(spans[second.pk], 10800)
        self.assertEqual(spans[third.pk], 5400)
        self.assertEqual(report.original_actual_seconds, 10800)
        self.assertEqual(report.frozen_unit_price, Decimal("3.30"))
        self.assertEqual(report.frozen_standard_minutes, Decimal("15.00"))
        dispatch.refresh_from_db()
        self.assertEqual(dispatch.first_listed_member().employee, first)
        self.assertEqual(dispatch.current_leader, second)

    def test_closed_work_order_can_still_be_reported(self):
        bundle = make_single_dispatch(order_no="MO-CLOSED")
        session = WorkSession.open(
            dispatch=bundle["dispatch"],
            started_by=bundle["worker"],
            started_at=at(2026, 10, 5, 8, 0),
        )
        bundle["order"].status = WorkOrder.Status.CLOSED
        bundle["order"].save()
        report = session.finish_and_report(
            completed_qty=Decimal("1"),
            reported_by=bundle["worker"],
            submitted_at=at(2026, 10, 5, 9, 0),
        )
        self.assertEqual(report.status, PieceReport.Status.PENDING)

    def test_naive_start_time_is_rejected(self):
        bundle = make_single_dispatch(order_no="MO-NAIVE")
        with self.assertRaises(ValidationError) as caught:
            WorkSession.open(
                dispatch=bundle["dispatch"],
                started_by=bundle["worker"],
                started_at=datetime(2026, 10, 5, 8, 0),
            )
        self.assertIn("时区", str(caught.exception))

    def test_original_report_cannot_be_overwritten(self):
        bundle = make_single_dispatch(order_no="MO-LOCKROW")
        report = report_single(
            bundle,
            at(2026, 10, 6, 8, 0),
            at(2026, 10, 6, 9, 0),
        )
        report.completed_qty = Decimal("99")
        with self.assertRaises(ValidationError) as caught:
            report.save()
        self.assertIn("不能覆盖", str(caught.exception))
