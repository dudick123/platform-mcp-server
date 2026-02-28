# Note 1: This file tests K8sMetricsClient, which retrieves real-time CPU and memory
# usage data from the Kubernetes Metrics API (metrics.k8s.io). Unlike the core API tests,
# metrics data is returned as plain dicts (from JSON) rather than typed SDK objects,
# so the mock setup uses plain Python dicts instead of deeply nested MagicMock chains.
"""Tests for K8sMetricsClient: node metrics retrieval, graceful degradation."""

# Note 2: `from __future__ import annotations` is included as a project-wide convention
# even though this file has simple type hints that would not require it. Keeping it
# consistent across all test files prevents confusion about which files need it and
# ensures annotations behave the same way throughout the codebase.
from __future__ import annotations

# Note 3: Only MagicMock and patch are needed here because the metrics API returns
# plain dict data (JSON-deserialized), not complex SDK objects. There are no nested
# mock attribute chains — the mock is configured to return a standard Python dict
# that mirrors the real metrics.k8s.io API response structure.
from unittest.mock import MagicMock, patch

import pytest

from platform_mcp_server.clients.k8s_metrics import K8sMetricsClient
from platform_mcp_server.config import CLUSTER_MAP


# Note 4: Unlike the other test files, there is no `_make_mock_*` helper function here
# because the metrics API response is a plain dict rather than an SDK object. Plain dicts
# are constructed inline in each test, which is acceptable because they are small and
# their structure is immediately readable without a factory abstraction.
@pytest.fixture
def client() -> K8sMetricsClient:
    # Note 5: "prod-eastus" is used as the cluster config key in every test file.
    # This is the canonical production cluster in the CLUSTER_MAP and provides realistic
    # config values (resource group, subscription ID, etc.) that the client constructor
    # might validate or store. Using a well-known key also serves as documentation:
    # readers know which environment these tests are conceptually targeting.
    return K8sMetricsClient(CLUSTER_MAP["prod-eastus"])


class TestGetNodeMetrics:
    async def test_returns_metrics_for_all_nodes(self, client: K8sMetricsClient) -> None:
        mock_api = MagicMock()
        # Note 6: The mock return value mirrors the exact JSON structure of a real
        # metrics.k8s.io/v1beta1 NodeMetrics list response: a top-level dict with an
        # "items" key containing a list of node metric objects. Each item has "metadata"
        # (with "name") and "usage" (with "cpu" and "memory"). Matching this structure
        # ensures the production parsing code can be tested without network access.
        mock_api.list_cluster_custom_object.return_value = {
            "items": [
                {"metadata": {"name": "node-1"}, "usage": {"cpu": "2500m", "memory": "8Gi"}},
                {"metadata": {"name": "node-2"}, "usage": {"cpu": "1200m", "memory": "4Gi"}},
            ]
        }

        # Note 7: `patch.object` is used instead of `patch` (module-level) because we
        # have a direct reference to the `client` instance. This is cleaner than the
        # string-based `patch("platform_mcp_server.clients.k8s_metrics.load_k8s_api_client")`
        # approach used in the init tests — it patches the already-resolved method on the
        # object rather than intercepting the import-time binding.
        with patch.object(client, "_get_api", return_value=mock_api):
            metrics = await client.get_node_metrics()

        assert len(metrics) == 2
        assert metrics[0]["name"] == "node-1"
        # Note 8: CPU usage is expressed in millicores (the "m" suffix). "2500m" means
        # 2.5 CPU cores. Memory "8Gi" means 8 gibibytes. These values are passed through
        # as strings from the Kubernetes API — the client does not parse or convert them,
        # which is why the assertion checks the exact string value rather than a numeric
        # conversion. The test documents this pass-through behavior implicitly.
        assert metrics[0]["cpu_usage"] == "2500m"
        assert metrics[0]["memory_usage"] == "8Gi"

    async def test_metrics_server_unavailable_raises(self, client: K8sMetricsClient) -> None:
        # Note 9: The metrics-server is an optional add-on that must be separately
        # deployed into the cluster. If it is not running, the metrics.k8s.io API group
        # returns a 404 or an error that includes "metrics-server" in the message. Testing
        # that this error propagates ensures callers can distinguish "no metrics" from
        # "empty metrics" and display an appropriate diagnostic message.
        mock_api = MagicMock()
        mock_api.list_cluster_custom_object.side_effect = Exception("metrics-server not available")

        with patch.object(client, "_get_api", return_value=mock_api), pytest.raises(Exception, match="metrics-server"):
            await client.get_node_metrics()

    async def test_empty_items_returns_empty_list(self, client: K8sMetricsClient) -> None:
        # Note 10: An empty "items" list is a valid response from the Kubernetes API when
        # no nodes match the query (e.g., a freshly created cluster with no ready nodes).
        # The test asserts that `metrics == []` (a list, not None) to confirm the client
        # returns an empty list rather than raising a KeyError or returning the dict directly.
        mock_api = MagicMock()
        mock_api.list_cluster_custom_object.return_value = {"items": []}

        with patch.object(client, "_get_api", return_value=mock_api):
            metrics = await client.get_node_metrics()

        assert metrics == []

    async def test_calls_correct_api_group(self, client: K8sMetricsClient) -> None:
        # Note 11: `list_cluster_custom_object` is the CustomObjectsApi method for fetching
        # resources from custom API groups. The three arguments — group, version, and plural —
        # must match the registered metrics.k8s.io API exactly; any typo would result in
        # a 404 from the API server. This test acts as a contract test that the client
        # always uses the correct API group coordinates, catching regressions from copy-paste
        # errors where a different group (e.g., "apps") or version (e.g., "v1") might be used.
        mock_api = MagicMock()
        mock_api.list_cluster_custom_object.return_value = {"items": []}

        with patch.object(client, "_get_api", return_value=mock_api):
            await client.get_node_metrics()

        # Note 12: `assert_called_once_with` with keyword arguments verifies both the number
        # of calls (exactly one) and the exact keyword argument values passed. The metrics
        # API uses group="metrics.k8s.io", version="v1beta1", and plural="nodes". Using
        # keyword arguments in the assertion rather than positional arguments documents
        # the intent more clearly and matches how the production code likely calls the method.
        mock_api.list_cluster_custom_object.assert_called_once_with(
            group="metrics.k8s.io",
            version="v1beta1",
            plural="nodes",
        )
