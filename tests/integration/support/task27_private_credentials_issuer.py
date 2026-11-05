"""Marker-gated test adapter for the real Task 2.7 credential owner seams."""

from __future__ import annotations

from typing import Any

import pytest

from hmt_crypto.exchange import credentials

_FIXTURE_MARKER = object()
_OWNER_MARKER = credentials._task27_fixture_marker_for_tests()


@pytest.fixture
def task27_private_credentials_marker() -> object:
    """Return the exact source-tree fixture marker accepted by this adapter."""
    return _FIXTURE_MARKER


def _marker(value: object) -> object:
    return _OWNER_MARKER if value is _FIXTURE_MARKER else value


def issue_enrollment_credential(marker: object, **values: Any) -> tuple[object, object]:
    """Issue one sealed signer snapshot and its enrollment currentness owner."""
    return credentials._issue_enrollment_credential_for_tests(
        _marker(marker),
        **values,
    )


def issue_account_binding_proof(
    marker: object,
    signer: object,
    **values: Any,
) -> tuple[object, object]:
    """Issue one sealed account proof and its ordinary currentness owner."""
    return credentials._issue_account_binding_proof_for_tests(
        _marker(marker),
        signer,
        **values,
    )


def advance_observed_at(
    marker: object,
    currentness: object,
    observed_at: object,
) -> None:
    """Advance the owner-published UTC snapshot without reading wall time."""
    credentials._publish_test_currentness_for_tests(
        _marker(marker),
        currentness,
        "observed_at",
        observed_at=observed_at,
    )


def invalidate_credential(marker: object, currentness: object) -> None:
    """Publish terminal credential revocation through the real owner state."""
    credentials._publish_test_currentness_for_tests(_marker(marker), currentness, "revoked")


def advance_credential_epoch(marker: object, currentness: object) -> None:
    """Publish a later credential epoch without minting a replacement."""
    credentials._publish_test_currentness_for_tests(
        _marker(marker), currentness, "credential_epoch"
    )


def advance_proof_epoch(marker: object, currentness: object) -> None:
    """Publish a later account-proof epoch without minting a replacement."""
    credentials._publish_test_currentness_for_tests(_marker(marker), currentness, "proof_epoch")


def invalidate_permission_probe(marker: object, currentness: object) -> None:
    """Publish terminal permission-probe invalidation for one account proof."""
    credentials._publish_test_currentness_for_tests(
        _marker(marker), currentness, "permission_invalid"
    )
