"""Shared utility functions used across tool modules."""

from __future__ import annotations

from datetime import datetime


def parse_iso_timestamp(ts_str: str | None) -> datetime | None:
    """Parse an ISO 8601 timestamp string to a timezone-aware datetime.

    Returns None for empty/None input or unparseable strings, enabling
    graceful degradation when event timestamps are malformed.
    """
    if not ts_str:
        return None
    try:
        return datetime.fromisoformat(ts_str)
    except ValueError, TypeError:
        return None
