"""Identity context and selective remapping preserve the existing person correspondence."""

import copy
import json
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parents[1] / "skills/film-analysis"))
from pipeline import jev_client, person_map

CAST = {
    "characters": [
        {"id": "C01", "name": "摄影师", "persona": {"gender": "女", "ageRange": "30岁"}},
        {"id": "C02", "name": "园丁", "persona": {"gender": "男", "ageRange": "60岁"}},
        {"id": "C03", "name": "维修员", "persona": {"gender": "男", "ageRange": "45岁"}},
    ]
}
NOTES = {
    "schema_version": 2,
    "source_id": "film",
    "revision_id": "r1",
    "model": "vision",
    "people": [
        {"id": "P1", "look": "短发女子，紫色外套", "name_caption": "摄影师"},
        {"id": "P2", "look": "银发男子，棕帽子", "name_caption": None},
        {"id": "P3", "look": "红帽子的男子", "name_caption": None},
    ],
    "shots": [
        {
            "index": 0,
            "start": 0,
            "end": 2,
            "place": "L1",
            "what": "P1 给 P2 拍照片",
            "people": ["P1", "P2"],
        },
        {
            "index": 1,
            "start": 2,
            "end": 4,
            "place": "insert",
            "what": "P1 的旧照片特写",
            "people": [],
        },
        {
            "index": 2,
            "start": 4,
            "end": 6,
            "place": "L2",
            "what": "P3 检查工具箱",
            "people": ["P3"],
        },
    ],
}


def answer(choice):
    return SimpleNamespace(
        choice=choice, confidence=0.95, probabilities={choice: 0.95, "unclear": 0.05}
    )


class Jev:
    def __init__(self, monkeypatch, choose):
        self.calls = []
        self.choose = choose
        monkeypatch.setattr(jev_client, "open_client", lambda _k: self)
        monkeypatch.setattr(jev_client, "_find_api_key", lambda: "key")

    def __enter__(self):
        return self

    def __exit__(self, *_a):
        return False

    def system_one(self, *, state, questions, model):
        self.calls.append({"state": copy.deepcopy(state), "questions": copy.deepcopy(questions)})
        return SimpleNamespace(
            model=model, answers={qid: answer(self.choose(qid, q)) for qid, q in questions.items()}
        )


def test_identity_question_contains_only_own_shots_and_caption_context(monkeypatch):
    jev = Jev(monkeypatch, lambda pid, _q: {"P2": "C02", "P3": "C03"}[pid])
    notes = copy.deepcopy(NOTES)
    notes["people"] = list(reversed(notes["people"]))
    result = person_map.map_people(notes, CAST, api_key="key")
    question = jev.calls[0]["questions"]["P2"]
    context = json.loads(question.instructions.split("\n", 1)[1])["context"]
    assert [s["index"] for s in context["shots"]] == [0]
    assert context["shots"][0]["what"] == "P1 给 P2 拍照片"
    assert context["co_visible"][0]["character_name"] == "摄影师"
    assert context["caption_matches"] == [
        {"person_id": "P1", "character_id": "C01", "character_name": "摄影师"}
    ]
    assert "C01" not in question.criteria
    row = next(r for r in result["people"] if r["person_id"] == "P2")
    assert row["excluded"]["C01"]["person_ids"] == ["P1"]
    assert row["excluded"]["C01"]["shots"] == [0]
    assert "同框" in row["excluded"]["C01"]["reason"]


def test_caption_match_does_not_exclude_another_costume_without_co_visibility(monkeypatch):
    jev = Jev(monkeypatch, lambda _pid, _q: "C01")
    notes = copy.deepcopy(NOTES)
    notes["people"] = notes["people"][:2]
    notes["shots"][0]["people"] = ["P2"]
    result = person_map.map_people(notes, CAST, api_key="key")
    question = jev.calls[0]["questions"]["P2"]
    assert "C01" in question.criteria and "caption_matches" in question.instructions
    assert result["people"][1]["character_id"] == "C01"
    assert result["people"][1]["excluded"] == {}


def test_co_visible_appearance_answers_are_sequential_and_excluded(monkeypatch):
    notes = copy.deepcopy(NOTES)
    notes["people"][0]["name_caption"] = None
    jev = Jev(
        monkeypatch, lambda pid, _q: "C01" if pid == "P1" else "C02" if pid == "P2" else "C03"
    )
    result = person_map.map_people(notes, CAST, api_key="key")
    assert list(jev.calls[0]["questions"]) == ["P1", "P3"]
    assert list(jev.calls[1]["questions"]) == ["P2"]
    assert "C01" not in jev.calls[1]["questions"]["P2"].criteria
    assert result["people"][1]["excluded"]["C01"]["person_ids"] == ["P1"]


def test_a_provider_cannot_return_a_co_visible_excluded_character(monkeypatch):
    Jev(monkeypatch, lambda _pid, _q: "C01")
    result = person_map.map_people(NOTES, CAST, api_key="key")
    assert result["people"][1]["status"] == "review"
    assert result["people"][1]["character_id"] is None
    assert result["people"][1]["error"] == "unknown_option:C01"


def test_conflicting_caption_matches_do_not_assign_two_co_visible_people_one_role():
    notes = copy.deepcopy(NOTES)
    notes["people"][1]["name_caption"] = "摄影师"
    result = person_map.map_people(notes, CAST, api_key="")
    assert all(r["status"] == "review" for r in result["people"][:2])
    assert all("同框" in r["reason"] for r in result["people"][:2])


def test_identity_shot_context_is_bounded():
    notes = copy.deepcopy(NOTES)
    notes["shots"] = [
        {"index": i, "what": "动作" * 200, "people": ["P2"]}
        for i in range(person_map.MAX_CONTEXT_SHOTS * 3)
    ]
    context, _excluded = person_map.appearance_context(
        notes["people"][1], notes, CAST["characters"], {}, {}
    )
    assert len(context["shots"]) == person_map.MAX_CONTEXT_SHOTS
    assert context["shots"][-1]["index"] == len(notes["shots"]) - 1
    assert all(len(s["what"]) <= person_map.MAX_SHOT_DESCRIPTION_CHARS for s in context["shots"])


def test_mapping_cache_tracks_own_shots_and_dependencies_without_global_remapping(
    monkeypatch, tmp_path
):
    notes = copy.deepcopy(NOTES)
    notes["people"][0]["name_caption"] = None
    choices = {"P1": "C01", "P2": "C02", "P3": "C03"}
    jev = Jev(monkeypatch, lambda pid, _q: choices[pid])
    cast_path = tmp_path / "cast.json"
    cast_path.write_text(json.dumps(CAST), encoding="utf-8")
    first = person_map.ensure_person_map(tmp_path, notes, cast_path)
    second = person_map.ensure_person_map(tmp_path, notes, cast_path)
    assert first["people"] == second["people"] and len(jev.calls) == 2
    assert second["reused"] == 3
    notes["shots"][2]["what"] = "P3 收起工具箱"
    person_map.ensure_person_map(tmp_path, notes, cast_path)
    assert list(jev.calls[-1]["questions"]) == ["P3"] and len(jev.calls) == 3
    notes["people"][0]["look"] += "，红色帽子"
    choices["P1"] = "C03"
    changed = person_map.ensure_person_map(tmp_path, notes, cast_path)
    assert [list(c["questions"]) for c in jev.calls[-2:]] == [["P1"], ["P2"]]
    assert "C03" in changed["people"][1]["excluded"]
    assert changed["people"][2] == first["people"][2] or changed["reused"] == 1
    assert len(jev.calls) == 5


def test_mapping_cache_invalidates_changed_co_visibility_but_reuses_independent_people(
    monkeypatch, tmp_path
):
    notes = copy.deepcopy(NOTES)
    jev = Jev(monkeypatch, lambda pid, _q: "C02" if pid == "P2" else "C03")
    cast_path = tmp_path / "cast.json"
    cast_path.write_text(json.dumps(CAST), encoding="utf-8")
    first = person_map.ensure_person_map(tmp_path, notes, cast_path)
    notes["shots"][0]["people"] = ["P2"]
    changed = person_map.ensure_person_map(tmp_path, notes, cast_path)
    assert len(jev.calls) == 2 and list(jev.calls[-1]["questions"]) == ["P2"]
    assert "C01" in jev.calls[-1]["questions"]["P2"].criteria
    assert changed["people"][1]["excluded"] == {}
    assert changed["people"][2] == first["people"][2]


def test_mapping_cache_rechecks_when_confirmed_caption_dependency_changes(monkeypatch, tmp_path):
    notes = copy.deepcopy(NOTES)
    jev = Jev(monkeypatch, lambda _pid, _q: "extra")
    cast_path = tmp_path / "cast.json"
    cast_path.write_text(json.dumps(CAST), encoding="utf-8")
    person_map.ensure_person_map(tmp_path, notes, cast_path)
    notes["people"][0]["name_caption"] = "园丁"
    changed = person_map.ensure_person_map(tmp_path, notes, cast_path)
    assert len(jev.calls) == 2
    assert "C02" not in jev.calls[-1]["questions"]["P2"].criteria
    assert "C01" in jev.calls[-1]["questions"]["P2"].criteria
    assert changed["people"][1]["excluded"]["C02"]["person_ids"] == ["P1"]
    assert changed["people"][2]["context"]["caption_matches"][0]["character_id"] == "C02"


def test_mapping_retries_failed_row_without_reasking_successful_independent_row(
    monkeypatch, tmp_path
):
    notes = copy.deepcopy(NOTES)
    jev = Jev(monkeypatch, lambda pid, _q: "not-an-option" if pid == "P2" else "C03")
    cast_path = tmp_path / "cast.json"
    cast_path.write_text(json.dumps(CAST), encoding="utf-8")
    first = person_map.ensure_person_map(tmp_path, notes, cast_path)
    assert first["people"][1]["error"] == "unknown_option:not-an-option"
    jev.choose = lambda _pid, _q: "C02"
    changed = person_map.ensure_person_map(tmp_path, notes, cast_path)
    assert list(jev.calls[-1]["questions"]) == ["P2"]
    assert changed["people"][2] == first["people"][2]
    assert changed["status"] == "mapped" and not changed["errors"]
