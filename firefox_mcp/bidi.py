"""Async WebDriver BiDi client for an already-running Firefox.

Speaks the raw BiDi JSON protocol over a WebSocket (no Puppeteer/Playwright).
Attaches to a Firefox launched with ``--remote-debugging-port`` and NEVER
spawns or closes the browser itself.

Wire format (WebDriver BiDi / W3C):
  * command  -> ``{"id": <int>, "method": "<mod>.<name>", "params": {...}}``
  * success  <- ``{"type": "success", "id": <int>, "result": {...}}``
  * error    <- ``{"type": "error", "id": <int>, "error": "...", "message": "..."}``
  * event    <- ``{"type": "event", "method": "<mod>.<name>", "params": {...}}``
"""

from __future__ import annotations

import asyncio
import base64
import json
from collections import deque
from typing import Any, Optional

import websockets

DEFAULT_WS_URL = "ws://127.0.0.1:9222/session"
_COMMAND_TIMEOUT = 30.0
_CONNECT_TIMEOUT = 5.0

# Events we care about; subscribed once per session.
_SUBSCRIBED_EVENTS = [
    "log.entryAdded",
    "browsingContext.navigationStarted",
    "browsingContext.load",
]


class BiDiError(Exception):
    """A BiDi command returned a ``type: error`` response."""

    def __init__(self, error: str, message: str, stacktrace: str = "") -> None:
        self.error = error
        self.message = message
        self.stacktrace = stacktrace
        super().__init__(f"{error}: {message}")


class BiDiNotConnected(Exception):
    """Firefox's remote agent is not reachable / the socket dropped."""


def _loopback_ok(ws_url: str) -> bool:
    """Enforce the loopback-only hard constraint."""
    return ws_url.startswith("ws://127.0.0.1") or ws_url.startswith("ws://[::1]")


class BiDiClient:
    """A single, lazily-connected BiDi session to Firefox."""

    def __init__(self, ws_url: str = DEFAULT_WS_URL) -> None:
        if not _loopback_ok(ws_url):
            raise ValueError(f"Refusing non-loopback BiDi URL: {ws_url!r}")
        self.ws_url = ws_url
        self._ws: Optional[Any] = None
        self._next_id = 0
        self._pending: dict[int, asyncio.Future] = {}
        self._reader_task: Optional[asyncio.Task] = None
        self._connect_lock = asyncio.Lock()
        self._connected = False

        # Session info + event ring buffers.
        self.session_capabilities: dict[str, Any] = {}
        self.console_logs: deque[dict[str, Any]] = deque(maxlen=100)
        self.last_navigation: dict[str, Any] = {}

    # ------------------------------------------------------------------ #
    # Connection lifecycle
    # ------------------------------------------------------------------ #
    @property
    def connected(self) -> bool:
        return self._connected and self._ws is not None

    async def ensure_connected(self) -> None:
        """Connect if not already; safe to call before every command."""
        if self.connected:
            return
        async with self._connect_lock:
            if self.connected:
                return
            try:
                self._ws = await websockets.connect(
                    self.ws_url, max_size=None, open_timeout=_CONNECT_TIMEOUT
                )
            except (OSError, asyncio.TimeoutError, websockets.InvalidHandshake) as exc:
                self._ws = None
                raise BiDiNotConnected(
                    f"Could not connect to Firefox at {self.ws_url}. Make sure Firefox "
                    "is running and was launched with --remote-debugging-port 9222. "
                    "(Fully quit Firefox first, then relaunch with the flag - otherwise "
                    "the flag just opens a tab in the existing instance.)"
                ) from exc
            self._reader_task = asyncio.create_task(self._reader())
            try:
                await self._handshake()
            except BaseException:
                # Don't leave a half-open socket / reader on a failed handshake.
                await self._teardown_socket()
                raise
            self._connected = True

    async def _teardown_socket(self) -> None:
        self._connected = False
        if self._reader_task is not None:
            self._reader_task.cancel()
            self._reader_task = None
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None

    async def close(self) -> None:
        """End our BiDi session (freeing Firefox's single session slot) and close."""
        if self._connected and self._ws is not None:
            try:
                await asyncio.wait_for(self._send("session.end", {}), timeout=3)
            except Exception:
                pass
        await self._teardown_socket()

    async def _handshake(self) -> None:
        """Create a BiDi-only session and subscribe to events.

        Firefox's remote agent allows only ONE active BiDi session. If a stale
        session from a previously-crashed client is still held, session.new
        fails and a fresh connection cannot clear it - so we surface an
        actionable error telling the user to relaunch Firefox.
        """
        try:
            result = await self._send("session.new", {"capabilities": {}})
        except BiDiError as exc:
            msg = (exc.message or "").lower()
            if "maximum" in msg or "already started" in msg or "session not created" in msg:
                raise BiDiNotConnected(
                    "Firefox already has an active WebDriver BiDi session that this "
                    "connection can't reuse (Firefox allows only one). If another tool "
                    "is automating Firefox, close it; otherwise fully quit Firefox and "
                    "relaunch it with --remote-debugging-port 9222 to clear the stale "
                    "session."
                ) from exc
            raise BiDiNotConnected(f"Could not start a BiDi session: {exc.message}") from exc
        self.session_capabilities = result.get("capabilities", {})
        try:
            await self._send("session.subscribe", {"events": _SUBSCRIBED_EVENTS})
        except BiDiError:
            # Non-fatal: page tools still work without event streaming.
            pass

    # ------------------------------------------------------------------ #
    # Low-level send / receive
    # ------------------------------------------------------------------ #
    async def send(self, method: str, params: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        """Public command send; ensures the connection first."""
        await self.ensure_connected()
        return await self._send(method, params or {})

    async def _send(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        if self._ws is None:
            raise BiDiNotConnected("Not connected to Firefox.")
        self._next_id += 1
        msg_id = self._next_id
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[msg_id] = fut
        payload = json.dumps({"id": msg_id, "method": method, "params": params})
        try:
            await self._ws.send(payload)
        except (OSError, websockets.ConnectionClosed) as exc:
            self._pending.pop(msg_id, None)
            self._connected = False
            raise BiDiNotConnected("Connection to Firefox dropped while sending.") from exc
        try:
            return await asyncio.wait_for(fut, timeout=_COMMAND_TIMEOUT)
        finally:
            self._pending.pop(msg_id, None)

    async def _reader(self) -> None:
        assert self._ws is not None
        try:
            async for raw in self._ws:
                try:
                    msg = json.loads(raw)
                except (ValueError, TypeError):
                    continue
                mtype = msg.get("type")
                if mtype == "success":
                    self._resolve(msg.get("id"), msg.get("result", {}))
                elif mtype == "error":
                    self._reject(msg)
                elif mtype == "event":
                    self._handle_event(msg.get("method", ""), msg.get("params", {}))
        except (websockets.ConnectionClosed, OSError):
            pass
        finally:
            self._connected = False
            exc = BiDiNotConnected("Connection to Firefox closed.")
            for fut in self._pending.values():
                if not fut.done():
                    fut.set_exception(exc)
            self._pending.clear()

    def _resolve(self, msg_id: Any, result: dict[str, Any]) -> None:
        fut = self._pending.get(msg_id)
        if fut is not None and not fut.done():
            fut.set_result(result)

    def _reject(self, msg: dict[str, Any]) -> None:
        fut = self._pending.get(msg.get("id"))
        if fut is not None and not fut.done():
            fut.set_exception(
                BiDiError(
                    msg.get("error", "unknown error"),
                    msg.get("message", ""),
                    msg.get("stacktrace", ""),
                )
            )

    def _handle_event(self, method: str, params: dict[str, Any]) -> None:
        if method == "log.entryAdded":
            self.console_logs.append(params)
        elif method in ("browsingContext.navigationStarted", "browsingContext.load"):
            self.last_navigation = {"method": method, "params": params}

    # ------------------------------------------------------------------ #
    # High-level BiDi helpers used by the tools
    # ------------------------------------------------------------------ #
    async def session_status(self) -> dict[str, Any]:
        return await self.send("session.status", {})

    async def get_tree(self, max_depth: Optional[int] = None) -> list[dict[str, Any]]:
        params: dict[str, Any] = {}
        if max_depth is not None:
            params["maxDepth"] = max_depth
        result = await self.send("browsingContext.getTree", params)
        return result.get("contexts", [])

    async def navigate(self, context: str, url: str, wait: str = "complete") -> dict[str, Any]:
        return await self.send(
            "browsingContext.navigate", {"context": context, "url": url, "wait": wait}
        )

    async def create_context(
        self, type_: str = "tab", reference: Optional[str] = None, background: bool = False
    ) -> str:
        params: dict[str, Any] = {"type": type_, "background": background}
        if reference:
            params["referenceContext"] = reference
        result = await self.send("browsingContext.create", params)
        return result["context"]

    async def traverse_history(self, context: str, delta: int) -> dict[str, Any]:
        return await self.send(
            "browsingContext.traverseHistory", {"context": context, "delta": delta}
        )

    async def capture_screenshot(self, context: str) -> bytes:
        result = await self.send("browsingContext.captureScreenshot", {"context": context})
        return base64.b64decode(result["data"])

    async def perform_actions(self, context: str, actions: list[dict[str, Any]]) -> dict[str, Any]:
        return await self.send("input.performActions", {"context": context, "actions": actions})

    async def release_actions(self, context: str) -> dict[str, Any]:
        return await self.send("input.releaseActions", {"context": context})

    async def call_function(
        self,
        context: str,
        function_declaration: str,
        arguments: Optional[list[dict[str, Any]]] = None,
        await_promise: bool = True,
    ) -> dict[str, Any]:
        """Run a JS function in the context's *default* realm (shares page window).

        Returns the raw BiDi RemoteValue. Raises BiDiError if the script threw.
        """
        params: dict[str, Any] = {
            "functionDeclaration": function_declaration,
            "target": {"context": context},
            "arguments": arguments or [],
            "awaitPromise": await_promise,
            "resultOwnership": "none",
            "serializationOptions": {"maxObjectDepth": 5, "maxDomDepth": 0},
        }
        res = await self.send("script.callFunction", params)
        if res.get("type") == "exception":
            details = res.get("exceptionDetails", {})
            raise BiDiError("script.exception", details.get("text", str(details)))
        return res.get("result", {})

    async def eval_json(
        self,
        context: str,
        function_declaration: str,
        args: Optional[list[str]] = None,
        await_promise: bool = False,
    ) -> Any:
        """Call an injected function that returns a JSON string; parse it.

        String args are passed positionally to the function.
        """
        arguments = [{"type": "string", "value": a} for a in (args or [])]
        remote = await self.call_function(
            context, function_declaration, arguments, await_promise=await_promise
        )
        rtype = remote.get("type")
        if rtype in ("undefined", "null"):
            return None
        if rtype == "string":
            raw = remote.get("value", "")
            return json.loads(raw) if raw else None
        # Fallback: return the raw remote value for non-string results.
        return remote.get("value")
