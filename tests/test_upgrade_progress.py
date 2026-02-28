"""Tests for get_upgrade_progress tool handler."""

# Note 1: `from __future__ import annotations` enables PEP 563 postponed
# evaluation of annotations. This allows the newer `int | None` and `dict | None`
# union syntax in type hints on Python 3.9 and below, where those forms would
# otherwise raise a `TypeError` at class-definition time. It is a zero-cost
# import that improves forward-compatibility.
from __future__ import annotations

# Note 2: `AsyncMock` handles coroutine patching correctly. When the handler
# under test awaits a client method (e.g., `await mock_aks.get_cluster_info()`),
# `AsyncMock` automatically returns an awaitable that resolves to `.return_value`.
# A plain `MagicMock` is NOT awaitable, so using it for async methods would
# cause the test to fail with a confusing `TypeError` inside the handler rather
# than a clear mock-configuration error. `patch` is the standard context-manager
# mechanism for replacing module-level symbols during a test.
from unittest.mock import AsyncMock, patch

# Note 3: Only the single handler function is imported, keeping the import
# surface minimal and making it immediately apparent which callable every test
# in this file is exercising. This also makes refactoring easier: if the
# function is moved or renamed, only this line needs updating.
from platform_mcp_server.tools.upgrade_progress import get_upgrade_progress_handler


# Note 4: `_make_pool_info` is a factory function (private by convention,
# indicated by the leading underscore) that constructs a fake AKS node-pool
# info dict with sensible defaults. Using a factory instead of inline dicts
# keeps each test concise — a test only specifies the fields that are
# meaningful to its scenario — and centralises the data shape so that adding
# a new required field only requires one change.
def _make_pool_info(
    name: str = "userpool",
    # Note 5: Choosing "1.29.8" and "1.30.0" as the default version pair
    # reflects a realistic minor-version upgrade. Using two adjacent minor
    # versions (not patch versions) is intentional: AKS only supports
    # minor-version upgrades (not skip-level upgrades) so the test data mirrors
    # real-world constraints and exercises the handler's version-comparison logic
    # with inputs that would actually occur in production.
    current_version: str = "1.29.8",
    target_version: str = "1.30.0",
    # Note 6: `provisioning_state="Upgrading"` is the default because most tests
    # in this file are testing the upgrade-in-progress code path. Tests that
    # need to model a finished or idle cluster explicitly pass
    # `provisioning_state="Succeeded"`. Starting from the more interesting state
    # reduces the number of overrides needed in each test body.
    provisioning_state: str = "Upgrading",
) -> dict:
    return {
        "name": name,
        "vm_size": "Standard_DS2_v2",
        "count": 5,
        "min_count": 3,
        "max_count": 10,
        "current_version": current_version,
        "target_version": target_version,
        "provisioning_state": provisioning_state,
        "power_state": "Running",
        "os_type": "Linux",
        "mode": "User",
    }


# Note 7: `_make_node` constructs a fake Kubernetes node dict. The `version`
# field uses the "v1.29.8" format (with the "v" prefix) because that is the
# exact format returned by the Kubernetes API (`kubectl get node` shows
# `v1.29.8`). If the handler strips the "v" before comparing to the pool's
# `current_version`/`target_version`, using the real format here would catch
# a regression in that normalisation logic.
def _make_node(
    name: str,
    pool: str = "userpool",
    version: str = "v1.29.8",
    unschedulable: bool = False,
) -> dict:
    return {
        "name": name,
        "pool": pool,
        "version": version,
        "unschedulable": unschedulable,
        # Note 8: `allocatable_cpu` in millicores ("4000m" = 4 vCPUs) and
        # `allocatable_memory` in binary gibibytes ("16Gi") mirror the exact
        # string format produced by the Kubernetes API. This ensures any handler
        # code that parses these strings (e.g., for resource-pressure checks) is
        # exercised with realistic inputs rather than simplified integers.
        "allocatable_cpu": "4000m",
        "allocatable_memory": "16Gi",
        # Note 9: `conditions: {"Ready": "True"}` uses the string "True", not
        # the boolean True. The Kubernetes API serialises all condition statuses
        # as strings ("True", "False", "Unknown"). Tests that accidentally use
        # the boolean would pass for loose equality checks but fail for strict
        # string comparisons, creating false confidence. Using the string here
        # ensures the handler is tested against the real API contract.
        "conditions": {"Ready": "True"},
        "labels": {"agentpool": pool},
    }


# Note 10: `_make_event` builds a Kubernetes event dict for a node. Events are
# the primary signal the handler uses to determine what stage of the upgrade
# pipeline a node is in (e.g., "NodeUpgrade" means draining has started,
# "NodeReady" means the node has rejoined the cluster after upgrading). The
# `timestamp` parameter uses an ISO-8601 string with a UTC offset, matching
# the format returned by the Kubernetes Events API.
def _make_event(node_name: str, reason: str, timestamp: str = "2026-02-28T12:00:00+00:00") -> dict:
    return {
        "reason": reason,
        "node_name": node_name,
        # Note 11: The `message` field is templated from `reason` and `node_name`
        # to produce a human-readable string resembling what Kubernetes would
        # emit. While the handler may not use `message` for logic, having a
        # non-empty realistic value ensures tests do not accidentally pass
        # because the handler short-circuits on an empty string.
        "message": f"{reason} event for {node_name}",
        "timestamp": timestamp,
        "count": 1,
    }


# Note 12: All tests live in a single class, grouping them under the handler
# they test. pytest discovers `async def test_*` methods in classes without
# `@pytest.mark.asyncio` when `asyncio_mode = "auto"` is configured in
# pyproject.toml. The class acts as a namespace and organises output in the
# pytest report, making it easy to see all upgrade-progress tests at a glance.
class TestGetUpgradeProgress:
    async def test_no_upgrade_in_progress(self) -> None:
        mock_aks = AsyncMock()
        # Note 13: Setting `provisioning_state="Succeeded"` and making
        # `current_version == target_version` ("1.29.8" == "1.29.8") models a
        # cluster that is fully idle. The handler should detect both signals
        # and return `upgrade_in_progress=False`. Testing the combination (not
        # just one signal) reflects the handler's likely logic: it may use
        # either or both fields to determine upgrade state.
        mock_aks.get_cluster_info.return_value = {
            "control_plane_version": "1.29.8",
            "provisioning_state": "Succeeded",
            "node_pools": [
                _make_pool_info(provisioning_state="Succeeded", current_version="1.29.8", target_version="1.29.8")
            ],
            "fqdn": "test.eastus.azmk8s.io",
        }
        # Note 14: Using separate `AsyncMock()` instances for each client
        # (mock_core, mock_events, mock_policy) rather than a single shared mock
        # ensures that each client's call log is independent. This avoids
        # accidental cross-contamination: an assertion on `mock_core.get_nodes`
        # will only reflect calls to that specific client, not calls to any other.
        mock_core = AsyncMock()
        mock_events = AsyncMock()
        mock_policy = AsyncMock()

        with (
            patch("platform_mcp_server.tools.upgrade_progress.AzureAksClient", return_value=mock_aks),
            patch("platform_mcp_server.tools.upgrade_progress.K8sCoreClient", return_value=mock_core),
            patch("platform_mcp_server.tools.upgrade_progress.K8sEventsClient", return_value=mock_events),
            patch("platform_mcp_server.tools.upgrade_progress.K8sPolicyClient", return_value=mock_policy),
        ):
            result = await get_upgrade_progress_handler("prod-eastus")

        # Note 15: `result.upgrade_in_progress is False` uses `is False` (not
        # `== False`) because `is` checks identity, ensuring the result is the
        # Python singleton `False` and not a truthy/falsy value like `0` or
        # `None`. This is a stricter assertion that enforces the handler returns
        # a proper boolean.
        assert result.upgrade_in_progress is False

    async def test_node_classified_as_upgraded(self) -> None:
        mock_aks = AsyncMock()
        # Note 16: `control_plane_version="1.30.0"` (the target version) signals
        # that the Kubernetes control plane has already been upgraded. The node
        # pool still has `provisioning_state="Upgrading"` (from the factory
        # default), indicating that the data-plane upgrade is ongoing. This
        # combination is realistic: AKS upgrades the control plane first, then
        # rolls through node pools one by one.
        mock_aks.get_cluster_info.return_value = {
            "control_plane_version": "1.30.0",
            "provisioning_state": "Succeeded",
            "node_pools": [_make_pool_info()],
            "fqdn": "test.eastus.azmk8s.io",
        }
        mock_core = AsyncMock()
        # Note 17: `version="v1.30.0"` on the node means the node has already
        # been upgraded to the target version. The handler should classify this
        # node as "upgraded" because its version matches the target version,
        # it is schedulable (unschedulable=False by default), and its events
        # include both "NodeUpgrade" and "NodeReady" (a complete cycle).
        mock_core.get_nodes.return_value = [_make_node("node-1", version="v1.30.0")]
        mock_events = AsyncMock()
        mock_events.get_node_events.return_value = [
            _make_event("node-1", "NodeUpgrade", "2026-02-28T11:50:00+00:00"),
            _make_event("node-1", "NodeReady", "2026-02-28T11:55:00+00:00"),
        ]
        mock_policy = AsyncMock()
        mock_policy.get_pdbs.return_value = []
        mock_policy.evaluate_pdb_satisfiability.return_value = []

        with (
            patch("platform_mcp_server.tools.upgrade_progress.AzureAksClient", return_value=mock_aks),
            patch("platform_mcp_server.tools.upgrade_progress.K8sCoreClient", return_value=mock_core),
            patch("platform_mcp_server.tools.upgrade_progress.K8sEventsClient", return_value=mock_events),
            patch("platform_mcp_server.tools.upgrade_progress.K8sPolicyClient", return_value=mock_policy),
        ):
            result = await get_upgrade_progress_handler("prod-eastus")

        assert result.upgrade_in_progress is True
        assert len(result.nodes) == 1
        # Note 18: `result.nodes[0].state == "upgraded"` asserts the exact
        # string label that the handler assigns to a node that has completed its
        # upgrade cycle. By testing the string value directly, this test acts as
        # a contract: any rename of the state constant in the handler would fail
        # this test, prompting a deliberate update to any downstream code that
        # interprets the state string (e.g., UI rendering, alerting rules).
        assert result.nodes[0].state == "upgraded"

    async def test_node_classified_as_cordoned(self) -> None:
        mock_aks = AsyncMock()
        mock_aks.get_cluster_info.return_value = {
            "control_plane_version": "1.30.0",
            "provisioning_state": "Succeeded",
            "node_pools": [_make_pool_info()],
            "fqdn": "test.eastus.azmk8s.io",
        }
        mock_core = AsyncMock()
        # Note 19: `version="v1.29.8"` (the old version) combined with
        # `unschedulable=True` models a node that has been cordoned by the
        # upgrade process but has not yet been drained and replaced. This is
        # the "in-flight" state: the node is pulled from the scheduler's pool
        # but its workloads have not yet migrated and its kubelet has not yet
        # been upgraded.
        mock_core.get_nodes.return_value = [_make_node("node-1", version="v1.29.8", unschedulable=True)]
        mock_events = AsyncMock()
        # Note 20: Returning an empty event list with `# No NodeUpgrade event yet`
        # documents a subtle state-machine detail: the node is already cordoned
        # (unschedulable=True) but has not yet emitted a "NodeUpgrade" event.
        # This can happen in the brief window between when the AKS upgrade
        # controller cordons the node and when it begins the actual kubelet
        # upgrade. The handler must use `unschedulable` as the primary signal,
        # not the presence of events.
        mock_events.get_node_events.return_value = []  # No NodeUpgrade event yet
        mock_policy = AsyncMock()
        mock_policy.get_pdbs.return_value = []
        mock_policy.evaluate_pdb_satisfiability.return_value = []

        with (
            patch("platform_mcp_server.tools.upgrade_progress.AzureAksClient", return_value=mock_aks),
            patch("platform_mcp_server.tools.upgrade_progress.K8sCoreClient", return_value=mock_core),
            patch("platform_mcp_server.tools.upgrade_progress.K8sEventsClient", return_value=mock_events),
            patch("platform_mcp_server.tools.upgrade_progress.K8sPolicyClient", return_value=mock_policy),
        ):
            result = await get_upgrade_progress_handler("prod-eastus")

        assert result.nodes[0].state == "cordoned"

    async def test_node_classified_as_pending(self) -> None:
        mock_aks = AsyncMock()
        mock_aks.get_cluster_info.return_value = {
            "control_plane_version": "1.30.0",
            "provisioning_state": "Succeeded",
            "node_pools": [_make_pool_info()],
            "fqdn": "test.eastus.azmk8s.io",
        }
        mock_core = AsyncMock()
        # Note 21: `version="v1.29.8"` (old) with `unschedulable=False` models a
        # node that has not yet been touched by the upgrade process. It is still
        # accepting workloads and has the old kubelet version. This is the "pending"
        # state: the node is queued for upgrade but the upgrade controller has not
        # yet begun processing it. There are no events either, which confirms the
        # node is truly untouched.
        mock_core.get_nodes.return_value = [_make_node("node-1", version="v1.29.8", unschedulable=False)]
        mock_events = AsyncMock()
        mock_events.get_node_events.return_value = []
        mock_policy = AsyncMock()
        mock_policy.get_pdbs.return_value = []
        mock_policy.evaluate_pdb_satisfiability.return_value = []

        with (
            patch("platform_mcp_server.tools.upgrade_progress.AzureAksClient", return_value=mock_aks),
            patch("platform_mcp_server.tools.upgrade_progress.K8sCoreClient", return_value=mock_core),
            patch("platform_mcp_server.tools.upgrade_progress.K8sEventsClient", return_value=mock_events),
            patch("platform_mcp_server.tools.upgrade_progress.K8sPolicyClient", return_value=mock_policy),
        ):
            result = await get_upgrade_progress_handler("prod-eastus")

        assert result.nodes[0].state == "pending"

    async def test_pdb_blocked_includes_reference(self) -> None:
        mock_aks = AsyncMock()
        mock_aks.get_cluster_info.return_value = {
            "control_plane_version": "1.30.0",
            "provisioning_state": "Succeeded",
            "node_pools": [_make_pool_info()],
            "fqdn": "test.eastus.azmk8s.io",
        }
        mock_core = AsyncMock()
        # Note 22: A cordoned node (`unschedulable=True`) with an old kubelet
        # version that has emitted a "NodeUpgrade" event but no "NodeReady" event
        # is the signature of a PDB-blocked upgrade. The node was cordoned and
        # the drain started, but the drain is stuck because a PDB is blocking the
        # eviction of its pods. The handler must synthesise information from node
        # state, events, and PDB evaluation to classify this as "pdb_blocked".
        mock_core.get_nodes.return_value = [_make_node("node-1", version="v1.29.8", unschedulable=True)]
        mock_events = AsyncMock()
        mock_events.get_node_events.return_value = [
            _make_event("node-1", "NodeUpgrade", "2026-02-28T11:50:00+00:00"),
        ]
        mock_policy = AsyncMock()
        # Note 23: The PDB returned by `get_pdbs` and the entry returned by
        # `evaluate_pdb_satisfiability` must be consistent (same name, namespace,
        # and block_reason). The handler is expected to join these two data sources
        # to determine which specific PDB is blocking the drain and to include its
        # name in the node's `blocking_pdb` field for actionable operator output.
        mock_policy.get_pdbs.return_value = [
            {
                "name": "block-pdb",
                "namespace": "ns1",
                "max_unavailable": 0,
                "disruptions_allowed": 0,
                "selector": {},
                "current_healthy": 3,
                "desired_healthy": 3,
                "expected_pods": 3,
            }
        ]
        mock_policy.evaluate_pdb_satisfiability.return_value = [
            {"name": "block-pdb", "namespace": "ns1", "block_reason": "maxUnavailable=0"}
        ]

        with (
            patch("platform_mcp_server.tools.upgrade_progress.AzureAksClient", return_value=mock_aks),
            patch("platform_mcp_server.tools.upgrade_progress.K8sCoreClient", return_value=mock_core),
            patch("platform_mcp_server.tools.upgrade_progress.K8sEventsClient", return_value=mock_events),
            patch("platform_mcp_server.tools.upgrade_progress.K8sPolicyClient", return_value=mock_policy),
        ):
            result = await get_upgrade_progress_handler("prod-eastus")

        # Note 24: Two assertions together verify both the classification and
        # the attribution. `state == "pdb_blocked"` confirms the node is in the
        # correct state bucket. `blocking_pdb == "block-pdb"` confirms the
        # handler populated the attribution field so that an operator knows
        # exactly which PDB to investigate, without having to re-run the
        # analysis manually.
        assert result.nodes[0].state == "pdb_blocked"
        assert result.nodes[0].blocking_pdb == "block-pdb"

    async def test_pod_transitions_with_pending_pods_on_cordoned_nodes(self) -> None:
        """Pods on cordoned nodes should appear in pod_transitions."""
        mock_aks = AsyncMock()
        mock_aks.get_cluster_info.return_value = {
            "control_plane_version": "1.30.0",
            "provisioning_state": "Succeeded",
            "node_pools": [_make_pool_info()],
            "fqdn": "test.eastus.azmk8s.io",
        }
        mock_core = AsyncMock()
        # Note 25: Two nodes are provided: node-1 is cordoned (unschedulable=True)
        # and node-2 is schedulable (unschedulable=False). This distinction is
        # essential for the pod-transitions feature: only pods that originated on
        # a cordoned node are considered "displaced" by the upgrade and should
        # appear in the transition summary. Pods on pending nodes (not yet touched)
        # are not yet displaced and must be excluded.
        mock_core.get_nodes.return_value = [
            _make_node("node-1", version="v1.29.8", unschedulable=True),
            _make_node("node-2", version="v1.29.8", unschedulable=False),
        ]
        # Note 26: Three pods are provided to exercise the categorisation logic:
        # - "web-abc": Pending/Unschedulable on node-1 → scheduling category
        # - "api-xyz": Failed/Error on node-1 → runtime category
        # - "healthy-pod": Running on node-2 → should be excluded (not displaced)
        # Using pods in different phases from different nodes tests the filtering
        # AND the categorisation in a single test, keeping the test count low
        # while covering multiple cases.
        mock_core.get_pods.return_value = [
            {
                "name": "web-abc",
                "namespace": "default",
                "phase": "Pending",
                "node_name": "node-1",
                "reason": "Unschedulable",
                "message": None,
                "container_statuses": [],
                "conditions": [],
            },
            {
                "name": "api-xyz",
                "namespace": "payments",
                "phase": "Failed",
                "node_name": "node-1",
                "reason": "Error",
                "message": None,
                "container_statuses": [],
                "conditions": [],
            },
            {
                "name": "healthy-pod",
                "namespace": "default",
                "phase": "Running",
                "node_name": "node-2",
                "reason": None,
                "message": None,
                "container_statuses": [],
                "conditions": [],
            },
        ]
        mock_events = AsyncMock()
        mock_events.get_node_events.return_value = []
        mock_policy = AsyncMock()
        mock_policy.get_pdbs.return_value = []
        mock_policy.evaluate_pdb_satisfiability.return_value = []

        with (
            patch("platform_mcp_server.tools.upgrade_progress.AzureAksClient", return_value=mock_aks),
            patch("platform_mcp_server.tools.upgrade_progress.K8sCoreClient", return_value=mock_core),
            patch("platform_mcp_server.tools.upgrade_progress.K8sEventsClient", return_value=mock_events),
            patch("platform_mcp_server.tools.upgrade_progress.K8sPolicyClient", return_value=mock_policy),
        ):
            result = await get_upgrade_progress_handler("prod-eastus")

        # Note 27: The block of assertions tests five distinct properties of the
        # pod_transitions summary object in one test. This is acceptable here
        # because all five properties are derived from the same set of three pods
        # — splitting into five separate tests would require duplicating all the
        # mock setup. The trade-off favours DRY over strict "one assertion per
        # test" purism.
        assert result.pod_transitions is not None
        assert result.pod_transitions.pending_count == 1
        assert result.pod_transitions.failed_count == 1
        assert result.pod_transitions.by_category.get("scheduling", 0) == 1
        assert result.pod_transitions.by_category.get("runtime", 0) == 1
        assert result.pod_transitions.total_affected == 2
        # Note 28: The sort-order assertion (`affected_pods[0].phase == "Failed"`)
        # verifies that the handler prioritises failed pods above pending pods in
        # the output list. This is a UX contract: operators should see the most
        # urgent problems (failures) first so they can act without scrolling.
        # Failed pods on a cordoned should come first
        assert result.pod_transitions.affected_pods[0].phase == "Failed"

    async def test_pod_transitions_empty_when_no_disrupted_pods(self) -> None:
        """When upgrade is active but no unhealthy pods, pod_transitions should be empty."""
        mock_aks = AsyncMock()
        mock_aks.get_cluster_info.return_value = {
            "control_plane_version": "1.30.0",
            "provisioning_state": "Succeeded",
            "node_pools": [_make_pool_info()],
            "fqdn": "test.eastus.azmk8s.io",
        }
        mock_core = AsyncMock()
        # Note 29: node-1 is cordoned but its only pod is Running. This models a
        # well-behaved upgrade where pods have already been evicted and
        # rescheduled successfully before the node snapshot was taken. The
        # handler should return a `pod_transitions` object (not None, because
        # an upgrade IS in progress) but with all counters at zero.
        mock_core.get_nodes.return_value = [_make_node("node-1", version="v1.29.8", unschedulable=True)]
        mock_core.get_pods.return_value = [
            {
                "name": "healthy-pod",
                "namespace": "default",
                "phase": "Running",
                "node_name": "node-1",
                "reason": None,
                "message": None,
                "container_statuses": [],
                "conditions": [],
            },
        ]
        mock_events = AsyncMock()
        mock_events.get_node_events.return_value = []
        mock_policy = AsyncMock()
        mock_policy.get_pdbs.return_value = []
        mock_policy.evaluate_pdb_satisfiability.return_value = []

        with (
            patch("platform_mcp_server.tools.upgrade_progress.AzureAksClient", return_value=mock_aks),
            patch("platform_mcp_server.tools.upgrade_progress.K8sCoreClient", return_value=mock_core),
            patch("platform_mcp_server.tools.upgrade_progress.K8sEventsClient", return_value=mock_events),
            patch("platform_mcp_server.tools.upgrade_progress.K8sPolicyClient", return_value=mock_policy),
        ):
            result = await get_upgrade_progress_handler("prod-eastus")

        # Note 30: Asserting `pod_transitions is not None` (even with zero counts)
        # tests an important distinction: an upgrade is in progress, so the
        # transitions object should exist and have well-defined counters, rather
        # than being absent (None). A None would indicate "not applicable",
        # whereas a zero-count object means "applicable and all clear".
        assert result.pod_transitions is not None
        assert result.pod_transitions.pending_count == 0
        assert result.pod_transitions.failed_count == 0
        assert result.pod_transitions.total_affected == 0

    async def test_pod_transitions_null_when_no_upgrade(self) -> None:
        """When no upgrade is in progress, pod_transitions should be null."""
        mock_aks = AsyncMock()
        # Note 31: `current_version == target_version` ("1.29.8" == "1.29.8")
        # and `provisioning_state="Succeeded"` together signal that no upgrade is
        # happening. In this state the handler should return `pod_transitions=None`
        # (not an empty transitions object) because the concept of upgrade-related
        # pod disruptions is not applicable — there is nothing to report.
        mock_aks.get_cluster_info.return_value = {
            "control_plane_version": "1.29.8",
            "provisioning_state": "Succeeded",
            "node_pools": [
                _make_pool_info(provisioning_state="Succeeded", current_version="1.29.8", target_version="1.29.8")
            ],
            "fqdn": "test.eastus.azmk8s.io",
        }
        mock_core = AsyncMock()
        mock_events = AsyncMock()
        mock_policy = AsyncMock()

        with (
            patch("platform_mcp_server.tools.upgrade_progress.AzureAksClient", return_value=mock_aks),
            patch("platform_mcp_server.tools.upgrade_progress.K8sCoreClient", return_value=mock_core),
            patch("platform_mcp_server.tools.upgrade_progress.K8sEventsClient", return_value=mock_events),
            patch("platform_mcp_server.tools.upgrade_progress.K8sPolicyClient", return_value=mock_policy),
        ):
            result = await get_upgrade_progress_handler("prod-eastus")

        # Note 32: `result.pod_transitions is None` uses identity (`is`) rather
        # than equality (`==`) because `None` is a singleton in Python. The `is`
        # check ensures the handler returned the actual None object, not a falsy
        # surrogate like an empty list or an empty transitions object whose
        # `__eq__` might evaluate to None.
        assert result.pod_transitions is None

    async def test_pod_transitions_excludes_pods_on_pending_nodes(self) -> None:
        """Pods on pending (not-yet-cordoned) nodes should not be counted."""
        mock_aks = AsyncMock()
        mock_aks.get_cluster_info.return_value = {
            "control_plane_version": "1.30.0",
            "provisioning_state": "Succeeded",
            "node_pools": [_make_pool_info()],
            "fqdn": "test.eastus.azmk8s.io",
        }
        mock_core = AsyncMock()
        mock_core.get_nodes.return_value = [
            # Note 33: Two nodes with identical pod phases (both have a Pending
            # pod) but different schedulability states are the key test data here.
            # node-1 is cordoned; node-2 is not. The test verifies that only the
            # pod on node-1 is counted. Without this test a buggy handler that
            # counts all Pending pods regardless of node state would pass the
            # simpler "pending pods on cordoned nodes" test above.
            _make_node("node-1", version="v1.29.8", unschedulable=True),  # cordoned
            _make_node("node-2", version="v1.29.8", unschedulable=False),  # pending
        ]
        mock_core.get_pods.return_value = [
            {
                "name": "pod-on-cordoned",
                "namespace": "default",
                "phase": "Pending",
                "node_name": "node-1",
                "reason": "Unschedulable",
                "message": None,
                "container_statuses": [],
                "conditions": [],
            },
            {
                "name": "pod-on-pending-node",
                "namespace": "default",
                "phase": "Pending",
                "node_name": "node-2",
                "reason": "Unschedulable",
                "message": None,
                "container_statuses": [],
                "conditions": [],
            },
        ]
        mock_events = AsyncMock()
        mock_events.get_node_events.return_value = []
        mock_policy = AsyncMock()
        mock_policy.get_pdbs.return_value = []
        mock_policy.evaluate_pdb_satisfiability.return_value = []

        with (
            patch("platform_mcp_server.tools.upgrade_progress.AzureAksClient", return_value=mock_aks),
            patch("platform_mcp_server.tools.upgrade_progress.K8sCoreClient", return_value=mock_core),
            patch("platform_mcp_server.tools.upgrade_progress.K8sEventsClient", return_value=mock_events),
            patch("platform_mcp_server.tools.upgrade_progress.K8sPolicyClient", return_value=mock_policy),
        ):
            result = await get_upgrade_progress_handler("prod-eastus")

        assert result.pod_transitions is not None
        # Only the pod on node-1 (cordoned) should be counted
        # Note 34: `total_affected == 1` (not 2) is the crucial assertion. If the
        # handler incorrectly includes pod-on-pending-node this assertion fails
        # with a clear count mismatch. The name assertion on `affected_pods[0]`
        # provides an additional signal about *which* pod was correctly included,
        # making the failure message immediately actionable.
        assert result.pod_transitions.total_affected == 1
        assert result.pod_transitions.affected_pods[0].name == "pod-on-cordoned"

    async def test_pod_transitions_cap_at_20(self) -> None:
        """Affected pods list should be capped at 20."""
        mock_aks = AsyncMock()
        mock_aks.get_cluster_info.return_value = {
            "control_plane_version": "1.30.0",
            "provisioning_state": "Succeeded",
            "node_pools": [_make_pool_info()],
            "fqdn": "test.eastus.azmk8s.io",
        }
        mock_core = AsyncMock()
        mock_core.get_nodes.return_value = [_make_node("node-1", version="v1.29.8", unschedulable=True)]
        # 25 pending pods on cordoned node
        # Note 35: A list comprehension generates 25 pod dicts (f"pod-{i}" for
        # i in range(25)) in a single expression, avoiding 25 lines of duplicated
        # dict literals. This is an idiomatic Python pattern for producing
        # parameterised test data at scale. The count of 25 is deliberately above
        # the cap of 20 to ensure the cap is actually triggered; using exactly 20
        # pods would not verify that the handler trims excess entries.
        mock_core.get_pods.return_value = [
            {
                "name": f"pod-{i}",
                "namespace": "default",
                "phase": "Pending",
                "node_name": "node-1",
                "reason": "Unschedulable",
                "message": None,
                "container_statuses": [],
                "conditions": [],
            }
            for i in range(25)
        ]
        mock_events = AsyncMock()
        mock_events.get_node_events.return_value = []
        mock_policy = AsyncMock()
        mock_policy.get_pdbs.return_value = []
        mock_policy.evaluate_pdb_satisfiability.return_value = []

        with (
            patch("platform_mcp_server.tools.upgrade_progress.AzureAksClient", return_value=mock_aks),
            patch("platform_mcp_server.tools.upgrade_progress.K8sCoreClient", return_value=mock_core),
            patch("platform_mcp_server.tools.upgrade_progress.K8sEventsClient", return_value=mock_events),
            patch("platform_mcp_server.tools.upgrade_progress.K8sPolicyClient", return_value=mock_policy),
        ):
            result = await get_upgrade_progress_handler("prod-eastus")

        assert result.pod_transitions is not None
        # Note 36: Three assertions test three different fields of the cap behaviour:
        # - `len(affected_pods) == 20`: the list is truncated to the display cap.
        # - `total_affected == 25`: the *total* count is NOT capped — it reflects
        #   the real number of disrupted pods, even if not all are listed.
        # - `pending_count == 25`: the category counter also reflects the real count.
        # This three-way check ensures that capping the list does not accidentally
        # also cap the aggregate counters, which would give operators a misleading
        # picture of the upgrade's impact.
        assert len(result.pod_transitions.affected_pods) == 20
        assert result.pod_transitions.total_affected == 25
        assert result.pod_transitions.pending_count == 25

    async def test_cluster_all_fan_out(self) -> None:
        mock_aks = AsyncMock()
        mock_aks.get_cluster_info.return_value = {
            "control_plane_version": "1.29.8",
            "provisioning_state": "Succeeded",
            "node_pools": [
                _make_pool_info(provisioning_state="Succeeded", current_version="1.29.8", target_version="1.29.8")
            ],
            "fqdn": "test.eastus.azmk8s.io",
        }
        mock_core = AsyncMock()
        mock_events = AsyncMock()
        mock_policy = AsyncMock()

        with (
            patch("platform_mcp_server.tools.upgrade_progress.AzureAksClient", return_value=mock_aks),
            patch("platform_mcp_server.tools.upgrade_progress.K8sCoreClient", return_value=mock_core),
            patch("platform_mcp_server.tools.upgrade_progress.K8sEventsClient", return_value=mock_events),
            patch("platform_mcp_server.tools.upgrade_progress.K8sPolicyClient", return_value=mock_policy),
        ):
            # Note 37: `get_upgrade_progress_all` is imported inside the `with`
            # block to guarantee the patches are already in place before the
            # module's top-level symbols are resolved. This prevents the classic
            # "mock applied after the reference was captured" problem, where a
            # module-level variable like `_AKS_CLIENT = AzureAksClient` would
            # hold the real class even after the patch is active if the import
            # happened before the `patch()` call.
            from platform_mcp_server.tools.upgrade_progress import get_upgrade_progress_all

            results = await get_upgrade_progress_all()

        # Note 38: `len(results) == 6` is a platform-registry contract assertion.
        # It encodes the expected number of managed clusters as a concrete number
        # in the test suite. If the cluster list grows or shrinks, this test fails
        # loudly with a count mismatch, which is far more informative than a
        # silent behaviour change where some clusters are silently skipped.
        assert len(results) == 6
