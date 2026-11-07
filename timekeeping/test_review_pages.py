import re
from decimal import Decimal
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.test import TestCase

from config.testing import at, make_employee, make_login_user, make_single_dispatch
from masterdata.models import Employee
from reporting.review_common import review_revision
from timekeeping.models import AuxiliaryWorkCategory, AuxiliaryWorkLog


class AuxiliaryReviewPageTests(TestCase):
    def setUp(self):
        self.own = make_single_dispatch(order_no="OWN")
        self.other = make_single_dispatch(order_no="OTHER")
        category = AuxiliaryWorkCategory.objects.create(code="CLEAN", name="清线")
        self.logs = []
        for bundle in (self.own, self.other):
            log = AuxiliaryWorkLog.start(
                employee=bundle["worker"],
                team=bundle["team"],
                category=category,
                started_at=at(2026, 10, 31, 8, 0),
            )
            log.submit(ended_at=at(2026, 10, 31, 9, 0))
            self.logs.append(log)
        self.log = self.logs[0]
        self.client.force_login(make_login_user(self.own["leader"]))
        self.url = f"/auxiliary/{self.log.pk}/"
        self.data = {"action": "approve", "revision": review_revision(self.log)}

    def test_scoped_lists_and_details(self):
        response = self.client.get("/auxiliary/")
        self.assertEqual(list(response.context["page_obj"]), [self.log])
        self.assertEqual(
            self.client.get(f"/auxiliary/{self.logs[1].pk}/").status_code, 404
        )
        self.assertEqual(self.client.get(self.url).status_code, 200)

    def test_review_and_rejection_form_ids_are_unique(self):
        ids = re.findall(r'\bid="([^"]+)"', self.client.get(self.url).content.decode())
        self.assertEqual(len(ids), len(set(ids)))

    def test_approve_with_zero_correction_preserves_original_minutes(self):
        self.assertEqual(
            self.client.post(
                self.url, {**self.data, "minutes": "0", "reason": "核对为零"}
            ).status_code,
            302,
        )
        self.log.refresh_from_db()
        self.assertEqual(self.log.original_effective_minutes, Decimal("60"))
        self.assertEqual(self.log.effective_minutes_for_review(), Decimal("0"))
        self.assertEqual(self.log.approved_by, self.own["leader"])
        self.assertEqual(self.client.post(self.url, self.data).status_code, 409)
        self.assertEqual(self.log.corrections.count(), 1)

    def test_missing_reason_does_not_change_data(self):
        self.assertEqual(
            self.client.post(self.url, {**self.data, "minutes": "30"}).status_code, 400
        )
        self.assertEqual(
            self.client.post(
                self.url, {**self.data, "action": "reject", "reason": " "}
            ).status_code,
            400,
        )
        self.assertFalse(self.log.corrections.exists())
        self.assertFalse(self.log.rejections.exists())

    def test_reject_and_rollback_of_correction(self):
        with patch(
            "timekeeping.models.AuxiliaryWorkLog.approve",
            side_effect=ValidationError("审核冲突"),
        ):
            self.assertEqual(
                self.client.post(
                    self.url, {**self.data, "minutes": "30", "reason": "核对"}
                ).status_code,
                409,
            )
        self.assertFalse(self.log.corrections.exists())
        self.assertEqual(
            self.client.post(
                self.url, {**self.data, "action": "reject", "reason": "作业说明不符"}
            ).status_code,
            302,
        )
        self.log.refresh_from_db()
        self.assertEqual(self.log.status, AuxiliaryWorkLog.Status.REJECTED)

    def test_finance_cannot_review_and_stale_form_cannot_approve(self):
        self.log.correct(
            minutes=Decimal("40"),
            reason="其他页面已修正",
            corrected_by=self.own["leader"],
        )
        self.assertEqual(self.client.post(self.url, self.data).status_code, 409)
        self.client.force_login(
            make_login_user(make_employee("财务", Employee.Role.SETTLEMENT))
        )
        self.assertEqual(self.client.get(self.url).status_code, 200)
        self.assertEqual(self.client.post(self.url, self.data).status_code, 403)
