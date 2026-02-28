"""Tests for get_upgrade_duration_metrics tool handler."""

# Note 1: `from __future__ import annotations` is a forward-compatibility
# import that activates PEP 563 "postponed evaluation of annotations". It
# allows type hints such as `int | None` and `dict | None` to be written using
# the newer union syntax on Python 3.9 and earlier without raising a
# `TypeError`. All annotations in the file are treated as strings and only
# evaluated when explicitly requested (e.g., by `typing.get_type_hints`).
from __future__ import annotations

# Note 2: Both `AsyncMock` and `patch` come from the standard-library
# `unittest.mock` module. `AsyncMock` is essential when patching clients whose
# methods are coroutines: it ensures that `await mock.method()` resolves
# correctly instead of raising `TypeError: object MagicMock is not awaitable`.
# `patch` replaces a named attribute in a module for the duration of a test,
# then restores it automatically, keeping tests fully isolated from real I/O.
from unittest.mock import AsyncMock, patch

# Note 3: Importing the single handler function under test (rather than the
# entire module) keeps the import surface small and makes it immediately clear
# which callable is the subject of every test in this file.
from platform_mcp_server.tools.upgrade_metrics import get_upgrade_metrics_handler


# Note 4: `_make_node_event` is a test-data factory function. The leading
# underscore signals that it is private to this test module. Factory functions
# centralise the shape of fake objects so that if the real data structure gains
# a new required field it only needs to be added in one place. Each test can
# then pass only the fields that are relevant to its scenario, keeping test
# bodies concise and focused.
def _make_node_event(node_name: str, reason: str, timestamp: str) -> dict:
    return {
        "reason": reason,
        "node_name": node_name,
        # Note 5: The `message` field is constructed from `reason` and
        # `node_name` to produce a realistic-looking event message. This
        # matters because the handler may parse or display the message field;
        # a realistic value exercises that code path in the same way the
        # production Kubernetes API would.
        "message": f"{reason} on {node_name}",
        "timestamp": timestamp,
        # Note 6: `count: 1` reflects a single occurrence of the event. In
        # Kubernetes, events are de-duplicated and the count increments for
        # repeated occurrences. Using `count=1` is the correct default for a
        # freshly emitted event and avoids misleading the handler into treating
        # a synthetic event as a frequently-repeated (and therefore notable)
        # occurrence.
        "count": 1,
    }


# Note 7: A second factory function models activity-log entries from the Azure
# AKS control plane. These are distinct from Kubernetes node events: they come
# from the Azure Resource Manager API and record cluster-level operations (e.g.,
# a full upgrade run) rather than per-node transitions. Having a separate
# factory makes the source of each piece of test data unambiguous.
def _make_activity_record(
    date: str = "2026-02-20T12:00:00+00:00",
    # Note 8: `duration_seconds=3000.0` (50 minutes) is chosen as a realistic
    # but not boundary-crossing default. The anomaly detection threshold in the
    # handler is 60 minutes (3600 seconds). Using 3000 seconds keeps this
    # default safely below the threshold so that tests which call
    # `_make_activity_record()` without overriding `duration_seconds` do not
    # accidentally trigger anomaly logic they are not testing.
    duration_seconds: float = 3000.0,
) -> dict:
    return {
        "date": date,
        # Note 9: The `operation` string matches the exact Azure ARM operation
        # name for AKS upgrades. Using the real operation identifier ensures
        # that any filtering logic in the handler (e.g., skipping non-upgrade
        # operations) behaves the same way in tests as it would against the
        # real Azure Activity Log API.
        "operation": "Microsoft.ContainerService/managedClusters/write",
        "status": "Succeeded",
        "duration_seconds": duration_seconds,
        "description": "Upgrade completed",
    }


# Note 10: Using a single class to group all tests for `get_upgrade_metrics_handler`
# is a deliberate organisation choice. pytest treats each test method as an
# independent test (instantiating the class anew each time), so there is no
# shared mutable state between methods. The class exists purely as a namespace
# that communicates "these tests all belong to the same handler under test".
class TestGetUpgradeMetrics:
    # Note 11: `async def` is required because `get_upgrade_metrics_handler` is
    # an async function (a coroutine). pytest-asyncio in `asyncio_mode="auto"`
    # detects async test methods and runs them inside an event loop without
    # requiring an explicit `@pytest.mark.asyncio` decorator on every method.
    # This reduces boilerplate while retaining full async/await support.
    async def test_current_run_timing(self) -> None:
        mock_events = AsyncMock()
        # Note 12: The event sequence for node-1 (NodeUpgrade at 11:00, NodeReady
        # at 11:05) and node-2 (NodeUpgrade at 11:05, NodeReady at 11:08) encodes
        # two fully completed node upgrades. The 5-minute gap for node-1 and the
        # 3-minute gap for node-2 should produce a positive mean. Using realistic
        # ISO-8601 timestamps with timezone offsets (+00:00) ensures the handler's
        # datetime parsing logic is exercised end-to-end.
        mock_events.get_node_events.return_value = [
            _make_node_event("node-1", "NodeUpgrade", "2026-02-28T11:00:00+00:00"),
            _make_node_event("node-1", "NodeReady", "2026-02-28T11:05:00+00:00"),
            _make_node_event("node-2", "NodeUpgrade", "2026-02-28T11:05:00+00:00"),
            _make_node_event("node-2", "NodeReady", "2026-02-28T11:08:00+00:00"),
        ]
        mock_aks = AsyncMock()
        # Note 13: Returning an empty list from `get_activity_log_upgrades`
        # isolates `test_current_run_timing` to the live-event path. If this
        # test also returned historical records it would be simultaneously
        # testing two different code paths, making it harder to diagnose which
        # path is broken when the test fails. Keeping each test focused on a
        # single path is a core principle of unit testing.
        mock_aks.get_activity_log_upgrades.return_value = []

        with (
            patch("platform_mcp_server.tools.upgrade_metrics.K8sEventsClient", return_value=mock_events),
            patch("platform_mcp_server.tools.upgrade_metrics.AzureAksClient", return_value=mock_aks),
        ):
            result = await get_upgrade_metrics_handler("prod-eastus", "userpool")

        # Note 14: `result.current_run is not None` is tested before accessing
        # sub-fields. If the handler returns `None` for `current_run` when node
        # events are present that is itself a bug, and asserting `is not None`
        # gives a clear failure message before a subsequent `AttributeError`
        # would confusingly point at the sub-field access.
        assert result.current_run is not None
        assert result.current_run.nodes_completed == 2
        # Note 15: `> 0` rather than a specific value (e.g., 240.0) is used for
        # `mean_seconds_per_node`. The exact calculation (mean of 300s and 180s =
        # 240s) could be asserted, but that would make this test duplicate the
        # arithmetic logic rather than verify the handler's behaviour. Testing
        # that the mean is positive confirms the metric was computed at all
        # without over-constraining the implementation.
        assert result.current_run.mean_seconds_per_node > 0

    async def test_historical_data_from_activity_log(self) -> None:
        mock_events = AsyncMock()
        # Note 16: An empty node-events list combined with two activity-log
        # records models the common scenario where no upgrade is currently active
        # but historical upgrade data is available. This tests the historical
        # path independently of the live-event path so failures are unambiguous.
        mock_events.get_node_events.return_value = []
        mock_aks = AsyncMock()
        mock_aks.get_activity_log_upgrades.return_value = [
            _make_activity_record(date="2026-02-20T12:00:00+00:00", duration_seconds=3000),
            _make_activity_record(date="2026-02-10T12:00:00+00:00", duration_seconds=3600),
        ]

        with (
            patch("platform_mcp_server.tools.upgrade_metrics.K8sEventsClient", return_value=mock_events),
            patch("platform_mcp_server.tools.upgrade_metrics.AzureAksClient", return_value=mock_aks),
        ):
            # Note 17: `history_count=5` requests more records than the two that
            # are returned by the mock. This tests that the handler handles the
            # "fewer available than requested" case gracefully — returning what
            # is available rather than padding with nulls or raising an error.
            result = await get_upgrade_metrics_handler("prod-eastus", "userpool", history_count=5)

        assert len(result.historical) == 2

    async def test_statistical_summary(self) -> None:
        mock_events = AsyncMock()
        mock_events.get_node_events.return_value = []
        mock_aks = AsyncMock()
        # Note 18: Three activity records with durations 2400s, 3000s, and 3600s
        # are chosen deliberately. Three is the minimum sample size needed for a
        # meaningful percentile calculation (p90 requires at least a few data
        # points). The values span a range (40 min to 60 min) so that mean and
        # p90 will differ, confirming that the handler is computing percentiles
        # rather than just returning the mean twice.
        mock_aks.get_activity_log_upgrades.return_value = [
            _make_activity_record(duration_seconds=2400),
            _make_activity_record(duration_seconds=3000),
            _make_activity_record(duration_seconds=3600),
        ]

        with (
            patch("platform_mcp_server.tools.upgrade_metrics.K8sEventsClient", return_value=mock_events),
            patch("platform_mcp_server.tools.upgrade_metrics.AzureAksClient", return_value=mock_aks),
        ):
            result = await get_upgrade_metrics_handler("prod-eastus", "userpool")

        # Note 19: Both `mean_duration_seconds > 0` and `p90_duration_seconds > 0`
        # are asserted. This verifies that the stats object is populated with
        # real computed values and not with zero-initialised defaults that would
        # pass a `is not None` check but indicate a silent calculation failure.
        assert result.stats is not None
        assert result.stats.mean_duration_seconds > 0
        assert result.stats.p90_duration_seconds > 0

    async def test_anomaly_flag_when_exceeds_threshold(self) -> None:
        mock_events = AsyncMock()
        mock_events.get_node_events.return_value = [
            _make_node_event("node-1", "NodeUpgrade", "2026-02-28T10:00:00+00:00"),
            # Note 20: The inline comment "NodeReady much later — long upgrade"
            # communicates *intent* rather than implementation detail. A 90-minute
            # gap (10:00 to 11:30) between NodeUpgrade and NodeReady far exceeds
            # the 60-minute anomaly threshold. This specific gap was chosen to
            # produce an unambiguous anomaly: a value just barely above the
            # threshold (e.g., 61 minutes) would be more fragile if the threshold
            # ever changes by even a small amount.
            _make_node_event("node-1", "NodeReady", "2026-02-28T11:30:00+00:00"),
        ]
        mock_aks = AsyncMock()
        mock_aks.get_activity_log_upgrades.return_value = []

        with (
            patch("platform_mcp_server.tools.upgrade_metrics.K8sEventsClient", return_value=mock_events),
            patch("platform_mcp_server.tools.upgrade_metrics.AzureAksClient", return_value=mock_aks),
        ):
            result = await get_upgrade_metrics_handler("prod-eastus", "userpool")

        # Total duration is 90 mins for one node, exceeds 60-minute threshold
        # Note 21: `result.anomaly_flag is not None` confirms the handler
        # detected the anomaly and set the flag. The second assertion,
        # `"60-minute" in result.anomaly_flag`, verifies that the anomaly message
        # references the threshold so that the operator reading the output
        # understands *why* the flag was raised, not just *that* it was.
        assert result.anomaly_flag is not None
        assert "60-minute" in result.anomaly_flag

    async def test_no_active_upgrade_history_only(self) -> None:
        # Note 22: This test covers the steady-state scenario: the cluster is not
        # currently upgrading (no node events) but has one historical record. The
        # `current_run is None` assertion is the critical one — it confirms the
        # handler correctly distinguishes "no active upgrade" from "active upgrade
        # with no completed nodes yet", which would have `current_run` set but
        # `nodes_completed == 0`.
        mock_events = AsyncMock()
        mock_events.get_node_events.return_value = []
        mock_aks = AsyncMock()
        mock_aks.get_activity_log_upgrades.return_value = [
            _make_activity_record(duration_seconds=2400),
        ]

        with (
            patch("platform_mcp_server.tools.upgrade_metrics.K8sEventsClient", return_value=mock_events),
            patch("platform_mcp_server.tools.upgrade_metrics.AzureAksClient", return_value=mock_aks),
        ):
            result = await get_upgrade_metrics_handler("prod-eastus", "userpool")

        assert result.current_run is None
        assert len(result.historical) == 1

    async def test_fewer_historical_records(self) -> None:
        mock_events = AsyncMock()
        mock_events.get_node_events.return_value = []
        mock_aks = AsyncMock()
        mock_aks.get_activity_log_upgrades.return_value = [
            _make_activity_record(duration_seconds=2400),
        ]

        with (
            patch("platform_mcp_server.tools.upgrade_metrics.K8sEventsClient", return_value=mock_events),
            patch("platform_mcp_server.tools.upgrade_metrics.AzureAksClient", return_value=mock_aks),
        ):
            result = await get_upgrade_metrics_handler("prod-eastus", "userpool", history_count=5)

        assert len(result.historical) == 1
        # Note 23: The substring assertion `"1 of 5" in result.summary` is a
        # lightweight contract test on the human-readable summary string. It
        # confirms that the handler tells the caller how many records were
        # actually returned versus how many were requested, which is useful for
        # operators who may wonder if the data is incomplete. Testing a substring
        # (not the exact string) allows the surrounding wording to evolve without
        # breaking the test.
        assert "1 of 5" in result.summary

    async def test_cluster_all_fan_out(self) -> None:
        mock_events = AsyncMock()
        mock_events.get_node_events.return_value = []
        mock_aks = AsyncMock()
        mock_aks.get_activity_log_upgrades.return_value = []

        with (
            patch("platform_mcp_server.tools.upgrade_metrics.K8sEventsClient", return_value=mock_events),
            patch("platform_mcp_server.tools.upgrade_metrics.AzureAksClient", return_value=mock_aks),
        ):
            # Note 24: The deferred import of `get_upgrade_metrics_all` inside the
            # `with` block ensures the patches are already active before the
            # function is imported. This is necessary when the module's top-level
            # code captures references to `K8sEventsClient` or `AzureAksClient` at
            # import time. Importing inside the patch context is the safest
            # pattern for avoiding the "patch applied after reference captured"
            # failure mode, even if in this case the import is safe either way.
            from platform_mcp_server.tools.upgrade_metrics import get_upgrade_metrics_all

            results = await get_upgrade_metrics_all("userpool")

        # Note 25: Asserting `len(results) == 6` encodes the platform's known
        # cluster count as a test contract. If a new cluster is registered the
        # test fails explicitly, prompting a deliberate update rather than a
        # silent behaviour change. This acts as a guard against accidental
        # additions or removals from the cluster registry.
        assert len(results) == 6
