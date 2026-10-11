"""Runner-signed media review receipts; verification never reads the private key.

This protects receipt integrity across the agent/tool boundary. It is not OS
isolation from another process running as the same user.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import math
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from filelock import FileLock, Timeout

from agentnexus.process_logging import data_dir

_DOMAIN = b"cine-media-quality-review:v1\0"
_TRUST_DIRECTORY = "cine-quality-trust"
_PRIVATE = Path("private") / "signing-key.bin"
_PUBLIC = Path("public") / "verification-key.bin"
_MAX_PAYLOAD_BYTES = 2 * 1024 * 1024


class MediaQualityReceiptError(ValueError):
    """A receipt or its fixed local trust anchor cannot be verified."""


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


def _root() -> Path:
    # Resolve at each call so configured runtime isolation and tests share one rule.
    return data_dir() / _TRUST_DIRECTORY


def _read_key(root: Path, relative: Path) -> bytes:
    path = root / relative
    try:
        if root.is_symlink() or path.parent.is_symlink() or path.is_symlink():
            raise MediaQualityReceiptError("CINE_REVIEW_TRUST_INVALID")
        raw = path.read_bytes()
    except OSError as exc:
        raise MediaQualityReceiptError("CINE_REVIEW_TRUST_UNAVAILABLE") from exc
    if len(raw) != 32:
        raise MediaQualityReceiptError("CINE_REVIEW_TRUST_INVALID")
    return raw


def _write_key(path: Path, raw: bytes) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())


def _signing_key() -> tuple[Ed25519PrivateKey, bytes]:
    root = _root()
    root.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        with FileLock(str(root.parent / ".cine-quality-trust.lock"), timeout=10):
            if not root.exists() and not root.is_symlink():
                temporary = Path(tempfile.mkdtemp(prefix=".cine-quality-trust-", dir=root.parent))
                try:
                    private = Ed25519PrivateKey.generate()
                    private_raw = private.private_bytes(
                        serialization.Encoding.Raw,
                        serialization.PrivateFormat.Raw,
                        serialization.NoEncryption(),
                    )
                    public_raw = private.public_key().public_bytes(
                        serialization.Encoding.Raw,
                        serialization.PublicFormat.Raw,
                    )
                    _write_key(temporary / _PRIVATE, private_raw)
                    _write_key(temporary / _PUBLIC, public_raw)
                    os.replace(temporary, root)
                finally:
                    if temporary.exists():
                        shutil.rmtree(temporary)
            private_raw = _read_key(root, _PRIVATE)
            public_raw = _read_key(root, _PUBLIC)
            private = Ed25519PrivateKey.from_private_bytes(private_raw)
            expected = private.public_key().public_bytes(
                serialization.Encoding.Raw,
                serialization.PublicFormat.Raw,
            )
            if not hmac.compare_digest(expected, public_raw):
                raise MediaQualityReceiptError("CINE_REVIEW_TRUST_INVALID")
            return private, public_raw
    except (OSError, Timeout) as exc:
        raise MediaQualityReceiptError("CINE_REVIEW_TRUST_UNAVAILABLE") from exc


def sign_review(payload: dict) -> dict:
    """Sign an independent review result after the runner verifies its media bindings."""
    raw = _canonical(payload)
    private, public_raw = _signing_key()
    return {
        "payload": json.loads(raw),
        "signature": base64.b64encode(private.sign(_DOMAIN + raw)).decode("ascii"),
        "key_id": hashlib.sha256(public_raw).hexdigest(),
        "algorithm": "Ed25519",
    }


def review_usage_path(session_id: str, run_id: str) -> Path:
    """Keep paid review reservations beside the protected runtime signing material."""
    _signing_key()
    directory = _root() / "private" / "review-usage"
    if directory.is_symlink():
        raise MediaQualityReceiptError("CINE_REVIEW_TRUST_INVALID")
    directory.mkdir(mode=0o700, exist_ok=True)
    identity = hashlib.sha256(_canonical({"session_id": session_id, "run_id": run_id})).hexdigest()
    path = directory / (identity + ".json")
    if path.is_symlink():
        raise MediaQualityReceiptError("CINE_REVIEW_TRUST_INVALID")
    return path


def verify_review(envelope: dict) -> dict:
    """Verify only against this runtime's public key; callers must check payload bindings."""
    if (
        not isinstance(envelope, dict)
        or set(envelope) != {"payload", "signature", "key_id", "algorithm"}
        or envelope.get("algorithm") != "Ed25519"
    ):
        raise MediaQualityReceiptError("CINE_REVIEW_ENVELOPE_INVALID")
    raw = _canonical(envelope["payload"])
    public_raw = _read_key(_root(), _PUBLIC)
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
