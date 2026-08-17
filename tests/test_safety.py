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


class ActivationKeyGateTests(unittest.TestCase):
    """Regressions for the keyboard path being weaker than the click path.

    press_key used to gate ONLY Enter, and only via inForm/isSubmit - so Space
    on a focused destructive button fired with no confirmation at all.
    """

    def test_space_on_focused_button_is_gated(self):
        # The hole: identical element, gated by click, ungated by keyboard.
        needs, _ = safety.press_key_requires_confirmation(
            "Space", {"tag": "button", "name": "Delete account", "inForm": False})
        self.assertTrue(needs)

    def test_space_reports_the_specific_keyword_reason(self):
        _, reason = safety.press_key_requires_confirmation(
            "Space", {"tag": "button", "name": "Delete account", "inForm": False})
        self.assertIn("delete", reason.lower())

    def test_enter_on_button_outside_form_is_gated(self):
        # A <button onclick=...> outside any form was previously ungated.
        needs, _ = safety.press_key_requires_confirmation(
            "Enter", {"tag": "button", "name": "Do the thing", "inForm": False})
        self.assertTrue(needs)

    def test_space_on_link_is_gated(self):
        needs, _ = safety.press_key_requires_confirmation(
            "Space", {"tag": "a", "href": "/checkout", "name": "Continue"})
        self.assertTrue(needs)

    def test_aria_role_button_is_gated(self):
        needs, _ = safety.press_key_requires_confirmation(
            "Enter", {"tag": "div", "role": "button", "name": "Send"})
        self.assertTrue(needs)

    def test_space_in_plain_text_input_not_gated(self):
        # Space while typing in a text box is the overwhelmingly common case and
        # must stay ungated, or the gate becomes noise and gets ignored.
        needs, _ = safety.press_key_requires_confirmation(
            "Space", {"tag": "input", "type": "text", "inForm": True})
        self.assertFalse(needs)

    def test_space_on_body_not_gated(self):
        # Space scrolls the page when nothing interactive has focus.
        needs, _ = safety.press_key_requires_confirmation("Space", {"tag": "body"})
        self.assertFalse(needs)

    def test_arrow_keys_never_gated_even_on_a_button(self):
        for key in ("ArrowDown", "Tab", "Escape", "PageDown", "Home"):
            needs, _ = safety.press_key_requires_confirmation(
                key, {"tag": "button", "name": "Delete account"})
            self.assertFalse(needs, f"{key!r} should not be gated")

    def test_submit_button_detected_by_form_membership(self):
        # _FOCUSED_INFO_JS now reports isSubmit for a <button> owned by a form.
        needs, reason = safety.press_key_requires_confirmation(
            "Enter", {"tag": "button", "isSubmit": True, "name": "Go"})
        self.assertTrue(needs)
        self.assertIn("submit", reason.lower())


class ActivatableTests(unittest.TestCase):
    def test_buttons_and_controls_are_activatable(self):
        self.assertTrue(safety.is_activatable({"tag": "button"}))
        self.assertTrue(safety.is_activatable({"tag": "input", "type": "submit"}))
        self.assertTrue(safety.is_activatable({"tag": "input", "type": "image"}))
        self.assertTrue(safety.is_activatable({"tag": "input", "type": "checkbox"}))
        self.assertTrue(safety.is_activatable({"tag": "a", "href": "/x"}))
        self.assertTrue(safety.is_activatable({"role": "menuitem"}))

    def test_inert_elements_are_not_activatable(self):
        self.assertFalse(safety.is_activatable({"tag": "input", "type": "text"}))
        self.assertFalse(safety.is_activatable({"tag": "textarea"}))
        self.assertFalse(safety.is_activatable({"tag": "body"}))
        self.assertFalse(safety.is_activatable({"tag": "div"}))
        self.assertFalse(safety.is_activatable({"tag": "a"}))  # anchor with no href


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

    def test_allowlist_boundary_match_subdomain_allowed(self):
        cfg = {"allowedDomains": ["example.com"], "blockedDomains": []}
        ok, _ = safety.domain_check("https://example.com/x", cfg)
        self.assertTrue(ok)
        ok, _ = safety.domain_check("https://sub.example.com/x", cfg)
        self.assertTrue(ok)

    def test_allowlist_boundary_match_lookalike_refused(self):
        # The verified bypass: a lookalike host must NOT satisfy the allowlist.
        cfg = {"allowedDomains": ["example.com"], "blockedDomains": []}
        ok, _ = safety.domain_check("https://example.com.evil.test/x", cfg)
        self.assertFalse(ok)
        ok, _ = safety.domain_check("https://notexample.com/x", cfg)
        self.assertFalse(ok)

    def test_blocklist_boundary_match_subdomain_refused(self):
        cfg = {"blockedDomains": ["paypal.com"], "allowedDomains": []}
        ok, _ = safety.domain_check("https://www.paypal.com/login", cfg)
        self.assertFalse(ok)
        ok, _ = safety.domain_check("https://paypal.com", cfg)
        self.assertFalse(ok)

    def test_blocklist_boundary_match_lookalike_allowed(self):
        # "safepaypal.com.evil.test" is neither == "paypal.com" nor ends with
        # ".paypal.com", so under boundary matching it is not blocked.
        cfg = {"blockedDomains": ["paypal.com"], "allowedDomains": []}
        ok, _ = safety.domain_check("https://safepaypal.com.evil.test/", cfg)
        self.assertTrue(ok)
        ok, _ = safety.domain_check("https://notpaypal.com/", cfg)
        self.assertTrue(ok)

    def test_blocklist_bare_keyword_no_longer_overmatches(self):
        # Boundary matching fixes the old substring over-match.
        cfg = {"blockedDomains": ["bank"], "allowedDomains": []}
        ok, _ = safety.domain_check("https://safebank-credit.com/", cfg)
        self.assertTrue(ok)

    def test_trailing_dot_host_still_blocked(self):
        # A trailing-dot FQDN must not dodge the blocklist.
        cfg = {"blockedDomains": ["paypal.com"], "allowedDomains": []}
        ok, _ = safety.domain_check("https://www.paypal.com./login", cfg)
        self.assertFalse(ok)

    def test_trailing_dot_entry_normalized(self):
        cfg = {"blockedDomains": ["paypal.com."], "allowedDomains": []}
        ok, _ = safety.domain_check("https://paypal.com/x", cfg)
        self.assertFalse(ok)

    def test_opaque_url_denied_under_allowlist(self):
        # file:/data: must not slip past an allowlist meant to confine the agent.
        cfg = {"allowedDomains": ["example.com"], "blockedDomains": []}
        ok_file, _ = safety.domain_check("file:///C:/Users/secret.txt", cfg)
        ok_data, _ = safety.domain_check("data:text/html,<h1>x</h1>", cfg)
        self.assertFalse(ok_file)
        self.assertFalse(ok_data)

    def test_opaque_url_allowed_without_allowlist(self):
        cfg = {"allowedDomains": [], "blockedDomains": ["paypal.com"]}
        ok, _ = safety.domain_check("file:///C:/tmp/fixture.html", cfg)
        self.assertTrue(ok)

    def test_about_blank_allowed_even_under_allowlist(self):
        cfg = {"allowedDomains": ["example.com"], "blockedDomains": []}
        ok, _ = safety.domain_check("about:blank", cfg)
        self.assertTrue(ok)


class TypeSubmitGateTests(unittest.TestCase):
    def test_submit_flag_in_form_gated(self):
        needs, _ = safety.type_submit_requires_confirmation(
            "hello", True, {"tag": "input", "inForm": True})
        self.assertTrue(needs)

    def test_submit_flag_outside_form_not_gated(self):
        needs, _ = safety.type_submit_requires_confirmation(
            "hello", True, {"tag": "input", "inForm": False})
        self.assertFalse(needs)

    def test_newline_in_input_in_form_gated(self):
        # The bypass Double found: newline in text, submit=False, single-line input.
        needs, _ = safety.type_submit_requires_confirmation(
            "query\n", False, {"tag": "input", "inForm": True})
        self.assertTrue(needs)

    def test_carriage_return_in_input_in_form_gated(self):
        needs, _ = safety.type_submit_requires_confirmation(
            "query\r", False, {"tag": "input", "inForm": True})
        self.assertTrue(needs)

    def test_newline_in_textarea_not_gated(self):
        # Textareas legitimately hold newlines - do not gate them.
        needs, _ = safety.type_submit_requires_confirmation(
            "line1\nline2", False, {"tag": "textarea", "inForm": True})
        self.assertFalse(needs)

    def test_plain_text_in_form_not_gated(self):
        needs, _ = safety.type_submit_requires_confirmation(
            "hello", False, {"tag": "input", "inForm": True})
        self.assertFalse(needs)


if __name__ == "__main__":
    unittest.main()
