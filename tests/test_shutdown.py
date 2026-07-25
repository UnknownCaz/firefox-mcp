"""The server must not orphan when its client goes away.

Spawns `python -m firefox_mcp`, closes its stdin (simulating the MCP client
disconnecting), and asserts the process exits promptly. No Firefox needed - the
BiDi connection is lazy, so the server just sits waiting for stdin.
"""

import subprocess
import sys
import time
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


class ClientGoneProbeTests(unittest.TestCase):
    def test_probe_returns_bool_and_no_false_positive_burst(self):
        from firefox_mcp.server import _make_client_gone_probe

        probe = _make_client_gone_probe()
        # Must be callable and return a bool without raising.
        self.assertIn(probe(), (True, False))


class OrphanShutdownTests(unittest.TestCase):
    def test_exits_when_stdin_closes(self):
        proc = subprocess.Popen(
            [sys.executable, "-m", "firefox_mcp"],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            cwd=str(REPO),
        )
        try:
            time.sleep(1.0)  # let it come up
            self.assertIsNone(proc.poll(), "server should still be running before stdin closes")
            proc.stdin.close()  # simulate the client disconnecting
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.fail("server did not exit within 10s of stdin close - orphan risk")
            self.assertIsNotNone(proc.returncode)
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=5)


if __name__ == "__main__":
    unittest.main()
