import hashlib
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
        "cuts": [
            {
                "beats": [1, 2],
                "seconds": 4,
                "size": "medium",
                "camera": "Static Shot",
                "frame": "medium shot of the server room",
                "characters": ["C01", "C02"],
                "props": ["P01"],
            }
        ],
    }
    script = {
        "episodes": [
            {
                "ep": 1,
                "scenes": [
                    {
                        "sceneId": "S01",
                        "lighting": "cold blue light",
                        "flow": [
                            {"action": "The engineer looks toward the hologram."},
                            {"speaker": "C02", "line": "先喝水。", "delivery": "calm"},
                        ],
                    }
                ],
            }
        ],
    }

    shots = build_structured_shots(segment, script, 1)

    assert shots[0]["dialogueBeats"] == [
        {
            "sourceRef": "script:1:1:2",
            "speaker": "C02",
            "exactText": "先喝水。",
            "delivery": "calm",
        }
    ]
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
            {
                "id": "S01",
                "sceneIndex": 1,
                "name": "街头算命摊",
                "image": {"prompt": "scene prompt for stall"},
            },
        ]
    }
    storyboard = {
        "source": "Test",
        "episodes": [
            {
                "ep": 1,
                "segments": [
                    {
                        "id": "E01-01",
                        "sceneIndex": 1,
                        "cuts": [{"beats": [1, 1], "seconds": 2.5, "characters": ["C01"]}],
                    }
                ],
            }
        ],
    }
    script = {
        "source": "Test",
        "episodes": [
            {
                "ep": 1,
                "scenes": [{"sceneId": "S01", "flow": [{"action": "Action beat"}]}],
            }
        ],
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
            current_nodes.append(
                {
                    "id": new_id,
                    "type": cmd.get("nodeType"),
                    "title": cmd.get("title"),
                    "data": cmd.get("data", {}),
                    "revision": 1,
                }
            )
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


def _write_production(directory, **documents):
    directory.mkdir(parents=True, exist_ok=True)
    for name, document in documents.items():
        (directory / f"{name}.json").write_text(json.dumps(document), encoding="utf-8")


def _canvas(nodes, edges=None, revision=None):
    """AsyncMock V3 client whose snapshots reflect the nodes and edges it has been sent."""
    state = {"nodes": list(nodes), "edges": list(edges or [])}
    v3 = AsyncMock()

    async def submit(project_id, cmd):
        if cmd.get("type") == "canvas.create_node":
            state["nodes"].append(
                {
                    "id": f"node_{len(state['nodes']) + 1}",
                    "type": cmd.get("nodeType"),
                    "title": cmd.get("title"),
                    "data": cmd.get("data", {}),
                    "revision": 1,
                }
            )
        elif cmd.get("type") == "canvas.connect":
            state["edges"].append({"from": cmd["from"], "to": cmd["to"], "kind": cmd["kind"]})
        return {"accepted": True, "response": {"verified": True}}

    def snapshot(project_id):
        snap = {"nodes": list(state["nodes"]), "edges": list(state["edges"])}
        if revision is not None:
            snap["revision"] = revision
        return snap

    v3.submit_command.side_effect = submit
    v3.get_snapshot.side_effect = snapshot
    return v3, state


async def _run_import(
    tmp_path, v3, episode_nodes, *, storyboard_file="storyboard.json", script_file="script.json"
):
    respx.get("http://server/v1/sessions/topic").respond(
        200, json={"workspace": str(tmp_path), "labels": {"seedance.project_id": "project"}}
    )
    async with httpx.AsyncClient(base_url="http://server") as server:
        return await import_storyboard(
            server,
            "topic",
            v3,
            storyboard_file=storyboard_file,
            script_file=script_file,
            summary_node_id="summary",
            episode_nodes=episode_nodes,
        )


def _segment(seg_id, *, scene_index=1, characters=("C01",), prompt=""):
    return {
        "id": seg_id,
        "sceneIndex": scene_index,
        "h3Prompt": prompt,
        "cuts": [{"beats": [1, 1], "seconds": 2.5, "characters": list(characters)}],
    }


_TARGETS = [
    {"id": "summary", "type": "text", "title": "Cine 分镜同步总卡", "revision": 1},
    {"id": "ep1", "type": "storyboard", "title": "EP01 分镜表", "revision": 1},
]
_CAST = {"characters": [{"id": "C01", "name": "林黛玉", "image": {"sheet": "16:9 sheet prompt"}}]}
_ART = {
    "scenes": [
        {"id": "S01", "sceneIndex": 1, "name": "街头算命摊", "image": {"prompt": "scene prompt"}}
    ]
}
_SCRIPT = {
    "source": "Test",
    "episodes": [
        {"ep": 1, "scenes": [{"sceneId": "S01", "flow": [{"action": "Action beat"}]}]},
        {"ep": 2, "scenes": [{"sceneId": "S01", "flow": [{"action": "Action beat"}]}]},
    ],
}


@pytest.mark.asyncio
@respx.mock
async def test_import_rejects_missing_character_before_any_canvas_write(tmp_path):
    storyboard = {
        "source": "Test",
        "episodes": [{"ep": 1, "segments": [_segment("E01-01", characters=["C99"])]}],
    }
    _write_production(
        tmp_path, cast={"characters": []}, art=_ART, storyboard=storyboard, script=_SCRIPT
    )
    v3, _ = _canvas(_TARGETS)

    with pytest.raises(ProductionRejected, match="CINE_IMPORT_ASSETS_MISSING") as rejected:
        await _run_import(tmp_path, v3, {"1": "ep1"})

    assert any("C99" in problem for problem in rejected.value.detail["problems"])
    v3.submit_command.assert_not_called()


@pytest.mark.asyncio
@respx.mock
async def test_import_rejects_missing_scene_art_before_any_canvas_write(tmp_path):
    storyboard = {
        "source": "Test",
        "episodes": [{"ep": 1, "segments": [_segment("E01-01", scene_index=2)]}],
    }
    _write_production(tmp_path, cast=_CAST, art=_ART, storyboard=storyboard, script=_SCRIPT)
    v3, _ = _canvas(_TARGETS)

    with pytest.raises(ProductionRejected, match="CINE_IMPORT_ASSETS_MISSING") as rejected:
        await _run_import(tmp_path, v3, {"1": "ep1"})

    assert rejected.value.detail["problems"] == [
        "Scene 2 is used in the storyboard but not defined in art.json."
    ]
    v3.submit_command.assert_not_called()


@pytest.mark.asyncio
@respx.mock
async def test_import_rejects_storyboard_and_script_in_different_directories(tmp_path):
    storyboard = {"source": "Test", "episodes": [{"ep": 1, "segments": []}]}
    _write_production(tmp_path / "production", storyboard=storyboard)
    _write_production(tmp_path / "outputs", script=_SCRIPT)
    v3, _ = _canvas(_TARGETS)

    with pytest.raises(ProductionRejected, match="CINE_IMPORT_SPLIT_PRODUCTION_DIR"):
        await _run_import(
            tmp_path,
            v3,
            {"1": "ep1"},
            storyboard_file="production/storyboard.json",
            script_file="outputs/script.json",
        )

    v3.get_snapshot.assert_not_called()
    v3.submit_command.assert_not_called()


@pytest.mark.asyncio
@respx.mock
async def test_import_links_each_episode_to_its_own_storyboard_node(tmp_path):
    storyboard = {
        "source": "Test",
        "episodes": [
            {"ep": 1, "segments": [_segment("E01-01"), _segment("E01-02")]},
            {"ep": 2, "segments": [_segment("E02-01")]},
        ],
    }
    _write_production(tmp_path, cast=_CAST, art=_ART, storyboard=storyboard, script=_SCRIPT)
    targets = [
        *_TARGETS,
        {"id": "ep2", "type": "storyboard", "title": "EP02 分镜表", "revision": 1},
    ]
    v3, state = _canvas(targets)

    await _run_import(tmp_path, v3, {"1": "ep1", "2": "ep2"})

    card = {
        node["data"]["segmentId"]: node["id"]
        for node in state["nodes"]
        if node.get("type") == "video_prompt"
    }
    derives = {(e["from"], e["to"]) for e in state["edges"] if e["kind"] == "derives"}
    assert derives == {("ep1", card["E01-01"]), ("ep1", card["E01-02"]), ("ep2", card["E02-01"])}
    sequence = {(e["from"], e["to"]) for e in state["edges"] if e["kind"] == "sequence"}
    assert sequence == {(card["E01-01"], card["E01-02"])}


@pytest.mark.asyncio
@respx.mock
async def test_reimport_preserves_cards_edited_on_canvas_and_updates_the_rest(tmp_path):
    def sha(text):
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    storyboard = {
        "source": "Test",
        "episodes": [
            {
                "ep": 1,
                "segments": [
                    _segment("E01-01", prompt="new prompt 1"),
                    _segment("E01-02", prompt="new prompt 2"),
                    _segment("E01-03", prompt="new prompt 3"),
                ],
            }
        ],
    }
    _write_production(tmp_path, storyboard=storyboard, script=_SCRIPT)
    existing = [
        *_TARGETS,
        {
            "id": "char",
            "type": "image_prompt",
            "title": "角色",
            "data": {"characterId": "C01"},
            "revision": 1,
        },
        {
            "id": "scene",
            "type": "image_prompt",
            "title": "场景",
            "data": {"sceneIndex": 1},
            "revision": 1,
        },
        # Hand-edited after the last import: its prompt no longer matches the recorded hash.
        {
            "id": "card1",
            "type": "video_prompt",
            "title": "E01-01",
            "revision": 2,
            "data": {
                "segmentId": "E01-01",
                "prompt": "hand edited",
                "importedPromptSha256": sha("old prompt 1"),
            },
        },
        # Untouched since the last import.
        {
            "id": "card2",
            "type": "video_prompt",
            "title": "E01-02",
            "revision": 2,
            "data": {
                "segmentId": "E01-02",
                "prompt": "old prompt 2",
                "importedPromptSha256": sha("old prompt 2"),
            },
        },
        # Imported before hashes were recorded, then rewritten by the prompt optimizer.
        {
            "id": "card3",
            "type": "video_prompt",
            "title": "E01-03",
            "revision": 2,
            "data": {
                "segmentId": "E01-03",
                "prompt": "optimized",
                "promptProvenance": {"source": "optimizer"},
            },
        },
    ]
    edges = [
        {"from": "ep1", "to": "card1", "kind": "derives"},
        {"from": "char", "to": "card1", "kind": "references"},
    ]
    v3, _ = _canvas(existing, edges, revision=7)

    result = await _run_import(tmp_path, v3, {"1": "ep1"})

    commands = [call.args[1] for call in v3.submit_command.call_args_list]
    updates = {c["nodeId"]: c for c in commands if c["type"] == "canvas.update_node"}
    assert set(updates) == {"card2"}
    assert updates["card2"]["expectedRevision"] == 7
    assert updates["card2"]["patch"]["data"]["prompt"] == "new prompt 2"
    assert updates["card2"]["patch"]["data"]["importedPromptSha256"] == sha("new prompt 2")
    assert result["receipt"]["preserved_edited_cards"] == ["E01-01", "E01-03"]
    assert "E01-01" in result["next_step"]
    connects = [(c["from"], c["to"], c["kind"]) for c in commands if c["type"] == "canvas.connect"]
    assert ("ep1", "card1", "derives") not in connects
    assert ("char", "card1", "references") not in connects
    assert len(connects) == len(set(connects))


def test_align_h3_prompt_to_references_reorders_correctly():
    from agentnexus.seedance.storyboard_import import align_h3_prompt_to_references

    cast = {
        "characters": [
            {"id": "C01", "name": "林黛玉", "aliases": ["黛玉"]},
            {"id": "C02", "name": "贾敏", "aliases": ["大姐"]},
            {"id": "C07", "name": "林如海", "aliases": ["老爷"]},
        ]
    }
    art = {
        "scenes": [
            {"id": "S01", "name": "街头卦摊（白天）", "brief": "street stall"},
        ]
    }

    # Canvas references order: C01 (Picture 1), C02 (Picture 2), C07 (Picture 3), S01 (Picture 4)
    actual_refs = [
        {"id": "node:c1", "label": "林黛玉 · 角色三视图", "kind": "image"},
        {"id": "node:c2", "label": "贾敏 · 角色三视图", "kind": "image"},
        {"id": "node:c7", "label": "林如海 · 角色三视图", "kind": "image"},
        {"id": "node:s1", "label": "街头卦摊（白天） · 场景概念图", "kind": "image"},
    ]

    # Inverted prompt: C07 is Picture 1, C01 is Picture 4!
    old_prompt = (
        "subject_definitions:\n"
        "<Subject 1> — a scholarly bureaucrat of forty-eight in a charcoal pinstripe three-piece suit; face, costume and glasses come entirely from <Picture 1>.\n"
        "<Subject 2> — a proud matriarch in her forties in an emerald-green embroidered jacket; face and costume come entirely from <Picture 2>.\n"
        "<Subject 3> — the daytime street fortune-telling stall environment; layout, materials and daylight come entirely from <Picture 3>.\n"
        "<Subject 4> — a cool young woman of eighteen in a pearl-white silk shirt; face, hair and costume come entirely from <Picture 4>.\n"
        "summary: Family meets at stall.\n"
        "retention_analysis: <Subject 1>, <Subject 2>, <Subject 3>, <Subject 4> are retained exactly as referenced.\n"
        "detailed_description:\n"
        "[Shot 1] <Subject 2> talks to <Subject 4>.\n"
        "[Shot 2] At 00:03.000, <Subject 1> arrives."
    )

    aligned = align_h3_prompt_to_references(old_prompt, actual_refs, cast, art)

    assert "<Subject 1> — a cool young woman" in aligned
    assert "come entirely from <Picture 1>" in aligned
    assert "<Subject 2> — a proud matriarch" in aligned
    assert "come entirely from <Picture 2>" in aligned
    assert "<Subject 3> — a scholarly bureaucrat" in aligned
    assert "come entirely from <Picture 3>" in aligned
    assert "<Subject 4> — the daytime street fortune-telling stall" in aligned
    assert "come entirely from <Picture 4>" in aligned

    assert "[Shot 1] <Subject 2> talks to <Subject 1>." in aligned
    assert "[Shot 2] At 00:03.000, <Subject 3> arrives." in aligned
