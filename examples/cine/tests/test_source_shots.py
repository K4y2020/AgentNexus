"""Source shots carried into the script and storyboard seeds."""

import json
import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "skills/film-analysis"))
from pipeline import production
from pipeline import source_shots as ss


def shot(index, start, end, place, what, **extra):
    return {
        "index": index,
        "start": start,
        "end": end,
        "place": place,
        "what": what,
        "teaser": False,
        "size": "medium",
        "camera": "Static Shot",
        "transition": "cut",
        **extra,
    }


MATERIAL = {
    "source_locations": {
        "people": [
            {"id": "P1", "look": "老年男子，蓑衣斗笠", "name_caption": None},
            {"id": "P2", "look": "年轻女子，蓝布衫", "name_caption": "沈知微"},
        ],
        "shots": [
            shot(0, 0.0, 2.0, "L1", "片头倒叙：雾中渡船", teaser=True),
            shot(1, 2.0, 5.0, "L1", "P1解开缆绳", size="wide", camera="Tracking Shot"),
            shot(2, 5.0, 6.5, "L1", "P2站在码头张望", size="close", transition="fade"),
            shot(3, 6.5, 9.0, "L2", "船舱里点起油灯"),
            shot(4, 9.0, 9.5, "L1", ""),
        ],
    }
}
OUTLINE = {
    "scenes": [
        {"id": "S01", "name": "雾中码头", "from": ["L1"]},
        {"id": "S02", "name": "船舱", "from": ["L2"]},
        {"id": "S03", "name": "新增梦境", "from": []},
    ],
    "episodes": [{"ep": 1, "sceneIds": ["S01", "S03"]}, {"ep": 2, "sceneIds": ["S02"]}],
}
ROWS = [
    {
        "start": 2.5,
        "end": 4.0,
        "text": "雾这么大还开船？",
        "speaker_name": "沈知微",
        "delivery": "spoken",
        "performance": {"speaker": "offscreen"},
    },
    {"start": 4.6, "end": 6.4, "text": "上来吧。", "speaker_name": "老周", "delivery": "spoken"},
    {
        "start": 7.0,
        "end": 8.0,
        "text": "灯亮了就不怕。",
        "speaker_name": "沈知微",
        "delivery": "voice_over",
    },
    {"start": 8.2, "end": 8.8, "text": "走吧。", "speaker": "extra", "speaker_name": "非角色人物"},
]


def test_scenes_take_the_body_shots_of_their_source_locations():
    found = ss.scene_shots(MATERIAL, OUTLINE)
    assert {k: [s["index"] for s in v] for k, v in found.items()} == {"S01": [1, 2], "S02": [3]}


def test_a_line_goes_to_the_shot_it_overlaps_most():
    shots = [s for s in MATERIAL["source_locations"]["shots"] if not s["teaser"]]
    placed = ss.lines_by_shot(ROWS, shots)
    assert [r["text"] for r in placed[id(shots[1])]] == ["上来吧。"]


def test_script_seed_lists_framing_speakers_and_readable_people():
    document = {"episodes": [{"ep": 1}, {"ep": 2}]}
    assert ss.add_source_shots("script", document, MATERIAL, OUTLINE, ROWS) == 3
    first, second = document["episodes"]
    assert first["sourceShots"] == {
        "S01 雾中码头": [
            "2.0s 全景·跟拍：老年男子解开缆绳 ｜ 沈知微（画外）「雾这么大还开船？」",
            "5.0s 特写·固定·渐黑入：沈知微站在码头张望 ｜ 老周「上来吧。」",
        ]
    }
    assert second["sourceShots"] == {
        "S02 船舱": [
            "6.5s 中景·固定：船舱里点起油灯 ｜ 沈知微（心声）「灯亮了就不怕。」；群演「走吧。」"
        ]
    }


def test_storyboard_seed_uses_its_own_vocabulary_per_scene():
    document = {"episodes": [{"ep": 1, "seedScenes": [{"sceneId": "S01"}, {"sceneId": "S03"}]}]}
    assert ss.add_source_shots("storyboard", document, MATERIAL, OUTLINE, ROWS) == 2
    scenes = document["episodes"][0]["seedScenes"]
    assert scenes[0]["sourceShots"] == [
        "2.0–5.0s · wide · Tracking Shot：老年男子解开缆绳 ｜ 「雾这么大还开船？」",
        "5.0–6.5s · close · Static Shot · fade in：沈知微站在码头张望 ｜ 「上来吧。」",
    ]
    assert "sourceShots" not in scenes[1]


def test_a_long_scene_is_sampled_evenly(monkeypatch):
    monkeypatch.setattr(ss, "MAX_SHOTS", 3)
    material = {
        "source_locations": {"shots": [shot(i, i, i + 1, "L1", f"第{i}镜") for i in range(9)]}
    }
    found = ss.scene_shots(material, OUTLINE)
    assert [s["index"] for s in found["S01"]] == [0, 3, 6]


def test_no_material_adds_nothing():
    document = {"episodes": [{"ep": 1}]}
    assert ss.add_source_shots("script", document, {}, OUTLINE, ROWS) == 0
    assert document == {"episodes": [{"ep": 1}]}


@pytest.mark.skipif(not shutil.which("node"), reason="native CLI requires node")
def test_controller_seeds_carry_source_shots(tmp_path):
    source = next((production.SKILLS / "cine-outline" / "examples").glob("*-outline.json"))
    outline = json.loads(source.read_text(encoding="utf-8"))
    for scene in outline["scenes"]:
        scene["from"] = ["L1"] if scene["id"] == "S01" else ["L2"]
    production.atomic_json(tmp_path / "outline.json", outline)
    production.seed(tmp_path, "script")
    plain = production.read(tmp_path / "script.json")
    assert all("sourceShots" not in episode for episode in plain["episodes"])

    (tmp_path / "script.json").unlink()
    production.atomic_json(tmp_path / "source-material.json", MATERIAL)
    production.seed(tmp_path, "script")
    seeded = production.read(tmp_path / "script.json")
    assert production.shape_errors("script", seeded) == []
    shots = seeded["episodes"][1]["sourceShots"]
    assert list(shots) == [f"S01 {outline['scenes'][0]['name']}"]
    assert shots[f"S01 {outline['scenes'][0]['name']}"][0].startswith(
        "2.0s 全景·跟拍：老年男子解开缆绳"
    )
