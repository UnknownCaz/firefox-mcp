"""Server bootstrap - runs the Firefox Browser Control MCP server over stdio.

Launched by the MCP client (Claude Code / Claude Desktop) as a subprocess; it
speaks JSON-RPC on stdin/stdout. It does NOT launch Firefox - attach only.

Robust shutdown (why the extra machinery below): a stdio server MUST exit when
its client goes away, otherwise it becomes an orphan that keeps holding
Firefox's single BiDi session - and the next server then fails with "Maximum
active sessions". On Windows the stdin EOF that signals the client's exit is not
reliably delivered to the async transport, so ``run_stdio_async`` can block
forever. Two guards prevent orphans:

  1. A watchdog thread polls stdin's pipe directly (``PeekNamedPipe`` on Windows,
     ppid change on POSIX). The moment the client's end closes it releases the
     BiDi session and hard-exits - no dependency on the transport noticing EOF.
  2. An ``os._exit`` failsafe after the normal run, so a lingering non-daemon
     transport thread can't keep the process alive either.
"""

from __future__ import annotations

import asyncio
import os
import sys
import threading
import time
import traceback

from .tools import _client, mcp

_WATCH_INTERVAL = 1.5  # seconds between client-liveness checks


def _make_client_gone_probe():
    """Return a callable reporting True once the MCP client has disconnected.

    Windows: peek stdin's pipe - a broken pipe means the client closed its end.
    A console stdin (manual run) fails differently and is treated as "still here"
    so we never false-positive. POSIX: the process is reparented (ppid changes)
    when the launching client dies.
    """
    if sys.platform == "win32":
        import ctypes
        import msvcrt

        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        ERROR_BROKEN_PIPE = 109
        ERROR_PIPE_NOT_CONNECTED = 233

        def _gone() -> bool:
            try:
                handle = msvcrt.get_osfhandle(0)  # stdin fd
            except OSError:
                return False
            avail = ctypes.c_ulong(0)
            ok = k32.PeekNamedPipe(
                ctypes.c_void_p(handle), None, 0, None, ctypes.byref(avail), None
            )
            if ok:
                return False  # pipe still open
            return ctypes.get_last_error() in (ERROR_BROKEN_PIPE, ERROR_PIPE_NOT_CONNECTED)

        return _gone

    parent_pid = os.getppid()

    def _gone() -> bool:
        return os.getppid() != parent_pid

    return _gone


def _hard_exit(code: int = 0) -> None:
    """Flush and terminate immediately, past any lingering non-daemon thread."""
    try:
        sys.stdout.flush()
        sys.stderr.flush()
    except Exception:
        pass
    os._exit(code)


def _start_client_watchdog(loop: asyncio.AbstractEventLoop) -> None:
    client_gone = _make_client_gone_probe()

    def _run() -> None:
        while not client_gone():
            time.sleep(_WATCH_INTERVAL)
        # Client is gone: best-effort release Firefox's BiDi session, then exit.
        try:
            asyncio.run_coroutine_threadsafe(_client.close(), loop).result(timeout=4)
        except Exception:
            pass
        _hard_exit()

    threading.Thread(target=_run, name="mcp-client-watchdog", daemon=True).start()


def main() -> None:
    async def _run() -> None:
        _start_client_watchdog(asyncio.get_running_loop())
        try:
            await mcp.run_stdio_async()
        finally:
            # Release Firefox's single BiDi session on a clean shutdown.
            await _client.close()

    try:
        asyncio.run(_run())
    except BaseException:  # log, then guarantee exit below
        traceback.print_exc()
    finally:
        _hard_exit()


if __name__ == "__main__":
    main()
