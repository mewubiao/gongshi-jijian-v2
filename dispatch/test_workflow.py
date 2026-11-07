from decimal import Decimal
from uuid import uuid4

from django.core.exceptions import ValidationError
from django.test import TestCase

from config.testing import make_employee, make_login_user, make_single_dispatch
from dispatch.models import Dispatch
from erp.models import WorkOrder
from masterdata.models import Employee


class FreshWorkOrderTests(TestCase):
    def test_stale_work_order_cannot_bypass_current_closed_status(self):
        bundle = make_single_dispatch()
        WorkOrder.objects.filter(pk=bundle["order"].pk).update(
            status=WorkOrder.Status.CLOSED
        )
        with self.assertRaises(ValidationError):
            Dispatch.create_single(
                work_order=bundle["order"],
                process=bundle["process"],
                quantity=Decimal("1"),
                assignee=bundle["worker"],
                assigned_by=bundle["leader"],
            )


class DispatchPageTests(TestCase):
    def setUp(self):
        self.own = make_single_dispatch(order_no="OWN")
        self.other = make_single_dispatch(order_no="OTHER")
        self.user = make_login_user(self.own["leader"])
        self.client.force_login(self.user)
        self.url = f"/dispatch/new/{self.own['order'].pk}/"
        self.data = {
            "submission_key": str(uuid4()),
            "kind": "single",
            "process": self.own["process"].pk,
            "quantity": "5.00",
            "assignee": self.own["worker"].pk,
            "planned_date": "2026-11-07",
            "dispatch_note": "优先安排",
            "assigned_by": self.other["leader"].pk,
        }

    def test_lists_choices_and_details_are_scoped(self):
        self.assertContains(self.client.get("/dispatch/"), "OWN")
        self.assertNotContains(self.client.get("/dispatch/"), "OTHER")
        self.assertNotContains(self.client.get("/dispatch/new/"), "OTHER")
        self.assertEqual(
            self.client.get(f"/dispatch/{self.other['dispatch'].pk}/").status_code, 404
        )
        self.assertEqual(
            self.client.get(f"/dispatch/new/{self.other['order'].pk}/").status_code, 404
        )
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        form = response.context["form"]
        self.assertNotIn(self.other["worker"], form.fields["assignee"].queryset)

    def test_single_dispatch_records_current_actor_and_metadata(self):
        response = self.client.post(self.url, self.data)
        self.assertEqual(response.status_code, 302)
        record = Dispatch.objects.get(submission_key=self.data["submission_key"])
        self.assertEqual(record.assigned_by, self.own["leader"])
        self.assertEqual(record.dispatch_note, "优先安排")
        self.assertEqual(record.planned_date.isoformat(), "2026-11-07")
        self.assertEqual(record.members.get().employee, self.own["worker"])
        self.assertContains(self.client.get(response.url), "派工已创建")

    def test_all_active_roles_in_managed_team_remain_eligible_for_dispatch(self):
        for role in Employee.Role.values:
            with self.subTest(role=role):
                employee = make_employee(role, role, team=self.own["team"])
                form = self.client.get(self.url).context["form"]
                self.assertIn(employee, form.fields["assignee"].queryset)
                data = {
                    **self.data,
                    "submission_key": str(uuid4()),
                    "assignee": employee.pk,
                }
                response = self.client.post(self.url, data)
                self.assertEqual(response.status_code, 302)
                self.assertEqual(
                    Dispatch.objects.get(submission_key=data["submission_key"])
                    .members.get()
                    .employee,
                    employee,
                )

    def test_group_preserves_first_member_for_leader_and_remainder(self):
        mate = make_employee("同伴", Employee.Role.WORKER, team=self.own["team"])
        self.data.update(kind="group", additional_members=[mate.pk])
        self.assertEqual(self.client.post(self.url, self.data).status_code, 302)
        record = Dispatch.objects.get(submission_key=self.data["submission_key"])
        self.assertEqual(record.first_listed_member().employee, self.own["worker"])
        self.assertEqual(record.current_leader, self.own["worker"])
        self.assertEqual(
            list(record.members.values_list("employee_id", flat=True)),
            [self.own["worker"].pk, mate.pk],
        )

    def test_repeated_post_creates_one_dispatch(self):
        before = Dispatch.objects.count()
        first = self.client.post(self.url, self.data)
        second = self.client.post(self.url, self.data)
        self.assertEqual((first.status_code, second.status_code), (302, 302))
        self.assertEqual(first.url, second.url)
        self.assertEqual(Dispatch.objects.count(), before + 1)

    def test_invalid_quantity_process_roster_and_token_do_not_write(self):
        before = Dispatch.objects.count()
        for change in (
            {"quantity": "100.01"},
            {"quantity": "0"},
            {"quantity": "1.234"},
            {"assignee": self.other["worker"].pk},
            {"process": self.other["process"].pk},
            {"kind": "group"},
            {"additional_members": [self.own["leader"].pk]},
            {"submission_key": "invalid"},
        ):
            with self.subTest(change=change):
                response = self.client.post(self.url, {**self.data, **change})
                self.assertEqual(response.status_code, 400)
                self.assertEqual(Dispatch.objects.count(), before)

    def test_closed_order_and_latest_remaining_quantity_are_enforced(self):
        before = Dispatch.objects.count()
        WorkOrder.objects.filter(pk=self.own["order"].pk).update(
            remaining_qty=Decimal("1")
        )
        self.assertEqual(self.client.post(self.url, self.data).status_code, 400)
        WorkOrder.objects.filter(pk=self.own["order"].pk).update(
            status=WorkOrder.Status.CLOSED
        )
        self.data["quantity"] = "1.00"
        self.assertEqual(self.client.post(self.url, self.data).status_code, 400)
        self.assertEqual(Dispatch.objects.count(), before)

    def test_worker_cannot_create_dispatch(self):
        self.client.force_login(make_login_user(self.own["worker"]))
        self.assertEqual(self.client.post(self.url, self.data).status_code, 403)

    def test_anonymous_is_redirected_to_login(self):
        self.client.logout()
        self.assertEqual(self.client.get("/dispatch/").status_code, 302)
