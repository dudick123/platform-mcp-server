"""Input validation helpers for MCP tool parameters."""

from __future__ import annotations

import re

# RFC 1123 label: lowercase alphanumeric and hyphens, 1-63 chars, starts/ends with alphanumeric
_NAMESPACE_RE = re.compile(r"^[a-z0-9]([a-z0-9\-]{0,61}[a-z0-9])?$")

# AKS node pool: lowercase alphanumeric, 1-12 chars, starts with letter
_NODE_POOL_RE = re.compile(r"^[a-z][a-z0-9]{0,11}$")

_VALID_MODES = {"preflight", "live"}


def validate_namespace(namespace: str | None) -> None:
    """Validate a Kubernetes namespace name against RFC 1123."""
    if namespace is None:
        return
    if not _NAMESPACE_RE.match(namespace):
        msg = f"Invalid namespace: {namespace!r}. Must be a valid RFC 1123 label."
        raise ValueError(msg)


def validate_node_pool(node_pool: str | None) -> None:
    """Validate an AKS node pool name."""
    if node_pool is None:
        return
    if not _NODE_POOL_RE.match(node_pool):
        msg = f"Invalid node pool name: {node_pool!r}. Must be 1-12 lowercase alphanumeric starting with a letter."
        raise ValueError(msg)


def validate_mode(mode: str) -> None:
    """Validate the PDB check mode parameter."""
    if mode not in _VALID_MODES:
        valid = ", ".join(sorted(_VALID_MODES))
        msg = f"Invalid mode: {mode!r}. Must be one of: {valid}"
        raise ValueError(msg)
