"""Portable verification of runner-signed quality receipts; never loads private keys."""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import math
import os
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

_DOMAIN = b"cine-media-quality-review:v1\0"
_MAX_PAYLOAD_BYTES = 2 * 1024 * 1024


class MediaQualityReceiptError(ValueError):
    """The receipt or the runtime's fixed public trust anchor is invalid."""


def _canonical(payload: dict) -> bytes:
    def valid(value: Any) -> bool:
        if isinstance(value, dict):
            return all(isinstance(key, str) and valid(item) for key, item in value.items())
        if isinstance(value, list):
            return all(valid(item) for item in value)
        return (
            value is None
            or type(value) in (str, int, bool)
            or (type(value) is float and math.isfinite(value))
        )

    if not isinstance(payload, dict) or not valid(payload):
        raise MediaQualityReceiptError("CINE_REVIEW_PAYLOAD_INVALID")
    try:
        raw = json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
    except (ValueError, TypeError, UnicodeError) as exc:
        raise MediaQualityReceiptError("CINE_REVIEW_PAYLOAD_INVALID") from exc
    if len(raw) > _MAX_PAYLOAD_BYTES:
        raise MediaQualityReceiptError("CINE_REVIEW_PAYLOAD_TOO_LARGE")
    return raw


def _public_key() -> bytes:
    value = os.environ.get("AGENTNEXUS_DATA_DIR")
    base = Path(value).expanduser() if value else Path.home() / ".agentnexus"
    root = base / "cine-quality-trust"
    path = root / "public" / "verification-key.bin"
    try:
        if root.is_symlink() or path.parent.is_symlink() or path.is_symlink():
            raise MediaQualityReceiptError("CINE_REVIEW_TRUST_INVALID")
        raw = path.read_bytes()
    except OSError as exc:
        raise MediaQualityReceiptError("CINE_REVIEW_TRUST_UNAVAILABLE") from exc
    if len(raw) != 32:
        raise MediaQualityReceiptError("CINE_REVIEW_TRUST_INVALID")
    return raw


def verify_review(envelope: dict) -> dict:
    """Verify using the fixed public key and return a detached copy of the payload."""
    if (
        not isinstance(envelope, dict)
        or set(envelope) != {"payload", "signature", "key_id", "algorithm"}
        or envelope.get("algorithm") != "Ed25519"
    ):
        raise MediaQualityReceiptError("CINE_REVIEW_ENVELOPE_INVALID")
    raw = _canonical(envelope["payload"])
    public_raw = _public_key()
    expected_id = hashlib.sha256(public_raw).hexdigest()
    if (
        not isinstance(envelope["key_id"], str)
        or not envelope["key_id"].isascii()
        or not hmac.compare_digest(envelope["key_id"], expected_id)
    ):
        raise MediaQualityReceiptError("CINE_REVIEW_KEY_MISMATCH")
    try:
        if not isinstance(envelope["signature"], str):
            raise ValueError("signature must be base64 text")
        signature = base64.b64decode(envelope["signature"], validate=True)
        if len(signature) != 64:
            raise ValueError("invalid signature length")
        Ed25519PublicKey.from_public_bytes(public_raw).verify(signature, _DOMAIN + raw)
    except (InvalidSignature, ValueError, TypeError, binascii.Error) as exc:
        raise MediaQualityReceiptError("CINE_REVIEW_SIGNATURE_INVALID") from exc
    return json.loads(raw)
