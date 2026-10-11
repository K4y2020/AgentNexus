"""Signed review receipts reject local edits and caller-selected trust anchors."""

import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from agentnexus.seedance import media_quality_receipts as receipts


@pytest.fixture
def trust(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTNEXUS_DATA_DIR", str(tmp_path / "runtime"))
    return tmp_path / "runtime" / "cine-quality-trust"


@pytest.fixture
def payload():
    return {
        "session_id": "topic",
        "project_id": "project",
        "run_id": "run-1",
        "plan_sha256": "a" * 64,
        "item_id": "shot-1",
        "attempt": 1,
        "generation_request_id": "generation-1",
        "job_id": "job-1",
        "media_sha256": "b" * 64,
        "requirements_sha256": "c" * 64,
        "upstream_hashes": {"script.json": "d" * 64},
        "reviewer_model": "independent-review-model",
        "reviewer_version": "1",
        "coverage": {"visual": "full", "motion": "full", "audio": "full"},
        "verdict": "passed",
        "defects": [],
        "notes": "画面、动作、音轨均已检查",
    }


def test_round_trip_persists_key_and_does_not_alias_payload(trust, payload):
    signed = receipts.sign_review(payload)
    assert receipts.verify_review(signed) == payload
    assert signed["algorithm"] == "Ed25519"
    assert (trust / "private/signing-key.bin").stat().st_size == 32
    assert (trust / "public/verification-key.bin").stat().st_size == 32
    second = receipts.sign_review(payload)
    assert second == signed
    payload["verdict"] = "changed"
    assert receipts.verify_review(signed)["verdict"] == "passed"


@pytest.mark.parametrize(
    "field",
    [
        "verdict",
        "media_sha256",
        "requirements_sha256",
        "job_id",
        "plan_sha256",
        "item_id",
        "attempt",
        "session_id",
        "upstream_hashes",
        "coverage",
    ],
)
def test_changed_review_or_input_binding_cannot_verify(trust, payload, field):
    signed = receipts.sign_review(payload)
    signed["payload"][field] = "edited by the agent"
    with pytest.raises(receipts.MediaQualityReceiptError, match="CINE_REVIEW_SIGNATURE_INVALID"):
        receipts.verify_review(signed)


@pytest.mark.parametrize("key", ["public_key", "public_key_path", "key_path", "data_dir"])
def test_caller_cannot_supply_a_trust_anchor(trust, payload, key):
    signed = receipts.sign_review(payload)
    signed[key] = "attacker-controlled-key"
    with pytest.raises(receipts.MediaQualityReceiptError, match="CINE_REVIEW_ENVELOPE_INVALID"):
        receipts.verify_review(signed)


def test_unsigned_model_written_pass_is_rejected_without_creating_keys(trust, payload):
    with pytest.raises(receipts.MediaQualityReceiptError):
        receipts.verify_review({"payload": payload, "verdict": "passed"})
    assert not trust.exists()


def test_missing_verification_key_does_not_initialize_a_new_identity(trust, payload):
    with pytest.raises(receipts.MediaQualityReceiptError, match="CINE_REVIEW_TRUST_UNAVAILABLE"):
        receipts.verify_review(
            {"payload": payload, "algorithm": "Ed25519", "key_id": "a" * 64, "signature": "fake"}
        )
    assert not trust.exists()


def test_verification_reads_only_public_key(trust, payload):
    signed = receipts.sign_review(payload)
    (trust / "private/signing-key.bin").unlink()
    assert receipts.verify_review(signed) == payload
    with pytest.raises(receipts.MediaQualityReceiptError, match="CINE_REVIEW_TRUST_UNAVAILABLE"):
        receipts.sign_review(payload)
    assert not (trust / "private/signing-key.bin").exists()


@pytest.mark.parametrize("relative", ["private/signing-key.bin", "public/verification-key.bin"])
def test_corrupt_key_pair_is_not_reinitialized(trust, payload, relative):
    receipts.sign_review(payload)
    path = trust / relative
    path.write_bytes(b"corrupt")
    with pytest.raises(receipts.MediaQualityReceiptError, match="CINE_REVIEW_TRUST_INVALID"):
        receipts.sign_review(payload)
    assert path.read_bytes() == b"corrupt"


def test_different_public_key_and_different_runtime_are_rejected(
    trust, payload, tmp_path, monkeypatch
):
    signed = receipts.sign_review(payload)
    monkeypatch.setenv("AGENTNEXUS_DATA_DIR", str(tmp_path / "other-runtime"))
    other = receipts.sign_review(payload)
    assert other["key_id"] != signed["key_id"]
    with pytest.raises(receipts.MediaQualityReceiptError, match="CINE_REVIEW_KEY_MISMATCH"):
        receipts.verify_review(signed)
    signed["key_id"] = other["key_id"]
    with pytest.raises(receipts.MediaQualityReceiptError, match="CINE_REVIEW_SIGNATURE_INVALID"):
        receipts.verify_review(signed)


def test_concurrent_initialization_keeps_one_key_pair(trust, payload):
    with ThreadPoolExecutor(max_workers=8) as pool:
        signed = list(pool.map(lambda _i: receipts.sign_review(payload), range(16)))
    assert len({item["key_id"] for item in signed}) == 1
    assert all(receipts.verify_review(item) == payload for item in signed)
    assert sorted(path.name for path in trust.iterdir()) == ["private", "public"]


@pytest.mark.parametrize(
    "changed",
    [
        {"algorithm": "HS256"},
        {"signature": "bad signature"},
        {"signature": None},
        {"key_id": "invalid"},
    ],
)
def test_bad_envelope_fields_fail_closed(trust, payload, changed):
    signed = receipts.sign_review(payload)
    signed.update(changed)
    with pytest.raises(receipts.MediaQualityReceiptError):
        receipts.verify_review(signed)


@pytest.mark.parametrize(
    "bad", [{"score": float("nan")}, {"value": b"bytes"}, {1: "non-string key"}, ["not a dict"]]
)
def test_non_json_payload_is_rejected_before_key_creation(trust, bad):
    with pytest.raises(receipts.MediaQualityReceiptError, match="CINE_REVIEW_PAYLOAD_INVALID"):
        receipts.sign_review(bad)
    assert not trust.exists()


def test_serialized_receipt_verifies_with_a_fresh_process(trust, payload, tmp_path):
    import subprocess
    import sys

    signed = receipts.sign_review(payload)
    path = tmp_path / "receipt.json"
    path.write_text(json.dumps(signed), encoding="utf-8")
    code = (
        "import json, sys; "
        "from agentnexus.seedance.media_quality_receipts import verify_review; "
        "print(verify_review(json.load(open(sys.argv[1], encoding='utf-8')))['job_id'])"
    )
    result = subprocess.run(
        [sys.executable, "-B", "-c", code, str(path)], capture_output=True, text=True, check=True
    )
    assert result.stdout.strip() == "job-1"
