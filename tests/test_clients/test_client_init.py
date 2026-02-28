# Note 1: This module focuses exclusively on the initialization contract of every client
# class — specifically the lazy-loading pattern. Isolating initialization tests from
# behavioral tests (e.g., "what does get_nodes() return?") keeps each test file focused
# on a single responsibility and makes failures immediately actionable.
"""Tests for client initialization and lazy API loading."""

# Note 2: `from __future__ import annotations` is placed at the very top of the file
# (after the module docstring) because it must appear before any other non-docstring,
# non-comment code. It activates PEP 563 annotation evaluation for the entire module,
# allowing forward references in type hints without runtime errors.
from __future__ import annotations

# Note 3: Both MagicMock and patch are imported from unittest.mock. MagicMock creates
# stand-in objects with auto-generated attributes, while patch is a context manager /
# decorator that temporarily replaces a named object in a module's namespace for the
# duration of a test. Together they allow tests to exercise code paths that would
# normally touch network or filesystem resources.
from unittest.mock import MagicMock, patch

# Note 4: All five client classes are imported so their initialization behavior can be
# tested uniformly. Importing directly from the production modules (not from a test
# helper) ensures the tests exercise the real class constructors.
from platform_mcp_server.clients.azure_aks import AzureAksClient
from platform_mcp_server.clients.k8s_core import K8sCoreClient
from platform_mcp_server.clients.k8s_events import K8sEventsClient
from platform_mcp_server.clients.k8s_metrics import K8sMetricsClient
from platform_mcp_server.clients.k8s_policy import K8sPolicyClient

# Note 5: CLUSTER_MAP is the application's configuration registry. Using a real entry
# from CLUSTER_MAP (rather than a hand-crafted dict) ensures the client constructors
# receive data in exactly the shape the production configuration provides. This catches
# issues where the constructor's expected fields diverge from the config schema.
from platform_mcp_server.config import CLUSTER_MAP


# Note 6: Test classes are used here as a namespace/grouping mechanism, not because
# pytest requires them. Grouping all K8sCoreClient initialization tests inside
# TestK8sCoreClientInit makes it easy to run just those tests with
# `pytest -k TestK8sCoreClientInit` and keeps the test report output readable.
class TestK8sCoreClientInit:
    def test_lazy_api_creation(self) -> None:
        # Note 7: "Lazy initialization" (also called "lazy loading") means the expensive
        # resource — here the Kubernetes API client — is not created when the object is
        # constructed, but only on first use. Asserting that `_api is None` right after
        # construction verifies this contract: no network calls or credential lookups
        # happen at import time or object creation time.
        config = CLUSTER_MAP["prod-eastus"]
        client = K8sCoreClient(config)
        assert client._api is None

    def test_get_api_creates_once(self) -> None:
        # Note 8: `patch` is used as a context manager here, replacing the real
        # `load_k8s_api_client` function with a MagicMock for the duration of the
        # `with` block. The string argument to patch must be the dotted path where the
        # name is *used*, not where it is *defined*. This is a common source of bugs:
        # patching the wrong module namespace means the real function still runs.
        config = CLUSTER_MAP["prod-eastus"]
        client = K8sCoreClient(config)
        with patch("platform_mcp_server.clients.k8s_core.load_k8s_api_client") as mock_load:
            # Note 9: Setting `return_value` on a mock controls what is returned when
            # the mock is called like a function. Using `MagicMock()` as the return
            # value gives us a distinct, trackable object we can later assert on with
            # the `is` identity check — which is stricter than `==` equality.
            mock_load.return_value = MagicMock()
            api1 = client._get_api()
            api2 = client._get_api()
        # Note 10: `api1 is api2` uses Python's identity operator rather than equality.
        # This confirms the client caches the same object instance rather than creating
        # a new API object on every call — a critical performance property for clients
        # that may be called repeatedly within a single request-handling cycle.
        assert api1 is api2
        # Note 11: `assert_called_once()` is a built-in MagicMock assertion method that
        # fails if the mock was called zero times or more than one time. This is the key
        # assertion for the singleton/cache pattern: even though `_get_api()` was invoked
        # twice, the underlying loader should have been called exactly once.
        mock_load.assert_called_once()


class TestK8sEventsClientInit:
    def test_lazy_api_creation(self) -> None:
        # Note 12: This test mirrors the pattern in TestK8sCoreClientInit. Repeating the
        # same structural test for every client class might appear redundant, but each
        # client is an independent class with its own `__init__`. A bug in one client's
        # constructor would not be caught by another client's test, so the repetition is
        # intentional and necessary.
        config = CLUSTER_MAP["prod-eastus"]
        client = K8sEventsClient(config)
        assert client._api is None

    def test_get_api_creates_once(self) -> None:
        # Note 13: Notice the patch target changes to `platform_mcp_server.clients.k8s_events`
        # to match where K8sEventsClient imports `load_k8s_api_client`. Even if two
        # clients import the same function, each must be patched in its own module's
        # namespace; patching k8s_core's import would have no effect on k8s_events code.
        config = CLUSTER_MAP["prod-eastus"]
        client = K8sEventsClient(config)
        with patch("platform_mcp_server.clients.k8s_events.load_k8s_api_client") as mock_load:
            mock_load.return_value = MagicMock()
            api1 = client._get_api()
            api2 = client._get_api()
        assert api1 is api2
        mock_load.assert_called_once()


class TestK8sMetricsClientInit:
    def test_lazy_api_creation(self) -> None:
        # Note 14: K8sMetricsClient accesses the Kubernetes Metrics API (metrics.k8s.io),
        # which is a custom API group rather than the core API. The lazy initialization
        # pattern is still tested the same way because the _api attribute convention is
        # shared across all clients by design.
        config = CLUSTER_MAP["prod-eastus"]
        client = K8sMetricsClient(config)
        assert client._api is None

    def test_get_api_creates_once(self) -> None:
        config = CLUSTER_MAP["prod-eastus"]
        client = K8sMetricsClient(config)
        with patch("platform_mcp_server.clients.k8s_metrics.load_k8s_api_client") as mock_load:
            mock_load.return_value = MagicMock()
            api1 = client._get_api()
            api2 = client._get_api()
        assert api1 is api2
        mock_load.assert_called_once()


class TestK8sPolicyClientInit:
    def test_lazy_api_creation(self) -> None:
        # Note 15: K8sPolicyClient works with the policy/v1 API group (PodDisruptionBudgets).
        # Testing _api is None at construction ensures that even clients wrapping
        # specialized API groups defer connection setup until the first operation.
        config = CLUSTER_MAP["prod-eastus"]
        client = K8sPolicyClient(config)
        assert client._api is None

    def test_get_api_creates_once(self) -> None:
        config = CLUSTER_MAP["prod-eastus"]
        client = K8sPolicyClient(config)
        with patch("platform_mcp_server.clients.k8s_policy.load_k8s_api_client") as mock_load:
            mock_load.return_value = MagicMock()
            api1 = client._get_api()
            api2 = client._get_api()
        assert api1 is api2
        mock_load.assert_called_once()


# Note 16: AzureAksClient is structurally different from the Kubernetes clients because
# it requires three separate lazy-initialized objects: a credential, a container service
# client, and a monitor client. Each maps to a different Azure SDK client class, so the
# tests must verify all three independently rather than just a single `_api` attribute.
class TestAzureAksClientInit:
    def test_lazy_credential_creation(self) -> None:
        # Note 17: Asserting all three lazy attributes are None at construction time
        # in a single test keeps the "no side effects at construction" story self-contained.
        # If we split this into three separate tests, a single constructor bug would
        # generate three failures, obscuring the root cause. One test, one assertion group.
        config = CLUSTER_MAP["prod-eastus"]
        client = AzureAksClient(config)
        assert client._credential is None
        assert client._container_client is None
        assert client._monitor_client is None

    def test_get_credential_creates_once(self) -> None:
        # Note 18: DefaultAzureCredential is Azure's credential chain class. It tries
        # environment variables, managed identity, Azure CLI, and other sources in order.
        # Patching it here prevents any actual credential lookup from running during
        # the test, which would either fail in a CI environment or expose credentials
        # in a local environment.
        config = CLUSTER_MAP["prod-eastus"]
        client = AzureAksClient(config)
        with patch("platform_mcp_server.clients.azure_aks.DefaultAzureCredential") as mock_cred:
            mock_cred.return_value = MagicMock()
            cred1 = client._get_credential()
            cred2 = client._get_credential()
        assert cred1 is cred2
        mock_cred.assert_called_once()

    def test_get_container_client_creates_once(self) -> None:
        # Note 19: Python 3.10+ allows multiple context managers inside a single `with`
        # statement using parentheses. Both DefaultAzureCredential and ContainerServiceClient
        # are patched together because `_get_container_client` internally calls
        # `_get_credential()`, which would invoke DefaultAzureCredential if not patched.
        # Patching both in the same block avoids an exception from the un-patched code path.
        config = CLUSTER_MAP["prod-eastus"]
        client = AzureAksClient(config)
        with (
            patch("platform_mcp_server.clients.azure_aks.DefaultAzureCredential"),
            patch("platform_mcp_server.clients.azure_aks.ContainerServiceClient") as mock_cs,
        ):
            mock_cs.return_value = MagicMock()
            c1 = client._get_container_client()
            c2 = client._get_container_client()
        assert c1 is c2
        mock_cs.assert_called_once()

    def test_get_monitor_client_creates_once(self) -> None:
        # Note 20: The monitor client is patched separately from the container client
        # because they are independent lazy singletons. Verifying each one individually
        # prevents a situation where a shared caching bug affects only one of the two
        # clients (e.g., if the code accidentally returned `_container_client` instead
        # of `_monitor_client` due to a copy-paste error).
        config = CLUSTER_MAP["prod-eastus"]
        client = AzureAksClient(config)
        with (
            patch("platform_mcp_server.clients.azure_aks.DefaultAzureCredential"),
            patch("platform_mcp_server.clients.azure_aks.MonitorManagementClient") as mock_mon,
        ):
            mock_mon.return_value = MagicMock()
            m1 = client._get_monitor_client()
            m2 = client._get_monitor_client()
        assert m1 is m2
        mock_mon.assert_called_once()
