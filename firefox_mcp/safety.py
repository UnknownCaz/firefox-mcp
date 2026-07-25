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

# Example domain substrings blocked by default (user-editable in config.json).
# These are EXAMPLES - Tyler should tune them to his own accounts.
DEFAULT_BLOCKED_DOMAINS = [
    "bank", "paypal.com", "venmo.com", "coinbase.com", "wellsfargo.com", "chase.com",
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
        return (urlparse(url).hostname or "").lower()
    except ValueError:
        return ""


def domain_check(url: str, config: Optional[dict[str, Any]] = None) -> tuple[bool, str]:
    """Return (allowed, reason). Empty/opaque URLs (about:, blank) are allowed."""
    host = host_of(url)
    if not host:
        return True, ""
    cfg = config or load_config()
    allowed = cfg.get("allowedDomains") or []
    blocked = cfg.get("blockedDomains") or []
    if allowed and not any(a in host for a in allowed):
        return False, (
            f"Domain '{host}' is not in allowedDomains - refusing to operate. "
            f"Edit {CONFIG_PATH} to allow it."
        )
    for b in blocked:
        if b in host:
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


CONFIRMATION_MESSAGE = (
    "This action is confirmation-gated ({reason}). Ask Tyler in the chat to confirm, "
    "then call again with confirmed=true. (Guardrail, not a sandbox.)"
)
