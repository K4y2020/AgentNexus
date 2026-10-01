"""Visual identities, deterministic A/B merging and scene presence without live models."""

import copy
import json
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "skills/film-analysis"))
from pipeline import (
    attribute_speakers,
    jev_client,
    line_judgment,
    line_performance,
    people_gate,
    person_map,
    speaker_evidence,
)

CAST = {
    "characters": [
        {
            "id": "C01",
            "name": "队长",
            "aliases": ["阿宁"],
            "persona": {"gender": "女", "ageRange": "25岁", "appearance": "短发，蓝外套"},
        },
        {"id": "C02", "name": "司机", "persona": {"gender": "男", "ageRange": "45岁"}},
        {"id": "C03", "name": "店员", "persona": {"gender": "男", "ageRange": "30岁"}},
    ]
}
NOTES = {
    "schema_version": 3,
    "status": "read",
    "model": "vision",
    "people": [
        {"id": "P1", "look": "年轻女子，短发，蓝外套", "name_caption": "阿宁"},
        {"id": "P2", "look": "中年男子，灰外套", "name_caption": None},
    ],
    "shots": [{"index": 0, "start": 0, "end": 5, "place": "L1", "people": ["P1", "P2"]}],
    "locations": [{"id": "L1", "name": "候车室", "spans": [{"start": 0, "end": 5}], "seconds": 5}],
}
MAPPING = {
    "people": [
        {"person_id": "P1", "character_id": "C01", "status": "matched"},
        {"person_id": "P2", "character_id": "C02", "status": "matched"},
    ]
}


def row(speaker="P1", notes=NOTES):
    value = {
        "id": "a",
        "text": "请在这里等一下",
        "start": 1,
        "end": 3,
        "performance": {
            "speaker": speaker,
            "emotion": "平静",
            "at": [1.6, 2.5],
            "schema_version": 2,
            "model": "vision",
        },
    }
    value["performance"]["input_sha256"] = line_performance.performance_inputs(notes, value)
    return value


def answer(choice, confidence=0.95):
    return SimpleNamespace(
        choice=choice,
        confidence=confidence,
        probabilities={choice: confidence, "unclear": 1 - confidence},
    )


def mapping_client(monkeypatch, choose=None):
    calls = []

    class Client:
        def __enter__(self):
            return self

        def __exit__(self, *_a):
            return False

    def ask(_client, _state, questions, _model):
        calls.append(copy.deepcopy(questions))
        return SimpleNamespace(
            answers={
                pid: answer(
                    choose(pid, question)
                    if choose
                    else {"P2": "C02", "P3": "C03"}.get(pid, "extra")
                )
                for pid, question in questions.items()
            }
        )

    monkeypatch.setattr(jev_client, "open_client", lambda _k: Client())
    monkeypatch.setattr(jev_client, "_find_api_key", lambda: "key")
    monkeypatch.setattr(jev_client, "_ask_batched", ask)
    return calls


def test_caption_matches_an_exact_name_or_alias_without_jev():
    result = person_map.map_people({"people": NOTES["people"][:1]}, CAST, api_key="")
    assert result["people"][0]["character_id"] == "C01"
    assert result["people"][0]["method"] == "name_caption"
    assert result["status"] == "mapped"


def test_appearance_ambiguity_is_reviewed_and_extras_are_excluded(monkeypatch):
    class Client:
        def __enter__(self):
            return self

        def __exit__(self, *_a):
            return False

    monkeypatch.setattr(jev_client, "open_client", lambda _k: Client())
    monkeypatch.setattr(
        jev_client,
        "_ask_batched",
        lambda *_a: SimpleNamespace(
            answers={
                "P2": answer("C02", 0.5),
                "P3": answer("extra", 0.95),
            }
        ),
    )
    result = person_map.map_people(
        {"people": [NOTES["people"][1], {"id": "P3", "look": "远处人群", "name_caption": None}]},
        CAST,
        api_key="k",
    )
    assert [r["status"] for r in result["people"]] == ["review", "extra"]
    assert speaker_evidence.mapping_ids(result) == {}


def test_person_map_reuses_exact_inputs_and_reopens_when_cast_or_notes_change(
    monkeypatch, tmp_path
):
    cast_path = tmp_path / "cast.json"
    cast_path.write_text(json.dumps(CAST), encoding="utf-8")
    calls = mapping_client(monkeypatch)
    notes = copy.deepcopy(NOTES)
    first = person_map.ensure_person_map(tmp_path, notes, cast_path)
    assert person_map.ensure_person_map(tmp_path, notes, cast_path)["people"] == first["people"]
    assert len(calls) == 1
    notes["people"][0]["look"] += "，戴帽子"
    person_map.ensure_person_map(tmp_path, notes, cast_path)
    cast_path.write_text(json.dumps({"characters": CAST["characters"][:2]}), encoding="utf-8")
    person_map.ensure_person_map(tmp_path, notes, cast_path)
    assert len(calls) == 3


def test_person_mapping_ignores_unrelated_edits_and_remaps_only_changed_person(
    monkeypatch, tmp_path
):
    cast = copy.deepcopy(CAST)
    notes = copy.deepcopy(NOTES)
    cast_path = tmp_path / "cast.json"
    cast_path.write_text(json.dumps(cast), encoding="utf-8")
    notes["people"].append({"id": "P3", "look": "穿绿背心的店员", "name_caption": None})
    notes["shots"].append(
        {"index": 1, "start": 5, "end": 10, "place": "L2", "people": ["P3"], "what": "P3 整理货架"}
    )
    calls = mapping_client(monkeypatch)
    person_map.ensure_person_map(tmp_path, notes, cast_path)
    notes["locations"][0]["name"] = "修改地点名称"
    notes["attempts"] = [{"seconds": 100}]
    cast["characters"][0]["voice"] = {"timbre": "低沉"}
    cast["characters"][0].setdefault("image", {})["sheet"] = "新版设定图"
    cast_path.write_text(json.dumps(cast, indent=2), encoding="utf-8")
    person_map.ensure_person_map(tmp_path, notes, cast_path)
    assert len(calls) == 1
    notes["people"][1]["look"] += "，戴帽子"
    person_map.ensure_person_map(tmp_path, notes, cast_path)
    assert list(calls[-1]) == ["P2"] and len(calls) == 2
    notes["shots"][1]["what"] = "P3 擦拭货架"
    person_map.ensure_person_map(tmp_path, notes, cast_path)
    assert list(calls[-1]) == ["P3"] and len(calls) == 3
    cast["characters"][0]["name"] += "的新名字"
    cast_path.write_text(json.dumps(cast), encoding="utf-8")
    person_map.ensure_person_map(tmp_path, notes, cast_path)
    assert list(calls[-1]) == ["P2", "P3"] and len(calls) == 4


def test_appearance_only_legacy_mapping_cannot_bypass_new_context(monkeypatch, tmp_path):
    import hashlib

    raw = json.dumps(CAST).encode()
    cast_path = tmp_path / "cast.json"
    cast_path.write_bytes(raw)
    old = {
        **copy.deepcopy(MAPPING),
        "schema_version": 1,
        "model": "jev-latest",
        "input_sha256": {
            "cast": hashlib.sha256(raw).hexdigest(),
            "shot_notes": person_map.digest(NOTES),
            "mapper": "appearance-only-mapper",
        },
    }
    (tmp_path / "person-map.json").write_text(json.dumps(old), encoding="utf-8")
    calls = mapping_client(monkeypatch)
    result = person_map.ensure_person_map(tmp_path, NOTES, cast_path)
    assert [r["status"] for r in result["people"]] == [r["status"] for r in old["people"]]
    assert list(calls[0]) == ["P2"]
    assert result["people"][1].get("input_sha256", {}).get("context")


@pytest.mark.parametrize("mode", ["忠实", "抽核", "借壳"])
def test_missing_and_extra_scene_people_follow_the_adaptation_mode(mode):
    outline = {"params": {"adaptMode": mode}, "scenes": [{"id": "S01", "from": ["L1"]}]}
    script = {
        "episodes": [{"scenes": [{"sceneId": "S01", "characters": ["C01", "C03"], "flow": []}]}]
    }
    result = people_gate.check_people(
        script, outline, {"source_locations": NOTES}, CAST, MAPPING, []
    )
    if mode == "忠实":
        assert result["status"] == "failed" and len(result["blocking"]) == 2
        assert "司机" in result["blocking"][0] and "0.0–5.0s" in result["blocking"][0]
    elif mode == "抽核":
        assert not result["blocking"] and len(result["review"]) == 2
    else:
        assert result["status"] == "skipped"


def test_used_uncertain_speaker_includes_evidence_for_review():
    screenplay = {
        "episodes": [
            {
                "scenes": [
                    {"sceneId": "S01", "flow": [{"speaker": "C01", "line": "请在这里等一下"}]}
                ]
            }
        ]
    }
    source = [
        {
            **row(),
            "speaker_needs_review": True,
            "evidence": {
                "a": {"person_id": "unclear"},
                "b": {},
                "present_people": [],
                "constraints": {"status": "review"},
            },
        }
    ]
    result = people_gate.used_unsettled(screenplay, source)
    assert (
        result[0]["text"] == source[0]["text"] and result[0]["evidence"] == source[0]["evidence"]
    )


def test_visible_worker_maps_to_extra_using_the_actual_appearance_question(monkeypatch):
    class Client:
        def __enter__(self):
            return self

        def __exit__(self, *_a):
            return False

    def ask(_client, _state, questions, _model):
        question = questions["P3"]
        assert "工作人员" in question.criteria["extra"]
        assert "看不清" in question.criteria["unclear"]
        assert "反光背心" in question.instructions
        return SimpleNamespace(answers={"P3": answer("extra")})

    monkeypatch.setattr(jev_client, "open_client", lambda _k: Client())
    monkeypatch.setattr(jev_client, "_ask_batched", ask)
    result = person_map.map_people(
        {"people": [{"id": "P3", "look": "戴安全帽、穿反光背心的工人", "name_caption": None}]},
        CAST,
        api_key="k",
    )
    assert result["status"] == "mapped"
    assert result["people"][0]["status"] == "extra"
    assert result["people"][0]["character_id"] is None


def test_merged_dialogue_keeps_the_unsettled_response_after_a_confirmed_extra():
    source = [
        {"id": "a", "text": "请稍等一下", "speaker": "extra", "speaker_needs_review": False},
        {"id": "b", "text": "马上就来", "speaker": "unclear", "speaker_needs_review": True},
        {"id": "c", "text": "没有问题", "speaker": "unclear", "speaker_needs_review": True},
    ]
    script = {
        "episodes": [
            {
                "scenes": [
                    {
                        "sceneId": "S01",
                        "flow": [{"speaker": "extra", "line": "请稍等一下，马上就来"}],
                    }
                ]
            }
        ]
    }
    assert [r["source_id"] for r in people_gate.used_unsettled(script, source)] == ["b"]


def test_merged_dialogue_keeps_an_unsettled_response_across_an_omitted_source_line():
    source = [
        {"id": "a", "text": "请稍等一下", "speaker": "extra", "speaker_needs_review": False},
        {"id": "b", "text": "今晚下雨", "speaker": "C01", "speaker_needs_review": False},
        {"id": "c", "text": "马上就来", "speaker": "unclear", "speaker_needs_review": True},
    ]
    script = {
        "episodes": [
            {
                "scenes": [
                    {
                        "sceneId": "S01",
                        "flow": [{"speaker": "extra", "line": "请稍等一下，马上就来"}],
                    }
                ]
            }
        ]
    }
    assert [r["source_id"] for r in people_gate.used_unsettled(script, source)] == ["c"]


def test_confirmed_extra_does_not_count_as_a_missing_main_character():
    notes = copy.deepcopy(NOTES)
    notes["shots"][0]["people"] = ["P1"]
    mapping = {"people": [{"person_id": "P1", "status": "extra"}]}
    source = speaker_evidence.attribute([row(notes=notes)], CAST, notes, mapping)
    outline = {"params": {"adaptMode": "忠实"}, "scenes": [{"id": "S01", "from": ["L1"]}]}
    script = {
        "episodes": [
            {
                "scenes": [
                    {
                        "sceneId": "S01",
                        "characters": [],
                        "flow": [{"speaker": "extra", "line": "请在这里等一下"}],
                    }
                ]
            }
        ]
    }
    result = people_gate.check_people(
        script, outline, {"source_locations": notes}, CAST, mapping, source["attributions"]
    )
    assert result["status"] == "passed" and not result["blocking"] and not result["review"]
    assert people_gate.used_unsettled(script, source["attributions"]) == []


def test_partial_identity_evidence_does_not_prove_absence():
    mapping = {"people": MAPPING["people"][:1]}
    outline = {"params": {"adaptMode": "忠实"}, "scenes": [{"id": "S01", "from": ["L1"]}]}
    script = {"episodes": [{"scenes": [{"sceneId": "S01", "characters": ["C01", "C03"]}]}]}
    result = people_gate.check_people(
        script, outline, {"source_locations": NOTES}, CAST, mapping, []
    )
    assert not result["blocking"]
    assert {r["reason"] for r in result["review"]} == {
        "source_people_partial",
        "source_person_extra",
    }


def test_spoken_character_is_required_even_without_majority_shots():
    notes = copy.deepcopy(NOTES)
    notes["shots"] = [
        {"start": i, "end": i + 1, "place": "L1", "people": ["P1"] if i == 0 else ["P2"]}
        for i in range(5)
    ]
    outline = {"params": {"adaptMode": "忠实"}, "scenes": [{"id": "S01", "from": ["L1"]}]}
    script = {"episodes": [{"scenes": [{"sceneId": "S01", "characters": ["C02"]}]}]}
    attribution = [
        {
            "speaker": "C01",
            "start": 0,
            "end": 1,
            "delivery": "spoken",
            "speaker_needs_review": False,
        }
    ]
    result = people_gate.check_people(
        script, outline, {"source_locations": notes}, CAST, MAPPING, attribution
    )
    assert len(result["blocking"]) == 1 and "队长" in result["blocking"][0]


def test_unsettled_speech_cannot_prove_an_unseen_role_never_spoke():
    outline = {"params": {"adaptMode": "忠实"}, "scenes": [{"id": "S01", "from": ["L1"]}]}
    script = {"episodes": [{"scenes": [{"sceneId": "S01", "characters": ["C01", "C02", "C03"]}]}]}
    source = [{"speaker": "unclear", "speaker_needs_review": True, "start": 1, "end": 2}]
    result = people_gate.check_people(
        script, outline, {"source_locations": NOTES}, CAST, MAPPING, source
    )
    assert not result["blocking"]
    assert any(r["reason"] == "source_person_extra" for r in result["review"])


@pytest.mark.parametrize("mode", ["忠实", "抽核", "借壳"])
@pytest.mark.parametrize("extra", [False, True])
def test_native_source_gate_consumes_visual_attribution_and_blocks_wrong_speaker(
    tmp_path, mode, extra
):
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not installed")
    script_module = Path(__file__).parents[1] / "skills/cine-script/scripts/cine-script.mjs"
    mapping = {"people": [{"person_id": "P1", "status": "extra"}]} if extra else MAPPING
    source = speaker_evidence.attribute([row()], CAST, NOTES, mapping)
    expected_name = "非角色人物（extra）" if extra else "队长（C01）"
    doc = {
        "source": "测试",
        "params": {},
        "episodes": [
            {
                "ep": 1,
                "scenes": [
                    {
                        "sceneId": "S01",
                        "characters": ["C01", "C02"],
                        "flow": [{"speaker": "C02", "line": "请在这里等一下"}],
                    }
                ],
            }
        ],
    }
    payload = tmp_path / "source-check.json"
    payload.write_text(
        json.dumps(
            {
                "doc": doc,
                "source": source,
                "cast": CAST,
                "outline": {"params": {"adaptMode": mode}},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    code = (
        "import fs from 'node:fs'; import {pathToFileURL} from 'node:url';"
        "const [modulePath,payloadPath]=process.argv.slice(1); process.argv=[];"
        "const {gateReport}=await import(pathToFileURL(modulePath).href);"
        "const x=JSON.parse(fs.readFileSync(payloadPath,'utf8'));"
        "console.log(JSON.stringify(gateReport(x.doc,{source:x.source,cast:x.cast,outline:x.outline})"
        ".find(g=>g.id==='source-fidelity')));"
    )
    result = subprocess.run(
        [node, "--input-type=module", "-e", code, str(script_module), str(payload)],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert result.returncode == 0, result.stderr
    gate = json.loads(result.stdout)
    if mode == "忠实":
        assert not gate["ok"] and f"源片归属为 {expected_name}" in str(gate["detail"])
        assert "1.0–3.0s" in gate["detail"]
    else:
        assert gate["ok"]
    if mode == "抽核":
        paths = {}
        for name, value in {
            "script": doc,
            "cast": CAST,
            "source": source,
            "outline": {"params": {"adaptMode": mode}},
        }.items():
            paths[name] = tmp_path / f"{name}.json"
            paths[name].write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
        result = subprocess.run(
            [
                node,
                str(script_module),
                "source-review",
                str(paths["script"]),
                "--source",
                str(paths["source"]),
                "--cast",
                str(paths["cast"]),
                "--outline",
                str(paths["outline"]),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=True,
        )
        report = json.loads(result.stdout)
        assert not report["bad"]["speaker"] and len(report["bad"]["review"]) == 1
        assert expected_name in report["bad"]["review"][0]


def b_rows(rows, speaker, delivery="spoken", basis="context", notes=NOTES, mapping=None):
    """Attach a current B reading, then let the merger recheck its constraints."""
    for i, value in enumerate(rows):
        record = {
            "speaker": speaker,
            "delivery": delivery,
            "basis": basis,
            "reason": "由问答和镜头推断",
            "schema_version": line_judgment.SCHEMA_VERSION,
            "method": line_judgment.METHOD,
            "model": "vision",
            "read": True,
            "model_review_reasons": [],
            "input_sha256": line_judgment.judgment_inputs(notes, rows, i, "vision"),
        }
        value["judgment"] = line_judgment._with_constraints(record, notes, value, mapping)
    return rows


def test_a_visible_speaker_wins_without_requesting_text_or_using_b(monkeypatch):
    monkeypatch.setattr(
        jev_client, "open_client", lambda *_a, **_k: pytest.fail("merge never calls models")
    )
    value = row()
    value["judgment"] = {"speaker": "P2", "delivery": "voice_over", "basis": "context"}
    result = speaker_evidence.attribute([value], CAST, NOTES, MAPPING)["attributions"][0]
    assert result["speaker"] == "C01" and result["delivery"] == "spoken"
    assert not result["needs_review"]
    assert set(result["evidence"]) == {"a", "b", "present_people", "constraints"}
    assert result["evidence"]["present_people"] == ["P1", "P2"]
    assert result["performance"] == value["performance"]


def test_cardless_a_returns_person_number_and_later_mapping_only_changes_role():
    value = row()
    before = speaker_evidence.attribute([value], None, NOTES, None)["attributions"][0]
    after = speaker_evidence.attribute([value], CAST, NOTES, MAPPING)["attributions"][0]
    assert before["speaker"] == "P1" and after["speaker"] == "C01"
    assert before["performance"] == after["performance"]
    assert not before["needs_review"] and not after["needs_review"]


def test_a_extra_keeps_anonymous_speech_without_another_attribution():
    mapping = {"people": [{"person_id": "P1", "status": "extra"}]}
    result = speaker_evidence.attribute([row()], CAST, NOTES, mapping)["attributions"][0]
    assert result["speaker"] == "extra" and result["delivery"] == "spoken"
    assert result["evidence"]["a"]["status"] == "extra" and not result["needs_review"]


def test_unmapped_visible_person_waits_for_cards():
    result = speaker_evidence.attribute([row()], CAST, NOTES, {})["attributions"][0]
    assert result["speaker"] == "unclear" and result["needs_review"]
    assert "尚未可靠对应角色卡" in result["review_reasons"][0]


def test_missing_b_keeps_offscreen_in_review():
    result = speaker_evidence.attribute([row("offscreen")], CAST, NOTES, MAPPING)["attributions"][
        0
    ]
    assert result["needs_review"] and result["speaker"] == "unclear"
    assert "需补读B步" in result["review_reasons"][0]


@pytest.mark.parametrize("basis", ["context", "caption"])
def test_unclear_a_can_use_a_current_context_or_caption_choice(basis):
    rows = b_rows([row("unclear")], "P2", basis=basis, mapping=MAPPING)
    result = speaker_evidence.attribute(rows, CAST, NOTES, MAPPING)["attributions"][0]
    assert result["speaker"] == "C02" and result["delivery"] == "spoken"
    assert not result["needs_review"] and result["performance"]["emotion"] == "平静"


def test_offscreen_cannot_be_reassigned_to_a_sampled_on_screen_person():
    rows = b_rows([row("offscreen")], "P1", mapping=MAPPING)
    result = speaker_evidence.attribute(rows, CAST, NOTES, MAPPING)["attributions"][0]
    assert result["needs_review"] and result["speaker"] == "unclear"
    assert "取帧镜头人物" in str(result["review_reasons"])


def test_offscreen_can_be_a_same_place_immediate_neighbor():
    notes = copy.deepcopy(NOTES)
    notes["shots"] = [
        {"start": 0, "end": 3, "place": "L1", "people": ["P1"]},
        {"start": 3, "end": 5, "place": "L1", "people": ["P2"]},
    ]
    rows = b_rows([row("offscreen", notes=notes)], "P2", notes=notes, mapping=MAPPING)
    result = speaker_evidence.attribute(rows, CAST, notes, MAPPING)["attributions"][0]
    assert result["speaker"] == "C02" and not result["needs_review"]
    assert result["evidence"]["present_people"] == ["P1"]


def test_voice_over_is_confirmed_without_adding_its_person_to_physical_presence():
    notes = copy.deepcopy(NOTES)
    notes["shots"][0]["people"] = ["P1"]
    rows = b_rows(
        [row("offscreen", notes=notes)], "P2", "voice_over", notes=notes, mapping=MAPPING
    )
    result = speaker_evidence.attribute(rows, CAST, notes, MAPPING)["attributions"][0]
    assert result["speaker"] == "C02" and result["delivery"] == "voice_over"
    assert result["evidence"]["present_people"] == ["P1"]
    assert not result["evidence"]["constraints"]["counts_as_present"]


@pytest.mark.parametrize("basis", ["mouth", "none"])
def test_b_cannot_create_mouth_evidence_or_confirm_without_basis(basis):
    rows = b_rows([row("unclear")], "P2", basis=basis, mapping=MAPPING)
    result = speaker_evidence.attribute(rows, CAST, NOTES, MAPPING)["attributions"][0]
    assert result["speaker"] == "unclear" and result["needs_review"]


def test_b_model_structural_error_is_not_hidden_by_passing_constraints():
    rows = b_rows([row("unclear")], "P2", mapping=MAPPING)
    rows[0]["judgment"].update(
        needs_review=True, model_review_reasons=["非法返回字段"], review_reasons=["非法返回字段"]
    )
    result = speaker_evidence.attribute(rows, CAST, NOTES, MAPPING)["attributions"][0]
    assert result["evidence"]["constraints"]["status"] == "passed"
    assert result["needs_review"] and result["speaker"] == "unclear"


def test_crowd_extra_mapping_recheck_needs_no_model_request(monkeypatch):
    monkeypatch.setattr(jev_client, "open_client", lambda *_a: pytest.fail("no model recheck"))
    rows = b_rows([row("unclear")], "crowd")
    before = speaker_evidence.attribute(rows, CAST, NOTES, {})["attributions"][0]
    mapping = {"people": [{"person_id": "P1", "status": "extra"}]}
    after = speaker_evidence.attribute(rows, CAST, NOTES, mapping)["attributions"][0]
    assert before["needs_review"] and after["speaker"] == "extra" and not after["needs_review"]


@pytest.mark.parametrize("delivery", ["spoken", "voice_over"])
def test_current_b_crowd_requires_overlapping_anonymous_presence(delivery):
    notes = copy.deepcopy(NOTES)
    notes["shots"][0]["crowd"] = True
    rows = b_rows([row("offscreen", notes=notes)], "crowd", delivery, notes=notes, mapping=MAPPING)
    result = speaker_evidence.attribute(rows, CAST, notes, MAPPING)["attributions"][0]
    assert result["speaker"] == "extra" and result["delivery"] == delivery
    assert not result["needs_review"]


def test_stale_a_cannot_be_rescued_by_a_b_reading():
    rows = b_rows([row("unclear")], "P2", mapping=MAPPING)
    rows[0]["text"] = "请先完成登记"
    result = speaker_evidence.attribute(rows, CAST, NOTES, MAPPING)["attributions"][0]
    assert result["needs_review"] and "过期" in result["evidence"]["a"]["reason"]


def test_b_cache_is_not_trusted_after_its_dialogue_context_changes():
    rows = [row("unclear"), {"text": "下一位请进。", "start": 3, "end": 4}]
    b_rows(rows[:1], "P2", mapping=MAPPING)
    # Attach the B key for the actual complete context before changing it.
    rows[0]["judgment"]["input_sha256"] = line_judgment.judgment_inputs(NOTES, rows, 0, "vision")
    rows[1]["text"] = "请先取号码牌。"
    result = speaker_evidence.attribute(rows[:1], CAST, NOTES, MAPPING, context_rows=rows)[
        "attributions"
    ][0]
    assert result["needs_review"] and result["evidence"]["b"]["current"] is False


def test_filtered_teaser_preserves_source_indices_and_original_context():
    full = [{**row(), "teaser": True}, row("unclear")]
    b_rows(full, "P2", mapping=MAPPING)
    result = attribute_speakers.attribute_speakers(
        full[1:], CAST, shot_notes=NOTES, person_map=MAPPING, context_rows=full
    )["attributions"][0]
    assert result["source_index"] == 1 and not result["needs_review"]


def test_missing_sentence_timing_remains_review_with_a_visible_a_speaker():
    value = {**row(), "sentence_boundary_review": True}
    result = speaker_evidence.attribute([value], CAST, NOTES, MAPPING)["attributions"][0]
    assert result["needs_review"] and result["speaker"] == "unclear"
    assert "无可靠时间切点" in str(result["review_reasons"])


def test_presence_accepts_any_overlap_but_excludes_insert_and_teaser():
    notes = {
        **NOTES,
        "shots": [
            {"start": 0, "end": 2, "place": "L1", "people": ["P1"]},
            {"start": 2, "end": 7, "place": "insert", "people": ["P1", "P2"]},
            {"start": 7, "end": 10, "place": "L1", "people": ["P2"]},
            {"start": 10, "end": 11, "place": "L1", "people": ["P1"], "teaser": True},
        ],
    }
    assert speaker_evidence.present_people(notes, {"start": 0, "end": 10}) == ["P1", "P2"]
    assert speaker_evidence.present_people(notes, {"start": 10, "end": 11}) == []


def test_voiceover_does_not_require_its_absent_role_in_a_scene():
    notes = copy.deepcopy(NOTES)
    notes["shots"][0]["people"] = ["P1"]
    source = [
        {
            "speaker": "C02",
            "delivery": "voice_over",
            "speaker_needs_review": False,
            "start": 1,
            "end": 3,
        }
    ]
    outline = {"params": {"adaptMode": "忠实"}, "scenes": [{"id": "S01", "from": ["L1"]}]}
    script = {"episodes": [{"scenes": [{"sceneId": "S01", "characters": ["C01"]}]}]}
    result = people_gate.check_people(
        script, outline, {"source_locations": notes}, CAST, MAPPING, source
    )
    assert result["status"] == "passed" and not result["blocking"]


@pytest.mark.parametrize("vision_model", ["changed-vision", ""])
def test_explicit_current_model_rejects_older_self_reported_a_and_b(vision_model):
    rows = b_rows([row("unclear")], "P2", mapping=MAPPING)
    result = speaker_evidence.attribute(rows, CAST, NOTES, MAPPING, vision_model=vision_model)
    assert result["model"] == (vision_model or None)
    value = result["attributions"][0]
    assert value["speaker"] == "unclear" and value["needs_review"]
    assert value["evidence"]["a"]["current"] is False


def test_wrapper_passes_explicit_current_model_into_deterministic_merge():
    result = attribute_speakers.attribute_speakers(
        [row()], CAST, shot_notes=NOTES, person_map=MAPPING, vision_model="changed-vision"
    )
    assert result["model"] == "changed-vision"
    assert result["attributions"][0]["needs_review"]
