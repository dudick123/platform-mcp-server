"""Input validation helpers for MCP tool parameters."""

# Note 1: `from __future__ import annotations` enables PEP 563 postponed evaluation of
# annotations, so type hints like `str | None` are treated as strings at runtime.
# This lets you use modern union syntax even on Python 3.9 without a TypeError.
from __future__ import annotations

import re

# Note 2: re.compile() pre-compiles the regex pattern into a reusable pattern object.
# Calling re.match(pattern, string) on every request would reparse the pattern each
# time; storing a compiled object avoids that overhead for hot validation paths.

# Note 3: RFC 1123 labels must be lowercase alphanumeric, may contain hyphens in the
# middle, and are capped at 63 characters. Kubernetes enforces this rule for all
# namespace names, so the regex anchors with ^ and $ to match the full string.
# RFC 1123 label: lowercase alphanumeric and hyphens, 1-63 chars, starts/ends with alphanumeric
_NAMESPACE_RE = re.compile(r"^[a-z0-9]([a-z0-9\-]{0,61}[a-z0-9])?$")

# Note 4: AKS node pool names have a stricter constraint than generic RFC 1123 labels:
# they must start with a letter (not a digit) and are limited to 12 characters.
# This extra restriction comes from AKS, not from Kubernetes core.
# AKS node pool: lowercase alphanumeric, 1-12 chars, starts with letter
_NODE_POOL_RE = re.compile(r"^[a-z][a-z0-9]{0,11}$")

# Note 5: A set literal gives O(1) average-case membership tests via hashing.
# Using a set here instead of a list means `mode not in _VALID_MODES` is constant
# time regardless of how many valid modes are defined.
_VALID_MODES = {"preflight", "live"}

# Valid values for the status_filter parameter used by get_pod_health.
_VALID_STATUS_FILTERS = {"all", "pending", "failed"}


# Note 6: This is the "guard clause" (or "early return") pattern. By returning
# immediately when the input is None, the rest of the function stays unindented
# and focused on the actual validation logic, avoiding a nested if-else pyramid.
def validate_namespace(namespace: str | None) -> None:
    """Validate a Kubernetes namespace name against RFC 1123."""
    if namespace is None:
        return
    if not _NAMESPACE_RE.match(namespace):
        # Note 7: The `!r` conversion flag calls repr() on the value before
        # interpolating it. This wraps strings in quotes and escapes special
        # characters, making it immediately clear in error messages that the
        # offending value is a string and revealing invisible characters.
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
        # Note 8: sorted() is called here to produce a deterministic, alphabetically
        # ordered list of valid options. Sets have no guaranteed iteration order,
        # so without sorted() the error message could differ between runs, making
        # tests brittle and user-facing output confusing.
        valid = ", ".join(sorted(_VALID_MODES))
        msg = f"Invalid mode: {mode!r}. Must be one of: {valid}"
        raise ValueError(msg)


def validate_status_filter(status_filter: str) -> None:
    """Validate the status_filter parameter for get_pod_health."""
    if status_filter not in _VALID_STATUS_FILTERS:
        valid = ", ".join(sorted(_VALID_STATUS_FILTERS))
        msg = f"Invalid status_filter: {status_filter!r}. Must be one of: {valid}"
        raise ValueError(msg)
