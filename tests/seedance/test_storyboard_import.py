import hashlib
import itertools
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
                "frame": "medium shot of a ferry deck",
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
                        "lighting": "soft light through river fog",
                        "flow": [
                            {"action": "The ferryman looks toward the signal lantern."},
                            {"speaker": "C02", "line": "把绳结系稳。", "delivery": "calm"},
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
            "exactText": "把绳结系稳。",
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
            {"id": "C01", "name": "顾青", "image": {"sheet": "16:9 sheet prompt for Gu Qing"}},
        ]
    }
    art = {
        "scenes": [
            {
                "id": "S01",
                "sceneIndex": 1,
                "name": "南岸候船亭",
                "image": {"prompt": "scene prompt for ferry pavilion"},
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
    """AsyncMock V3 client whose snapshots reflect the nodes and edges it has been sent.

    ``state["outputs"]`` maps a node id to its output URL, reported as V3's node_media.
    """
    state = {"nodes": list(nodes), "edges": list(edges or []), "outputs": {}}
    new_ids = itertools.count(len(state["nodes"]) + 1)
    v3 = AsyncMock()

    async def submit(project_id, cmd):
        if cmd.get("type") == "canvas.create_node":
            state["nodes"].append(
                {
                    "id": f"node_{next(new_ids)}",
                    "type": cmd.get("nodeType"),
                    "title": cmd.get("title"),
                    "data": cmd.get("data", {}),
                    "revision": 1,
                }
            )
        elif cmd.get("type") == "canvas.connect":
            state["edges"].append({"from": cmd["from"], "to": cmd["to"], "kind": cmd["kind"]})
        elif cmd.get("type") == "canvas.delete_node":
            state["nodes"] = [n for n in state["nodes"] if n["id"] != cmd["nodeId"]]
            state["edges"] = [
                e for e in state["edges"] if cmd["nodeId"] not in (e["from"], e["to"])
            ]
        return {"accepted": True, "response": {"verified": True}}

    def snapshot(project_id):
        snap = {
            "nodes": list(state["nodes"]),
            "edges": list(state["edges"]),
            "node_media": [
                {
                    "nodeId": n["id"],
                    "outputUrl": state["outputs"].get(n["id"]),
                    "mediaState": (
                        "output_available_unreviewed"
                        if state["outputs"].get(n["id"])
                        else "no_output"
                    ),
                    "latestJob": None,
                    "pendingJobCount": 0,
                }
                for n in state["nodes"]
            ],
        }
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
_CAST = {"characters": [{"id": "C01", "name": "顾青", "image": {"sheet": "16:9 sheet prompt"}}]}
_ART = {
    "scenes": [
        {"id": "S01", "sceneIndex": 1, "name": "南岸候船亭", "image": {"prompt": "scene prompt"}}
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
            {"id": "C01", "name": "顾青", "aliases": ["阿青"]},
            {"id": "C02", "name": "杜禾", "aliases": ["大姐"]},
            {"id": "C07", "name": "程砚", "aliases": ["老爷"]},
        ]
    }
    art = {
        "scenes": [
            {"id": "S01", "name": "南岸候船亭（清晨）", "brief": "ferry pavilion"},
        ]
    }

    # Canvas references order: C01 (Picture 1), C02 (Picture 2), C07 (Picture 3), S01 (Picture 4)
    actual_refs = [
        {"id": "node:c1", "label": "顾青 · 角色三视图", "kind": "image"},
        {"id": "node:c2", "label": "杜禾 · 角色三视图", "kind": "image"},
        {"id": "node:c7", "label": "程砚 · 角色三视图", "kind": "image"},
        {"id": "node:s1", "label": "南岸候船亭（清晨） · 场景概念图", "kind": "image"},
    ]

    # Inverted prompt: C07 is Picture 1, C01 is Picture 4!
    old_prompt = (
        "format: h3_ref2va\n"
        "subject_definitions:\n"
        "<Subject 1> — a middle-aged navigator in a weathered navy "
        "coat; face, costume and glasses come entirely from <Picture 1>.\n"
        "<Subject 2> — a retired cartographer in a plain grey wool "
        "jacket; face and costume come entirely from <Picture 2>.\n"
        "<Subject 3> — the morning ferry pavilion environment; layout, "
        "materials and daylight come entirely from <Picture 3>.\n"
        "<Subject 4> — a young traveler in a rust-red windbreaker; "
        "face, hair and costume come entirely from <Picture 4>.\n"
        "summary: Passengers wait beside the ferry.\n"
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
    assert "<Subject 1> — a young traveler" in aligned
    assert "costume come entirely from <Picture 1>" in aligned
    assert "<Subject 2> — a retired cartographer" in aligned
    assert "come entirely from <Picture 2>" in aligned
    assert "<Subject 3> — a middle-aged navigator" in aligned
    # The author's own citation wording is kept, only renumbered.
    assert "costume and glasses come entirely from <Picture 3>" in aligned
    assert "<Subject 4> — the morning ferry pavilion" in aligned
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


@pytest.mark.parametrize("retention_separator", ["\n", " "])
def test_reference_alignment_preserves_every_retention_role_and_definition(retention_separator):
    from agentnexus.seedance.storyboard_import import align_h3_prompt_to_references

    definitions = (
        "subject_definitions:\n"
        "<Subject 1> — S01, the foggy ferry landing; layout comes from <Picture 1>.\n"
        "<Subject 2> — C01, a ferryman in a straw hat; appearance comes from <Picture 2>.\n"
        "<Subject 3> — C02, a passenger in a white gown; appearance comes from <Picture 3>.\n"
        "<Picture 1> — the first-frame composition for [Shot 1].\n"
        "<Video 1> — an editing-rhythm reference.\n"
        "<Audio 1> — a reused ferry-bell recording.\n"
    )
    retention = (
        "<Subject 1> (appears in [Shot 1], [Shot 2]): fully_preserved - the fog and pier remain.\n"
        "<Subject 2> (appears in [Shot 1]): partially_preserved - the hat remains but is tilted.\n"
        "<Subject 3> (appears in [Shot 2]): attribute_transfer - the gown's color is carried.\n"
        "<Picture 1> ([Shot 1] first frame): fully_preserved - the opening composition matches.\n"
        "<Video 1> (cut and pacing structure): weak_reference - only the slow rhythm is used.\n"
        "<Audio 1>: partially_copy - one bell tone is mixed with the new river ambience.\n"
    )
    prompt = (
        definitions
        + "summary: [reference generation + keyframe completion] A passenger boards.\n"
        + "retention_analysis:"
        + retention_separator
        + retention
        + "detailed_description:\n"
        + "Semi-realistic painterly style.\n"
        + "[Shot 1] <Subject 2> unties the rope at <Subject 1>, beginning from <Picture 1>.\n"
        + "[Shot 2] <Subject 3> steps onto the pier.\n"
        + "overall_soundscape: A continuous river breeze.\nnon_diegetic_music: N/A"
    )
    refs = [_FERRY_REFS[1], {"id": "node:scene", "label": "雾中渡口"}, _FERRY_REFS[0]]
    issues = []

    aligned = align_h3_prompt_to_references(prompt, refs, _FERRY_CAST, _FERRY_ART, issues=issues)

    expected_retention = (
        "<Subject 2> (appears in [Shot 1], [Shot 2]): fully_preserved - the fog and pier remain.\n"
        "<Subject 3> (appears in [Shot 1]): partially_preserved - the hat remains but is tilted.\n"
        "<Subject 1> (appears in [Shot 2]): attribute_transfer - the gown's color is carried.\n"
        "<Picture 2> ([Shot 1] first frame): fully_preserved - the opening composition matches.\n"
        "<Video 1> (cut and pacing structure): weak_reference - only the slow rhythm is used.\n"
        "<Audio 1>: partially_copy - one bell tone is mixed with the new river ambience.\n"
    )
    assert issues == []
    assert "retention_analysis:" + retention_separator + expected_retention in aligned
    assert "<Picture 2> — the first-frame composition for [Shot 1]." in aligned
    assert "<Video 1> — an editing-rhythm reference." in aligned
    assert "<Audio 1> — a reused ferry-bell recording." in aligned
    assert "<Subject 1> — C02, a passenger" in aligned
    assert "<Subject 2> — S01, the foggy ferry landing" in aligned
    assert "<Subject 3> — C01, a ferryman" in aligned
    assert (
        "[Shot 1] <Subject 3> unties the rope at <Subject 2>, beginning from <Picture 2>."
        in aligned
    )
    assert align_h3_prompt_to_references(aligned, refs, _FERRY_CAST, _FERRY_ART) == aligned


@pytest.mark.parametrize("unknown_token", ["<Subject 9>", "<Picture 9>"])
def test_reference_alignment_refuses_undefined_tokens_in_standalone_definitions(unknown_token):
    from agentnexus.seedance.storyboard_import import align_h3_prompt_to_references

    prompt = (
        _FERRY_PROMPT.replace("<Subject 1> — ", "<Subject 1> — C02, ")
        .replace("<Subject 2> — ", "<Subject 2> — C01, ")
        .replace("summary:", f"<Audio 1> — a sound associated with {unknown_token}.\nsummary:")
    )
    issues = []

    aligned = align_h3_prompt_to_references(
        prompt, _FERRY_REFS, _FERRY_CAST, _FERRY_ART, issues=issues
    )

    assert aligned == prompt
    assert any(unknown_token in issue for issue in issues)


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
        "<Subject 1> — 南岸候船亭; layout comes from <Picture 1>.\n"
        "<Subject 2> — 顾青; face comes from <Picture 2>.\n"
        "summary: [reference generation] A traveler reaches the ferry pavilion.\n"
        "retention_analysis:\n"
        "<Subject 1> (appears in [Shot 1]): fully_preserved - the pavilion's layout is retained.\n"
        "<Subject 2> (appears in [Shot 1]): partially_preserved - "
        "the face stays; posture changes.\n"
        "detailed_description:\n"
        "[Shot 1] <Subject 2> walks into <Subject 1>.\n"
    )


_ALIGNMENT_REFS = [
    {"id": "character", "label": "顾青 · 角色三视图", "kind": "image"},
    {"id": "scene", "label": "南岸候船亭 · 场景概念图", "kind": "image"},
]


def test_reference_alignment_keeps_retention_when_an_unclaimed_canvas_reference_is_added():
    from agentnexus.seedance.storyboard_import import align_h3_prompt_to_references

    prompt = _prompt_needing_alignment()
    refs = [*_ALIGNMENT_REFS, {"id": "mood", "label": "dawn color palette", "kind": "image"}]

    aligned = align_h3_prompt_to_references(prompt, refs, _CAST, _ART)

    assert (
        "retention_analysis:\n"
        "<Subject 2> (appears in [Shot 1]): fully_preserved - the pavilion's layout is retained.\n"
        "<Subject 1> (appears in [Shot 1]): partially_preserved - "
        "the face stays; posture changes.\n"
        "detailed_description:"
    ) in aligned
    retention = aligned.split("retention_analysis:", 1)[1].split("detailed_description:", 1)[0]
    assert "<Subject 3>" not in retention


@pytest.mark.asyncio
async def test_reference_alignment_appends_only_the_known_continuation_frame_retention():
    from agentnexus.seedance.storyboard_import import align_prompt_with_references

    prompt = _prompt_needing_alignment()
    refs = [*_ALIGNMENT_REFS, {"id": "node:frame", "label": "E01-01 尾帧", "kind": "image"}]
    frame_entity = {"node:frame": ("frame", "E01-01")}

    aligned = await align_prompt_with_references(
        prompt, refs, _CAST, _ART, ref_entities=frame_entity, frame_ids=("E01-01",)
    )

    assert "<Subject 3> — E01-01, the last frame of the previous segment" in aligned
    assert "light at the opening come entirely from <Picture 3>." in aligned
    assert (
        "retention_analysis:\n"
        "<Subject 2> (appears in [Shot 1]): fully_preserved - the pavilion's layout is retained.\n"
        "<Subject 1> (appears in [Shot 1]): partially_preserved - "
        "the face stays; posture changes.\n"
        "<Subject 3> ([Shot 1] opening frame): fully_preserved - positions, framing "
        "and light at the opening carry from the preceding segment's last frame.\n"
        "detailed_description:"
    ) in aligned
    assert (
        await align_prompt_with_references(
            aligned, refs, _CAST, _ART, ref_entities=frame_entity, frame_ids=("E01-01",)
        )
        == aligned
    )


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
    assert "<Subject 1> — 顾青; face comes from <Picture 1>." in aligned
    assert "<Subject 2> — 南岸候船亭; layout comes from <Picture 2>." in aligned
    assert "[Shot 1] <Subject 1> walks into <Subject 2>." in aligned
    assert (
        "retention_analysis:\n"
        "<Subject 2> (appears in [Shot 1]): fully_preserved - the pavilion's layout is retained.\n"
        "<Subject 1> (appears in [Shot 1]): partially_preserved - "
        "the face stays; posture changes.\n"
        "detailed_description:"
    ) in aligned
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
    # The scene card is connected too; it has no image yet but will be a reference.
    assert set(calls[0][1]["subject_1"]["criteria"]) == {
        "char:C01",
        "char:C02",
        "scene:S01",
        "unclear",
    }


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
        "<Subject 1> — 南岸候船亭; layout comes from <Picture 1>.\n"
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


@pytest.mark.asyncio
async def test_a_keyframe_prompt_on_a_card_with_reference_images_is_reported():
    """The canvas cards reference character sheets and scene art, not shot keyframes.

    A prompt without subject definitions ("Picture 1 (from Shot 1) aligns with
    0.00 s") would make the model take a character sheet for the first shot and
    never use the scene; the import reports it instead of passing it on silently.
    """
    from agentnexus.seedance.storyboard_import import align_prompt_with_references

    legacy = (
        "How the reference pictures align with the target video — Picture 1 (from Shot 1) "
        "aligns with the 0.00-second mark of the target video.\n\n"
        "integrated_multimodal_description:\n[Shot 1] A woman waits.\n\n"
        "overall_soundscape: rain\n\nnon_diegetic_music: N/A"
    )
    refs = [
        {"id": "node:a", "kind": "image", "label": "顾青 · 角色三视图"},
        {"id": "node:b", "kind": "image", "label": "南岸候船亭 · 场景概念图"},
    ]
    issues = []
    out = await align_prompt_with_references(legacy, refs, _FERRY_CAST, _FERRY_ART, issues=issues)
    assert out == legacy
    [issue] = issues
    assert "defines no <Subject N>" in issue
    assert "南岸候船亭 · 场景概念图" in issue

    # A card without reference images has nothing to tie.
    quiet = []
    await align_prompt_with_references(legacy, [], _FERRY_CAST, _FERRY_ART, issues=quiet)
    assert quiet == []


def _planned_canvas():
    """Cards created in the order an import makes them: sheets first, then scenes."""
    nodes = [
        {"id": "sb", "type": "storyboard", "title": "EP01 分镜表", "data": {}},
        {
            "id": "c01",
            "type": "image_prompt",
            "title": "顾青 · 角色三视图",
            "data": {"characterId": "C01"},
        },
        {
            "id": "c04",
            "type": "image_prompt",
            "title": "杜禾 · 角色三视图",
            "data": {"characterId": "C04"},
        },
        {
            "id": "c06",
            "type": "image_prompt",
            "title": "巡渡员 · 角色三视图",
            "data": {"characterId": "C06"},
        },
        {
            "id": "s01",
            "type": "image_prompt",
            "title": "南岸候船亭 · 场景概念图",
            "data": {"sceneId": "S01"},
        },
        {"id": "v1", "type": "video_prompt", "title": "E01-01", "data": {}},
        {"id": "v0", "type": "video_prompt", "title": "E00", "data": {}},
    ]
    # Edges in the order the storyboard names them — not the order Seedance numbers by.
    edges = [
        {"from": "sb", "to": "v1", "kind": "derives"},
        {"from": "c06", "to": "v1", "kind": "references"},
        {"from": "c04", "to": "v1", "kind": "references"},
        {"from": "c01", "to": "v1", "kind": "references"},
        {"from": "s01", "to": "v1", "kind": "references"},
        {"from": "v0", "to": "v1", "kind": "sequence"},
    ]
    return {"nodes": nodes, "edges": edges}


def test_planned_references_follow_canvas_node_order_not_edge_order():
    """Seedance numbers <Picture N> by node order and lists only sheets with an image.

    Before any sheet is generated it reports no references at all, so an import
    that trusted it never renumbered: a prompt written in speaking order kept
    "<Subject 1> = the harbor courier from <Picture 1>", while Picture 1 is the
    traveler's sheet (created first).
    """
    from agentnexus.seedance.storyboard_import import (
        _references_for_alignment,
        planned_image_references,
    )

    planned = planned_image_references(_planned_canvas(), "v1")
    assert [r["id"] for r in planned] == ["node:c01", "node:c04", "node:c06", "node:s01"]

    # No images yet: the planned order is used; with every image present, Seedance's own.
    assert _references_for_alignment([], planned) == planned
    actual = [
        {"id": f"node:{i}", "kind": "image", "label": i} for i in ("c01", "c04", "c06", "s01")
    ]
    assert _references_for_alignment(actual, planned) == actual

    canvas = _planned_canvas()
    canvas["nodes"][5]["data"]["excludedReferenceIds"] = ["node:c04"]
    assert "node:c04" not in [r["id"] for r in planned_image_references(canvas, "v1")]


def test_a_speaking_order_prompt_is_renumbered_to_the_canvas_order_before_images_exist():
    from agentnexus.seedance.storyboard_import import (
        align_h3_prompt_to_references,
        planned_image_references,
    )

    cast = {"characters": [{"id": "C01"}, {"id": "C04"}, {"id": "C06"}]}
    art = {"scenes": [{"id": "S01"}]}
    prompt = (
        "subject_definitions:\n"
        "<Subject 1> — C06, a harbor courier; face and clothing come from <Picture 1>.\n"
        "<Subject 2> — C04, the cartographer; face and clothing come from <Picture 2>.\n"
        "<Subject 3> — C01, the traveler; face and clothing come from <Picture 3>.\n"
        "<Subject 4> — S01, the ferry pavilion; layout and light come from <Picture 4>.\n"
        "summary: A parcel changes hands.\n"
        "retention_analysis: all retained.\n"
        "detailed_description:\n"
        "[Shot 1] <Subject 1> hands a parcel to <Subject 2> at <Subject 4>.\n"
        "[Shot 2] At 00:04.000, <Subject 3> checks her wristwatch.\n"
        "overall_soundscape: a continuous river breeze\nnon_diegetic_music: N/A"
    )
    refs = planned_image_references(_planned_canvas(), "v1")
    entities = {
        "node:c01": ("char", "C01"),
        "node:c04": ("char", "C04"),
        "node:c06": ("char", "C06"),
        "node:s01": ("scene", "S01"),
    }
    issues = []
    aligned = align_h3_prompt_to_references(
        prompt, refs, cast, art, ref_entities=entities, issues=issues
    )
    assert issues == []
    assert "<Subject 1> — C01, the traveler; face and clothing come from <Picture 1>." in aligned
    assert (
        "<Subject 3> — C06, a harbor courier; face and clothing come from <Picture 3>." in aligned
    )
    assert "[Shot 1] <Subject 3> hands a parcel to <Subject 2> at <Subject 4>." in aligned
    assert "[Shot 2] At 00:04.000, <Subject 1> checks her wristwatch." in aligned


# ---------------------------------------------------------------------------
# A segment that continues from the previous one's last frame
# ---------------------------------------------------------------------------

_FRAME_PROMPT = (
    "subject_definitions:\n"
    "<Subject 1> — E01-01, the last frame of the previous segment; the opening comes "
    "entirely from <Picture 1>.\n"
    "<Subject 2> — C01, the traveler; face and clothing come from <Picture 2>.\n"
    "<Subject 3> — S01, the ferry pavilion; layout and light come from <Picture 3>.\n"
    "<Picture 1> — E01-01, the previous last frame used as [Shot 1]'s first frame.\n"
    "summary: [reference generation + keyframe completion] The traveler turns, "
    "opening from <Picture 1>.\n"
    "retention_analysis:\n"
    "<Subject 1> (appears in [Shot 1]): fully_preserved - the opening positions stay fixed.\n"
    "<Subject 2> (appears in [Shot 1]): partially_preserved - appearance stays; pose changes.\n"
    "<Subject 3> (appears in [Shot 1]): fully_preserved - the pavilion's light is retained.\n"
    "<Picture 1> ([Shot 1] first frame): fully_preserved - its composition anchors the start.\n"
    "detailed_description:\n"
    "[Shot 1] Continuing from <Subject 1>, <Subject 2> turns at <Subject 3>.\n"
    "overall_soundscape: a continuous river breeze\nnon_diegetic_music: N/A"
)


def _updating_canvas(nodes):
    """Like :func:`_canvas`, and node updates change the node's data."""
    v3, state = _canvas(nodes)
    create_or_connect = v3.submit_command.side_effect

    async def submit(project_id, cmd):
        if cmd.get("type") == "canvas.update_node":
            node = next(n for n in state["nodes"] if n["id"] == cmd["nodeId"])
            node["data"] = dict(cmd["patch"]["data"])
            node["title"] = cmd["patch"].get("title", node.get("title"))
            return {"accepted": True}
        return await create_or_connect(project_id, cmd)

    v3.submit_command.side_effect = submit
    v3.get_node_references.return_value = []
    return v3, state


def _continuing_storyboard(second_prompt=""):
    second = _segment("E01-02", prompt=second_prompt)
    second["continuesFrom"] = "E01-01"
    return {
        "source": "Test",
        "episodes": [{"ep": 1, "segments": [_segment("E01-01"), second, _segment("E01-03")]}],
    }


def _cards(state):
    return {n["data"]["segmentId"]: n for n in state["nodes"] if n.get("type") == "video_prompt"}


def _frames(state):
    return [n for n in state["nodes"] if (n.get("data") or {}).get("continuationTargetNodeId")]


@pytest.mark.asyncio
@respx.mock
async def test_a_continuing_segment_gets_a_slot_for_the_previous_last_frame(tmp_path):
    storyboard = _continuing_storyboard()
    _write_production(tmp_path, cast=_CAST, art=_ART, storyboard=storyboard, script=_SCRIPT)
    v3, state = _updating_canvas(_TARGETS)

    await _run_import(tmp_path, v3, {"1": "ep1"})

    cards = _cards(state)
    [frame] = _frames(state)
    assert frame["type"] == "image"
    assert frame["title"].endswith(" 尾帧") and frame["title"].startswith("E01-01")
    assert frame["data"]["continuationSourceNodeId"] == cards["E01-01"]["id"]
    assert frame["data"]["continuationTargetNodeId"] == cards["E01-02"]["id"]
    assert {"from": frame["id"], "to": cards["E01-02"]["id"], "kind": "references"} in state[
        "edges"
    ]
    assert cards["E01-02"]["data"]["lastFrameContinuation"] is True
    assert cards["E01-02"]["data"]["continuesFrom"] == "E01-01"
    for seg in ("E01-01", "E01-03"):
        assert cards[seg]["data"]["lastFrameContinuation"] is False
    # Every segment stays in order on the canvas; only the flag decides continuation.
    sequence = {(e["from"], e["to"]) for e in state["edges"] if e["kind"] == "sequence"}
    assert len(sequence) == 2

    await _run_import(tmp_path, v3, {"1": "ep1"})
    assert len(_frames(state)) == 1, "a re-import reuses the slot"


@pytest.mark.asyncio
@respx.mock
async def test_a_last_frame_on_a_segment_that_does_not_continue_is_left_out(tmp_path):
    storyboard = _continuing_storyboard()
    _write_production(tmp_path, cast=_CAST, art=_ART, storyboard=storyboard, script=_SCRIPT)
    v3, state = _updating_canvas(_TARGETS)
    await _run_import(tmp_path, v3, {"1": "ep1"})
    cards = _cards(state)
    # Seedance attached E01-02's last frame to E01-03 before this flag existed.
    state["nodes"].append(
        {
            "id": "worker_frame",
            "type": "image",
            "title": "E01-02 尾帧",
            "data": {
                "activeOutputRef": "local://frame",
                "continuationSourceNodeId": cards["E01-02"]["id"],
                "continuationTargetNodeId": cards["E01-03"]["id"],
            },
        }
    )
    state["edges"].append(
        {"from": "worker_frame", "to": cards["E01-03"]["id"], "kind": "references"}
    )

    await _run_import(tmp_path, v3, {"1": "ep1"})

    assert _cards(state)["E01-03"]["data"]["excludedReferenceIds"] == ["node:worker_frame"]
    assert (
        "excludedReferenceIds" not in _cards(state)["E01-02"]["data"]
        or not _cards(state)["E01-02"]["data"]["excludedReferenceIds"]
    )


@pytest.mark.asyncio
@respx.mock
async def test_the_frame_subject_is_numbered_where_the_frame_will_be(tmp_path):
    storyboard = _continuing_storyboard(_FRAME_PROMPT)
    _write_production(tmp_path, cast=_CAST, art=_ART, storyboard=storyboard, script=_SCRIPT)
    v3, state = _updating_canvas(_TARGETS)

    await _run_import(tmp_path, v3, {"1": "ep1"})

    prompt = _cards(state)["E01-02"]["data"]["prompt"]
    # The sheets were created before the slot, so the frame is the last picture.
    assert "<Subject 3> — E01-01, the last frame of the previous segment" in prompt
    assert "the opening comes entirely from <Picture 3>." in prompt
    assert "<Subject 1> — C01, the traveler; face and clothing come from <Picture 1>." in prompt
    assert "[Shot 1] Continuing from <Subject 3>, <Subject 1> turns at <Subject 2>." in prompt
    assert (
        "<Picture 3> — E01-01, the previous last frame used as [Shot 1]'s first frame."
    ) in prompt
    assert (
        "summary: [reference generation + keyframe completion] The traveler turns, "
        "opening from <Picture 3>."
    ) in prompt
    assert (
        "retention_analysis:\n"
        "<Subject 3> (appears in [Shot 1]): fully_preserved - the opening positions stay fixed.\n"
        "<Subject 1> (appears in [Shot 1]): partially_preserved - "
        "appearance stays; pose changes.\n"
        "<Subject 2> (appears in [Shot 1]): fully_preserved - the pavilion's light is retained.\n"
        "<Picture 3> ([Shot 1] first frame): fully_preserved - "
        "its composition anchors the start.\n"
        "detailed_description:"
    ) in prompt
    saved = json.loads((tmp_path / "storyboard.json").read_text(encoding="utf-8"))
    assert saved["episodes"][0]["segments"][1]["h3Prompt"] == prompt


@pytest.mark.asyncio
@respx.mock
async def test_a_reimport_leaves_out_assets_a_segment_no_longer_uses(tmp_path):
    cast = {
        "characters": [
            *_CAST["characters"],
            {"id": "C02", "name": "罗婶", "image": {"sheet": "16:9 sheet prompt"}},
        ]
    }
    both = {
        "source": "Test",
        "episodes": [{"ep": 1, "segments": [_segment("E01-01", characters=("C01", "C02"))]}],
    }
    _write_production(tmp_path, cast=cast, art=_ART, storyboard=both, script=_SCRIPT)
    v3, state = _updating_canvas(_TARGETS)
    await _run_import(tmp_path, v3, {"1": "ep1"})
    card = _cards(state)["E01-01"]
    sheet = next(n for n in state["nodes"] if (n.get("data") or {}).get("characterId") == "C02")
    # A reference image the user wired in by hand is not the importer's to drop.
    state["nodes"].append({"id": "mood", "type": "image", "title": "mood board", "data": {}})
    state["edges"].append({"from": "mood", "to": card["id"], "kind": "references"})

    alone = {"source": "Test", "episodes": [{"ep": 1, "segments": [_segment("E01-01")]}]}
    _write_production(tmp_path, cast=cast, art=_ART, storyboard=alone, script=_SCRIPT)
    await _run_import(tmp_path, v3, {"1": "ep1"})
    assert _cards(state)["E01-01"]["data"]["excludedReferenceIds"] == [f"node:{sheet['id']}"]

    _write_production(tmp_path, cast=cast, art=_ART, storyboard=both, script=_SCRIPT)
    await _run_import(tmp_path, v3, {"1": "ep1"})
    assert _cards(state)["E01-01"]["data"]["excludedReferenceIds"] == []


def _chained_storyboard(*seg_ids):
    segments = [_segment(seg_ids[0])]
    for previous, seg_id in itertools.pairwise(seg_ids):
        segments.append({**_segment(seg_id), "continuesFrom": previous})
    return {"source": "Test", "episodes": [{"ep": 1, "segments": segments}]}


async def _imported_chain(tmp_path):
    """E01-01 → E01-02 → E01-03 imported, each later card with its last-frame slot."""
    storyboard = _chained_storyboard("E01-01", "E01-02", "E01-03")
    _write_production(tmp_path, cast=_CAST, art=_ART, storyboard=storyboard, script=_SCRIPT)
    v3, state = _updating_canvas(_TARGETS)
    await _run_import(tmp_path, v3, {"1": "ep1"})
    assert len(_frames(state)) == 2
    return v3, state


@pytest.mark.asyncio
@respx.mock
async def test_a_shorter_storyboard_deletes_the_cards_it_no_longer_has(tmp_path):
    v3, state = await _imported_chain(tmp_path)
    kept = _cards(state)["E01-01"]
    # A card the user made by hand is not the importer's, whatever its segment id.
    state["nodes"].append(
        {"id": "own", "type": "video_prompt", "title": "mine", "data": {"segmentId": "X-01"}}
    )

    _write_production(
        tmp_path, cast=_CAST, art=_ART, storyboard=_chained_storyboard("E01-01"), script=_SCRIPT
    )
    result = await _run_import(tmp_path, v3, {"1": "ep1"})

    assert set(_cards(state)) == {"E01-01", "X-01"}
    assert _cards(state)["E01-01"] is kept
    assert _frames(state) == []
    ids = {n["id"] for n in state["nodes"]}
    assert all(e["from"] in ids and e["to"] in ids for e in state["edges"])
    assert result["receipt"]["removed_stale_cards"] == ["E01-02", "E01-03"]
    assert result["receipt"]["removed_stale_frames"] == 2
    assert result["receipt"]["retired_stale_cards"] == []
    assert "E01-02, E01-03" in result["next_step"]


@pytest.mark.asyncio
@respx.mock
async def test_a_stale_card_holding_media_or_edits_is_renamed_not_deleted(tmp_path):
    v3, state = await _imported_chain(tmp_path)
    cards = _cards(state)
    state["outputs"][cards["E01-03"]["id"]] = "/v3/storage/generated"
    cards["E01-02"]["data"]["prompt"] = "rewritten on the canvas"

    _write_production(
        tmp_path, cast=_CAST, art=_ART, storyboard=_chained_storyboard("E01-01"), script=_SCRIPT
    )
    result = await _run_import(tmp_path, v3, {"1": "ep1"})

    assert result["receipt"]["retired_stale_cards"] == ["E01-02", "E01-03"]
    assert result["receipt"]["removed_stale_cards"] == []
    for seg_id in ("E01-02", "E01-03"):
        card = cards[seg_id]
        assert card in state["nodes"]
        assert card["title"].startswith("[已作废] ")
        assert card["data"]["retired"] is True
        assert "production_pointer" not in card["data"]
    assert cards["E01-02"]["data"]["prompt"] == "rewritten on the canvas"

    # The segment coming back gets a new card; the retired one keeps its media.
    _write_production(
        tmp_path,
        cast=_CAST,
        art=_ART,
        storyboard=_chained_storyboard("E01-01", "E01-03"),
        script=_SCRIPT,
    )
    await _run_import(tmp_path, v3, {"1": "ep1"})
    assert _cards(state)["E01-03"]["id"] != cards["E01-03"]["id"]
    assert cards["E01-03"]["title"].count("[已作废]") == 1


@pytest.mark.asyncio
@respx.mock
async def test_an_empty_slot_a_segment_no_longer_continues_from_is_deleted(tmp_path):
    v3, state = await _imported_chain(tmp_path)
    cards = _cards(state)
    used = next(
        f for f in _frames(state) if f["data"]["continuationTargetNodeId"] == cards["E01-03"]["id"]
    )

    storyboard = _chained_storyboard("E01-01", "E01-02", "E01-03")
    del storyboard["episodes"][0]["segments"][1]["continuesFrom"]
    _write_production(tmp_path, cast=_CAST, art=_ART, storyboard=storyboard, script=_SCRIPT)
    result = await _run_import(tmp_path, v3, {"1": "ep1"})

    assert _frames(state) == [used]
    assert set(_cards(state)) == {"E01-01", "E01-02", "E01-03"}
    assert not _cards(state)["E01-02"]["data"]["excludedReferenceIds"]
    assert result["receipt"]["removed_stale_frames"] == 1
    assert result["receipt"]["removed_stale_cards"] == []


@pytest.mark.asyncio
@respx.mock
async def test_a_stale_card_is_never_deleted_when_media_is_not_reported(tmp_path):
    v3, state = await _imported_chain(tmp_path)
    reporting = v3.get_snapshot.side_effect

    def without_media(project_id):
        snap = reporting(project_id)
        snap.pop("node_media")
        return snap

    v3.get_snapshot.side_effect = without_media
    _write_production(
        tmp_path, cast=_CAST, art=_ART, storyboard=_chained_storyboard("E01-01"), script=_SCRIPT
    )
    result = await _run_import(tmp_path, v3, {"1": "ep1"})

    assert result["receipt"]["removed_stale_cards"] == []
    assert result["receipt"]["retired_stale_cards"] == ["E01-02", "E01-03"]
    assert len(_frames(state)) == 2


_STATE_CAST = {
    "characters": [
        {
            "id": "C01",
            "name": "顾青",
            "image": {"sheet": "16:9 sheet prompt"},
            "states": [
                {
                    "id": "soaked",
                    "label": "淋雨后",
                    "descriptor": "Same traveler, soaked through.",
                    "image": {"prompt": "half-length state portrait", "sheet": "16:9 state sheet"},
                }
            ],
        }
    ]
}


def _state_segment(seg_id, state, prompt=""):
    seg = _segment(seg_id, prompt=prompt)
    seg["cuts"][0]["characterStates"] = {"C01": state}
    return seg


def _state_sheets(state):
    return [n for n in state["nodes"] if (n.get("data") or {}).get("characterState")]


def _references(state, card):
    return {
        e["from"] for e in state["edges"] if e["to"] == card["id"] and e["kind"] == "references"
    }


@pytest.mark.asyncio
@respx.mock
async def test_a_costume_state_is_referenced_through_its_own_sheet_card(tmp_path):
    plain = {
        "source": "Test",
        "episodes": [{"ep": 1, "segments": [_segment("E01-01"), _segment("E01-02")]}],
    }
    _write_production(tmp_path, cast=_STATE_CAST, art=_ART, storyboard=plain, script=_SCRIPT)
    v3, state = _updating_canvas(_TARGETS)
    await _run_import(tmp_path, v3, {"1": "ep1"})
    [main] = [n for n in state["nodes"] if (n.get("data") or {}).get("characterId") == "C01"]

    # The second segment now dresses the character in a costume state.
    dressed = {
        "source": "Test",
        "episodes": [
            {"ep": 1, "segments": [_segment("E01-01"), _state_segment("E01-02", "soaked")]}
        ],
    }
    _write_production(tmp_path, cast=_STATE_CAST, art=_ART, storyboard=dressed, script=_SCRIPT)
    result = await _run_import(tmp_path, v3, {"1": "ep1"})

    [sheet] = _state_sheets(state)
    assert sheet["title"] == "顾青 · 淋雨后 · 状态三视图"
    assert sheet["data"]["characterId"] == "C01"
    assert sheet["data"]["prompt"] == "16:9 state sheet"
    assert sheet["data"]["role"] == "character_sheet"
    assert sheet["data"]["production_pointer"] == "/characters/0/states/0/image/sheet"
    # The state sheet takes its face from the main sheet.
    assert {"from": main["id"], "to": sheet["id"], "kind": "references"} in state["edges"]
    cards = _cards(state)
    assert main["id"] in _references(state, cards["E01-01"])
    assert sheet["id"] not in _references(state, cards["E01-01"])
    assert sheet["id"] in _references(state, cards["E01-02"])
    # The first import wired the main sheet in; it no longer feeds that card.
    assert cards["E01-02"]["data"]["excludedReferenceIds"] == [f"node:{main['id']}"]
    assert result["receipt"]["character_state_assets_linked"] == 1

    await _run_import(tmp_path, v3, {"1": "ep1"})
    sheets = [n for n in state["nodes"] if (n.get("data") or {}).get("characterId") == "C01"]
    assert len(sheets) == 2, "a re-import reuses the main and the state sheet"


@pytest.mark.asyncio
@respx.mock
async def test_a_state_sheet_takes_the_characters_picture_number(tmp_path):
    from agentnexus.seedance.storyboard_import import planned_image_references

    on_canvas = [
        *_TARGETS,
        {
            "id": "main",
            "type": "image_prompt",
            "title": "顾青 · 角色三视图",
            "data": {"characterId": "C01"},
        },
        {
            "id": "scene",
            "type": "image_prompt",
            "title": "南岸候船亭 · 场景概念图",
            "data": {"sceneId": "S01"},
        },
    ]
    storyboard = {
        "source": "Test",
        "episodes": [
            {
                "ep": 1,
                "segments": [_state_segment("E01-01", "soaked", _prompt_needing_alignment())],
            }
        ],
    }
    _write_production(tmp_path, cast=_STATE_CAST, art=_ART, storyboard=storyboard, script=_SCRIPT)
    v3, state = _updating_canvas(on_canvas)

    result = await _run_import(tmp_path, v3, {"1": "ep1"})

    card = _cards(state)["E01-01"]
    [sheet] = _state_sheets(state)
    planned = planned_image_references(state, card["id"])
    assert [ref["id"] for ref in planned] == ["node:scene", f"node:{sheet['id']}"]
    # Authored with the character second, which is where its state sheet sits.
    assert card["data"]["prompt"] == _prompt_needing_alignment()
    assert result["receipt"]["subject_alignment_review"] == []


def test_missing_assets_require_a_sheet_for_every_costume_state_on_screen():
    from pathlib import Path

    from agentnexus.seedance.asset_anchors import _missing_visual_assets

    segments = [
        ({"ep": 1}, _state_segment("E01-01", "soaked")),
        ({"ep": 1}, _state_segment("E01-02", "masked")),
    ]
    cast = json.loads(json.dumps(_STATE_CAST))
    cast["characters"][0]["states"][0]["image"] = {}
    documents = ((Path("cast.json"), cast), (Path("art.json"), _ART))

    assert _missing_visual_assets(segments, [], *documents, script_doc=_SCRIPT) == [
        "Character C01 wears state masked, which cast.json does not define.",
        "Character C01 state soaked has no image.sheet in cast.json.",
    ]
    drawn = [
        {"id": n, "type": "image_prompt", "data": {"characterId": "C01", "characterState": s}}
        for n, s in (("a", "soaked"), ("b", "masked"))
    ]
    assert _missing_visual_assets(segments, drawn, *documents, script_doc=_SCRIPT) == []
