"""Server bootstrap - runs the Firefox Browser Control MCP server over stdio.

Launched by the MCP client (Claude Code / Claude Desktop) as a subprocess; it
speaks JSON-RPC on stdin/stdout. It does NOT launch Firefox - attach only.
"""

from __future__ import annotations

from .tools import mcp


def main() -> None:
    mcp.run()  # stdio transport by default


if __name__ == "__main__":
    main()
