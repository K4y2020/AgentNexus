import json
from unittest.mock import AsyncMock

import httpx
import pytest
import respx

from agentnexus.seedance.production_gate import ProductionRejected
from agentnexus.seedance.storyboard_import import build_structured_shots, import_storyboard


def test_build_structured_shots_preserves_dialogue_ownership():
    segment = {
        "id": "E01-02",
        "sceneIndex": 1,
        "cuts": [{
            "beats": [1, 2],
            "seconds": 4,
            "size": "medium",
            "camera": "Static Shot",
            "frame": "medium shot of the server room",
            "characters": ["C01", "C02"],
            "props": ["P01"],
        }],
    }
    script = {
        "episodes": [{"ep": 1, "scenes": [{
            "sceneId": "S01",
            "lighting": "cold blue light",
            "flow": [
                {"action": "The engineer looks toward the hologram."},
                {"speaker": "C02", "line": "先喝水。", "delivery": "calm"},
            ],
        }]}],
    }

    shots = build_structured_shots(segment, script, 1)

    assert shots[0]["dialogueBeats"] == [{
        "sourceRef": "script:1:1:2",
        "speaker": "C02",
        "exactText": "先喝水。",
        "delivery": "calm",
    }]
    assert shots[0]["visibleSubjects"] == ["C01", "C02"]
    assert shots[0]["referenceNeeds"] == ["P01"]


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


@pytest.mark.asyncio
@respx.mock
async def test_import_provisions_and_links_visual_assets(tmp_path):
    cast = {
        "characters": [
            {"id": "C01", "name": "林黛玉", "image": {"sheet": "16:9 sheet prompt for Lin Daiyu"}},
        ]
    }
    art = {
        "scenes": [
            {"id": "S01", "sceneIndex": 1, "name": "街头算命摊", "image": {"prompt": "scene prompt for stall"}},
        ]
    }
    storyboard = {
        "source": "Test",
        "episodes": [{
            "ep": 1,
            "segments": [{
                "id": "E01-01",
                "sceneIndex": 1,
                "cuts": [{"beats": [1, 1], "seconds": 2.5, "characters": ["C01"]}],
            }],
        }],
    }
    script = {
        "source": "Test",
        "episodes": [{
            "ep": 1,
            "scenes": [{"sceneId": "S01", "flow": [{"action": "Action beat"}]}],
        }],
    }
    (tmp_path / "cast.json").write_text(json.dumps(cast), encoding="utf-8")
    (tmp_path / "art.json").write_text(json.dumps(art), encoding="utf-8")
    (tmp_path / "storyboard.json").write_text(json.dumps(storyboard), encoding="utf-8")
    (tmp_path / "script.json").write_text(json.dumps(script), encoding="utf-8")

    respx.get("http://server/v1/sessions/topic").respond(
        200, json={"workspace": str(tmp_path), "labels": {"seedance.project_id": "project"}}
    )
    v3 = AsyncMock()
    current_nodes = [
        {"id": "summary", "type": "text", "title": "Cine 分镜同步总卡", "revision": 1},
        {"id": "ep1", "type": "storyboard", "title": "EP01 分镜表", "revision": 1},
    ]

    async def mock_submit(project_id, cmd):
        if cmd.get("type") == "canvas.create_node":
            new_id = f"node_{len(current_nodes) + 1}"
            current_nodes.append({
                "id": new_id,
                "type": cmd.get("nodeType"),
                "title": cmd.get("title"),
                "data": cmd.get("data", {}),
                "revision": 1,
            })
        return {"accepted": True, "response": {"verified": True}}

    v3.submit_command.side_effect = mock_submit
    v3.get_snapshot.side_effect = lambda project_id: {"nodes": list(current_nodes)}

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
    assert result["status"] == "synced"
    assert result["receipt"]["character_assets_linked"] >= 1
    assert result["receipt"]["scene_assets_linked"] >= 1

    command_types = [c.args[1]["type"] for c in v3.submit_command.call_args_list]
    assert "canvas.create_node" in command_types
    assert "canvas.connect" in command_types


@pytest.mark.asyncio
@respx.mock
async def test_import_rejects_when_character_is_missing_from_cast(tmp_path):
    cast = {"characters": []}  # C99 missing from cast.json!
    storyboard = {
        "source": "Test",
        "episodes": [{
            "ep": 1,
            "segments": [{
                "id": "E01-01",
                "sceneIndex": 1,
                "cuts": [{"beats": [1, 1], "seconds": 2.5, "characters": ["C99"]}],
            }],
        }],
    }
    script = {
        "source": "Test",
        "episodes": [{
            "ep": 1,
            "scenes": [{"sceneId": "S01", "flow": [{"action": "Action beat"}]}],
        }],
    }
    (tmp_path / "cast.json").write_text(json.dumps(cast), encoding="utf-8")
    (tmp_path / "storyboard.json").write_text(json.dumps(storyboard), encoding="utf-8")
    (tmp_path / "script.json").write_text(json.dumps(script), encoding="utf-8")

    respx.get("http://server/v1/sessions/topic").respond(
        200, json={"workspace": str(tmp_path), "labels": {"seedance.project_id": "project"}}
    )
    v3 = AsyncMock()
    v3.get_snapshot.return_value = {
        "nodes": [
            {"id": "summary", "type": "text", "title": "Cine 分镜同步总卡", "revision": 1},
            {"id": "ep1", "type": "storyboard", "title": "EP01 分镜表", "revision": 1},
        ]
    }
    v3.submit_command.return_value = {"accepted": True, "response": {"verified": True}}

    async with httpx.AsyncClient(base_url="http://server") as server:
        with pytest.raises(ProductionRejected, match="CINE_IMPORT_VIDEO_CARD_PROJECTION_FAILED"):
            await import_storyboard(
                server,
                "topic",
                v3,
                storyboard_file="storyboard.json",
                script_file="script.json",
                summary_node_id="summary",
                episode_nodes={"1": "ep1"},
            )


