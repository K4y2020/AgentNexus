import json
from unittest.mock import AsyncMock

import httpx
import pytest
import respx

from omnigent.seedance.production_gate import ProductionRejected
from omnigent.seedance.storyboard_import import import_storyboard


@pytest.mark.asyncio
@respx.mock
async def test_import_reads_native_files_and_forwards_targets_without_generation(tmp_path):
    doc = {"source": "Test", "episodes": [{"ep": 1}]}
    for name in ("storyboard.json", "script.json"):
        (tmp_path / name).write_text(json.dumps(doc), encoding="utf-8")
    respx.get("http://server/v1/sessions/topic").respond(
        200,
        json={
            "workspace": str(tmp_path),
            "labels": {"seedance.project_id": "project"},
        },
    )
    v3 = AsyncMock()
    v3.get_snapshot.return_value = {
        "nodes": [{"id": "summary", "revision": 1}, {"id": "ep1", "revision": 3}]
    }
    v3.submit_command.return_value = {"accepted": True, "response": {"verified": True}}
    async with httpx.AsyncClient(base_url="http://server") as server:
        result = await import_storyboard(
            server,
            "topic",
            v3,
            storyboard_file="storyboard.json",
            script_file="script.json",
            summary_node_id="summary",
            episode_nodes={"1": "ep1"},
        )
    assert result["generation_submitted"] is False
    command = v3.submit_command.call_args.args[1]
    assert command["type"] == "storyboard.import"
    assert command["document"] == doc
    assert command["nodeRevisions"] == {"summary": 1, "ep1": 3}
    v3.submit_command.assert_awaited_once()


@pytest.mark.asyncio
@respx.mock
async def test_import_rejects_cross_topic_before_reading_files(tmp_path):
    respx.get("http://server/v1/sessions/topic").respond(
        200,
        json={
            "workspace": str(tmp_path),
            "labels": {"seedance.project_id": "project"},
        },
    )
    v3 = AsyncMock()
    async with httpx.AsyncClient(base_url="http://server") as server:
        with pytest.raises(ProductionRejected, match="CINE_PROJECT_BINDING_MISMATCH"):
            await import_storyboard(
                server,
                "topic",
                v3,
                storyboard_file="unused.json",
                script_file="unused.json",
                summary_node_id="summary",
                episode_nodes={"1": "ep1"},
                project_id="other",
            )
    v3.submit_command.assert_not_called()


@pytest.mark.asyncio
@respx.mock
async def test_import_creates_missing_targets_deterministically(tmp_path):
    storyboard = {"source": "Test", "episodes": [{"ep": 1, "segments": []}]}
    script = {"source": "Test", "episodes": [{"ep": 1, "scenes": []}]}
    (tmp_path / "storyboard.json").write_text(json.dumps(storyboard), encoding="utf-8")
    (tmp_path / "script.json").write_text(json.dumps(script), encoding="utf-8")
    respx.get("http://server/v1/sessions/topic").respond(
        200, json={"workspace": str(tmp_path), "labels": {"seedance.project_id": "project"}}
    )
    v3 = AsyncMock()
    v3.get_snapshot.side_effect = [
        {"nodes": []},
        {
            "nodes": [
                {"id": "summary", "type": "text", "title": "Cine 分镜同步总卡", "revision": 1}
            ]
        },
        {
            "nodes": [
                {"id": "summary", "type": "text", "title": "Cine 分镜同步总卡", "revision": 1},
                {"id": "ep1", "type": "storyboard", "title": "EP01 分镜表", "revision": 1},
            ]
        },
    ]
    v3.submit_command.side_effect = [
        {"accepted": True},
        {"accepted": True},
        {"accepted": True, "response": {"verified": True}},
    ]
    async with httpx.AsyncClient(base_url="http://server") as server:
        result = await import_storyboard(
            server,
            "topic",
            v3,
            storyboard_file="storyboard.json",
            script_file="script.json",
            summary_node_id=None,
            episode_nodes=None,
        )
    assert result["verified"] is True
    command = v3.submit_command.await_args_list[-1].args[1]
    assert command["summaryNodeId"] == "summary"
    assert command["episodeNodes"] == {"1": "ep1"}
