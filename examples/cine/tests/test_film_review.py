"""Tests for JEV film analysis review and scoring."""

import json
import math
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "skills/film-analysis/pipeline"))

from film_review import review_film_analysis


class Answer:
    def __init__(self, **values):
        self.__dict__.update(values)

    def model_dump(self, mode="json"):
        return dict(self.__dict__)


class Response:
    model = "jev-test"

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


SOURCE = {
    "source_id": "src",
    "size_bytes": 1024,
    "sha256_head": "c" * 64,
    "sha256_tail": "d" * 64,
}
FINGERPRINT = {k: SOURCE[k] for k in ("size_bytes", "sha256_head", "sha256_tail")}
BATCHES = ("B001", "B002")


def test_missing_project_returns_status(tmp_path):
    res = review_film_analysis(tmp_path / "nope")
    assert res["status"] == "missing_project"


def test_film_review_calculates_scores_and_verdict(tmp_path, monkeypatch):
    """A draft that fills every documented field and plan batch can be ready."""
    project = _project(tmp_path)
    rev_dir = project / "revisions" / "r1"

    answers = {
        "dialogue_fidelity": Answer(type="score", score=1.8, confidence=0.9),
        "character_coherence": Answer(type="score", score=1.9, confidence=0.85),
        "story_causality": Answer(type="score", score=1.8, confidence=0.88),
        "adaptation_readiness": Answer(type="score", score=1.9, confidence=0.92),
        "dialogue_inversion_detected": Answer(type="noul", noul=0.1),
    }
    monkeypatch.setattr("film_review.open_client", lambda _k, **_kw: Client(Response(answers)))
    monkeypatch.setattr("film_review._find_api_key", lambda: "mock-key")

    report = review_film_analysis(project)
    assert report["status"] == "reviewed"
    assert report["verdict"] == "ready"
    assert report["total_score"] >= 85.0
    assert report["story_completeness"] == {"complete": True, "gaps": []}
    assert (rev_dir / "film_review.json").is_file()


def test_skeleton_draft_is_not_ready_even_with_high_scores(tmp_path, monkeypatch):
    """The indexer's empty skeleton must not become ready because JEV scored high."""
    project = _project(tmp_path, draft=_skeleton_draft())
    monkeypatch.setattr(
        "film_review.open_client", lambda _k, **_kw: Client(Response(_answers(scores=2.0)))
    )
    report = review_film_analysis(project, api_key="test-key")

    assert report["total_score"] == 100.0          # diagnostics are still reported
    assert report["verdict"] == "needs_repair"
    assert report["story_completeness"]["complete"] is False
    gaps = report["story_completeness"]["gaps"]
    expected = {"summary_premise_missing", "characters_required", "section_incomplete:B001"}
    assert expected <= set(gaps)
    assert any(f["category"] == "story_incomplete" for f in report["findings"])


def test_draft_missing_a_plan_batch_is_not_ready(tmp_path, monkeypatch):
    draft = _complete_draft()
    draft["sections"].pop()                        # the ending batch was never read
    project = _project(tmp_path, draft=draft)
    monkeypatch.setattr(
        "film_review.open_client", lambda _k, **_kw: Client(Response(_answers(scores=2.0)))
    )
    report = review_film_analysis(project, api_key="test-key")
    assert report["verdict"] != "ready"
    assert report["story_completeness"]["gaps"] == ["section_missing:B002"]


@pytest.mark.parametrize(
    ("fingerprint", "expected"),
    [
        ({**FINGERPRINT, "sha256_tail": "e" * 64}, "source_mismatch"),
        (None, "unverified_source"),
    ],
)
def test_transcript_of_another_source_is_not_scored(tmp_path, monkeypatch, fingerprint, expected):
    transcript = {
        "source_media": "source.mp4",                # same basename, different video
        "segments": [{"speaker": "阿明", "text": "别家的台词", "needs_review": True}],
    }
    project = _project(tmp_path, transcript=transcript, fingerprint=fingerprint)
    client = Client(Response(_answers()))
    monkeypatch.setattr("film_review.open_client", lambda _k, **_kw: client)
    report = review_film_analysis(project, api_key="test-key")

    assert client.captured["state"]["transcript_lines"] == []
    assert report["transcript_status"] == expected
    assert report["verdict"] == "usable_with_risks"
    assert any(f["category"] == "transcript_source_mismatch" for f in report["findings"])
    # Its review flags are not counted as this film's either.
    assert not any(f["category"] == "attribution_unsettled" for f in report["findings"])


def test_dialogue_inversion_forces_penalty(tmp_path, monkeypatch):
    project = tmp_path / "ep01"
    project.mkdir()
    (project / "project.json").write_text(json.dumps({"current_revision": "r1"}), encoding="utf-8")
    (project / "revisions" / "r1").mkdir(parents=True)
    (project / "story").mkdir()
    (project / "story" / "r1.json").write_text(
        json.dumps({
            "summary": {"premise": "x"},
            "characters": ["林黛玉"],
            "sections": [{"batch_id": "B001", "events": ["event 1"]}],
        }),
        encoding="utf-8",
    )

    answers = {
        "dialogue_fidelity": Answer(type="score", score=1.0, confidence=0.9),
        "character_coherence": Answer(type="score", score=1.5, confidence=0.85),
        "story_causality": Answer(type="score", score=1.5, confidence=0.88),
        "adaptation_readiness": Answer(type="score", score=1.0, confidence=0.92),
        "dialogue_inversion_detected": Answer(type="noul", noul=0.85),
    }
    monkeypatch.setattr("film_review.open_client", lambda _k, **_kw: Client(Response(answers)))
    monkeypatch.setattr("film_review._find_api_key", lambda: "mock-key")

    report = review_film_analysis(project)
    assert report["verdict"] == "needs_repair"
    assert report["total_score"] <= 65.0
    assert any(f["category"] == "dialogue_inversion" for f in report["findings"])


def _complete_draft(characters=("老周",)):
    return {
        "source_id": "src",
        "revision_id": "r1",
        "dialogue_provenance": {"status": "unverified", "source_path": None},
        "characters": list(characters),
        "summary": {
            "premise": "雾天渡口，老船夫照常开船",
            "conflict": "雾越来越厚",
            "turning_points": ["船到江心"],
            "ending": "船靠岸",
        },
        "sections": [
            {
                "batch_id": batch,
                "events": [f"{batch} 发生的事"],
                "connection": "承接上一段",
                "image_receipt_ids": [],
                "uncertainties": [],
            }
            for batch in BATCHES
        ],
    }


def _skeleton_draft():
    """What entry.index_media writes before anyone reads the film."""
    return {
        "schema_version": 1,
        "source_id": "src",
        "revision_id": "r1",
        "dialogue_provenance": {"status": "unverified", "source_path": None},
        "characters": [],
        "summary": {"premise": "", "conflict": "", "turning_points": [], "ending": ""},
        "sections": [
            {
                "batch_id": batch,
                "events": [],
                "connection": "",
                "image_receipt_ids": [],
                "uncertainties": [],
            }
            for batch in BATCHES
        ],
    }


def _project(tmp_path, *, characters=("老周",), transcript=None, draft=None, fingerprint=True):
    project = tmp_path / "ws" / "projects" / "ep01"
    (project / "revisions" / "r1").mkdir(parents=True)
    (project / "story").mkdir()
    (project / "project.json").write_text(json.dumps({"current_revision": "r1"}), encoding="utf-8")
    (project / "source.json").write_text(json.dumps(SOURCE), encoding="utf-8")
    (project / "revisions" / "r1" / "story_plan.json").write_text(
        json.dumps(
            {
                "source_id": "src",
                "revision_id": "r1",
                "batches": [{"batch_id": batch} for batch in BATCHES],
            }
        ),
        encoding="utf-8",
    )
    (project / "story" / "r1.json").write_text(
        json.dumps(draft or _complete_draft(characters), ensure_ascii=False), encoding="utf-8"
    )
    if transcript is not None:
        inputs = tmp_path / "ws" / "inputs"
        inputs.mkdir(parents=True)
        record = {"kind": "qualified_asr_transcript", **transcript}
        if fingerprint is True:
            record.setdefault("source_fingerprint", FINGERPRINT)
        elif fingerprint:
            record["source_fingerprint"] = fingerprint
        (inputs / "source-transcript.json").write_text(
            json.dumps(record, ensure_ascii=False), encoding="utf-8"
        )
    return project


def _answers(scores=1.9, confidence=0.9, inversion=0.05):
    answers = {
        name: Answer(score=scores, confidence=confidence)
        for name in (
            "dialogue_fidelity",
            "character_coherence",
            "story_causality",
            "adaptation_readiness",
        )
    }
    answers["dialogue_inversion_detected"] = Answer(noul=inversion)
    return answers


def test_review_questions_quote_no_sample_story(tmp_path, monkeypatch):
    project = _project(tmp_path)
    client = Client(Response(_answers()))
    monkeypatch.setattr("film_review.open_client", lambda _k, **_kw: client)
    report = review_film_analysis(project, api_key="test-key")

    text = json.dumps(
        {
            qid: {"instructions": q.instructions, "criteria": getattr(q, "criteria", None)}
            for qid, q in client.captured["questions"].items()
        },
        ensure_ascii=False,
    )
    for sample in ("王熙凤", "林黛玉", "黛玉", "嫂子", "我爸姓林"):
        assert sample not in text
    findings = json.dumps(report["findings"], ensure_ascii=False)
    assert all(sample not in findings for sample in ("嫂子", "我爸姓林"))


def test_unsure_scores_cannot_make_a_ready_verdict(tmp_path, monkeypatch):
    project = _project(tmp_path)
    monkeypatch.setattr(
        "film_review.open_client", lambda _k, **_kw: Client(Response(_answers(confidence=0.3)))
    )
    report = review_film_analysis(project, api_key="test-key")
    assert report["total_score"] >= 75.0
    assert report["verdict"] == "usable_with_risks"
    assert any(f["category"] == "low_confidence" for f in report["findings"])


def test_unsettled_attributions_are_marked_for_the_judge(tmp_path, monkeypatch):
    transcript = {
        "segments": [
            {"speaker": "老周", "text": "开船咯。"},
            {"speaker": "unclear", "text": "等等我！", "needs_review": True},
        ]
    }
    project = _project(tmp_path, transcript=transcript)
    client = Client(Response(_answers()))
    monkeypatch.setattr("film_review.open_client", lambda _k, **_kw: client)
    report = review_film_analysis(project, api_key="test-key")

    state = client.captured["state"]
    assert state["transcript_lines"] == ["[老周] 开船咯。", "[unclear（待复核）] 等等我！"]
    assert report["transcript_status"] == "verified"
    assert report["story_completeness"]["complete"] is True
    assert any(f["category"] == "attribution_unsettled" for f in report["findings"])
    # A complete draft with high scores is still not ready while an
    # attribution in its own source transcript is unresolved.
    assert report["total_score"] >= 75.0
    assert report["verdict"] == "usable_with_risks"


def test_dict_characters_do_not_crash_and_are_reported_as_a_gap(tmp_path, monkeypatch):
    project = _project(tmp_path, characters=[{"name": "老周"}])
    client = Client(Response(_answers()))
    monkeypatch.setattr("film_review.open_client", lambda _k, **_kw: client)
    report = review_film_analysis(project, api_key="test-key")
    assert "老周" in client.captured["state"]["characters"][0]
    assert "characters_required" in report["story_completeness"]["gaps"]
    assert report["verdict"] != "ready"


def _strict(path):
    """Parse JSON the way a strict consumer would: NaN/Infinity are errors."""

    def reject(token):
        raise ValueError(f"non-standard JSON constant {token}")

    return json.loads(path.read_text(encoding="utf-8"), parse_constant=reject)


@pytest.mark.parametrize("confidence", [math.nan, math.inf, 1.5, -0.1, True, "0.9"])
def test_malformed_confidence_is_unknown_and_blocks_ready(tmp_path, monkeypatch, confidence):
    project = _project(tmp_path)
    answers = _answers()
    answers["story_causality"] = Answer(score=1.9, confidence=confidence)
    monkeypatch.setattr("film_review.open_client", lambda _k, **_kw: Client(Response(answers)))
    report = review_film_analysis(project, api_key="test-key")

    assert report["total_score"] >= 75.0
    assert report["verdict"] == "usable_with_risks"
    assert report["scores"]["story_causality"]["confidence"] is None
    low = next(f for f in report["findings"] if f["category"] == "low_confidence")
    assert "story_causality（置信度无效）" in low["message"]
    assert _strict(project / "film_review.json")["verdict"] == "usable_with_risks"


@pytest.mark.parametrize("score", [math.nan, math.inf, 2.5, -1.0, True, "2"])
def test_malformed_score_fails_closed_without_writing_a_report(tmp_path, monkeypatch, score):
    project = _project(tmp_path)
    answers = _answers()
    answers["dialogue_fidelity"] = Answer(score=score, confidence=0.9)
    monkeypatch.setattr("film_review.open_client", lambda _k, **_kw: Client(Response(answers)))
    with pytest.raises(ValueError, match="malformed score for dialogue_fidelity"):
        review_film_analysis(project, api_key="test-key")
    assert not (project / "film_review.json").exists()
    assert not (project / "revisions" / "r1" / "film_review.json").exists()


@pytest.mark.parametrize("inversion", [math.nan, 1.5, True])
def test_malformed_inversion_fails_closed(tmp_path, monkeypatch, inversion):
    project = _project(tmp_path)
    monkeypatch.setattr(
        "film_review.open_client",
        lambda _k, **_kw: Client(Response(_answers(inversion=inversion))),
    )
    with pytest.raises(ValueError, match="malformed dialogue_inversion_detected"):
        review_film_analysis(project, api_key="test-key")
    assert not (project / "film_review.json").exists()


def test_findings_are_not_duplicated(tmp_path, monkeypatch):
    project = _project(tmp_path)
    monkeypatch.setattr(
        "film_review.open_client", lambda _k, **_kw: Client(Response(_answers(scores=0.4)))
    )
    report = review_film_analysis(project, api_key="test-key")
    assert report["verdict"] == "blocked"
    keys = [(f["category"], f["message"]) for f in report["findings"]]
    assert len(keys) == len(set(keys))


def test_zero_dialogue_score_is_not_promoted_to_ready(tmp_path, monkeypatch):
    project = tmp_path / "ep01"
    (project / "revisions" / "r1").mkdir(parents=True)
    (project / "story").mkdir()
    (project / "project.json").write_text(json.dumps({"current_revision": "r1"}), encoding="utf-8")
    (project / "story" / "r1.json").write_text(
        json.dumps({"summary": {}, "characters": [], "sections": []}), encoding="utf-8"
    )
    answers = {
        name: Answer(score=0.0 if name == "dialogue_fidelity" else 2.0, confidence=0.9)
        for name in (
            "dialogue_fidelity",
            "character_coherence",
            "story_causality",
            "adaptation_readiness",
        )
    }
    answers["dialogue_inversion_detected"] = Answer(noul=0.0)
    monkeypatch.setattr("film_review.open_client", lambda _key: Client(Response(answers)))

    report = review_film_analysis(project, api_key="test-key")
    assert report["scores"]["dialogue_fidelity"]["score"] == 0.0
    assert report["verdict"] == "needs_repair"
    assert any(
        f["category"] == "dialogue_fidelity" and f["severity"] == "high"
        for f in report["findings"]
    )
