"""Unit tests for snapshot formatting — no browser needed."""

import unittest

from firefox_mcp.snapshot import format_snapshot


class SnapshotFormatTests(unittest.TestCase):
    def test_formats_mixed_items(self):
        data = {
            "url": "https://mail.example.com",
            "title": "Inbox",
            "count": 4,
            "items": [
                {"interactive": True, "ref": "e1", "role": "link", "name": "Inbox (3)"},
                {"interactive": True, "ref": "e2", "role": "button", "name": "Compose"},
                {"interactive": True, "ref": "e3", "role": "textbox", "name": "Search mail", "value": ""},
                {"interactive": False, "role": "heading", "name": "Today", "level": 2},
                {"interactive": True, "ref": "e4", "role": "checkbox", "name": "Select all", "checked": False},
            ],
        }
        out = format_snapshot(data)
        self.assertIn('[e1] link "Inbox (3)"', out)
        self.assertIn('[e2] button "Compose"', out)
        self.assertIn('[e3] textbox "Search mail" value=""', out)
        self.assertIn('heading "Today"', out)
        self.assertIn('[e4] checkbox "Select all" checked=false', out)

    def test_password_value_redacted(self):
        data = {
            "url": "x", "title": "x", "count": 1,
            "items": [{"interactive": True, "ref": "e1", "role": "password",
                       "name": "Password", "value": "***"}],
        }
        out = format_snapshot(data)
        self.assertIn("[e1] password", out)
        self.assertIn("value=***", out)

    def test_empty(self):
        out = format_snapshot({"url": "x", "title": "x", "count": 0, "items": []})
        self.assertIn("no visible interactive elements", out)


if __name__ == "__main__":
    unittest.main()
