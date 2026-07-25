"""Unit tests for the safety layer — no browser needed."""

import unittest

from firefox_mcp import safety


class GatedActionTests(unittest.TestCase):
    def test_submit_button_is_gated(self):
        needs, _ = safety.click_requires_confirmation({"submit": True, "name": "OK"})
        self.assertTrue(needs)

    def test_keyword_links_are_gated(self):
        for word in ["Send", "Buy now", "Pay", "Place order", "Delete", "Sign in", "Checkout"]:
            needs, _ = safety.click_requires_confirmation({"name": word, "href": ""})
            self.assertTrue(needs, f"{word!r} should be gated")

    def test_keyword_in_href_is_gated(self):
        needs, _ = safety.click_requires_confirmation(
            {"name": "Continue", "href": "/account/checkout"}
        )
        self.assertTrue(needs)

    def test_benign_link_not_gated(self):
        needs, _ = safety.click_requires_confirmation(
            {"name": "Read more about cats", "href": "/blog/cats"}
        )
        self.assertFalse(needs)

    def test_enter_in_form_is_gated(self):
        needs, _ = safety.press_key_requires_confirmation("Enter", {"inForm": True})
        self.assertTrue(needs)

    def test_enter_outside_form_not_gated(self):
        needs, _ = safety.press_key_requires_confirmation("Enter", {"inForm": False})
        self.assertFalse(needs)

    def test_escape_never_gated(self):
        needs, _ = safety.press_key_requires_confirmation("Escape", {"inForm": True})
        self.assertFalse(needs)


class PasswordTests(unittest.TestCase):
    def test_password_type(self):
        self.assertTrue(safety.is_password_target({"type": "password"}))

    def test_password_flag(self):
        self.assertTrue(safety.is_password_target({"isPassword": True}))

    def test_autocomplete_current_password(self):
        self.assertTrue(safety.is_password_target({"autocomplete": "current-password"}))

    def test_autocomplete_cc(self):
        self.assertTrue(safety.is_password_target({"autocomplete": "cc-number"}))

    def test_plain_text_not_password(self):
        self.assertFalse(safety.is_password_target({"type": "text", "autocomplete": "email"}))


class DomainTests(unittest.TestCase):
    def test_blocked_substring(self):
        cfg = {"blockedDomains": ["paypal.com"], "allowedDomains": []}
        ok, _ = safety.domain_check("https://www.paypal.com/login", cfg)
        self.assertFalse(ok)

    def test_allowed_list_restricts(self):
        cfg = {"blockedDomains": [], "allowedDomains": ["example.com"]}
        ok_good, _ = safety.domain_check("https://example.com/x", cfg)
        ok_bad, _ = safety.domain_check("https://other.org/x", cfg)
        self.assertTrue(ok_good)
        self.assertFalse(ok_bad)

    def test_opaque_url_allowed(self):
        cfg = {"blockedDomains": ["bank"], "allowedDomains": []}
        ok, _ = safety.domain_check("about:blank", cfg)
        self.assertTrue(ok)


if __name__ == "__main__":
    unittest.main()
