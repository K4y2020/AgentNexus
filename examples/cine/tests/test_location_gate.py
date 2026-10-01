"""Source locations constrain scene coverage and the placement of faithful lines."""

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "skills/film-analysis"))
from pipeline import location_gate as gate
from pipeline import production


def material():
    return {
        "source_locations": {
            "status": "read",
            "locations": [
                {
                    "id": "L1",
                    "name": "街头算命摊",
                    "seconds": 17.6,
                    "spans": [{"start": 0, "end": 17.6}],
                    "line_numbers": [1],
                },
                {
                    "id": "L2",
                    "name": "灵堂",
                    "seconds": 3.7,
                    "spans": [{"start": 17.6, "end": 21.3}],
                    "line_numbers": [2],
                    "lines": [{"text": "你的命怎么这么苦啊"}],
                },
            ],
            "shots": [],
        }
    }


def outline(mode="忠实"):
    return {
        "source": "film",
        "params": {"adaptMode": mode},
        "adaptation": {"cut": [], "merge": []},
        "characters": [],
        "beats": [],
        "episodes": [],
        "scenes": [
            {"id": "S01", "name": "街头算命摊", "from": ["L1"]},
            {"id": "S02", "name": "灵堂", "from": ["L2"]},
        ],
    }


def script(scene="S01", lines=None):
    return {
        "source": "film",
        "episodes": [
            {
                "ep": 1,
                "scenes": [
                    {
                        "sceneId": scene,
                        "flow": [
                            {"speaker": "C01", "line": text}
                            for text in (lines or ["你的命怎么这么苦啊"])
                        ],
                    }
                ],
            }
        ],
    }


def crossing_material(cut=3, end=10):
    source = material()
    for location, start, stop in zip(
        source["source_locations"]["locations"], (0, cut), (cut, end), strict=True
    ):
        location.update(
            seconds=stop - start,
            spans=[{"start": start, "end": stop}],
            line_numbers=[],
            lines=[],
        )
    return source


def test_faithful_outline_reports_missing_location_and_invented_scene_together():
    doc = outline()
    doc["scenes"][1] = {"id": "S04", "name": "贾府回廊过道"}
    result = gate.check_outline(doc, material())
    assert result["status"] == "failed"
    assert any("L2 灵堂" in e and "17.6–21.3s" in e for e in result["blocking"])
    assert any("S04 贾府回廊过道" in e and "来源" in e for e in result["blocking"])
    assert gate.check_outline(outline(), material())["status"] == "passed"


def test_merging_needs_the_exact_location_set_order_does_not_matter():
    doc = outline()
    doc["scenes"] = [{"id": "S01", "name": "合并场景", "from": ["L1", "L2"]}]
    assert gate.check_outline(doc, material())["status"] == "failed"
    doc["adaptation"]["merge"] = [{"locations": ["L2", "L1"], "what": "合并", "why": "压缩"}]
    assert gate.check_outline(doc, material())["status"] == "passed"
    doc["adaptation"]["merge"][0]["locations"] = ["L1"]
    assert gate.check_outline(doc, material())["status"] == "failed"


def test_extraction_requires_declared_cuts_and_reviews_new_scenes():
    doc = outline("抽核")
    doc["scenes"][1] = {"id": "S04", "name": "原创场景"}
    result = gate.check_outline(doc, material())
    assert result["status"] == "failed" and result["review"]
    doc["adaptation"]["cut"] = [{"locations": ["L2"], "what": "灵堂", "why": "压缩"}]
    result = gate.check_outline(doc, material())
    assert result["status"] == "passed"
    assert result["review"][0]["reason"] == "scene_location_original"


def test_short_locations_with_dialogue_and_accumulated_spans_are_required():
    source = material()
    location = source["source_locations"]["locations"][1]
    location["seconds"] = 0.4
    doc = outline()
    doc["scenes"].pop()
    assert gate.check_outline(doc, source)["status"] == "failed"
    location["line_numbers"] = []
    assert gate.check_outline(doc, source)["status"] == "passed"
    location.update(seconds=3, spans=[{"start": 17.6, "end": 19.1}, {"start": 20, "end": 21.5}])
    assert gate.check_outline(doc, source)["status"] == "failed"


@pytest.mark.parametrize("origins", [[], None, ["L99"], ["L1", "L1"], "L1"])
def test_invalid_scene_sources_fail(origins):
    doc = outline()
    doc["scenes"][0]["from"] = origins
    assert gate.check_outline(doc, material())["status"] == "failed"


def test_free_adaptation_skips_both_checks_even_without_source():
    doc = outline("借壳")
    for result in (gate.check_outline(doc, None), gate.check_script(script(), doc, None, None)):
        assert result["status"] == "skipped" and result["skipped"] == "free adaptation"


@pytest.mark.parametrize("mode", ["忠实", "抽核"])
def test_missing_or_unavailable_source_locations_are_reviewed(mode):
    for source in (None, {}, {"source_locations": {"status": "unavailable"}}):
        result = gate.check_outline(outline(mode), source)
        assert result["status"] == "unavailable"
        assert result["review"][0]["reason"] == "source_locations_missing"


def test_partial_source_and_unknown_shots_are_reviewed_even_if_known_locations_are_covered():
    source = material()
    source["source_locations"].update(status="partial", shots=[{"index": 8, "place": "?"}])
    result = gate.check_outline(outline(), source)
    assert result["status"] == "passed"
    assert {r["reason"] for r in result["review"]} == {
        "source_locations_partial",
        "source_locations_unknown",
    }


def test_wrong_scene_fails_without_any_performance_metadata():
    rows = [{"id": "line1", "start": 18, "end": 19, "text": "你的命怎么这么苦啊"}]
    result = gate.check_script(script(), outline(), material(), rows)
    assert result["status"] == "failed" and result["checked"] == 1
    assert "灵堂" in result["blocking"][0] and "街头算命摊（S01）" in result["blocking"][0]
    assert gate.check_script(script("S02"), outline(), material(), rows)["status"] == "passed"


@pytest.mark.parametrize(
    ("cut", "allowed"),
    [
        (2.9, {"S02"}),
        (3, {"S01", "S02"}),
        (5, {"S01", "S02"}),
        (7, {"S01", "S02"}),
        (7.1, {"S01"}),
    ],
)
def test_cross_cut_dialogue_accepts_each_location_with_thirty_percent(cut, allowed):
    source = crossing_material(cut)
    rows = [{"text": "你的命怎么这么苦啊", "start": 0, "end": 10}]
    for scene in ("S01", "S02"):
        result = gate.check_script(script(scene), outline(), source, rows)
        assert result["checked"] == 1
        assert result["status"] == ("passed" if scene in allowed else "failed")


def test_split_dialogue_keeps_the_full_source_interval_across_a_cut():
    source = crossing_material(cut=3)
    rows = [{"text": "我们到了门口，拿出钥匙，然后继续往前走", "start": 2.9, "end": 8}]
    lines = ["我们到了门口", "拿出钥匙", "然后继续往前走"]
    result = gate.check_script(script("S02", lines), outline(), source, rows)
    assert result["status"] == "passed" and result["checked"] == 3
    result = gate.check_script(script("S01", lines), outline(), source, rows)
    assert result["status"] == "failed" and len(result["blocking"]) == 3


def test_red_mansion_cross_cut_lines_do_not_block_the_gate_and_hall_scenes():
    source = material()
    source["source_locations"]["locations"] = [
        {"id": "L2", "name": "灵堂", "spans": [{"start": 17.6333125, "end": 21.2666875}]},
        {"id": "L3", "name": "大门外", "spans": [{"start": 21.2666875, "end": 29.0333125}]},
        {"id": "L4", "name": "大厅", "spans": [{"start": 29.0333125, "end": 104.0333125}]},
    ]
    doc = outline()
    doc["scenes"] = [
        {"id": "S01", "name": "算命摊", "from": ["L1"]},
        {"id": "S02", "name": "大门外", "from": ["L3"]},
        {"id": "S03", "name": "大厅", "from": ["L4"]},
    ]
    voiceover = [
        "我叫林黛玉，是林氏集团的继承人。",
        "老妈刚刚去世，家里一团乱麻，",
        "所以老爸让我来外婆家住一阵子。",
    ]
    greeting = "我的好孙女，你妈怎么就忍心丢下我们走了！"
    rows = [
        {"text": "妈", "start": 16.625, "end": 17.675},
        {"text": "你的命怎么这么苦啊", "start": 18.025, "end": 19.775},
        {"text": "".join(voiceover), "start": 20.9, "end": 28.5},
        {"text": greeting, "start": 28.5, "end": 32.02},
    ]
    screenplay = script(lines=["妈", "你的命怎么这么苦啊"])
    screenplay["episodes"][0]["scenes"].extend(
        [
            script("S02", voiceover)["episodes"][0]["scenes"][0],
            script("S03", [greeting])["episodes"][0]["scenes"][0],
        ]
    )
    result = gate.check_script(screenplay, doc, source, rows)
    assert result["checked"] == 5 and len(result["blocking"]) == 1
    assert "你的命怎么这么苦啊" in result["blocking"][0]
    assert len(result["review"]) == 1 and "「妈」" in result["review"][0]["message"]
    gate.attach_dialogue(source["source_locations"], rows)
    assert [loc["line_numbers"] for loc in source["source_locations"]["locations"]] == [
        [2],
        [3],
        [4],
    ]


def test_overlap_sums_revisited_spans_of_the_same_location():
    source = crossing_material()
    first, second = source["source_locations"]["locations"]
    first["spans"] = [{"start": 0, "end": 2}, {"start": 6, "end": 8}]
    second["spans"] = [{"start": 2, "end": 6}, {"start": 8, "end": 10}]
    rows = [{"text": "你的命怎么这么苦啊", "start": 0, "end": 10}]
    for scene in ("S01", "S02"):
        assert gate.check_script(script(scene), outline(), source, rows)["status"] == "passed"


def test_inserted_frame_dialogue_with_only_a_sliver_in_a_location_is_reviewed():
    source = material()
    first, second = source["source_locations"]["locations"]
    first["spans"] = [{"start": 0, "end": 15.7}]
    second["spans"] = [{"start": 17.633, "end": 21.3}]
    rows = [{"text": "妈", "start": 16.625, "end": 17.675}]
    result = gate.check_script(script(lines=["妈"]), outline(), source, rows)
    assert result["status"] == "passed" and result["checked"] == 0
    assert result["review"][0]["reason"] == "dialogue_location_unknown"
    gate.attach_dialogue(source["source_locations"], rows)
    assert all(not location["line_numbers"] for location in (first, second))


@pytest.mark.parametrize("end", [None, 17, 18, float("nan"), float("inf"), True])
def test_missing_or_invalid_line_duration_is_reviewed(end):
    rows = [{"text": "你的命怎么这么苦啊", "start": 18, "end": end}]
    result = gate.check_script(script("S02"), outline(), material(), rows)
    assert result["checked"] == 0 and not result["blocking"]
    assert result["review"][0]["reason"] == "dialogue_location_unknown"


def test_boundary_belongs_to_next_location_and_teaser_is_not_paired():
    rows = [
        {"text": "你的命怎么这么苦啊", "start": 0, "end": 1, "teaser": True},
        {"text": "你的命怎么这么苦啊", "start": 17.6, "end": 18},
    ]
    result = gate.check_script(script("S02"), outline(), material(), rows)
    assert result["status"] == "passed" and result["checked"] == 1


def test_duplicate_lines_match_distinct_occurrences_in_order():
    rows = [
        {"text": "妈", "start": 1, "end": 2},
        {"text": "妈", "start": 18, "end": 19},
    ]
    result = gate.check_script(script(lines=["妈", "妈"]), outline(), material(), rows)
    assert result["checked"] == 2 and result["status"] == "failed"
    assert len(result["blocking"]) == 1


def test_unknown_dialogue_location_is_reviewed():
    rows = [{"text": "你的命怎么这么苦啊", "start": 99, "end": 100}]
    result = gate.check_script(script(), outline(), material(), rows)
    assert result["status"] == "passed" and result["checked"] == 0
    assert result["review"][0]["reason"] == "dialogue_location_unknown"


def test_production_runs_location_check_after_node_and_records_dependencies(monkeypatch, tmp_path):
    doc = outline()
    doc["scenes"].pop()
    for filename, value in (("outline.json", doc), ("source-material.json", material())):
        (tmp_path / filename).write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(production.shutil, "which", lambda _n: "node")
    monkeypatch.setattr(
        production.subprocess,
        "run",
        lambda *_a, **_k: SimpleNamespace(returncode=0, stdout="", stderr=""),
    )
    result = production.check_stage(tmp_path, "outline")
    assert result["status"] == "failed" and "灵堂" in result["errors"][0]
    assert result["input_hashes"][
        str(tmp_path / "source-material.json")
    ] == production.fingerprint(tmp_path / "source-material.json")
    missing = tmp_path / "source-transcript.json"
    assert str(missing) in result["input_hashes"] and result["input_hashes"][str(missing)] is None


@pytest.mark.parametrize(
    ("end", "start", "scene", "status"),
    [(10, 1.9, "S02", "passed"), (3.5, 1, "S01", "failed")],
)
def test_outline_check_reassigns_short_location_dialogue_by_overlap(
    monkeypatch, tmp_path, end, start, scene, status
):
    source = crossing_material(cut=2, end=end)
    source["source_locations"]["locations"][0]["line_numbers"] = [1]
    fingerprint = {"size_bytes": 10, "sha256_head": "a" * 64, "sha256_tail": "b" * 64}
    source["source_fingerprint"] = fingerprint
    doc = outline()
    doc["scenes"] = [row for row in doc["scenes"] if row["id"] == scene]
    for filename, value in (
        ("outline.json", doc),
        ("source-material.json", source),
        (
            "source-transcript.json",
            {
                "source_fingerprint": fingerprint,
                "segments": [{"text": "跨切换点的台词", "start": start, "end": end}],
            },
        ),
    ):
        (tmp_path / filename).write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(production.shutil, "which", lambda _n: "node")
    monkeypatch.setattr(
        production.subprocess,
        "run",
        lambda *_a, **_k: SimpleNamespace(returncode=0, stdout="", stderr=""),
    )
    result = production.check_stage(tmp_path, "outline")
    assert result["status"] == status
    if status == "failed":
        assert "L2" in result["errors"][0] and "跨切换点的台词" in result["errors"][0]


def test_production_script_check_uses_timed_rows_and_blocks_wrong_scene(monkeypatch, tmp_path):
    source = material()
    fingerprint = {"size_bytes": 10, "sha256_head": "a" * 64, "sha256_tail": "b" * 64}
    source["source_fingerprint"] = fingerprint
    rows = [{"id": "a", "text": "你的命怎么这么苦啊", "start": 18, "end": 19}]
    docs = {
        "outline.json": outline(),
        "script.json": script(),
        "cast.json": {"source": "film", "characters": []},
        "art.json": {"source": "film", "scenes": []},
        "source-material.json": source,
        "source-transcript.json": {"source_fingerprint": fingerprint, "segments": rows},
    }
    for filename, value in docs.items():
        (tmp_path / filename).write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    attributed = tmp_path / "source-transcript-attributed.json"
    production.atomic_json(
        attributed,
        {
            "attributions": rows,
            "input_sha256": production.attribution_input_hashes(
                tmp_path,
                tmp_path / "source-transcript.json",
                tmp_path / "cast.json",
            ),
        },
    )
    monkeypatch.setattr(production.shutil, "which", lambda _n: "node")
    monkeypatch.setattr(
        production.subprocess,
        "run",
        lambda *_a, **_k: SimpleNamespace(returncode=0, stdout="", stderr=""),
    )
    result = production.check_stage(tmp_path, "script")
    assert result["status"] == "failed" and result["locations"]["checked"] == 1
    assert "S01" in result["errors"][0] and "灵堂" in result["errors"][0]
    production.atomic_json(tmp_path / "script.json", script("S02"))
    assert production.check_stage(tmp_path, "script")["status"] == "passed"


def test_locations_pair_after_many_declared_cuts_and_split_lines_keep_their_time():
    rows = [
        *({"text": f"不保留的台词{i}", "start": i, "end": i + 1} for i in range(12)),
        {"text": "你的命怎么这么苦啊，妈", "start": 18, "end": 19},
    ]
    result = gate.check_script(
        script("S02", ["你的命怎么这么苦啊", "妈"]), outline(), material(), rows
    )
    assert result["status"] == "passed" and result["checked"] == 2


def test_unpaired_dialogue_cannot_silently_pass_location_check():
    result = gate.check_script(script(), outline(), material(), [])
    assert result["review"][0]["reason"] == "dialogue_location_unpaired"
