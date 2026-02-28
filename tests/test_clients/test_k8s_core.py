# Note 1: This file exercises K8sCoreClient, which wraps the Kubernetes core/v1 API
# (nodes and pods). The tests are organized into two classes — TestGetNodes and
# TestGetPods — mirroring the two primary public methods on K8sCoreClient. This 1:1
# mapping between test class and production method makes navigation intuitive.
"""Tests for K8sCoreClient: node listing, pod listing, context resolution, error handling."""

# Note 2: `from __future__ import annotations` enables PEP 563 postponed annotation
# evaluation for the whole module. This is a project-wide convention that reduces
# coupling between type annotations and import order, and is especially useful in
# files with complex cross-module type references.
from __future__ import annotations

# Note 3: Only MagicMock and patch are needed from unittest.mock here because all
# Kubernetes API interactions are synchronous at the mock level (the async behavior
# is in the production code, not in the mock itself). pytest-asyncio transparently
# wraps the async test coroutines in an event loop.
from unittest.mock import MagicMock, patch

import pytest

# Note 4: PRIMARY_POOL_LABEL is imported alongside K8sCoreClient so tests can use
# the exact constant the production code uses when reading node labels. Hard-coding
# the label string in tests would be fragile — if the constant changes, tests should
# fail because the import breaks, not because of a silent string mismatch.
from platform_mcp_server.clients.k8s_core import PRIMARY_POOL_LABEL, K8sCoreClient
from platform_mcp_server.config import CLUSTER_MAP


# Note 5: _make_mock_node is a factory function that creates a MagicMock simulating
# a Kubernetes Node object. The keyword arguments with defaults represent the most
# common configurable fields. Tests that only care about one aspect (e.g., schedulability)
# call the factory with just that argument, keeping the test body concise and focused.
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
    # Note 6: The labels dict is built conditionally based on `use_fallback_label` and
    # `no_pool_label`. This design tests the production code's label-reading priority:
    # it should check PRIMARY_POOL_LABEL first, then fall back to a secondary label,
    # and return None if neither is present. The three code paths need three factory
    # configurations — hence the two boolean flags.
    labels: dict[str, str] = {}
    if not no_pool_label:
        if use_fallback_label:
            labels["kubernetes.azure.com/agentpool"] = pool_label
        else:
            labels[PRIMARY_POOL_LABEL] = pool_label
    node.metadata.labels = labels
    node.spec.unschedulable = unschedulable
    node.status.node_info.kubelet_version = version
    # Note 7: allocatable is a dict rather than a nested object because the Kubernetes
    # API returns resource quantities as a plain mapping. Using "4" for CPU (4 cores)
    # and "16Gi" for memory are realistic AKS node defaults for a Standard_D4s_v3 SKU.
    node.status.allocatable = {"cpu": cpu_alloc, "memory": mem_alloc}
    # Note 8: Kubernetes node conditions are a list of condition objects, each with a
    # `type` and `status`. We create a minimal Ready=True condition because production
    # code typically inspects `conditions` to determine node health. Using a separate
    # MagicMock for `condition` (rather than a plain dict) matches the real SDK object
    # structure where conditions are typed Kubernetes objects.
    condition = MagicMock()
    condition.type = "Ready"
    condition.status = "True"
    node.status.conditions = [condition]
    return node


# Note 9: _make_mock_pod mirrors the node factory pattern for Kubernetes Pod objects.
# Pods have richer state than nodes (phase, container statuses, conditions), but this
# base factory only sets the fields needed for the simple list/filter tests. Container
# status scenarios are built up manually in the relevant test methods.
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
    # Note 10: reason and message are set to None explicitly (rather than relying on
    # MagicMock's default of returning a MagicMock for unset attributes) because
    # production code likely checks `if pod.status.reason:` — a MagicMock is truthy
    # by default, which would cause false positives in conditional checks.
    pod.status.reason = None
    pod.status.message = None
    pod.spec.node_name = node_name
    # Note 11: Empty lists are used for container_statuses and conditions to simulate a
    # healthy pod with no container-level issues. Tests that need non-empty container
    # statuses build a new pod and manually populate these lists (see test_container_status_*
    # tests below).
    pod.status.container_statuses = []
    pod.status.conditions = []
    return pod


# Note 12: This fixture provides a fresh K8sCoreClient instance for every test in this
# file. pytest creates a new instance per test by default (function scope), ensuring
# that state set during one test (e.g., a cached _api value) does not leak into
# the next test. Explicit teardown is not needed because the client has no persistent
# external connections when _api is replaced by a mock.
@pytest.fixture
def client() -> K8sCoreClient:
    config = CLUSTER_MAP["prod-eastus"]
    return K8sCoreClient(config)


class TestGetNodes:
    async def test_returns_nodes_with_pool_grouping(self, client: K8sCoreClient) -> None:
        # Note 13: `mock_api` simulates the kubernetes.client.CoreV1Api object. The
        # mock is injected via `patch.object(client, "_get_api", return_value=mock_api)`,
        # which means every call to `client._get_api()` inside `get_nodes()` returns
        # our controlled mock without touching any real Kubernetes cluster.
        mock_api = MagicMock()
        node_list = MagicMock()
        # Note 14: Two nodes from different pools are provided. Having two items verifies
        # that the production code iterates over all items (not just the first), and
        # having different pool labels verifies that pool grouping is per-node, not a
        # global default.
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
        # Note 15: This test exercises the secondary label fallback path. AKS nodes can
        # carry pool information under either of two label keys depending on the cluster
        # configuration or API version. The `use_fallback_label=True` flag in the factory
        # omits the primary label and sets only the fallback, forcing the production code
        # to use its secondary lookup branch.
        mock_api = MagicMock()
        node_list = MagicMock()
        node_list.items = [_make_mock_node(name="node-1", pool_label="fbpool", use_fallback_label=True)]
        mock_api.list_node.return_value = node_list

        with patch.object(client, "_get_api", return_value=mock_api):
            nodes = await client.get_nodes()

        assert nodes[0]["pool"] == "fbpool"

    async def test_missing_pool_label_returns_none(self, client: K8sCoreClient) -> None:
        # Note 16: `no_pool_label=True` creates a node with an empty labels dict, which
        # represents a node that has no pool label at all. The test asserts that `pool`
        # is None (the Python None, not the string "None") in this case. This is important
        # because callers of `get_nodes()` may branch on `if node["pool"] is not None`.
        mock_api = MagicMock()
        node_list = MagicMock()
        node_list.items = [_make_mock_node(name="node-1", no_pool_label=True)]
        mock_api.list_node.return_value = node_list

        with patch.object(client, "_get_api", return_value=mock_api):
            nodes = await client.get_nodes()

        assert nodes[0]["pool"] is None

    async def test_unschedulable_node(self, client: K8sCoreClient) -> None:
        # Note 17: An unschedulable node has `spec.unschedulable = True` in Kubernetes.
        # This state is set by `kubectl cordon` and is used during rolling upgrades to
        # prevent new pods from being scheduled on a node being drained. Testing this
        # ensures the client correctly surfaces the cordon state to consumers.
        mock_api = MagicMock()
        node_list = MagicMock()
        node_list.items = [_make_mock_node(name="node-1", unschedulable=True)]
        mock_api.list_node.return_value = node_list

        with patch.object(client, "_get_api", return_value=mock_api):
            nodes = await client.get_nodes()

        assert nodes[0]["unschedulable"] is True

    async def test_error_handling_for_unreachable_cluster(self, client: K8sCoreClient) -> None:
        # Note 18: "Connection refused" is used as the error message because it simulates
        # the most common failure when a Kubernetes API server is unreachable (e.g., during
        # a control-plane upgrade). The `match="Connection"` argument to `pytest.raises`
        # is a regex pattern that only needs to match a substring of the exception message,
        # so it is resilient to minor message wording changes.
        mock_api = MagicMock()
        mock_api.list_node.side_effect = Exception("Connection refused")

        with patch.object(client, "_get_api", return_value=mock_api), pytest.raises(Exception, match="Connection"):
            await client.get_nodes()


class TestGetPods:
    async def test_returns_pods_all_namespaces(self, client: K8sCoreClient) -> None:
        # Note 19: `list_pod_for_all_namespaces` is the CoreV1Api method for fetching pods
        # cluster-wide. The test verifies both the count of returned pods and that the
        # correct API method was used (via `assert_called_once()`). If production code
        # accidentally called `list_namespaced_pod` without a namespace, the assertion on
        # `list_pod_for_all_namespaces` would catch the regression.
        mock_api = MagicMock()
        pod_list = MagicMock()
        pod_list.items = [_make_mock_pod(name="pod-1"), _make_mock_pod(name="pod-2")]
        mock_api.list_pod_for_all_namespaces.return_value = pod_list

        with patch.object(client, "_get_api", return_value=mock_api):
            pods = await client.get_pods()

        assert len(pods) == 2
        mock_api.list_pod_for_all_namespaces.assert_called_once()

    async def test_returns_pods_filtered_by_namespace(self, client: K8sCoreClient) -> None:
        # Note 20: When `namespace` is provided, the client should call `list_namespaced_pod`
        # instead of `list_pod_for_all_namespaces`. `assert_called_once_with("payments")`
        # is stricter than `assert_called_once()` — it verifies both that the method was
        # called exactly once AND that it received the correct argument. This ensures the
        # namespace is not dropped or defaulted.
        mock_api = MagicMock()
        pod_list = MagicMock()
        pod_list.items = [_make_mock_pod(name="pod-1", namespace="payments")]
        mock_api.list_namespaced_pod.return_value = pod_list

        with patch.object(client, "_get_api", return_value=mock_api):
            pods = await client.get_pods(namespace="payments")

        assert len(pods) == 1
        mock_api.list_namespaced_pod.assert_called_once_with("payments")

    async def test_error_handling(self, client: K8sCoreClient) -> None:
        # Note 21: Network timeouts are a common real-world failure mode for Kubernetes
        # API calls, especially in large clusters with many pods. The test ensures this
        # error propagates to the caller rather than being caught and swallowed silently,
        # which would result in an empty list being returned — a silent failure.
        mock_api = MagicMock()
        mock_api.list_pod_for_all_namespaces.side_effect = Exception("Timeout")

        with patch.object(client, "_get_api", return_value=mock_api), pytest.raises(Exception, match="Timeout"):
            await client.get_pods()

    async def test_container_status_waiting(self, client: K8sCoreClient) -> None:
        # Note 22: Container status objects in Kubernetes are discriminated unions:
        # a container is in exactly one of three states — waiting, running, or terminated.
        # The `cs.state.waiting.reason = "CrashLoopBackOff"` setup simulates the most
        # operationally significant waiting reason: a container that has crashed and is
        # being restarted by kubelet with exponential backoff. `restart_count = 5` is a
        # realistic value that would trigger alerting in a production environment.
        pod = _make_mock_pod(name="crash-pod")
        cs = MagicMock()
        cs.name = "app"
        cs.ready = False
        cs.restart_count = 5
        cs.state.waiting.reason = "CrashLoopBackOff"
        # Note 23: Setting `cs.state.terminated = None` and `cs.last_state.terminated = None`
        # is necessary because MagicMock attributes are truthy by default. If production
        # code checks `if cs.state.terminated:` before reading its fields, a MagicMock
        # (truthy) would incorrectly enter the terminated branch. Explicit None assignment
        # ensures the code follows the waiting branch, not the terminated branch.
        cs.state.terminated = None
        cs.last_state.terminated = None
        pod.status.container_statuses = [cs]
        mock_api = MagicMock()
        pod_list = MagicMock()
        pod_list.items = [pod]
        mock_api.list_pod_for_all_namespaces.return_value = pod_list

        with patch.object(client, "_get_api", return_value=mock_api):
            pods = await client.get_pods()

        # Note 24: The assertion checks the exact structure of the serialized container
        # state dict. This is an integration-level assertion on the output shape, ensuring
        # the production serialization code maps the mock object fields into the expected
        # dict keys. If the production code changes the output key from "waiting" to
        # "wait_state", this assertion catches the breaking change.
        assert pods[0]["container_statuses"][0]["state"] == {"waiting": {"reason": "CrashLoopBackOff"}}

    async def test_container_status_terminated(self, client: K8sCoreClient) -> None:
        # Note 25: exit_code = 0 indicates a clean (successful) termination. This is
        # the typical state for a batch job container or an init container that completed
        # successfully. Testing exit_code = 0 specifically confirms the client does not
        # treat zero as falsy and omit it from the output dict.
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
        # Note 26: `last_state.terminated` captures the previous termination state of a
        # container. This is critical for diagnosing OOMKilled containers: the current
        # state might be "Running" (after restart), while `last_state` reveals it was
        # killed by the OOM killer. exit_code = 137 is the standard Linux exit code for
        # SIGKILL (128 + 9), which is what the kernel sends when killing an OOM process.
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
        # Note 27: Kubernetes field selectors filter resources server-side before the
        # response is returned. `status.phase=Pending` is a common selector used to find
        # pods stuck in Pending state (e.g., due to resource constraints or missing PVCs).
        # `assert_called_once_with(field_selector="status.phase=Pending")` verifies the
        # selector is forwarded as a keyword argument, not discarded or rewritten by the
        # client layer. This is a "pass-through" contract test.
        mock_api = MagicMock()
        pod_list = MagicMock()
        pod_list.items = []
        mock_api.list_pod_for_all_namespaces.return_value = pod_list

        with patch.object(client, "_get_api", return_value=mock_api):
            await client.get_pods(field_selector="status.phase=Pending")

        mock_api.list_pod_for_all_namespaces.assert_called_once_with(field_selector="status.phase=Pending")
