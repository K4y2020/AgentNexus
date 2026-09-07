import json
import shutil
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import pytest
import respx

from omnigent.seedance.bridge import execute_seedance_canvas_edit
from omnigent.seedance.production_gate import (
    ProductionRejected,
    assert_current,
    validate_submission,
)

SKILLS = Path(__file__).resolve().parents[2] / "examples/cine/skills"
SERVER = "http://localhost:6767"


@pytest.fixture
def bundle(tmp_path):
    directory = tmp_path / "production"
    directory.mkdir()
    for stage, skill in {
        "outline": "novel-outline",
        "cast": "novel-characters",
        "art": "novel-art",
        "script": "novel-script",
        "storyboard": "novel-storyboard",
    }.items():
        example = next((SKILLS / skill / "examples").glob(f"*-{stage}.json"))
        shutil.copyfile(example, directory / f"{stage}.json")
    shutil.copyfile(
        next((SKILLS / "novel-characters/examples").glob("*.txt")), tmp_path / "source.txt"
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


@pytest.mark.asyncio
@respx.mock
async def test_unknown_reference_and_cross_topic_rejected_before_validation(bundle):
    mock_session(bundle.parent)
    async with httpx.AsyncClient(base_url=SERVER) as client:
        for change, code in [
            ({"production_pointer": "/summary"}, "CINE_PROMPT_REFERENCE_REQUIRED"),
            ({"project_id": "other"}, "CINE_PROJECT_BINDING_MISMATCH"),
            ({"prompt": "unrelated prompt"}, "CINE_PROMPT_MISMATCH"),
            ({"generation_kind": "video"}, "CINE_PRODUCTION_STAGE_REQUIRED"),
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
        assert len(proof["report_paths"]) == 2
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
    v3.submit_command.return_value = {"accepted": True}
    v3.get_snapshot.return_value = {"jobs": [{"id": "new-job"}]}
    async with httpx.AsyncClient(base_url=SERVER) as client:
        kwargs = {
            "project_id": "project",
            "prompt": prompt,
            "generation_kind": "image",
            "production_dir": "production",
            "source_text": "source.txt",
            "production_stage": "cast",
            "production_pointer": "/characters/0/image/sheet",
            "trusted_skills_dir": SKILLS,
            "seedance_client": v3,
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
