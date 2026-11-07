from decimal import Decimal
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.test import TestCase

from config.testing import (
    at,
    make_employee,
    make_single_dispatch,
    make_team,
    report_single,
)
from dispatch.models import Dispatch
from masterdata.models import Employee
from reporting.models import ReportApprovalReversal
from settlement.models import SettlementPeriod
from timekeeping.models import AuxiliaryWorkCategory, AuxiliaryWorkLog


class BusinessAccessTests(TestCase):
    def setUp(self):
        self.own = make_single_dispatch(order_no="OWN")
        self.other = make_single_dispatch(order_no="OTHER")
        self.report = report_single(
            self.own, at(2026, 10, 31, 8, 0), at(2026, 10, 31, 9, 0)
        )
        category = AuxiliaryWorkCategory.objects.create(code="CLEAN", name="清线")
        self.log = AuxiliaryWorkLog.start(
            employee=self.own["worker"],
            team=self.own["team"],
            category=category,
            started_at=at(2026, 10, 31, 9, 0),
        )
        self.log.submit(ended_at=at(2026, 10, 31, 10, 0))

    def test_leader_cannot_dispatch_workers_from_another_team(self):
        with self.assertRaises(ValidationError):
            Dispatch.create_single(
                work_order=self.own["order"],
                process=self.own["process"],
                quantity=Decimal("1"),
                assignee=self.other["worker"],
                assigned_by=self.own["leader"],
            )
        with self.assertRaises(ValidationError):
            Dispatch.create_group(
                work_order=self.own["order"],
                process=self.own["process"],
                quantity=Decimal("1"),
                members=[self.own["worker"], self.other["worker"]],
                assigned_by=self.own["leader"],
            )

    def test_leader_can_manage_multiple_teams(self):
        team = make_team(self.own["center"], self.own["leader"], name="乙班")
        worker = make_employee("乙班工人", Employee.Role.WORKER, team=team)
        dispatch = Dispatch.create_single(
            work_order=self.own["order"],
            process=self.own["process"],
            quantity=Decimal("1"),
            assignee=worker,
            assigned_by=self.own["leader"],
        )
        self.assertEqual(dispatch.team, team)

    def test_stale_employee_team_cannot_bypass_dispatch_scope(self):
        Employee.objects.filter(pk=self.own["worker"].pk).update(
            team=self.other["team"]
        )
        with self.assertRaises(ValidationError):
            Dispatch.create_single(
                work_order=self.own["order"],
                process=self.own["process"],
                quantity=Decimal("1"),
                assignee=self.own["worker"],
                assigned_by=self.own["leader"],
            )

    def test_leader_cannot_manage_another_team_in_the_same_work_center(self):
        team = make_team(self.own["center"], self.other["leader"], name="别的班组")
        worker = make_employee("同中心另一组", Employee.Role.WORKER, team=team)
        with self.assertRaises(ValidationError):
            Dispatch.create_single(
                work_order=self.own["order"],
                process=self.own["process"],
                quantity=Decimal("1"),
                assignee=worker,
                assigned_by=self.own["leader"],
            )

    def test_review_actions_reject_other_teams(self):
        outsider = self.other["leader"]
        actions = [
            lambda: self.report.approve(reviewer=outsider),
            lambda: self.report.reject(reason="驳回", rejected_by=outsider),
            lambda: self.report.correct(
                reason="修正", corrected_by=outsider, corrected_qty=Decimal("1")
            ),
            lambda: self.log.approve(reviewer=outsider),
            lambda: self.log.reject(reason="驳回", rejected_by=outsider),
            lambda: self.log.correct(
                reason="修正", corrected_by=outsider, minutes=Decimal("10")
            ),
        ]
        for action in actions:
            with self.subTest(action=action), self.assertRaises(ValidationError):
                action()
        self.report.refresh_from_db()
        self.log.refresh_from_db()
        self.assertEqual(self.report.status, self.report.Status.PENDING)
        self.assertEqual(self.log.status, self.log.Status.PENDING_REVIEW)
        self.assertFalse(self.report.corrections.exists())
        self.assertFalse(self.report.rejections.exists())
        self.assertFalse(self.log.corrections.exists())
        self.assertFalse(self.log.rejections.exists())

    def test_disabled_or_reassigned_leader_loses_business_access_immediately(self):
        Employee.objects.filter(pk=self.own["leader"].pk).update(active=False)
        with self.assertRaises(ValidationError):
            self.report.approve(reviewer=self.own["leader"])

    def test_reverse_approval_requires_system_administrator(self):
        self.report.approve(reviewer=self.own["leader"])
        with self.assertRaises(ValidationError):
            self.report.reverse_approval(actor=self.own["leader"])

    def test_reverse_approval_is_traced_and_locked_month_is_protected(self):
        actor = make_employee("管理员", Employee.Role.ADMIN)
        self.report.approve(reviewer=self.own["leader"])
        original_approver = self.report.approved_by_id
        self.report.reverse_approval(actor=actor, at=at(2026, 11, 1, 8, 0))
        event = self.report.approval_reversals.get()
        self.assertEqual(event.reversed_by, actor)
        self.assertEqual(event.reversed_at, at(2026, 11, 1, 8, 0))
        self.assertEqual(event.approved_by_id, original_approver)
        self.assertEqual(self.report.approved_by_id, original_approver)
        with self.assertRaises(ValidationError):
            event.delete()
        with self.assertRaises(ValidationError):
            event.save()
        with self.assertRaises(ValidationError):
            self.report.reverse_approval(actor=actor)
        self.assertEqual(ReportApprovalReversal.objects.count(), 1)
        self.report.approve(reviewer=self.own["leader"])
        self.log.approve(reviewer=self.own["leader"])
        period = SettlementPeriod.objects.get(year=2026, month=10)
        period.lock(actor)
        with self.assertRaises(ValidationError):
            self.report.reverse_approval(actor=actor)
        self.report.refresh_from_db()
        self.assertEqual(self.report.status, self.report.Status.APPROVED)
        self.assertEqual(ReportApprovalReversal.objects.count(), 1)

    def test_reversal_and_audit_record_roll_back_together(self):
        actor = make_employee("管理员", Employee.Role.ADMIN)
        self.report.approve(reviewer=self.own["leader"])
        with (
            patch(
                "reporting.models.ReportApprovalReversal.objects.create",
                side_effect=RuntimeError("audit storage failed"),
            ),
            self.assertRaises(RuntimeError),
        ):
            self.report.reverse_approval(actor=actor)
        self.report.refresh_from_db()
        self.assertEqual(self.report.status, self.report.Status.APPROVED)
        self.assertFalse(ReportApprovalReversal.objects.exists())

    def test_historical_approver_role_change_does_not_prevent_admin_reversal(self):
        actor = make_employee("管理员", Employee.Role.ADMIN)
        self.report.approve(reviewer=self.own["leader"])
        Employee.objects.filter(pk=self.own["leader"].pk).update(
            role=Employee.Role.SETTLEMENT
        )
        self.report.reverse_approval(actor=actor)
        self.assertEqual(self.report.status, self.report.Status.PENDING)
        self.assertEqual(
            self.report.approval_reversals.get().approved_by_id, self.own["leader"].pk
        )
