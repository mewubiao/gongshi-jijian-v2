import re
from decimal import Decimal
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.test import Client, TestCase

from config.testing import (
    at,
    make_employee,
    make_login_user,
    make_single_dispatch,
    report_single,
)
from masterdata.models import Employee
from reporting.models import PieceReport
from reporting.review_common import review_revision
from settlement.models import SettlementPeriod


class ReviewPageTests(TestCase):
    def setUp(self):
        self.own = make_single_dispatch(order_no="OWN")
        self.other = make_single_dispatch(order_no="OTHER")
        self.report = report_single(
            self.own, at(2026, 10, 31, 8, 0), at(2026, 10, 31, 9, 0)
        )
        self.foreign = report_single(
            self.other, at(2026, 10, 31, 8, 0), at(2026, 10, 31, 9, 0)
        )
        self.user = make_login_user(self.own["leader"])
        self.client.force_login(self.user)
        self.url = f"/reports/{self.report.pk}/"
        self.data = {"action": "approve", "revision": review_revision(self.report)}

    def test_read_pages_and_filters_restrict_team_data(self):
        self.assertNotContains(self.client.get("/reports/"), "OTHER")
        self.assertEqual(
            self.client.get(f"/reports/{self.foreign.pk}/").status_code, 404
        )
        self.assertContains(self.client.get(self.url), "原始打卡工时")
        self.assertEqual(self.client.get("/reports/?month=bad").status_code, 400)
        self.assertNotContains(self.client.get("/reports/?month=2026-11"), "OWN")

    def test_correction_and_approval_preserve_original_and_actor(self):
        response = self.client.post(
            self.url,
            {
                **self.data,
                "corrected_qty": "3",
                "corrected_minutes": "30",
                "reason": "现场核对",
                "reviewer": self.other["leader"].pk,
            },
        )
        self.assertEqual(response.status_code, 302)
        self.report.refresh_from_db()
        self.assertEqual(self.report.status, PieceReport.Status.APPROVED)
        self.assertEqual(self.report.completed_qty, Decimal("2"))
        self.assertEqual(self.report.original_actual_seconds, 3600)
        self.assertEqual(self.report.effective_completed_qty(), Decimal("3"))
        self.assertEqual(self.report.effective_actual_seconds(), 1800)
        self.assertEqual(self.report.approved_by, self.own["leader"])
        self.assertEqual(self.report.corrections.get().corrected_by, self.own["leader"])
        self.assertContains(self.client.get(self.url), "现场核对")

    def test_reason_required_and_seconds_must_be_valid(self):
        for changes in (
            {"corrected_qty": "0"},
            {"corrected_seconds": "60", "reason": "核对"},
        ):
            with self.subTest(changes=changes):
                self.assertEqual(
                    self.client.post(self.url, {**self.data, **changes}).status_code,
                    400,
                )
        self.assertFalse(self.report.corrections.exists())

    def test_excess_requires_explicit_confirmation_and_supports_zero_correction(self):
        data = {**self.data, "corrected_qty": "12", "reason": "超量核对"}
        self.assertEqual(self.client.post(self.url, data).status_code, 409)
        self.assertFalse(self.report.corrections.exists())
        self.assertEqual(
            self.client.post(self.url, {**data, "confirm_excess": "on"}).status_code,
            302,
        )
        self.report.refresh_from_db()
        self.assertEqual(self.report.effective_completed_qty(), Decimal("12"))

    def test_zero_quantity_and_zero_time_are_not_treated_as_empty(self):
        self.assertEqual(
            self.client.post(
                self.url,
                {
                    **self.data,
                    "corrected_qty": "0",
                    "corrected_seconds": "0",
                    "reason": "作废数量工时",
                },
            ).status_code,
            302,
        )
        self.report.refresh_from_db()
        self.assertEqual(self.report.effective_completed_qty(), Decimal("0"))
        self.assertEqual(self.report.effective_actual_seconds(), 0)

    def test_replayed_and_stale_requests_do_not_add_history(self):
        payload = {**self.data, "corrected_qty": "3", "reason": "核对"}
        self.assertEqual(self.client.post(self.url, payload).status_code, 302)
        self.assertEqual(self.client.post(self.url, payload).status_code, 409)
        self.assertEqual(self.report.corrections.count(), 1)

    def test_new_correction_invalidates_open_review_form(self):
        self.report.correct(
            reason="另一页面已修正",
            corrected_by=self.own["leader"],
            corrected_qty=Decimal("4"),
        )
        self.assertEqual(self.client.post(self.url, self.data).status_code, 409)
        self.report.refresh_from_db()
        self.assertEqual(self.report.status, PieceReport.Status.PENDING)

    def test_atomic_rollback_if_approval_fails_after_correction(self):
        with patch(
            "reporting.models.PieceReport.approve",
            side_effect=ValidationError("审核冲突"),
        ):
            response = self.client.post(
                self.url, {**self.data, "corrected_qty": "3", "reason": "核对"}
            )
        self.assertEqual(response.status_code, 409)
        self.assertFalse(self.report.corrections.exists())
        self.report.refresh_from_db()
        self.assertEqual(self.report.status, PieceReport.Status.PENDING)

    def test_rejection_requires_reason_and_preserves_original(self):
        payload = {**self.data, "action": "reject", "reason": "   "}
        self.assertEqual(self.client.post(self.url, payload).status_code, 400)
        payload["reason"] = "数量须重新核对"
        self.assertEqual(self.client.post(self.url, payload).status_code, 302)
        self.report.refresh_from_db()
        self.assertEqual(self.report.status, PieceReport.Status.REJECTED)
        self.assertEqual(self.report.rejections.get().rejected_by, self.own["leader"])

    def test_finance_is_readonly_and_foreign_team_is_hidden(self):
        finance = make_employee("财务", Employee.Role.SETTLEMENT)
        self.client.force_login(make_login_user(finance))
        self.assertContains(self.client.get("/reports/"), "OTHER")
        self.assertNotContains(self.client.get(self.url), "确认通过")
        self.assertEqual(self.client.post(self.url, self.data).status_code, 403)

    def test_administrator_can_reverse_and_old_pending_form_stays_invalid(self):
        actor = make_employee("管理员", Employee.Role.ADMIN)
        self.report.approve(reviewer=self.own["leader"])
        self.client.force_login(make_login_user(actor))
        response = self.client.get(self.url)
        token = response.context["reverse_form"]["revision"].value()
        self.assertEqual(
            self.client.post(
                self.url, {"action": "reverse", "revision": token, "confirm": "on"}
            ).status_code,
            302,
        )
        self.assertEqual(self.report.approval_reversals.get().reversed_by, actor)
        self.client.force_login(self.user)
        self.assertEqual(self.client.post(self.url, self.data).status_code, 409)

    def test_locked_month_and_invalid_signatures_reject_post(self):
        self.assertEqual(
            self.client.post(self.url, {**self.data, "revision": "forged"}).status_code,
            409,
        )
        self.report.approve(reviewer=self.own["leader"])
        actor = make_employee("管理员", Employee.Role.ADMIN)
        self.foreign.approve(reviewer=self.other["leader"])
        period = SettlementPeriod.objects.create(year=2026, month=10)
        period.lock(actor)
        self.assertEqual(self.client.post(self.url, self.data).status_code, 409)
        self.assertFalse(self.report.corrections.exists())

    def test_csrf_is_required(self):
        browser = Client(enforce_csrf_checks=True)
        browser.force_login(self.user)
        self.assertEqual(browser.post(self.url, self.data).status_code, 403)

    def test_review_and_rejection_form_ids_are_unique(self):
        ids = re.findall(r'\bid="([^"]+)"', self.client.get(self.url).content.decode())
        self.assertEqual(len(ids), len(set(ids)))
