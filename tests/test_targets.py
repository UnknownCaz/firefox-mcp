"""Browser-target selection - no browser needed.

Two Firefoxes share one server: 'main' (Tyler's logged-in profile, port 9222)
and 'sandbox' (a throwaway automation profile, port 9223). The risk this file
guards against is acting on the WRONG one - specifically, leaking sandbox state
into the main session or silently staying on main when the caller asked for
sandbox.
"""

import asyncio
import unittest

from firefox_mcp import tools


def _call(name, **kwargs):
    return asyncio.run(tools.mcp.call_tool(name, kwargs))


class TargetRegistryTests(unittest.TestCase):
    def setUp(self):
        # Every test starts from a known target; these are module globals.
        tools._target["name"] = tools.DEFAULT_TARGET

    def tearDown(self):
        tools._target["name"] = tools.DEFAULT_TARGET

    def test_default_target_is_main(self):
        # Defaulting to the sandbox would silently answer questions about
        # Tyler's browsing with an empty browser.
        self.assertEqual(tools.DEFAULT_TARGET, "main")
        self.assertEqual(tools.current_target(), "main")

    def test_targets_use_distinct_loopback_ports(self):
        urls = [m["ws"] for m in tools.BROWSER_TARGETS.values()]
        self.assertEqual(len(urls), len(set(urls)), "targets must not share a port")
        for u in urls:
            self.assertTrue(u.startswith("ws://127.0.0.1:"), f"{u} must be loopback")

    def test_clients_are_per_target_and_lazy(self):
        tools._clients.clear()
        self.assertEqual(tools._clients, {}, "no client should exist before use")
        main = tools.active_client()
        self.assertEqual(set(tools._clients), {"main"})
        tools._target["name"] = "sandbox"
        sandbox = tools.active_client()
        self.assertIsNot(main, sandbox, "targets must not share a BiDi client")
        self.assertEqual(sandbox.ws_url, tools.BROWSER_TARGETS["sandbox"]["ws"])

    def test_active_tab_is_remembered_per_target(self):
        # Switching must not carry one browser's tab id into the other, where
        # it would resolve to a different page or a stale context.
        tools._active_context.clear()
        tools._target["name"] = "main"
        tools._active_context["main"] = "ctx-main"
        tools._target["name"] = "sandbox"
        tools._active_context["sandbox"] = "ctx-sandbox"
        self.assertEqual(tools._active_context["main"], "ctx-main")
        self.assertEqual(tools._active_context["sandbox"], "ctx-sandbox")


class SwitchBrowserToolTests(unittest.TestCase):
    def setUp(self):
        tools._target["name"] = tools.DEFAULT_TARGET

    def tearDown(self):
        tools._target["name"] = tools.DEFAULT_TARGET

    def test_no_argument_reports_without_switching(self):
        out = str(_call("switch_browser"))
        self.assertIn("main", out)
        self.assertIn("sandbox", out)
        self.assertEqual(tools.current_target(), "main", "listing must not switch")

    def test_unknown_target_does_not_switch(self):
        # A typo must leave the target alone rather than landing somewhere
        # arbitrary - silently staying put is recoverable, silently moving is not.
        out = str(_call("switch_browser", target="sandbax"))
        self.assertIn("Unknown target", out)
        self.assertEqual(tools.current_target(), "main")

    def test_switching_to_unreachable_target_still_sets_target(self):
        # Point 'sandbox' at a port nothing can be listening on, so this is
        # deterministic. The first version of this test assumed no sandbox was
        # running and broke the moment one was - the suite must not depend on
        # what happens to be up on the machine.
        real = tools.BROWSER_TARGETS["sandbox"]["ws"]
        tools.BROWSER_TARGETS["sandbox"]["ws"] = "ws://127.0.0.1:9/session"
        tools._clients.pop("sandbox", None)
        try:
            out = str(_call("switch_browser", target="sandbox"))
            # The target must change even when unreachable: silently staying on
            # main would send later calls to Tyler's real browser.
            self.assertEqual(tools.current_target(), "sandbox")
            self.assertIn("not reachable", out)
            self.assertIn("start-firefox-sandbox", out)
        finally:
            tools.BROWSER_TARGETS["sandbox"]["ws"] = real
            tools._clients.pop("sandbox", None)

    def test_switching_sets_target_regardless_of_reachability(self):
        # Holds whether or not a sandbox happens to be running.
        _call("switch_browser", target="sandbox")
        self.assertEqual(tools.current_target(), "sandbox")
        _call("switch_browser", target="main")
        self.assertEqual(tools.current_target(), "main")

    def test_target_name_is_case_and_space_insensitive(self):
        _call("switch_browser", target="  SANDBOX ")
        self.assertEqual(tools.current_target(), "sandbox")


if __name__ == "__main__":
    unittest.main()
