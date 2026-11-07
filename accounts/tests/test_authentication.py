from django.contrib.auth import SESSION_KEY, authenticate, get_user_model
from django.contrib.auth.models import Permission
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from config.testing import make_employee
from masterdata.models import Employee

User = get_user_model()
PASSWORD = "Workshop-test-password-2026"


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class AuthenticationTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("worker", password=PASSWORD)
        self.employee = make_employee("王工", Employee.Role.WORKER, user=self.user)

    def test_login_dashboard_and_post_only_logout(self):
        self.assertRedirects(self.client.get("/"), "/accounts/login/?next=/")
        response = self.client.post(
            reverse("accounts:login"), {"username": "worker", "password": PASSWORD}
        )
        self.assertRedirects(response, "/")
        self.assertContains(self.client.get("/"), "王工")
        self.assertEqual(self.client.get(reverse("accounts:logout")).status_code, 405)
        self.assertRedirects(
            self.client.post(reverse("accounts:logout")), "/accounts/login/"
        )
        self.assertNotIn(SESSION_KEY, self.client.session)

    def test_bad_password_unlinked_and_disabled_accounts_cannot_login(self):
        orphan = User.objects.create_user("orphan", password=PASSWORD)
        self.assertIsNone(authenticate(username=orphan.username, password=PASSWORD))
        self.assertIsNone(authenticate(username=self.user.username, password="wrong"))
        self.employee.active = False
        self.employee.save()
        self.assertIsNone(authenticate(username=self.user.username, password=PASSWORD))
        self.employee.active = True
        self.employee.save()
        self.user.is_active = False
        self.user.save()
        self.assertIsNone(authenticate(username=self.user.username, password=PASSWORD))

    def test_invalid_session_is_revoked_and_does_not_revive_after_reactivation(self):
        self.client.login(username=self.user.username, password=PASSWORD)
        Employee.objects.filter(pk=self.employee.pk).update(active=False)
        self.assertRedirects(self.client.get("/"), "/accounts/login/?next=/")
        self.assertNotIn(SESSION_KEY, self.client.session)
        Employee.objects.filter(pk=self.employee.pk).update(active=True)
        self.assertRedirects(self.client.get("/"), "/accounts/login/?next=/")

    def test_password_change_keeps_current_session_and_rejects_old_password(self):
        self.client.login(username=self.user.username, password=PASSWORD)
        other_browser = Client()
        other_browser.login(username=self.user.username, password=PASSWORD)
        new_password = "New-workshop-password-2026"
        response = self.client.post(
            reverse("accounts:password_change"),
            {
                "old_password": PASSWORD,
                "new_password1": new_password,
                "new_password2": new_password,
            },
        )
        self.assertRedirects(response, reverse("accounts:password_change_done"))
        self.assertEqual(self.client.get("/").status_code, 200)
        self.assertRedirects(other_browser.get("/"), "/accounts/login/?next=/")
        self.assertIsNone(authenticate(username=self.user.username, password=PASSWORD))
        self.assertIsNotNone(
            authenticate(username=self.user.username, password=new_password)
        )

    def test_login_prevents_external_redirect_and_requires_csrf(self):
        response = self.client.post(
            reverse("accounts:login"),
            {
                "username": "worker",
                "password": PASSWORD,
                "next": "https://example.com/",
            },
        )
        self.assertRedirects(response, "/")
        browser = Client(enforce_csrf_checks=True)
        self.assertEqual(
            browser.post(
                reverse("accounts:login"),
                {
                    "username": "worker",
                    "password": PASSWORD,
                },
            ).status_code,
            403,
        )

    def test_worker_admin_access_is_denied_without_redirect_loop(self):
        self.client.login(username=self.user.username, password=PASSWORD)
        response = self.client.get("/admin/", follow=True)
        self.assertEqual(response.status_code, 403)

    def test_technical_superuser_can_bootstrap_without_employee(self):
        root = User.objects.create_superuser("root", password=PASSWORD)
        self.assertTrue(self.client.login(username=root.username, password=PASSWORD))
        self.assertEqual(self.client.get("/admin/").status_code, 200)
        self.assertContains(self.client.get("/"), "技术超级用户")

    def test_role_is_the_only_permission_source_and_changes_take_effect_immediately(
        self,
    ):
        self.assertFalse(self.user.has_perm("masterdata.change_employee"))
        self.user.is_staff = True
        self.user.save()
        self.user.user_permissions.add(
            Permission.objects.get(
                content_type__app_label="masterdata",
                codename="change_employee",
            )
        )
        self.assertFalse(self.user.has_perm("masterdata.change_employee"))
        Employee.objects.filter(pk=self.employee.pk).update(role=Employee.Role.ADMIN)
        self.assertTrue(self.user.has_perm("masterdata.change_employee"))
        self.assertTrue(self.user.has_perm("reporting.reverse_piecereport"))
        Employee.objects.filter(pk=self.employee.pk).update(
            role=Employee.Role.SETTLEMENT
        )
        self.assertFalse(self.user.has_perm("masterdata.change_employee"))
        self.assertFalse(self.user.has_perm("reporting.reverse_piecereport"))
