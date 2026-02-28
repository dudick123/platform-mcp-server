"""Tests for get_kubernetes_upgrade_status tool handler."""

# Note 1: `from __future__ import annotations` is placed at the top of the module
# (after the docstring) to activate PEP 563 postponed evaluation of annotations.
# This allows union-type syntax like `list | None` and `list[str] | None` to be used
# as type hints in Python 3.9 and 3.10 without raising a `TypeError` at import time.
# On Python 3.12+, this behaviour is the default and the import is a no-op.
from __future__ import annotations

# Note 2: `AsyncMock` is essential any time the code under test calls `await` on a
# collaborator. A regular `MagicMock` does not produce awaitable objects, so
# `await mock.some_method()` would raise `TypeError: object MagicMock can't be used
# in 'await' expression`. `AsyncMock` makes every attribute access and call return
# a coroutine by default, making it the correct choice for mocking async clients.
#
# `patch` replaces a named symbol in a module's namespace for the duration of a
# `with` block. The patched name must be the path as seen from the module under
# test — i.e., where the name is *used*, not where it is *defined*.
from unittest.mock import AsyncMock, patch

from platform_mcp_server.tools.k8s_upgrades import get_upgrade_status_handler


# Note 3: `_make_cluster_info` is an Object Mother factory. It constructs a dict
# that mirrors the shape returned by the AKS API client after normalisation. Using
# a factory with sensible defaults means each test only specifies the fields relevant
# to its scenario. The default `cp_version = "1.29.8"` represents a realistic AKS
# version that has at least one available upgrade ("1.30.0"), which keeps the happy-
# path tests readable without explaining version semantics inline.
def _make_cluster_info(
    cp_version: str = "1.29.8",
    pools: list | None = None,
) -> dict:
    # Note 4: The `default_pools` list is defined inside the function rather than as
    # a module-level constant to avoid the mutable-default-argument trap. Each call
    # to `_make_cluster_info()` produces a fresh list, so modifications made by one
    # test cannot bleed into another test's factory invocation.
    default_pools = [
        {
            "name": "systempool",
            "vm_size": "Standard_DS2_v2",
            "count": 3,
            "min_count": 3,
            "max_count": 5,
            "current_version": cp_version,
            # Note 5: `target_version` matching `current_version` signals that this
            # pool is NOT currently being upgraded. When they differ (as in
            # `test_active_upgrade_detected`), the handler should detect an in-progress
            # upgrade. This field is the primary signal the handler uses to set
            # `upgrade_active = True` and `node_pool.upgrading = True`.
            "target_version": cp_version,
            "provisioning_state": "Succeeded",
            "power_state": "Running",
            "os_type": "Linux",
            "mode": "System",
        },
    ]
    return {
        "control_plane_version": cp_version,
        "provisioning_state": "Succeeded",
        # Note 6: `pools if pools is not None else default_pools` uses explicit None
        # checking rather than `pools or default_pools`. The `or` form would
        # incorrectly fall back to `default_pools` if the caller passed an empty list
        # (`pools=[]`) to simulate a cluster with no node pools. The `is not None`
        # guard preserves the semantic difference between "caller provided no value"
        # and "caller explicitly requested an empty pool list".
        "node_pools": pools if pools is not None else default_pools,
        "fqdn": "aks-test.eastus.azmk8s.io",
    }


# Note 7: `_make_upgrade_profile` produces the dict returned by
# `AzureAksClient.get_upgrade_profile()`. AKS exposes upgrade profiles separately
# from cluster info — the profile lists what versions are available to upgrade to,
# while the cluster info describes what version the cluster is currently running.
# The handler must call both APIs and merge their data into a single result object.
def _make_upgrade_profile(
    cp_version: str = "1.29.8",
    cp_upgrades: list[str] | None = None,
    pool_upgrades: dict | None = None,
) -> dict:
    return {
        "control_plane_version": cp_version,
        # Note 8: `cp_upgrades or ["1.30.0"]` provides a sensible default — exactly
        # one upgrade available — so that tests focused on other aspects of the handler
        # do not need to specify upgrade lists explicitly. The `or` shorthand is safe
        # here because a caller passing an empty list would be testing "no upgrades
        # available", which is a meaningful scenario that a dedicated test should
        # construct explicitly rather than relying on the default.
        "control_plane_upgrades": cp_upgrades or ["1.30.0"],
        "pool_upgrades": pool_upgrades or {"systempool": ["1.30.0"]},
    }


# Note 9: The test class is named after the handler under test. pytest collects all
# `async def test_*` methods in the class as individual test items. Each test gets
# its own class instance (pytest creates a new instance per test), so instance
# variables set in one test cannot affect another. No `setUp` / `tearDown` methods
# are needed because the mock context managers handle setup and teardown via their
# `__enter__` / `__exit__` protocol.
class TestGetUpgradeStatus:
    # Note 10: `asyncio_mode = "auto"` in `pyproject.toml` makes pytest-asyncio
    # automatically wrap every `async def test_*` coroutine in an event loop. Without
    # this configuration, each async test would need a `@pytest.mark.asyncio`
    # decorator. Auto mode reduces decorator boilerplate and ensures consistent event
    # loop configuration across the entire test suite.
    async def test_happy_path_version_data(self) -> None:
        # Note 11: The happy-path test validates the three most fundamental fields of
        # the upgrade status result: (1) `control_plane_version` is propagated from
        # the cluster info, (2) `available_upgrades` includes "1.30.0" from the upgrade
        # profile, and (3) `upgrade_active` is `False` because no pool has a differing
        # `current_version` / `target_version`. These three assertions collectively
        # confirm the handler correctly merges data from two separate API responses.
        mock_aks = AsyncMock()
        mock_aks.get_cluster_info.return_value = _make_cluster_info()
        mock_aks.get_upgrade_profile.return_value = _make_upgrade_profile()

        # Note 12: `patch("...AzureAksClient", return_value=mock_aks)` replaces the
        # AKS client class so that every call to `AzureAksClient(cluster_name)` inside
        # the handler returns `mock_aks`. The `return_value` kwarg is how `patch` sets
        # what the class constructor returns — it effectively makes
        # `AzureAksClient(anything)` return `mock_aks` without actually constructing
        # a real client or touching the Azure API.
        with patch("platform_mcp_server.tools.k8s_upgrades.AzureAksClient", return_value=mock_aks):
            result = await get_upgrade_status_handler("prod-eastus")

        assert result.control_plane_version == "1.29.8"
        # Note 13: `"1.30.0" in result.available_upgrades` uses the `in` operator,
        # which works correctly for both lists and sets. This assertion is deliberately
        # loose — it checks that the version is present without requiring the exact
        # position or that no other versions are included. This makes the test robust
        # to future changes in the AKS version matrix where additional patch versions
        # might be added.
        assert "1.30.0" in result.available_upgrades
        assert result.upgrade_active is False

    async def test_active_upgrade_detected(self) -> None:
        # Note 14: This test constructs a pool dict inline rather than using the
        # factory, because the scenario requires very specific field values that differ
        # significantly from the factory's defaults. Inline construction is acceptable
        # here — the fields are self-documenting (e.g. `"provisioning_state":
        # "Upgrading"`) and the test is the only consumer of this exact configuration.
        pool = {
            "name": "userpool",
            "vm_size": "Standard_DS2_v2",
            "count": 5,
            "min_count": 3,
            "max_count": 10,
            # Note 15: `current_version != target_version` is the key signal that an
            # upgrade is in flight. The node pool's current version is still "1.29.8"
            # (old), but the AKS control plane has set `target_version = "1.30.0"` as
            # the desired state. The handler must detect this mismatch and set both
            # `result.upgrade_active = True` and `node_pool.upgrading = True`.
            "current_version": "1.29.8",
            "target_version": "1.30.0",
            # Note 16: `provisioning_state = "Upgrading"` is the Azure Resource Manager
            # state that AKS sets during an upgrade operation. It is a secondary signal
            # that complements the version mismatch. Either signal alone should be
            # sufficient to detect an active upgrade, but testing both together mirrors
            # real-world conditions where both fields change simultaneously.
            "provisioning_state": "Upgrading",
            "power_state": "Running",
            "os_type": "Linux",
            "mode": "User",
        }
        mock_aks = AsyncMock()
        mock_aks.get_cluster_info.return_value = _make_cluster_info(pools=[pool])
        mock_aks.get_upgrade_profile.return_value = _make_upgrade_profile()

        with patch("platform_mcp_server.tools.k8s_upgrades.AzureAksClient", return_value=mock_aks):
            result = await get_upgrade_status_handler("prod-eastus")

        assert result.upgrade_active is True
        # Note 17: `any(np.upgrading for np in result.node_pools)` uses a generator
        # expression passed to the built-in `any()`. It evaluates to `True` if at
        # least one node pool in the result has `upgrading == True`. This is more
        # expressive than `result.node_pools[0].upgrading` because it does not assume
        # a specific position in the list, and it reads as a natural-language
        # statement: "any node pool is upgrading".
        assert any(np.upgrading for np in result.node_pools)

    async def test_cluster_all_fan_out(self) -> None:
        # Note 18: The `_all` functions are fan-out entry points that call the single-
        # cluster handler for every cluster in the platform's cluster registry. The
        # test confirms the fan-out breadth by asserting `len(results) == 6`. If a
        # new cluster is added to the registry without updating this assertion, the
        # test will catch the discrepancy. The mocks are configured to succeed for all
        # calls, so the test focuses purely on counting results rather than validating
        # per-cluster data.
        mock_aks = AsyncMock()
        mock_aks.get_cluster_info.return_value = _make_cluster_info()
        mock_aks.get_upgrade_profile.return_value = _make_upgrade_profile()

        with patch("platform_mcp_server.tools.k8s_upgrades.AzureAksClient", return_value=mock_aks):
            # Note 19: The import of `get_upgrade_status_all` is placed *inside* the
            # `with` block to ensure the function is resolved while the patch is active.
            # If it were imported at the module level (top of the file), the module
            # would be loaded before the patch context manager replaces
            # `AzureAksClient`, and the function would hold a reference to the real
            # class rather than the mock. Deferred imports are a pragmatic workaround
            # for this module-loading ordering issue.
            from platform_mcp_server.tools.k8s_upgrades import get_upgrade_status_all

            results = await get_upgrade_status_all()

        assert len(results) == 6

    async def test_partial_failure_returns_error(self) -> None:
        # Note 20: `side_effect = Exception("AKS API unreachable")` configures the
        # mock to raise an exception when `get_cluster_info` is awaited. This simulates
        # a transient Azure API failure — for example, a network timeout or a 503
        # response from the AKS resource provider. The test verifies that the handler
        # catches this exception rather than propagating it, packages the error details
        # into `result.errors`, and still returns a partial result to the caller.
        mock_aks = AsyncMock()
        mock_aks.get_cluster_info.side_effect = Exception("AKS API unreachable")
        # Note 21: `get_upgrade_profile` is configured to succeed even though
        # `get_cluster_info` will fail. This asymmetry tests that the handler handles
        # partial failures gracefully — it should still attempt the upgrade profile
        # call (or report the cluster-info error without calling the profile API,
        # depending on implementation) and surface the error rather than silently
        # returning an empty result.
        mock_aks.get_upgrade_profile.return_value = _make_upgrade_profile()

        with patch("platform_mcp_server.tools.k8s_upgrades.AzureAksClient", return_value=mock_aks):
            result = await get_upgrade_status_handler("prod-eastus")

        # Note 22: Asserting `len(result.errors) > 0` is intentionally permissive —
        # it allows the handler to surface one or more error entries without requiring
        # an exact count. The follow-up assertion `result.errors[0].source == "aks-api"`
        # verifies that the error is tagged with a meaningful source label so that
        # operators and downstream consumers can distinguish AKS API failures from
        # metrics-server failures, network issues, or validation errors.
        assert len(result.errors) > 0
        assert result.errors[0].source == "aks-api"
