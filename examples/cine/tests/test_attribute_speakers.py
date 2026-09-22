"""Speaker attribution: JEV decides who says each ASR line, from visual features."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "skills/film-analysis"))
from pipeline import attribute_speakers  # noqa: E402


class Answer:
    def __init__(self, **values):
        self.__dict__.update(values)

    def model_dump(self, mode="json"):
        return dict(self.__dict__)


class Response:
    model = "jev-test-1"

    def __init__(self, answers):
        self.answers = answers


class Client:
    def __init__(self, response):
        self.response = response
        self.captured = {}

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def system_one(self, **kwargs):
        self.captured = kwargs
        return self.response


CAST = {
    "characters": [
        {
            "id": "C01",
            "name": "林黛玉",
            "aliases": ["黛玉"],
            "oneLiner": "初次到外婆家，不认得亲戚辈分",
            "persona": {"gender": "女", "ageRange": "十八岁", "personality": ["清冷", "自嘲"]},
        },
        {
            "id": "C02",
            "name": "贾敏",
            "aliases": ["大姐"],
            "oneLiner": "黛玉之母、林如海之妻",
            "persona": {"gender": "女", "ageRange": "四十二岁"},
        },
    ]
}


def test_roster_builds_from_cast_features():
    roster = attribute_speakers._roster(CAST)
    assert "林黛玉" in roster["C01"]
    assert "黛玉" in roster["C01"]           # aliases reach the roster
    assert "十八岁" in roster["C01"]          # persona fields reach the roster
    assert attribute_speakers.UNATTRIBUTED in roster


def test_conditions_write_the_exclusion_into_the_option():
    """A bare label lets an address term flip the attribution to the addressee."""
    roster = attribute_speakers._roster(CAST)
    conditioned = attribute_speakers._conditions({"text": "妈，你算命的钱还是爸爸出的呢！"}, roster)
    assert "没有人会称呼自己为妈" in conditioned["C02"]
    # Untouched candidates keep their plain description.
    assert conditioned["C01"] == roster["C01"]


def test_attribute_speakers_returns_typed_attributions(monkeypatch):
    segments = [
        {"id": "l1", "scene": 1, "text": "宝贝懂了吧？男人靠不住啊。"},
        {"id": "l2", "scene": 1, "text": "老婆，我升官了！"},
    ]
    answers = {
        "l1": Answer(type="choice", choice="C02", confidence=0.9, probabilities={"C02": 0.9}),
        "l1__delivery": Answer(type="choice", choice="spoken", confidence=0.9,
                               probabilities={"spoken": 0.9}),
        "l2": Answer(type="choice", choice="C07", confidence=0.95, probabilities={"C07": 0.95}),
        "l2__delivery": Answer(type="choice", choice="spoken", confidence=0.95,
                               probabilities={"spoken": 0.95}),
    }
    captured = {}

    def factory(_key, **_kwargs):
        client = Client(Response(answers))
        captured["client"] = client
        return client

    monkeypatch.setattr(attribute_speakers, "open_client", factory)
    monkeypatch.setattr(attribute_speakers, "_find_api_key", lambda: "test-key")
    result = attribute_speakers.attribute_speakers(segments, CAST, scenes={1: "卦摊"})

    assert [a["speaker"] for a in result["attributions"]] == ["C02", "C07"]
    assert all(a["needs_review"] is False for a in result["attributions"])
    assert result["model"] == "jev-test-1"

    # The exchange is supplied as state so reply structure is usable evidence.
    state = captured["client"].captured["state"]
    assert state["scenes"][0]["sequence"][0]["text"] == "宝贝懂了吧？男人靠不住啊。"


def test_low_confidence_and_unclear_are_flagged_for_review(monkeypatch):
    segments = [{"id": "l1", "scene": 1, "text": "模糊的一句"}]
    answers = {
        "l1": Answer(
            type="choice",
            choice=attribute_speakers.UNATTRIBUTED,
            confidence=0.4,
            probabilities={attribute_speakers.UNATTRIBUTED: 0.4},
        )
    }
    monkeypatch.setattr(attribute_speakers, "open_client", lambda _k, **_kw: Client(Response(answers)))
    monkeypatch.setattr(attribute_speakers, "_find_api_key", lambda: "test-key")
    result = attribute_speakers.attribute_speakers(segments, CAST)
    assert result["attributions"][0]["needs_review"] is True


def test_missing_key_is_reported_not_silently_attributed(monkeypatch):
    monkeypatch.setattr(attribute_speakers, "_find_api_key", lambda: "")
    result = attribute_speakers.attribute_speakers([{"id": "l1", "text": "x"}], CAST)
    assert "error" in result
    assert "attributions" not in result


def test_exchanges_split_on_silence_not_into_one_block():
    """One 18-line block dilutes each judgment with unrelated neighbours."""
    segments = [
        {"id": "a", "start": 0.0, "end": 3.0, "text": "第一句"},
        {"id": "b", "start": 3.5, "end": 6.0, "text": "紧接的一句"},
        {"id": "c", "start": 20.0, "end": 22.0, "text": "很久之后的一句"},
    ]
    groups = attribute_speakers._scene_groups(segments)
    assert [[s["id"] for _, s in g] for g in groups] == [["a", "b"], ["c"]]


def test_exchanges_honour_an_explicit_scene_field():
    segments = [
        {"scene": 1, "text": "a"},
        {"scene": 1, "text": "b"},
        {"scene": 2, "text": "c"},
    ]
    groups = attribute_speakers._scene_groups(segments)
    assert len(groups) == 2


def test_corrected_address_line_marks_the_established_member_ineligible():
    """王熙凤 knows the family; only the newcomer guesses and corrects herself."""
    roster = {
        "C01": "林黛玉｜寄居表妹，母亲新丧后初到外婆家",
        "C04": "王熙凤｜掌权表嫂，家族实权管事少奶奶",
        attribute_speakers.UNATTRIBUTED: "无法判定",
    }
    conditions = attribute_speakers._conditions({"text": "这位姐姐……哟，这不是姐姐，是妹妹。"}, roster)
    assert "排除" in conditions["C04"]
    assert "不认得" in conditions["C01"]


def test_scene_groups_do_not_mutate_the_input_segments():
    segments = [{"id": "a", "start": 0.0, "end": 1.0, "text": "x"}]
    snapshot = json.dumps(segments)
    attribute_speakers._scene_groups(segments)
    assert json.dumps(segments) == snapshot


def test_delivery_mode_is_captured_and_flagged(monkeypatch):
    """A voice-over has no addressee and gets no reply, so it must be marked."""
    segments = [
        {"id": "spoken", "scene": 1, "text": "老婆，我升官了！"},
        {"id": "vo", "scene": 1, "text": "完蛋了，这个表哥我听说过，颠中之颠！"},
    ]
    answers = {
        "spoken": Answer(type="choice", choice="C07", confidence=0.9, probabilities={"C07": 0.9}),
        "spoken__delivery": Answer(type="choice", choice="spoken", confidence=0.95,
                                   probabilities={"spoken": 0.95}),
        "vo": Answer(type="choice", choice="C01", confidence=0.9, probabilities={"C01": 0.9}),
        "vo__delivery": Answer(type="choice", choice="voice_over", confidence=0.9,
                               probabilities={"voice_over": 0.9}),
    }
    monkeypatch.setattr(attribute_speakers, "open_client", lambda _k, **_kw: Client(Response(answers)))
    monkeypatch.setattr(attribute_speakers, "_find_api_key", lambda: "test-key")
    result = attribute_speakers.attribute_speakers(segments, CAST, scenes={1: "大厅"})
    by_id = {a["id"]: a for a in result["attributions"]}
    assert by_id["spoken"]["delivery"] == "spoken"
    assert by_id["vo"]["delivery"] == "voice_over"
    assert all(a["needs_review"] is False for a in result["attributions"])


def test_uncertain_delivery_forces_review_even_when_the_speaker_is_certain(monkeypatch):
    segments = [{"id": "l1", "scene": 1, "text": "这种情况，我们应该当然是陪伴啊。"}]
    answers = {
        "l1": Answer(type="choice", choice="C08", confidence=0.95, probabilities={"C08": 0.95}),
        "l1__delivery": Answer(type="choice", choice="voice_over", confidence=0.28,
                               probabilities={"voice_over": 0.4}),
    }
    monkeypatch.setattr(attribute_speakers, "open_client", lambda _k, **_kw: Client(Response(answers)))
    monkeypatch.setattr(attribute_speakers, "_find_api_key", lambda: "test-key")
    result = attribute_speakers.attribute_speakers(segments, CAST, scenes={1: "佣人齐声应和"})
    assert result["attributions"][0]["needs_review"] is True


def test_delivery_question_is_batched_into_the_same_request(monkeypatch):
    """Delivery is independent of speaker, so it costs no extra round trip."""
    segments = [{"id": "l1", "scene": 1, "text": "x"}]
    answers = {
        "l1": Answer(type="choice", choice="C01", confidence=0.9, probabilities={"C01": 0.9}),
        "l1__delivery": Answer(type="choice", choice="spoken", confidence=0.9,
                               probabilities={"spoken": 0.9}),
    }
    captured = {}

    def factory(_key, **_kwargs):
        client = Client(Response(answers))
        captured["client"] = client
        return client

    monkeypatch.setattr(attribute_speakers, "open_client", factory)
    monkeypatch.setattr(attribute_speakers, "_find_api_key", lambda: "test-key")
    attribute_speakers.attribute_speakers(segments, CAST)
    asked = set(captured["client"].captured["questions"])
    assert asked == {"l1", "l1__delivery"}


def test_candidate_merge_pairs_detects_kinship_and_named_roles():
    found = {
        "林黛玉": "自报姓名",
        "母亲": "出现称呼",
        "妻子": "出现称呼",
        "孙女": "出现称呼",
        "算命老者": "出现称呼",
        "死骗子": "出现称呼",
    }
    pairs = attribute_speakers._candidate_merge_pairs(found)
    assert ("母亲", "妻子") in pairs
    assert ("算命老者", "死骗子") in pairs
    assert ("林黛玉", "孙女") in pairs


def test_derived_cast_merges_roles_offline_fallback():
    segments = [
        {"text": "宝贝懂了吧？男人靠不住啊。"},
        {"text": "妈，你算命的钱还是爸爸出的呢！"},
        {"text": "老婆，我升官了！"},
        {"text": "我叫林黛玉，是林氏集团的继承人。"},
        {"text": "我的好孙女，来见见表妹。"},
    ]
    cast = attribute_speakers.derived_cast(segments, api_key="")
    cnames = [c["name"] for c in cast["characters"]]
    assert "母亲/妻子" in cnames
    assert "林黛玉" in cnames
    # Lin Daiyu merges with 孙女 and 表妹 in fallback
    daiyu = next(c for c in cast["characters"] if c["name"] == "林黛玉")
    assert "孙女" in daiyu["aliases"] or "表妹" in daiyu["aliases"]


def test_conditions_generalizes_mother_and_wife_exclusions():
    roster = {
        "R01": "母亲/妻子｜被孩子叫妈被丈夫叫老婆",
        "R02": "林黛玉｜女儿",
        attribute_speakers.UNATTRIBUTED: "无法判定",
    }
    cond_mom = attribute_speakers._conditions({"text": "妈，你算命的钱！"}, roster)
    assert "没有人会称呼自己为妈" in cond_mom["R01"]

    cond_wife = attribute_speakers._conditions({"text": "老婆，我升官了！"}, roster)
    assert "她是被称呼为老婆的那个人" in cond_wife["R01"]

