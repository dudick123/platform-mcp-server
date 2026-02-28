"""Tests for input validation helpers."""

from __future__ import annotations

import pytest

from platform_mcp_server.validation import validate_mode, validate_namespace, validate_node_pool


class TestValidateNamespace:
    def test_valid_namespace(self) -> None:
        validate_namespace("kube-system")

    def test_valid_single_char(self) -> None:
        validate_namespace("a")

    def test_none_is_valid(self) -> None:
        validate_namespace(None)

    def test_invalid_uppercase(self) -> None:
        with pytest.raises(ValueError, match="Invalid namespace"):
            validate_namespace("Kube-System")

    def test_invalid_special_chars(self) -> None:
        with pytest.raises(ValueError, match="Invalid namespace"):
            validate_namespace("ns/test")

    def test_invalid_starts_with_hyphen(self) -> None:
        with pytest.raises(ValueError, match="Invalid namespace"):
            validate_namespace("-invalid")

    def test_empty_string(self) -> None:
        with pytest.raises(ValueError, match="Invalid namespace"):
            validate_namespace("")


class TestValidateNodePool:
    def test_valid_pool(self) -> None:
        validate_node_pool("userpool")

    def test_valid_short(self) -> None:
        validate_node_pool("a")

    def test_none_is_valid(self) -> None:
        validate_node_pool(None)

    def test_invalid_starts_with_digit(self) -> None:
        with pytest.raises(ValueError, match="Invalid node pool"):
            validate_node_pool("1pool")

    def test_invalid_too_long(self) -> None:
        with pytest.raises(ValueError, match="Invalid node pool"):
            validate_node_pool("abcdefghijklm")  # 13 chars

    def test_invalid_uppercase(self) -> None:
        with pytest.raises(ValueError, match="Invalid node pool"):
            validate_node_pool("UserPool")

    def test_invalid_special_chars(self) -> None:
        with pytest.raises(ValueError, match="Invalid node pool"):
            validate_node_pool("user-pool")

    def test_empty_string(self) -> None:
        with pytest.raises(ValueError, match="Invalid node pool"):
            validate_node_pool("")


class TestValidateMode:
    def test_preflight_valid(self) -> None:
        validate_mode("preflight")

    def test_live_valid(self) -> None:
        validate_mode("live")

    def test_invalid_mode(self) -> None:
        with pytest.raises(ValueError, match="Invalid mode"):
            validate_mode("debug")

    def test_case_sensitive(self) -> None:
        with pytest.raises(ValueError, match="Invalid mode"):
            validate_mode("LIVE")
