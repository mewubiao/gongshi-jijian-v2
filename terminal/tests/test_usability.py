from datetime import date
from decimal import Decimal
from unittest.mock import patch

from django.db import connection
from django.http import HttpResponse
from django.test import TestCase
from django.test.utils import CaptureQueriesContext

from config.testing import (
    at,
    make_employee,
    make_login_user,
    make_single_dispatch,
    report_single,
)
from dispatch.models import Dispatch
from masterdata.models import Employee
from terminal.auth import set_device_cookie, set_worker_cookie
from terminal.models import TerminalDevice
from terminal.operations import task_ticket


class TerminalUsabilityTests(TestCase):
    def setUp(self):
        self.enterContext(
            patch("django.utils.timezone.localdate", return_value=date(2026, 11, 7))
        )
        self.bundle = make_single_dispatch()
        self.worker = self.bundle["worker"]
        self.device = TerminalDevice.objects.create(
            code="K1", name="终端", login_sequence=1
        )
        response = HttpResponse()
        set_device_cookie(response, self.device)
        set_worker_cookie(response, self.device, self.worker)
        self.client.cookies.update(response.cookies)

    def new_dispatch(self):
        return Dispatch.create_single(
            work_order=self.bundle["order"],
            process=self.bundle["process"],
            quantity=Decimal("1"),
            assignee=self.worker,
            assigned_by=self.bundle["leader"],
        )

    def test_actionable_tasks_are_paginated_and_search_remains_personal(self):
        report = report_single(self.bundle, at(2026, 11, 7, 8), at(2026, 11, 7, 9))
        report.approve(reviewer=self.bundle["leader"])
        for _ in range(22):
            self.new_dispatch()
        page = self.client.get("/terminal/work/?page=2")
        self.assertEqual(page.context["page_obj"].paginator.count, 22)
        self.assertNotIn(
            self.bundle["dispatch"].pk, [task.pk for task in page.context["page_obj"]]
        )
        self.assertEqual(
            self.client.get("/terminal/work/?state=all")
            .context["page_obj"]
            .paginator.count,
            23,
        )
        self.assertEqual(
            self.client.get("/terminal/work/?q=NOT-MY-ORDER")
            .context["page_obj"]
            .paginator.count,
            0,
        )

    def test_invalid_finish_preserves_quantity_input(self):
        dispatch = self.bundle["dispatch"]
        url = f"/terminal/tasks/{dispatch.pk}/"
        self.client.post(
            url, {"action": "open", "ticket": task_ticket(dispatch, self.worker)}
        )
        response = self.client.post(
            url,
            {
                "action": "finish",
                "quantity": "-1",
                "ticket": task_ticket(dispatch, self.worker),
            },
        )
        self.assertContains(response, 'value="-1"', status_code=400)
        self.assertFalse(dispatch.reports.exists())

    def test_resubmitted_history_links_to_new_report_instead_of_repeat_form(self):
        report = report_single(self.bundle, at(2026, 11, 7, 8), at(2026, 11, 7, 9))
        report.reject(reason="改数量", rejected_by=self.bundle["leader"])
        revised = report.resubmit(completed_qty=Decimal("3"), reported_by=self.worker)
        response = self.client.get(f"/terminal/reports/{report.pk}/")
        self.assertContains(response, f"/terminal/reports/{revised.pk}/")
        self.assertNotContains(response, "重新提交审核")

    def test_cards_can_reach_later_employees_and_team_queries_do_not_grow(self):
        self.client.force_login(
            make_login_user(make_employee("管理员", Employee.Role.ADMIN))
        )
        with CaptureQueriesContext(connection) as small:
            self.client.get("/terminal/manage/cards/")
        for i in range(26):
            make_employee(f"员工{i}", Employee.Role.WORKER, team=self.bundle["team"])
        with CaptureQueriesContext(connection) as larger:
            response = self.client.get("/terminal/manage/cards/?page=2")
        self.assertEqual(response.context["page_obj"].number, 2)
        self.assertLessEqual(len(larger), len(small) + 1)
        self.assertContains(
            self.client.get("/terminal/manage/cards/?q=员工25"), "员工25"
        )
