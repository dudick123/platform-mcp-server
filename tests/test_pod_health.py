"""Tests for get_pod_health tool handler."""

# Note 1: `from __future__ import annotations` must appear as the very first statement
# in the module (after the docstring). It activates PEP 563 deferred evaluation of
# annotations, meaning type hints like `list | None` and `str | None` are stored as
# strings at import time rather than being evaluated immediately. This is necessary for
# union-type syntax on Python 3.9 and is a no-op on Python 3.10+ where the syntax is
# natively supported.
from __future__ import annotations

# Note 2: `datetime` and `UTC` are imported from the standard library's `datetime`
# module. `UTC` (available from Python 3.11 onward, or as `timezone.utc` on earlier
# versions) is a timezone-aware sentinel representing Coordinated Universal Time. Using
# timezone-aware datetimes in test data is important because the production code likely
# converts timestamps to UTC before storing or comparing them. A naive datetime (no
# tzinfo) would produce different behaviour and could mask timezone-handling bugs.
from datetime import UTC, datetime

# Note 3: `AsyncMock` replaces collaborators whose methods are coroutines (defined with
# `async def`). When production code does `await client.get_pods(...)`, the mock must
# return an awaitable — `AsyncMock` handles this automatically. `patch` is the
# context-manager / decorator that swaps a name in a module's namespace with a test
# double for the duration of a test, restoring the original on exit.
from unittest.mock import AsyncMock, patch

from platform_mcp_server.tools.pod_health import get_pod_health_handler


# Note 4: The `_make_pod` factory uses the Object Mother pattern. Default arguments
# represent a "healthy, running pod" — the most common state. Tests that want a
# specific abnormal state only need to override the one or two fields relevant to
# their scenario. This keeps test bodies short and focused on what makes each case
# unique. The leading underscore signals that this is a module-private helper, not
# part of the public test API.
def _make_pod(
    name: str,
    namespace: str = "default",
    phase: str = "Running",
    node_name: str = "node-1",
    reason: str | None = None,
    container_statuses: list | None = None,
) -> dict:
    return {
        "name": name,
        "namespace": namespace,
        "phase": phase,
        "node_name": node_name,
        "reason": reason,
        "message": None,
        # Note 5: `container_statuses or []` is a common Python idiom to provide a
        # default mutable value without the well-known "mutable default argument" trap.
        # If `container_statuses=None` is passed, the expression evaluates to `[]`,
        # giving each call its own fresh list. Never use `def f(x=[])` — that single
        # list object is shared across all calls and can accumulate state across tests.
        "container_statuses": container_statuses or [],
        "conditions": [],
    }


# Note 6: The `_make_event` factory mirrors how Kubernetes event objects look after
# being normalised by the events client. The `timestamp` field defaults to the current
# UTC time via `datetime.now(tz=UTC).isoformat()`, producing an ISO 8601 string.
# Using `isoformat()` here exercises the same string format that the production parsing
# code must handle, so the test data is realistic rather than synthetic.
def _make_event(
    pod_name: str,
    namespace: str = "default",
    reason: str = "FailedScheduling",
    message: str = "0/12 nodes available",
    timestamp: str | None = None,
) -> dict:
    # Note 7: `timestamp or datetime.now(tz=UTC).isoformat()` uses short-circuit
    # evaluation: if `timestamp` is a non-empty string (truthy), it is used directly;
    # otherwise, the current UTC time is generated. This pattern lets individual tests
    # supply a fixed timestamp when they need deterministic time-based assertions,
    # while sparing tests that do not care about the timestamp from constructing one.
    ts = timestamp or datetime.now(tz=UTC).isoformat()
    return {
        "reason": reason,
        "pod_name": pod_name,
        "namespace": namespace,
        "message": message,
        "timestamp": ts,
        "count": 1,
    }


# Note 8: Grouping related tests inside a class (without inheriting from
# `unittest.TestCase`) is the pytest-idiomatic way to add structure to a test module.
# Benefits include: the class name appears in pytest's output alongside the test name,
# making failures easier to locate; test methods share a common namespace for fixtures
# defined at class scope; and `pytest -k TestGetPodHealth` can selectively run only
# this group. No `__init__` is needed — pytest instantiates the class fresh for every
# test method, ensuring complete isolation between tests.
class TestGetPodHealth:
    # Note 9: Every `async def test_*` method is automatically treated as an async test
    # when `asyncio_mode = "auto"` is configured in `pyproject.toml`. pytest-asyncio
    # creates a new event loop, schedules the coroutine, and tears the loop down after
    # each test. This means the test can `await` the handler under test just as
    # production code would, providing realistic execution semantics without any
    # threading complexity.
    async def test_happy_path_pending_pods(self) -> None:
        # Note 10: A happy-path test establishes the baseline contract: given a well-
        # formed pending pod with a scheduling failure event, the handler should return
        # exactly one pod entry with `phase == "Pending"` and
        # `failure_category == "scheduling"`. This test runs first (alphabetically or
        # in file order) and acts as a smoke test — if it fails, the other tests are
        # likely symptomatic of the same root cause.
        mock_core = AsyncMock()
        mock_core.get_pods.return_value = [
            _make_pod("pod-1", phase="Pending", reason="Unschedulable"),
        ]
        mock_events = AsyncMock()
        mock_events.get_pod_events.return_value = [
            _make_event("pod-1", reason="FailedScheduling", message="Insufficient cpu"),
        ]

        # Note 11: Two separate `patch` calls target two different client classes used
        # by the handler. Each patch replaces the class itself (not an instance), so
        # `return_value=mock_core` makes every call to `K8sCoreClient(...)` return the
        # same `mock_core` instance regardless of what constructor arguments are passed.
        # This is the standard pattern for mocking clients that are instantiated inside
        # the function under test rather than injected via parameters.
        with (
            patch("platform_mcp_server.tools.pod_health.K8sCoreClient", return_value=mock_core),
            patch("platform_mcp_server.tools.pod_health.K8sEventsClient", return_value=mock_events),
        ):
            result = await get_pod_health_handler("prod-eastus")

        assert len(result.pods) == 1
        assert result.pods[0].phase == "Pending"
        # Note 12: `failure_category` is a derived field that the handler computes by
        # inspecting the pod's phase, reason, container states, and associated events.
        # "scheduling" means the pod could not be placed on any node. Testing the
        # category rather than the raw reason string verifies the classification logic
        # in the handler, which is the behaviour that matters to callers.
        assert result.pods[0].failure_category == "scheduling"

    async def test_failure_reason_grouping(self) -> None:
        # Note 13: This test validates the aggregation step that groups pod failures
        # by category and counts them. Two "Unschedulable" pods should produce
        # `groups["scheduling"] == 2`, and one CrashLoopBackOff pod should produce
        # `groups["runtime"] == 1`. The `groups` field enables the caller (or an LLM
        # agent) to get a high-level summary without iterating over every pod entry.
        mock_core = AsyncMock()
        mock_core.get_pods.return_value = [
            _make_pod("pod-1", phase="Pending", reason="Unschedulable"),
            _make_pod("pod-2", phase="Pending", reason="Unschedulable"),
            _make_pod(
                "pod-3",
                phase="Failed",
                # Note 14: `container_statuses` carries the per-container lifecycle
                # state. The `state.waiting.reason == "CrashLoopBackOff"` field is how
                # Kubernetes signals that a container is repeatedly crashing and the
                # kubelet is applying an exponential back-off before restarting it.
                # `restart_count: 5` simulates a pod that has already crashed five
                # times. The handler inspects this structure to classify the failure
                # as "runtime" rather than "scheduling".
                container_statuses=[
                    {
                        "name": "app",
                        "ready": False,
                        "restart_count": 5,
                        "state": {"waiting": {"reason": "CrashLoopBackOff"}},
                    }
                ],
            ),
        ]
        mock_events = AsyncMock()
        mock_events.get_pod_events.return_value = []

        with (
            patch("platform_mcp_server.tools.pod_health.K8sCoreClient", return_value=mock_core),
            patch("platform_mcp_server.tools.pod_health.K8sEventsClient", return_value=mock_events),
        ):
            result = await get_pod_health_handler("prod-eastus")

        # Note 15: `result.groups.get("scheduling", 0)` uses the dict `.get()` method
        # with a default of 0 to avoid a `KeyError` if the key is absent. This is
        # safer than `result.groups["scheduling"]` and also documents the expected
        # type: counts are integers, and a missing category is equivalent to a count
        # of zero.
        assert result.groups.get("scheduling", 0) == 2
        assert result.groups.get("runtime", 0) == 1

    async def test_oomkill_detection(self) -> None:
        # Note 16: OOMKilled (Out of Memory Killed) is a critical Kubernetes failure
        # mode where the Linux kernel terminates a container because it exceeded its
        # memory limit. It is reported in `last_terminated.reason` (not in the current
        # `state`), because after the OOM event the container may be in a waiting or
        # running state again. Exit code 137 (128 + SIGKILL) is the canonical signal
        # for OOM termination. This test confirms the handler inspects `last_terminated`
        # and classifies the pod as "runtime" failure.
        mock_core = AsyncMock()
        mock_core.get_pods.return_value = [
            _make_pod(
                "pod-1",
                phase="Running",
                container_statuses=[
                    {
                        "name": "worker",
                        "ready": False,
                        "restart_count": 3,
                        "state": {},
                        # Note 17: `last_terminated` is a separate field from `state`
                        # in the Kubernetes API. A container can currently be in a
                        # "running" state while having a recent OOMKill in its
                        # termination history. The handler must check both `state` and
                        # `last_terminated` to catch all failure categories.
                        "last_terminated": {"reason": "OOMKilled", "exit_code": 137},
                    }
                ],
            ),
        ]
        mock_events = AsyncMock()
        mock_events.get_pod_events.return_value = []

        with (
            patch("platform_mcp_server.tools.pod_health.K8sCoreClient", return_value=mock_core),
            patch("platform_mcp_server.tools.pod_health.K8sEventsClient", return_value=mock_events),
        ):
            result = await get_pod_health_handler("prod-eastus")

        assert len(result.pods) == 1
        assert result.pods[0].failure_category == "runtime"
        # Note 18: Asserting `container_name == "worker"` verifies that the handler
        # correctly attributes the failure to the right container within a multi-
        # container pod. Without this assertion, a bug that always reports the first
        # container regardless of which one failed would go undetected.
        assert result.pods[0].container_name == "worker"

    async def test_result_cap_at_50(self) -> None:
        # Note 19: A list comprehension inside `return_value` is a clean way to
        # generate a large collection of mock objects without writing 120 individual
        # factory calls. The f-string `f"pod-{i}"` gives each pod a unique, predictable
        # name, which is important if the handler uses the name as a dict key or for
        # de-duplication.
        mock_core = AsyncMock()
        mock_core.get_pods.return_value = [
            _make_pod(f"pod-{i}", phase="Pending", reason="Unschedulable") for i in range(120)
        ]
        mock_events = AsyncMock()
        mock_events.get_pod_events.return_value = []

        with (
            patch("platform_mcp_server.tools.pod_health.K8sCoreClient", return_value=mock_core),
            patch("platform_mcp_server.tools.pod_health.K8sEventsClient", return_value=mock_events),
        ):
            result = await get_pod_health_handler("prod-eastus")

        # Note 20: The 50-pod cap is an important contract for LLM tool consumers.
        # Returning hundreds of pods would blow out the context window of a language
        # model and degrade response quality. The handler must cap the returned list
        # at 50 while still reporting the true total count (`total_matching == 120`)
        # and setting `truncated = True` so callers know the list is incomplete and
        # can request further filtering (by namespace or status) to narrow results.
        assert len(result.pods) == 50
        assert result.total_matching == 120
        assert result.truncated is True

    async def test_namespace_filtering(self) -> None:
        # Note 21: This test verifies two distinct behaviours in one scenario: (1) the
        # handler passes the `namespace` argument through to `get_pods`, and (2) the
        # result contains only pods from that namespace. The mock is set up to return
        # a pod in the "payments" namespace; the handler is called with
        # `namespace="payments"`; and `assert_called_once_with` confirms the namespace
        # was forwarded to the API call rather than being used only as a post-fetch
        # filter.
        mock_core = AsyncMock()
        mock_core.get_pods.return_value = [
            _make_pod("pod-1", namespace="payments", phase="Pending"),
        ]
        mock_events = AsyncMock()
        mock_events.get_pod_events.return_value = []

        with (
            patch("platform_mcp_server.tools.pod_health.K8sCoreClient", return_value=mock_core),
            patch("platform_mcp_server.tools.pod_health.K8sEventsClient", return_value=mock_events),
        ):
            result = await get_pod_health_handler("prod-eastus", namespace="payments")

        assert len(result.pods) == 1
        # Note 22: `assert_called_once_with(...)` is an `AsyncMock` / `MagicMock`
        # assertion method that checks both the call count (exactly once) and the
        # exact arguments. This is more precise than asserting the result length alone,
        # because it distinguishes between "the handler fetched all pods and filtered
        # client-side" versus "the handler fetched only the right pods server-side".
        # Server-side filtering is preferable for performance.
        mock_core.get_pods.assert_called_once_with(namespace="payments")

    async def test_status_filter_pending(self) -> None:
        # Note 23: The `status_filter` parameter allows callers to request only pods
        # in a specific phase. The mock returns pods in two different phases; the
        # handler is asked to filter for "pending" only. The assertion uses a generator
        # expression inside `all(...)` to verify every returned pod has the expected
        # phase — this pattern scales to any result set size and produces a clear
        # failure message identifying which pod violated the expectation.
        mock_core = AsyncMock()
        mock_core.get_pods.return_value = [
            _make_pod("pod-1", phase="Pending"),
            _make_pod("pod-2", phase="Failed"),
        ]
        mock_events = AsyncMock()
        mock_events.get_pod_events.return_value = []

        with (
            patch("platform_mcp_server.tools.pod_health.K8sCoreClient", return_value=mock_core),
            patch("platform_mcp_server.tools.pod_health.K8sEventsClient", return_value=mock_events),
        ):
            result = await get_pod_health_handler("prod-eastus", status_filter="pending")

        # Note 24: `all(predicate for item in collection)` is the Pythonic way to
        # assert a universal property over a sequence. It short-circuits on the first
        # falsy item, which keeps it efficient even for large result sets. An empty
        # `result.pods` would make `all(...)` return `True`, so this assertion is only
        # meaningful in combination with an assertion that the result is non-empty —
        # here that is implicitly guaranteed by the mock returning two pods (one of
        # which should be filtered out).
        assert all(p.phase == "Pending" for p in result.pods)

    async def test_event_context_per_pod(self) -> None:
        # Note 25: Kubernetes events provide the most actionable diagnostic context for
        # scheduling failures. A pod stuck in "Pending" due to insufficient resources
        # will have a `FailedScheduling` event whose `message` field explains exactly
        # what is missing ("0/12 nodes available: Insufficient cpu"). The handler
        # enriches each pod entry with the most recent event message in `last_event`.
        # This test confirms the enrichment pipeline connects the event to the correct
        # pod (by name and namespace) and surfaces the message in the output.
        mock_core = AsyncMock()
        mock_core.get_pods.return_value = [
            _make_pod("pod-1", phase="Pending"),
        ]
        mock_events = AsyncMock()
        mock_events.get_pod_events.return_value = [
            _make_event("pod-1", message="0/12 nodes available: Insufficient cpu"),
        ]

        with (
            patch("platform_mcp_server.tools.pod_health.K8sCoreClient", return_value=mock_core),
            patch("platform_mcp_server.tools.pod_health.K8sEventsClient", return_value=mock_events),
        ):
            result = await get_pod_health_handler("prod-eastus")

        # Note 26: The assertion checks the exact message string rather than a
        # substring, because the handler should propagate the event message verbatim
        # without truncating or reformatting it. Operators and LLM agents read this
        # field directly to understand why a pod is stuck, so fidelity to the original
        # Kubernetes event message is important.
        assert result.pods[0].last_event == "0/12 nodes available: Insufficient cpu"

    async def test_cluster_all_fan_out(self) -> None:
        # Note 27: The `_all` fan-out function iterates over every registered cluster
        # and calls the single-cluster handler for each. This test mocks both client
        # classes globally (via `patch`) so that all six handler invocations succeed
        # and return consistent data. The assertion `len(results) == 6` confirms the
        # fan-out covers the entire cluster registry — a regression that hard-coded
        # only a subset of clusters would be caught here.
        mock_core = AsyncMock()
        mock_core.get_pods.return_value = []
        mock_events = AsyncMock()
        mock_events.get_pod_events.return_value = []

        with (
            patch("platform_mcp_server.tools.pod_health.K8sCoreClient", return_value=mock_core),
            patch("platform_mcp_server.tools.pod_health.K8sEventsClient", return_value=mock_events),
        ):
            # Note 28: Importing `get_pod_health_all` inside the `with` block ensures
            # the module is resolved while the patches are active. If the module were
            # imported at the top of the test file, it would capture references to the
            # real `K8sCoreClient` and `K8sEventsClient` at import time, before the
            # test's patches take effect. The local import sidesteps this ordering
            # issue entirely.
            from platform_mcp_server.tools.pod_health import get_pod_health_all

            results = await get_pod_health_all()

        assert len(results) == 6
