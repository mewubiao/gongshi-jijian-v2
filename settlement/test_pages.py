from decimal import Decimal
from io import BytesIO

from django.test import Client, TestCase
from openpyxl import load_workbook

from config.testing import (
    at,
    make_employee,
    make_login_user,
    make_single_dispatch,
    report_single,
)
from masterdata.models import Employee
from settlement.models import PieceworkAllocation, SettlementLockEvent, SettlementPeriod


class SettlementPageTests(TestCase):
    url = "/settlement/2026/10/"

    def setUp(self):
        self.bundle = make_single_dispatch()
        self.report = report_single(
            self.bundle, at(2026, 10, 31, 8), at(2026, 10, 31, 9)
        )
        self.report.approve(reviewer=self.bundle["leader"])
        self.admin = make_employee("管理员", Employee.Role.ADMIN)
        self.admin_user = make_login_user(self.admin)
        self.finance = make_login_user(make_employee("财务", Employee.Role.SETTLEMENT))
        self.client.force_login(self.admin_user)

    def revision(self):
        return self.client.get(self.url).context["action_form"]["revision"].value()

    def test_month_search_and_read_only_preview_do_not_create_period(self):
        self.assertRedirects(self.client.get("/settlement/?month=2026-10"), self.url)
        self.assertContains(self.client.get(self.url), "2.50")
        self.assertFalse(SettlementPeriod.objects.exists())
        self.assertEqual(self.client.get("/settlement/?month=bad").status_code, 400)
        self.assertEqual(self.client.get("/settlement/2026/13/").status_code, 404)

    def test_lock_export_and_unlock_are_scoped_and_snapshot_is_stable(self):
        worker = self.bundle["worker"]
        worker.name = "=1+1"
        worker.save()
        self.assertEqual(self.client.get(self.url + "export/").status_code, 409)
        self.assertRedirects(
            self.client.post(self.url, {"action": "lock", "revision": self.revision()}),
            self.url,
        )
        worker.name = "修改后姓名"
        worker.save()
        self.client.force_login(self.finance)
        page = self.client.get(self.url)
        self.assertContains(page, "=1+1")
        self.assertNotContains(page, "确认解锁")
        self.assertEqual(
            self.client.post(self.url, {"action": "unlock"}).status_code, 403
        )
        response = self.client.get(self.url + "export/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("piecework-2026-10-v", response["Content-Disposition"])
        self.assertIn("no-store", response["Cache-Control"])
        book = load_workbook(
            BytesIO(b"".join(response.streaming_content)), read_only=True
        )
        self.addCleanup(book.close)
        self.assertEqual(book.sheetnames, ["员工汇总", "计件明细"])
        self.assertEqual(book["员工汇总"]["C2"].value, "=1+1")
        self.assertEqual(book["员工汇总"]["C2"].data_type, "s")
        self.assertEqual(book["员工汇总"]["D2"].value, 2.5)
        self.assertEqual(book["计件明细"]["J2"].value, 2.5)
        self.client.force_login(make_login_user(worker))
        self.assertEqual(self.client.get(self.url).status_code, 403)
        self.assertEqual(self.client.get(self.url + "export/").status_code, 403)
        self.client.force_login(self.admin_user)
        self.assertEqual(
            self.client.post(
                self.url,
                {"action": "unlock", "revision": self.revision(), "reason": " "},
            ).status_code,
            400,
        )
        self.assertRedirects(
            self.client.post(
                self.url,
                {"action": "unlock", "revision": self.revision(), "reason": "复核"},
            ),
            self.url,
        )
        self.assertEqual(self.client.get(self.url + "export/").status_code, 409)
        self.assertEqual(PieceworkAllocation.objects.count(), 1)

    def test_changed_approved_quantity_rejects_old_lock_page(self):
        old = self.revision()
        self.report.reverse_approval(actor=self.admin)
        self.report.correct(
            corrected_qty=Decimal("3"),
            reason="复核",
            corrected_by=self.bundle["leader"],
        )
        self.report.approve(reviewer=self.bundle["leader"])
        response = self.client.post(self.url, {"action": "lock", "revision": old})
        self.assertContains(response, "月结数据已变化", status_code=409)
        self.assertFalse(PieceworkAllocation.objects.exists())
        self.assertFalse(SettlementLockEvent.objects.exists())

    def test_legacy_locked_month_is_not_backfilled_or_exported(self):
        period = SettlementPeriod.objects.create(
            year=2026,
            month=10,
            status=SettlementPeriod.Status.LOCKED,
            locked_by=self.admin,
            locked_at=at(2026, 11, 1, 9),
        )
        event = SettlementLockEvent.objects.create(
            period=period,
            action=SettlementLockEvent.Action.LOCK,
            actor=self.admin,
            occurred_at=period.locked_at,
        )
        self.assertContains(self.client.get(self.url), "暂无计件快照")
        self.assertEqual(self.client.get(self.url + "export/").status_code, 409)
        self.assertFalse(event.piecework_entries.exists())
        self.assertRedirects(
            self.client.post(
                self.url,
                {
                    "action": "unlock",
                    "revision": self.revision(),
                    "reason": "复核旧月份",
                },
            ),
            self.url,
        )
        self.assertRedirects(
            self.client.post(self.url, {"action": "lock", "revision": self.revision()}),
            self.url,
        )
        self.assertEqual(PieceworkAllocation.objects.count(), 1)
        event.refresh_from_db()
        self.assertFalse(event.has_piecework_snapshot)

    def test_lock_requires_csrf(self):
        browser = Client(enforce_csrf_checks=True)
        browser.force_login(self.admin_user)
        self.assertEqual(
            browser.post(
                self.url, {"action": "lock", "revision": self.revision()}
            ).status_code,
            403,
        )
