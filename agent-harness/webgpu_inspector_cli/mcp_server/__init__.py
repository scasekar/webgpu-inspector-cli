"""MCP server entry point for webgpu-inspector-cli.

The server is intentionally a sibling of the CLI: same Bridge, same Playwright
session, same buffer_decoders. The MCP protocol gives every LLM client (Claude
Code, Claude Desktop, Cursor, etc.) a long-lived process to talk to, which
solves the "each CLI invocation spawns a fresh browser" problem inherent to
shelling out from an agent loop.
"""

from webgpu_inspector_cli.mcp_server.server import build_server, main

__all__ = ["build_server", "main"]
