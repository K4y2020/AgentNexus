"""Tests for JEV film analysis review and scoring."""

import json
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


def test_missing_project_returns_status(tmp_path):
    res = review_film_analysis(tmp_path / "nope")
    assert res["status"] == "missing_project"


def test_film_review_calculates_scores_and_verdict(tmp_path, monkeypatch):
    project = tmp_path / "ep01"
    project.mkdir()
    (project / "project.json").write_text(json.dumps({"current_revision": "r1"}), encoding="utf-8")
    rev_dir = project / "revisions" / "r1"
    rev_dir.mkdir(parents=True)
    story_dir = project / "story"
    story_dir.mkdir()
    (story_dir / "r1.json").write_text(
        json.dumps({
            "summary": {"premise": "x"},
            "characters": ["林黛玉"],
            "sections": [{"batch_id": "B001", "events": ["event 1"]}],
        }),
        encoding="utf-8",
    )

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
    assert (rev_dir / "film_review.json").is_file()


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
