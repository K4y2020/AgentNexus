"""Unit and integration tests for Seedance V3 Bridge."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import respx

from omnigent.seedance.bridge import (
    SEEDANCE_BASE_URL_LABEL,
    SEEDANCE_PROJECT_LABEL,
    SEEDANCE_SESSION_LABEL,
    SEEDANCE_STATUS_LABEL,
    execute_seedance_agent_message,
    execute_seedance_canvas_edit,
    format_shot_contract_message,
    read_seedance_canvas_snapshot,
    read_matching_shots,
    resolve_or_create_topic_project_and_session,
)
from omnigent.seedance.client import SeedanceClient

_BASE = "http://127.0.0.1:8893"
_SERVER = "http://localhost:6767"


def test_format_shot_contract_generation_boundary():
    draft = format_shot_contract_message("Draft", generation_allowed=False)
    assert "unverified draft" in draft
    assert "generation_allowed is FALSE" in draft
    with pytest.raises(ValueError, match="CINE_SHOTS_REQUIRED"):
        format_shot_contract_message("Generate", generation_allowed=True)


def test_legacy_ledger_does_not_silently_fall_back(tmp_path):
    with pytest.raises(ValueError, match="CINE_BINDING_REQUIRED"):
        read_matching_shots(tmp_path, ["S01"])


@pytest.mark.asyncio
@respx.mock
async def test_resolve_new_topic_creates_project_and_session():
    conv_id = "conv_topic_123"
    # AgentNexus server mocks
    respx.get(f"{_SERVER}/v1/sessions/{conv_id}").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": conv_id,
                "purpose": "topic",
                "title": "Film Analysis Topic",
                "labels": {},
            },
        )
    )
    patch_route = respx.patch(f"{_SERVER}/v1/sessions/{conv_id}").mock(
        return_value=httpx.Response(200, json={"id": conv_id})
    )

    # Seedance API mocks
    respx.post(f"{_BASE}/v3/projects").mock(
        return_value=httpx.Response(201, json={"project": {"id": "proj_seed_1", "name": "Cine: Film Analysis Topic"}})
    )
    respx.post(f"{_BASE}/v3/projects/proj_seed_1/agent-sessions").mock(
        return_value=httpx.Response(201, json={"session": {"id": "sess_seed_1", "projectId": "proj_seed_1"}})
    )

    async with httpx.AsyncClient(base_url=_SERVER) as s_client:
        async with SeedanceClient(base_url=_BASE, api_key="secret") as sd_client:
            pid, sid, scope = await resolve_or_create_topic_project_and_session(
                s_client, conv_id, sd_client
            )
            assert pid == "proj_seed_1"
            assert sid == "sess_seed_1"
            assert scope == f"topic:{conv_id}"

            assert patch_route.called
            sent_labels = json.loads(patch_route.calls.last.request.content)["labels"]
            assert sent_labels[SEEDANCE_PROJECT_LABEL] == "proj_seed_1"
            assert sent_labels[SEEDANCE_SESSION_LABEL] == "sess_seed_1"
            assert sent_labels[SEEDANCE_STATUS_LABEL] == "bound"


@pytest.mark.asyncio
@respx.mock
async def test_resolve_existing_topic_reuses_project_and_session():
    conv_id = "conv_topic_existing"
    # AgentNexus session already has labels
    respx.get(f"{_SERVER}/v1/sessions/{conv_id}").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": conv_id,
                "purpose": "topic",
                "title": "Existing Topic",
                "labels": {
                    SEEDANCE_PROJECT_LABEL: "proj_existing_99",
                    SEEDANCE_SESSION_LABEL: "sess_existing_99",
                },
            },
        )
    )
    # Seedance snapshot verifies project exists
    respx.get(f"{_BASE}/v3/projects/proj_existing_99/snapshot").mock(
        return_value=httpx.Response(200, json={"snapshot": {"nodes": [], "jobs": []}})
    )

    async with httpx.AsyncClient(base_url=_SERVER) as s_client:
        async with SeedanceClient(base_url=_BASE, api_key="secret") as sd_client:
            pid, sid, scope = await resolve_or_create_topic_project_and_session(
                s_client, conv_id, sd_client
            )
            assert pid == "proj_existing_99"
            assert sid == "sess_existing_99"
            assert scope == f"topic:{conv_id}"


@pytest.mark.asyncio
@pytest.mark.parametrize("allowed", [False, True])
@respx.mock
async def test_legacy_agent_delegation_cannot_bypass_direct_gate(allowed):
    async with httpx.AsyncClient(base_url=_SERVER) as client:
        result = await execute_seedance_agent_message(
            server_client=client, conversation_id="topic", task="Generate or draft",
            generation_allowed=allowed,
        )
    assert result["error_code"] == "CINE_AGENT_DELEGATION_DISABLED"
    assert not respx.calls


@pytest.mark.asyncio
@respx.mock
async def test_read_seedance_canvas_snapshot_flow():
    conv_id = "conv_canvas_read"
    respx.get(f"{_SERVER}/v1/sessions/{conv_id}").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": conv_id,
                "labels": {
                    SEEDANCE_PROJECT_LABEL: "proj_read_1",
                },
            },
        )
    )
    respx.get(f"{_BASE}/v3/projects/proj_read_1/snapshot").mock(
        return_value=httpx.Response(
            200,
            json={
                "snapshot": {
                    "revision": 5,
                    "nodes": [
                        {
                            "id": "n_ch1",
                            "type": "image_prompt",
                            "title": "Character 1",
                            "status": "ready",
                            "data": {"prompt": "Turnaround sheet", "aspectRatio": "16:9"},
                        },
                        {
                            "id": "n_s01",
                            "type": "video_prompt",
                            "title": "S01 - Opening",
                            "status": "draft",
                            "data": {"brief": "Opening wide shot", "durationSec": 8},
                        },
                    ],
                    "edges": [
                        {"id": "e1", "from": "n_ch1", "to": "n_s01", "kind": "references"}
                    ],
                }
            },
        )
    )

    async with httpx.AsyncClient(base_url=_SERVER) as s_client:
        async with SeedanceClient(base_url=_BASE, api_key="secret") as sd_client:
            res = await read_seedance_canvas_snapshot(
                server_client=s_client,
                conversation_id=conv_id,
                seedance_client=sd_client,
            )
            assert res["status"] == "completed"
            assert res["bound"] is True
            assert res["counts"]["total_nodes"] == 2
            assert res["counts"]["edges"] == 1
            assert "Character 1" in res["summary_markdown"]
            assert "S01 - Opening" in res["summary_markdown"]


@pytest.mark.asyncio
@respx.mock
async def test_execute_seedance_canvas_edit_update_and_verification():
    conv_id = "conv_canvas_edit"
    respx.get(f"{_SERVER}/v1/sessions/{conv_id}").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": conv_id,
                "labels": {SEEDANCE_PROJECT_LABEL: "proj_edit_1"},
            },
        )
    )
    # Pre-check snapshot
    respx.get(f"{_BASE}/v3/projects/proj_edit_1/snapshot").mock(
        side_effect=[
            # First read (for expected_revision pre-check)
            httpx.Response(
                200,
                json={
                    "snapshot": {
                        "nodes": [
                            {"id": "node_s03", "title": "S03", "revision": 1, "data": {"prompt": "Old prompt"}}
                        ],
                    }
                },
            ),
            # Post-check snapshot (for read-after-write verification)
            httpx.Response(
                200,
                json={
                    "snapshot": {
                        "nodes": [
                            {"id": "node_s03", "title": "S03 Updated", "revision": 2, "data": {"prompt": "New cinematic prompt"}}
                        ],
                    }
                },
            ),
        ]
    )
    respx.post(f"{_BASE}/v3/projects/proj_edit_1/commands").mock(
        return_value=httpx.Response(200, json={"accepted": True, "cursor": 100})
    )

    async with httpx.AsyncClient(base_url=_SERVER) as s_client:
        async with SeedanceClient(base_url=_BASE, api_key="secret") as sd_client:
            # 1. Successful update with expected_revision check
            res = await execute_seedance_canvas_edit(
                server_client=s_client,
                conversation_id=conv_id,
                action="update_node",
                node_id="node_s03",
                title="S03 Updated",
                prompt="New cinematic prompt",
                expected_revision=1,
                seedance_client=sd_client,
            )
            assert res["status"] == "completed"
            assert res["verified"] is True
            assert res["new_revision"] == 2
            assert res["node"]["data"]["prompt"] == "New cinematic prompt"


@pytest.mark.asyncio
@respx.mock
async def test_execute_seedance_canvas_edit_safety_gates():
    conv_id = "conv_canvas_safe"
    respx.get(f"{_SERVER}/v1/sessions/{conv_id}").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": conv_id,
                "labels": {SEEDANCE_PROJECT_LABEL: "proj_safe_gate"},
            },
        )
    )

    async with httpx.AsyncClient(base_url=_SERVER) as s_client:
        async with SeedanceClient(base_url=_BASE, api_key="secret") as sd_client:
            # 1. Delete rejected when confirm=False
            del_res = await execute_seedance_canvas_edit(
                server_client=s_client,
                conversation_id=conv_id,
                action="delete_node",
                node_id="node_target",
                confirm=False,
                seedance_client=sd_client,
            )
            assert del_res["status"] == "rejected"
            assert "confirm=true" in del_res["error"]

            # 2. Submit generation rejected when generation_allowed=False
            gen_res = await execute_seedance_canvas_edit(
                server_client=s_client,
                conversation_id=conv_id,
                action="submit_generation",
                node_id="node_target",
                generation_allowed=False,
                seedance_client=sd_client,
            )
            assert gen_res["status"] == "rejected"
            assert "generation_allowed 为 false" in gen_res["error"]
