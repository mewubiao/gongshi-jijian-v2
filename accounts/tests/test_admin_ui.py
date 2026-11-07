from django.test import TestCase
from django.urls import reverse

from config.testing import make_employee, make_login_user
from masterdata.models import Employee


class AdminNavigationTests(TestCase):
    def test_unfold_sidebar_shows_only_allowed_entries_for_each_role(self):
        for role, visible, hidden in (
            (
                Employee.Role.ADMIN,
                (
                    "/dispatch/",
                    "/reports/",
                    "/terminal/manage/",
                    "/settlement/",
                    "/admin/masterdata/employee/",
                    "/admin/erp/workorder/",
                ),
                ("/admin/auth/group/",),
            ),
            (
                Employee.Role.TEAM_LEADER,
                ("/dispatch/", "/reports/", "/auxiliary/"),
                ("/terminal/manage/", "/settlement/", "/admin/masterdata/employee/"),
            ),
            (
                Employee.Role.SETTLEMENT,
                ("/reports/", "/auxiliary/", "/settlement/"),
                ("/dispatch/", "/terminal/manage/", "/admin/masterdata/employee/"),
            ),
            (
                Employee.Role.PROCESS_ENGINEER,
                ("/admin/masterdata/product/", "/admin/masterdata/process/"),
                ("/reports/", "/settlement/", "/admin/masterdata/employee/"),
            ),
        ):
            with self.subTest(role=role):
                self.client.force_login(make_login_user(make_employee(str(role), role)))
                response = self.client.get(reverse("admin:index"))
                self.assertContains(response, "unfold/css/styles.css")
                self.assertContains(response, 'href="/"')
                for path in visible:
                    self.assertContains(response, f'href="{path}"')
                for path in hidden:
                    self.assertNotContains(response, f'href="{path}"')
                if role == Employee.Role.TEAM_LEADER:
                    self.assertContains(response, "报工审核")

    def test_business_menu_updates_when_a_role_is_changed(self):
        employee = make_employee("员工", Employee.Role.ADMIN)
        self.client.force_login(make_login_user(employee))
        response = self.client.get("/reports/")
        self.assertContains(response, 'href="/admin/">管理后台')
        self.assertContains(response, 'href="/reports/" aria-current="page"')

        Employee.objects.filter(pk=employee.pk).update(role=Employee.Role.WORKER)
        self.assertNotContains(self.client.get("/reports/"), 'href="/admin/"')
        self.assertEqual(
            self.client.get(reverse("admin:index"), follow=True).status_code, 403
        )

    def test_global_search_is_disabled(self):
        self.client.force_login(
            make_login_user(make_employee("管理员", Employee.Role.ADMIN))
        )
        self.assertEqual(
            self.client.get(reverse("admin:search"), {"s": "工序"}).status_code, 404
        )
