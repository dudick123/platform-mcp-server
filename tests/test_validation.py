"""Tests for input validation helpers."""

# Note 1: Validation tests are among the most important in any API-facing service.
# They document the contract the system enforces on its callers and act as a
# regression guard — if someone loosens a validation rule unintentionally, these
# tests will catch it immediately.

# Note 2: `from __future__ import annotations` is included here even though no
# complex type hints are used. It is a project-wide convention that makes all
# annotation strings lazy, which keeps import times low as the type annotation
# ecosystem grows.
from __future__ import annotations

import pytest

from platform_mcp_server.validation import validate_mode, validate_namespace, validate_node_pool, validate_status_filter


# Note 3: Grouping tests into classes is a pytest best practice for related test
# cases. The class name (`TestValidateNamespace`) becomes part of the test node ID
# shown in output, making it easy to run just one group with:
#   pytest tests/test_validation.py::TestValidateNamespace
# Classes also let you share fixtures via `self` or class-level setup, though that
# is not needed here since these tests are stateless.
class TestValidateNamespace:
    # Note 4: "Happy path" tests always come first by convention. They verify the
    # function works at all before you test its edge cases. If the happy path fails,
    # the error signal is unambiguous — no need to debug whether a fixture or edge
    # case is interfering.
    def test_valid_namespace(self) -> None:
        # Note 5: This test calls `validate_namespace` without capturing its return
        # value. The implicit assertion is that no exception is raised. pytest treats
        # any uncaught exception as a test failure, so a silent return is sufficient
        # to prove the input was accepted.
        validate_namespace("kube-system")

    def test_valid_single_char(self) -> None:
        # Note 6: Single-character input tests the lower boundary of the length
        # constraint. Kubernetes namespace rules allow a single lowercase letter.
        # Boundary testing (minimum valid, maximum valid, one-below-minimum,
        # one-above-maximum) is a core technique in equivalence partitioning.
        validate_namespace("a")

    def test_none_is_valid(self) -> None:
        # Note 7: Accepting `None` as a valid namespace means "no filter applied"
        # (i.e., list resources across all namespaces). Testing this explicitly
        # prevents future refactors from accidentally treating `None` as an empty
        # string, which would fail the regex check and break callers that rely on
        # the all-namespaces behavior.
        validate_namespace(None)

    def test_invalid_uppercase(self) -> None:
        # Note 8: `pytest.raises` is the idiomatic way to assert that a specific
        # exception type is raised. It acts as a context manager — code inside the
        # `with` block is expected to raise; if it does not, pytest marks the test
        # as failed. This is far cleaner than a try/except with an explicit `fail()`.
        with pytest.raises(ValueError, match="Invalid namespace"):
            # Note 9: The `match` parameter is a regex pattern applied to the string
            # representation of the exception. Checking the message (not just the
            # type) ensures the error comes from the right code path and that the
            # error text is human-readable. "Invalid namespace" ties the test to the
            # exact wording used in the production error message.
            validate_namespace("Kube-System")

    def test_invalid_special_chars(self) -> None:
        # Note 10: The slash character (`/`) would be interpreted as a URL path
        # separator in Kubernetes API calls, creating a security risk (path
        # traversal). Validating against it at the input layer is a defence-in-depth
        # measure. Testing this specific character exercises the special-chars branch
        # of the regex, independently from the uppercase branch.
        with pytest.raises(ValueError, match="Invalid namespace"):
            validate_namespace("ns/test")

    def test_invalid_starts_with_hyphen(self) -> None:
        # Note 11: Kubernetes RFC-1123 label rules prohibit names from starting or
        # ending with a hyphen. A string that passes the character-set check (only
        # lowercase letters and hyphens) but violates the start/end rule is a
        # distinct equivalence class that needs its own test case.
        with pytest.raises(ValueError, match="Invalid namespace"):
            validate_namespace("-invalid")

    def test_empty_string(self) -> None:
        # Note 12: An empty string is subtly different from `None`. `None` means
        # "no preference" (all namespaces), while `""` is likely a programmer error
        # (accidentally passing an uninitialized variable). Rejecting `""` ensures
        # the API surface is unambiguous.
        with pytest.raises(ValueError, match="Invalid namespace"):
            validate_namespace("")


class TestValidateNodePool:
    def test_valid_pool(self) -> None:
        # Note 13: "userpool" is the canonical default node pool name in AKS
        # configurations managed by this project. Using the real production value
        # (rather than a generic "foo") makes the test double as living documentation
        # of valid pool naming conventions.
        validate_node_pool("userpool")

    def test_valid_short(self) -> None:
        validate_node_pool("a")

    def test_none_is_valid(self) -> None:
        # Note 14: Like namespace, `None` for node pool means "all pools". This is
        # the default state for many tool calls where the operator does not want to
        # restrict output to a single pool.
        validate_node_pool(None)

    def test_invalid_starts_with_digit(self) -> None:
        # Note 15: AKS node pool names must start with a lowercase letter. Names
        # starting with a digit would conflict with AKS ARM resource naming rules.
        # This test isolates that specific rule from the other constraints (length,
        # uppercase, special chars) by providing a string that only violates the
        # leading-digit rule.
        with pytest.raises(ValueError, match="Invalid node pool"):
            validate_node_pool("1pool")

    def test_invalid_too_long(self) -> None:
        # Note 16: The comment `# 13 chars` is an inline documentation aid. The
        # actual maximum AKS node pool name length is 12 characters. By explicitly
        # counting the test string in the comment, future readers can quickly verify
        # this is a genuine over-limit input without counting manually.
        with pytest.raises(ValueError, match="Invalid node pool"):
            validate_node_pool("abcdefghijklm")  # 13 chars

    def test_invalid_uppercase(self) -> None:
        # Note 17: "UserPool" (mixed case) is a plausible typo — someone might type
        # it when copying from documentation that uses title case. Testing this
        # specific mistake makes the test suite feel like a safety net for real-world
        # operator errors, not just theoretical edge cases.
        with pytest.raises(ValueError, match="Invalid node pool"):
            validate_node_pool("UserPool")

    def test_invalid_special_chars(self) -> None:
        # Note 18: Hyphens are valid in namespace names but NOT in node pool names.
        # This is a subtle asymmetry in Kubernetes naming rules. Having a dedicated
        # test for this prevents confusion and ensures the two validators have
        # genuinely different regex patterns rather than sharing a single one.
        with pytest.raises(ValueError, match="Invalid node pool"):
            validate_node_pool("user-pool")

    def test_empty_string(self) -> None:
        with pytest.raises(ValueError, match="Invalid node pool"):
            validate_node_pool("")


class TestValidateMode:
    # Note 19: Mode validation tests form an exhaustive check of an enum-like
    # constraint. Because the set of valid modes is small and fixed ("preflight"
    # and "live"), it is practical to test every valid value explicitly rather than
    # parameterising. This makes the allowed values visible at a glance in the test
    # file — they serve as documentation.
    def test_preflight_valid(self) -> None:
        # Note 20: "preflight" mode means validation runs before an upgrade begins,
        # checking for PDB risks or other blocking conditions without touching the
        # cluster. Testing both modes separately ensures neither is accidentally
        # removed from the allowlist.
        validate_mode("preflight")

    def test_live_valid(self) -> None:
        # Note 21: "live" mode runs checks against a cluster that is actively being
        # upgraded. It is a distinct operational context with different safety
        # assumptions. Verifying it is accepted (not just that "preflight" is
        # accepted) prevents a regression where only one mode survives a refactor.
        validate_mode("live")

    def test_invalid_mode(self) -> None:
        # Note 22: "debug" is a plausible value someone might try if they assume the
        # function accepts any free-form string. Using a believable invalid value
        # (rather than a nonsense string like "xyz") makes the test intent clear:
        # anything outside the explicit allowlist must be rejected.
        with pytest.raises(ValueError, match="Invalid mode"):
            validate_mode("debug")

    def test_case_sensitive(self) -> None:
        # Note 23: Case sensitivity tests are critical for security-adjacent
        # validation. Without an explicit test, a developer might add a `.lower()`
        # call thinking they are being helpful, unintentionally making "LIVE" and
        # "live" equivalent. This test locks in the requirement that the check is
        # case-sensitive, preventing that category of silent behaviour change.
        with pytest.raises(ValueError, match="Invalid mode"):
            validate_mode("LIVE")


class TestValidateStatusFilter:
    def test_all_valid(self) -> None:
        validate_status_filter("all")

    def test_pending_valid(self) -> None:
        validate_status_filter("pending")

    def test_failed_valid(self) -> None:
        validate_status_filter("failed")

    def test_invalid_filter(self) -> None:
        with pytest.raises(ValueError, match="Invalid status_filter"):
            validate_status_filter("running")

    def test_case_sensitive(self) -> None:
        with pytest.raises(ValueError, match="Invalid status_filter"):
            validate_status_filter("All")

    def test_empty_string(self) -> None:
        with pytest.raises(ValueError, match="Invalid status_filter"):
            validate_status_filter("")
