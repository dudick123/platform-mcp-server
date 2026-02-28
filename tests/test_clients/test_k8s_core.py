"""Tests for K8sCoreClient: node listing, pod listing, context resolution, error handling."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from platform_mcp_server.clients.k8s_core import PRIMARY_POOL_LABEL, K8sCoreClient
from platform_mcp_server.config import CLUSTER_MAP


def _make_mock_node(
    name: str = "aks-userpool-00000001",
    pool_label: str = "userpool",
    version: str = "v1.29.8",
    unschedulable: bool = False,
    cpu_alloc: str = "4",
    mem_alloc: str = "16Gi",
    use_fallback_label: bool = False,
    no_pool_label: bool = False,
) -> MagicMock:
    node = MagicMock()
    node.metadata.name = name
    labels: dict[str, str] = {}
    if not no_pool_label:
        if use_fallback_label:
            labels["kubernetes.azure.com/agentpool"] = pool_label
        else:
            labels[PRIMARY_POOL_LABEL] = pool_label
    node.metadata.labels = labels
    node.spec.unschedulable = unschedulable
    node.status.node_info.kubelet_version = version
    node.status.allocatable = {"cpu": cpu_alloc, "memory": mem_alloc}
    condition = MagicMock()
    condition.type = "Ready"
    condition.status = "True"
    node.status.conditions = [condition]
    return node


def _make_mock_pod(
    name: str = "test-pod",
    namespace: str = "default",
    phase: str = "Running",
    node_name: str = "aks-userpool-00000001",
) -> MagicMock:
    pod = MagicMock()
    pod.metadata.name = name
    pod.metadata.namespace = namespace
    pod.status.phase = phase
    pod.status.reason = None
    pod.status.message = None
    pod.spec.node_name = node_name
    pod.status.container_statuses = []
    pod.status.conditions = []
    return pod


@pytest.fixture
def client() -> K8sCoreClient:
    config = CLUSTER_MAP["prod-eastus"]
    return K8sCoreClient(config)


class TestGetNodes:
    async def test_returns_nodes_with_pool_grouping(self, client: K8sCoreClient) -> None:
        mock_api = MagicMock()
        node_list = MagicMock()
        node_list.items = [
            _make_mock_node(name="node-1", pool_label="userpool"),
            _make_mock_node(name="node-2", pool_label="systempool"),
        ]
        mock_api.list_node.return_value = node_list

        with patch.object(client, "_get_api", return_value=mock_api):
            nodes = await client.get_nodes()

        assert len(nodes) == 2
        assert nodes[0]["name"] == "node-1"
        assert nodes[0]["pool"] == "userpool"
        assert nodes[1]["pool"] == "systempool"

    async def test_fallback_pool_label(self, client: K8sCoreClient) -> None:
        mock_api = MagicMock()
        node_list = MagicMock()
        node_list.items = [_make_mock_node(name="node-1", pool_label="fbpool", use_fallback_label=True)]
        mock_api.list_node.return_value = node_list

        with patch.object(client, "_get_api", return_value=mock_api):
            nodes = await client.get_nodes()

        assert nodes[0]["pool"] == "fbpool"

    async def test_missing_pool_label_returns_none(self, client: K8sCoreClient) -> None:
        mock_api = MagicMock()
        node_list = MagicMock()
        node_list.items = [_make_mock_node(name="node-1", no_pool_label=True)]
        mock_api.list_node.return_value = node_list

        with patch.object(client, "_get_api", return_value=mock_api):
            nodes = await client.get_nodes()

        assert nodes[0]["pool"] is None

    async def test_unschedulable_node(self, client: K8sCoreClient) -> None:
        mock_api = MagicMock()
        node_list = MagicMock()
        node_list.items = [_make_mock_node(name="node-1", unschedulable=True)]
        mock_api.list_node.return_value = node_list

        with patch.object(client, "_get_api", return_value=mock_api):
            nodes = await client.get_nodes()

        assert nodes[0]["unschedulable"] is True

    async def test_error_handling_for_unreachable_cluster(self, client: K8sCoreClient) -> None:
        mock_api = MagicMock()
        mock_api.list_node.side_effect = Exception("Connection refused")

        with patch.object(client, "_get_api", return_value=mock_api), pytest.raises(Exception, match="Connection"):
            await client.get_nodes()


class TestGetPods:
    async def test_returns_pods_all_namespaces(self, client: K8sCoreClient) -> None:
        mock_api = MagicMock()
        pod_list = MagicMock()
        pod_list.items = [_make_mock_pod(name="pod-1"), _make_mock_pod(name="pod-2")]
        mock_api.list_pod_for_all_namespaces.return_value = pod_list

        with patch.object(client, "_get_api", return_value=mock_api):
            pods = await client.get_pods()

        assert len(pods) == 2
        mock_api.list_pod_for_all_namespaces.assert_called_once()

    async def test_returns_pods_filtered_by_namespace(self, client: K8sCoreClient) -> None:
        mock_api = MagicMock()
        pod_list = MagicMock()
        pod_list.items = [_make_mock_pod(name="pod-1", namespace="payments")]
        mock_api.list_namespaced_pod.return_value = pod_list

        with patch.object(client, "_get_api", return_value=mock_api):
            pods = await client.get_pods(namespace="payments")

        assert len(pods) == 1
        mock_api.list_namespaced_pod.assert_called_once_with("payments")

    async def test_error_handling(self, client: K8sCoreClient) -> None:
        mock_api = MagicMock()
        mock_api.list_pod_for_all_namespaces.side_effect = Exception("Timeout")

        with patch.object(client, "_get_api", return_value=mock_api), pytest.raises(Exception, match="Timeout"):
            await client.get_pods()

    async def test_container_status_waiting(self, client: K8sCoreClient) -> None:
        pod = _make_mock_pod(name="crash-pod")
        cs = MagicMock()
        cs.name = "app"
        cs.ready = False
        cs.restart_count = 5
        cs.state.waiting.reason = "CrashLoopBackOff"
        cs.state.terminated = None
        cs.last_state.terminated = None
        pod.status.container_statuses = [cs]
        mock_api = MagicMock()
        pod_list = MagicMock()
        pod_list.items = [pod]
        mock_api.list_pod_for_all_namespaces.return_value = pod_list

        with patch.object(client, "_get_api", return_value=mock_api):
            pods = await client.get_pods()

        assert pods[0]["container_statuses"][0]["state"] == {"waiting": {"reason": "CrashLoopBackOff"}}

    async def test_container_status_terminated(self, client: K8sCoreClient) -> None:
        pod = _make_mock_pod(name="done-pod")
        cs = MagicMock()
        cs.name = "worker"
        cs.ready = False
        cs.restart_count = 0
        cs.state.waiting = None
        cs.state.terminated.reason = "Completed"
        cs.state.terminated.exit_code = 0
        cs.last_state.terminated = None
        pod.status.container_statuses = [cs]
        mock_api = MagicMock()
        pod_list = MagicMock()
        pod_list.items = [pod]
        mock_api.list_pod_for_all_namespaces.return_value = pod_list

        with patch.object(client, "_get_api", return_value=mock_api):
            pods = await client.get_pods()

        assert pods[0]["container_statuses"][0]["state"] == {"terminated": {"reason": "Completed", "exit_code": 0}}

    async def test_container_status_last_terminated(self, client: K8sCoreClient) -> None:
        pod = _make_mock_pod(name="oom-pod")
        cs = MagicMock()
        cs.name = "worker"
        cs.ready = True
        cs.restart_count = 3
        cs.state.waiting = None
        cs.state.terminated = None
        cs.last_state.terminated.reason = "OOMKilled"
        cs.last_state.terminated.exit_code = 137
        pod.status.container_statuses = [cs]
        mock_api = MagicMock()
        pod_list = MagicMock()
        pod_list.items = [pod]
        mock_api.list_pod_for_all_namespaces.return_value = pod_list

        with patch.object(client, "_get_api", return_value=mock_api):
            pods = await client.get_pods()

        assert pods[0]["container_statuses"][0]["last_terminated"] == {
            "reason": "OOMKilled",
            "exit_code": 137,
        }

    async def test_field_selector_passed(self, client: K8sCoreClient) -> None:
        mock_api = MagicMock()
        pod_list = MagicMock()
        pod_list.items = []
        mock_api.list_pod_for_all_namespaces.return_value = pod_list

        with patch.object(client, "_get_api", return_value=mock_api):
            await client.get_pods(field_selector="status.phase=Pending")

        mock_api.list_pod_for_all_namespaces.assert_called_once_with(field_selector="status.phase=Pending")
