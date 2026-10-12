"""The copied skill verifies real runner signatures with Python 3.10 and no core install."""

import copy
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from agentnexus.seedance import media_quality_receipts as core

SOURCE = Path(__file__).parents[1] / "skills/film-analysis/pipeline/quality_receipt.py"
_SPEC = importlib.util.spec_from_file_location("portable_quality_receipt", SOURCE)
assert _SPEC and _SPEC.loader
portable = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(portable)


@pytest.fixture
def trust(tmp_path, monkeypatch):
    base = tmp_path / "runtime"
    monkeypatch.setenv("AGENTNEXUS_DATA_DIR", str(base))
    return base / "cine-quality-trust"


@pytest.fixture
def payload():
    return {
        "session_id": "topic",
        "snapshot_sha256": "a" * 64,
        "review": {
            "decision": "pass",
            "summary": "人物听见回应后再离场。",
            "confidence": 0.92,
            "issues": [],
            "covered": True,
            "unknown": None,
        },
    }


def test_real_core_signature_verifies_without_private_key_and_without_aliasing(trust, payload):
    envelope = core.sign_review(payload)
    private = trust / "private" / "signing-key.bin"
    private.unlink()
    result = portable.verify_review(envelope)
    assert result == core.verify_review(envelope) == payload
    result["review"]["decision"] = "modified result"
    assert envelope["payload"]["review"]["decision"] == "pass"
    envelope["payload"]["review"]["issues"].append("modified envelope")
    assert result["review"]["issues"] == []
    assert not private.exists()
    assert not hasattr(portable, "sign_review")


@pytest.mark.parametrize("field", ["session_id", "snapshot_sha256", "review"])
@pytest.mark.usefixtures("trust")
def test_payload_changes_fail_exactly_like_core(payload, field):
    envelope = core.sign_review(payload)
    envelope["payload"][field] = "tampered"
    for module in (portable, core):
        with pytest.raises(module.MediaQualityReceiptError, match="CINE_REVIEW_SIGNATURE_INVALID"):
            module.verify_review(envelope)


@pytest.mark.parametrize("name", ["public_key", "public_key_path", "data_dir", "key_path"])
@pytest.mark.usefixtures("trust")
def test_envelope_cannot_select_its_key_or_trust_directory(payload, name):
    envelope = core.sign_review(payload)
    envelope[name] = "attacker-controlled"
    with pytest.raises(portable.MediaQualityReceiptError, match="CINE_REVIEW_ENVELOPE_INVALID"):
        portable.verify_review(envelope)


def test_missing_public_key_fails_without_creating_a_runtime(trust, payload):
    envelope = {
        "payload": payload,
        "signature": "none",
        "key_id": "a" * 64,
        "algorithm": "Ed25519",
    }
    with pytest.raises(portable.MediaQualityReceiptError, match="CINE_REVIEW_TRUST_UNAVAILABLE"):
        portable.verify_review(envelope)
    assert not trust.exists()


def test_replaced_public_key_and_rewritten_key_id_cannot_validate_old_signature(trust, payload):
    envelope = core.sign_review(payload)
    key = trust / "public" / "verification-key.bin"
    replacement = bytes(reversed(key.read_bytes()))
    key.write_bytes(replacement)
    with pytest.raises(portable.MediaQualityReceiptError, match="CINE_REVIEW_KEY_MISMATCH"):
        portable.verify_review(envelope)
    import hashlib

    envelope["key_id"] = hashlib.sha256(replacement).hexdigest()
    with pytest.raises(portable.MediaQualityReceiptError, match="CINE_REVIEW_SIGNATURE_INVALID"):
        portable.verify_review(envelope)
    assert key.read_bytes() == replacement


@pytest.mark.parametrize(
    "changed",
    [
        {"algorithm": "HS256"},
        {"signature": "broken!"},
        {"signature": None},
        {"signature": "YQ=="},
        {"key_id": "非ASCII"},
        {"key_id": "invalid"},
    ],
)
@pytest.mark.usefixtures("trust")
def test_malformed_envelope_fields_match_core_rejection(payload, changed):
    envelope = core.sign_review(payload)
    envelope.update(changed)
    errors = []
    for module in (portable, core):
        with pytest.raises(module.MediaQualityReceiptError) as error:
            module.verify_review(envelope)
        errors.append(str(error.value))
    assert errors[0] == errors[1]


@pytest.mark.parametrize(
    "bad",
    [
        {"score": float("nan")},
        {"score": float("inf")},
        {"nested": [b"bytes"]},
        {1: "integer key"},
        {"tuple": (1, 2)},
        ["not an object"],
        {"text": "\ud800"},
    ],
)
def test_non_json_payloads_are_rejected_before_any_key_read(trust, bad):
    envelope = {"payload": bad, "signature": "none", "key_id": "a" * 64, "algorithm": "Ed25519"}
    for module in (portable, core):
        with pytest.raises(module.MediaQualityReceiptError, match="CINE_REVIEW_PAYLOAD_INVALID"):
            module.verify_review(copy.deepcopy(envelope))
    assert not trust.exists()


def test_payload_size_limit_matches_core_without_reading_keys(trust):
    envelope = {
        "payload": {"data": "x" * (2 * 1024 * 1024)},
        "signature": "none",
        "key_id": "a" * 64,
        "algorithm": "Ed25519",
    }
    for module in (portable, core):
        with pytest.raises(module.MediaQualityReceiptError, match="CINE_REVIEW_PAYLOAD_TOO_LARGE"):
            module.verify_review(envelope)
    assert not trust.exists()


def test_public_key_path_aliases_are_refused(trust, payload, monkeypatch):
    envelope = core.sign_review(payload)
    original = Path.is_symlink
    public = trust / "public" / "verification-key.bin"
    monkeypatch.setattr(Path, "is_symlink", lambda path: path == public or original(path))
    for module in (portable, core):
        with pytest.raises(module.MediaQualityReceiptError, match="CINE_REVIEW_TRUST_INVALID"):
            module.verify_review(envelope)


@pytest.mark.parametrize("setting", [None, "", "~/custom-runtime"])
def test_default_and_expanded_runtime_paths_match_core(tmp_path, monkeypatch, payload, setting):
    monkeypatch.setattr(Path, "home", classmethod(lambda _cls: tmp_path))
    if setting is None:
        monkeypatch.delenv("AGENTNEXUS_DATA_DIR", raising=False)
    else:
        monkeypatch.setenv("AGENTNEXUS_DATA_DIR", setting)
    if setting == "~/custom-runtime":
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setenv("USERPROFILE", str(tmp_path))
    envelope = core.sign_review(payload)
    assert portable.verify_review(envelope) == payload


@pytest.mark.parametrize(
    "interpreter", list(dict.fromkeys([sys.executable, shutil.which("python") or sys.executable]))
)
def test_copied_verifier_runs_in_isolated_python_without_agentnexus(
    trust, payload, tmp_path, interpreter
):
    envelope = core.sign_review(payload)
    (trust / "private" / "signing-key.bin").unlink()
    directory = tmp_path / "standalone"
    directory.mkdir()
    module = directory / "quality_receipt.py"
    shutil.copyfile(SOURCE, module)
    receipt = directory / "receipt.json"
    receipt.write_text(json.dumps(envelope, ensure_ascii=False), encoding="utf-8")
    code = """
import builtins, importlib.util, json, sys
original_import = builtins.__import__
def isolated_import(name, *args, **kwargs):
    if name == 'agentnexus' or name.startswith('agentnexus.'):
        raise AssertionError('The portable verifier imported agentnexus')
    return original_import(name, *args, **kwargs)
builtins.__import__ = isolated_import
spec = importlib.util.spec_from_file_location('portable', sys.argv[1])
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
with open(sys.argv[2], encoding='utf-8') as handle:
    result = module.verify_review(json.load(handle))
assert not any(name == 'agentnexus' or name.startswith('agentnexus.') for name in sys.modules)
assert not hasattr(module, 'sign_review')
print(json.dumps({'version': list(sys.version_info[:2]),
                  'decision': result['review']['decision']}))
"""
    result = subprocess.run(
        [interpreter, "-I", "-B", "-c", code, str(module), str(receipt)],
        cwd=directory,
        env=dict(os.environ),
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )
    verified = json.loads(result.stdout)
    assert verified["decision"] == "pass"
    assert verified["version"] >= [3, 10]
    assert not (trust / "private" / "signing-key.bin").exists()
