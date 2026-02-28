# Note 1: This file tests K8sEventsClient, which wraps the Kubernetes events API for
# both node-level and pod-level events. The two test classes (TestGetNodeEvents and
# TestGetPodEvents) map directly to the two public methods on the client. Node events
# and pod events differ in how they are filtered and which API endpoint they use, which
# is why they are tested separately rather than sharing a single test class.
"""Tests for K8sEventsClient: event filtering by reason, timestamp parsing."""

# Note 2: `from __future__ import annotations` at the module level defers annotation
# evaluation for all function signatures in this file. This is the project's standard
# header ordering: future imports, then stdlib imports, then third-party, then local.
from __future__ import annotations

# Note 3: datetime and UTC are imported from Python's standard library to construct
# timezone-aware datetime objects for the `last_timestamp` field of mocked events.
# Using timezone-aware datetimes (via `tzinfo=UTC`) is required for consistent timestamp
# serialization — naive datetimes would serialize without timezone info and could produce
# different ISO 8601 strings depending on the system locale.
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest

from platform_mcp_server.clients.k8s_events import K8sEventsClient
from platform_mcp_server.config import CLUSTER_MAP


# Note 4: _make_mock_event centralizes the construction of mock Kubernetes Event objects.
# Kubernetes events have multiple timestamp fields (last_timestamp, event_time, and
# first_timestamp) because the API evolved over time: older events use `last_timestamp`,
# newer events use `event_time`. Setting all three explicitly in the factory (even to None)
# ensures tests exercise the production code's timestamp-priority logic without surprises
# from MagicMock's auto-attribute behavior.
def _make_mock_event(
    reason: str = "NodeReady",
    object_name: str = "node-1",
    object_kind: str = "Node",
    object_namespace: str | None = None,
    message: str = "Node is ready",
    last_timestamp: datetime | None = None,
    count: int = 1,
) -> MagicMock:
    event = MagicMock()
    event.reason = reason
    # Note 5: The involved_object fields (name, kind, namespace) identify the Kubernetes
    # resource the event is about. Kind distinguishes "Node" events from "Pod" events —
    # production filtering logic may branch on this field to separate node events from
    # pod events even when both arrive from the same API call.
    event.involved_object.name = object_name
    event.involved_object.kind = object_kind
    event.involved_object.namespace = object_namespace
    event.message = message
    # Note 6: The `or` expression `last_timestamp or datetime(...)` provides a default
    # timestamp when none is supplied. Using a fixed date (2026-02-28) rather than
    # `datetime.now()` makes tests deterministic: a test that asserts on the timestamp
    # value will not fail because the test ran at an unexpected time.
    event.last_timestamp = last_timestamp or datetime(2026, 2, 28, 12, 0, 0, tzinfo=UTC)
    # Note 7: event_time and first_timestamp are set to None explicitly so that the
    # production timestamp-priority logic is testable. If these were left as MagicMock
    # defaults (truthy), the code might use event_time or first_timestamp instead of
    # last_timestamp, producing unexpected results in the test assertions.
    event.event_time = None
    event.first_timestamp = None
    event.count = count
    return event


# Note 8: The client fixture is scoped to function-level (pytest's default), so each
# test gets a fresh K8sEventsClient with `_api = None`. This prevents test pollution
# where a cached API object from one test affects another test's patch behavior.
@pytest.fixture
def client() -> K8sEventsClient:
    return K8sEventsClient(CLUSTER_MAP["prod-eastus"])


class TestGetNodeEvents:
    async def test_returns_all_node_events(self, client: K8sEventsClient) -> None:
        # Note 9: Two events with different reasons are provided to verify the production
        # code does not accidentally filter events when no `reasons` argument is given.
        # A single-event list would pass even if the code applied unintentional filtering.
        # Using len(events) == 2 is a stronger signal that all events are returned.
        mock_api = MagicMock()
        event_list = MagicMock()
        event_list.items = [
            _make_mock_event(reason="NodeReady", object_name="node-1"),
            _make_mock_event(reason="NodeUpgrade", object_name="node-2"),
        ]
        mock_api.list_event_for_all_namespaces.return_value = event_list

        # Note 10: `patch.object(client, "_get_api", return_value=mock_api)` replaces the
        # `_get_api` method on this specific client instance. The `return_value` shorthand
        # on patch.object means the mock function returns `mock_api` when called, rather
        # than returning a new MagicMock automatically. This avoids an extra `.return_value`
        # access in the test setup.
        with patch.object(client, "_get_api", return_value=mock_api):
            events = await client.get_node_events()

        assert len(events) == 2

    async def test_filters_by_reason(self, client: K8sEventsClient) -> None:
        # Note 11: Three events are created but only two reasons are requested. This setup
        # tests the exclusion logic: "NodeNotReady" should be filtered out when the caller
        # asks for only ["NodeUpgrade", "NodeReady"]. The set assertion `reasons == {...}`
        # verifies the correct subset is returned without caring about order.
        mock_api = MagicMock()
        event_list = MagicMock()
        event_list.items = [
            _make_mock_event(reason="NodeReady", object_name="node-1"),
            _make_mock_event(reason="NodeUpgrade", object_name="node-2"),
            _make_mock_event(reason="NodeNotReady", object_name="node-3"),
        ]
        mock_api.list_event_for_all_namespaces.return_value = event_list

        with patch.object(client, "_get_api", return_value=mock_api):
            events = await client.get_node_events(reasons=["NodeUpgrade", "NodeReady"])

        assert len(events) == 2
        # Note 12: A set comprehension is used to collect all distinct reason values from
        # the filtered result, then compared to the expected set. This pattern is resilient
        # to ordering differences (the API may return events in any order) and validates
        # that no unexpected reason values slipped through the filter.
        reasons = {e["reason"] for e in events}
        assert reasons == {"NodeUpgrade", "NodeReady"}

    async def test_timestamp_parsing(self, client: K8sEventsClient) -> None:
        # Note 13: A specific timezone-aware datetime is created and passed to the mock
        # event. The test then asserts that the output timestamp matches `ts.isoformat()`.
        # This verifies the production code serializes the timestamp correctly (including
        # timezone offset) rather than losing timezone info or changing the format. ISO 8601
        # is the standard format for API responses and log aggregation pipelines.
        ts = datetime(2026, 2, 28, 15, 30, 0, tzinfo=UTC)
        mock_api = MagicMock()
        event_list = MagicMock()
        event_list.items = [_make_mock_event(reason="NodeReady", last_timestamp=ts)]
        mock_api.list_event_for_all_namespaces.return_value = event_list

        with patch.object(client, "_get_api", return_value=mock_api):
            events = await client.get_node_events()

        assert events[0]["timestamp"] == ts.isoformat()

    async def test_error_handling(self, client: K8sEventsClient) -> None:
        # Note 14: "Connection refused" simulates the Kubernetes API server being
        # unreachable, the most common failure when a node or cluster is in a bad state.
        # The test ensures the client does not catch and suppress this error, preserving
        # the ability for callers to implement retry logic or surface the error to the user.
        mock_api = MagicMock()
        mock_api.list_event_for_all_namespaces.side_effect = Exception("Connection refused")

        with patch.object(client, "_get_api", return_value=mock_api), pytest.raises(Exception, match="Connection"):
            await client.get_node_events()


class TestGetPodEvents:
    async def test_returns_pod_events_all_namespaces(self, client: K8sEventsClient) -> None:
        # Note 15: `object_namespace="default"` is set on the mock event to simulate a
        # real pod event that carries namespace information. Pod events always have a
        # namespace (unlike node events, which are cluster-scoped). The assertion on
        # `events[0]["pod_name"]` verifies the client correctly maps `involved_object.name`
        # to the "pod_name" key in its output dict.
        mock_api = MagicMock()
        event_list = MagicMock()
        event_list.items = [
            _make_mock_event(reason="FailedScheduling", object_name="pod-1", object_namespace="default"),
        ]
        mock_api.list_event_for_all_namespaces.return_value = event_list

        with patch.object(client, "_get_api", return_value=mock_api):
            events = await client.get_pod_events()

        assert len(events) == 1
        assert events[0]["pod_name"] == "pod-1"

    async def test_returns_pod_events_filtered_namespace(self, client: K8sEventsClient) -> None:
        # Note 16: When a namespace is provided, the client should call `list_namespaced_event`
        # instead of `list_event_for_all_namespaces`. This mirrors the same namespace-filtering
        # pattern tested in TestGetPods — namespace-scoped queries are more efficient because
        # the API server does the filtering rather than returning all events cluster-wide.
        # `assert_called_once()` verifies the correct method branch was taken.
        mock_api = MagicMock()
        event_list = MagicMock()
        event_list.items = [
            _make_mock_event(reason="BackOff", object_name="pod-1", object_namespace="payments"),
        ]
        mock_api.list_namespaced_event.return_value = event_list

        with patch.object(client, "_get_api", return_value=mock_api):
            events = await client.get_pod_events(namespace="payments")

        assert len(events) == 1
        # Note 17: `assert_called_once()` (without `_with`) only checks that the method
        # was called exactly once, not what arguments it received. This is intentionally
        # less strict here than in test_k8s_core.py, where `assert_called_once_with`
        # was used. If verifying the namespace argument is important, this should be
        # upgraded to `mock_api.list_namespaced_event.assert_called_once_with("payments")`.
        mock_api.list_namespaced_event.assert_called_once()
