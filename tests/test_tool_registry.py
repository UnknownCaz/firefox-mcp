"""Guards the MCP tool registry - no browser needed.

The set below is the server's public interface. It is meant to be edited
DELIBERATELY when a tool is added or removed: if these tests fail after you
changed the tools, update the list and say so in the commit. A failure here is a
prompt to make that an explicit decision, not a bug in the test.

Why this file exists: `browser_status` was renamed to `firefox_status` because
the generic name collided with the two other browser-control surfaces
registered in Claude Code (the in-app browser pane and Claude-in-Chrome), and a
bare mention of it got answered from the wrong one. Nothing in the suite noticed
the rename, so nothing would notice it being undone.
"""

import asyncio
import unittest

from firefox_mcp.tools import mcp

EXPECTED_TOOLS = {
    "back",
    "click",
    "console_logs",
    "firefox_status",
    "forward",
    "list_tabs",
    "navigate",
    "new_tab",
    "press_key",
    "read_page",
    "screenshot",
    "scroll",
    "select_option",
    "select_tab",
    "snapshot",
    "type",
}


def _tool_names() -> set[str]:
    return {t.name for t in asyncio.run(mcp.list_tools())}


class ToolRegistryTests(unittest.TestCase):
    def test_firefox_status_is_registered(self):
        self.assertIn("firefox_status", _tool_names())

    def test_old_generic_status_name_is_gone(self):
        # Regression guard. Must not come back, not even as a deprecation
        # alias: the whole point of the rename is that this token does not
        # exist as a tool name anywhere.
        self.assertNotIn("browser_status", _tool_names())

    def test_tool_surface_is_frozen(self):
        # Freezing the full set rather than the count catches an accidental
        # rename of ANY tool, plus dropped decorators and silent additions,
        # instead of passing on arithmetic.
        self.assertEqual(len(EXPECTED_TOOLS), 16)
        self.assertEqual(_tool_names(), EXPECTED_TOOLS)


if __name__ == "__main__":
    unittest.main()
