from decimal import Decimal

from django.core import signing
from django.core.exceptions import ValidationError
from django.http import HttpResponse
from django.test import TestCase
from django.utils import timezone

from config.testing import make_employee, make_single_dispatch
from dispatch.models import Dispatch
from masterdata.models import Employee
from reporting.models import WorkSession
from terminal.auth import set_device_cookie, set_worker_cookie
from terminal.models import TerminalDevice
from timekeeping.models import AuxiliaryWorkCategory, AuxiliaryWorkLog


class TerminalAuxiliaryTests(TestCase):
    def setUp(self):
        self.bundle = make_single_dispatch()
        self.worker = self.bundle["worker"]
        self.device = TerminalDevice.objects.create(
            code="K1", name="终端", login_sequence=1
        )
        response = HttpResponse()
        set_device_cookie(response, self.device)
        set_worker_cookie(response, self.device, self.worker)
        self.client.cookies.update(response.cookies)
        self.category = AuxiliaryWorkCategory.objects.create(
            code="CLEAN", name="清理", note_required=True
        )
        self.data = {
            "ticket": signing.dumps([self.worker.pk, 1], salt="terminal-aux"),
            "category": self.category.pk,
        }

    def test_start_submit_with_note_and_withdraw(self):
        self.assertEqual(self.client.get("/terminal/auxiliary/").status_code, 200)
        self.assertEqual(
            self.client.post(
                "/terminal/auxiliary/", {**self.data, "action": "start"}
            ).status_code,
            302,
        )
        log = AuxiliaryWorkLog.objects.get(employee=self.worker)
        self.assertEqual(
            self.client.post(
                "/terminal/auxiliary/",
                {**self.data, "action": "submit", "log_id": log.pk},
            ).status_code,
            409,
        )
        self.assertEqual(
            self.client.post(
                "/terminal/auxiliary/",
                {**self.data, "action": "submit", "log_id": log.pk, "note": "设备清理"},
            ).status_code,
            302,
        )
        self.assertEqual(
            self.client.post(
                "/terminal/auxiliary/",
                {
                    **self.data,
                    "action": "withdraw",
                    "log_id": log.pk,
                    "reason": "选错类别",
                },
            ).status_code,
            302,
        )
        log.refresh_from_db()
        self.assertEqual(log.status, AuxiliaryWorkLog.Status.WITHDRAWN)

    def test_duplicate_start_keeps_one_running_record(self):
        payload = {**self.data, "action": "start"}
        self.assertEqual(
            self.client.post("/terminal/auxiliary/", payload).status_code, 302
        )
        self.assertEqual(
            self.client.post("/terminal/auxiliary/", payload).status_code, 409
        )
        self.assertEqual(AuxiliaryWorkLog.objects.count(), 1)

    def test_invalid_start_preserves_note_and_older_records_remain_accessible(self):
        response = self.client.post(
            "/terminal/auxiliary/",
            {**self.data, "action": "start", "category": "bad", "note": "保留现场说明"},
        )
        self.assertContains(response, ">保留现场说明</textarea>", status_code=400)
        self.assertIn("page_obj", self.client.get("/terminal/auxiliary/").context)

    def test_group_members_cannot_start_auxiliary_work(self):
        mate = make_employee("同伴", Employee.Role.WORKER, team=self.bundle["team"])
        dispatch = Dispatch.create_group(
            work_order=self.bundle["order"],
            process=self.bundle["process"],
            quantity=Decimal("10"),
            members=[self.worker, mate],
            assigned_by=self.bundle["leader"],
        )
        WorkSession.open(dispatch=dispatch, started_by=self.worker)
        self.assertEqual(
            self.client.post(
                "/terminal/auxiliary/", {**self.data, "action": "start"}
            ).status_code,
            409,
        )
        self.assertFalse(AuxiliaryWorkLog.objects.exists())

    def test_running_auxiliary_work_prevents_group_open_and_join(self):
        self.client.post("/terminal/auxiliary/", {**self.data, "action": "start"})
        mate = make_employee("同伴", Employee.Role.WORKER, team=self.bundle["team"])
        dispatch = Dispatch.create_group(
            work_order=self.bundle["order"],
            process=self.bundle["process"],
            quantity=Decimal("10"),
            members=[mate, self.worker],
            assigned_by=self.bundle["leader"],
        )
        with self.assertRaises(ValidationError):
            WorkSession.open(dispatch=dispatch, started_by=mate)
        session = WorkSession.open(
            dispatch=dispatch, started_by=mate, present_employees=[mate]
        )
        with self.assertRaises(ValidationError):
            session.join_member(
                employee=self.worker, recorded_by=mate, joined_at=timezone.now()
            )
        self.assertFalse(session.member_spans.filter(employee=self.worker).exists())

    def test_malformed_record_and_category_ids_do_not_create_records(self):
        for field, action in (("log_id", "submit"), ("category", "start")):
            for value in ("oops", "9" * 40):
                with self.subTest(field=field, value=value):
                    self.assertEqual(
                        self.client.post(
                            "/terminal/auxiliary/",
                            {**self.data, "action": action, field: value},
                        ).status_code,
                        400,
                    )
        self.assertFalse(AuxiliaryWorkLog.objects.exists())
