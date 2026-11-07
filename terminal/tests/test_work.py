from decimal import Decimal
from unittest.mock import patch

from django.http import HttpResponse
from django.test import TestCase

from config.testing import at, make_employee, make_single_dispatch
from dispatch.models import Dispatch
from masterdata.models import Employee
from reporting.models import PieceReport
from terminal.auth import set_device_cookie, set_worker_cookie
from terminal.models import TerminalDevice
from terminal.operations import report_ticket, task_ticket


class TerminalWorkTests(TestCase):
    def setUp(self):
        self.bundle = make_single_dispatch()
        self.other = make_single_dispatch(order_no="OTHER")
        self.device = TerminalDevice.objects.create(
            code="K1", name="终端", login_sequence=1
        )
        self.employee = self.bundle["worker"]
        self.login(self.employee)
        self.dispatch = self.bundle["dispatch"]
        self.url = f"/terminal/tasks/{self.dispatch.pk}/"

    def login(self, employee):
        response = HttpResponse()
        set_device_cookie(response, self.device)
        set_worker_cookie(response, self.device, employee)
        self.client.cookies.update(response.cookies)

    def post(self, action, **data):
        self.dispatch.refresh_from_db()
        return self.client.post(
            self.url,
            {
                "action": action,
                "ticket": task_ticket(self.dispatch, self.employee),
                **data,
            },
        )

    def test_open_pause_resume_finish_uses_server_times_and_freezes_values(self):
        with patch("terminal.operations.timezone.now", return_value=at(2026, 11, 7, 8)):
            self.assertEqual(
                self.post("open", started_at="2000-01-01").status_code, 302
            )
        session = self.dispatch.sessions.get()
        self.assertEqual(session.started_at, at(2026, 11, 7, 8))
        with patch(
            "terminal.operations.timezone.now", return_value=at(2026, 11, 7, 8, 20)
        ):
            self.assertEqual(self.post("pause", reason="待料").status_code, 302)
        with patch(
            "terminal.operations.timezone.now", return_value=at(2026, 11, 7, 8, 30)
        ):
            self.assertEqual(self.post("resume").status_code, 302)
        with patch("terminal.operations.timezone.now", return_value=at(2026, 11, 7, 9)):
            response = self.post("finish", quantity="12")
        self.assertEqual(response.status_code, 302)
        report = self.dispatch.reports.get()
        self.assertEqual(report.reported_by, self.employee)
        self.assertEqual(report.original_actual_seconds, 3000)
        self.assertEqual(report.allocated_actual_seconds, 3000)
        self.assertEqual(report.completed_qty, Decimal("12"))

    def test_foreign_task_is_hidden_and_repeated_open_is_rejected(self):
        self.assertEqual(
            self.client.get(
                f"/terminal/tasks/{self.other['dispatch'].pk}/"
            ).status_code,
            404,
        )
        payload = {
            "action": "open",
            "ticket": task_ticket(self.dispatch, self.employee),
        }
        self.assertEqual(self.client.post(self.url, payload).status_code, 302)
        self.assertEqual(self.client.post(self.url, payload).status_code, 409)
        self.assertEqual(self.dispatch.sessions.count(), 1)

    def test_bad_quantities_and_blank_pause_do_not_change_session(self):
        self.post("open")
        self.assertEqual(self.post("pause", reason=" ").status_code, 409)
        for quantity in ("-1", "1.234", ""):
            with self.subTest(quantity=quantity):
                self.assertIn(
                    self.post("finish", quantity=quantity).status_code, (400, 409)
                )
        self.assertFalse(self.dispatch.reports.exists())
        self.assertIsNone(self.dispatch.sessions.get().ended_at)

    def test_pending_withdrawal_and_rejected_resubmission_preserve_original(self):
        self.post("open")
        self.post("finish", quantity="2")
        report = self.dispatch.reports.get()
        url = f"/terminal/reports/{report.pk}/"
        report.reject(reason="请核对", rejected_by=self.bundle["leader"])
        payload = {
            "action": "resubmit",
            "ticket": report_ticket(report, self.employee),
            "quantity": "3",
        }
        self.assertEqual(self.client.post(url, payload).status_code, 302)
        revised = report.resubmissions.get()
        self.assertEqual(revised.frozen_unit_price, report.frozen_unit_price)
        self.assertEqual(
            revised.allocated_actual_seconds, report.allocated_actual_seconds
        )
        self.assertEqual(self.client.post(url, payload).status_code, 409)
        self.assertEqual(
            self.client.post(
                f"/terminal/reports/{revised.pk}/",
                {
                    "action": "withdraw",
                    "ticket": report_ticket(revised, self.employee),
                    "reason": "选错任务",
                },
            ).status_code,
            302,
        )
        revised.refresh_from_db()
        self.assertEqual(revised.status, PieceReport.Status.WITHDRAWN)

    def test_group_only_current_leader_can_operate_and_first_member_does_not_change(
        self,
    ):
        mate = make_employee("同伴", Employee.Role.WORKER, team=self.bundle["team"])
        self.dispatch = Dispatch.create_group(
            work_order=self.bundle["order"],
            process=self.bundle["process"],
            quantity=Decimal("5"),
            members=[self.employee, mate],
            assigned_by=self.bundle["leader"],
        )
        self.url = f"/terminal/tasks/{self.dispatch.pk}/"
        self.assertEqual(
            self.post("open", present_members=[self.employee.pk, mate.pk]).status_code,
            302,
        )
        self.assertEqual(self.post("leave", member=self.employee.pk).status_code, 409)
        self.assertEqual(self.post("transfer", member=mate.pk).status_code, 302)
        self.assertEqual(self.post("pause", reason="旧组长").status_code, 403)
        self.employee = mate
        self.login(mate)
        self.assertEqual(
            self.post("leave", member=self.bundle["worker"].pk).status_code, 302
        )
        self.assertEqual(self.post("finish", quantity="5").status_code, 302)
        self.dispatch.refresh_from_db()
        self.assertEqual(
            self.dispatch.first_listed_member().employee, self.bundle["worker"]
        )

    def test_identity_change_invalidates_previous_worker_action_ticket(self):
        ticket = task_ticket(self.dispatch, self.employee)
        self.device.login_sequence += 1
        self.device.save()
        self.assertEqual(
            self.client.post(
                self.url, {"action": "open", "ticket": ticket}
            ).status_code,
            302,
        )
        self.assertFalse(self.dispatch.sessions.exists())

    def test_invalid_group_attendance_is_rejected_without_opening_session(self):
        mate = make_employee("同伴", Employee.Role.WORKER, team=self.bundle["team"])
        self.dispatch = Dispatch.create_group(
            work_order=self.bundle["order"],
            process=self.bundle["process"],
            quantity=Decimal("5"),
            members=[self.employee, mate],
            assigned_by=self.bundle["leader"],
        )
        self.url = f"/terminal/tasks/{self.dispatch.pk}/"
        for selected in (
            [self.employee.pk, "oops"],
            [self.employee.pk, "9" * 40],
            [self.employee.pk, self.other["worker"].pk],
            [self.employee.pk, self.employee.pk],
            [mate.pk],
        ):
            with self.subTest(selected=selected):
                self.assertEqual(
                    self.post("open", present_members=selected).status_code, 409
                )
                self.assertFalse(self.dispatch.sessions.exists())
