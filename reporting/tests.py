from django.apps import apps
from django.test import TestCase


class HealthTests(TestCase):
    def test_health_returns_ok(self):
        response = self.client.get("/health/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})


class ReportingAppTests(TestCase):
    def test_app_is_installed_for_piecework(self):
        config = apps.get_app_config("reporting")
        self.assertEqual(config.verbose_name, "报工计件")
