"""E01 regression: login help line removed; the login form itself is unchanged.

These tests render the public login page only; they need no database rows.
"""
from django.test import SimpleTestCase
from django.urls import reverse

REMOVED_TEXT = "در صورت فراموشی اطلاعات ورود با مدیر سامانه تماس بگیرید."


class LoginPageHelpTextTests(SimpleTestCase):
    def get_html(self):
        response = self.client.get(reverse("accounts:login"), secure=True)
        self.assertEqual(response.status_code, 200)
        return response.content.decode("utf-8")

    def test_login_page_does_not_show_forgotten_credentials_text(self):
        html = self.get_html()
        self.assertNotIn(REMOVED_TEXT, html)
        self.assertNotIn("فراموشی", html)
        self.assertNotIn('class="auth-help"', html)

    def test_login_form_is_otherwise_unchanged(self):
        html = self.get_html()
        self.assertIn('name="username"', html)
        self.assertIn('name="password"', html)
        self.assertIn('name="csrfmiddlewaretoken"', html)
        self.assertIn("ورود امن به سامانه", html)
        self.assertIn("برای ادامه، نام کاربری و رمز عبور خود را وارد کنید.", html)
