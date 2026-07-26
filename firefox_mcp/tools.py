"""MCP tool definitions - thin wrappers over the BiDi client.

All page tools act on a single tracked "active context" (one tab). They never
touch other tabs. Interaction is only ever via element refs from ``snapshot``,
resolved server-side against ``window.__claudeRefs`` - never by coordinates
Claude supplies. Page content is untrusted: a tool does exactly and only what
its arguments say.
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable, Optional

from mcp.server.fastmcp import FastMCP, Image

from . import safety
from .bidi import BiDiClient, BiDiError, BiDiNotConnected
from .snapshot import READ_PAGE_JS, SNAPSHOT_JS, format_snapshot

mcp = FastMCP("firefox-mcp")

_client = BiDiClient()
_active: dict[str, Optional[str]] = {"context": None}

_READ_PAGE_CAP = 20_000


# ---------------------------------------------------------------------- #
# Injected page scripts for interaction (resolve refs server-side)
# ---------------------------------------------------------------------- #
_RESOLVE_CLICK_JS = r"""
(ref) => {
  const el = window.__claudeRefs && window.__claudeRefs.get(ref);
  if(!el) return JSON.stringify({found:false});
  if(!el.isConnected) return JSON.stringify({found:false, disconnected:true});
  el.scrollIntoView({block:'center', inline:'center'});
  const r = el.getBoundingClientRect();
  const tag = el.tagName ? el.tagName.toLowerCase() : '';
  const type = (el.getAttribute && (el.getAttribute('type') || '')).toLowerCase();
  const isSubmit = (type === 'submit') ||
                   (tag === 'button' && (el.type === 'submit' || (!el.type && !!el.form))) ||
                   (tag === 'input' && type === 'image');
  const name = ((el.innerText || el.value || (el.getAttribute && el.getAttribute('aria-label')) || '') + '')
                 .replace(/\s+/g, ' ').trim().slice(0, 120);
  const href = (tag === 'a') ? (el.getAttribute('href') || '') : '';
  return JSON.stringify({
    found:true,
    x: Math.round(r.left + r.width / 2),
    y: Math.round(r.top + r.height / 2),
    w: r.width, h: r.height,
    tag, type, name, href,
    inForm: !!(el.closest && el.closest('form')),
    isSubmit: !!isSubmit
  });
}
"""

_RESOLVE_TYPE_JS = r"""
(ref) => {
  const el = window.__claudeRefs && window.__claudeRefs.get(ref);
  if(!el) return JSON.stringify({found:false});
  if(!el.isConnected) return JSON.stringify({found:false, disconnected:true});
  el.scrollIntoView({block:'center'});
  try { el.focus({preventScroll:true}); } catch(e){}
  const tag = el.tagName ? el.tagName.toLowerCase() : '';
  const type = (el.getAttribute && (el.getAttribute('type') || '')).toLowerCase();
  const ac = (el.getAttribute && (el.getAttribute('autocomplete') || '')).toLowerCase();
  const editable = (tag === 'input' || tag === 'textarea' || el.isContentEditable);
  return JSON.stringify({
    found:true, tag, type, autocomplete:ac,
    isPassword: type === 'password', editable,
    focused: (document.activeElement === el),
    inForm: !!(el.closest && el.closest('form'))
  });
}
"""

_SELECT_OPTION_JS = r"""
(ref, value) => {
  const el = window.__claudeRefs && window.__claudeRefs.get(ref);
  if(!el) return JSON.stringify({found:false});
  if(!el.tagName || el.tagName.toLowerCase() !== 'select') return JSON.stringify({found:true, notSelect:true});
  let matched = false;
  for(const opt of el.options){
    if(opt.value === value || opt.text === value){ el.value = opt.value; matched = true; break; }
  }
  if(!matched) return JSON.stringify({found:true, matched:false, options: Array.from(el.options).map(function(o){ return o.value; })});
  el.dispatchEvent(new Event('input', {bubbles:true}));
  el.dispatchEvent(new Event('change', {bubbles:true}));
  return JSON.stringify({found:true, matched:true, value: el.value});
}
"""

_FOCUSED_INFO_JS = r"""
() => {
  const el = document.activeElement;
  if(!el) return JSON.stringify({none:true});
  const tag = el.tagName ? el.tagName.toLowerCase() : '';
  const type = (el.getAttribute && (el.getAttribute('type') || '')).toLowerCase();
  return JSON.stringify({tag, type, inForm: !!(el.closest && el.closest('form')), isSubmit: type === 'submit'});
}
"""

_SCROLL_JS = r"""
(dir, amount) => {
  const n = parseInt(amount, 10) || 500;
  let dx = 0, dy = 0;
  if(dir === 'down') dy = n; else if(dir === 'up') dy = -n;
  else if(dir === 'right') dx = n; else if(dir === 'left') dx = -n;
  window.scrollBy({left:dx, top:dy, behavior:'instant'});
  return JSON.stringify({x: window.scrollX, y: window.scrollY,
                         maxY: Math.max(0, document.documentElement.scrollHeight - window.innerHeight)});
}
"""

_KEY_MAP = {
    "enter": "\uE007", "return": "\uE006", "tab": "\uE004",
    "escape": "\uE00C", "esc": "\uE00C", "backspace": "\uE003", "delete": "\uE017",
    "space": " ", "arrowup": "\uE013", "up": "\uE013", "arrowdown": "\uE015", "down": "\uE015",
    "arrowleft": "\uE012", "left": "\uE012", "arrowright": "\uE014", "right": "\uE014",
    "home": "\uE011", "end": "\uE010", "pageup": "\uE00E", "pagedown": "\uE00F",
}


# ---------------------------------------------------------------------- #
# Shared helpers
# ---------------------------------------------------------------------- #
async def _safe(fn: Callable[[], Awaitable[Any]]) -> Any:
    """Run a tool body, translating connection/BiDi errors into readable text."""
    try:
        return await fn()
    except BiDiNotConnected as exc:
        return f"Not connected to Firefox. {exc}"
    except BiDiError as exc:
        return f"Firefox/BiDi error: {exc}"


async def _top_contexts() -> list[dict[str, Any]]:
    return await _client.get_tree()


async def _active_context_info() -> tuple[str, str]:
    """Return (context_id, url) for the active tab, defaulting to the first tab."""
    contexts = await _top_contexts()
    if not contexts:
        raise BiDiError("no-tabs", "No open tabs in Firefox.")
    by_id = {c["context"]: c for c in contexts}
    cur = _active["context"]
    if cur not in by_id:
        cur = contexts[0]["context"]
        _active["context"] = cur
    return cur, by_id[cur].get("url", "")


def _require_domain(url: str) -> Optional[str]:
    ok, reason = safety.domain_check(url)
    return None if ok else f"Refused: {reason}"


async def _tab_title(ctx: str) -> str:
    try:
        remote = await _client.call_function(ctx, "() => document.title", [], await_promise=False)
        return remote.get("value", "") if remote.get("type") == "string" else ""
    except BiDiError:
        return ""


def _click_actions(x: int, y: int) -> list[dict[str, Any]]:
    return [{
        "type": "pointer", "id": "mouse", "parameters": {"pointerType": "mouse"},
        "actions": [
            {"type": "pointerMove", "x": int(x), "y": int(y), "origin": "viewport"},
            {"type": "pointerDown", "button": 0},
            {"type": "pointerUp", "button": 0},
        ],
    }]


def _key_actions(values: list[tuple[str, str]]) -> list[dict[str, Any]]:
    """values: list of (down_value, up_value); usually the same char."""
    acts: list[dict[str, Any]] = []
    for down, up in values:
        acts.append({"type": "keyDown", "value": down})
        acts.append({"type": "keyUp", "value": up})
    return [{"type": "key", "id": "kbd", "actions": acts}]


# ---------------------------------------------------------------------- #
# Status / tabs
# ---------------------------------------------------------------------- #
@mcp.tool()
async def firefox_status() -> str:
    """Report whether this server is attached to Tyler's own Firefox, plus its
    version and the active tab's URL/title.

    This is specifically Tyler's real, logged-in Firefox, reached over WebDriver
    BiDi on 127.0.0.1:9222 - NOT Claude Code's in-app browser pane, and NOT Chrome.

    Use this first to confirm the server can reach Firefox. If it can't, it tells
    you how to relaunch Firefox with the remote-debugging flag.
    """
    async def _run() -> str:
        await _client.ensure_connected()
        caps = _client.session_capabilities or {}
        name = caps.get("browserName", "firefox")
        version = caps.get("browserVersion", "?")
        try:
            ctx, url = await _active_context_info()
            title = await _tab_title(ctx)
            tab = f'\nActive tab: "{title}" - {url}'
        except BiDiError:
            tab = "\nActive tab: (none)"
        return f"Connected to {name} {version}.{tab}"
    return await _safe(_run)


@mcp.tool()
async def list_tabs() -> str:
    """List the open top-level tabs (index, title, URL) and mark the active one.

    Indexes are what `select_tab` takes.
    """
    async def _run() -> str:
        contexts = await _top_contexts()
        if not contexts:
            return "No open tabs."
        active, _ = await _active_context_info()
        lines = []
        for i, c in enumerate(contexts):
            ctx = c["context"]
            title = await _tab_title(ctx)
            marker = " *active*" if ctx == active else ""
            lines.append(f'[{i}] "{title}" - {c.get("url", "")}{marker}')
        return "\n".join(lines)
    return await _safe(_run)


@mcp.tool()
async def select_tab(index: int) -> str:
    """Set the active tab by its index from `list_tabs`. All page tools act on it."""
    async def _run() -> str:
        contexts = await _top_contexts()
        if index < 0 or index >= len(contexts):
            return f"No tab at index {index}. There are {len(contexts)} tab(s)."
        ctx = contexts[index]["context"]
        _active["context"] = ctx
        title = await _tab_title(ctx)
        return f'Active tab is now [{index}] "{title}" - {contexts[index].get("url", "")}'
    return await _safe(_run)


@mcp.tool()
async def new_tab(url: str = "") -> str:
    """Open a new tab (optionally at `url`) and make it the active tab."""
    async def _run() -> str:
        ctx = await _client.create_context(type_="tab")
        _active["context"] = ctx
        if url:
            blocked = _require_domain(url)
            if blocked:
                return f"Opened a blank new tab, but {blocked}"
            await _client.navigate(ctx, url, wait="complete")
        return f"Opened new tab (context {ctx[:8]}...){' at ' + url if url else ''}."
    return await _safe(_run)


@mcp.tool()
async def navigate(url: str) -> str:
    """Navigate the active tab to `url` and wait for it to finish loading."""
    async def _run() -> str:
        ctx, _ = await _active_context_info()
        blocked = _require_domain(url)
        if blocked:
            return blocked
        result = await _client.navigate(ctx, url, wait="complete")
        return f"Navigated to {result.get('url', url)}."
    return await _safe(_run)


@mcp.tool()
async def back() -> str:
    """Go back one entry in the active tab's history."""
    async def _run() -> str:
        ctx, _ = await _active_context_info()
        await _client.traverse_history(ctx, -1)
        _, url = await _active_context_info()
        return f"Went back. Now at {url}."
    return await _safe(_run)


@mcp.tool()
async def forward() -> str:
    """Go forward one entry in the active tab's history."""
    async def _run() -> str:
        ctx, _ = await _active_context_info()
        await _client.traverse_history(ctx, 1)
        _, url = await _active_context_info()
        return f"Went forward. Now at {url}."
    return await _safe(_run)


# ---------------------------------------------------------------------- #
# Reading the page
# ---------------------------------------------------------------------- #
@mcp.tool()
async def snapshot() -> str:
    """Return an element-ref tree of the active tab.

    Each interactive element gets a ref like `e1`, `e2`. Pass those refs to
    `click`, `type`, and `select_option`. Re-run `snapshot` after the page
    changes or navigates - refs from an old snapshot go stale.
    """
    async def _run() -> str:
        ctx, url = await _active_context_info()
        blocked = _require_domain(url)
        if blocked:
            return blocked
        data = await _client.eval_json(ctx, SNAPSHOT_JS)
        return format_snapshot(data or {})
    return await _safe(_run)


@mcp.tool()
async def read_page() -> str:
    """Return the readable text of the active tab (innerText, trimmed to ~20k chars)."""
    async def _run() -> str:
        ctx, url = await _active_context_info()
        blocked = _require_domain(url)
        if blocked:
            return blocked
        data = await _client.eval_json(ctx, READ_PAGE_JS)
        if not data:
            return "(no text - is a page loaded?)"
        text = data.get("text", "")
        header = f'Page: "{data.get("title", "")}" ({data.get("url", "")})\n\n'
        if len(text) > _READ_PAGE_CAP:
            text = text[:_READ_PAGE_CAP] + "\n...[truncated]"
        return header + text
    return await _safe(_run)


@mcp.tool()
async def screenshot() -> Image:
    """Return a PNG screenshot of the active tab."""
    ctx, url = await _active_context_info()
    ok, reason = safety.domain_check(url)
    if not ok:
        raise BiDiError("blocked", reason)
    png = await _client.capture_screenshot(ctx)
    return Image(data=png, format="png")


# ---------------------------------------------------------------------- #
# Interaction
# ---------------------------------------------------------------------- #
@mcp.tool()
async def click(ref: str, confirmed: bool = False) -> str:
    """Click the element with the given ref (from `snapshot`).

    Consequential clicks (submit buttons, or links/buttons whose text or URL
    looks like send/buy/pay/delete/sign in/checkout/...) are confirmation-gated:
    ask Tyler in the chat first, then call again with confirmed=true.
    """
    async def _run() -> str:
        ctx, url = await _active_context_info()
        blocked = _require_domain(url)
        if blocked:
            return blocked
        info = await _client.eval_json(ctx, _RESOLVE_CLICK_JS, [ref])
        if not info or not info.get("found"):
            return f"Ref '{ref}' is stale (element gone or page changed). Re-run snapshot."
        needs, reason = safety.click_requires_confirmation(info)
        if needs and not confirmed:
            return safety.CONFIRMATION_MESSAGE.format(reason=reason)
        await _client.perform_actions(ctx, _click_actions(info["x"], info["y"]))
        await _client.release_actions(ctx)
        return f'Clicked [{ref}] {info.get("tag", "")} "{info.get("name", "")}".'
    return await _safe(_run)


@mcp.tool(name="type")
async def type_text(ref: str, text: str, submit: bool = False, confirmed: bool = False) -> str:
    """Type `text` into the element with the given ref. If `submit`, press Enter after.

    Refuses to type into password / credential fields - type those yourself.

    If this may submit a form - the `submit` flag, or a newline in `text` typed
    into a single-line input - it is confirmation-gated: ask Tyler in the chat to
    confirm, then call again with confirmed=true. Plain typing without a newline
    (and submit into a non-form field like a search box) is never gated.
    """
    async def _run() -> str:
        ctx, url = await _active_context_info()
        blocked = _require_domain(url)
        if blocked:
            return blocked
        info = await _client.eval_json(ctx, _RESOLVE_TYPE_JS, [ref])
        if not info or not info.get("found"):
            return f"Ref '{ref}' is stale (element gone or page changed). Re-run snapshot."
        if safety.is_password_target(info):
            return ("Refused: this is a password / credential field. For your security, "
                    "type it in yourself - I won't enter passwords.")
        if not info.get("editable"):
            return f"Ref '{ref}' is a {info.get('tag', '?')}, not a text field. Use click instead?"
        needs_confirm, reason = safety.type_submit_requires_confirmation(text, submit, info)
        if needs_confirm and not confirmed:
            return safety.CONFIRMATION_MESSAGE.format(reason=reason)
        values = [(ch, ch) for ch in text]
        await _client.perform_actions(ctx, _key_actions(values))
        if submit:
            await _client.perform_actions(ctx, _key_actions([("\uE007", "\uE007")]))
        await _client.release_actions(ctx)
        suffix = " and pressed Enter" if submit else ""
        return f'Typed into [{ref}]{suffix}.'
    return await _safe(_run)


@mcp.tool()
async def select_option(ref: str, value: str) -> str:
    """Set a <select> dropdown (by ref) to `value` (matches option value or label)."""
    async def _run() -> str:
        ctx, url = await _active_context_info()
        blocked = _require_domain(url)
        if blocked:
            return blocked
        info = await _client.eval_json(ctx, _SELECT_OPTION_JS, [ref, value])
        if not info or not info.get("found"):
            return f"Ref '{ref}' is stale. Re-run snapshot."
        if info.get("notSelect"):
            return f"Ref '{ref}' is not a <select> element."
        if not info.get("matched"):
            opts = ", ".join(info.get("options", [])[:20])
            return f"No option matching '{value}'. Available values: {opts}"
        return f"Set [{ref}] to '{info.get('value')}'."
    return await _safe(_run)


@mcp.tool()
async def press_key(key: str, confirmed: bool = False) -> str:
    """Press a single key on the active tab (Enter, Escape, Tab, arrows, Backspace, ...).

    Enter is confirmation-gated when the focused element is inside a form (it may
    submit): ask Tyler first, then call again with confirmed=true.
    """
    async def _run() -> str:
        ctx, url = await _active_context_info()
        blocked = _require_domain(url)
        if blocked:
            return blocked
        value = _KEY_MAP.get(key.strip().lower())
        if value is None:
            if len(key.strip()) == 1:
                value = key.strip()
            else:
                return f"Unknown key '{key}'. Try: Enter, Escape, Tab, Backspace, Delete, arrows, Home, End, PageUp, PageDown."
        focused = await _client.eval_json(ctx, _FOCUSED_INFO_JS) or {}
        needs, reason = safety.press_key_requires_confirmation(key, focused)
        if needs and not confirmed:
            return safety.CONFIRMATION_MESSAGE.format(reason=reason)
        await _client.perform_actions(ctx, _key_actions([(value, value)]))
        await _client.release_actions(ctx)
        return f"Pressed {key}."
    return await _safe(_run)


@mcp.tool()
async def scroll(direction: str, amount: int = 500) -> str:
    """Scroll the active tab. `direction` is up/down/left/right; `amount` is pixels."""
    async def _run() -> str:
        d = direction.strip().lower()
        if d not in ("up", "down", "left", "right"):
            return "direction must be one of: up, down, left, right."
        ctx, url = await _active_context_info()
        blocked = _require_domain(url)
        if blocked:
            return blocked
        pos = await _client.eval_json(ctx, _SCROLL_JS, [d, str(int(amount))]) or {}
        return f"Scrolled {d}. Now at y={pos.get('y', '?')} (max {pos.get('maxY', '?')})."
    return await _safe(_run)


@mcp.tool()
async def console_logs() -> str:
    """Show recent browser console log entries from the active session (last 100)."""
    async def _run() -> str:
        await _client.ensure_connected()
        entries = list(_client.console_logs)
        if not entries:
            return "(no console entries captured yet)"
        lines = []
        for e in entries[-40:]:
            level = e.get("level", "log")
            text = e.get("text", "")
            if not text and e.get("args"):
                text = " ".join(str(a.get("value", "")) for a in e.get("args", []))
            lines.append(f"[{level}] {text}")
        return "\n".join(lines)
    return await _safe(_run)
