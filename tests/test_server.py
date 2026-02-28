"""Tests for server.py: FastMCP initialization, tool registration, stdio transport."""

from __future__ import annotations

from platform_mcp_server.server import mcp


class TestServerInitialization:
    """Tests for MCP server setup."""

    def test_server_instance_exists(self) -> None:
        assert mcp is not None

    def test_server_name(self) -> None:
        assert mcp.name == "Platform MCP Server"

    def test_all_six_tools_registered(self) -> None:
        tool_names = {tool.name for tool in mcp._tool_manager.list_tools()}
        expected = {
            "check_node_pool_pressure",
            "get_pod_health",
            "get_kubernetes_upgrade_status",
            "get_upgrade_progress",
            "get_upgrade_duration_metrics",
            "check_pdb_upgrade_risk",
        }
        assert expected.issubset(tool_names), f"Missing tools: {expected - tool_names}"

    def test_each_tool_has_docstring(self) -> None:
        for tool in mcp._tool_manager.list_tools():
            assert tool.description, f"Tool '{tool.name}' has no description"
