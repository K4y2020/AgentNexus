"""Each shot's prompt text must carry the body position the storyboard plans for its people."""

import copy
import json
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parents[1] / "skills/film-analysis"))
from pipeline import autopilot, jev_client, production
from pipeline import shot_state_gate as gate

CAST = {
    "characters": [
        {"id": "C01", "name": "陈小满"},
        {"id": "C04", "name": "周秀娥"},
    ]
}
SUBJECTS = (
    "subject_definitions:\n"
    "<Subject 1> — C04, a 45-year-old ferrywoman in a blue headscarf; her appearance comes from "
    "<Picture 1>.\n"
    "<Subject 2> — C01, an 18-year-old girl in a straw hat; her appearance comes from <Picture 2>.\n"
)


def _h3(*shots):
    body = " ".join(f"[Shot {k}] {text}" for k, text in enumerate(shots, 1))
    return (
        f"{SUBJECTS}\ndetailed_description:\nPainterly style. {body}\n\n"
        "overall_soundscape:\nRiver wind.\n\nnon_diegetic_music:\nN/A"
    )


def _cut(size, characters, frame, enters=None):
    cut = {"size": size, "characters": characters, "frame": frame}
    if enters:
        cut["continuity"] = {"actionStates": {c: {"in": enters, "out": enters} for c in characters}}
    return cut


def _board(calling="<Subject 1> (S1) calls across the water to her daughter."):
    first = {
        "id": "E01-01",
        "sceneIndex": 1,
        "cuts": [
            _cut(
                "medium",
                ["C04"],
                "medium shot of the ferrywoman sitting at the bow, holding the oar",
                "sitting_with_oar",
            )
        ],
        "h3Prompt": _h3("<Subject 1> sits at the bow, gripping the oar."),
    }
    second = {
        "id": "E01-02",
        "sceneIndex": 1,
        "continuesFrom": "E01-01",
        "cuts": [
            _cut("medium", ["C04"], "medium shot of the ferrywoman calling to her daughter"),
            _cut("close", ["C01"], "close-up of the daughter listening on the jetty"),
        ],
        "h3Prompt": _h3(calling, "<Subject 2> listens."),
    }
    return {"episodes": [{"segments": [first, second]}]}


def _answer(probabilities):
    choice = max(probabilities, key=probabilities.get)
    return SimpleNamespace(
        choice=choice, confidence=probabilities[choice], probabilities=probabilities
    )


class FakeJev:
    """Reads positions from keywords; asserts each request sees only its own side."""

    def __init__(self, monkeypatch, written=None):
        self.requests = []
        self.written = written
        monkeypatch.setattr(jev_client, "_find_api_key", lambda: "key")
        monkeypatch.setattr(jev_client, "open_client", lambda _key: self)

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def system_one(self, state, questions, **_kwargs):
        question = questions["q"].instructions
        self.requests.append((question, state))
        if question in (gate.PLAN_POSITION, gate.PLAN_HANDS):
            assert set(state) == {"character", "plan", "previous_shot"}
            seen = json.dumps(state["plan"]) + state["previous_shot"]
        else:
            assert set(state) == {"character", "shot_text"}
            seen = state["shot_text"]
        if question == gate.PLAN_POSITION:
            position = "seated" if "sitting" in seen else "standing"
            return self._choice({position: 0.95, "standing": 0.05} if position != "standing"
                                else {"standing": 1.0})
        if question == gate.TEXT_POSITION:
            if self.written:
                return self._choice(self.written)
            position = "seated" if "sits" in seen else gate.NOT_STATED
            return self._choice({position: 0.97, "standing": 0.03})
        holding = "oar" in seen
        return SimpleNamespace(answers={"q": SimpleNamespace(noul=0.9 if holding else 0.05)})

    @staticmethod
    def _choice(probabilities):
        return SimpleNamespace(answers={"q": _answer(probabilities)})

    def asked(self, question):
        return [state for q, state in self.requests if q == question]


def test_an_omitted_position_blocks_and_carried_hands_are_reviewed(monkeypatch):
    jev = FakeJev(monkeypatch)

    result = gate.check_shot_states(_board(), CAST)

    assert result["status"] == "failed"
    assert result["blocking"] == [
        "E01-02 [Shot 1]：周秀娥（C04）进镜时坐着，这段正文却没写姿态。视频模型只读 h3Prompt，"
        "frame、continuity 和尾帧图都代替不了正文：在这一段里写明此刻的姿态和正在做的动作。"
        "姿态如果确实变了，正文写新姿态，并同步改这一镜的 frame 和 continuity。"
    ]
    assert [item["message"] for item in result["review"]] == [
        "E01-02 [Shot 1]：周秀娥（C04）手上的动作从上一镜延续到这一镜，正文没写手上在做什么。"
    ]
    plan = jev.asked(gate.PLAN_POSITION)[1]
    assert plan["character"].startswith("<Subject 1>: a 45-year-old ferrywoman")
    assert plan["previous_shot"].startswith("medium shot of the ferrywoman sitting")
    # The close-up of the standing daughter needs no text check.
    assert len(jev.asked(gate.TEXT_POSITION)) == 2


def test_writing_the_carried_state_passes(monkeypatch):
    FakeJev(monkeypatch)
    board = _board("<Subject 1> (S1) still sits at the bow, oar in hand, and calls out.")

    result = gate.check_shot_states(board, CAST)

    assert result == {"status": "passed", "checked": 2, "blocking": [], "review": [], "reused": 0}


def test_a_close_up_checks_only_positions_a_face_shows(monkeypatch):
    jev = FakeJev(monkeypatch)
    board = _board()
    board["episodes"][0]["segments"][1]["cuts"][0]["size"] = "close"

    assert gate.check_shot_states(board, CAST)["blocking"] == []
    assert len(jev.asked(gate.TEXT_POSITION)) == 1  # only the first segment's medium shot


def test_a_sure_different_position_blocks_and_a_weaker_one_is_reviewed(monkeypatch):
    FakeJev(monkeypatch, written={"standing": 0.8, "seated": 0.1, gate.NOT_STATED: 0.1})
    sure = gate.check_shot_states(_board(), CAST)
    assert sure["status"] == "failed"
    assert "按分镜进镜时坐着，正文却写成站着" in sure["blocking"][0]

    FakeJev(monkeypatch, written={"standing": 0.5, "seated": 0.2, gate.NOT_STATED: 0.3})
    weak = gate.check_shot_states(_board(), CAST)
    assert weak["status"] == "passed"
    assert {item["reason"] for item in weak["review"]} == {"position_differs", "hands_unwritten"}


def test_no_jev_key_leaves_the_check_unavailable(monkeypatch):
    monkeypatch.setattr(jev_client, "_find_api_key", lambda: "")
    result = gate.check_shot_states(_board(), CAST)
    assert result["status"] == "unavailable"
    assert result["error"] == "TYPESAFE_API_KEY is not configured"


def test_judgments_are_reused_and_only_changed_shots_are_asked_again(monkeypatch, tmp_path):
    jev = FakeJev(monkeypatch)
    board = _board()
    first = gate.check_shot_states(board, CAST, cache_dir=tmp_path)
    asked = len(jev.requests)
    again = gate.check_shot_states(board, CAST, cache_dir=tmp_path)
    assert again["reused"] == asked and len(jev.requests) == asked
    assert again["blocking"] == first["blocking"]

    fixed = copy.deepcopy(board)
    fixed["episodes"][0]["segments"][1]["h3Prompt"] = _h3(
        "<Subject 1> (S1) still sits at the bow, oar in hand, and calls out.", "<Subject 2> listens."
    )
    assert gate.check_shot_states(fixed, CAST, cache_dir=tmp_path)["status"] == "passed"
    # Only the edited shot's two text questions were new.
    assert len(jev.requests) == asked + 2


def test_shots_pair_with_their_cuts_and_previous_shot_in_the_scene():
    board = _board()
    board["episodes"][0]["segments"][1]["sceneIndex"] = 2
    pairs = gate.pair_shots(board)

    assert [(p["location"], p["character"], p["size"]) for p in pairs] == [
        ("E01-01 [Shot 1]", "C04", "medium"),
        ("E01-02 [Shot 1]", "C04", "medium"),
        ("E01-02 [Shot 2]", "C01", "close"),
    ]
    assert pairs[0]["plan"]["enters_as"] == "sitting with oar"
    assert pairs[1]["previous_shot"] == "none: first appearance in this scene"
    assert pairs[1]["shot_text"].startswith("[Shot 1] <Subject 1> (S1) calls")


def test_production_runs_the_check_and_records_its_code(monkeypatch, tmp_path):
    FakeJev(monkeypatch)
    for name, value in {"storyboard": _board(), "cast": CAST}.items():
        (tmp_path / f"{name}.json").write_text(json.dumps(value, ensure_ascii=False), "utf-8")
    paths = {name: tmp_path / f"{name}.json" for name in ("storyboard", "cast")}
    hashes = {}

    result = production.storyboard_shot_states(tmp_path, paths, hashes, "jev")

    assert result["status"] == "failed"
    assert any(path.endswith("shot_state_gate.py") for path in hashes)
    assert (tmp_path / ".cine-validation" / "shot-states").is_dir()


def test_review_items_reach_the_review_queue(tmp_path):
    row = {
        "status": "passed",
        "shot_states": {
            "status": "passed",
            "review": [{"reason": "hands_unwritten", "message": "E01-02 [Shot 1]：…"}],
            "errors": ["TimeoutError: slow"],
        },
    }
    found = autopilot._findings(tmp_path, "storyboard", row)["shot_states"]
    assert [item["reason"] for item in found] == ["hands_unwritten", "shot_states_unchecked"]
