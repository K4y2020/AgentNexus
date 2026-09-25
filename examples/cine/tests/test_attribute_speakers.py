"""Speaker attribution: JEV decides who says each ASR line, from cast features."""

import json
import math
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
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def system_one(self, **kwargs):
        self.captured = kwargs
        self.calls.append(kwargs)
        return self.response


def choice(option, confidence=0.9, probabilities=None):
    return Answer(
        type="choice",
        choice=option,
        confidence=confidence,
        probabilities=probabilities if probabilities is not None else {option: confidence},
    )


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
        {"id": "C07", "name": "林如海", "oneLiner": "黛玉之父、贾敏之夫"},
        {"id": "C08", "name": "佣人群体", "persona": {"gender": "群体"}},
    ]
}

# A different story, with a cast document in the documented schema: cards
# carry no ids, and kinship lives in persona.relationships.
FERRY_CAST = {
    "characters": [
        {
            "name": "老周",
            "aliases": ["老伯"],
            "oneLiner": "在渡口摆渡四十年的老船夫",
            "persona": {
                "gender": "男",
                "ageRange": "约七十岁",
                "relationships": [{"name": "小满", "relation": "外孙"}],
            },
        },
        {
            "name": "小满",
            "aliases": ["满儿"],
            "persona": {
                "gender": "男",
                "ageRange": "九岁",
                "relationships": [{"name": "老周", "relation": "外公"}],
            },
        },
        {"name": "沈知微", "aliases": ["沈小姐"], "persona": {"gender": "女"}},
    ]
}


def _patch(monkeypatch, answers):
    captured = {}

    def factory(_key, **_kwargs):
        client = Client(Response(answers))
        captured["client"] = client
        return client

    monkeypatch.setattr(attribute_speakers, "open_client", factory)
    monkeypatch.setattr(attribute_speakers, "_find_api_key", lambda: "test-key")
    return captured


def test_roster_builds_from_cast_features():
    roster = attribute_speakers._roster(CAST)
    assert "林黛玉" in roster["C01"]
    assert "黛玉" in roster["C01"]           # aliases reach the roster
    assert "十八岁" in roster["C01"]          # persona fields reach the roster
    assert attribute_speakers.UNATTRIBUTED in roster


def test_roster_accepts_a_cast_without_ids_and_carries_relationships():
    """cast.json cards may omit ``id``; they must still be options."""
    roster = attribute_speakers._roster(FERRY_CAST)
    assert {"老周", "小满", "沈知微"} <= set(roster)
    assert "小满——外孙" in roster["老周"]
    assert attribute_speakers._name_for(FERRY_CAST, "小满") == "小满"


def test_vocative_of_a_cast_alias_excludes_that_character():
    """A bare label lets an address term flip the attribution to the addressee."""
    roster = attribute_speakers._roster(FERRY_CAST)
    terms = attribute_speakers._address_terms(FERRY_CAST)
    conditioned = attribute_speakers._conditions(
        {"text": "老伯，这雾什么时候散？"}, roster, terms=terms
    )
    assert "本句直接以「老伯」称呼此人" in conditioned["老周"]
    # Untouched candidates keep their plain description.
    assert conditioned["沈知微"] == roster["沈知微"]


def test_a_mention_or_self_introduction_is_not_a_vocative():
    roster = attribute_speakers._roster(FERRY_CAST)
    terms = attribute_speakers._address_terms(FERRY_CAST)
    for text in ("我叫小满，今年九岁。", "老伯说今天不开船。"):
        assert attribute_speakers._conditions({"text": text}, roster, terms=terms) == roster


def test_confident_addressee_is_written_into_the_option():
    roster = attribute_speakers._roster(CAST)
    conditioned = attribute_speakers._conditions(
        {"text": "妈，你算命的钱还是爸爸出的呢！"}, roster, addressee=("C02", 0.92)
    )
    assert "本句是对此人说的" in conditioned["C02"]
    assert conditioned["C01"] == roster["C01"]


def test_description_keywords_and_phrases_create_no_conditions():
    """Sample-story rules (newcomer vs matriarch, 「我们应该」 chorus) are gone."""
    roster = {
        "C01": "林黛玉｜寄居表妹，母亲新丧后初到外婆家",
        "C04": "王熙凤｜掌权表嫂，家族实权管事少奶奶",
        attribute_speakers.UNATTRIBUTED: "无法判定",
    }
    for text in ("这位姐姐……哟，这不是姐姐，是妹妹。", "你个死骗子！", "我们应该当然是陪伴啊。"):
        assert attribute_speakers._conditions({"text": text}, roster) == roster


def test_group_condition_comes_from_the_cast_not_from_phrases():
    roster = attribute_speakers._roster(CAST)
    conditioned = attribute_speakers._conditions({"text": "开船咯"}, roster, groups={"C08"})
    assert "多人齐声" in conditioned["C08"]
    assert "我们应该" not in conditioned["C08"]


def test_attribute_speakers_returns_typed_attributions(monkeypatch):
    segments = [
        {"id": "l1", "scene": 1, "text": "宝贝懂了吧？男人靠不住啊。"},
        {"id": "l2", "scene": 1, "text": "老婆，我升官了！"},
    ]
    answers = {
        "l1": choice("C02"),
        "l1__delivery": choice("spoken"),
        "l2": choice("C07", 0.95),
        "l2__delivery": choice("spoken", 0.95),
    }
    captured = _patch(monkeypatch, answers)
    result = attribute_speakers.attribute_speakers(segments, CAST, scenes={1: "卦摊"})

    assert [a["speaker"] for a in result["attributions"]] == ["C02", "C07"]
    assert all(a["needs_review"] is False for a in result["attributions"])
    assert result["model"] == "jev-test-1"

    # The exchange is supplied as state so reply structure is usable evidence.
    state = captured["client"].captured["state"]
    assert state["scenes"][0]["sequence"][0]["text"] == "宝贝懂了吧？男人靠不住啊。"


def test_addressee_pass_runs_first_and_feeds_the_speaker_question(monkeypatch):
    segments = [
        {"id": "a", "scene": 1, "text": "这雾什么时候散？"},
        {"id": "b", "scene": 1, "text": "雾一厚，连自己的手都看不清。"},
    ]
    answers = {
        "a__addressee": choice("老周", 0.9),
        "b__addressee": choice("沈知微", 0.6),   # not sure enough to exclude
        "a": choice("沈知微"),
        "a__delivery": choice("spoken"),
        "b": choice("老周"),
        "b__delivery": choice("spoken"),
    }
    captured = _patch(monkeypatch, answers)
    result = attribute_speakers.attribute_speakers(segments, FERRY_CAST)

    first, second = captured["client"].calls
    assert set(first["questions"]) == {"a__addressee", "b__addressee"}
    assert set(second["questions"]) == {"a", "a__delivery", "b", "b__delivery"}
    assert "本句是对此人说的" in second["questions"]["a"].criteria["老周"]
    assert "本句是对此人说的" not in second["questions"]["b"].criteria["沈知微"]
    assert [a["speaker"] for a in result["attributions"]] == ["沈知微", "老周"]
    assert result["attributions"][0]["addressee"] == "老周"
    assert all(a["needs_review"] is False for a in result["attributions"])


def test_speaker_equal_to_confident_addressee_is_flagged(monkeypatch):
    segments = [{"id": "a", "scene": 1, "text": "老伯，这雾什么时候散？"}]
    answers = {
        "a__addressee": choice("老周", 0.9),
        "a": choice("老周", 0.8),
        "a__delivery": choice("spoken"),
    }
    _patch(monkeypatch, answers)
    item = attribute_speakers.attribute_speakers(segments, FERRY_CAST)["attributions"][0]
    assert item["needs_review"] is True
    assert item["speaker_needs_review"] is True
    assert "speaker_is_addressee" in item["review_reasons"]


def test_an_option_outside_the_roster_is_not_an_answer(monkeypatch):
    segments = [{"id": "a", "scene": 1, "text": "开船！"}]
    answers = {"a": choice("C99", 0.99), "a__delivery": choice("spoken")}
    _patch(monkeypatch, answers)
    item = attribute_speakers.attribute_speakers(segments, FERRY_CAST)["attributions"][0]
    assert item["speaker"] == attribute_speakers.UNATTRIBUTED
    assert item["needs_review"] is True
    assert item["delivery_needs_review"] is False


def test_near_tie_between_two_candidates_is_flagged(monkeypatch):
    segments = [{"id": "a", "scene": 1, "text": "走吧。"}]
    answers = {
        "a": choice("老周", 0.6, {"老周": 0.48, "沈知微": 0.42, "unclear": 0.1}),
        "a__delivery": choice("spoken"),
    }
    _patch(monkeypatch, answers)
    item = attribute_speakers.attribute_speakers(segments, FERRY_CAST)["attributions"][0]
    assert item["needs_review"] is True
    assert "speaker_ambiguous" in item["review_reasons"]


def test_a_missing_speaker_answer_still_yields_an_unresolved_line(monkeypatch):
    """Dropping the line would silently shorten the transcript."""
    segments = [
        {"id": "a", "scene": 1, "text": "老伯，这雾什么时候散？"},
        {"id": "b", "scene": 1, "text": "雾一厚，连自己的手都看不清。"},
    ]
    answers = {
        "a": choice("沈知微"),
        "a__delivery": choice("spoken"),
        # No answer at all for "b", neither speaker nor delivery.
    }
    _patch(monkeypatch, answers)
    result = attribute_speakers.attribute_speakers(segments, FERRY_CAST)
    assert [a["id"] for a in result["attributions"]] == ["a", "b"]
    missing = result["attributions"][1]
    assert missing["speaker"] == attribute_speakers.UNATTRIBUTED
    assert missing["needs_review"] is True
    assert missing["speaker_needs_review"] is True
    assert missing["delivery_needs_review"] is True
    assert "speaker_missing_answer" in missing["review_reasons"]
    assert "delivery_missing_answer" in missing["review_reasons"]


def test_duplicate_segment_ids_do_not_overwrite_each_other(monkeypatch):
    segments = [
        {"id": "a", "scene": 1, "text": "老伯，开船吗？"},
        {"id": "a", "scene": 1, "text": "开，坐稳了。"},
        {"id": "b", "scene": 1, "text": "谢谢老伯。"},
    ]
    answers = {
        "a": choice("沈知微"),
        "a__delivery": choice("spoken"),
        "a#2": choice("老周"),
        "a#2__delivery": choice("spoken"),
        "b": choice("沈知微"),
        "b__delivery": choice("spoken"),
    }
    captured = _patch(monkeypatch, answers)
    result = attribute_speakers.attribute_speakers(segments, FERRY_CAST)

    rows = result["attributions"]
    assert len(rows) == len(segments)
    assert [r["id"] for r in rows] == ["a", "a", "b"]           # original ids preserved
    assert [r["question_id"] for r in rows] == ["a", "a#2", "b"]
    assert [r["text"] for r in rows] == [s["text"] for s in segments]
    assert [r["speaker"] for r in rows] == ["沈知微", "老周", "沈知微"]
    assert [r.get("duplicate_id", False) for r in rows] == [True, True, False]
    asked = captured["client"].calls[-1]["questions"]
    assert {"a", "a#2", "b"} <= set(asked)


def test_question_keys_avoid_collisions_with_derived_keys():
    keys = attribute_speakers._question_keys(
        [{"id": "x__delivery"}, {"id": "x"}, {}, {"id": "seg002"}]
    )
    flat = [k for k, _ in keys]
    assert len(set(flat)) == 4
    derived = {k + s for k in flat for s in ("", "__addressee", "__delivery")}
    assert len(derived) == 12
    assert [original for _, original in keys] == ["x__delivery", "x", "seg002", "seg002"]


@pytest.mark.parametrize("confidence", [math.nan, math.inf, -math.inf, 1.5, -0.2, True, "0.9"])
def test_malformed_speaker_confidence_is_unresolved(monkeypatch, confidence):
    """``nan < MIN_CONFIDENCE`` is False, so NaN must not pass as a settled answer."""
    segments = [{"id": "a", "scene": 1, "text": "开船咯。"}]
    answers = {
        "a": Answer(choice="老周", confidence=confidence, probabilities={"老周": 0.9}),
        "a__delivery": choice("spoken"),
    }
    _patch(monkeypatch, answers)
    item = attribute_speakers.attribute_speakers(segments, FERRY_CAST)["attributions"][0]
    assert item["speaker"] == attribute_speakers.UNATTRIBUTED
    assert item["confidence"] == 0.0
    assert item["needs_review"] is True and item["speaker_needs_review"] is True
    assert "speaker_malformed_answer" in item["review_reasons"]
    assert item["delivery_needs_review"] is False
    json.dumps(item, allow_nan=False)  # the public output stays strict JSON


@pytest.mark.parametrize(
    "probabilities",
    [{"老周": math.nan}, {"老周": 0.9, "沈知微": math.inf}, {"老周": 1.2}, ["老周"]],
)
def test_malformed_probabilities_make_the_answer_unresolved(monkeypatch, probabilities):
    segments = [{"id": "a", "scene": 1, "text": "开船咯。"}]
    answers = {
        "a": Answer(choice="老周", confidence=0.9, probabilities=probabilities),
        "a__delivery": choice("spoken"),
    }
    _patch(monkeypatch, answers)
    item = attribute_speakers.attribute_speakers(segments, FERRY_CAST)["attributions"][0]
    assert item["speaker"] == attribute_speakers.UNATTRIBUTED
    assert "speaker_malformed_answer" in item["review_reasons"]
    json.dumps(item, allow_nan=False)


def test_malformed_delivery_and_addressee_confidence_fail_closed(monkeypatch):
    segments = [{"id": "a", "scene": 1, "text": "这雾什么时候散？"}]
    answers = {
        # NaN addressee confidence must not become an exclusion...
        "a__addressee": Answer(choice="老周", confidence=math.nan, probabilities={}),
        "a": choice("沈知微"),
        # ...and NaN delivery confidence must not read as settled.
        "a__delivery": Answer(choice="spoken", confidence=math.nan, probabilities={}),
    }
    captured = _patch(monkeypatch, answers)
    item = attribute_speakers.attribute_speakers(segments, FERRY_CAST)["attributions"][0]
    assert item["addressee"] is None
    second = captured["client"].calls[-1]["questions"]
    assert "本句是对此人说的" not in second["a"].criteria["老周"]
    assert item["speaker"] == "沈知微" and item["speaker_needs_review"] is False
    assert item["delivery"] == attribute_speakers.UNATTRIBUTED
    assert item["delivery_needs_review"] is True and item["needs_review"] is True
    assert "delivery_malformed_answer" in item["review_reasons"]


def test_low_confidence_and_unclear_are_flagged_for_review(monkeypatch):
    segments = [{"id": "l1", "scene": 1, "text": "模糊的一句"}]
    answers = {"l1": choice(attribute_speakers.UNATTRIBUTED, 0.4)}
    _patch(monkeypatch, answers)
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
        "spoken": choice("C07"),
        "spoken__delivery": choice("spoken", 0.95),
        "vo": choice("C01"),
        "vo__delivery": choice("voice_over"),
    }
    _patch(monkeypatch, answers)
    result = attribute_speakers.attribute_speakers(segments, CAST, scenes={1: "大厅"})
    by_id = {a["id"]: a for a in result["attributions"]}
    assert by_id["spoken"]["delivery"] == "spoken"
    assert by_id["vo"]["delivery"] == "voice_over"
    assert all(a["needs_review"] is False for a in result["attributions"])


def test_uncertain_delivery_forces_review_even_when_the_speaker_is_certain(monkeypatch):
    segments = [{"id": "l1", "scene": 1, "text": "这种情况，我们应该当然是陪伴啊。"}]
    answers = {
        "l1": choice("C08", 0.95),
        "l1__delivery": choice("voice_over", 0.28, {"voice_over": 0.4}),
    }
    _patch(monkeypatch, answers)
    item = attribute_speakers.attribute_speakers(segments, CAST, scenes={1: "齐声应和"})[
        "attributions"
    ][0]
    assert item["needs_review"] is True
    assert item["speaker_needs_review"] is False
    assert item["delivery_needs_review"] is True


def test_delivery_question_is_batched_with_the_speaker_question(monkeypatch):
    """Delivery is independent of speaker, so it costs no extra round trip."""
    segments = [{"id": "l1", "scene": 1, "text": "x"}]
    answers = {"l1": choice("C01"), "l1__delivery": choice("spoken")}
    captured = _patch(monkeypatch, answers)
    attribute_speakers.attribute_speakers(segments, CAST)
    asked = set(captured["client"].captured["questions"])
    assert asked == {"l1", "l1__delivery"}


def test_noncontiguous_scene_reuse_keeps_every_dialogue_line(monkeypatch):
    segments = [
        {"id": "a", "scene": 1, "text": "first"},
        {"id": "b", "scene": 2, "text": "second"},
        {"id": "c", "scene": 1, "text": "third"},
    ]
    answers = {}
    for qid in ("a", "b", "c"):
        answers[qid] = choice("C01")
        answers[f"{qid}__delivery"] = choice("spoken")
    client = Client(Response(answers))
    monkeypatch.setattr(attribute_speakers, "open_client", lambda _key: client)
    result = attribute_speakers.attribute_speakers(segments, CAST, api_key="test-key")

    assert [item["id"] for item in result["attributions"]] == ["a", "b", "c"]
    assert [scene["scene"] for scene in client.captured["state"]["scenes"]] == [1, 2, 1]


# ---------------------------------------------------------------------------
# Dialogue-derived roster (no cast document yet)
# ---------------------------------------------------------------------------

FERRY_LINES = [
    {"text": "外公，雾这么大还开船吗？"},
    {"text": "我叫沈知微，要去对岸。"},
    {"text": "妈，外公说今天不开船。"},
    {"text": "你个死骗子！老夫夜观天象，算命不收钱。"},
]


def test_derived_roles_come_from_the_dialogue_not_a_sample_story():
    found = attribute_speakers._address_roles(" ".join(s["text"] for s in FERRY_LINES))
    assert set(found) == {"外公", "母亲"}
    assert attribute_speakers._self_introduced_names("我叫沈知微，要去对岸。") == ["沈知微"]
    # Not self-introductions: an imperative, and a name running into more text.
    assert attribute_speakers._self_introduced_names("我叫你别去！") == []
    assert attribute_speakers._self_introduced_names("我叫小明今年十岁") == []
    # Longer kinship terms are not double counted.
    assert set(attribute_speakers._address_roles("我的好外孙女")) == {"外孙女"}


def test_derived_cast_without_jev_merges_nothing():
    """Offline there is no evidence two roles are one person, so none are merged."""
    segments = [
        {"text": "宝贝懂了吧？男人靠不住啊。"},
        {"text": "妈，你算命的钱还是爸爸出的呢！"},
        {"text": "老婆，我升官了！"},
        {"text": "我叫林黛玉，是林氏集团的继承人。"},
        {"text": "我的好孙女，来见见表妹。"},
    ]
    cast = attribute_speakers.derived_cast(segments, api_key="")
    names = [c["name"] for c in cast["characters"]]
    assert cast["identity_resolution"]["status"] == "unavailable"
    assert cast["identity_resolution"]["merged"] == []
    assert {"林黛玉", "母亲", "父亲", "妻子", "孙女", "表妹"} <= set(names)
    assert not any("/" in name for name in names)
    # No story-specific roles are fabricated.
    assert not {"算命老者", "死骗子", "孩子", "老者"} & set(names)
    mother = next(c for c in cast["characters"] if c["name"] == "母亲")
    assert "妈" in mother["aliases"]


def test_derived_cast_merges_only_confident_jev_pairs_with_complete_linkage(monkeypatch):
    segments = [
        {"text": "妈，外公说今天不开船。"},
        {"text": "老婆，船票我买好了。"},
        {"text": "姐姐，你也去对岸？"},
    ]
    # Roles in order: 母亲(0), 妻子(1), 外公(2), 姐姐(3)
    names = list(attribute_speakers._address_roles(" ".join(s["text"] for s in segments)))
    index = {name: i for i, name in enumerate(names)}

    def qid(a, b):
        i, j = sorted((index[a], index[b]))
        return f"same__{i}__{j}"

    answers = {
        qid("母亲", "妻子"): Answer(noul=0.9),   # confident: merge
        qid("妻子", "姐姐"): Answer(noul=0.8),   # confident, but...
        qid("母亲", "姐姐"): Answer(noul=0.1),   # ...not with 母亲: no chain merge
        qid("母亲", "外公"): Answer(noul=0.5),   # ambiguous: reported only
        qid("妻子", "外公"): Answer(noul=0.05),
        qid("外公", "姐姐"): Answer(noul=0.05),
    }
    monkeypatch.setattr(
        attribute_speakers, "open_client", lambda _k, **_kw: Client(Response(answers))
    )
    cast = attribute_speakers.derived_cast(segments, api_key="test-key")

    resolution = cast["identity_resolution"]
    assert resolution["status"] == "judged"
    assert resolution["merged"] == [["母亲", "妻子"]]
    assert {"roles": ["母亲", "外公"], "p_same": 0.5} in resolution["review"]
    names = [c["name"] for c in cast["characters"]]
    assert "母亲/妻子" in names and "姐姐" in names and "外公" in names


def test_identity_questions_are_bounded_for_many_roles(monkeypatch):
    """Pairs grow quadratically; 32 roles must not become 496 questions."""
    names = [f"{s}{g}" for s in "赵钱孙李周吴郑王冯陈褚卫蒋沈韩杨" for g in "甲乙"]
    segments = [{"text": f"我叫{name}。"} for name in names]
    # The most mentioned roles are the ones compared.
    segments += [{"text": f"{names[-1]}，你好。"}, {"text": f"{names[-1]}来了。"}]
    calls = []

    class Recording(Client):
        def system_one(self, **kwargs):
            calls.append(kwargs)
            return Response({qid: Answer(noul=0.1) for qid in kwargs["questions"]})

    monkeypatch.setattr(attribute_speakers, "open_client", lambda _k, **_kw: Recording(None))
    cast = attribute_speakers.derived_cast(segments, api_key="test-key")

    limit = attribute_speakers._MAX_MERGE_ROLES
    assert len(calls) == 1
    assert len(calls[0]["questions"]) == limit * (limit - 1) // 2
    resolution = cast["identity_resolution"]
    assert resolution["status"] == "partial"
    assert len(resolution["unasked_roles"]) == len(names) - limit
    assert names[-1] not in resolution["unasked_roles"]
    # Roles that were never compared stay separate people.
    character_names = [c["name"] for c in cast["characters"]]
    for name in resolution["unasked_roles"]:
        assert name in character_names


def test_malformed_same_person_answers_merge_nothing(monkeypatch):
    answers = {"same__0__1": Answer(noul="yes"), "same__0__2": Answer(noul=1.7)}
    monkeypatch.setattr(
        attribute_speakers, "open_client", lambda _k, **_kw: Client(Response(answers))
    )
    cast = attribute_speakers.derived_cast(
        [{"text": "妈，你来了。"}, {"text": "老婆，吃饭了。"}, {"text": "爸，走吧。"}],
        api_key="test-key",
    )
    resolution = cast["identity_resolution"]
    assert resolution["merged"] == []
    assert sorted(r["reason"] for r in resolution["review"]) == [
        "malformed_answer",
        "malformed_answer",
        "missing_answer",
    ]


@pytest.mark.parametrize("value", [math.nan, math.inf, True, -0.1])
def test_non_finite_or_out_of_range_same_person_answers_never_merge(monkeypatch, value):
    answers = {"same__0__1": Answer(noul=value)}
    monkeypatch.setattr(
        attribute_speakers, "open_client", lambda _k, **_kw: Client(Response(answers))
    )
    cast = attribute_speakers.derived_cast(
        [{"text": "妈，你来了。"}, {"text": "老婆，吃饭了。"}], api_key="test-key"
    )
    resolution = cast["identity_resolution"]
    assert resolution["merged"] == []
    assert resolution["review"] == [{"roles": ["母亲", "妻子"], "reason": "malformed_answer"}]


def test_derived_cast_jev_failure_merges_nothing(monkeypatch):
    class Broken(Client):
        def system_one(self, **_kwargs):
            raise RuntimeError("connection reset")

    monkeypatch.setattr(attribute_speakers, "open_client", lambda _k, **_kw: Broken(None))
    cast = attribute_speakers.derived_cast(
        [{"text": "妈，你来了。"}, {"text": "老婆，吃饭了。"}], api_key="test-key"
    )
    assert cast["identity_resolution"]["status"] == "failed"
    assert {"母亲", "妻子"} <= {c["name"] for c in cast["characters"]}


def test_derived_vocative_excludes_the_addressed_role():
    cast = attribute_speakers.derived_cast([{"text": "妈，你算命的钱！"}], api_key="")
    roster = attribute_speakers._roster(cast)
    terms = attribute_speakers._address_terms(cast)
    mother = next(c["id"] for c in cast["characters"] if c["name"] == "母亲")
    conditioned = attribute_speakers._conditions({"text": "妈，你算命的钱！"}, roster, terms=terms)
    assert "本句直接以「妈」称呼此人" in conditioned[mother]


def test_attribution_reports_how_derived_identity_was_resolved(monkeypatch):
    segments = [{"id": "a", "text": "妈，开船了。"}]
    answers = {"a": choice("R01"), "a__delivery": choice("spoken")}
    _patch(monkeypatch, answers)
    result = attribute_speakers.attribute_speakers(segments, None)
    assert result["identity_resolution"]["status"] == "not_needed"
    assert result["attributions"][0]["speaker_name"] == "母亲"
