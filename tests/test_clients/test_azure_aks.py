# Note 1: This file tests AzureAksClient's four public async methods: get_cluster_info,
# get_node_pool_state, get_upgrade_profile, and get_activity_log_upgrades. Each class
# groups tests for one method, making it straightforward to run a focused subset with
# `pytest -k TestGetClusterInfo` and to understand which feature failed from a CI report.
"""Tests for AzureAksClient: cluster info, node pool state, upgrade profile, activity log."""

# Note 2: `from __future__ import annotations` must appear before all non-docstring code.
# It instructs the interpreter to treat all annotations in this module as strings (lazy),
# enabling forward references and reducing import overhead at module load time.
from __future__ import annotations

# Note 3: datetime objects from Python's standard library are used here to construct
# realistic timestamp values for activity log entries. The UTC sentinel from datetime
# ensures all timestamps are timezone-aware, which is important for duration calculations
# that subtract two datetime objects — naive datetimes cannot be subtracted from
# timezone-aware ones without raising a TypeError.
from datetime import UTC, datetime

# Note 4: MagicMock auto-generates any attribute or method access, making it ideal for
# deeply nested SDK response objects like Azure's ManagedCluster. patch is used as a
# context manager to swap real Azure SDK calls with mocks for the duration of each test.
from unittest.mock import MagicMock, patch

import pytest

from platform_mcp_server.clients.azure_aks import AzureAksClient
from platform_mcp_server.config import CLUSTER_MAP


# Note 5: Module-level helper functions prefixed with `_make_mock_` are a common pattern
# in test suites for creating valid, fully-populated mock objects. Using a factory
# function with keyword arguments and default values serves two purposes: (1) tests only
# need to specify the fields they care about, and (2) the function documents what shape
# the real SDK object is expected to have, acting as living documentation.
def _make_mock_pool(
    name: str = "userpool",
    count: int = 3,
    current_version: str = "1.29.8",
    target_version: str = "1.29.8",
    provisioning_state: str = "Succeeded",
) -> MagicMock:
    # Note 6: Every attribute set on this mock mirrors a real field from Azure's
    # AgentPool SDK object. The defaults ("Standard_DS2_v2", count=3, etc.) reflect
    # realistic production values for a general-purpose AKS node pool, which helps
    # readers understand the domain without needing to consult Azure documentation.
    pool = MagicMock()
    pool.name = name
    pool.vm_size = "Standard_DS2_v2"
    pool.count = count
    pool.min_count = 1
    pool.max_count = 10
    pool.current_orchestrator_version = current_version
    pool.orchestrator_version = target_version
    pool.provisioning_state = provisioning_state
    # Note 7: `pool.power_state.code = "Running"` works because MagicMock automatically
    # creates `pool.power_state` as another MagicMock when first accessed. Setting `.code`
    # on that nested mock stores the value as expected. This auto-nesting behavior is what
    # distinguishes MagicMock from a plain object — no class hierarchy needed.
    pool.power_state.code = "Running"
    pool.os_type = "Linux"
    pool.mode = "User"
    return pool


# Note 8: A file-local fixture defined with @pytest.fixture provides the AzureAksClient
# instance to every test in this file. Defining it here (rather than in conftest.py)
# limits its scope to this module, preventing fixture namespace pollution in other test
# files. Tests receive it by declaring `client` as a parameter.
@pytest.fixture
def client() -> AzureAksClient:
    return AzureAksClient(CLUSTER_MAP["prod-eastus"])


# Note 9: All test methods in this suite are plain `async def` functions. pytest runs
# them as coroutines when the asyncio_mode is configured (typically "auto" in pyproject.toml
# via pytest-asyncio). This means no `@pytest.mark.asyncio` decorator is needed on each
# individual test — the plugin handles the event loop lifecycle automatically.
class TestGetClusterInfo:
    async def test_returns_cluster_version_and_pools(self, client: AzureAksClient) -> None:
        # Note 10: MagicMock() for `mock_container` simulates the Azure ContainerServiceClient
        # SDK object. Rather than constructing a real client (which requires credentials and
        # network access), we build a minimal mock that only implements the methods our code
        # actually calls. This is the "test only what you use" principle.
        mock_container = MagicMock()
        cluster_mock = MagicMock()
        cluster_mock.kubernetes_version = "1.29.8"
        cluster_mock.provisioning_state = "Succeeded"
        cluster_mock.fqdn = "aks-prod.eastus.azmk8s.io"
        # Note 11: Providing two pool profiles with different names lets the test verify
        # both the count and the ordering of items in the returned list. Using descriptive
        # names ("systempool", "userpool") also makes the assertion on index [0] readable.
        cluster_mock.agent_pool_profiles = [
            _make_mock_pool(name="systempool"),
            _make_mock_pool(name="userpool"),
        ]
        mock_container.managed_clusters.get.return_value = cluster_mock

        # Note 12: `patch.object` replaces an attribute on a specific object instance
        # (here `client._get_container_client`) rather than patching a name in a module
        # namespace. This is the right tool when you have direct access to the instance
        # whose method you want to mock. It is scoped to the `with` block and is
        # automatically restored when the block exits, even if the test raises an exception.
        with patch.object(client, "_get_container_client", return_value=mock_container):
            info = await client.get_cluster_info()

        assert info["control_plane_version"] == "1.29.8"
        assert len(info["node_pools"]) == 2
        assert info["node_pools"][0]["name"] == "systempool"

    async def test_error_handling(self, client: AzureAksClient) -> None:
        # Note 13: `side_effect = Exception(...)` is a powerful MagicMock feature. When
        # set to an exception class or instance, calling the mock raises that exception
        # instead of returning a value. This simulates error conditions (network failures,
        # auth errors, API rate limits) without needing a real broken service.
        mock_container = MagicMock()
        mock_container.managed_clusters.get.side_effect = Exception("Unauthorized")

        # Note 14: Combining `patch.object` and `pytest.raises` inside a single `with`
        # block (using Python 3.10+ parenthesized context managers) is clean and readable.
        # `pytest.raises(Exception, match="Unauthorized")` acts as an assertion that the
        # exact exception with the matching message is raised. If the code swallows the
        # exception or raises a different one, the test fails.
        with (
            patch.object(client, "_get_container_client", return_value=mock_container),
            pytest.raises(Exception, match="Unauthorized"),
        ):
            await client.get_cluster_info()


class TestGetNodePoolState:
    async def test_returns_pool_details(self, client: AzureAksClient) -> None:
        # Note 15: This test focuses on a single node pool (`userpool`) with count=5.
        # The count value is deliberately different from the factory default (3) to prove
        # the method reads and returns the actual value from the response, not a hardcoded
        # default from the production code or the test factory.
        mock_container = MagicMock()
        mock_container.agent_pools.get.return_value = _make_mock_pool(name="userpool", count=5)

        with patch.object(client, "_get_container_client", return_value=mock_container):
            state = await client.get_node_pool_state("userpool")

        assert state["name"] == "userpool"
        assert state["count"] == 5


class TestGetUpgradeProfile:
    async def test_returns_available_upgrades(self, client: AzureAksClient) -> None:
        mock_container = MagicMock()
        profile = MagicMock()

        # Control plane upgrades
        # Note 16: Comments in tests that label logical sections ("Control plane upgrades",
        # "Pool upgrades") are more valuable than comments that describe what a single line
        # of code does. They help a reader understand the conceptual structure of the mock
        # data being assembled, especially when that data mirrors a non-trivial Azure SDK
        # response schema with nested profile objects.
        upgrade_1 = MagicMock()
        upgrade_1.kubernetes_version = "1.30.0"
        profile.control_plane_profile.kubernetes_version = "1.29.8"
        profile.control_plane_profile.upgrades = [upgrade_1]

        # Pool upgrades
        pool_profile = MagicMock()
        pool_profile.name = "userpool"
        pool_upgrade = MagicMock()
        pool_upgrade.kubernetes_version = "1.30.0"
        pool_profile.upgrades = [pool_upgrade]
        profile.agent_pool_profiles = [pool_profile]

        mock_container.managed_clusters.get_upgrade_profile.return_value = profile

        with patch.object(client, "_get_container_client", return_value=mock_container):
            result = await client.get_upgrade_profile()

        # Note 17: The `in` operator tests membership. Using `"1.30.0" in result["control_plane_upgrades"]`
        # checks that the version appears in the list without caring about order or other
        # versions that might be present. This makes the test resilient to changes in
        # the ordering logic of the production code.
        assert "1.30.0" in result["control_plane_upgrades"]
        assert "1.30.0" in result["pool_upgrades"]["userpool"]


class TestGetActivityLogUpgrades:
    async def test_returns_historical_records(self, client: AzureAksClient) -> None:
        mock_monitor = MagicMock()
        entry = MagicMock()
        entry.status.value = "Succeeded"
        # Note 18: Two distinct datetime values are used (event_timestamp and submission_timestamp)
        # with a deliberate 1-hour gap. This lets the test verify the duration calculation
        # (3600.0 seconds == 1 hour) rather than just asserting the timestamps are present.
        # Choosing timezone-aware UTC datetimes matches production behavior where all
        # Azure timestamps are in UTC.
        entry.event_timestamp = datetime(2026, 2, 20, 12, 0, 0, tzinfo=UTC)
        entry.submission_timestamp = datetime(2026, 2, 20, 11, 0, 0, tzinfo=UTC)
        entry.operation_name.value = "Microsoft.ContainerService/managedClusters/write"
        entry.description = "Upgrade to 1.29.8"
        mock_monitor.activity_logs.list.return_value = [entry]

        # Note 19: `patch.object` is used on `_get_monitor_client` (not `_get_container_client`)
        # because this method interacts with Azure Monitor, not the Container Service API.
        # Using the wrong patch target would cause the real `_get_monitor_client` to run,
        # which would attempt to authenticate against Azure and fail in CI.
        with patch.object(client, "_get_monitor_client", return_value=mock_monitor):
            records = await client.get_activity_log_upgrades(count=5)

        assert len(records) == 1
        # Note 20: 3600.0 seconds is the expected duration for a 1-hour upgrade window.
        # Asserting on a concrete float value (rather than just checking the key exists)
        # ensures the duration arithmetic is correct. The `.0` suffix confirms the result
        # is a float, which is the expected return type for `timedelta.total_seconds()`.
        assert records[0]["duration_seconds"] == 3600.0  # 1 hour

    async def test_fewer_records_than_requested(self, client: AzureAksClient) -> None:
        # Note 21: This test covers the boundary case where the activity log contains
        # fewer entries than the `count` parameter requests. Production code that slices
        # a list without bounds-checking would raise an IndexError in this scenario.
        # An empty return value from the API is the simplest edge case to test.
        mock_monitor = MagicMock()
        mock_monitor.activity_logs.list.return_value = []

        with patch.object(client, "_get_monitor_client", return_value=mock_monitor):
            records = await client.get_activity_log_upgrades(count=5)

        assert len(records) == 0

    async def test_partial_failure_handling(self, client: AzureAksClient) -> None:
        # Note 22: "Timeout" is a realistic error string for Azure Monitor API calls,
        # which can experience throttling or latency under load. By asserting the
        # exception propagates (rather than being swallowed), the test enforces that the
        # client does not silently hide errors from callers — important for observability.
        mock_monitor = MagicMock()
        mock_monitor.activity_logs.list.side_effect = Exception("Timeout")

        with (
            patch.object(client, "_get_monitor_client", return_value=mock_monitor),
            pytest.raises(Exception, match="Timeout"),
        ):
            await client.get_activity_log_upgrades()
