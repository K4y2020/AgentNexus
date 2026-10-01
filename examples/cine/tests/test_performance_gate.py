"""A faithful script's performance of each line, checked against the source frames."""

import copy
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "skills/film-analysis"))
from pipeline import jev_client, production, speaker_evidence
from pipeline import performance_gate as gate

CRYING = {"emotion": "委屈痛哭", "visible": "满脸泪水", "who": "盘发白衣妇人"}
PORTRAIT = {"emotion": "温和微笑", "visible": "", "who": "黑白遗照里的女子"}
CAST = {
    "characters": [
        {
            "id": "C01",
            "name": "林黛玉",
            "image": {"prompt": "18-year-old young woman, ivory blouse"},
        },
        {"id": "C04", "name": "贾敏", "image": {"prompt": "45-year-old woman, hair pinned up"}},
    ]
}
ROWS = [
    {"text": "老夫夜观天象，看大姐你是命中带苦。"},
    {
        "text": "宝贝懂了吧，男人靠不住啊！",
        "performance": CRYING,
        "speaker": "C04",
        "speaker_needs_review": False,
        "evidence": {"a": {"current": True, "character_id": "C04", "person_id": "P1"}},
    },
    {"text": "妈", "performance": PORTRAIT},
]


def _script(delivery, *, daughter="望着遗照喃喃呼唤"):
    flow = [
        {"action": "卦摊前，贾敏带着林黛玉坐下。"},
        {"speaker": "C04", "line": "宝贝懂了吧，男人靠不住啊！", "delivery": delivery},
        {"speaker": "C01", "line": "妈", "delivery": daughter},
        {"speaker": "C01", "line": "这是剧本加的一句", "delivery": ""},
    ]
    scene = {"sceneId": "S01", "characters": ["C01", "C04"], "flow": flow}
    return {"episodes": [{"scenes": [scene]}]}


def _answer(probabilities):
    choice = max(probabilities, key=probabilities.get)
    return SimpleNamespace(
        choice=choice, confidence=probabilities[choice], probabilities=probabilities
    )


class FakeJev:
    """Recognises the mother by her frame; a teasing delivery contradicts her tears."""

    def __init__(self, monkeypatch, fit=None):
        self.states = []
        self.fit = fit
        monkeypatch.setattr(jev_client, "_find_api_key", lambda: "key")
        monkeypatch.setattr(jev_client, "open_client", lambda _key: self)

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def system_one(self, state, questions, **_kwargs):
        self.states.append(state)
        assert set(questions) == {"fit"}
        delivery = state["script"]["delivery"]
        if self.fit:
            fit = self.fit
        elif "调侃" in delivery:
            fit = {"contradicts": 0.97, "consistent": 0.02, "no_cue": 0.01}
        elif not delivery:
            fit = {"no_cue": 0.9, "consistent": 0.05, "contradicts": 0.05}
        else:
            fit = {"consistent": 0.95, "contradicts": 0.04, "no_cue": 0.01}
        return SimpleNamespace(answers={"fit": _answer(fit)})


def test_lines_are_paired_in_order_and_only_readable_frames_count():
    rows = [*ROWS, {"text": "来来来！", "performance": {"emotion": "看不清"}}]
    pairs = gate.pair_lines(_script("打趣"), rows)

    assert [(p["line"], p["source_text"]) for p in pairs] == [
        ("宝贝懂了吧，男人靠不住啊！", "宝贝懂了吧，男人靠不住啊！"),
        ("妈", "妈"),
    ]
    assert pairs[0]["action_before"] == "卦摊前，贾敏带着林黛玉坐下。"
    assert pairs[0]["characters"] == ["C01", "C04"]


def test_a_split_source_line_pairs_every_part():
    script = _script("打趣")
    flow = script["episodes"][0]["scenes"][0]["flow"]
    flow[1:2] = [
        {"speaker": "C04", "line": "宝贝懂了吧，", "delivery": "哭着"},
        {"speaker": "C04", "line": "男人靠不住啊！", "delivery": "哭着"},
    ]
    assert [p["source_text"] for p in gate.pair_lines(script, ROWS)][:2] == [ROWS[1]["text"]] * 2


def test_the_speaker_played_against_the_source_emotion_fails(monkeypatch):
    jev = FakeJev(monkeypatch)

    result = gate.check_performance(_script("转头打趣，语带调侃"), CAST, ROWS)

    assert result["status"] == "failed"
    assert len(result["blocking"]) == 1
    message = result["blocking"][0]
    assert "宝贝懂了吧" in message and "委屈痛哭" in message and "语带调侃" in message
    assert set(jev.states[0]) == {"line", "speaker", "source_frame", "script"}
    assert len(jev.states) == 1  # only the already identified speaker's performance


def test_the_same_emotion_passes_and_a_frame_of_someone_else_is_not_compared(monkeypatch):
    FakeJev(monkeypatch)
    # The frame of 「妈」 is the portrait; its smile says nothing about the speaker.
    result = gate.check_performance(_script("抽泣着，满脸是泪", daughter="笑着调侃"), CAST, ROWS)
    assert result["status"] == "passed" and result["review"] == []


def test_confirmed_anonymous_speaker_compares_only_performance(monkeypatch):
    jev = FakeJev(monkeypatch)
    script = {
        "episodes": [
            {
                "scenes": [
                    {
                        "sceneId": "S01",
                        "characters": [],
                        "flow": [{"speaker": "extra", "line": "请稍等", "delivery": "笑着说"}],
                    }
                ]
            }
        ]
    }
    source = [
        {
            "text": "请稍等",
            "speaker": "extra",
            "speaker_needs_review": False,
            "performance": {"emotion": "友好微笑", "who": "穿反光背心的工人"},
            "evidence": {
                "a": {"current": True, "person_id": "P3", "status": "extra", "look": "工人"}
            },
        }
    ]
    result = gate.check_performance(script, CAST, source)
    assert result["status"] == "passed" and result["checked"] == 1
    assert jev.states[0]["speaker"] == "非角色人物"


def test_a_weaker_contradiction_or_a_missing_cue_is_only_reviewed(monkeypatch):
    FakeJev(monkeypatch)
    result = gate.check_performance(_script(""), CAST, ROWS)
    assert result["status"] == "passed"
    assert result["review"][0]["message"].startswith("S01「宝贝懂了吧")

    FakeJev(monkeypatch, fit={"contradicts": 0.75, "consistent": 0.25, "no_cue": 0.0})
    result = gate.check_performance(_script("平静地说"), CAST, ROWS)
    assert result["status"] == "passed"
    assert result["review"][0]["fit"]["contradicts"] == 0.75


def test_no_jev_key_leaves_the_check_unavailable(monkeypatch):
    monkeypatch.setattr(jev_client, "_find_api_key", lambda: "")
    assert gate.check_performance(_script("调侃"), CAST, ROWS)["status"] == "unavailable"


def test_a_failing_request_is_reported_not_judged(monkeypatch):
    jev = FakeJev(monkeypatch)

    def broken(**_kwargs):
        raise OSError("jev unreachable")

    jev.system_one = broken
    result = gate.check_performance(_script("调侃"), CAST, ROWS)
    assert result["status"] == "unavailable"
    assert "jev unreachable" in result["errors"][0]


def test_the_script_check_reads_the_attributed_transcript(monkeypatch, tmp_path):
    FakeJev(monkeypatch)
    for name, value in {"script": _script("语带调侃"), "cast": CAST}.items():
        (tmp_path / f"{name}.json").write_text(
            json.dumps(value, ensure_ascii=False), encoding="utf-8"
        )
    attributed = tmp_path / "source-transcript-attributed.json"
    attributed.write_text(json.dumps({"attributions": ROWS}, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(production, "stage_path", lambda d, name, **_k: d / f"{name}.json")
    paths = {"cast": tmp_path / "cast.json"}

    assert production.script_performance(tmp_path, paths, attributed, "jev")["status"] == "failed"

    assert production.script_performance(tmp_path, paths, attributed, "jev")["reused"] == 1

    attributed.write_text(json.dumps({"attributions": [{"text": "妈"}]}), encoding="utf-8")
    assert production.script_performance(tmp_path, paths, attributed, "jev")["status"] == "skipped"


def test_per_line_judgments_reuse_and_only_changed_inputs_are_requested(monkeypatch, tmp_path):
    jev = FakeJev(monkeypatch)
    rows = [copy.deepcopy(ROWS[1]), {**copy.deepcopy(ROWS[1]), "text": "请稍等"}]
    script = _script("抽泣着")
    flow = script["episodes"][0]["scenes"][0]["flow"]
    flow[2:] = [{"speaker": "C04", "line": "请稍等", "delivery": "哭着说"}]
    assert gate.check_performance(script, CAST, rows, cache_dir=tmp_path)["reused"] == 0
    assert gate.check_performance(script, CAST, rows, cache_dir=tmp_path)["reused"] == 2
    assert len(jev.states) == 2
    flow[1]["delivery"] = "语带调侃"
    changed = gate.check_performance(script, CAST, rows, cache_dir=tmp_path)
    assert changed["status"] == "failed" and changed["reused"] == 1
    assert len(jev.states) == 3
    rows[1]["performance"]["emotion"] = "强忍泪水"
    assert gate.check_performance(script, CAST, rows, cache_dir=tmp_path)["reused"] == 1
    assert len(jev.states) == 4
    assert (
        gate.check_performance(script, CAST, rows, model="other", cache_dir=tmp_path)["reused"]
        == 0
    )
    assert len(jev.states) == 6
    record = next(
        value
        for path in tmp_path.glob("*.json")
        if (value := json.loads(path.read_text(encoding="utf-8")))["requested_model"]
        == "jev-latest"
    )
    assert record["requested_model"] == "jev-latest" and record["model"] == "jev-latest"


def test_cached_fit_never_bypasses_unresolved_speaker_evidence(monkeypatch, tmp_path):
    jev = FakeJev(monkeypatch)
    rows = copy.deepcopy(ROWS)
    gate.check_performance(_script("哭着"), CAST, rows, cache_dir=tmp_path)
    rows[1]["speaker_needs_review"] = True
    result = gate.check_performance(_script("哭着"), CAST, rows, cache_dir=tmp_path)
    assert result["checked"] == 0 and len(jev.states) == 1


def test_failed_and_corrupt_judgments_are_retried(monkeypatch, tmp_path):
    jev = FakeJev(monkeypatch)
    original = jev.system_one
    jev.system_one = lambda **_kw: (_ for _ in ()).throw(OSError("temporary error"))
    assert (
        gate.check_performance(_script("哭着"), CAST, ROWS, cache_dir=tmp_path)["status"]
        == "unavailable"
    )
    assert not list(tmp_path.glob("*.json"))
    jev.system_one = original
    assert (
        gate.check_performance(_script("哭着"), CAST, ROWS, cache_dir=tmp_path)["status"]
        == "passed"
    )
    target = next(tmp_path.glob("*.json"))
    target.write_text("broken json", encoding="utf-8")
    assert gate.check_performance(_script("哭着"), CAST, ROWS, cache_dir=tmp_path)["reused"] == 0
    assert len(jev.states) == 2


def test_manual_check_then_next_reuses_the_same_performance(monkeypatch, tmp_path):
    from pipeline import autopilot

    jev = FakeJev(monkeypatch)
    documents = {
        "outline": {"params": {"adaptMode": "忠实"}},
        "cast": CAST,
        "art": {},
        "script": _script("哭着说"),
        "storyboard": {},
    }
    for stage, document in documents.items():
        (tmp_path / f"{stage}.json").write_text(json.dumps(document), encoding="utf-8")
    attributed = tmp_path / "source-transcript-attributed.json"
    attributed.write_text(json.dumps({"attributions": ROWS}), encoding="utf-8")
    original_check = production.script_performance(
        tmp_path, {"cast": tmp_path / "cast.json"}, attributed, "jev"
    )
    assert original_check["checked"] == 1 and len(jev.states) == 1
    seen = []

    def check_stage(directory, stage, *_a, **_k):
        row = {"stage": stage, "status": "passed", "input_hashes": {}}
        if stage == "script":
            row["performance"] = production.script_performance(
                directory, {"cast": directory / "cast.json"}, attributed, "jev"
            )
            seen.append(row["performance"]["reused"])
        return row

    monkeypatch.setattr(production, "check_stage", check_stage)
    assert autopilot.next_step(tmp_path, jev=False)["action"] == "human_review_speakers"
    assert seen == [1] and len(jev.states) == 1


def test_the_jev_baseline_shows_what_the_frames_show(tmp_path):
    attributed = tmp_path / "source-transcript-attributed.json"
    row = {"speaker_name": "贾敏", "text": "男人靠不住啊！", "performance": CRYING}
    attributed.write_text(
        json.dumps({"attributions": [row]}, ensure_ascii=False), encoding="utf-8"
    )

    text = production.render_attributed_source(tmp_path, attributed).read_text(encoding="utf-8")

    assert "[贾敏] 男人靠不住啊！  〔画面：盘发白衣妇人，委屈痛哭：满脸泪水〕" in text


@pytest.mark.parametrize("carried", [True, False])
def test_attribution_carries_only_the_a_line_performance(carried):
    segment = {
        "id": "a",
        "text": "请在此稍候。",
        "start": 0,
        "end": 1,
        **({"performance": CRYING} if carried else {}),
    }
    result = speaker_evidence.attribute([segment], None, {}, None)["attributions"][0]
    assert result.get("performance") == (CRYING if carried else None)
    assert ("performance" in result) is carried


def test_context_b_cannot_turn_a_listener_emotion_into_speaker_performance(monkeypatch):
    row = copy.deepcopy(ROWS[1])
    row["evidence"] = {
        "a": {"speaker": "offscreen", "current": True},
        "b": {"speaker": "P1", "basis": "context"},
        "present_people": ["P1"],
        "constraints": {"status": "passed"},
    }
    jev = FakeJev(monkeypatch)
    result = gate.check_performance(_script("平静"), CAST, [row])
    assert result["checked"] == 0 and jev.states == []
