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
    character = next(n for n in current_nodes if (n.get("data") or {}).get("characterId") == "C01")
    scene = next(n for n in current_nodes if (n.get("data") or {}).get("sceneId") == "S01")
    video = next(n for n in current_nodes if (n.get("data") or {}).get("segmentId") == "E01-01")
    assert (character["data"]["production_stage"], character["data"]["production_pointer"]) == (
        "cast",
        "/characters/0/image/sheet",
    )
    assert (scene["data"]["production_stage"], scene["data"]["production_pointer"]) == (
        "art",
        "/scenes/0/image/prompt",
    )
    assert (video["data"]["production_stage"], video["data"]["production_pointer"]) == (
        "storyboard",
        "/episodes/0/segments/0/h3Prompt",
    )


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


async def _no_jev(_state, _questions):
    """Tests never reach the real JEV service."""
    return {"error": "JEV disabled in tests"}


async def _run_import(
    tmp_path,
    v3,
    episode_nodes,
    *,
    storyboard_file="storyboard.json",
    script_file="script.json",
    subject_judge=_no_jev,
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
            subject_judge=subject_judge,
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
    assert set(updates) == {"card1", "card2", "card3"}
    assert updates["card1"]["patch"]["data"]["prompt"] == "hand edited"
    assert updates["card3"]["patch"]["data"]["prompt"] == "optimized"
    assert (
        updates["card1"]["patch"]["data"]["production_pointer"]
        == "/episodes/0/segments/0/h3Prompt"
    )
    assert (
        updates["card3"]["patch"]["data"]["production_pointer"]
        == "/episodes/0/segments/2/h3Prompt"
    )
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
        "format: h3_ref2va\n"
        "subject_definitions:\n"
        "<Subject 1> — a scholarly bureaucrat of forty-eight in a charcoal pinstripe "
        "three-piece suit; face, costume and glasses come entirely from <Picture 1>.\n"
        "<Subject 2> — a proud matriarch in her forties in an emerald-green embroidered "
        "jacket; face and costume come entirely from <Picture 2>.\n"
        "<Subject 3> — the daytime street fortune-telling stall environment; layout, "
        "materials and daylight come entirely from <Picture 3>.\n"
        "<Subject 4> — a cool young woman of eighteen in a pearl-white silk shirt; "
        "face, hair and costume come entirely from <Picture 4>.\n"
        "summary: Family meets at stall.\n"
        "retention_analysis: <Subject 1>, <Subject 2>, <Subject 3>, <Subject 4> "
        "are retained exactly as referenced.\n"
        "detailed_description:\n"
        "[Shot 1] <Subject 2> talks to <Subject 4>.\n"
        "[Shot 2] At 00:03.000, <Subject 1> arrives."
    )

    # English Ref2VA prompts carry no names, so which subject is which is a
    # semantic match (JEV's job, see align_prompt_with_references). Without it
    # nothing is guessed and the prompt stays as authored.
    issues = []
    unchanged = align_h3_prompt_to_references(old_prompt, actual_refs, cast, art, issues=issues)
    assert unchanged == old_prompt
    assert any("<Subject 1>" in issue for issue in issues)

    # With the subjects matched (as JEV would), numbering follows the canvas.
    matched = {1: ("char", "C07"), 2: ("char", "C02"), 3: ("scene", "S01"), 4: ("char", "C01")}
    aligned = align_h3_prompt_to_references(
        old_prompt, actual_refs, cast, art, subject_entities=matched
    )

    assert aligned.startswith("format: h3_ref2va\n")
    assert "<Subject 1> — a cool young woman" in aligned
    assert "costume come entirely from <Picture 1>" in aligned
    assert "<Subject 2> — a proud matriarch" in aligned
    assert "come entirely from <Picture 2>" in aligned
    assert "<Subject 3> — a scholarly bureaucrat" in aligned
    # The author's own citation wording is kept, only renumbered.
    assert "costume and glasses come entirely from <Picture 3>" in aligned
    assert "<Subject 4> — the daytime street fortune-telling stall" in aligned
    assert "come entirely from <Picture 4>" in aligned

    assert "[Shot 1] <Subject 2> talks to <Subject 1>." in aligned
    assert "[Shot 2] At 00:03.000, <Subject 3> arrives." in aligned


_FERRY_CAST = {
    "characters": [
        {
            "id": "C01",
            "name": "老周",
            "aliases": ["老伯"],
            "persona": {"gender": "男", "ageRange": "约七十岁", "appearance": "驼背，左眼白翳"},
            "image": {"sheet": "Model sheet of an elderly ferryman in a straw hat"},
        },
        {
            "id": "C02",
            "name": "沈知微",
            "persona": {"gender": "女", "ageRange": "十八岁", "appearance": "月白长衫，提皮箱"},
            "image": {
                "sheet": "Model sheet of a slender eighteen-year-old woman in a pearl-white gown"
            },
        },
    ]
}
_FERRY_ART = {
    "scenes": [{"id": "S01", "name": "雾中渡口", "image": {"prompt": "foggy river ferry"}}]
}
# Descriptions that the old fixed profiles mapped to the sample story's cast:
# "slender" / "eighteen-year-old" was always C01, whoever C01 is.
_FERRY_PROMPT = (
    "subject_definitions:\n"
    "<Subject 1> — a slender eighteen-year-old woman in a pearl-white gown; "
    "face and costume come entirely from <Picture 1>.\n"
    "<Subject 2> — an elderly ferryman in a straw hat; "
    "face, hair and costume come entirely from <Picture 2>.\n"
    "summary: A passenger boards.\n"
    "retention_analysis: <Subject 1>, <Subject 2> are retained exactly as referenced.\n"
    "detailed_description:\n"
    "[Shot 1] <Subject 2> unties the rope while <Subject 1> steps aboard."
)
_FERRY_REFS = [
    {"id": "node:n_c01", "label": "老周 · 角色三视图", "kind": "image"},
    {"id": "node:n_c02", "label": "沈知微 · 角色三视图", "kind": "image"},
]


def test_alignment_uses_no_sample_story_profiles():
    from agentnexus.seedance.storyboard_import import (
        _identify_subject_entity,
        align_h3_prompt_to_references,
    )

    for line in _FERRY_PROMPT.splitlines()[1:3]:
        assert _identify_subject_entity(line, _FERRY_CAST, _FERRY_ART) == ("unknown", None)
    issues = []
    unchanged = align_h3_prompt_to_references(
        _FERRY_PROMPT, _FERRY_REFS, _FERRY_CAST, _FERRY_ART, issues=issues
    )
    assert unchanged == _FERRY_PROMPT
    assert issues


def test_subject_identification_uses_project_names_aliases_and_whole_ids():
    from agentnexus.seedance.storyboard_import import _identify_subject_entity

    cast = {
        "characters": [
            {"id": "C1", "name": "小满", "aliases": ["满儿"]},
            {"id": "C10", "name": "小满娘"},
            {"id": "C11", "name": "阿福", "aliases": ["船家"]},
            {"id": "C12", "name": "阿贵", "aliases": ["船家"]},
        ]
    }
    art = {"scenes": [{"id": "S01", "name": "雾中渡口"}]}
    assert _identify_subject_entity("满儿蹲在船头", cast, art) == ("char", "C1")
    # The longer name wins: 小满娘 is not also read as 小满.
    assert _identify_subject_entity("小满娘提着竹篮", cast, art) == ("char", "C10")
    # An ASCII id matches only as a whole token.
    assert _identify_subject_entity("character C10 by the rail", cast, art) == ("char", "C10")
    # An alias two characters share identifies neither.
    assert _identify_subject_entity("船家摇橹", cast, art) == ("unknown", None)
    # Two different entities in one definition is ambiguous.
    assert _identify_subject_entity("小满站在雾中渡口", cast, art) == ("unknown", None)


def _judge(answers, calls=None):
    async def judge(state, questions):
        if calls is not None:
            calls.append((state, questions))
        return {"model": "jev-test", "answers": answers}

    return judge


@pytest.mark.asyncio
async def test_jev_matches_unnamed_subjects_for_a_different_story():
    from agentnexus.seedance.storyboard_import import align_prompt_with_references

    calls = []
    judge = _judge(
        {
            "subject_1": {"type": "choice", "choice": "char:C02", "confidence": 0.93},
            "subject_2": {"type": "choice", "choice": "char:C01", "confidence": 0.91},
        },
        calls,
    )
    ref_entities = {"node:n_c01": ("char", "C01"), "node:n_c02": ("char", "C02")}
    issues = []
    aligned = await align_prompt_with_references(
        _FERRY_PROMPT,
        _FERRY_REFS,
        _FERRY_CAST,
        _FERRY_ART,
        ref_entities=ref_entities,
        judge=judge,
        issues=issues,
    )
    assert issues == []
    assert "<Subject 1> — an elderly ferryman" in aligned
    assert "<Subject 2> — a slender eighteen-year-old woman" in aligned
    assert "[Shot 1] <Subject 1> unties the rope while <Subject 2> steps aboard." in aligned
    # Options come from the project's own cast entries, not a built-in profile.
    _state, questions = calls[0]
    options = questions["subject_1"]["criteria"]
    assert set(options) == {"char:C01", "char:C02", "unclear"}
    assert "straw hat" in options["char:C01"]
    assert "沈知微" in options["char:C02"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "answers",
    [
        # Ambiguous: JEV is not sure.
        {
            "subject_1": {"choice": "char:C02", "confidence": 0.55},
            "subject_2": {"choice": "char:C01", "confidence": 0.91},
        },
        # Explicitly unclear.
        {
            "subject_1": {"choice": "unclear", "confidence": 0.9},
            "subject_2": {"choice": "char:C01", "confidence": 0.91},
        },
        # Both subjects claimed for the same character.
        {
            "subject_1": {"choice": "char:C01", "confidence": 0.9},
            "subject_2": {"choice": "char:C01", "confidence": 0.91},
        },
        # An option that was never offered.
        {
            "subject_1": {"choice": "char:C99", "confidence": 0.99},
            "subject_2": {"choice": "char:C01", "confidence": 0.91},
        },
    ],
)
async def test_uncertain_jev_subject_matches_leave_the_prompt_as_authored(answers):
    from agentnexus.seedance.storyboard_import import align_prompt_with_references

    issues = []
    aligned = await align_prompt_with_references(
        _FERRY_PROMPT, _FERRY_REFS, _FERRY_CAST, _FERRY_ART, judge=_judge(answers), issues=issues
    )
    assert aligned == _FERRY_PROMPT
    assert issues


@pytest.mark.asyncio
async def test_failed_jev_call_leaves_the_prompt_as_authored():
    from agentnexus.seedance.storyboard_import import align_prompt_with_references

    async def broken(_state, _questions):
        raise RuntimeError("network down")

    issues = []
    aligned = await align_prompt_with_references(
        _FERRY_PROMPT, _FERRY_REFS, _FERRY_CAST, _FERRY_ART, judge=broken, issues=issues
    )
    assert aligned == _FERRY_PROMPT
    assert any("JEV subject match failed" in issue for issue in issues)


@pytest.mark.asyncio
@respx.mock
async def test_scene_index_reuse_across_episodes_links_distinct_scene_ids(tmp_path):
    storyboard = {
        "source": "Test",
        "episodes": [
            {"ep": 1, "segments": [_segment("E01-01", scene_index=1)]},
            {"ep": 2, "segments": [_segment("E02-01", scene_index=1)]},
        ],
    }
    script = {
        "source": "Test",
        "episodes": [
            {"ep": 1, "scenes": [{"sceneId": "S01", "flow": [{"action": "A"}]}]},
            {"ep": 2, "scenes": [{"sceneId": "S04", "flow": [{"action": "B"}]}]},
        ],
    }
    art = {
        "scenes": [
            {"id": "S01", "sceneIndex": 1, "image": {"prompt": "first scene"}},
            {"id": "S04", "sceneIndex": 1, "image": {"prompt": "second scene"}},
        ]
    }
    _write_production(tmp_path, cast=_CAST, art=art, storyboard=storyboard, script=script)
    targets = [*_TARGETS, {"id": "ep2", "type": "storyboard", "title": "EP02", "revision": 1}]
    v3, state = _canvas(targets)

    result = await _run_import(tmp_path, v3, {"1": "ep1", "2": "ep2"})
    scenes = {
        node["data"]["sceneId"]: node["id"]
        for node in state["nodes"]
        if (node.get("data") or {}).get("sceneId")
    }
    card = next(
        node["id"]
        for node in state["nodes"]
        if (node.get("data") or {}).get("segmentId") == "E02-01"
    )
    references = {
        edge["from"]
        for edge in state["edges"]
        if edge.get("kind") == "references" and edge.get("to") == card
    }
    assert result["status"] == "synced"
    assert set(scenes) == {"S01", "S04"}
    assert scenes["S04"] in references
    assert scenes["S01"] not in references


def _prompt_needing_alignment():
    # Authored with the scene as Picture 1; the canvas attaches the character first.
    return (
        "subject_definitions:\n"
        "<Subject 1> — 街头算命摊; layout comes from <Picture 1>.\n"
        "<Subject 2> — 林黛玉; face comes from <Picture 2>.\n"
        "summary: test\n"
        "retention_analysis: <Subject 1>, <Subject 2> retained.\n"
        "detailed_description:\n"
        "[Shot 1] <Subject 2> walks into <Subject 1>.\n"
    )


_ALIGNMENT_REFS = [
    {"id": "character", "label": "林黛玉 · 角色三视图", "kind": "image"},
    {"id": "scene", "label": "街头算命摊 · 场景概念图", "kind": "image"},
]


@pytest.mark.asyncio
@respx.mock
async def test_rejected_alignment_does_not_change_verified_storyboard(tmp_path):
    storyboard = {
        "source": "Test",
        "episodes": [
            {"ep": 1, "segments": [_segment("E01-01", prompt=_prompt_needing_alignment())]}
        ],
    }
    _write_production(tmp_path, cast=_CAST, art=_ART, storyboard=storyboard, script=_SCRIPT)
    original = (tmp_path / "storyboard.json").read_bytes()
    v3, _ = _canvas(_TARGETS)
    v3.get_node_references.return_value = list(_ALIGNMENT_REFS)
    original_submit = v3.submit_command.side_effect

    async def reject_alignment(project_id, command):
        if command.get("commandId", "").startswith("cine-align-vp-"):
            return {"accepted": False}
        return await original_submit(project_id, command)

    v3.submit_command.side_effect = reject_alignment
    with pytest.raises(ProductionRejected, match="CINE_IMPORT_VIDEO_CARD_PROJECTION_FAILED"):
        await _run_import(tmp_path, v3, {"1": "ep1"})
    assert (tmp_path / "storyboard.json").read_bytes() == original


@pytest.mark.asyncio
@respx.mock
async def test_successful_alignment_is_verified_against_final_storyboard(tmp_path):
    storyboard = {
        "source": "Test",
        "episodes": [
            {"ep": 1, "segments": [_segment("E01-01", prompt=_prompt_needing_alignment())]}
        ],
    }
    _write_production(tmp_path, cast=_CAST, art=_ART, storyboard=storyboard, script=_SCRIPT)
    v3, _ = _canvas(_TARGETS)
    v3.get_node_references.return_value = list(_ALIGNMENT_REFS)

    result = await _run_import(tmp_path, v3, {"1": "ep1"})
    imports = [
        call.args[1]
        for call in v3.submit_command.call_args_list
        if call.args[1]["type"] == "storyboard.import"
    ]
    saved = json.loads((tmp_path / "storyboard.json").read_text(encoding="utf-8"))
    assert result["status"] == "synced"
    assert len(imports) == 2
    assert imports[-1]["document"] == saved
    aligned = saved["episodes"][0]["segments"][0]["h3Prompt"]
    assert "<Subject 1> — 林黛玉; face comes from <Picture 1>." in aligned
    assert "<Subject 2> — 街头算命摊; layout comes from <Picture 2>." in aligned
    assert "[Shot 1] <Subject 1> walks into <Subject 2>." in aligned
    assert result["receipt"]["subject_alignment_review"] == []


@pytest.mark.asyncio
@respx.mock
async def test_unmatched_subjects_are_reported_and_not_renumbered(tmp_path):
    prompt = _FERRY_PROMPT
    storyboard = {
        "source": "渡口",
        "episodes": [
            {
                "ep": 1,
                "segments": [_segment("E01-01", characters=("C01", "C02"), prompt=prompt)],
            }
        ],
    }
    cast = {
        "characters": [
            {**c, "image": {"sheet": c["image"]["sheet"]}} for c in _FERRY_CAST["characters"]
        ]
    }
    art = {
        "scenes": [{"id": "S01", "sceneIndex": 1, "name": "雾中渡口", "image": {"prompt": "fog"}}]
    }
    _write_production(tmp_path, cast=cast, art=art, storyboard=storyboard, script=_SCRIPT)
    v3, state = _canvas(_TARGETS)

    async def references(_project, _card):
        cards = {
            (n.get("data") or {}).get("characterId"): n["id"]
            for n in state["nodes"]
            if (n.get("data") or {}).get("characterId")
        }
        return [
            {"id": f"node:{cards['C01']}", "label": "card", "kind": "image"},
            {"id": f"node:{cards['C02']}", "label": "card", "kind": "image"},
        ]

    v3.get_node_references.side_effect = references
    calls = []
    unsure = _judge(
        {
            "subject_1": {"choice": "char:C02", "confidence": 0.6},
            "subject_2": {"choice": "char:C01", "confidence": 0.6},
        },
        calls,
    )

    result = await _run_import(tmp_path, v3, {"1": "ep1"}, subject_judge=unsure)
    saved = json.loads((tmp_path / "storyboard.json").read_text(encoding="utf-8"))
    assert saved["episodes"][0]["segments"][0]["h3Prompt"] == prompt
    review = result["receipt"]["subject_alignment_review"]
    assert [item["segmentId"] for item in review] == ["E01-01"]
    assert "E01-01" in result["next_step"]
    # The canvas cards' own data identified the references, so JEV was asked
    # to choose only among this production's characters.
    assert set(calls[0][1]["subject_1"]["criteria"]) == {"char:C01", "char:C02", "unclear"}


# ---------------------------------------------------------------------------
# Cast cards without an id (allowed by the cast schema) resolve via the outline
# ---------------------------------------------------------------------------

_ALICE_OUTLINE = {
    "source": "Test",
    "characters": [{"id": "C01", "name": "Alice", "aliases": ["Al"]}],
}
_IDLESS_CAST = {
    "characters": [
        {
            "name": "Alice",
            "aliases": ["Al"],
            "oneLiner": "A ferry passenger",
            "image": {"sheet": "sheet prompt for Alice"},
        }
    ]
}


def _idless_storyboard(prompt=""):
    return {
        "source": "Test",
        "episodes": [{"ep": 1, "segments": [_segment("E01-01", prompt=prompt)]}],
    }


def test_missing_assets_resolve_an_idless_card_only_through_an_outline():
    from pathlib import Path

    from agentnexus.seedance.storyboard_import import _missing_visual_assets

    segments = [({"ep": 1}, {"cuts": [{"characters": ["C01"]}]})]
    cast = (Path("cast.json"), {"characters": [{"name": "Alice", "image": {"sheet": "portrait"}}]})
    art = (Path("art.json"), {"scenes": []})

    without_outline = _missing_visual_assets(segments, [], cast, art)
    assert len(without_outline) == 1
    assert "outline.json" in without_outline[0] and "not found" in without_outline[0]
    assert "'Alice' has no id" in without_outline[0]

    outline = (Path("outline.json"), {"characters": [{"id": "C01", "name": "Alice"}]})
    assert _missing_visual_assets(segments, [], cast, art, outline=outline) == []


def test_resolve_cast_links_explicit_foreign_ids_and_refuses_ambiguity():
    from pathlib import Path

    from agentnexus.seedance.storyboard_import import _resolve_cast

    outline = (
        Path("outline.json"),
        {
            "characters": [
                {"id": "C01", "name": "Alice", "aliases": ["Al"]},
                {"id": "C02", "name": "Alicia", "aliases": ["Al"]},
                {"id": "C03", "name": "Bob"},
            ]
        },
    )
    cast = {
        "characters": [
            {"id": "C01", "name": "Alice"},  # explicit outline id: used as-is
            {"id": "R03", "name": "Bob"},  # its own id scheme: linked by name
            {"name": "Al"},  # alias shared by C01 and C02
            {"name": "Nobody"},  # matches nothing
        ]
    }
    index = _resolve_cast(cast, outline)
    assert set(index.cards) == {"C01", "C03"}
    assert index.cards["C03"]["id"] == "R03"  # the original card, unchanged
    assert "'Al' matches C01, C02" in index.unlinked
    assert "'Nobody' matches no outline character" in index.unlinked
    assert "id" not in cast["characters"][2]  # nothing written into the source


@pytest.mark.asyncio
@respx.mock
async def test_idless_cast_resolves_through_the_outline_end_to_end(tmp_path):
    prompt = (
        "subject_definitions:\n"
        "<Subject 1> — 街头算命摊; layout comes from <Picture 1>.\n"
        "<Subject 2> — Al in a grey coat; face comes from <Picture 2>.\n"
        "summary: test\n"
        "retention_analysis: <Subject 1>, <Subject 2> retained.\n"
        "detailed_description:\n"
        "[Shot 1] <Subject 2> walks into <Subject 1>.\n"
    )
    _write_production(
        tmp_path,
        cast=_IDLESS_CAST,
        art=_ART,
        outline=_ALICE_OUTLINE,
        storyboard=_idless_storyboard(prompt),
        script=_SCRIPT,
    )
    cast_bytes = (tmp_path / "cast.json").read_bytes()
    v3, state = _canvas(_TARGETS)

    async def references(_project, _card):
        def card(key, value):
            return next(n for n in state["nodes"] if (n.get("data") or {}).get(key) == value)

        return [
            {"id": f"node:{card('characterId', 'C01')['id']}", "label": "card", "kind": "image"},
            {"id": f"node:{card('sceneId', 'S01')['id']}", "label": "card", "kind": "image"},
        ]

    v3.get_node_references.side_effect = references
    result = await _run_import(tmp_path, v3, {"1": "ep1"})

    assert result["status"] == "synced"
    characters = [n for n in state["nodes"] if (n.get("data") or {}).get("characterId")]
    assert len(characters) == 1
    sheet = characters[0]
    assert sheet["data"]["characterId"] == "C01"  # the storyboard's id
    assert sheet["title"] == "Alice · 角色三视图"
    assert sheet["data"]["prompt"] == "sheet prompt for Alice"
    assert sheet["data"]["brief"] == "A ferry passenger"
    assert sheet["data"]["production_pointer"] == "/characters/0/image/sheet"
    video = next(n for n in state["nodes"] if n.get("type") == "video_prompt")
    edges = {(e["from"], e["to"], e["kind"]) for e in state["edges"]}
    assert (sheet["id"], video["id"], "references") in edges
    # The subject named by the resolved card's alias was matched and renumbered.
    saved = json.loads((tmp_path / "storyboard.json").read_text(encoding="utf-8"))
    aligned = saved["episodes"][0]["segments"][0]["h3Prompt"]
    assert "<Subject 1> — Al in a grey coat; face comes from <Picture 1>." in aligned
    assert "[Shot 1] <Subject 1> walks into <Subject 2>." in aligned
    assert result["receipt"]["subject_alignment_review"] == []
    # No synthetic id was written into the source cast.
    assert (tmp_path / "cast.json").read_bytes() == cast_bytes


async def _rejected_before_canvas_write(tmp_path, code):
    v3, state = _canvas(_TARGETS)
    with pytest.raises(ProductionRejected, match=code) as rejected:
        await _run_import(tmp_path, v3, {"1": "ep1"})
    v3.submit_command.assert_not_called()
    assert state["nodes"] == _TARGETS
    return rejected.value


@pytest.mark.asyncio
@respx.mock
async def test_ambiguous_idless_card_fails_before_canvas_mutation(tmp_path):
    outline = {
        "source": "Test",
        "characters": [
            {"id": "C01", "name": "Alice", "aliases": ["Al"]},
            {"id": "C02", "name": "Alicia", "aliases": ["Al"]},
        ],
    }
    cast = {"characters": [{"name": "Al", "image": {"sheet": "sheet"}}]}
    _write_production(
        tmp_path,
        cast=cast,
        art=_ART,
        outline=outline,
        storyboard=_idless_storyboard(),
        script=_SCRIPT,
    )
    error = await _rejected_before_canvas_write(tmp_path, "CINE_IMPORT_ASSETS_MISSING")
    (problem,) = error.detail["problems"]
    assert "C01" in problem and "'Al' matches C01, C02" in problem


@pytest.mark.asyncio
@respx.mock
async def test_idless_cast_without_outline_fails_before_canvas_mutation(tmp_path):
    _write_production(
        tmp_path, cast=_IDLESS_CAST, art=_ART, storyboard=_idless_storyboard(), script=_SCRIPT
    )
    error = await _rejected_before_canvas_write(tmp_path, "CINE_IMPORT_ASSETS_MISSING")
    (problem,) = error.detail["problems"]
    assert "outline.json" in problem and "not found" in problem


@pytest.mark.asyncio
@respx.mock
async def test_two_outline_documents_fail_before_canvas_mutation(tmp_path):
    _write_production(
        tmp_path,
        cast=_IDLESS_CAST,
        art=_ART,
        outline=_ALICE_OUTLINE,
        storyboard=_idless_storyboard(),
        script=_SCRIPT,
    )
    (tmp_path / "draft-outline.json").write_text(json.dumps(_ALICE_OUTLINE), encoding="utf-8")
    error = await _rejected_before_canvas_write(tmp_path, "CINE_IMPORT_STAGE_AMBIGUOUS")
    assert error.detail["stage"] == "outline"


@pytest.mark.asyncio
@respx.mock
async def test_two_cards_claiming_one_character_fail_before_canvas_mutation(tmp_path):
    cast = {
        "characters": [
            {"id": "C01", "name": "Alice", "image": {"sheet": "first"}},
            {"name": "Alice", "image": {"sheet": "second"}},
        ]
    }
    _write_production(
        tmp_path,
        cast=cast,
        art=_ART,
        outline=_ALICE_OUTLINE,
        storyboard=_idless_storyboard(),
        script=_SCRIPT,
    )
    error = await _rejected_before_canvas_write(tmp_path, "CINE_IMPORT_ASSETS_MISSING")
    (problem,) = error.detail["problems"]
    assert "more than one card" in problem
