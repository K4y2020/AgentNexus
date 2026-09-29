"""Workspace transcript discovery and fail-closed script attribution."""

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "skills/film-analysis"))
from pipeline import attribute_speakers, production


def _write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def test_attribution_reads_workspace_inputs(monkeypatch, tmp_path):
    directory = tmp_path / "production"
    directory.mkdir()
    _write(directory / "cast.json", {"characters": [{"id": "C01", "name": "A"}]})
    _write(
        tmp_path / "inputs" / "source-transcript.json", {"segments": [{"id": "a", "text": "Hi"}]}
    )
    monkeypatch.setattr(
        attribute_speakers,
        "attribute_speakers",
        lambda _segments, _cast, **_kwargs: {
            "model": "mock-jev",
            "attributions": [{"id": "a", "needs_review": False}],
        },
    )

    result = production.run_attribution(directory)
    saved = json.loads(
        (directory / "source-transcript-attributed.json").read_text(encoding="utf-8")
    )
    assert result["status"] == "attributed"
    assert saved["input_sha256"]["source_transcript"] == production.digest(
        tmp_path / "inputs" / "source-transcript.json"
    )


def test_script_check_does_not_use_stale_attribution(monkeypatch, tmp_path):
    directory = tmp_path / "production"
    documents = {
        "outline": {"source": "x", "characters": [], "scenes": [], "beats": [], "episodes": []},
        "cast": {"source": "x", "characters": []},
        "art": {"source": "x", "scenes": []},
        "script": {"source": "x", "episodes": []},
    }
    for stage, document in documents.items():
        _write(directory / f"{stage}.json", document)
    _write(
        directory / "production.json",
        {
            "schema_version": 1,
            "artifacts": {stage: {"path": f"{stage}.json"} for stage in documents},
        },
    )
    _write(tmp_path / "inputs" / "source-transcript.json", {"segments": []})
    _write(directory / "source-transcript-attributed.json", {"input_sha256": {"cast": "old"}})
    monkeypatch.setattr(production.shutil, "which", lambda _name: "node")
    monkeypatch.setattr(
        production,
        "run_attribution",
        lambda *_args, **_kwargs: {"status": "error", "error": "mock JEV failure"},
    )
    monkeypatch.setattr(
        production.subprocess,
        "run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("native validator must not run")
        ),
    )

    result = production.check_stage(directory, "script")
    assert result["status"] == "attribution_error"
    assert "mock JEV failure" in result["stderr"]


def test_script_without_transcript_keeps_text_workflow_available(monkeypatch, tmp_path):
    directory = tmp_path / "production"
    documents = {
        "outline": {"source": "x", "characters": [], "scenes": [], "beats": [], "episodes": []},
        "cast": {"source": "x", "characters": []},
        "art": {"source": "x", "scenes": []},
        "script": {"source": "x", "episodes": []},
    }
    for stage, document in documents.items():
        _write(directory / f"{stage}.json", document)
    _write(
        directory / "production.json",
        {
            "schema_version": 1,
            "artifacts": {stage: {"path": f"{stage}.json"} for stage in documents},
        },
    )
    source = tmp_path / "source.txt"
    source.write_text("text source", encoding="utf-8")
    monkeypatch.setattr(production.shutil, "which", lambda _name: "node")
    monkeypatch.setattr(
        production.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=0, stdout="", stderr=""),
    )

    result = production.check_stage(directory, "script", source_text=source)
    assert result["status"] == "passed"
    assert "--source" not in result["command"]


# ---------------------------------------------------------------------------
# The transcript must be the bound film's (source_material fingerprint)
# ---------------------------------------------------------------------------

FILM = {"size_bytes": 10, "sha256_head": "a" * 64, "sha256_tail": "b" * 64}
OTHER_FILM = {**FILM, "sha256_tail": "c" * 64}
SEGMENTS = [{"id": "a", "start": 0.0, "end": 1.0, "text": "Hi"}]


def _production(tmp_path, *, mode="adaptation", material=None, transcript=None, pin=None):
    """A production; ``pin="actual"`` pins the material's real sha256, a string pins that."""
    directory = tmp_path / "production"
    documents = {
        "outline": {"source": "x", "characters": [], "scenes": [], "beats": [], "episodes": []},
        "cast": {"source": "x", "characters": [{"id": "C01", "name": "A"}]},
        "art": {"source": "x", "scenes": []},
        "script": {"source": "x", "episodes": []},
    }
    for stage, document in documents.items():
        _write(directory / f"{stage}.json", document)
    artifacts = {stage: {"path": f"{stage}.json"} for stage in documents}
    if material is not None:
        _write(directory / "source-material.json", material)
        artifacts["source_material"] = {"path": "source-material.json"}
        if pin is not None:
            artifacts["source_material"]["sha256"] = (
                production.digest(directory / "source-material.json") if pin == "actual" else pin
            )
    _write(
        directory / "production.json",
        {"schema_version": 1, "mode": mode, "artifacts": artifacts},
    )
    if transcript is not None:
        _write(tmp_path / "inputs" / "source-transcript.json", transcript)
    return directory


def _material(fingerprint=FILM):
    material = {"source_id": "film", "revision_id": "r1", "shots": []}
    if fingerprint is not None:
        material["source_fingerprint"] = fingerprint
    return material


def _transcript(fingerprint):
    transcript = {"kind": "qualified_asr_transcript", "segments": SEGMENTS}
    if fingerprint is not None:
        transcript["source_fingerprint"] = fingerprint
    return transcript


def _no_jev(monkeypatch):
    def refuse(*_args, **_kwargs):
        raise AssertionError("a transcript of another film must not reach JEV")

    monkeypatch.setattr(attribute_speakers, "attribute_speakers", refuse)


def _stub_jev(monkeypatch):
    calls = []

    def fake(segments, _cast, **_kwargs):
        calls.append(segments)
        return {
            "model": "mock-jev",
            "attributions": [{"id": s["id"], "needs_review": False} for s in segments],
        }

    monkeypatch.setattr(attribute_speakers, "attribute_speakers", fake)
    return calls


def _no_validator(monkeypatch):
    monkeypatch.setattr(production.shutil, "which", lambda _name: "node")

    def refuse(*_args, **_kwargs):
        raise AssertionError("the script validator must not see another film's transcript")

    monkeypatch.setattr(production.subprocess, "run", refuse)


@pytest.mark.parametrize(
    ("transcript_fingerprint", "identity", "phrase"),
    [
        (OTHER_FILM, "source_mismatch", "different video"),
        (None, "transcript_unverified", "no source fingerprint"),
    ],
    ids=["other_film", "legacy_transcript"],
)
def test_bound_production_refuses_a_transcript_that_is_not_its_film(
    monkeypatch, tmp_path, transcript_fingerprint, identity, phrase
):
    directory = _production(
        tmp_path, material=_material(), transcript=_transcript(transcript_fingerprint)
    )
    _no_jev(monkeypatch)
    _no_validator(monkeypatch)

    attribution = production.run_attribution(directory)
    assert attribution["status"] == "blocked"
    assert attribution["transcript_identity"] == identity
    assert phrase in attribution["reason"]
    assert not (directory / "source-transcript-attributed.json").exists()

    row = production.check_stage(directory, "script")
    assert row["status"] == "attribution_error"
    assert row["transcript_identity"] == identity
    assert phrase in row["stderr"]
    assert str(directory / "source-material.json") in {
        str(Path(p)) for p in row["input_hashes"]
    }


def test_a_cached_attribution_cannot_bypass_the_identity_check(monkeypatch, tmp_path):
    directory = _production(tmp_path, material=_material(), transcript=_transcript(OTHER_FILM))
    rows_path = tmp_path / "inputs" / "source-transcript.json"
    _write(
        directory / "source-transcript-attributed.json",
        {
            "attributions": [],
            "input_sha256": {
                "source_transcript": production.digest(rows_path),
                "cast": production.digest(directory / "cast.json"),
            },
        },
    )
    _no_jev(monkeypatch)
    _no_validator(monkeypatch)
    row = production.check_stage(directory, "script")
    assert row["status"] == "attribution_error"
    assert row["transcript_identity"] == "source_mismatch"


@pytest.mark.parametrize(
    ("pin", "identity"),
    [("actual", "verified"), (None, "verified_unpinned")],
    ids=["pinned", "legacy_manifest_without_pin"],
)
def test_the_bound_films_transcript_is_attributed_and_checked(
    monkeypatch, tmp_path, pin, identity
):
    directory = _production(
        tmp_path, material=_material(), transcript=_transcript(FILM), pin=pin
    )
    calls = _stub_jev(monkeypatch)
    monkeypatch.setattr(production.shutil, "which", lambda _name: "node")
    monkeypatch.setattr(
        production.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=0, stdout="", stderr=""),
    )

    attribution = production.run_attribution(directory)
    assert attribution["status"] == "attributed"
    assert attribution["transcript_identity"] == identity
    assert len(calls) == 1

    row = production.check_stage(directory, "script")
    assert row["status"] == "passed"
    assert row["transcript_identity"] == identity
    assert "--source" in row["command"]


@pytest.mark.parametrize(
    ("mode", "material"),
    [("original", None), ("original", _material()), ("adaptation", None)],
    ids=["original", "original_with_material", "no_source_material"],
)
def test_unbound_productions_still_use_the_transcript(monkeypatch, tmp_path, mode, material):
    """Text-only workflows: no film binding is invented, and the status says so."""
    directory = _production(
        tmp_path, mode=mode, material=material, transcript=_transcript(None)
    )
    calls = _stub_jev(monkeypatch)
    attribution = production.run_attribution(directory)
    assert attribution["status"] == "attributed"
    assert attribution["transcript_identity"] == "unbound"
    assert len(calls) == 1


def _write_cached_attribution(directory, rows_path):
    """An attribution whose input hashes match, so check_stage would reuse it."""
    _write(
        directory / "source-transcript-attributed.json",
        {
            "attributions": [],
            "input_sha256": {
                "source_transcript": production.digest(rows_path),
                "cast": production.digest(directory / "cast.json"),
            },
        },
    )


@pytest.mark.parametrize("transcript_fingerprint", [FILM, None], ids=["fingerprinted", "legacy"])
def test_legacy_material_without_a_fingerprint_fails_closed(
    monkeypatch, tmp_path, transcript_fingerprint
):
    """Bound to a film that cannot be identified: no transcript can be verified."""
    directory = _production(
        tmp_path,
        material=_material(fingerprint=None),
        transcript=_transcript(transcript_fingerprint),
        pin="actual",
    )
    _write_cached_attribution(directory, tmp_path / "inputs" / "source-transcript.json")
    _no_jev(monkeypatch)
    _no_validator(monkeypatch)

    attribution = production.run_attribution(directory)
    assert attribution["status"] == "blocked"
    assert attribution["transcript_identity"] == "material_without_fingerprint"
    # The instruction is a runnable sequence for this production, not a hash edit.
    for step in (
        f"production.py rebind-source {directory} --source-project",
        f"production.py finalize {directory}",
    ):
        assert step in attribution["reason"]

    row = production.check_stage(directory, "script")
    assert row["status"] == "attribution_error"
    assert row["transcript_identity"] == "material_without_fingerprint"
    assert "rebind-source" in row["stderr"]


@pytest.mark.parametrize(
    "tamper",
    ["edit_material_after_pinning", "stale_pin_value"],
)
def test_a_stale_or_edited_material_pin_is_rejected_before_jev_and_cache(
    monkeypatch, tmp_path, tamper
):
    directory = _production(
        tmp_path,
        material=_material(),
        transcript=_transcript(FILM),
        pin="actual" if tamper == "edit_material_after_pinning" else "0" * 64,
    )
    if tamper == "edit_material_after_pinning":
        # Swap the bound film for another after the pins were computed.
        _write(directory / "source-material.json", _material(fingerprint=OTHER_FILM))
        _write(tmp_path / "inputs" / "source-transcript.json", _transcript(OTHER_FILM))
    _write_cached_attribution(directory, tmp_path / "inputs" / "source-transcript.json")
    _no_jev(monkeypatch)
    _no_validator(monkeypatch)

    attribution = production.run_attribution(directory)
    assert attribution["status"] == "blocked"
    assert attribution["transcript_identity"] == "material_pin_mismatch"
    assert "rebind-source" in attribution["reason"]
    assert "production.py finalize" in attribution["reason"]

    row = production.check_stage(directory, "script")
    assert row["status"] == "attribution_error"
    assert row["transcript_identity"] == "material_pin_mismatch"
    assert "stale or was edited" in row["stderr"]


# ---------------------------------------------------------------------------
# rebind-source: the runnable recovery for a legacy or stale film binding
# ---------------------------------------------------------------------------


def _analysis_project(tmp_path, *, source_id="film", revision="r1", fingerprint=FILM):
    """A committed film-analysis project, as handoff.source_material reads it."""
    project = tmp_path / "analysis"
    _write(project / "project.json", {"current_revision": revision})
    _write(project / "source.json", {"source_id": source_id, **(fingerprint or {})})
    _write(project / "revisions" / revision / "source_shots.json", [])
    return project


def _legacy_bound_production(tmp_path, *, pin="actual"):
    """Adaptation production whose pinned material predates fingerprints."""
    return _production(
        tmp_path,
        material=_material(fingerprint=None),
        transcript=_transcript(FILM),
        pin=pin,
    )


def _passing_validator(monkeypatch):
    monkeypatch.setattr(production.shutil, "which", lambda _name: "node")
    monkeypatch.setattr(
        production.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=0, stdout="", stderr=""),
    )


def test_rebind_source_recovers_a_legacy_pinned_material(monkeypatch, tmp_path, capsys):
    directory = _legacy_bound_production(tmp_path)
    analysis = _analysis_project(tmp_path)
    manifest_before = json.loads((directory / "production.json").read_text(encoding="utf-8"))
    outline_before = (directory / "outline.json").read_bytes()
    _no_jev(monkeypatch)
    _no_validator(monkeypatch)
    assert production.check_stage(directory, "script")["transcript_identity"] == (
        "material_without_fingerprint"
    )

    # The documented command, through the real CLI. No JEV or validator runs.
    code = production.main(
        ["rebind-source", str(directory), "--source-project", str(analysis)]
    )
    result = json.loads(capsys.readouterr().out)
    assert code == 0
    assert result["status"] == "source_rebound"
    assert f"production.py finalize {directory}" in result["next_step"]

    material = json.loads((directory / "source-material.json").read_text(encoding="utf-8"))
    assert material["source_fingerprint"] == FILM
    assert (material["source_id"], material["revision_id"]) == ("film", "r1")
    manifest = json.loads((directory / "production.json").read_text(encoding="utf-8"))
    pin = manifest["artifacts"]["source_material"]["sha256"]
    assert pin == production.digest(directory / "source-material.json") == result["sha256"]
    assert result["previous_sha256"] == manifest_before["artifacts"]["source_material"]["sha256"]
    # Only the material pin changed; everything else is left for finalize.
    manifest["artifacts"]["source_material"].pop("sha256")
    manifest_before["artifacts"]["source_material"].pop("sha256")
    assert manifest == manifest_before
    assert (directory / "outline.json").read_bytes() == outline_before

    assert production.bound_source_fingerprint(directory)[1] == "bound"
    _stub_jev(monkeypatch)
    _passing_validator(monkeypatch)
    row = production.check_stage(directory, "script")
    assert row["status"] == "passed"
    assert row["transcript_identity"] == "verified"


def test_rebind_source_repairs_a_stale_pin(monkeypatch, tmp_path):
    directory = _production(
        tmp_path, material=_material(fingerprint=None), transcript=_transcript(FILM), pin="0" * 64
    )
    assert production.bound_source_fingerprint(directory)[1] == "material_pin_mismatch"
    production.rebind_source(directory, _analysis_project(tmp_path))
    _stub_jev(monkeypatch)
    _passing_validator(monkeypatch)
    row = production.check_stage(directory, "script")
    assert row["status"] == "passed"
    assert row["transcript_identity"] == "verified"


def _refusal(tmp_path, case):
    if case == "original":
        return _production(
            tmp_path, mode="original", material=_material(fingerprint=None), pin="actual"
        ), _analysis_project(tmp_path)
    if case == "unbound":
        return _production(tmp_path, transcript=_transcript(FILM)), _analysis_project(tmp_path)
    directory = _legacy_bound_production(tmp_path)
    if case == "other_film":
        return directory, _analysis_project(tmp_path, source_id="another-film")
    if case == "other_revision":
        return directory, _analysis_project(tmp_path, revision="r2")
    return directory, _analysis_project(tmp_path, fingerprint=None)  # no_fingerprint


@pytest.mark.parametrize(
    ("case", "message"),
    [
        ("other_film", "different film or revision needs a new production"),
        ("other_revision", "different film or revision needs a new production"),
        ("no_fingerprint", "records no size/head/tail fingerprint"),
        ("original", "only to faithful/adaptation productions"),
        ("unbound", "no source_material binding"),
    ],
)
def test_rebind_source_refuses_without_writing(tmp_path, case, message):
    directory, analysis = _refusal(tmp_path, case)
    before = {p.name: p.read_bytes() for p in directory.iterdir() if p.is_file()}
    with pytest.raises(ValueError, match=message):
        production.rebind_source(directory, analysis)
    assert {p.name: p.read_bytes() for p in directory.iterdir() if p.is_file()} == before


@pytest.mark.parametrize("pin", ["actual", None], ids=["pinned", "unpinned_manifest"])
def test_an_interrupted_rebind_still_fails_closed(monkeypatch, tmp_path, pin):
    """The pin is written first: a crash before the material lands is a pin mismatch."""
    directory = _legacy_bound_production(tmp_path, pin=pin)

    def crash(*_args, **_kwargs):
        raise OSError("disk full")

    with monkeypatch.context() as patched:
        patched.setattr(production, "_atomic_bytes", crash)
        with pytest.raises(OSError):
            production.rebind_source(directory, _analysis_project(tmp_path))

    _no_jev(monkeypatch)
    _no_validator(monkeypatch)
    row = production.check_stage(directory, "script")
    assert row["status"] == "attribution_error"
    assert row["transcript_identity"] == "material_pin_mismatch"


def test_a_failed_manifest_write_leaves_the_binding_refused(monkeypatch, tmp_path):
    directory = _legacy_bound_production(tmp_path)
    before = (directory / "source-material.json").read_bytes()

    def crash(*_args, **_kwargs):
        raise OSError("manifest locked")

    with monkeypatch.context() as patched:
        patched.setattr(production, "atomic_json", crash)
        with pytest.raises(OSError):
            production.rebind_source(directory, _analysis_project(tmp_path))

    assert (directory / "source-material.json").read_bytes() == before
    _no_jev(monkeypatch)
    _no_validator(monkeypatch)
    row = production.check_stage(directory, "script")
    assert row["status"] == "attribution_error"
    assert row["transcript_identity"] == "material_without_fingerprint"


# ---------------------------------------------------------------------------
# JEV judges the script against the cast-attributed transcript
# ---------------------------------------------------------------------------


def _capture_jev_baseline(monkeypatch):
    from pipeline import jev_gates

    seen = {}

    def gate(stage, paths, *, model, source_text):
        seen["text"] = Path(source_text).read_text(encoding="utf-8") if source_text else None
        return {"status": "passed", "stage": stage, "input_sha256": {}, "scheduler": {}}

    monkeypatch.setattr(jev_gates, "run_stage_gate", gate)
    return seen


def test_jev_compares_the_script_with_the_attributed_transcript(monkeypatch, tmp_path):
    """Raw ASR can credit a line to the wrong character; JEV sees the resolved speaker."""
    directory = _production(tmp_path, transcript=_transcript(None))

    def attribute(segments, _cast, **_kwargs):
        return {
            "model": "mock-jev",
            "attributions": [
                {"id": s["id"], "text": s["text"], "speaker": "C01", "speaker_name": "A",
                 "speaker_needs_review": True, "needs_review": True, "delivery": "voiceover"}
                for s in segments
            ],
        }

    monkeypatch.setattr(attribute_speakers, "attribute_speakers", attribute)
    _passing_validator(monkeypatch)
    seen = _capture_jev_baseline(monkeypatch)
    raw = tmp_path / "raw.txt"
    raw.write_text("[someone else] Hi\n", encoding="utf-8")

    row = production.check_stage(directory, "script", source_text=raw, advise_jev=True)
    assert row["status"] == "passed"
    assert "[A (speaker unsettled, voiceover)] Hi" in seen["text"]
    assert "someone else" not in seen["text"]
    assert row["jev"]["source_baseline"]["kind"] == "attributed"
    assert row["jev"]["source_baseline"]["supplied_source_text"] == str(raw)


def test_jev_falls_back_to_the_supplied_text_without_a_transcript(monkeypatch, tmp_path):
    directory = _production(tmp_path)
    _passing_validator(monkeypatch)
    seen = _capture_jev_baseline(monkeypatch)
    raw = tmp_path / "treatment.txt"
    raw.write_text("[A] Hi\n", encoding="utf-8")

    row = production.check_stage(directory, "script", source_text=raw, advise_jev=True)
    assert seen["text"] == "[A] Hi\n"
    assert row["jev"]["source_baseline"]["kind"] == "source_text"


# ---------------------------------------------------------------------------
# Storyboard asset anchors are checked in code, with the importer's rule
# ---------------------------------------------------------------------------


def _storyboard_production(tmp_path, *, sheet):
    directory = _production(tmp_path)
    _write(directory / "cast.json", {"source": "x", "characters": [
        {"id": "C01", "name": "A", "image": {"sheet": "full sheet" if sheet else ""}}]})
    _write(directory / "art.json", {"source": "x", "scenes": [
        {"id": "S01", "name": "Room", "image": {"prompt": "a room"}}]})
    _write(directory / "script.json", {"source": "x", "episodes": [{"ep": 1, "scenes": [{"sceneId": "S01"}]}]})
    _write(directory / "storyboard.json", {"source": "x", "episodes": [{"ep": 1, "segments": [
        {"id": "E01-01", "sceneIndex": 1, "cuts": [{"characters": ["C01"]}]}]}]})
    manifest = json.loads((directory / "production.json").read_text(encoding="utf-8"))
    manifest["artifacts"]["storyboard"] = {"path": "storyboard.json"}
    _write(directory / "production.json", manifest)
    return directory


def test_storyboard_reports_a_character_without_a_sheet(monkeypatch, tmp_path):
    directory = _storyboard_production(tmp_path, sheet=False)
    _passing_validator(monkeypatch)
    row = production.check_stage(directory, "storyboard")
    assert row["status"] == "assets_missing"
    assert row["asset_anchors"]["problems"] == ["Character C01 (A) has no image.sheet in cast.json."]


def test_storyboard_with_every_anchor_passes(monkeypatch, tmp_path):
    directory = _storyboard_production(tmp_path, sheet=True)
    _passing_validator(monkeypatch)
    row = production.check_stage(directory, "storyboard")
    assert row["status"] == "passed"
    assert row["asset_anchors"] == {"status": "complete", "problems": []}


def test_attribution_questions_are_split_to_fit_the_request_limit():
    """Each question repeats the roster, so a full episode must go out in several requests."""
    from typesafe_sdk import Choice

    batches = []

    class Client:
        def system_one(self, *, state, questions, model):
            batches.append(list(questions))
            return SimpleNamespace(model="jev-x", answers={name: name for name in questions})

    roster = {f"C{i:02d}": "人物描述" * 300 for i in range(8)}
    questions = {f"q{i}": Choice(instructions="谁在说话？", criteria=roster) for i in range(12)}
    response = attribute_speakers._ask_batched(Client(), {"characters": {}}, questions, "jev-latest")

    assert len(batches) > 1
    assert sorted(name for batch in batches for name in batch) == sorted(questions)
    assert response.answers == {name: name for name in questions}
    assert response.model == "jev-x"


def test_cast_review_sees_the_outline_it_judges_states_against(monkeypatch, tmp_path):
    from pipeline import jev_gates

    directory = _production(tmp_path)
    _passing_validator(monkeypatch)
    seen = {}

    def gate(stage, paths, *, model, source_text):
        seen["paths"] = set(paths)
        return {"status": "passed", "stage": stage, "input_sha256": {}, "scheduler": {}}

    monkeypatch.setattr(jev_gates, "run_stage_gate", gate)
    production.check_stage(directory, "cast", advise_jev=True)
    assert seen["paths"] == {"cast", "outline"}


def test_free_adaptation_is_not_compared_line_by_line(monkeypatch, tmp_path):
    """借壳 rewrites the source; attributing its speakers or scoring fidelity is noise."""
    directory = _production(tmp_path, transcript=_transcript(None))
    outline = json.loads((directory / "outline.json").read_text(encoding="utf-8"))
    outline["params"] = {"adaptMode": "借壳"}
    _write(directory / "outline.json", outline)
    _no_jev(monkeypatch)
    _passing_validator(monkeypatch)
    seen = _capture_jev_baseline(monkeypatch)
    raw = tmp_path / "raw.txt"
    raw.write_text("[A] Hi\n", encoding="utf-8")

    row = production.check_stage(directory, "script", source_text=raw, advise_jev=True)
    assert row["status"] == "passed"
    assert "--source" not in row["command"]
    assert row["source_comparison"] == "skipped: free adaptation"
    assert seen["text"] is None


# ---------------------------------------------------------------------------
# Rows inside an opening teaser are neither attributed nor compared
# ---------------------------------------------------------------------------

TEASER_TRANSCRIPT = {
    "kind": "qualified_asr_transcript",
    "segments": [
        {"id": "a", "start": 0.1, "end": 4.7, "text": "晚上见", "teaser": True},
        {"id": "b", "start": 35.0, "end": 37.0, "text": "你好"},
    ],
}


def test_teaser_rows_are_left_out_of_the_attributed_baseline(monkeypatch, tmp_path):
    directory = _production(tmp_path, transcript=TEASER_TRANSCRIPT)
    seen = []

    def attribute(segments, _cast, **_kwargs):
        seen.extend(s["text"] for s in segments)
        return {
            "model": "mock-jev",
            "attributions": [
                {"id": s["id"], "text": s["text"], "speaker": "C01", "needs_review": False}
                for s in segments
            ],
        }

    monkeypatch.setattr(attribute_speakers, "attribute_speakers", attribute)

    result = production.run_attribution(directory)

    assert result["status"] == "attributed" and seen == ["你好"]
    saved = json.loads(
        (directory / "source-transcript-attributed.json").read_text(encoding="utf-8")
    )
    assert [a["text"] for a in saved["attributions"]] == ["你好"]


def test_scene_notes_are_placed_without_the_teaser_row(tmp_path):
    """A teaser row would shift every exchange by one."""
    directory = _production(tmp_path, transcript=TEASER_TRANSCRIPT)
    _write(
        directory / "source-material.json",
        {
            "adaptation_story": {
                "sections": [
                    {"batch_id": "B001", "events": ["开场"]},
                    {"batch_id": "B002", "events": ["相遇"]},
                ]
            },
            "adaptation_story_batches": [
                {"batch_id": "B001", "start_seconds": 0.0, "end_seconds": 30.0},
                {"batch_id": "B002", "start_seconds": 30.0, "end_seconds": 60.0},
            ],
        },
    )

    production.write_scene_notes(directory)

    notes = json.loads((directory / "scene-notes.json").read_text(encoding="utf-8"))
    assert notes == {"0": "相遇"}
