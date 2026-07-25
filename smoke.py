"""Scripted end-to-end smoke test. Requires Firefox running with
--remote-debugging-port 9222 (run start-firefox-debug.bat first).

Drives the real MCP tools: status -> navigate -> snapshot -> read_page ->
screenshot -> click -> re-snapshot. Safe target (example.com); no gated actions.
"""

import asyncio
import base64
import re

from firefox_mcp.tools import mcp


async def text(name, **args):
    content, _ = await mcp.call_tool(name, args)
    return content[0].text


async def main():
    print("=== browser_status ===")
    print(await text("browser_status"))

    print("\n=== navigate example.com ===")
    print(await text("navigate", url="https://example.com"))

    print("\n=== snapshot ===")
    snap = await text("snapshot")
    print(snap)

    print("\n=== read_page (first 300 chars) ===")
    page = await text("read_page")
    print(page[:300])

    print("\n=== screenshot ===")
    content, _ = await mcp.call_tool("screenshot", {})
    img = content[0]
    data = getattr(img, "data", None)
    if data:
        raw = base64.b64decode(data) if isinstance(data, str) else data
        out = "smoke_screenshot.png"
        with open(out, "wb") as f:
            f.write(raw)
        print(f"saved {out} ({len(raw)} bytes)")

    m = re.search(r"\[(e\d+)\] link", snap)
    if m:
        ref = m.group(1)
        print(f"\n=== click {ref} (the link) ===")
        print(await text("click", ref=ref))
        await asyncio.sleep(1.0)
        print("\n=== re-snapshot after navigation ===")
        print(await text("snapshot"))
    else:
        print("\n(no link ref found to click)")


if __name__ == "__main__":
    asyncio.run(main())
