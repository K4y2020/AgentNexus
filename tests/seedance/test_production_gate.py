import hashlib
import json
import shutil
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import pytest
import respx

from agentnexus.seedance.bridge import execute_seedance_canvas_edit
from agentnexus.seedance.client import SeedanceError
from agentnexus.seedance.production_gate import (
    ProductionRejected,
    assert_current,
    storyboard_state_requirements,
    validate_submission,
)

SKILLS = Path(__file__).resolve().parents[2] / "examples/cine/skills"
SERVER = "http://localhost:6767"


@pytest.fixture
def bundle(tmp_path):
    directory = tmp_path / "production"
    directory.mkdir()
    for stage, skill in {
        "outline": "cine-outline",
        "cast": "cine-characters",
        "art": "cine-art",
        "script": "cine-script",
        "storyboard": "cine-storyboard",
    }.items():
        example = next((SKILLS / skill / "examples").glob(f"*-{stage}.json"))
        shutil.copyfile(example, directory / f"{stage}.json")
    shutil.copyfile(
        next((SKILLS / "cine-characters/examples").glob("*.txt")), tmp_path / "source.txt"
    )
    return directory


def mock_session(workspace):
    respx.get(f"{SERVER}/v1/sessions/topic").respond(
        200,
        json={
            "id": "topic",
            "workspace": str(workspace),
            "labels": {"seedance.project_id": "project"},
        },
    )


def args():
    return {
        "skills_dir": SKILLS,
        "production_dir": "production",
        "source_text": "source.txt",
        "production_stage": "cast",
        "production_pointer": "/characters/0/image/sheet",
        "generation_kind": "image",
        "project_id": "project",
    }


def test_storyboard_state_requirements_resolve_asset_nodes():
    board = {
        "stateContractVersion": 1,
        "episodes": [{"segments": [{"cuts": [{"characterStates": {"C01": "home_morning", "C02": "default"}}]}]}],
    }
    cast = {"characters": [{"id": "C01", "states": [{"id": "home_morning", "assetNodeId": "node_home"}]}, {"id": "C02"}]}
    assert storyboard_state_requirements(board, cast, "/episodes/0/segments/0/h3Prompt") == [
        {"character": "C01", "state": "home_morning", "asset_node_id": "node_home"}
    ]
    cast["characters"][0]["states"][0].pop("assetNodeId")
    with pytest.raises(ProductionRejected, match="CINE_CHARACTER_STATE_ASSET_REQUIRED"):
        storyboard_state_requirements(board, cast, "/episodes/0/segments/0/h3Prompt")


@pytest.mark.asyncio
@respx.mock
async def test_unknown_reference_and_cross_topic_rejected_before_validation(bundle):
    mock_session(bundle.parent)
    async with httpx.AsyncClient(base_url=SERVER) as client:
        for change, code in [
            ({"production_pointer": "/summary"}, "CINE_PROMPT_REFERENCE_REQUIRED"),
            ({"project_id": "other"}, "CINE_PROJECT_BINDING_MISMATCH"),
            ({"prompt": "unrelated prompt"}, "CINE_PROMPT_MISMATCH"),
            ({"generation_kind": "video"}, "CINE_GENERATION_KIND_MISMATCH"),
        ]:
            with pytest.raises(ProductionRejected, match=code):
                await validate_submission(client, "topic", **{**args(), **change})


@pytest.mark.asyncio
@respx.mock
async def test_workspace_escape_is_not_an_alternate_input(bundle):
    mock_session(bundle)
    async with httpx.AsyncClient(base_url=SERVER) as client:
        with pytest.raises(ProductionRejected, match="CINE_PRODUCTION_PATH_ESCAPE"):
            await validate_submission(client, "topic", **{**args(), "production_dir": ".."})


@pytest.mark.asyncio
@respx.mock
@pytest.mark.skipif(not shutil.which("node"), reason="requires native Node validators")
async def test_real_validation_freshness_and_no_trust_in_latest_report(bundle):
    mock_session(bundle.parent)
    report = bundle / ".cine-validation"
    report.mkdir()
    (report / "latest.json").write_text('{"status":"native_validated"}')
    async with httpx.AsyncClient(base_url=SERVER) as client:
        proof = await validate_submission(client, "topic", **args())
        assert len(proof["report_paths"]) == 1
        assert_current(proof)
        (bundle / "cast.json").write_text('{"source":"changed", "characters":[]}')
        with pytest.raises(ProductionRejected, match="CINE_INPUTS_CHANGED"):
            assert_current(proof)
        with pytest.raises(ProductionRejected, match="CINE_PROMPT_REFERENCE_INVALID"):
            await validate_submission(client, "topic", **args())


@pytest.mark.asyncio
@respx.mock
@pytest.mark.skipif(not shutil.which("node"), reason="requires native Node validators")
async def test_bridge_rejects_bad_native_data_without_v3_command(bundle):
    mock_session(bundle.parent)
    doc = json.loads((bundle / "cast.json").read_text(encoding="utf-8"))
    doc["characters"][0].pop("persona")
    prompt = doc["characters"][0]["image"]["sheet"]
    (bundle / "cast.json").write_text(json.dumps(doc), encoding="utf-8")
    v3 = AsyncMock()
    async with httpx.AsyncClient(base_url=SERVER) as client:
        result = await execute_seedance_canvas_edit(
            client,
            "topic",
            "submit_generation",
            project_id="project",
            prompt=prompt,
            generation_allowed=True,
            generation_kind="image",
            production_dir="production",
            source_text="source.txt",
            production_stage="cast",
            production_pointer="/characters/0/image/sheet",
            trusted_skills_dir=SKILLS,
            seedance_client=v3,
        )
    assert result["error_code"] == "CINE_NATIVE_VALIDATION_FAILED"
    v3.submit_command.assert_not_called()


@pytest.mark.asyncio
@respx.mock
@pytest.mark.skipif(not shutil.which("node"), reason="requires native Node validators")
async def test_validation_only_and_real_submission_share_gate(bundle):
    mock_session(bundle.parent)
    prompt = json.loads((bundle / "cast.json").read_text(encoding="utf-8"))["characters"][0][
        "image"
    ]["sheet"]
    v3 = AsyncMock()
    v3.submit_command.return_value = {"accepted": True, "response": {"job": {"id": "actual-job"}}}
    v3.validate_generation.return_value = {
        "validated": True,
        "model": "test-image",
        "referenceCount": 0,
    }
    v3.get_generation_models.return_value = {"models": [{"value": "test-image", "kind": "image"}]}
    v3.get_snapshot.return_value = {"jobs": [{"id": "new-job"}]}
    async with httpx.AsyncClient(base_url=SERVER) as client:
        kwargs = {
            "project_id": "project",
            "prompt": prompt,
            "production_dir": "production",
            "source_text": "source.txt",
            "production_stage": "cast",
            "production_pointer": "/characters/0/image/sheet",
            "trusted_skills_dir": SKILLS,
            "seedance_client": v3,
            "model": "test-image",
        }
        result = await execute_seedance_canvas_edit(
            client, "topic", "validate_generation", **kwargs
        )
        assert result["submitted"] is False
        v3.submit_command.assert_not_called()
        result = await execute_seedance_canvas_edit(
            client, "topic", "submit_generation", generation_allowed=True, **kwargs
        )
        assert result["outcome"] == "succeeded"
        assert result["validation_reports"]
        assert v3.submit_command.call_args.args[1]["input"]["prompt"] == prompt
        assert v3.submit_command.call_args.args[1]["kind"] == "image"
        assert result["status"] == "submitted"
        assert result["job"]["id"] == "actual-job"


@pytest.mark.asyncio
@respx.mock
async def test_cast_legacy_name_needs_no_outline_and_does_not_rewrite(bundle):
    mock_session(bundle.parent)
    (bundle / "outline.json").unlink()
    legacy = bundle / "title-cast.json"
    (bundle / "cast.json").rename(legacy)
    before = legacy.read_bytes()
    async with httpx.AsyncClient(base_url=SERVER) as client:
        proof = await validate_submission(client, "topic", **args())
    assert len(proof["report_paths"]) == 1
    assert legacy.read_bytes() == before
    assert not (bundle / "cast.json").exists()
    assert not (bundle / "outline.json").exists()
    legacy.with_name("another-cast.json").write_bytes(before)
    async with httpx.AsyncClient(base_url=SERVER) as client:
        with pytest.raises(ProductionRejected, match="CINE_PRODUCTION_ARTIFACT_REQUIRED"):
            await validate_submission(client, "topic", **args())


@pytest.mark.asyncio
@respx.mock
async def test_explicit_binding_and_binding_freshness_share_submission_gate(bundle):
    mock_session(bundle.parent)
    selected = bundle / "selected-cast.json"
    selected.write_bytes((bundle / "cast.json").read_bytes())
    (bundle / "cast.json").write_text('{"source":"wrong","characters":[]}', encoding="utf-8")
    async with httpx.AsyncClient(base_url=SERVER) as client:
        with pytest.raises(ProductionRejected, match="CINE_PRODUCTION_ARTIFACT_REQUIRED"):
            await validate_submission(client, "topic", **args())
        manifest = bundle / "production.json"
        manifest.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "artifacts": {"cast": {"path": selected.name}},
                }
            ),
            encoding="utf-8",
        )
        proof = await validate_submission(client, "topic", **args())
    assert (
        proof["input_hashes"][str(selected)] == hashlib.sha256(selected.read_bytes()).hexdigest()
    )
    assert str(bundle / "cast.json") not in proof["input_hashes"]
    assert_current(proof)
    manifest.write_text('{"artifacts":{"cast":{"path":"cast.json"}}}', encoding="utf-8")
    with pytest.raises(ProductionRejected, match="CINE_INPUTS_CHANGED"):
        assert_current(proof)


def test_proof_rejects_a_new_manifest_after_validation(tmp_path):
    manifest = tmp_path / "production.json"
    proof = {"input_hashes": {str(manifest): None}}
    assert_current(proof)
    manifest.write_text("{}", encoding="utf-8")
    with pytest.raises(ProductionRejected, match="CINE_INPUTS_CHANGED"):
        assert_current(proof)


@pytest.mark.asyncio
@respx.mock
async def test_v3_preflight_error_is_returned_without_submitting(bundle):
    mock_session(bundle.parent)
    v3 = AsyncMock()
    v3.validate_generation.side_effect = SeedanceError(
        "Select a model", code="GENERATION_INPUT_INVALID"
    )
    async with httpx.AsyncClient(base_url=SERVER) as client:
        result = await execute_seedance_canvas_edit(
            client,
            "topic",
            "validate_generation",
            project_id="project",
            production_dir="production",
            source_text="source.txt",
            production_stage="cast",
            production_pointer="/characters/0/image/sheet",
            trusted_skills_dir=SKILLS,
            seedance_client=v3,
        )
    assert result["error_code"] == "GENERATION_INPUT_INVALID"
    v3.submit_command.assert_not_called()


@pytest.mark.asyncio
@respx.mock
@pytest.mark.parametrize("case", ["success", "missing", "unsupported", "changed"])
async def test_turnaround_sends_portrait_reference_without_mutating_portrait(bundle, case):
    mock_session(bundle.parent)
    prompt = json.loads((bundle / "cast.json").read_text(encoding="utf-8"))["characters"][0][
        "image"
    ]["sheet"]
    v3 = AsyncMock()
    v3.get_snapshot.return_value = {
        "revision": 1,
        "nodes": [
            {
                "id": "turnaround",
                "data": {
                    "prompt": prompt,
                    "production_stage": "cast",
                    "production_pointer": "/characters/0/image/sheet",
                },
            }
        ],
    }
    v3.submit_command.return_value = {"accepted": True, "response": {"job": {"id": "new-job"}}}
    codes = {
        "missing": "GENERATION_PORTRAIT_REFERENCE_REQUIRED",
        "unsupported": "GENERATION_INPUT_INVALID",
        "changed": "GENERATION_NODE_CHANGED",
    }
    if case != "success":
        v3.submit_command.side_effect = SeedanceError("V3 rejected input", code=codes[case])
    async with httpx.AsyncClient(base_url=SERVER) as client:
        result = await execute_seedance_canvas_edit(
            client,
            "topic",
            "submit_generation",
            generation_allowed=True,
            project_id="project",
            node_id="turnaround",
            production_dir="production",
            source_text="source.txt",
            production_stage="cast",
            production_pointer="/characters/0/image/sheet",
            trusted_skills_dir=SKILLS,
            seedance_client=v3,
        )
    if case == "success":
        v3.submit_command.assert_awaited_once()
        cmd = v3.submit_command.call_args.args[1]
        assert cmd["type"] == "generation.submit"
        assert cmd["nodeId"] == "turnaround"
        assert cmd["input"] == {"prompt": prompt}
        assert cmd["expectedPrompt"] == prompt
    else:
        assert result["error_code"] == codes[case]
    v3.get_node_references.assert_not_called()
    v3.get_generation_models.assert_not_called()
    v3.get_snapshot.assert_awaited_once()


@pytest.mark.asyncio
@respx.mock
async def test_missing_binding_fields_cannot_use_old_generation_route():
    v3 = AsyncMock()
    async with httpx.AsyncClient(base_url=SERVER) as client:
        result = await execute_seedance_canvas_edit(
            client,
            "topic",
            "submit_generation",
            project_id="project",
            prompt="Generate",
            generation_allowed=True,
            seedance_client=v3,
        )
    assert result["error_code"] == "CINE_PRODUCTION_STAGE_REQUIRED"
    v3.submit_command.assert_not_called()
