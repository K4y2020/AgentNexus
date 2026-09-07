import base64
import hashlib
import json
from unittest.mock import AsyncMock

import httpx
import pytest

from omnigent.seedance.report_review import verify_report


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


@pytest.fixture
def report(tmp_path):
    project = tmp_path / "project"
    rev = project / "revisions/r1"
    write(
        tmp_path / ".cine/sessions/topic.json",
        {"session_id": "topic", "project_path": str(project)},
    )
    write(project / "project.json", {"current_revision": "r1"})
    write(
        project / "source.json",
        {"source_id": "source", "duration_pts": 100, "time_base_num": 1, "time_base_den": 1},
    )
    interval = {"in_pts": 0, "out_pts": 30, "time_base_num": 1, "time_base_den": 1}
    write(
        rev / "source_shots.json",
        [
            {
                "shot_id": "S1",
                "source_id": "source",
                "revision_id": "r1",
                "interval": interval,
                "evidence_ids": ["e"],
            }
        ],
    )
    write(
        project / "reviews/r1.json",
        [
            {
                "shot_id": "S1",
                "source_id": "source",
                "revision_id": "r1",
                "status": "model_reviewed",
                "observations": ["visible figure"],
                "image_receipt_ids": ["receipt"],
            }
        ],
    )
    image = b"evidence"
    (rev / "image.jpg").write_bytes(image)
    meta = {
        "source_id": "source",
        "revision_id": "r1",
        "evidence_id": "e",
        "receipt_id": "receipt",
        "sha256": hashlib.sha256(image).hexdigest(),
        "source_interval": interval,
    }
    write(
        rev / "evidence_index.json",
        [{**meta, "relative_path": "image.jpg", "extraction_status": "ok"}],
    )
    items = [
        {"type": "function_call", "call_id": "c", "name": "sys_os_view_image"},
        {
            "id": "out",
            "type": "function_call_output",
            "call_id": "c",
            "output": json.dumps(
                {
                    "metadata": meta,
                    "image": {
                        "type": "image",
                        "source": {"data": base64.b64encode(image).decode()},
                    },
                }
            ),
        },
    ]
    client = AsyncMock()

    def get(route, **_kwargs):
        data = (
            {"data": items, "has_more": False}
            if route.endswith("/items")
            else {"id": "topic", "workspace": str(tmp_path)}
        )
        return httpx.Response(200, request=httpx.Request("GET", "http://test"), json=data)

    client.get.side_effect = get
    return client, tmp_path, project, items


async def test_short_sample_cannot_certify_full_film(report):
    client, _, _, _ = report
    full = await verify_report(client, "topic", scope="full")
    assert full["status"] == "rejected"
    assert full["verified_shot_count"] == 1
    assert not full["indexed_scope_complete"]
    sample = await verify_report(client, "topic", scope="sample", start_seconds=0, end_seconds=30)
    assert sample["status"] == "accepted_visual_scope"
    assert sample["can_claim_full_audiovisual_analysis"] is False
    assert sample["audio_review"] == "unverified"


async def test_imported_status_is_not_inherited_and_original_unchanged(report):
    client, root, _, _ = report
    path = root / "legacy.json"
    write(
        path, [{"shot_id": "old", "review_status": "model_reviewed", "observations": ["invented"]}]
    )
    before = path.read_bytes()
    result = await verify_report(
        client, "topic", scope="sample", start_seconds=0, end_seconds=30, ledger_path="legacy.json"
    )
    assert result["status"] == "rejected"
    assert result["imported_rows"][0]["effective_status"] == "historical_unverified"
    assert path.read_bytes() == before


async def test_same_id_with_different_observations_does_not_pass(report):
    client, root, _, _ = report
    write(
        root / "report.json",
        [
            {
                "canonical_shot_id": "S1",
                "review_status": "model_reviewed",
                "observations": ["invented"],
            }
        ],
    )
    result = await verify_report(
        client, "topic", scope="sample", start_seconds=0, end_seconds=30, ledger_path="report.json"
    )
    assert result["status"] == "rejected"


async def test_missing_current_receipt_cannot_inherit_review(report):
    client, _, _, items = report
    items.clear()
    result = await verify_report(client, "topic", scope="sample", start_seconds=0, end_seconds=30)
    assert result["verified_shot_count"] == 0
    assert result["status"] == "rejected"


@pytest.mark.parametrize(
    "kwargs",
    [
        {"scope": "full", "end_seconds": 30},
        {"scope": "sample", "start_seconds": 0, "end_seconds": float("nan")},
        {"scope": "sample", "start_seconds": 0, "end_seconds": 101},
    ],
)
async def test_scope_cannot_be_faked(report, kwargs):
    client, _, _, _ = report
    with pytest.raises(ValueError):
        await verify_report(client, "topic", **kwargs)
