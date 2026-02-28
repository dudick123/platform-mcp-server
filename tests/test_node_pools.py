"""Tests for check_node_pool_pressure tool handler."""

# Note 1: `from __future__ import annotations` enables PEP 563 postponed evaluation of
# annotations. This allows using built-in generics like `list[str]` and `str | None` as
# type hints in Python 3.9 without a runtime error, since annotations are stored as
# strings rather than evaluated eagerly. It is a forward-compatibility shim for the
# typing system.
from __future__ import annotations

# Note 2: `AsyncMock` is used instead of `MagicMock` whenever the code under test
# calls `await` on the mock. A plain `MagicMock` returns another `MagicMock` from
# `await`, which is truthy but not an awaitable coroutine — this would raise a
# `TypeError` at runtime. `AsyncMock` makes every method return an awaitable by default,
# so `await mock.some_method()` works correctly.
#
# `patch` is the workhorse of Python's `unittest.mock` library. It temporarily replaces
# a named attribute on a module or class for the duration of a `with` block (or the
# lifetime of a decorated function), then restores the original. It is the standard
# way to inject test doubles without modifying production code.
from unittest.mock import AsyncMock, patch

from platform_mcp_server.tools.node_pools import check_node_pool_pressure_handler


# Note 3: Helper factory functions like `_make_node` follow the Object Mother pattern.
# They centralise the construction of test data so that each test only specifies the
# fields that are relevant to its scenario. Default values represent the "happy path"
# state, and individual tests override only what they need to change. This prevents
# large amounts of repetitive dict literals in every test body, and makes it easy to
# extend the schema later — you update the factory in one place.
def _make_node(
    name: str,
    pool: str,
    cpu_alloc: str = "4000m",
    mem_alloc: str = "16Gi",
) -> dict:
    return {
        "name": name,
        "pool": pool,
        "version": "v1.29.8",
        "unschedulable": False,
        "allocatable_cpu": cpu_alloc,
        "allocatable_memory": mem_alloc,
        "conditions": {"Ready": "True"},
        "labels": {"agentpool": pool},
    }


# Note 4: Kubernetes resource quantities use a special string format. CPU is expressed
# in "millicores" (e.g. "1000m" == 1 vCPU, "4000m" == 4 vCPUs). Memory uses binary
# suffixes (e.g. "16Gi" == 16 gibibytes). The handler under test must parse these
# strings into numeric values to perform percentage calculations. Using realistic
# Kubernetes quantity strings in test data keeps the parsing logic exercised without
# introducing a hard dependency on the Kubernetes client library in test scope.
def _make_metric(name: str, cpu: str = "1000m", mem: str = "4Gi") -> dict:
    return {"name": name, "cpu_usage": cpu, "memory_usage": mem}


# Note 5: `str | None` is the modern union-type syntax (Python 3.10+, or 3.9+ with
# `from __future__ import annotations`). It is equivalent to `Optional[str]` from the
# `typing` module. Using the union pipe syntax is preferred in new code because it is
# more readable and avoids an extra import.
def _make_pod(name: str, namespace: str = "default", phase: str = "Pending", node: str | None = None) -> dict:
    return {
        "name": name,
        "namespace": namespace,
        "phase": phase,
        "node_name": node,
        "reason": None,
        "message": None,
        "container_statuses": [],
        # Note 6: The `conditions` list here simulates the Kubernetes PodScheduled
        # condition being `False` with reason `Unschedulable`. This is the signal the
        # scheduler emits when it cannot find a node that satisfies the pod's resource
        # requests or affinity rules. Tests that care about scheduling failures rely on
        # this field; tests that only care about other aspects use the default factory
        # value as-is without parsing it.
        "conditions": [{"type": "PodScheduled", "status": "False", "reason": "Unschedulable", "message": ""}],
    }


# Note 7: Grouping tests inside a class is a pytest convention that has several
# benefits: the class name appears in the test report output, making it easy to
# identify which component a failing test belongs to; shared fixtures can be defined
# at class scope; and related tests can be collected and run together with
# `pytest tests/test_node_pools.py::TestCheckNodePoolPressure`. The class does NOT
# inherit from `unittest.TestCase` — doing so would prevent pytest from using its
# own fixture injection and async test runner.
class TestCheckNodePoolPressure:
    # Note 8: pytest discovers async test methods automatically when `asyncio_mode =
    # "auto"` is set in `pyproject.toml` (or `pytest.ini`). In auto mode, pytest-asyncio
    # wraps every `async def test_*` coroutine in an event loop without requiring the
    # `@pytest.mark.asyncio` decorator on each test. This reduces boilerplate and
    # ensures the entire test class runs under the same async configuration.
    async def test_happy_path_single_pool(self) -> None:
        # Note 9: A "happy path" test validates the most common, error-free scenario.
        # It is written first to confirm that the integration between collaborators
        # produces a coherent, well-formed output before edge cases are explored.
        # If the happy path breaks, every other test is suspect, so it acts as a
        # canary for regressions.
        mock_core = AsyncMock()
        mock_core.get_nodes.return_value = [
            _make_node("node-1", "userpool"),
            _make_node("node-2", "userpool"),
        ]
        mock_core.get_pods.return_value = []

        mock_metrics = AsyncMock()
        mock_metrics.get_node_metrics.return_value = [
            _make_metric("node-1", cpu="3000m", mem="12Gi"),
            _make_metric("node-2", cpu="2000m", mem="8Gi"),
        ]

        # Note 10: The parenthesised `with (...)` syntax (Python 3.10+) allows
        # multiple context managers to be stacked without the backslash line-
        # continuation character. Each `patch(...)` call replaces the named symbol
        # for the duration of the `with` block and restores it on exit, even if the
        # block raises an exception.
        #
        # The target string format is `"module.path.ClassName"` — it must match the
        # import path used by the *production* module, not where the class is defined.
        # If `node_pools.py` imports `K8sCoreClient` as
        # `from platform_mcp_server.clients import K8sCoreClient`, the patch target
        # must be `platform_mcp_server.tools.node_pools.K8sCoreClient` so the
        # replacement is seen by that module's namespace.
        with (
            patch("platform_mcp_server.tools.node_pools.K8sCoreClient", return_value=mock_core),
            patch("platform_mcp_server.tools.node_pools.K8sMetricsClient", return_value=mock_metrics),
        ):
            result = await check_node_pool_pressure_handler("prod-eastus")

        # Note 11: Assertions are placed *outside* the `with` block deliberately.
        # Once the `with` block exits, the patches are torn down and the real classes
        # are restored. Asserting outside ensures that the result object does not hold
        # live references to patched objects that could influence assertion behaviour,
        # and it keeps the "act" phase cleanly separated from the "assert" phase
        # (the AAA — Arrange, Act, Assert — pattern).
        assert result.cluster == "prod-eastus"
        assert len(result.pools) == 1
        assert result.pools[0].pool_name == "userpool"
        assert result.pools[0].ready_nodes == 2
        assert result.pools[0].pending_pods == 0
        assert result.pools[0].pressure_level == "ok"  # CPU ~62.5%, mem ~62.5% — both below 75%/80%

    async def test_critical_pressure_from_cpu(self) -> None:
        # Note 12: This test exercises a specific threshold boundary. The node has
        # 4000m of allocatable CPU and the mock metric reports 3800m in use — that
        # is 95% utilisation, which should exceed the "critical" threshold (typically
        # >= 90%). Boundary-value tests are important because off-by-one errors in
        # percentage calculations are a common defect. Using explicit numeric strings
        # makes the expected ratio easy to verify by inspection.
        mock_core = AsyncMock()
        mock_core.get_nodes.return_value = [_make_node("node-1", "userpool", cpu_alloc="4000m")]
        mock_core.get_pods.return_value = []

        mock_metrics = AsyncMock()
        mock_metrics.get_node_metrics.return_value = [_make_metric("node-1", cpu="3800m", mem="4Gi")]

        with (
            patch("platform_mcp_server.tools.node_pools.K8sCoreClient", return_value=mock_core),
            patch("platform_mcp_server.tools.node_pools.K8sMetricsClient", return_value=mock_metrics),
        ):
            result = await check_node_pool_pressure_handler("prod-eastus")

        assert result.pools[0].pressure_level == "critical"
        # Note 13: Asserting `is not None` before asserting a numeric comparison
        # produces a clearer failure message. If `cpu_requests_percent` were `None`,
        # the `>= 90.0` assertion would raise a `TypeError` with an opaque message.
        # The explicit None guard makes the intent visible and the failure diagnostic
        # more useful.
        assert result.pools[0].cpu_requests_percent is not None
        assert result.pools[0].cpu_requests_percent >= 90.0

    async def test_warning_from_pending_pods(self) -> None:
        # Note 14: This test validates that the pressure classification logic takes
        # pending pods into account, not just raw resource utilisation. A pool can
        # have low CPU/memory usage but still be "warning" if workloads cannot be
        # scheduled. The mock deliberately sets low resource usage (1000m / 2Gi on
        # a 4000m / 16Gi node) to confirm the pending-pod signal alone is sufficient
        # to elevate the pressure level.
        mock_core = AsyncMock()
        mock_core.get_nodes.return_value = [_make_node("node-1", "userpool")]
        mock_core.get_pods.return_value = [
            _make_pod("pod-1", phase="Pending"),
            _make_pod("pod-2", phase="Pending"),
        ]

        mock_metrics = AsyncMock()
        mock_metrics.get_node_metrics.return_value = [_make_metric("node-1", cpu="1000m", mem="2Gi")]

        with (
            patch("platform_mcp_server.tools.node_pools.K8sCoreClient", return_value=mock_core),
            patch("platform_mcp_server.tools.node_pools.K8sMetricsClient", return_value=mock_metrics),
        ):
            result = await check_node_pool_pressure_handler("prod-eastus")

        assert result.pools[0].pressure_level == "warning"
        assert result.pools[0].pending_pods == 2

    async def test_ok_when_all_below_thresholds(self) -> None:
        # Note 15: Negative-space tests (confirming something does NOT happen) are as
        # important as positive-space tests. This test confirms that the handler does
        # not produce a false alarm when all metrics are comfortably below their
        # thresholds. Without it, a bug that always emits "warning" would only be
        # caught accidentally by the happy-path test's comment rather than by an
        # explicit assertion.
        mock_core = AsyncMock()
        mock_core.get_nodes.return_value = [_make_node("node-1", "userpool")]
        mock_core.get_pods.return_value = []

        mock_metrics = AsyncMock()
        mock_metrics.get_node_metrics.return_value = [_make_metric("node-1", cpu="1000m", mem="2Gi")]

        with (
            patch("platform_mcp_server.tools.node_pools.K8sCoreClient", return_value=mock_core),
            patch("platform_mcp_server.tools.node_pools.K8sMetricsClient", return_value=mock_metrics),
        ):
            result = await check_node_pool_pressure_handler("prod-eastus")

        assert result.pools[0].pressure_level == "ok"

    async def test_multiple_pools_grouped(self) -> None:
        # Note 16: This test validates the aggregation / grouping logic that partitions
        # nodes by their `agentpool` label. Three nodes belonging to two distinct pools
        # are returned from the mock; the handler must produce exactly two pool entries
        # in the result — one per unique pool name. Using a `set` comprehension for the
        # assertion makes order irrelevant, which is correct because the handler may
        # return pools in any order.
        mock_core = AsyncMock()
        mock_core.get_nodes.return_value = [
            _make_node("node-1", "systempool"),
            _make_node("node-2", "userpool"),
            _make_node("node-3", "userpool"),
        ]
        mock_core.get_pods.return_value = []

        mock_metrics = AsyncMock()
        mock_metrics.get_node_metrics.return_value = [
            _make_metric("node-1"),
            _make_metric("node-2"),
            _make_metric("node-3"),
        ]

        with (
            patch("platform_mcp_server.tools.node_pools.K8sCoreClient", return_value=mock_core),
            patch("platform_mcp_server.tools.node_pools.K8sMetricsClient", return_value=mock_metrics),
        ):
            result = await check_node_pool_pressure_handler("prod-eastus")

        # Note 17: Set comparison (`==`) is the idiomatic way to assert that two
        # collections contain the same elements regardless of order. If the handler
        # returned pools in alphabetical or insertion order, a list equality check
        # would be brittle — a refactoring that changed the iteration order would
        # break the test without indicating a real regression.
        pool_names = {p.pool_name for p in result.pools}
        assert pool_names == {"systempool", "userpool"}

    async def test_graceful_degradation_without_metrics(self) -> None:
        # Note 18: `side_effect = Exception(...)` on an `AsyncMock` causes the mock to
        # raise that exception when awaited, instead of returning a value. This is the
        # standard way to simulate downstream failures in async code. When
        # `side_effect` is set to an exception *instance* or *class*, the mock raises
        # it every time it is called.
        #
        # This test verifies that the handler implements graceful degradation: it
        # should continue operating with reduced information rather than propagating
        # the exception to the caller. This is a key resilience pattern for
        # observability tools — a metrics-server outage should not prevent the tool
        # from returning node-level data.
        mock_core = AsyncMock()
        mock_core.get_nodes.return_value = [_make_node("node-1", "userpool")]
        mock_core.get_pods.return_value = []

        mock_metrics = AsyncMock()
        mock_metrics.get_node_metrics.side_effect = Exception("metrics-server unavailable")

        with (
            patch("platform_mcp_server.tools.node_pools.K8sCoreClient", return_value=mock_core),
            patch("platform_mcp_server.tools.node_pools.K8sMetricsClient", return_value=mock_metrics),
        ):
            result = await check_node_pool_pressure_handler("prod-eastus")

        # Note 19: When the metrics client fails, `cpu_requests_percent` and
        # `memory_requests_percent` should be `None` (not 0.0 or some sentinel value)
        # because `None` unambiguously communicates "data not available" to callers,
        # whereas 0.0 would look like "zero utilisation" and could suppress alerts.
        # The test also confirms that the error is surfaced in `result.errors` with a
        # meaningful `source` tag so operators know which subsystem failed.
        assert len(result.pools) == 1
        assert result.pools[0].cpu_requests_percent is None
        assert result.pools[0].memory_requests_percent is None
        assert len(result.errors) == 1
        assert result.errors[0].source == "metrics-server"

    async def test_cluster_all_fan_out(self) -> None:
        # Note 20: The `_all` variant of a handler is a fan-out function that calls
        # the single-cluster handler for every cluster in a predefined registry and
        # collects the results. Testing it with a single set of mocks works because
        # `patch` replaces the class constructor globally — every instantiation of
        # `K8sCoreClient` and `K8sMetricsClient` within the `with` block returns the
        # same mock object. The assertion `len(results) == 6` confirms that the fan-out
        # iterates over all six registered clusters, not just one.
        mock_core = AsyncMock()
        mock_core.get_nodes.return_value = [_make_node("node-1", "userpool")]
        mock_core.get_pods.return_value = []

        mock_metrics = AsyncMock()
        mock_metrics.get_node_metrics.return_value = [_make_metric("node-1")]

        with (
            patch("platform_mcp_server.tools.node_pools.K8sCoreClient", return_value=mock_core),
            patch("platform_mcp_server.tools.node_pools.K8sMetricsClient", return_value=mock_metrics),
        ):
            # Note 21: The import is placed inside the `with` block so that the module
            # is loaded (and its top-level references to `K8sCoreClient` and
            # `K8sMetricsClient` are resolved) while the patches are active. If the
            # import were at the top of the file, the module would capture the *real*
            # classes before the test's patches could intercept them.
            from platform_mcp_server.tools.node_pools import check_node_pool_pressure_all

            results = await check_node_pool_pressure_all()

        assert len(results) == 6

    async def test_summary_line_present(self) -> None:
        # Note 22: This test targets the human-readable `summary` field of the result
        # object. Such fields are often overlooked because they don't affect programmatic
        # consumers, but they are critical for LLM tool output and for operators reading
        # logs. Verifying that `result.summary` is truthy (non-empty) and contains the
        # cluster name catches regressions where the summary template is broken or
        # the cluster name is not threaded through to the output layer.
        mock_core = AsyncMock()
        mock_core.get_nodes.return_value = [_make_node("node-1", "userpool")]
        mock_core.get_pods.return_value = []

        mock_metrics = AsyncMock()
        mock_metrics.get_node_metrics.return_value = [_make_metric("node-1")]

        with (
            patch("platform_mcp_server.tools.node_pools.K8sCoreClient", return_value=mock_core),
            patch("platform_mcp_server.tools.node_pools.K8sMetricsClient", return_value=mock_metrics),
        ):
            result = await check_node_pool_pressure_handler("prod-eastus")

        assert result.summary
        assert "prod-eastus" in result.summary

    async def test_output_has_timestamp(self) -> None:
        # Note 23: Timestamps on tool results are essential for operators to understand
        # data freshness. A result without a timestamp might be cached or stale, so
        # asserting `result.timestamp` is truthy confirms the handler populates the
        # field on every response. The test does not assert the exact timestamp value
        # because that would make the test time-dependent and fragile — it only
        # verifies that the field is set to something non-falsy.
        mock_core = AsyncMock()
        mock_core.get_nodes.return_value = [_make_node("node-1", "userpool")]
        mock_core.get_pods.return_value = []

        mock_metrics = AsyncMock()
        mock_metrics.get_node_metrics.return_value = [_make_metric("node-1")]

        with (
            patch("platform_mcp_server.tools.node_pools.K8sCoreClient", return_value=mock_core),
            patch("platform_mcp_server.tools.node_pools.K8sMetricsClient", return_value=mock_metrics),
        ):
            result = await check_node_pool_pressure_handler("prod-eastus")

        assert result.timestamp
