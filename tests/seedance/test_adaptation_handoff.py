import hashlib
import importlib.util
import json
from pathlib import Path

import pytest


@pytest.fixture
def handoff():
    path = (
        Path(__file__).resolve().parents[2]
        / "examples/cine/skills/film-analysis/pipeline/handoff.py"
    )
    spec = importlib.util.spec_from_file_location("_test_cine_handoff", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def make_manifest(root, mode="adaptation"):
    docs = {
        "source_material": {"source_id": "video-a", "revision_id": "r1", "shots": []},
        "outline": {"source": "adapted", "episodes": []},
        "cast": {"source": "adapted", "characters": []},
        "art": {"source": "adapted", "scenes": []},
        "script": {"source": "adapted", "episodes": []},
    }
    deps = {
        "source_material": [],
        "outline": ["source_material"],
        "cast": ["outline"],
        "art": ["outline"],
        "script": ["outline", "cast", "art"],
    }
    if mode == "original":
        del docs["source_material"]
        deps["outline"] = []
    artifacts = {}
    for name, doc in docs.items():
        path = root / f"chosen-{name}.json"
        raw = json.dumps(doc).encode()
        path.write_bytes(raw)
        artifacts[name] = {
            "path": path.name,
            "sha256": hashlib.sha256(raw).hexdigest(),
            "inputs": {},
        }
    for name in artifacts:
        artifacts[name]["inputs"] = {key: artifacts[key]["sha256"] for key in deps[name]}
    path = root / "production.json"
    path.write_text(
        json.dumps({"schema_version": 1, "mode": mode, "artifacts": artifacts}), encoding="utf-8"
    )
    return path


def test_script_inputs_can_be_checked_before_storyboarding(handoff, tmp_path):
    path = make_manifest(tmp_path)
    result = handoff.validate(path, "script")
    assert result["status"] == "stage_inputs_validated"
    assert set(result["checked_artifacts"]) == {
        "source_material",
        "outline",
        "cast",
        "art",
        "script",
    }
    assert "source_readiness" in result["not_verified"]
    assert "semantic_quality" in result["not_verified"]
    assert not (tmp_path / "storyboard.json").exists()
    with pytest.raises(ValueError, match="ARTIFACT_SET_MISMATCH"):
        handoff.validate(path)


def test_outline_check_does_not_require_future_stages(handoff, tmp_path):
    path = make_manifest(tmp_path)
    value = json.loads(path.read_text())
    value["artifacts"] = {
        k: v for k, v in value["artifacts"].items() if k in ["outline", "source_material"]
    }
    path.write_text(json.dumps(value), encoding="utf-8")
    assert handoff.validate(path, "outline")["checked_artifacts"] == ["source_material", "outline"]


def test_changed_outline_cannot_silently_validate_old_script(handoff, tmp_path):
    path = make_manifest(tmp_path)
    outline = tmp_path / "chosen-outline.json"
    outline.write_text('{"source":"another revision","episodes":[]}', encoding="utf-8")
    value = json.loads(path.read_text())
    value["artifacts"]["outline"]["sha256"] = hashlib.sha256(outline.read_bytes()).hexdigest()
    for stage in ["cast", "art"]:
        value["artifacts"][stage]["inputs"]["outline"] = value["artifacts"]["outline"]["sha256"]
    path.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(ValueError, match="UPSTREAM_STALE:script:outline"):
        handoff.validate(path, "script")


def test_original_script_does_not_require_fabricated_video_source(handoff, tmp_path):
    path = make_manifest(tmp_path, "original")
    result = handoff.validate(path, "script")
    assert "source_material" not in result["checked_artifacts"]


def test_partial_stage_still_rejects_escaping_bindings(handoff, tmp_path):
    path = make_manifest(tmp_path)
    value = json.loads(path.read_text())
    value["artifacts"]["script"]["path"] = "../outside-script.json"
    outside = tmp_path.parent / "outside-script.json"
    outside.write_text("{}", encoding="utf-8")
    path.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(ValueError, match="PATH_ESCAPE:script"):
        handoff.validate(path, "script")


def source_fixture(root):
    values = {
        "project.json": {"current_revision": "r1"},
        "source.json": {"source_id": "source"},
        "revisions/r1/source_shots.json": [],
        "revisions/r1/story_plan.json": {
            "source_id": "source",
            "revision_id": "r1",
            "batches": [],
        },
        "story/r1.json": {
            "source_id": "source",
            "revision_id": "r1",
            "characters": ["unknown"],
            "summary": {"premise": "observed draft", "ending": "uncertain"},
            "sections": [],
        },
    }
    for rel, value in values.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value), encoding="utf-8")
    return values


def test_adaptation_export_keeps_story_but_does_not_certify_it(handoff, tmp_path):
    values = source_fixture(tmp_path)
    result = handoff.source_material(tmp_path)
    assert result["adaptation_story"] == values["story/r1.json"]
    assert result["adaptation_story_status"] == "exported_unverified"
    assert result["adaptation_readiness"] == "not_checked_use_cine_verify_report"
    assert result["can_claim_reviewed"] is False
    assert (
        result["source_input_hashes"]["story/r1.json"]
        == hashlib.sha256((tmp_path / "story/r1.json").read_bytes()).hexdigest()
    )


def test_adaptation_export_rejects_story_from_another_revision(handoff, tmp_path):
    values = source_fixture(tmp_path)
    draft = values["story/r1.json"]
    draft["revision_id"] = "old"
    (tmp_path / "story/r1.json").write_text(json.dumps(draft), encoding="utf-8")
    with pytest.raises(ValueError, match="STORY_REVISION_MISMATCH"):
        handoff.source_material(tmp_path)


def test_missing_story_is_not_reported_ready(handoff, tmp_path):
    source_fixture(tmp_path)
    (tmp_path / "story/r1.json").unlink()
    result = handoff.source_material(tmp_path)
    assert result["adaptation_story_status"] == "missing"
    assert "adaptation_story" not in result
    assert result["can_claim_reviewed"] is False
