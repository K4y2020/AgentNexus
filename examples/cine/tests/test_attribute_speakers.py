"""Derived identities and shared JEV parsing remain independent of speaker filling."""

import math
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "skills/film-analysis"))
from pipeline import attribute_speakers, jev_client


class Answer:
    def __init__(self, **values):
        self.__dict__.update(values)


class Response:
    model = "jev-test-1"

    def __init__(self, answers):
        self.answers = answers


class Client:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def system_one(self, **kwargs):
        self.calls.append(kwargs)
        return self.response


FERRY_LINES = [
    {"text": "外公，雾这么大还开船吗？"},
    {"text": "我叫沈知微，要去对岸。"},
    {"text": "妈，外公说今天不开船。"},
    {"text": "请先到值班室登记。"},
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
        qid("母亲", "妻子"): Answer(noul=0.9),  # confident: merge
        qid("妻子", "姐姐"): Answer(noul=0.8),  # confident, but...
        qid("母亲", "姐姐"): Answer(noul=0.1),  # ...not with 母亲: no chain merge
        qid("母亲", "外公"): Answer(noul=0.5),  # ambiguous: reported only
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


def test_derived_cast_offline_keeps_all_candidate_identities_separate():
    result = attribute_speakers.derived_cast(
        [
            {"text": "我叫安然。"},
            {"text": "妈，爸已经到码头了。"},
            {"text": "老婆，请取一下船票。"},
        ],
        api_key="",
    )
    assert result["identity_resolution"]["status"] == "unavailable"
    assert {"安然", "母亲", "父亲", "妻子"} <= {c["name"] for c in result["characters"]}
    assert not result["identity_resolution"]["merged"]


@pytest.mark.parametrize("confidence", [math.nan, math.inf, -math.inf, 1.5, -0.2, True, "0.9"])
def test_shared_choice_rejects_malformed_confidence(confidence):
    value = Answer(choice="ready", confidence=confidence, probabilities={"ready": 0.9})
    choice, score, _p, problem = jev_client._choice(value, {"ready"})
    assert (choice, score, problem) == ("unclear", 0.0, "malformed_answer")


@pytest.mark.parametrize(
    "probabilities",
    [{"ready": math.nan}, {"ready": 1.2}, {"ready": 0.9, "wait": math.inf}, ["ready"]],
)
def test_shared_choice_rejects_malformed_probabilities(probabilities):
    value = Answer(choice="ready", confidence=0.9, probabilities=probabilities)
    assert jev_client._choice(value, {"ready"})[3] == "malformed_answer"


def test_shared_choice_allowed_values_and_existing_margin():
    assert jev_client._choice(None, {"ready"})[3] == "missing_answer"
    assert (
        jev_client._choice(Answer(choice="unknown", confidence=0.9), {"ready"})[3]
        == "unknown_option:unknown"
    )
    value = Answer(choice="ready", confidence=0.9, probabilities={"ready": 0.9, "wait": 0.1})
    assert jev_client._choice(value, {"ready", "wait"})[:2] == ("ready", 0.9)
    assert jev_client._ambiguous("ready", {"ready": 0.6, "wait": 0.55})
    assert not jev_client._ambiguous("ready", {"ready": 0.9, "wait": 0.1})


def test_shared_question_batches_observe_the_existing_state_budget(monkeypatch):
    monkeypatch.setattr(jev_client, "STATE_TOKEN_BUDGET", 100)
    monkeypatch.setattr(jev_client, "_estimate_value_tokens", lambda _value: 40)
    client = Client(Response({"ok": SimpleNamespace(choice="ready")}))
    question = SimpleNamespace(model_dump=lambda **_kwargs: {"instructions": "x"})
    result = jev_client._ask_batched(
        client, {}, {"a": question, "b": question, "c": question}, "jev"
    )
    assert [list(c["questions"]) for c in client.calls] == [["a"], ["b"], ["c"]]
    assert result.model == "jev-test-1"


def test_wrapper_without_visual_evidence_requests_no_model(monkeypatch):
    monkeypatch.setattr(
        attribute_speakers, "open_client", lambda *_a: pytest.fail("no text attribution")
    )
    result = attribute_speakers.attribute_speakers(
        [{"text": "请稍候。", "start": 0, "end": 1}], None
    )
    assert result["attributions"][0]["speaker"] == "unclear"
    assert result["attributions"][0]["needs_review"]
