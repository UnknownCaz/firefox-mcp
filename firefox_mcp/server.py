"""Server bootstrap - runs the Firefox Browser Control MCP server over stdio.

Launched by the MCP client (Claude Code / Claude Desktop) as a subprocess; it
speaks JSON-RPC on stdin/stdout. It does NOT launch Firefox - attach only.
"""

from __future__ import annotations

import asyncio

from .tools import _client, mcp


def main() -> None:
    async def _run() -> None:
        try:
            await mcp.run_stdio_async()
        finally:
            # Release Firefox's single BiDi session on a clean shutdown.
            await _client.close()

    asyncio.run(_run())


if __name__ == "__main__":
    main()
