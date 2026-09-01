"""Ingress limits for coordination.v1 envelopes.

Plan §14.2 requires the control plane to constrain hop count, TTL,
payload size, artifact-reference cardinality and loop risk before a
message is durably queued. Oversized bodies must be published as
artifacts and referenced by ``artifact_id`` or ``uri`` instead of being
embedded in the envelope.
"""

from __future__ import annotations

import json
from typing import Any

from omnigent.coordination.types import (
    DEFAULT_MAX_ARTIFACT_REFERENCES,
    DEFAULT_MAX_HOPS,
    DEFAULT_MAX_PAYLOAD_BYTES,
    DEFAULT_MAX_TTL_SECONDS,
)


class CoordinationLimitError(ValueError):
    """Raised when one coordination.v1 envelope limit is exceeded."""

    def __init__(self, message: str, *, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


def validate_hop_budget(*, hop_count: int, max_hops: int) -> None:
    """Reject envelopes past the hop budget or outside server bounds."""
    if max_hops < 1 or max_hops > DEFAULT_MAX_HOPS:
        raise CoordinationLimitError(
            f"max_hops must be between 1 and {DEFAULT_MAX_HOPS}"
        )
    if hop_count < 0 or hop_count >= max_hops:
        raise CoordinationLimitError(
            f"hop_count {hop_count} must be below max_hops {max_hops}"
        )


def validate_ttl(*, ttl_seconds: float | None) -> None:
    """Reject absent/negative/impractical TTLs before queueing."""
    if ttl_seconds is None:
        return
    if not 0 < ttl_seconds <= DEFAULT_MAX_TTL_SECONDS:
        raise CoordinationLimitError(
            f"ttl_seconds must be in (0, {DEFAULT_MAX_TTL_SECONDS}]"
        )


def validate_payload_and_artifacts(
    *,
    payload: dict[str, Any],
    artifacts: list[dict[str, Any]],
) -> None:
    """Enforce payload size and artifact-reference shape.

    Large bodies are always rejected with a 413 so callers publish the
    content through the artifact API first; a message may carry a URI or
    artifact_id reference instead of embedding a dump of the artifact.
    """
    if len(artifacts) > DEFAULT_MAX_ARTIFACT_REFERENCES:
        raise CoordinationLimitError(
            f"message carries more than {DEFAULT_MAX_ARTIFACT_REFERENCES} "
            "artifact references"
        )
    for ref in artifacts:
        if not isinstance(ref, dict) or not (ref.get("artifact_id") or ref.get("uri")):
            raise CoordinationLimitError(
                "each artifact reference must include artifact_id or uri"
            )
    size = len(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
    if size > DEFAULT_MAX_PAYLOAD_BYTES:
        raise CoordinationLimitError(
            f"payload is {size} bytes; the limit is {DEFAULT_MAX_PAYLOAD_BYTES} "
            "bytes. Publish the content as an artifact and reference it with "
            "artifact_id/uri",
            status_code=413,
        )


def validate_message_envelope(
    *,
    hop_count: int,
    max_hops: int,
    ttl_seconds: float | None,
    payload: dict[str, Any],
    artifacts: list[dict[str, Any]],
) -> None:
    """Run all coordination.v1 ingress limits in one pass."""
    validate_hop_budget(hop_count=hop_count, max_hops=max_hops)
    validate_ttl(ttl_seconds=ttl_seconds)
    validate_payload_and_artifacts(payload=payload, artifacts=artifacts)


__all__ = [
    "CoordinationLimitError",
    "validate_hop_budget",
    "validate_message_envelope",
    "validate_payload_and_artifacts",
    "validate_ttl",
]
