"""Tests for server.py: FastMCP initialization, tool registration, stdio transport."""

# Note 1: `from __future__ import annotations` enables PEP 563 postponed evaluation of
# annotations. This means type hints are treated as strings at runtime rather than being
# evaluated eagerly, which avoids circular import errors and speeds up module loading.
# It is especially important in projects using complex type hierarchies or forward
# references, and it is good practice to include it in every file that uses annotations.
from __future__ import annotations

# Note 2: Importing `mcp` directly from the server module under test is intentional.
# Rather than re-constructing a server instance inside the test, we assert on the live
# singleton that will actually be registered and served in production. This technique
# (testing the real object) ensures that module-level side effects — such as tool
# registration via decorators — have already executed by the time any test runs.
# If we imported only the class and instantiated a fresh copy we could miss bugs where
# a decorator or startup hook was never called on the real server object.
from platform_mcp_server.server import mcp


# Note 3: Grouping related tests inside a class (even without inheriting from
# `unittest.TestCase`) is a common pytest idiom. Classes provide logical namespacing
# in test output, allow shared setup/teardown via `setup_method` / `teardown_method`,
# and make it easy to apply class-scoped fixtures or marks. pytest discovers test
# classes automatically when their name starts with "Test".
class TestServerInitialization:
    """Tests for MCP server setup."""

    # Note 4: This "smoke test" verifies that the module-level `mcp` object was
    # successfully created. If server.py raises an exception during import (e.g., a
    # missing dependency or a misconfigured decorator), the import at the top of this
    # file would fail first, making this assertion redundant — but it still serves as
    # documentation: the object must exist and be truthy. Smoke tests are cheap to write
    # and catch regressions caused by import-time errors that might otherwise surface
    # only at runtime in production.
    def test_server_instance_exists(self) -> None:
        assert mcp is not None

    # Note 5: Verifying the server name is a contract test. The MCP protocol exposes the
    # server name to AI clients during the handshake phase. If the name changes, clients
    # that have hard-coded expectations (e.g., log filters, routing rules) will break.
    # Pinning the expected string here ensures that any accidental rename produces an
    # immediate, descriptive test failure rather than a subtle protocol mismatch in
    # production.
    def test_server_name(self) -> None:
        assert mcp.name == "Platform MCP Server"

    # Note 6: `_tool_manager.list_tools()` accesses a semi-private attribute of the
    # FastMCP instance. Using a set comprehension (`{tool.name for tool in ...}`) and
    # then calling `.issubset()` is a deliberate strategy: it verifies that *at least*
    # the six required tools exist without being brittle to the addition of new tools
    # in the future. If we used `==` instead, any new tool added to the server would
    # break this test unnecessarily. The `issubset` approach encodes the minimum
    # contract rather than the exhaustive list, making the test forward-compatible.
    #
    # Note 7: The f-string in the assertion message (`f"Missing tools: {expected - tool_names}"`)
    # uses set difference to produce a human-readable list of exactly which tools are
    # absent when the assertion fails. This is a pytest best practice: always include a
    # failure message that pinpoints *what* is wrong, because pytest's default assertion
    # introspection may not fully unwrap set operations into readable output.
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

    # Note 8: Iterating over every registered tool and asserting that `tool.description`
    # is truthy enforces an important documentation contract for the MCP protocol.
    # AI assistants use tool descriptions to decide *which* tool to call for a given
    # user request. A tool with an empty or missing description is effectively invisible
    # to the LLM's tool-selection logic and will never be invoked correctly. This test
    # acts as a lint rule that catches missing docstrings at the tool-registration layer
    # before they silently degrade AI assistant behavior in production.
    def test_each_tool_has_docstring(self) -> None:
        for tool in mcp._tool_manager.list_tools():
            assert tool.description, f"Tool '{tool.name}' has no description"
