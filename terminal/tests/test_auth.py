import time
from unittest.mock import patch

from django.contrib.admin.models import LogEntry
from django.contrib.auth import SESSION_KEY
from django.test import Client, TestCase

from config.testing import make_employee, make_login_user, make_single_dispatch
from masterdata.models import Employee
from terminal.auth import (
    WORKER_COOKIE,
)
from terminal.models import TerminalDevice


class TerminalAuthTests(TestCase):
    def setUp(self):
        self.bundle = make_single_dispatch()
        self.device = TerminalDevice.objects.create(code="K1", name="一号终端")
        self.activation = self.device.issue_activation()

    def activate(self, browser=None):
        browser = browser or self.client
        response = browser.post("/terminal/activate/", {"code": self.activation})
        self.assertEqual(response.status_code, 302)
        return browser

    def identify(self, browser=None):
        browser = browser or self.client
        return browser.post(
            "/terminal/", {"method": "qr", "credential": self.bundle["worker"].qr_code}
        )

    def test_unregistered_device_cannot_identify_or_read_work(self):
        self.assertRedirects(self.client.get("/terminal/work/"), "/terminal/activate/")
        self.assertEqual(self.identify().url, "/terminal/activate/")
        self.assertFalse(self.client.cookies.get(WORKER_COOKIE).value)

    def test_activation_is_one_time_and_new_authorization_revokes_old_browser(self):
        self.activate()
        self.assertEqual(self.client.get("/terminal/").status_code, 200)
        self.assertEqual(
            Client().post("/terminal/activate/", {"code": self.activation}).status_code,
            400,
        )
        self.device.issue_activation()
        self.assertEqual(self.client.get("/terminal/work/").url, "/terminal/activate/")

    def test_worker_without_management_account_can_use_qr_and_secret_is_not_echoed(
        self,
    ):
        self.activate()
        self.assertEqual(self.identify().status_code, 302)
        response = self.client.get("/terminal/work/")
        self.assertContains(response, self.bundle["worker"].name)
        self.assertNotContains(response, self.bundle["worker"].qr_code)
        self.assertNotIn(SESSION_KEY, self.client.session)
        self.assertIn("no-store", response.headers["Cache-Control"])

    def test_nfc_keeps_leading_zeros_and_uses_separate_identifier_namespace(self):
        worker = self.bundle["worker"]
        worker.badge_nfc = "00001234"
        worker.save()
        self.activate()
        response = self.client.post(
            "/terminal/", {"method": "nfc", "credential": "00001234"}
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.client.get("/terminal/work/").status_code, 200)

    def test_server_expires_after_15_seconds_and_activity_cannot_revive_expired_cookie(
        self,
    ):
        self.activate()
        now = time.time()
        with patch("time.time", return_value=now):
            self.identify()
        with patch("time.time", return_value=now + 16):
            self.assertEqual(self.client.post("/terminal/activity/").status_code, 401)
            self.assertEqual(self.client.get("/terminal/work/").url, "/terminal/")

    def test_disable_employee_or_device_revokes_access_immediately(self):
        self.activate()
        self.identify()
        Employee.objects.filter(pk=self.bundle["worker"].pk).update(active=False)
        self.assertEqual(self.client.get("/terminal/status/").status_code, 401)
        self.device.active = False
        self.device.save()
        self.assertEqual(self.client.get("/terminal/work/").url, "/terminal/activate/")

    def test_switch_identity_and_signout_revoke_old_worker_cookie(self):
        self.activate()
        self.identify()
        old = self.client.cookies[WORKER_COOKIE].value
        self.device.refresh_from_db()
        self.client.post("/terminal/logout/", {"sequence": self.device.login_sequence})
        self.client.cookies[WORKER_COOKIE] = old
        self.assertEqual(self.client.get("/terminal/work/").url, "/terminal/")

    def test_old_window_cannot_sign_out_newly_identified_worker(self):
        self.activate()
        self.identify()
        self.device.refresh_from_db()
        old_sequence = self.device.login_sequence
        self.identify()
        current_cookie = self.client.cookies[WORKER_COOKIE].value
        for method, url, data in (
            ("get", "/terminal/", {"locked": "1", "sequence": old_sequence}),
            ("post", "/terminal/logout/", {"sequence": old_sequence}),
        ):
            with self.subTest(method=method):
                response = getattr(self.client, method)(url, data)
                self.assertContains(response, "身份已切换")
                self.assertEqual(
                    self.client.cookies[WORKER_COOKIE].value, current_cookie
                )
                self.assertEqual(self.client.get("/terminal/status/").status_code, 200)

    def test_terminal_does_not_inherit_management_identity(self):
        manager = make_employee("管理员", Employee.Role.ADMIN)
        user = make_login_user(manager)
        self.client.force_login(user)
        self.client.get("/terminal/activate/")
        self.assertNotIn(SESSION_KEY, self.client.session)

    def test_identifier_failures_are_rate_limited_per_device(self):
        self.activate()
        for _ in range(10):
            self.client.post(
                "/terminal/", {"method": "qr", "credential": "not-a-worker"}
            )
        self.assertContains(self.identify(), "识别失败次数较多", status_code=400)
        self.device.refresh_from_db()
        self.assertEqual(self.device.failed_attempts, 10)

    def test_admin_management_and_printable_cards_are_not_public(self):
        self.assertEqual(self.client.get("/terminal/manage/").status_code, 302)
        manager = make_employee("管理员", Employee.Role.ADMIN)
        self.client.force_login(make_login_user(manager))
        self.assertEqual(self.client.get("/terminal/manage/").status_code, 200)
        response = self.client.get(
            f"/terminal/manage/cards/{self.bundle['worker'].pk}/qr/"
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "image/svg+xml")
        self.assertIn(b"<svg", response.content)
        self.assertIn("no-store", response["Cache-Control"])

    def test_only_admin_can_register_reauthorize_and_disable_device_with_audit(self):
        self.client.force_login(make_login_user(self.bundle["leader"]))
        self.assertEqual(
            self.client.post(
                "/terminal/manage/", {"action": "disable", "device_id": self.device.pk}
            ).status_code,
            403,
        )
        manager = make_employee("管理员", Employee.Role.ADMIN)
        self.client.force_login(make_login_user(manager))
        self.assertEqual(
            self.client.post(
                "/terminal/manage/",
                {"action": "create", "code": "K2", "name": "二号终端"},
            ).status_code,
            200,
        )
        device = TerminalDevice.objects.get(code="K2")
        generation, digest = device.generation, device.activation_digest
        self.client.post(
            "/terminal/manage/", {"action": "authorize", "device_id": device.pk}
        )
        device.refresh_from_db()
        self.assertGreater(device.generation, generation)
        self.assertNotEqual(device.activation_digest, digest)
        self.client.post(
            "/terminal/manage/", {"action": "disable", "device_id": device.pk}
        )
        device.refresh_from_db()
        self.assertFalse(device.active)
        self.assertFalse(device.activation_digest)
        self.assertEqual(LogEntry.objects.filter(object_id=str(device.pk)).count(), 3)
        for bad_id in ("oops", "9" * 40):
            self.assertEqual(
                self.client.post(
                    "/terminal/manage/", {"action": "disable", "device_id": bad_id}
                ).status_code,
                400,
            )

    def test_csrf_required_for_terminal_writes(self):
        browser = Client(enforce_csrf_checks=True)
        self.assertEqual(
            browser.post("/terminal/activate/", {"code": self.activation}).status_code,
            403,
        )
