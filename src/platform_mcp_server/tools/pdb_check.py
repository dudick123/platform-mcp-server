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

    if mode == "live":
        # In live mode, only report blockers on cordoned nodes
        nodes = await core_client.get_nodes()
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
    results = await asyncio.gather(*tasks, return_exceptions=True)
    outputs: list[PdbCheckOutput] = []
    for cid, result in zip(ALL_CLUSTER_IDS, results, strict=True):
        if isinstance(result, BaseException):
            log.error("fan_out_cluster_failed", tool="check_pdb_upgrade_risk", cluster=cid, error=str(result))
        else:
            outputs.append(result)
    return outputs


def _workload_from_selector(selector: dict[str, Any]) -> str:
    """Derive a workload name from PDB selector labels."""
    if "app" in selector:
        return str(selector["app"])
    if "app.kubernetes.io/name" in selector:
        return str(selector["app.kubernetes.io/name"])
    return str(selector) if selector else "unknown"
