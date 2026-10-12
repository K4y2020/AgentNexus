import base64
import hashlib
import json
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import pytest

from agentnexus.seedance.report_review import verify_report as _verify_report

SKILLS = Path(__file__).resolve().parents[2] / "examples/cine/skills"


def verify_report(*args, **kwargs):
    """The Cine report check as the runner calls it, with the bundle's skills."""
    return _verify_report(*args, skills_dir=SKILLS, **kwargs)


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


# The committed source's identity (size + head/tail SHA-256), as in source.json.
FINGERPRINT = {"size_bytes": 4096, "sha256_head": "1" * 64, "sha256_tail": "2" * 64}


def write_asr(workspace, fingerprint=FINGERPRINT, name="inputs/source-transcript"):
    """A qualified ASR transcript (.txt plus its machine .json record)."""
    txt = workspace / f"{name}.txt"
    txt.parent.mkdir(parents=True, exist_ok=True)
    txt.write_text("[0.000-1.000] 别过来。", encoding="utf-8")
    record = {
        "kind": "qualified_asr_transcript",
        "source_media": "source.mp4",
        "segments": [{"start": 0.0, "end": 1.0, "text": "别过来。"}],
    }
    if fingerprint is not None:
        record["source_fingerprint"] = fingerprint
    write(workspace / f"{name}.json", record)


@pytest.fixture
def story_fixture(tmp_path):
    project = tmp_path / "project"
    rev = project / "revisions/r1"
    write(
        tmp_path / ".cine/sessions/topic.json",
        {"session_id": "topic", "project_path": str(project)},
    )
    write(project / "project.json", {"current_revision": "r1"})
    write(
        project / "source.json",
        {
            "source_id": "s",
            "duration_pts": 60,
            "time_base_num": 1,
            "time_base_den": 1,
            **FINGERPRINT,
        },
    )
    write(rev / "source_shots.json", [])
    plan = {"source_id": "s", "revision_id": "r1", "batches": []}
    draft = {
        "source_id": "s",
        "revision_id": "r1",
        "dialogue_provenance": {"status": "unverified", "source_path": None},
        "forms": [],
        "characters": ["masked protagonist"],
        "summary": {
            "premise": "arrival",
            "conflict": "rescue",
            "turning_points": ["intervention"],
            "ending": "rescued",
        },
        "sections": [],
    }
    evidence, items = [], []
    for i in range(2):
        interval = {
            "in_pts": 30 * i,
            "out_pts": 30 * i + 30,
            "time_base_num": 1,
            "time_base_den": 1,
        }
        point = {**interval, "out_pts": 30 * i + 1}
        data = f"image{i}".encode()
        path = rev / f"image{i}.jpg"
        path.write_bytes(data)
        meta = {
            "source_id": "s",
            "revision_id": "r1",
            "evidence_id": f"e{i}",
            "receipt_id": f"r{i}",
            "source_interval": point,
            "sha256": hashlib.sha256(data).hexdigest(),
        }
        evidence.append({**meta, "relative_path": path.name, "extraction_status": "ok"})
        plan["batches"].append(
            {"batch_id": f"B{i}", "interval": interval, "evidence_ids": [f"e{i}"], "shot_ids": []}
        )
        draft["sections"].append(
            {
                "batch_id": f"B{i}",
                "events": ["story event"],
                "connection": "continuation",
                "image_receipt_ids": [f"r{i}"],
                "uncertainties": ["uncertain cut"],
            }
        )
        items.extend(
            [
                {"type": "function_call", "name": "sys_os_view_image", "call_id": f"c{i}"},
                {
                    "id": f"o{i}",
                    "type": "function_call_output",
                    "call_id": f"c{i}",
                    "output": json.dumps(
                        {
                            "metadata": meta,
                            "image": {
                                "type": "image",
                                "source": {"data": base64.b64encode(data).decode()},
                            },
                        }
                    ),
                },
            ]
        )
    write(rev / "story_plan.json", plan)
    write(rev / "evidence_index.json", evidence)
    write(project / "story/r1.json", draft)
    client = AsyncMock()

    def get(route, **_kwargs):
        data = (
            {"data": items, "has_more": False}
            if route.endswith("/items")
            else {"id": "topic", "workspace": str(tmp_path)}
        )
        return httpx.Response(200, request=httpx.Request("GET", "http://test"), json=data)

    client.get.side_effect = get
    return client, project, draft, items


async def test_all_temporal_batches_allow_readiness_without_per_shot_reviews(story_fixture):
    client, _, _, _ = story_fixture
    result = await verify_report(client, "topic", scope="adaptation")
    assert result["status"] == "ready_for_adaptation"
    assert result["completed_batch_count"] == 2
    assert result["warnings"]
    assert result["accuracy_percent"] is None
    assert not result["can_claim_full_audiovisual_analysis"]


def write_shot_notes(project, *, status="read"):
    rev = project / "revisions/r1"
    shots, rows = [], []
    for index in range(2):
        shots.append(
            {
                "source_id": "s",
                "revision_id": "r1",
                "shot_id": f"s{index}",
                "interval": {
                    "in_pts": index * 30,
                    "out_pts": (index + 1) * 30,
                    "time_base_num": 1,
                    "time_base_den": 1,
                },
            }
        )
        rows.append(
            {
                "index": index,
                "shot_id": f"s{index}",
                "start": index * 30,
                "end": (index + 1) * 30,
                "place": "L1",
                "what": "大厅中两人交谈",
            }
        )
    write(rev / "source_shots.json", shots)
    notes = {
        "schema_version": 1,
        "source_id": "s",
        "revision_id": "r1",
        "model": "vision",
        "status": status,
        "shots": rows,
        "locations": [{"id": "L1"}],
        "input_sha256": {
            "source_shots": hashlib.sha256((rev / "source_shots.json").read_bytes()).hexdigest()
        },
    }
    write(rev / "shot_notes.json", notes)
    return notes


async def test_current_shot_notes_allow_story_reading_without_manual_images(story_fixture):
    client, project, draft, _ = story_fixture
    write_shot_notes(project)
    for section in draft["sections"]:
        section["image_receipt_ids"] = []
    write(project / "story/r1.json", draft)
    result = await verify_report(client, "topic", scope="adaptation")
    assert result["status"] == "ready_for_adaptation"
    assert [section["visual_basis"] for section in result["sections"]] == ["shot_notes"] * 2
    assert not result["can_claim_full_audiovisual_analysis"]


@pytest.mark.parametrize("problem", ["revision", "hash", "time", "unknown", "missing", "failed"])
async def test_stale_or_unread_shot_notes_do_not_replace_inspected_images(story_fixture, problem):
    client, project, draft, _ = story_fixture
    notes = write_shot_notes(project)
    for section in draft["sections"]:
        section["image_receipt_ids"] = []
    write(project / "story/r1.json", draft)
    if problem == "revision":
        notes["revision_id"] = "old"
    elif problem == "hash":
        notes["input_sha256"]["source_shots"] = "0" * 64
    elif problem == "time":
        notes["shots"][0]["end"] = 31
    elif problem == "unknown":
        notes["shots"][0]["place"] = "?"
    elif problem == "missing":
        notes["shots"].pop()
    else:
        notes["status"] = "partial"
        notes["unread_shots"] = [0]
    write(project / "revisions/r1/shot_notes.json", notes)
    result = await verify_report(client, "topic", scope="adaptation")
    assert result["status"] == "needs_story_completion"


@pytest.mark.parametrize("missing", ["section", "ending", "receipt"])
async def test_missing_plot_scope_does_not_pass(story_fixture, missing):
    client, project, draft, _ = story_fixture
    if missing == "section":
        draft["sections"].pop()
    elif missing == "ending":
        draft["summary"]["ending"] = ""
    else:
        draft["sections"][-1]["image_receipt_ids"] = ["old-session-receipt"]
    write(project / "story/r1.json", draft)
    result = await verify_report(client, "topic", scope="adaptation")
    assert result["status"] == "needs_story_completion"


async def test_old_revision_and_changed_images_do_not_gain_readiness(story_fixture):
    client, project, draft, _ = story_fixture
    draft["revision_id"] = "old"
    write(project / "story/r1.json", draft)
    with pytest.raises(ValueError, match="REVISION_MISMATCH"):
        await verify_report(client, "topic", scope="adaptation")
    draft["revision_id"] = "r1"
    write(project / "story/r1.json", draft)
    (project / "revisions/r1/image1.jpg").write_bytes(b"changed")
    assert (await verify_report(client, "topic", scope="adaptation"))[
        "status"
    ] == "needs_story_completion"


async def test_quoted_dialogue_without_provenance_is_blocked(story_fixture):
    client, project, draft, _ = story_fixture
    draft["sections"][0]["events"] = ["她说：'别过来。'"]
    write(project / "story/r1.json", draft)
    result = await verify_report(client, "topic", scope="adaptation")
    assert result["status"] == "needs_story_completion"
    assert {issue["reason"] for issue in result["issues"]} >= {
        "quoted_dialogue_without_provenance"
    }


async def test_asr_dialogue_is_qualified_but_allowed(story_fixture):
    client, project, draft, _ = story_fixture
    write_asr(project.parent)
    draft["dialogue_provenance"] = {
        "status": "asr",
        "source_path": "inputs/source-transcript.txt",
    }
    draft["sections"][0]["events"] = ["ASR记录她说：“别过来。”"]
    write(project / "story/r1.json", draft)
    result = await verify_report(client, "topic", scope="adaptation")
    assert result["status"] == "ready_for_adaptation"
    assert result["audio_review"] == "asr"
    assert any("ASR-derived" in warning["detail"] for warning in result["warnings"])


@pytest.mark.parametrize(
    ("fingerprint", "reason"),
    [
        ({**FINGERPRINT, "sha256_tail": "3" * 64}, "dialogue_provenance_asr_source_mismatch"),
        (None, "dialogue_provenance_asr_source_unverified"),
    ],
)
async def test_asr_of_another_source_is_not_accepted(story_fixture, fingerprint, reason):
    """Same file name, different video: basename is not identity."""
    client, project, draft, _ = story_fixture
    write_asr(project.parent, fingerprint=fingerprint)
    draft["dialogue_provenance"] = {"status": "asr", "source_path": "inputs/source-transcript.txt"}
    write(project / "story/r1.json", draft)
    result = await verify_report(client, "topic", scope="adaptation")
    assert result["status"] == "needs_story_completion"
    assert reason in {issue["reason"] for issue in result["issues"]}


async def test_missing_asr_file_is_an_issue_not_a_crash(story_fixture):
    client, project, draft, _ = story_fixture
    draft["dialogue_provenance"] = {"status": "asr", "source_path": "inputs/nowhere.txt"}
    write(project / "story/r1.json", draft)
    result = await verify_report(client, "topic", scope="adaptation")
    assert "dialogue_provenance_asr_missing" in {issue["reason"] for issue in result["issues"]}


async def test_subtitle_asr_is_accepted_with_both_files_and_stays_qualified(story_fixture):
    client, project, draft, _ = story_fixture
    workspace = project.parent
    write_asr(workspace)
    (workspace / "inputs/source.srt").write_text(
        "1\n00:00:00,000 --> 00:00:01,000\n别过来。\n", encoding="utf-8"
    )
    draft["dialogue_provenance"] = {
        "status": "subtitle_asr",
        "subtitle_path": "inputs/source.srt",
        "asr_path": "inputs/source-transcript.txt",
    }
    draft["sections"][0]["events"] = ["字幕与ASR记录她说：“别过来。”"]
    write(project / "story/r1.json", draft)
    result = await verify_report(client, "topic", scope="adaptation")
    assert result["status"] == "ready_for_adaptation"
    assert result["audio_review"] == "subtitle_asr"
    assert any("qualified ASR" in warning["detail"] for warning in result["warnings"])


@pytest.mark.parametrize(
    ("provenance", "reason"),
    [
        (
            {"status": "subtitle_asr", "asr_path": "inputs/source-transcript.txt"},
            "dialogue_provenance_subtitle_missing",
        ),
        (
            {
                "status": "subtitle_asr",
                "subtitle_path": "../outside.srt",
                "asr_path": "inputs/source-transcript.txt",
            },
            "dialogue_provenance_subtitle_outside_workspace",
        ),
        (
            {"status": "subtitle_asr", "subtitle_path": "inputs/source.srt"},
            "dialogue_provenance_asr_missing",
        ),
    ],
)
async def test_subtitle_asr_requires_both_declared_files(story_fixture, provenance, reason):
    client, project, draft, _ = story_fixture
    workspace = project.parent
    write_asr(workspace)
    (workspace / "inputs/source.srt").write_text("1\n", encoding="utf-8")
    (workspace.parent / "outside.srt").write_text("1\n", encoding="utf-8")
    draft["dialogue_provenance"] = provenance
    write(project / "story/r1.json", draft)
    result = await verify_report(client, "topic", scope="adaptation")
    assert result["status"] == "needs_story_completion"
    assert reason in {issue["reason"] for issue in result["issues"]}


async def test_draft_structure_uses_the_pipelines_issue_codes(story_fixture):
    client, project, draft, _ = story_fixture
    missing = draft["sections"].pop()["batch_id"]
    draft["sections"][0]["connection"] = ""
    del draft["summary"]
    write(project / "story/r1.json", draft)
    result = await verify_report(client, "topic", scope="adaptation")

    assert result["status"] == "needs_story_completion"
    assert {"reason": "section_missing", "batch_id": missing} in result["issues"]
    assert {
        "reason": "section_incomplete",
        "batch_id": draft["sections"][0]["batch_id"],
    } in result["issues"]
    assert {"reason": "summary_missing"} in result["issues"]


async def test_adaptation_readiness_needs_the_film_analysis_pipeline(story_fixture):
    client, *_ = story_fixture
    with pytest.raises(ValueError, match="no film-analysis skill"):
        await _verify_report(client, "topic", scope="adaptation")
