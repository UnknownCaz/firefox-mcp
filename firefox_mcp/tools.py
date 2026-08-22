"""MCP tool definitions - thin wrappers over the BiDi client.

All page tools act on a single tracked "active context" (one tab). They never
touch other tabs. Interaction is only ever via element refs from ``snapshot``,
resolved server-side against ``window.__claudeRefs`` - never by coordinates
Claude supplies. Page content is untrusted: a tool does exactly and only what
its arguments say.

Anything read out of a page leaves here fenced as untrusted data (see
``untrusted``): read_page, snapshot and console_logs get a tagged block, and
short page-controlled strings - tab titles, URLs - are flattened to one bounded
line so they cannot fake structure. The rule is that no page-authored text
reaches the model outside a marked boundary.
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable, Optional

from mcp.server.fastmcp import FastMCP, Image

from . import safety, untrusted
from .bidi import BiDiClient, BiDiError, BiDiNotConnected
from .snapshot import READ_PAGE_JS, SNAPSHOT_JS, format_snapshot

mcp = FastMCP("firefox-mcp")

# ---------------------------------------------------------------------- #
# Browser targets
#
# Two Firefoxes, deliberately kept apart:
#   main    - the user's real, logged-in profile. Everything they care about.
#   sandbox - a throwaway profile on its own port, started by
#             start-firefox-sandbox.ps1. Not signed in to anything.
#
# One server switches between them rather than registering two servers,
# because two registrations would put 32 near-identical tools in the model's
# context and invite acting on the wrong one. There is exactly one active
# target; every page tool acts on it and says which when it matters.
#
# Each target gets its own BiDiClient (its own session, socket, and console
# ring buffer) and its own remembered active tab, so switching back and forth
# does not disturb either browser's state.
# ---------------------------------------------------------------------- #
BROWSER_TARGETS: dict[str, dict[str, str]] = {
    "main": {
        "ws": "ws://127.0.0.1:9222/session",
        "desc": "the user's own logged-in Firefox (their tabs, logins, extensions)",
    },
    "sandbox": {
        "ws": "ws://127.0.0.1:9223/session",
        "desc": "throwaway automation profile - not signed in to anything",
    },
}
DEFAULT_TARGET = "main"

_clients: dict[str, BiDiClient] = {}
_active_context: dict[str, Optional[str]] = {}
_target: dict[str, str] = {"name": DEFAULT_TARGET}

_READ_PAGE_CAP = 20_000


def current_target() -> str:
    return _target["name"]


def active_client() -> BiDiClient:
    """The BiDiClient for the current target, created lazily.

    Lazy on purpose: constructing a client must not connect, so a sandbox that
    was never started costs nothing and never produces a spurious error.
    """
    name = _target["name"]
    if name not in _clients:
        _clients[name] = BiDiClient(BROWSER_TARGETS[name]["ws"])
    return _clients[name]


async def close_all() -> None:
    """End every open BiDi session. Called on shutdown.

    Must cover ALL targets, not just the active one - a sandbox session left
    open holds that Firefox's single session slot and the next server start
    fails with "Maximum number of active sessions".
    """
    for client in list(_clients.values()):
        try:
            await client.close()
        except Exception:
            pass


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

# Must report the SAME fields _RESOLVE_CLICK_JS does. When this returned only
# {tag, type, inForm, isSubmit:type==='submit'} the keyboard path was blind to
# submit <button>s, <input type=image>, ARIA roles, and the element's own label -
# so press_key sailed past gates that click enforced on the identical element.
_FOCUSED_INFO_JS = r"""
() => {
  const el = document.activeElement;
  if(!el) return JSON.stringify({none:true});
  const tag = el.tagName ? el.tagName.toLowerCase() : '';
  const type = (el.getAttribute && (el.getAttribute('type') || '')).toLowerCase();
  const isSubmit = (type === 'submit') ||
                   (tag === 'button' && (el.type === 'submit' || (!el.type && !!el.form))) ||
                   (tag === 'input' && type === 'image');
  const name = ((el.innerText || el.value || (el.getAttribute && el.getAttribute('aria-label')) || '') + '')
                 .replace(/\s+/g, ' ').trim().slice(0, 120);
  const href = (tag === 'a') ? (el.getAttribute('href') || '') : '';
  const role = (el.getAttribute && (el.getAttribute('role') || '')).toLowerCase();
  return JSON.stringify({
    tag, type, name, href, role,
    inForm: !!(el.closest && el.closest('form')),
    isSubmit: !!isSubmit
  });
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
def _readable_error(exc: BaseException) -> str:
    """Phrase a connection/BiDi failure the way a person would want to read it."""
    if isinstance(exc, BiDiNotConnected):
        return f"Not connected to Firefox. {exc}"
    return f"Firefox/BiDi error: {exc}"


async def _safe(fn: Callable[[], Awaitable[Any]]) -> Any:
    """Run a tool body, translating connection/BiDi errors into readable text."""
    try:
        return await fn()
    except (BiDiNotConnected, BiDiError) as exc:
        return _readable_error(exc)


async def _top_contexts() -> list[dict[str, Any]]:
    return await active_client().get_tree()


async def _active_context_info() -> tuple[str, str]:
    """Return (context_id, url) for the active tab, defaulting to the first tab."""
    contexts = await _top_contexts()
    if not contexts:
        raise BiDiError("no-tabs", "No open tabs in Firefox.")
    by_id = {c["context"]: c for c in contexts}
    cur = _active_context.get(_target["name"])
    if cur not in by_id:
        cur = contexts[0]["context"]
        _active_context[_target["name"]] = cur
    return cur, by_id[cur].get("url", "")


def _require_domain(url: str) -> Optional[str]:
    ok, reason = safety.domain_check(url)
    return None if ok else f"Refused: {reason}"


async def _tab_title(ctx: str) -> str:
    """The tab's title, flattened to one bounded line.

    document.title is page-controlled. Fencing a one-line title would cost more
    context than it protects, but a raw one could carry newlines and a forged
    marker into list_tabs / firefox_status output, so it gets neutralized here -
    once, at the single place every caller goes through.
    """
    try:
        remote = await active_client().call_function(ctx, "() => document.title", [], await_promise=False)
        title = remote.get("value", "") if remote.get("type") == "string" else ""
    except BiDiError:
        return ""
    return untrusted.inline(title)


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
async def switch_browser(target: str = "") -> str:
    """Choose which Firefox the page tools act on: 'main' or 'sandbox'.

    - main    - the user's real, logged-in Firefox on port 9222. Their tabs,
                their accounts, their extensions. Use this to see or act on
                what they are actually doing.
    - sandbox - a throwaway automation profile on port 9223, signed in to
                nothing. Use this for browsing, scraping, or testing that should
                not touch their session. Start it with start-firefox-sandbox.ps1.

    Call with no argument to see the current target and what is available.
    Switching does not disturb either browser - each keeps its own session and
    its own active tab.
    """
    if not target:
        lines = [f"Current target: {current_target()}", "", "Available:"]
        for name, meta in BROWSER_TARGETS.items():
            mark = " <- current" if name == current_target() else ""
            live = "connected" if _clients.get(name) and _clients[name].connected else "not connected"
            lines.append(f"  {name:8} {meta['ws']}  [{live}] - {meta['desc']}{mark}")
        return "\n".join(lines)

    key = target.strip().lower()
    if key not in BROWSER_TARGETS:
        return (f"Unknown target '{target}'. Choose one of: "
                f"{', '.join(BROWSER_TARGETS)}.")
    _target["name"] = key
    meta = BROWSER_TARGETS[key]

    # Report reachability now rather than letting the next tool fail: switching
    # to a sandbox that was never started is the expected mistake, and the fix
    # (run the launcher) is worth saying up front.
    async def _run() -> str:
        await active_client().ensure_connected()
        caps = active_client().session_capabilities or {}
        return (f"Target is now '{key}' ({meta['desc']}). "
                f"Connected to {caps.get('browserName', 'firefox')} "
                f"{caps.get('browserVersion', '?')} at {meta['ws']}.")
    result = await _safe(_run)
    if isinstance(result, str) and result.startswith("Not connected"):
        hint = (" Run start-firefox-sandbox.ps1 to start it."
                if key == "sandbox" else
                " Run start-firefox-debug.bat to start it.")
        return f"Target is now '{key}', but it is not reachable. {result}{hint}"
    return result


@mcp.tool()
async def firefox_status() -> str:
    """Report which Firefox this server is attached to, its version, and the
    active tab's URL/title.

    Reached over WebDriver BiDi on loopback - NOT Claude Code's in-app browser
    pane, and NOT Chrome. Says which target is current ('main' = the user's real
    logged-in Firefox, 'sandbox' = the throwaway automation profile); use
    switch_browser to change it.

    Use this first to confirm the server can reach Firefox. If it can't, it tells
    you how to relaunch Firefox with the remote-debugging flag.
    """
    async def _run() -> str:
        await active_client().ensure_connected()
        caps = active_client().session_capabilities or {}
        name = caps.get("browserName", "firefox")
        version = caps.get("browserVersion", "?")
        tgt = current_target()
        head = f"Connected to {name} {version} [target: {tgt} - {BROWSER_TARGETS[tgt]['desc']}]."
        try:
            ctx, url = await _active_context_info()
            title = await _tab_title(ctx)
            tab = f'\nActive tab: "{title}" - {url}'
        except BiDiError:
            tab = "\nActive tab: (none)"
        return f"{head}{tab}"
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
            lines.append(f'[{i}] "{title}" - {untrusted.inline(c.get("url", ""))}{marker}')
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
        _active_context[_target["name"]] = ctx
        title = await _tab_title(ctx)
        return (f'Active tab is now [{index}] "{title}" - '
                f'{untrusted.inline(contexts[index].get("url", ""))}')
    return await _safe(_run)


@mcp.tool()
async def new_tab(url: str = "") -> str:
    """Open a new tab (optionally at `url`) and make it the active tab."""
    async def _run() -> str:
        ctx = await active_client().create_context(type_="tab")
        _active_context[_target["name"]] = ctx
        if url:
            blocked = _require_domain(url)
            if blocked:
                return f"Opened a blank new tab, but {blocked}"
            await active_client().navigate(ctx, url, wait="complete")
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
        result = await active_client().navigate(ctx, url, wait="complete")
        return f"Navigated to {untrusted.inline(result.get('url', url))}."
    return await _safe(_run)


@mcp.tool()
async def back() -> str:
    """Go back one entry in the active tab's history."""
    async def _run() -> str:
        ctx, _ = await _active_context_info()
        await active_client().traverse_history(ctx, -1)
        _, url = await _active_context_info()
        return f"Went back. Now at {url}."
    return await _safe(_run)


@mcp.tool()
async def forward() -> str:
    """Go forward one entry in the active tab's history."""
    async def _run() -> str:
        ctx, _ = await _active_context_info()
        await active_client().traverse_history(ctx, 1)
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
        data = await active_client().eval_json(ctx, SNAPSHOT_JS)
        # Element labels, aria-labels and hrefs are all page-authored, so the
        # whole tree is untrusted - the refs are ours, the words around them
        # are not.
        return untrusted.fence(format_snapshot(data or {}), source="snapshot", url=url)
    return await _safe(_run)


@mcp.tool()
async def read_page() -> str:
    """Return the readable text of the active tab (innerText, trimmed to ~20k chars)."""
    async def _run() -> str:
        ctx, url = await _active_context_info()
        blocked = _require_domain(url)
        if blocked:
            return blocked
        data = await active_client().eval_json(ctx, READ_PAGE_JS)
        if not data:
            return "(no text - is a page loaded?)"
        text = data.get("text", "")
        if len(text) > _READ_PAGE_CAP:
            text = text[:_READ_PAGE_CAP] + "\n...[truncated]"
        # The title goes INSIDE the fence: it is page-controlled like the body,
        # and it used to sit in an unfenced header where a crafted <title> would
        # have read as top-level text.
        body = f'Page title: {untrusted.inline(data.get("title", ""))}\n\n{text}'
        return untrusted.fence(body, source="read_page", url=data.get("url", ""))
    return await _safe(_run)


@mcp.tool()
async def screenshot() -> Image:
    """Return a PNG screenshot of the active tab."""
    # The only tool that cannot use _safe: it returns an Image, so it has no
    # way to hand back an error STRING without lying about its return type.
    # Raising with the same readable text is the honest equivalent - previously
    # this leaked a raw BiDiNotConnected traceback, and it is the tool most
    # likely to be called when Firefox is already down.
    try:
        ctx, url = await _active_context_info()
        ok, reason = safety.domain_check(url)
        if not ok:
            raise RuntimeError(f"Refused: {reason}")
        png = await active_client().capture_screenshot(ctx)
    except (BiDiNotConnected, BiDiError) as exc:
        raise RuntimeError(_readable_error(exc)) from exc
    return Image(data=png, format="png")


# ---------------------------------------------------------------------- #
# Interaction
# ---------------------------------------------------------------------- #
@mcp.tool()
async def click(ref: str, confirmed: bool = False) -> str:
    """Click the element with the given ref (from `snapshot`).

    Consequential clicks (submit buttons, or links/buttons whose text or URL
    looks like send/buy/pay/delete/sign in/checkout/...) are confirmation-gated:
    ask the user in the chat first, then call again with confirmed=true.
    """
    async def _run() -> str:
        ctx, url = await _active_context_info()
        blocked = _require_domain(url)
        if blocked:
            return blocked
        info = await active_client().eval_json(ctx, _RESOLVE_CLICK_JS, [ref])
        if not info or not info.get("found"):
            return f"Ref '{ref}' is stale (element gone or page changed). Re-run snapshot."
        needs, reason = safety.click_requires_confirmation(info)
        if needs and not confirmed:
            return safety.CONFIRMATION_MESSAGE.format(reason=reason)
        await active_client().perform_actions(ctx, _click_actions(info["x"], info["y"]))
        await active_client().release_actions(ctx)
        return f'Clicked [{ref}] {info.get("tag", "")} "{info.get("name", "")}".'
    return await _safe(_run)


@mcp.tool(name="type")
async def type_text(ref: str, text: str, submit: bool = False, confirmed: bool = False) -> str:
    """Type `text` into the element with the given ref. If `submit`, press Enter after.

    Refuses to type into password / credential fields - type those yourself.

    If this may submit a form - the `submit` flag, or a newline in `text` typed
    into a single-line input - it is confirmation-gated: ask the user in the chat to
    confirm, then call again with confirmed=true. Plain typing without a newline
    (and submit into a non-form field like a search box) is never gated.
    """
    async def _run() -> str:
        ctx, url = await _active_context_info()
        blocked = _require_domain(url)
        if blocked:
            return blocked
        info = await active_client().eval_json(ctx, _RESOLVE_TYPE_JS, [ref])
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
        await active_client().perform_actions(ctx, _key_actions(values))
        if submit:
            await active_client().perform_actions(ctx, _key_actions([("\uE007", "\uE007")]))
        await active_client().release_actions(ctx)
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
        info = await active_client().eval_json(ctx, _SELECT_OPTION_JS, [ref, value])
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

    Enter and Space are confirmation-gated whenever they could activate the
    focused element - a submit control, anything inside a form (Enter), or a
    focused button/link/checkbox. Ask the user first, then call again with
    confirmed=true. Navigation keys (arrows, Tab, Escape, Home/End, PageUp/Down)
    are never gated.
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
        focused = await active_client().eval_json(ctx, _FOCUSED_INFO_JS) or {}
        needs, reason = safety.press_key_requires_confirmation(key, focused)
        if needs and not confirmed:
            return safety.CONFIRMATION_MESSAGE.format(reason=reason)
        await active_client().perform_actions(ctx, _key_actions([(value, value)]))
        await active_client().release_actions(ctx)
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
        pos = await active_client().eval_json(ctx, _SCROLL_JS, [d, str(int(amount))]) or {}
        return f"Scrolled {d}. Now at y={pos.get('y', '?')} (max {pos.get('maxY', '?')})."
    return await _safe(_run)


@mcp.tool()
async def console_logs() -> str:
    """Show recent browser console log entries from the active session (last 100)."""
    async def _run() -> str:
        await active_client().ensure_connected()
        entries = list(active_client().console_logs)
        if not entries:
            return "(no console entries captured yet)"
        lines = []
        for e in entries[-40:]:
            level = e.get("level", "log")
            text = e.get("text", "")
            if not text and e.get("args"):
                text = " ".join(str(a.get("value", "")) for a in e.get("args", []))
            lines.append(f"[{level}] {text}")
        # Console output is page-authored too - console.log() is as good a
        # delivery channel for an injected instruction as body text, and an
        # easier one to overlook because it reads like debug output.
        return untrusted.fence("\n".join(lines), source="console_logs")
    return await _safe(_run)
