"""Durable generation submissions against an offline idempotent V3 stand-in."""

import asyncio
import copy
import json
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import pytest
import respx

from agentnexus.seedance.bridge import execute_seedance_canvas_edit
from agentnexus.seedance.generation_requests import GenerationRequest
from agentnexus.seedance.production_gate import ProductionRejected

SERVER = "http://server"


@pytest.fixture
def generation(tmp_path, monkeypatch):
    (tmp_path / "production").mkdir()
    prompt = {"value": "Approved character sheet"}

    async def check(*_args, **_kwargs):
        return {
            "prompt": prompt["value"],
            "input_hashes": prompt.get("input_hashes", {}),
            "report_paths": ["receipt.json"],
        }

    monkeypatch.setattr("agentnexus.seedance.production_gate.validate_submission", check)
    v3 = AsyncMock()
    v3.get_snapshot.return_value = {
        "revision": 3,
        "nodes": [
            {
                "id": "node",
                "data": {
                    "prompt": prompt["value"],
                    "model": "gpt-image-2",
                    "aspectRatio": "16:9",
                    "production_stage": "cast",
                    "production_pointer": "/characters/0/image/sheet",
                },
            }
        ],
    }
    v3.get_node_references.return_value = [
        {"id": "node:ref", "kind": "image", "url": "/v3/storage/aaa"}
    ]
    v3.validate_generation.return_value = {"validated": True}
    v3.submit_command.return_value = {
        "accepted": True,
        "response": {
            "job": {"id": "job-one", "projectId": "project", "kind": "image", "status": "queued"}
        },
    }
    options = {
        "project_id": "project",
        "node_id": "node",
        "generation_kind": "image",
        "generation_allowed": True,
        "generation_request_id": "plan-1:cast-1",
        "production_dir": "production",
        "production_stage": "cast",
        "production_pointer": "/characters/0/image/sheet",
        "seedance_client": v3,
    }
    return tmp_path, prompt, v3, options


def session(path):
    respx.get(SERVER + "/v1/sessions/topic").respond(
        200,
        json={
            "id": "topic",
            "workspace": str(path),
            "labels": {"seedance.project_id": "project"},
        },
    )


async def submit(server, options, **changes):
    return await execute_seedance_canvas_edit(
        server, "topic", "submit_generation", **{**options, **changes}
    )


@pytest.mark.asyncio
@respx.mock
async def test_saved_receipt_survives_new_client_without_another_submission(generation):
    path, _prompt, v3, options = generation
    session(path)
    async with httpx.AsyncClient(base_url=SERVER) as server:
        first = await submit(server, options)
    assert first["status"] == "submitted"
    assert first["generation_request_id"] == options["generation_request_id"]
    saved = json.loads(Path(first["receipt_path"]).read_text(encoding="utf-8"))
    assert saved["command"]["idempotencyKey"] == first["command_id"]
    assert saved["state"] == "submitted"
    restarted = AsyncMock()
    restarted.get_snapshot.return_value = v3.get_snapshot.return_value
    restarted.get_node_references.return_value = v3.get_node_references.return_value
    async with httpx.AsyncClient(base_url=SERVER) as server:
        second = await submit(server, options, seedance_client=restarted)
    assert second["job"] == first["job"] and second["replayed"] is True
    restarted.submit_command.assert_not_called()
    v3.submit_command.assert_awaited_once()


@pytest.mark.asyncio
@respx.mock
@pytest.mark.parametrize("crash", [False, True])
async def test_lost_response_or_process_exit_replays_identical_command(generation, crash):
    path, _prompt, v3, options = generation
    session(path)
    accepted, commands = {}, []

    async def backend(_project, command):
        commands.append(copy.deepcopy(command))
        accepted.setdefault(
            command["idempotencyKey"], {"id": "one-paid-job", "projectId": "project"}
        )
        if len(commands) == 1:
            if crash:
                raise asyncio.CancelledError()
            raise httpx.ReadTimeout("accepted but response lost")
        return {"accepted": True, "response": {"job": accepted[command["idempotencyKey"]]}}

    v3.submit_command.side_effect = backend
    async with httpx.AsyncClient(base_url=SERVER) as server:
        if crash:
            with pytest.raises(asyncio.CancelledError):
                await submit(server, options)
        else:
            first = await submit(server, options)
            assert first["status"] == "unknown"
            assert first["request_state"] == "unknown"
        blocked = await submit(server, options, generation_request_id="new-id-must-not-work")
        assert blocked["error_code"] == "CINE_GENERATION_OUTCOME_UNKNOWN"
        assert len(commands) == 1
        omitted = await submit(server, options, generation_request_id=None)
        assert omitted["error_code"] == "CINE_GENERATION_OUTCOME_UNKNOWN"
        assert len(commands) == 1
        restored = await submit(server, options)
    assert restored["status"] == "submitted" and restored["replayed"] is True
    assert commands[0] == commands[1]
    assert len(accepted) == 1


@pytest.mark.asyncio
@respx.mock
@pytest.mark.parametrize(
    "change", ["prompt", "model", "references", "duration", "aspect", "canvas_prompt", "upstream"]
)
async def test_same_identity_rejects_changed_effective_inputs(generation, change):
    path, prompt, v3, options = generation
    session(path)
    async with httpx.AsyncClient(base_url=SERVER) as server:
        assert (await submit(server, options))["status"] == "submitted"
        data = v3.get_snapshot.return_value["nodes"][0]["data"]
        if change == "upstream":
            prompt["input_hashes"] = {"cast.json": "changed-upstream-proof"}
        elif change == "prompt":
            prompt["value"] = "Different canonical prompt"
        elif change == "model":
            data["model"] = "different-model"
        elif change == "duration":
            data["durationSec"] = 10
        elif change == "aspect":
            data["aspectRatio"] = "9:16"
        elif change == "canvas_prompt":
            data["prompt"] = "A human edited this canvas card"
        else:
            v3.get_node_references.return_value[0]["url"] = "/v3/storage/changed"
        result = await submit(server, options)
    assert result["error_code"] == "CINE_GENERATION_REQUEST_CONFLICT"
    v3.submit_command.assert_awaited_once()


@pytest.mark.asyncio
@respx.mock
async def test_pre_submit_input_change_rejects_without_spending(generation):
    path, _prompt, v3, options = generation
    session(path)
    original = v3.get_snapshot.return_value
    changed = copy.deepcopy(original)
    changed["nodes"][0]["data"]["model"] = "changed-during-validation"
    v3.get_snapshot.side_effect = [original, original, changed]
    async with httpx.AsyncClient(base_url=SERVER) as server:
        result = await submit(server, options)
    assert result["error_code"] == "CINE_GENERATION_REQUEST_CONFLICT"
    v3.submit_command.assert_not_called()


@pytest.mark.asyncio
@respx.mock
async def test_optimized_prompt_is_recoverable_after_lost_response(generation):
    path, prompt, v3, options = generation
    session(path)
    data = v3.get_snapshot.return_value["nodes"][0]["data"]
    from agentnexus.seedance.generation_requests import open_generation_request

    async with httpx.AsyncClient(base_url=SERVER) as server:
        request = await open_generation_request(
            server,
            "topic",
            v3,
            request_id=options["generation_request_id"],
            project_id="project",
            production_dir="production",
            proof={"prompt": prompt["value"]},
            intent={
                "node_id": "node",
                "generation_kind": "image",
                "prompt": prompt["value"],
                "model": None,
                "aspect_ratio": None,
                "duration_seconds": None,
                "production_stage": "cast",
                "production_pointer": "/characters/0/image/sheet",
                "expected_continuation_job_id": None,
            },
        )
        request.record["optimized_prompt"] = "Optimized approved prompt"
        request.save()
        request.close()
    data.update(
        prompt="Optimized approved prompt",
        sourcePrompt=prompt["value"],
        promptProvenance={"version": 1},
    )
    response = v3.submit_command.return_value
    v3.submit_command.side_effect = [httpx.ReadTimeout("lost"), response]
    async with httpx.AsyncClient(base_url=SERVER) as server:
        assert (await submit(server, options))["status"] == "unknown"
        recovered = await submit(server, options)
    assert recovered["status"] == "submitted"
    assert v3.submit_command.call_args.args[1]["input"]["prompt"] == "Optimized approved prompt"
    v3.optimize_prompt.assert_not_called()


def test_request_lock_and_corrupt_receipt_fail_closed(tmp_path):
    first = GenerationRequest(tmp_path, "request", "project", {"node_id": "node"}, {})
    first.acquire()
    second = GenerationRequest(tmp_path, "request", "project", {"node_id": "node"}, {})
    with pytest.raises(ProductionRejected, match="CINE_GENERATION_REQUEST_IN_PROGRESS"):
        second.acquire()
    first.close()
    first.path.write_text("invalid", encoding="utf-8")
    with pytest.raises(ProductionRejected, match="CINE_GENERATION_RECEIPT_INVALID"):
        second.acquire()


@pytest.mark.asyncio
@respx.mock
@pytest.mark.parametrize("request_id", ["../escape", "", 123, "a" * 129])
async def test_request_identity_is_validated(generation, request_id):
    path, _prompt, v3, options = generation
    session(path)
    async with httpx.AsyncClient(base_url=SERVER) as server:
        result = await submit(server, options, generation_request_id=request_id)
    assert result["error_code"] == "CINE_GENERATION_REQUEST_ID_INVALID"
    v3.submit_command.assert_not_called()


@pytest.mark.asyncio
@respx.mock
async def test_known_rejection_does_not_become_unknown_or_spend_on_replay(generation):
    from agentnexus.seedance.client import SeedanceError

    path, _prompt, v3, options = generation
    session(path)
    v3.submit_command.side_effect = SeedanceError(
        "V3 rejected", code="GENERATION_INPUT_INVALID", status_code=422
    )
    async with httpx.AsyncClient(base_url=SERVER) as server:
        rejected = await submit(server, options)
        assert rejected["status"] == "rejected" and rejected["request_state"] == "rejected"
        assert rejected["error_code"] == "GENERATION_INPUT_INVALID"
        replay = await submit(server, options)
    assert replay["replayed"] is True
    v3.submit_command.assert_awaited_once()


@pytest.mark.asyncio
@respx.mock
async def test_tampered_saved_command_cannot_be_replayed(generation):
    path, _prompt, v3, options = generation
    session(path)
    v3.submit_command.side_effect = httpx.ReadTimeout("lost")
    async with httpx.AsyncClient(base_url=SERVER) as server:
        pending = await submit(server, options)
        receipt_path = Path(pending["receipt_path"])
        saved = json.loads(receipt_path.read_text(encoding="utf-8"))
        saved["command"]["type"] = "canvas.delete_node"
        receipt_path.write_text(json.dumps(saved), encoding="utf-8")
        rejected = await submit(server, options)
    assert rejected["error_code"] == "CINE_GENERATION_RECEIPT_INVALID"
    v3.submit_command.assert_awaited_once()


@pytest.mark.asyncio
@respx.mock
@pytest.mark.parametrize("prior", [None, "submitted", "unknown"])
async def test_upstream_change_during_final_snapshot_invalidates_proof(generation, prior):
    import hashlib

    path, prompt, v3, options = generation
    session(path)
    source = path / "production" / "cast.json"
    source.write_text("approved upstream", encoding="utf-8")
    prompt["input_hashes"] = {str(source): hashlib.sha256(source.read_bytes()).hexdigest()}
    async with httpx.AsyncClient(base_url=SERVER) as server:
        if prior == "unknown":
            v3.submit_command.side_effect = httpx.ReadTimeout("lost")
        if prior is not None:
            assert (await submit(server, options))["status"] == prior
        before = v3.submit_command.await_count
        snapshots = 0

        async def snapshot(_project):
            nonlocal snapshots
            snapshots += 1
            if snapshots == (3 if prior is None else 1):
                source.write_text("edited during snapshot read", encoding="utf-8")
            return v3.get_snapshot.return_value

        v3.get_snapshot.side_effect = snapshot
        result = await submit(server, options)
    assert result["error_code"] == "CINE_INPUTS_CHANGED"
    assert v3.submit_command.await_count == before


@pytest.fixture
def continuation(generation):
    path, prompt, v3, options = generation
    snapshot = v3.get_snapshot.return_value
    snapshot["nodes"][0]["data"].update(
        production_stage="storyboard",
        production_pointer="/episodes/0/segments/0/h3Prompt",
        lastFrameContinuation=True,
    )
    snapshot["nodes"].extend(
        [
            {"id": "previous", "data": {"activeOutputRef": "local://source-video"}},
            {
                "id": "tail",
                "type": "image",
                "data": {
                    "continuationSourceNodeId": "previous",
                    "continuationTargetNodeId": "node",
                    "continuationSourceJobId": "previous-job",
                    "continuationSourceOutputRef": "local://source-video",
                    "activeOutputRef": "local://frame",
                },
            },
        ]
    )
    snapshot["edges"] = [{"kind": "sequence", "from": "previous", "to": "node"}]
    v3.get_node_references.return_value = [
        {"id": "node:tail", "kind": "image", "url": "/v3/storage/frame"}
    ]
    v3.get_job.return_value = {
        "id": "previous-job",
        "projectId": "project",
        "nodeId": "previous",
        "kind": "video",
        "status": "succeeded",
        "outputRefs": ["local://source-video"],
    }
    options.update(
        generation_kind="video",
        production_stage="storyboard",
        production_pointer="/episodes/0/segments/0/h3Prompt",
        expected_continuation_job_id="previous-job",
    )
    return path, prompt, v3, options


@pytest.mark.asyncio
@respx.mock
async def test_continuation_preflight_and_submission_bind_the_selected_source_job(continuation):
    path, _prompt, v3, options = continuation
    session(path)
    async with httpx.AsyncClient(base_url=SERVER) as server:
        result = await execute_seedance_canvas_edit(
            server, "topic", "validate_generation", **options
        )
        assert result["status"] == "validated"
        v3.submit_command.assert_not_called()
        result = await submit(server, options)
    assert result["status"] == "submitted"
    saved = json.loads(Path(result["receipt_path"]).read_text(encoding="utf-8"))
    assert saved["intent"]["expected_continuation_job_id"] == "previous-job"
    assert saved["context"]["continuation"]["source_job_id"] == "previous-job"
    assert saved["command"]["expectedRevision"] == 3
    assert saved["command"]["expectedContinuationJobId"] == "previous-job"


@pytest.mark.asyncio
@respx.mock
@pytest.mark.parametrize("change", ["job", "node", "output", "reference", "status", "edge"])
async def test_stale_continuation_is_rejected_before_paid_submission(continuation, change):
    path, _prompt, v3, options = continuation
    session(path)
    snapshot = v3.get_snapshot.return_value
    if change == "job":
        snapshot["nodes"][2]["data"]["continuationSourceJobId"] = "replacement-job"
    elif change == "node":
        v3.get_job.return_value["nodeId"] = "another-node"
    elif change == "output":
        snapshot["nodes"][1]["data"]["activeOutputRef"] = "local://replacement-video"
    elif change == "reference":
        v3.get_node_references.return_value[0]["url"] = "/v3/storage/older-frame"
    elif change == "status":
        v3.get_job.return_value["status"] = "running"
    else:
        snapshot["edges"] = []
    async with httpx.AsyncClient(base_url=SERVER) as server:
        result = await submit(server, options)
    assert result["error_code"] == "CINE_CONTINUATION_SOURCE_MISMATCH"
    v3.submit_command.assert_not_called()


@pytest.mark.asyncio
@respx.mock
async def test_last_frame_replacement_during_submit_is_rejected(continuation):
    path, _prompt, v3, options = continuation
    session(path)
    original = v3.get_snapshot.return_value
    changed = copy.deepcopy(original)
    changed["nodes"][2]["data"]["continuationSourceJobId"] = "replacement-job"
    v3.get_snapshot.side_effect = [original, original, original, changed]
    async with httpx.AsyncClient(base_url=SERVER) as server:
        result = await submit(server, options)
    assert result["error_code"] == "CINE_CONTINUATION_SOURCE_MISMATCH"
    v3.submit_command.assert_not_called()
