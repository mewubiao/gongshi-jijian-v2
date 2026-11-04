from django.contrib.auth.models import User
from django.test import TestCase, override_settings

from ui.smoke import PAGES


class PageSmokeTests(TestCase):
    def test_registered_pages(self):
        for page in PAGES:
            with self.subTest(url=page.url):
                response = self._open(page)
                self.assertEqual(response.status_code, page.status)
                body = response.content.decode()
                for text in page.contains:
                    self.assertIn(text, body)
                for text in page.absent:
                    self.assertNotIn(text, body)

    def _open(self, page):
        if page.access == "debug":
            with override_settings(DEBUG=True):
                return self.client.get(page.url, follow=False)
        if page.access == "auth":
            user = User.objects.create_user("smoke-user", password="smoke-pass-123")
            self.client.force_login(user)
            return self.client.get(page.url, follow=False)
        if page.access != "public":
            self.fail(f"未知的 access：{page.access}")
        return self.client.get(page.url, follow=False)


class GalleryAccessTests(TestCase):
    def test_hidden_without_debug_or_login(self):
        for url in ("/ui/", "/ui/modal/"):
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 403)
                self.assertIn("登录", response.content.decode())

    def test_visible_to_logged_in_user(self):
        user = User.objects.create_user("worker1", password="not-a-real-password")
        self.client.force_login(user)
        response = self.client.get("/ui/")
        self.assertEqual(response.status_code, 200)

    @override_settings(DEBUG=True)
    def test_form_post_shows_success(self):
        response = self.client.post(
            "/ui/",
            {
                "demo-worker": "张三",
                "demo-quantity": "3",
                "demo-shift": "day",
                "demo-note": "",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "示例表单已通过校验。")

    @override_settings(DEBUG=True)
    def test_form_post_keeps_field_errors(self):
        response = self.client.post(
            "/ui/",
            {
                "demo-worker": "",
                "demo-quantity": "",
                "demo-shift": "day",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "这个字段是必填项。")

    @override_settings(DEBUG=True)
    def test_table_page_two(self):
        response = self.client.get("/ui/?page=2")
        self.assertContains(response, "WO-1004")
        self.assertContains(response, "第 2 / 3 页")


class FrontendAssetTests(TestCase):
    def test_tailwind_and_daisyui_versions_are_locked(self):
        from django.conf import settings

        css = (settings.BASE_DIR / "assets" / "css" / "input.css").read_text(
            encoding="utf-8"
        )
        self.assertIn('@import "tailwindcss"', css)
        self.assertIn('@plugin "daisyui"', css)
        self.assertIn('@source "../../templates"', css)
        package = settings.BASE_DIR / "assets/css/node_modules/daisyui/package.json"
        self.assertIn('"version": "5.7.50"', package.read_text(encoding="utf-8"))
        script = (settings.BASE_DIR / "scripts" / "build-css.sh").read_text(
            encoding="utf-8"
        )
        self.assertIn('TAILWIND_VERSION="v4.3.4"', script)
        self.assertIn('DAISYUI_VERSION="5.7.50"', script)
        built = (settings.BASE_DIR / "static" / "css" / "app.css").read_text(
            encoding="utf-8"
        )
        self.assertIn("tailwindcss v4.3.4", built)
        self.assertIn("@layer daisyui", built)

    def test_local_css_and_htmx(self):
        from django.conf import settings

        css = settings.BASE_DIR / "static" / "css" / "app.css"
        htmx = settings.BASE_DIR / "static" / "vendor" / "htmx.min.js"
        self.assertTrue(css.is_file())
        text = css.read_text(encoding="utf-8")
        for token in (".btn", ".modal", ".table", ".alert"):
            self.assertIn(token, text)
        self.assertNotIn("cupcake", text)
        self.assertTrue(htmx.is_file())
        self.assertIn("htmx", htmx.read_text(encoding="utf-8"))

    def test_templates_do_not_call_a_cdn(self):
        from django.conf import settings

        for path in (settings.BASE_DIR / "templates").rglob("*.html"):
            text = path.read_text(encoding="utf-8").lower()
            for host in ("cdn.", "unpkg.com", "jsdelivr", "googleapis"):
                self.assertNotIn(host, text, path.name)
