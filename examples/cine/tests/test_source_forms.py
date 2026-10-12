"""A whole-film shooting form is recorded, decided in the outline and kept downstream."""

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "skills/film-analysis"))
from pipeline import (
    draft_check,
    form_gate,
    jev_client,
    people_gate,
    production,
    shot_notes,
    source_shots,
)
from pipeline import pov_gate as gate

from tests.test_people_evidence import CAST, MAPPING, NOTES

POV = {"form": "pov", "how": "镜头就是船夫的眼睛，只有他撑篙的手进画"}


def _material(forms=None, viewpoints=()):
    shots = [{"start": i, "end": i + 1, "viewpoint": v} for i, v in enumerate(viewpoints)]
    return {
        "adaptation_story": {"forms": list(forms or [])},
        "source_locations": {"shots": shots},
    }


def _outline(*forms, characters=("C01",)):
    return {
        "adaptation": {"forms": list(forms)},
        "characters": [{"id": c} for c in characters],
    }


def test_the_form_names_agree_with_the_story_draft_check():
    assert set(form_gate.FORMS) == set(draft_check.FORMS)


def test_the_story_draft_must_say_which_forms_it_uses():
    draft = {"summary": {}, "characters": ["船夫"]}
    assert {"reason": "forms_list_required"} in draft_check.story_draft_issues(draft, None)
    draft["forms"] = [{"form": "flashback", "how": "倒叙"}]
    assert {"reason": "form_entry_invalid"} in draft_check.story_draft_issues(draft, None)
    draft["forms"] = []
    reasons = {i["reason"] for i in draft_check.story_draft_issues(draft, None)}
    assert not reasons & {"forms_list_required", "form_entry_invalid"}


def test_viewpoints_in_a_third_of_the_shots_make_a_form_the_draft_left_out():
    material = _material(
        viewpoints=["pov", "observer", "to_camera", "pov", "observer", "observer"]
    )
    material["source_locations"]["shots"].append(
        {"start": 9, "end": 10, "viewpoint": "pov", "teaser": True}
    )

    forms = form_gate.source_forms(material)

    assert [f["form"] for f in forms] == ["pov"]
    assert "2/6" in forms[0]["how"]


def test_the_draft_names_a_form_once_and_its_own_wording_wins():
    material = _material([POV, POV], viewpoints=["pov", "pov"])
    assert form_gate.source_forms(material) == [POV]


def test_per_scene_continuity_is_distinct_from_a_whole_film_take(tmp_path):
    form = {"form": "scene_one_take", "how": "每次登门是一个完整镜头，换户才切镜"}
    material = _material([form])
    assert form_gate.source_forms(material) == [form]
    (tmp_path / "source-material.json").write_text(
        json.dumps(material, ensure_ascii=False), encoding="utf-8"
    )
    production.init_outline(tmp_path, "登门", 1, 60, "喜剧", "抽核")
    outline = json.loads((tmp_path / "outline.json").read_text(encoding="utf-8"))
    assert outline["adaptation"]["forms"] == [{**form, "decision": "", "why": ""}]
    entry = {**form, "decision": "keep", "why": "保留不中断的当面对话"}
    assert form_gate.outline_form_errors(_outline(entry), material) == []
    document = {}
    outline["adaptation"]["forms"] = [entry]
    (tmp_path / "outline.json").write_text(json.dumps(outline), encoding="utf-8")
    production._seed_forms(tmp_path, document)
    assert document["keptForms"] == [entry]


@pytest.mark.parametrize("selected", [[], ["S99"], "S01", None])
def test_scene_take_scope_cannot_silently_select_nothing(selected):
    form = {
        "form": "scene_one_take",
        "how": "对话不中断",
        "decision": "keep",
        "why": "维持主观视角",
        "sceneIds": selected,
    }
    outline = {**_outline(form), "scenes": [{"id": "S01"}]}
    assert any("sceneIds" in error for error in form_gate.outline_form_errors(outline, None))
    form["sceneIds"] = ["S01"]
    assert form_gate.outline_form_errors(outline, None) == []


def test_init_seeds_an_undecided_entry_for_every_source_form(tmp_path):
    (tmp_path / "source-material.json").write_text(
        json.dumps(_material([POV]), ensure_ascii=False), encoding="utf-8"
    )
    production.init_outline(tmp_path, "渡口", 1, 60, "悬疑", "抽核")
    outline = json.loads((tmp_path / "outline.json").read_text(encoding="utf-8"))
    assert outline["adaptation"]["forms"] == [{**POV, "decision": "", "why": "", "holder": ""}]


@pytest.mark.parametrize(
    ("entry", "problem"),
    [
        (None, "缺这一项"),
        ({**POV, "decision": "", "why": "x"}, "keep 或 drop"),
        ({**POV, "decision": "drop", "why": " "}, "理由"),
        ({**POV, "decision": "keep", "why": "代入感", "holder": "C09"}, "holder"),
    ],
)
def test_the_outline_must_keep_or_drop_each_form_with_a_reason(entry, problem):
    outline = _outline(*([entry] if entry else []))
    errors = form_gate.outline_form_errors(outline, _material([POV]))
    assert len(errors) == 1 and problem in errors[0]


@pytest.mark.parametrize(
    "entry",
    [
        {**POV, "decision": "keep", "why": "代入感来自被人当面托付", "holder": "C01"},
        {**POV, "decision": "drop", "why": "改成双人戏，需要看到两人的脸"},
    ],
)
def test_a_reasoned_decision_passes(entry):
    assert form_gate.outline_form_errors(_outline(entry), _material([POV])) == []


def test_a_film_without_forms_asks_nothing():
    assert form_gate.outline_form_errors(_outline(), _material()) == []
    assert form_gate.kept_pov_holder(_outline()) is None


def test_the_outline_check_reads_the_bound_source_material(tmp_path):
    (tmp_path / "source-material.json").write_text(
        json.dumps(_material([POV]), ensure_ascii=False), encoding="utf-8"
    )
    outline = tmp_path / "outline.json"
    outline.write_text(json.dumps(_outline()), encoding="utf-8")
    hashes = {}
    result = production.stage_forms(tmp_path, {"outline": outline}, hashes)
    assert result["status"] == "failed"
    assert any(path.endswith("form_gate.py") for path in hashes)


def test_kept_forms_reach_the_writers_seed(tmp_path):
    kept = {**POV, "decision": "keep", "why": "代入感", "holder": "C01"}
    dropped = {"form": "direct_address", "how": "对镜头说", "decision": "drop", "why": "x"}
    (tmp_path / "outline.json").write_text(
        json.dumps(_outline(kept, dropped), ensure_ascii=False), encoding="utf-8"
    )
    document = {}
    production._seed_forms(tmp_path, document)
    assert document == {"keptForms": [kept]}


def test_a_kept_pov_holder_is_never_an_extra_person():
    outline = {"params": {"adaptMode": "抽核"}, "scenes": [{"id": "S01", "from": ["L1"]}]}
    script = {"episodes": [{"scenes": [{"sceneId": "S01", "characters": ["C01", "C03"]}]}]}
    mapping = {"people": MAPPING["people"][:1]}

    def extras(outline):
        result = people_gate.check_people(
            script, outline, {"source_locations": NOTES}, CAST, mapping, []
        )
        return [r for r in result["review"] if r["reason"] == "source_person_extra"]

    assert extras(outline)
    outline["adaptation"] = {"forms": [{**POV, "decision": "keep", "why": "x", "holder": "C03"}]}
    assert not extras(outline)


@pytest.mark.parametrize("mode", ["忠实", "抽核"])
@pytest.mark.parametrize("holder_present", [True, False])
def test_a_speaking_pov_holder_counts_as_present_without_hiding_a_real_omission(
    mode, holder_present
):
    outline = {
        "params": {"adaptMode": mode},
        "scenes": [{"id": "S01", "from": ["L1"]}],
        "adaptation": {"forms": [{**POV, "decision": "keep", "holder": "C03"}]},
    }
    characters = ["C01", "C02", *(["C03"] if holder_present else [])]
    script = {"episodes": [{"scenes": [{"sceneId": "S01", "characters": characters}]}]}
    spoken = [
        {
            "id": "a",
            "text": "请稍等",
            "speaker": "C03",
            "speaker_needs_review": False,
            "delivery": "spoken",
            "start": 1,
            "end": 3,
        }
    ]

    result = people_gate.check_people(
        script, outline, {"source_locations": NOTES}, CAST, MAPPING, spoken
    )

    findings = result["blocking"] or [r["message"] for r in result["review"]]
    if holder_present:
        assert result["status"] == "passed" and not findings
    else:
        assert len(findings) == 1 and "漏掉" in findings[0] and "C03" in findings[0]


def test_allowing_the_camera_holder_keeps_other_missing_and_extra_people_findings():
    outline = {
        "params": {"adaptMode": "抽核"},
        "scenes": [{"id": "S01", "from": ["L1"]}],
        "adaptation": {"forms": [{**POV, "decision": "keep", "holder": "C03"}]},
    }
    script = {"episodes": [{"scenes": [{"sceneId": "S01", "characters": ["C01", "C03", "C04"]}]}]}

    result = people_gate.check_people(
        script, outline, {"source_locations": NOTES}, CAST, MAPPING, []
    )

    assert [
        (r["reason"], "C02" in r["message"], "C04" in r["message"]) for r in result["review"]
    ] == [
        ("source_person_missing", True, False),
        ("source_person_extra", False, True),
    ]


def test_shot_notes_keep_a_valid_viewpoint_and_drop_an_unknown_one():
    reply = {
        "new": [{"id": "L1", "name": "渡口", "setting": "木栈桥"}],
        "people": [],
        "shots": [
            {
                "n": 1,
                "place": "L1",
                "what": "船靠岸",
                "people": [],
                "crowd": False,
                "viewpoint": "pov",
            },
            {
                "n": 2,
                "place": "L1",
                "what": "船离岸",
                "people": [],
                "crowd": False,
                "viewpoint": "drone",
            },
        ],
    }
    _known, found, _people, problems = shot_notes._accept(reply, [], {1, 2}, [])
    assert not problems
    assert (found[1]["viewpoint"], found[2]["viewpoint"]) == ("pov", None)


def test_source_shot_lines_name_the_viewpoint():
    shot = {
        "start": 1.0,
        "end": 3.0,
        "size": "medium",
        "camera": "Static Shot",
        "viewpoint": "pov",
        "what": "一只手把缆绳递过来",
        "people": [],
    }
    assert "主视角" in source_shots.script_line(shot, [])
    assert "first-person POV" in source_shots.storyboard_line(shot, [])


SUBJECT = "<Subject 1> — C01, a ferryman seen only as his weathered hands; from <Picture 1>."


def _board(*texts):
    body = " ".join(f"[Shot {k}] {text}" for k, text in enumerate(texts, 1))
    return {
        "episodes": [
            {
                "segments": [
                    {
                        "id": "E01-01",
                        "cuts": [{"characters": ["C01"], "frame": t} for t in texts],
                        "h3Prompt": (
                            f"subject_definitions:\n{SUBJECT}\n\ndetailed_description:\n{body}\n\n"
                            "overall_soundscape:\nRiver."
                        ),
                    }
                ]
            }
        ]
    }


class FakeJev:
    """Return explicit judgments; these tests check plumbing, not model accuracy."""

    def __init__(self, monkeypatch, answers=None):
        self.calls = 0
        self.states = []
        self.answers = answers
        monkeypatch.setattr(jev_client, "_find_api_key", lambda: "key")
        monkeypatch.setattr(jev_client, "open_client", lambda _key: self)

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def system_one(self, state, questions, **_kwargs):
        self.calls += 1
        self.states.append(state)
        assert questions["q"].instructions == gate.QUESTION
        probabilities = (
            self.answers[state["shot_text"]]
            if self.answers is not None
            else {"own_eyes": 0.98, "unclear": 0.02}
        )
        choice = max(probabilities, key=probabilities.get)
        return SimpleNamespace(
            answers={
                "q": SimpleNamespace(
                    choice=choice, confidence=probabilities[choice], probabilities=probabilities
                )
            }
        )


def test_a_shown_face_blocks_a_borderline_one_is_reviewed(monkeypatch, tmp_path):
    board = _board(
        "A POV shot: <Subject 1>'s hand passes the rope forward.",
        "A close shot of <Subject 1>'s weathered face.",
        "<Subject 1> turns toward the jetty.",
    )
    jev = FakeJev(
        monkeypatch,
        {
            "[Shot 1] A POV shot: <Subject 1>'s hand passes the rope forward.": {
                "own_eyes": 0.98,
                "face_shown": 0.02,
            },
            "[Shot 2] A close shot of <Subject 1>'s weathered face.": {
                "own_eyes": 0.1,
                "face_shown": 0.9,
            },
            "[Shot 3] <Subject 1> turns toward the jetty.": {
                "own_eyes": 0.6,
                "face_shown": 0.4,
            },
        },
    )

    result = gate.check_pov(board, "C01", holder_name="船夫", cache_dir=tmp_path)

    assert result["status"] == "failed" and result["checked"] == 3
    assert len(result["blocking"]) == 1 and "E01-01 [Shot 2]" in result["blocking"][0]
    assert "船夫（C01）" in result["blocking"][0]
    assert [r["reason"] for r in result["review"]] == ["pov_face_risk"]
    gate.check_pov(board, "C01", cache_dir=tmp_path)
    assert jev.calls == 3


@pytest.mark.parametrize("characters", [[], ["C02"]])
def test_pov_checks_the_prompt_when_the_holder_is_absent_from_the_cut_cast(
    monkeypatch, characters
):
    text = "A close shot of <Subject 1>'s weathered face."
    jev = FakeJev(monkeypatch, {f"[Shot 1] {text}": {"face_shown": 0.95, "own_eyes": 0.05}})
    board = _board(text)
    board["episodes"][0]["segments"][0]["cuts"][0]["characters"] = characters

    result = gate.check_pov(board, "C01")

    assert result["status"] == "failed" and result["checked"] == result["total"] == 1
    assert len(result["blocking"]) == 1 and jev.calls == 1
    assert jev.states[0]["character_id"] == "C01"
    assert "<Subject 1>" in jev.states[0]["character"]


@pytest.mark.parametrize(
    ("judgment", "blocking", "review"),
    [
        ({"outside_view": 0.95, "own_eyes": 0.05}, True, False),
        ({"unclear": 0.9, "own_eyes": 0.1}, False, True),
        ({"unclear": 0.49, "own_eyes": 0.51}, False, True),
        ({"own_eyes": 0.98, "unclear": 0.02}, False, False),
    ],
)
def test_hiding_a_face_does_not_by_itself_prove_first_person_pov(
    monkeypatch, judgment, blocking, review
):
    text = "The holder's face stays out of frame."
    FakeJev(monkeypatch, {f"[Shot 1] {text}": judgment})

    result = gate.check_pov(_board(text), "C01")

    assert bool(result["blocking"]) is blocking
    assert bool(result["review"]) is review
    if blocking:
        assert result["status"] == "failed" and "主视角" in result["blocking"][0]
    if review:
        assert result["review"][0]["reason"] == "pov_viewpoint_unclear"


def test_prompt_bindings_are_local_to_each_segment_and_keep_negative_instructions(monkeypatch):
    jev = FakeJev(monkeypatch)
    text = (
        "From <Subject 2>'s eyes, <Subject 1> looks into the lens. Never show "
        "<Subject 2>'s face. <Subject 1> says <d>[English] Look at my face.</d>"
    )
    board = _board(text)
    segment = board["episodes"][0]["segments"][0]
    segment["cuts"][0]["characters"] = ["C02"]
    segment["h3Prompt"] = (
        segment["h3Prompt"]
        .replace(
            SUBJECT,
            (
                "<Subject 1> — C02, a student with a freckled face.\n"
                "<Subject 2> — C01, the camera holder from a full-face reference portrait."
            ),
        )
        .replace(
            "detailed_description:\n", "detailed_description:\nAll shots use <Subject 2>'s eyes.\n"
        )
    )
    second = _board("From <Subject 1>'s eyes, the river fills the frame.")["episodes"][0][
        "segments"
    ][0]
    second["id"] = "E01-02"
    board["episodes"][0]["segments"].append(second)

    result = gate.check_pov(board, "C01")

    assert result["status"] == "passed" and result["checked"] == 2
    first_state = next(s for s in jev.states if "Never show" in s["shot_text"])
    second_state = next(s for s in jev.states if "river fills" in s["shot_text"])
    assert first_state["character"].startswith("<Subject 2>")
    assert second_state["character"].startswith("<Subject 1>")
    assert first_state["shot_text"] == f"[Shot 1] {text}"
    assert first_state["shared_context"] == "All shots use <Subject 2>'s eyes."
    assert "C02" in first_state["subject_definitions"]
    assert "prohibition" in gate.QUESTION and "actual conflicting depiction" in gate.QUESTION


@pytest.mark.parametrize("indent", ["", "  ", "\t "])
def test_indented_ref2va_sections_and_subjects_keep_the_same_shot_binding(monkeypatch, indent):
    jev = FakeJev(monkeypatch)
    text = "From <Subject 2>'s eyes, <Subject 1> looks into the lens."
    board = _board(text)
    segment = board["episodes"][0]["segments"][0]
    lines = [
        "subject_definitions:",
        "<Subject 1> — C02, a student.",
        "<Subject 2> — C01, the ferryman whose eyes are the camera.",
        "",
        "summary:",
        "[reference generation] A student talks to the ferryman.",
        "",
        "retention_analysis:",
        "<Subject 1> ([Shot 1]): fully_preserved - her appearance.",
        "",
        "detailed_description:",
        "The target video keeps the painterly references.",
        f"[Shot 1] {text}",
        "",
        "overall_soundscape:",
        "Wind.",
        "",
        "non_diegetic_music:",
        "N/A",
    ]
    segment["h3Prompt"] = "\n".join(indent + line if line else "" for line in lines)

    result = gate.check_pov(board, "C01")

    assert result["status"] == "passed" and result["checked"] == result["total"] == 1
    state = jev.states[0]
    assert state["character"].startswith("<Subject 2>")
    assert "<Subject 1> — C02" in state["subject_definitions"]
    assert "<Subject 2> — C01" in state["subject_definitions"]
    assert "summary:" not in state["subject_definitions"]
    assert state["shared_context"] == "The target video keeps the painterly references."
    assert state["shot_text"] == f"[Shot 1] {text}"


@pytest.mark.parametrize(
    "board",
    [
        {},
        _board(),
        _board(""),
        {"episodes": [{"segments": [{"id": "E01-01", "cuts": [{}], "h3Prompt": "POV"}]}]},
    ],
)
def test_no_usable_shot_prompts_are_unavailable_instead_of_passing(monkeypatch, board):
    jev = FakeJev(monkeypatch)

    result = gate.check_pov(board, "C01")

    assert result["status"] == "unavailable" and result["checked"] == 0
    assert result["error"] and jev.calls == 0


def test_partial_prompt_coverage_exposes_the_unchecked_shot(monkeypatch):
    FakeJev(monkeypatch)
    board = _board("A first-person view from <Subject 1>'s eyes.", "")

    result = gate.check_pov(board, "C01")

    assert result["checked"] == 1 and result["total"] == 2
    assert "E01-01 [Shot 2]" in result["errors"][0]


@pytest.mark.parametrize(
    ("heading", "marker", "ending"),
    [
        ("integrated_multimodal_description:", "[Shot 1]", "overall_soundscape:"),
        ("整体视听描述：", "[镜头 1]", "整体音景："),
    ],
)
def test_legacy_prompt_formats_are_still_checked(monkeypatch, heading, marker, ending):
    jev = FakeJev(monkeypatch)
    board = _board("A first-person view from the ferryman's eyes.")
    segment = board["episodes"][0]["segments"][0]
    segment["h3Prompt"] = f"{heading}\n{marker} A first-person view.\n{ending}\nWind."

    result = gate.check_pov(board, "C01", holder_name="ferryman")

    assert result["status"] == "passed" and result["checked"] == 1
    assert jev.states[0]["character"] == "ferryman"
    assert "Wind" not in jev.states[0]["shot_text"]


def test_without_a_key_the_pov_check_is_unavailable(monkeypatch):
    monkeypatch.setattr(jev_client, "_find_api_key", lambda: "")
    result = gate.check_pov(_board("A POV shot."), "C01")
    assert result["status"] == "unavailable"


def test_the_storyboard_check_runs_only_for_a_kept_pov(tmp_path):
    paths = {}
    for name, value in {
        "outline": _outline(),
        "cast": {"characters": [{"id": "C01", "name": "船夫"}]},
        "storyboard": _board("A POV shot."),
    }.items():
        paths[name] = tmp_path / f"{name}.json"
        paths[name].write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    assert production.storyboard_pov(tmp_path, paths, {}, "jev-latest")["status"] == "skipped"
