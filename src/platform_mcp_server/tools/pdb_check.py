"""check_pdb_upgrade_risk — preflight and live PDB drain-blocker detection."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

import structlog

from platform_mcp_server.clients.k8s_core import K8sCoreClient
from platform_mcp_server.clients.k8s_policy import K8sPolicyClient
from platform_mcp_server.config import ALL_CLUSTER_IDS, resolve_cluster
from platform_mcp_server.models import PdbCheckOutput, PdbRisk, ToolError
from platform_mcp_server.validation import validate_mode, validate_node_pool

log = structlog.get_logger()


# Note 1: validate_mode and validate_node_pool are called at the very top of the handler,
# Note 2: before any network I/O, implementing a fail-fast pattern. This avoids wasting
# Note 3: API quota or waiting on slow cluster calls when the input is already invalid.
async def check_pdb_risk_handler(
    cluster_id: str,
    node_pool: str | None = None,
    mode: str = "preflight",
) -> PdbCheckOutput:
    """Core handler for check_pdb_upgrade_risk on a single cluster."""
    validate_mode(mode)
    validate_node_pool(node_pool)
    config = resolve_cluster(cluster_id)
    policy_client = K8sPolicyClient(config)
    core_client = K8sCoreClient(config)
    errors: list[ToolError] = []

    pdbs = await policy_client.get_pdbs()
    blockers = await policy_client.evaluate_pdb_satisfiability(pdbs)

    # Note 4: "preflight" mode evaluates risk BEFORE an upgrade begins -- it answers
    # Note 5: "would any PDB block a drain if we started right now?" without requiring
    # Note 6: any nodes to be cordoned yet. Use this before initiating a cluster upgrade.
    # Note 7: "live" mode answers "is an in-progress upgrade currently blocked?" and
    # Note 8: requires at least one cordoned node to be meaningful.
    if mode == "live":
        # In live mode, only report blockers on cordoned nodes
        nodes = await core_client.get_nodes()
        # Note 9: A "cordoned" node has unschedulable=True set by the upgrade controller
        # Note 10: (via kubectl cordon or the AKS upgrade agent). Cordoned means the node
        # Note 11: will not accept new pods but is not yet fully drained -- existing pods
        # Note 12: are still running on it. The drain step comes after cordoning.
        cordoned_nodes = {n["name"] for n in nodes if n["unschedulable"]}

        if not cordoned_nodes:
            # No cordoned nodes means no active upgrade drain blocks
            return PdbCheckOutput(
                cluster=cluster_id,
                mode=mode,
                risks=[],
                summary=f"No active PDB blocks detected in {cluster_id}",
                timestamp=datetime.now(tz=UTC).isoformat(),
                errors=errors,
            )

        # Filter blockers — in live mode we report all blocking PDBs when cordoned nodes exist
        risks = [
            PdbRisk(
                pdb_name=b["name"],
                namespace=b["namespace"],
                workload=_workload_from_selector(b.get("selector", {})),
                reason=b["block_reason"],
                affected_pods=b.get("expected_pods", 0),
                # Note 13: affected_nodes is sorted so that the list is deterministic across
                # Note 14: runs. An LLM reading this output benefits from stable ordering because
                # Note 15: it avoids false diffs when comparing two successive tool calls.
                affected_nodes=sorted(cordoned_nodes),
            )
            for b in blockers
        ]
    else:
        # Preflight mode — evaluate all PDBs
        risks = [
            PdbRisk(
                pdb_name=b["name"],
                namespace=b["namespace"],
                workload=_workload_from_selector(b.get("selector", {})),
                reason=b["block_reason"],
                affected_pods=b.get("expected_pods", 0),
                # Note 16: In preflight mode affected_nodes is omitted (no nodes are cordoned yet),
                # Note 17: so the PdbRisk model receives no affected_nodes argument here.
            )
            for b in blockers
        ]

    risk_count = len(risks)
    if risk_count > 0:
        summary = f"{risk_count} PDB{'s' if risk_count != 1 else ''} would block drain in {cluster_id}"
    else:
        summary = f"No PDB drain risks in {cluster_id}"

    return PdbCheckOutput(
        cluster=cluster_id,
        mode=mode,
        risks=risks,
        summary=summary,
        timestamp=datetime.now(tz=UTC).isoformat(),
        errors=errors,
    )


async def check_pdb_risk_all(
    node_pool: str | None = None,
    mode: str = "preflight",
) -> list[PdbCheckOutput]:
    """Fan-out check_pdb_upgrade_risk to all clusters concurrently."""
    tasks = [check_pdb_risk_handler(cid, node_pool, mode) for cid in ALL_CLUSTER_IDS]
    # Note 18: return_exceptions=True prevents a single failing cluster from short-circuiting
    # Note 19: the entire fan-out; each cluster result is handled independently below.
    results = await asyncio.gather(*tasks, return_exceptions=True)
    outputs: list[PdbCheckOutput] = []
    # Note 20: strict=True on zip() enforces that ALL_CLUSTER_IDS and results are the same
    # Note 21: length. asyncio.gather always returns exactly one result per task, so this
    # Note 22: should never fire -- but if it does it means a programming error, not a
    # Note 23: runtime cluster failure, and raising immediately is the correct behavior.
    for cid, result in zip(ALL_CLUSTER_IDS, results, strict=True):
        if isinstance(result, BaseException):
            log.error("fan_out_cluster_failed", tool="check_pdb_upgrade_risk", cluster=cid, error=str(result))
        else:
            outputs.append(result)
    return outputs


# Note 24: PDB selectors use label keys to identify the workload they protect.
# Note 25: The "app" label is the original informal Kubernetes convention and remains
# Note 26: by far the most common key found in real-world deployments and Helm charts.
# Note 27: "app.kubernetes.io/name" is the newer structured label recommended by the
# Note 28: Kubernetes well-known labels spec (sig-apps), but adoption is still partial.
# Note 29: Checking "app" first therefore matches the majority of clusters in practice.
def _workload_from_selector(selector: dict[str, Any]) -> str:
    """Derive a workload name from PDB selector labels."""
    if "app" in selector:
        return str(selector["app"])
    if "app.kubernetes.io/name" in selector:
        return str(selector["app.kubernetes.io/name"])
    # Note 30: Falling back to str(selector) preserves all label key-value pairs so that
    # Note 31: an operator reading the output still has enough context to identify the workload
    # Note 32: even when neither standard label key is present.
    return str(selector) if selector else "unknown"
