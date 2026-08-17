"""Live pass: verify the round-1 + round-2 safety gates through the REAL tools
against a freshly-launched Firefox (BiDi). Runs the on-disk (new) code directly -
the same code path the MCP server uses. Requires Firefox running with
--remote-debugging-port 9222 and no other BiDi session holder.
"""
import asyncio
import json
import re
from pathlib import Path

from firefox_mcp.tools import active_client, close_all, mcp
from firefox_mcp import safety

# Committed fixture next to this script, resolved to a file:// URL so the pass
# runs anywhere without depending on a scratch path.
FIXTURE = (Path(__file__).resolve().parent / "fixtures" / "live_form.html").as_uri()

results = []


def check(label, cond, detail=""):
    results.append(bool(cond))
    tag = "PASS" if cond else "FAIL"
    print(f"{tag} | {label} :: {str(detail).replace(chr(10), ' ')[:130]}")


def _content(r):
    return r[0] if isinstance(r, tuple) else r


async def text(name, **args):
    return _content(await mcp.call_tool(name, args))[0].text


async def connect(retries=20, delay=1.0):
    for i in range(retries):
        try:
            await active_client().ensure_connected()
            return True
        except Exception as e:  # BiDi may still be warming up
            if i == retries - 1:
                print("CONNECT FAIL:", e)
                return False
            await asyncio.sleep(delay)


async def main():
    if not await connect():
        return
    print(await text("firefox_status"))
    await text("new_tab", url=FIXTURE)
    await asyncio.sleep(0.6)
    snap = await text("snapshot")
    print(snap)

    m_tb = re.search(r"\[(e\d+)\]\s+textbox", snap)
    m_pw = re.search(r"\[(e\d+)\]\s+password", snap)
    tb = m_tb.group(1) if m_tb else None
    pw = m_pw.group(1) if m_pw else None
    if not tb:
        print("no textbox ref found; aborting")
        return

    # round 2: a newline in a single-line <input> must be gated
    r = await text("type", ref=tb, text="query\n")
    check("newline-in-input gated (round 2)", "confirmation-gated" in r, r)

    # round 1: submit=True must be gated
    r = await text("type", ref=tb, text="hello", submit=True)
    check("type submit=True gated (round 1)", "confirmation-gated" in r, r)

    # round 1: password field refused
    if pw:
        r = await text("type", ref=pw, text="secret")
        check("password field refused (round 1)",
              "Refused" in r and "password" in r.lower(), r)

    # plain typing (no newline, no submit) still works
    r = await text("type", ref=tb, text="hello")
    check("plain type still works", "Typed into" in r, r)
    snap2 = await text("snapshot")
    check("typed value present", 'value="hello"' in snap2, "value check")

    # round 2: opaque file:// must be refused while an allowlist is set
    cfgp = safety.CONFIG_PATH
    cfgp.parent.mkdir(parents=True, exist_ok=True)
    orig = cfgp.read_text(encoding="utf-8") if cfgp.exists() else None
    cfgp.write_text(json.dumps({"allowedDomains": ["example.com"], "blockedDomains": []}),
                    encoding="utf-8")
    try:
        r = await text("navigate", url=FIXTURE)
        check("file:// refused under allowlist (round 2)",
              ("Refused" in r) or ("not in allowedDomains" in r), r)
        r = await text("navigate", url="https://example.com")
        check("allowlisted host allowed (round 2)", "Navigated" in r, r)
    finally:
        if orig is None:
            try:
                cfgp.unlink()
            except FileNotFoundError:
                pass
        else:
            cfgp.write_text(orig, encoding="utf-8")

    npass = sum(1 for c in results if c)
    print(f"\n=== LIVE PASS: {npass}/{len(results)} checks passed ===")


async def _main():
    try:
        await main()
    finally:
        await close_all()  # send session.end so we don't orphan the session


if __name__ == "__main__":
    asyncio.run(_main())
