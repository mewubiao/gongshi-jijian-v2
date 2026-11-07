from datetime import date
from decimal import Decimal
from unittest.mock import patch

from asgiref.sync import async_to_sync
from django.contrib.admin.models import CHANGE, LogEntry
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.test import TestCase, override_settings
from django.urls import reverse

from accounts.scopes import scope_queryset
from config.testing import at, make_employee, make_single_dispatch, report_single
from dispatch.models import Dispatch
from masterdata.models import Employee
from reporting.models import PieceReport

User = get_user_model()
PASSWORD = "Workshop-test-password-2026"


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class RolePermissionTests(TestCase):
    def setUp(self):
        self.own = make_single_dispatch(order_no="OWN")
        self.other = make_single_dispatch(order_no="OTHER")
        self.report = report_single(
            self.own, at(2026, 10, 31, 8, 0), at(2026, 10, 31, 9, 0)
        )
        self.foreign_report = report_single(
            self.other, at(2026, 10, 31, 8, 0), at(2026, 10, 31, 9, 0)
        )
        self.leader_user = self.account(self.own["leader"])
        self.worker_user = self.account(self.own["worker"])
        self.admin = make_employee("管理员", Employee.Role.ADMIN)
        self.admin_user = self.account(self.admin)
        self.engineer = make_employee("工艺", Employee.Role.PROCESS_ENGINEER)
        self.engineer_user = self.account(self.engineer)
        self.finance_user = self.account(
            make_employee("财务", Employee.Role.SETTLEMENT)
        )

    @staticmethod
    def account(employee):
        user = User.objects.create_user(f"user-{employee.pk}", password=PASSWORD)
        employee.user = user
        employee.save()
        return user

    def test_leader_admin_lists_and_object_urls_are_scoped(self):
        self.client.force_login(self.leader_user)
        response = self.client.get(reverse("admin:reporting_piecereport_changelist"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(list(response.context["cl"].queryset), [self.report])
        own_detail = reverse(
            "admin:reporting_piecereport_change", args=[self.report.pk]
        )
        foreign_detail = reverse(
            "admin:reporting_piecereport_change", args=[self.foreign_report.pk]
        )
        self.assertEqual(self.client.get(own_detail).status_code, 200)
        response = self.client.get(foreign_detail)
        self.assertEqual(response.status_code, 302)
        self.assertNotIn("OTHER", response.content.decode())
        dispatches = scope_queryset(self.leader_user, Dispatch.objects.all())
        self.assertEqual(list(dispatches), [self.own["dispatch"]])
        self.assertFalse(
            self.leader_user.has_perm(
                "reporting.review_piecereport", self.foreign_report
            )
        )
        self.assertTrue(
            self.leader_user.has_perm("reporting.review_piecereport", self.report)
        )

    def test_leader_cannot_access_employee_maintenance(self):
        self.client.force_login(self.leader_user)
        self.assertEqual(
            self.client.get(
                reverse("admin:masterdata_employee_changelist")
            ).status_code,
            403,
        )
        self.assertFalse(self.leader_user.has_perm("reporting.reverse_piecereport"))

    def test_readonly_role_cannot_see_old_admin_actions_from_a_previous_role(self):
        LogEntry.objects.create(
            user=self.leader_user,
            content_type=ContentType.objects.get_for_model(Employee),
            object_id=str(self.other["worker"].pk),
            object_repr="PRIVATE OUTSIDE TEAM",
            action_flag=CHANGE,
        )
        self.client.force_login(self.leader_user)
        self.assertNotContains(self.client.get("/admin/"), "PRIVATE OUTSIDE TEAM")

    def test_finance_can_read_all_reports_but_cannot_modify(self):
        self.client.force_login(self.finance_user)
        response = self.client.get(reverse("admin:reporting_piecereport_changelist"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["cl"].queryset.count(), 2)
        response = self.client.post(
            reverse("admin:reporting_piecereport_change", args=[self.report.pk]),
            {"status": "approved"},
        )
        self.assertEqual(response.status_code, 403)
        self.assertFalse(self.finance_user.has_perm("settlement.lock_settlementperiod"))

    @patch("accounts.scopes.timezone.localdate", return_value=date(2026, 11, 6))
    def test_worker_can_only_query_personal_reports_for_current_and_previous_month(
        self, _today
    ):
        self.assertEqual(
            list(scope_queryset(self.worker_user, PieceReport.objects.all())),
            [self.report],
        )
        PieceReport.objects.filter(pk=self.report.pk).update(
            ownership_date=date(2026, 9, 30)
        )
        self.assertFalse(
            scope_queryset(self.worker_user, PieceReport.objects.all()).exists()
        )

    @patch("accounts.scopes.timezone.localdate", return_value=date(2027, 1, 6))
    def test_personal_report_month_range_handles_year_boundary(self, _today):
        PieceReport.objects.filter(pk=self.report.pk).update(
            ownership_date=date(2026, 12, 31)
        )
        self.assertEqual(
            list(scope_queryset(self.worker_user, PieceReport.objects.all())),
            [self.report],
        )

    def test_engineer_rate_change_uses_logged_in_employee_as_actor(self):
        self.client.force_login(self.engineer_user)
        process = self.own["process"]
        forged_actor = make_employee("另一工艺", Employee.Role.PROCESS_ENGINEER)
        response = self.client.post(
            reverse("admin:masterdata_process_change", args=[process.pk]),
            {
                "product": process.product_id,
                "operation_code": process.operation_code,
                "sequence": process.sequence,
                "name": process.name,
                "active": "on",
                "unit_price": "2.00",
                "standard_minutes": "30.00",
                "_save": "保存",
                "changed_by": forged_actor.pk,
                "rate_revision": self.client.get(
                    reverse("admin:masterdata_process_change", args=[process.pk])
                )
                .context["adminform"]
                .form["rate_revision"]
                .value(),
                "rate_history-TOTAL_FORMS": "0",
                "rate_history-INITIAL_FORMS": "0",
                "rate_history-MIN_NUM_FORMS": "0",
                "rate_history-MAX_NUM_FORMS": "1000",
            },
        )
        self.assertEqual(response.status_code, 302)
        process.refresh_from_db()
        self.assertEqual(process.unit_price, Decimal("2.00"))
        self.assertEqual(process.rate_history.get().changed_by, self.engineer)
        self.assertEqual(
            self.client.get(
                reverse("admin:masterdata_employee_changelist")
            ).status_code,
            403,
        )

    def test_business_admin_cannot_create_or_modify_technical_superuser(self):
        root = User.objects.create_superuser("root", password=PASSWORD)
        self.client.force_login(self.admin_user)
        self.assertEqual(self.client.get("/admin/").status_code, 200)
        response = self.client.post(
            reverse("admin:auth_user_add"),
            {
                "username": "new-user",
                "usable_password": "true",
                "password1": PASSWORD,
                "password2": PASSWORD,
                "is_superuser": "on",
                "is_staff": "on",
                "_save": "保存",
            },
        )
        self.assertEqual(response.status_code, 302)
        created = User.objects.get(username="new-user")
        self.assertFalse(created.is_superuser)
        self.assertFalse(created.is_staff)
        self.client.post(
            reverse("admin:auth_user_change", args=[root.pk]),
            {
                "username": "hacked",
                "is_active": "on",
            },
        )
        self.client.post(
            reverse("admin:auth_user_password_change", args=[root.pk]),
            {
                "password1": "New-unauthorized-password",
                "password2": "New-unauthorized-password",
            },
        )
        root.refresh_from_db()
        self.assertEqual(root.username, "root")
        self.assertTrue(root.check_password(PASSWORD))

    def test_business_admin_cannot_bind_superuser_to_employee(self):
        root = User.objects.create_superuser("root", password=PASSWORD)
        self.client.force_login(self.admin_user)
        response = self.client.post(
            reverse("admin:masterdata_employee_change", args=[self.admin.pk]),
            {
                "code": self.admin.code,
                "name": self.admin.name,
                "role": self.admin.role,
                "qr_code": self.admin.qr_code,
                "active": "on",
                "user": root.pk,
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("user", response.context["adminform"].form.errors)
        self.admin.refresh_from_db()
        self.assertEqual(self.admin.user_id, self.admin_user.pk)

    def test_async_permission_checks_use_the_same_role_rules(self):
        self.assertFalse(
            async_to_sync(self.leader_user.ahas_perm)("masterdata.change_employee")
        )
        self.assertTrue(
            async_to_sync(self.leader_user.ahas_perm)("reporting.review_piecereport")
        )
