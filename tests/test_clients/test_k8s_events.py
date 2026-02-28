"""Tests for K8sEventsClient: event filtering by reason, timestamp parsing."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest

from platform_mcp_server.clients.k8s_events import K8sEventsClient
from platform_mcp_server.config import CLUSTER_MAP


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
    event.involved_object.name = object_name
    event.involved_object.kind = object_kind
    event.involved_object.namespace = object_namespace
    event.message = message
    event.last_timestamp = last_timestamp or datetime(2026, 2, 28, 12, 0, 0, tzinfo=UTC)
    event.event_time = None
    event.first_timestamp = None
    event.count = count
    return event


@pytest.fixture
def client() -> K8sEventsClient:
    return K8sEventsClient(CLUSTER_MAP["prod-eastus"])


class TestGetNodeEvents:
    async def test_returns_all_node_events(self, client: K8sEventsClient) -> None:
        mock_api = MagicMock()
        event_list = MagicMock()
        event_list.items = [
            _make_mock_event(reason="NodeReady", object_name="node-1"),
            _make_mock_event(reason="NodeUpgrade", object_name="node-2"),
        ]
        mock_api.list_event_for_all_namespaces.return_value = event_list

        with patch.object(client, "_get_api", return_value=mock_api):
            events = await client.get_node_events()

        assert len(events) == 2

    async def test_filters_by_reason(self, client: K8sEventsClient) -> None:
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
        reasons = {e["reason"] for e in events}
        assert reasons == {"NodeUpgrade", "NodeReady"}

    async def test_timestamp_parsing(self, client: K8sEventsClient) -> None:
        ts = datetime(2026, 2, 28, 15, 30, 0, tzinfo=UTC)
        mock_api = MagicMock()
        event_list = MagicMock()
        event_list.items = [_make_mock_event(reason="NodeReady", last_timestamp=ts)]
        mock_api.list_event_for_all_namespaces.return_value = event_list

        with patch.object(client, "_get_api", return_value=mock_api):
            events = await client.get_node_events()

        assert events[0]["timestamp"] == ts.isoformat()

    async def test_error_handling(self, client: K8sEventsClient) -> None:
        mock_api = MagicMock()
        mock_api.list_event_for_all_namespaces.side_effect = Exception("Connection refused")

        with patch.object(client, "_get_api", return_value=mock_api), pytest.raises(Exception, match="Connection"):
            await client.get_node_events()


class TestGetPodEvents:
    async def test_returns_pod_events_all_namespaces(self, client: K8sEventsClient) -> None:
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
        mock_api = MagicMock()
        event_list = MagicMock()
        event_list.items = [
            _make_mock_event(reason="BackOff", object_name="pod-1", object_namespace="payments"),
        ]
        mock_api.list_namespaced_event.return_value = event_list

        with patch.object(client, "_get_api", return_value=mock_api):
            events = await client.get_pod_events(namespace="payments")

        assert len(events) == 1
        mock_api.list_namespaced_event.assert_called_once()
