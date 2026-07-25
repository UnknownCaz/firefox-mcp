"""Safety guardrails: gated actions, password refusal, domain allow/deny.

This is a *guardrail, not a sandbox*. It structurally enforces that certain
consequential actions (submitting, buying, deleting, signing in, ...) can only
proceed after Claude has passed ``confirmed=True`` - which Claude may only set
after asking Tyler in the chat. The server cannot see the chat, so the flag is
the structural stand-in for that confirmation.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse

CONFIG_PATH = Path.home() / ".config" / "firefox-mcp" / "config.json"

# Substrings that, in a link/button's text or href, mark a consequential action.
GATED_KEYWORDS = [
    "send", "submit", "buy", "pay", "order", "confirm", "delete",
    "unsubscribe", "download", "sign in", "signin", "log in", "login", "checkout",
]

# Domains blocked by default (user-editable in config.json). Entries are matched
# by host boundary (exact host or subdomain), so they must be real domains /
# suffixes, not bare keywords. These are EXAMPLES - Tyler should tune them.
DEFAULT_BLOCKED_DOMAINS = [
    "paypal.com", "venmo.com", "coinbase.com", "wellsfargo.com", "chase.com",
]


# ---------------------------------------------------------------------- #
# Config
# ---------------------------------------------------------------------- #
def load_config() -> dict[str, Any]:
    """Load ~/.config/firefox-mcp/config.json, falling back to defaults."""
    try:
        raw = CONFIG_PATH.read_text(encoding="utf-8")
        data = json.loads(raw)
    except (OSError, ValueError):
        data = {}
    blocked = data.get("blockedDomains")
    if blocked is None:
        blocked = list(DEFAULT_BLOCKED_DOMAINS)
    allowed = data.get("allowedDomains") or []
    return {
        "blockedDomains": [str(d).lower() for d in blocked],
        "allowedDomains": [str(d).lower() for d in allowed],
    }


# ---------------------------------------------------------------------- #
# Domain allow / deny
# ---------------------------------------------------------------------- #
def host_of(url: str) -> str:
    try:
        # Strip a trailing FQDN dot ("example.com." -> "example.com") so it
        # cannot dodge host-boundary matching.
        return (urlparse(url).hostname or "").rstrip(".").lower()
    except ValueError:
        return ""


def scheme_of(url: str) -> str:
    try:
        return (urlparse(url).scheme or "").lower()
    except ValueError:
        return ""


def _host_matches(host: str, entry: str) -> bool:
    """True if ``host`` equals ``entry`` or is a subdomain of it (host-boundary match).

    Not a substring match - "example.com" matches "sub.example.com" but not
    "example.com.evil.test" or "notexample.com".
    """
    entry = (entry or "").strip().lower().strip(".")
    if not entry:
        return False
    host = (host or "").lower()
    return host == entry or host.endswith("." + entry)


def domain_check(url: str, config: Optional[dict[str, Any]] = None) -> tuple[bool, str]:
    """Return (allowed, reason).

    Hostless URLs are decided by scheme: benign browser-internal pages
    (about:blank, about:*) are always allowed; data-bearing local schemes
    (file:, data:, blob:, javascript:) are allowed only when NO allowlist is set -
    if an allowlist is configured to confine the agent, they must not slip through
    (e.g. file:// local-file reads).
    """
    cfg = config or load_config()
    allowed = cfg.get("allowedDomains") or []
    blocked = cfg.get("blockedDomains") or []
    host = host_of(url)
    if not host:
        scheme = scheme_of(url)
        if scheme in ("", "about"):
            return True, ""
        if allowed:
            return False, (
                f"Opaque/local URL (scheme '{scheme}:') is not in allowedDomains - "
                f"refusing to operate. Edit {CONFIG_PATH} to allow it."
            )
        return True, ""
    if allowed and not any(_host_matches(host, a) for a in allowed):
        return False, (
            f"Domain '{host}' is not in allowedDomains - refusing to operate. "
            f"Edit {CONFIG_PATH} to allow it."
        )
    for b in blocked:
        if _host_matches(host, b):
            return False, (
                f"Domain '{host}' matches blockedDomains entry '{b}' - refusing to operate. "
                f"Edit {CONFIG_PATH} to change this."
            )
    return True, ""


# ---------------------------------------------------------------------- #
# Password fields
# ---------------------------------------------------------------------- #
def is_password_target(info: dict[str, Any]) -> bool:
    """True if the resolved element is a password / sensitive-credential field."""
    if info.get("password") or info.get("isPassword"):
        return True
    if (info.get("type") or "").lower() == "password":
        return True
    ac = (info.get("autocomplete") or "").lower()
    if not ac:
        return False
    return (
        "current-password" in ac
        or "new-password" in ac
        or ac.startswith("cc-")
        or " cc-" in ac
    )


# ---------------------------------------------------------------------- #
# Gated actions (clicks / key presses)
# ---------------------------------------------------------------------- #
def click_requires_confirmation(info: dict[str, Any]) -> tuple[bool, str]:
    """Decide whether a click on this element needs explicit confirmation."""
    if info.get("submit") or info.get("isSubmit"):
        return True, "target is a submit button / triggers a form submission"
    haystack = f"{info.get('name', '')} {info.get('href', '')}".lower()
    for kw in GATED_KEYWORDS:
        if kw in haystack:
            return True, f"target text/URL matches '{kw}'"
    return False, ""


def press_key_requires_confirmation(key: str, focused: dict[str, Any]) -> tuple[bool, str]:
    """Enter/Return inside a form (or on a submit control) can submit - gate it."""
    k = (key or "").strip().lower()
    if k in ("enter", "return"):
        if focused.get("inForm") or focused.get("isSubmit") or focused.get("submit"):
            return True, "Enter may submit the current form"
    return False, ""


def type_submit_requires_confirmation(
    text: str, submit: bool, info: dict[str, Any]
) -> tuple[bool, str]:
    """Whether a ``type`` call may submit a form and so needs confirmation.

    Enter can reach a form three ways: the explicit ``submit`` flag, or a raw
    newline embedded in ``text`` (in a single-line <input>, a newline can trigger
    implicit form submission). Textarea / contenteditable treat a newline as a
    literal character, so a bare newline only counts as a submit risk for <input>.
    """
    newline_in_text = ("\n" in (text or "")) or ("\r" in (text or ""))
    if not (submit or newline_in_text):
        return False, ""
    if not submit and (info.get("tag") or "").lower() != "input":
        return False, ""
    return press_key_requires_confirmation("Enter", info)


CONFIRMATION_MESSAGE = (
    "This action is confirmation-gated ({reason}). Ask Tyler in the chat to confirm, "
    "then call again with confirmed=true. (Guardrail, not a sandbox.)"
)
