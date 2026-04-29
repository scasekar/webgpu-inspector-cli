"""Smoke tests for the MCP server tool surface.

These tests do not spawn a real browser — they confirm the server builds, the
expected tool names are registered with valid schemas, and the no-session
guard surfaces a clean error to clients.
"""

import asyncio

import pytest

# Defer the FastMCP import until test time so a missing optional dep produces a
# clear skip rather than a collection-time crash.
mcp_module = pytest.importorskip("mcp.server.fastmcp")

from webgpu_inspector_cli.mcp_server.server import build_server  # noqa: E402
import webgpu_inspector_cli.core.bridge as bridge_module  # noqa: E402


EXPECTED_TOOLS = {
    "browser_launch",
    "browser_close",
    "browser_navigate",
    "browser_screenshot",
    "browser_status",
    "browser_eval",
    "browser_click",
    "browser_type",
    "browser_wait",
    "objects_list",
    "objects_inspect",
    "objects_search",
    "objects_memory",
    "capture_frame",
    "capture_commands",
    "capture_texture",
    "capture_buffer",
    "shaders_list",
    "shaders_view",
    "shaders_replace",
    "shaders_revert",
    "errors_list",
    "errors_clear",
    "status_summary",
}


@pytest.fixture
def server():
    return build_server()


@pytest.fixture(autouse=True)
def reset_bridge_singleton():
    """Make sure each test starts with no live Bridge."""
    bridge_module._bridge = None
    yield
    bridge_module._bridge = None


def _list_tool_names(server):
    """FastMCP exposes tools via an async list_tools method on its tool manager."""
    tools = asyncio.run(server.list_tools())
    return {t.name for t in tools}


def test_server_builds():
    server = build_server()
    assert server is not None


def test_expected_tools_registered(server):
    names = _list_tool_names(server)
    missing = EXPECTED_TOOLS - names
    assert not missing, f"Missing MCP tools: {sorted(missing)}"


def test_no_unexpected_tools(server):
    """Catch accidental drift — every registered tool should be intentional."""
    names = _list_tool_names(server)
    unexpected = names - EXPECTED_TOOLS
    assert not unexpected, f"Unexpected tools added without test update: {sorted(unexpected)}"


def test_tools_have_descriptions(server):
    tools = asyncio.run(server.list_tools())
    for tool in tools:
        assert tool.description, f"Tool {tool.name} has no description"


def test_tools_have_input_schemas(server):
    tools = asyncio.run(server.list_tools())
    for tool in tools:
        # FastMCP auto-generates inputSchema from type hints.
        assert tool.inputSchema, f"Tool {tool.name} missing inputSchema"
        assert tool.inputSchema.get("type") == "object"


def test_browser_launch_required_args(server):
    tools = {t.name: t for t in asyncio.run(server.list_tools())}
    schema = tools["browser_launch"].inputSchema
    # `url` is required, the rest have defaults.
    assert "url" in schema.get("required", [])


def test_capture_buffer_has_struct_spec(server):
    tools = {t.name: t for t in asyncio.run(server.list_tools())}
    schema = tools["capture_buffer"].inputSchema
    assert "struct_spec" in schema["properties"]
    assert "format" in schema["properties"]


def test_no_session_tool_returns_error(server):
    """Tools that need an active browser must surface a friendly error, not crash."""
    from mcp.server.fastmcp.exceptions import ToolError

    with pytest.raises(ToolError) as excinfo:
        asyncio.run(server.call_tool("objects_list", {}))
    assert "No active browser session" in str(excinfo.value)


def test_browser_close_no_session_is_no_op(server):
    """browser_close should report no_session without raising — the user may
    call it idempotently."""
    result = asyncio.run(server.call_tool("browser_close", {}))
    # FastMCP returns the (content, structured) pair when convert_result is
    # True. The structured value should contain status=no_session.
    structured = result[1] if isinstance(result, tuple) else result
    text = repr(structured)
    assert "no_session" in text
