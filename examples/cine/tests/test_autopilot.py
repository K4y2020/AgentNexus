"""The unattended production controller decides the next step from files alone."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "skills/film-analysis"))
from pipeline import autopilot, production

STAGES = ("outline", "cast", "art", "script", "storyboard")


@pytest.fixture
def directory(tmp_path):
    root = tmp_path / "production"
    root.mkdir()
    for stage in STAGES:
        (root / f"{stage}.json").write_text(json.dumps({"stage": stage}), encoding="utf-8")
    return root


class Checks:
    """Stand-in for check_stage: every stage passes unless told otherwise."""

    def __init__(self, monkeypatch):
        self.results: dict[str, dict] = {}
        self.calls: list[str] = []
        monkeypatch.setattr(production, "check_stage", self)
        monkeypatch.setattr(production, "write_scene_notes", lambda _d: {"status": "kept"})

    def __call__(self, directory, stage, source_text=None, **_kwargs):
        self.calls.append(stage)
        path = directory / f"{stage}.json"
        row = {"stage": stage, "status": "passed", "input_hashes": {str(path): production.fingerprint(path)}}
        row.update(self.results.get(stage, {}))
        return row


def edit(directory, stage, n):
    (directory / f"{stage}.json").write_text(json.dumps({"stage": stage, "edit": n}), encoding="utf-8")


def queue(directory):
    return json.loads((directory / "review-queue.json").read_text(encoding="utf-8"))["items"]


def test_missing_outline_asks_for_intake(tmp_path):
    assert autopilot.next_step(tmp_path)["action"] == "intake"


def test_a_failing_stage_gets_a_bounded_edit_budget(monkeypatch, directory):
    checks = Checks(monkeypatch)
    checks.results["script"] = {"status": "failed", "stderr": "台词顺序被调换了"}

    steps = []
    for n in range(autopilot.MAX_EDITS + 1):
        steps.append(autopilot.next_step(directory, jev=False))
        edit(directory, "script", n)

    assert [s["action"] for s in steps[:-1]] == ["edit"] * autopilot.MAX_EDITS
    assert [s["mode"] for s in steps[:2]] == ["write", "repair"]
    assert steps[0]["skill"] == "cine-script" and "台词顺序被调换了" in steps[0]["errors"]
    assert steps[-1]["action"] == "escalate" and steps[-1]["reason"] == "repair_exhausted"
    assert [i["severity"] for i in queue(directory)] == ["blocking"]


def test_an_unedited_file_stops_after_repeated_calls(monkeypatch, directory):
    checks = Checks(monkeypatch)
    checks.results["cast"] = {"status": "failed"}
    steps = [autopilot.next_step(directory, jev=False) for _ in range(autopilot.MAX_UNCHANGED + 1)]
    assert steps[-1]["reason"] == "not_progressing"


def test_a_person_editing_the_file_reopens_an_escalated_stage(monkeypatch, directory):
    checks = Checks(monkeypatch)
    checks.results["cast"] = {"status": "failed"}
    for n in range(autopilot.MAX_EDITS + 1):
        result = autopilot.next_step(directory, jev=False)
        edit(directory, "cast", n)
    # The last edit above happened after escalation, i.e. by a person.
    assert result["action"] == "escalate"
    checks.results.pop("cast")
    assert autopilot.next_step(directory, jev=False)["action"] == "import_canvas"
    assert queue(directory) == []


def test_passed_stages_are_not_rechecked_until_an_input_changes(monkeypatch, directory):
    checks = Checks(monkeypatch)
    autopilot.next_step(directory, jev=False)
    assert checks.calls == list(STAGES)
    autopilot.next_step(directory, jev=False)
    assert checks.calls == list(STAGES)
    edit(directory, "art", 1)
    autopilot.next_step(directory, jev=False)
    assert checks.calls[len(STAGES):] == ["art"]


def test_missing_sheets_are_repaired_in_cast(monkeypatch, directory):
    checks = Checks(monkeypatch)
    checks.results["storyboard"] = {
        "status": "assets_missing",
        "asset_anchors": {"status": "missing", "problems": ["Character C01 (A) has no image.sheet in cast.json."]},
    }
    step = autopilot.next_step(directory, jev=False)
    assert (step["stage"], step["target_stage"], step["skill"]) == ("storyboard", "cast", "cine-characters")
    assert step["file"].endswith("cast.json")


def test_technical_failures_retry_once_then_wait_for_an_explicit_retry(monkeypatch, directory):
    checks = Checks(monkeypatch)
    checks.results["script"] = {"status": "attribution_error", "stderr": "TYPESAFE_API_KEY missing"}
    step = autopilot.next_step(directory, jev=False)
    assert step["reason"] == "technical_attribution_error"
    assert checks.calls.count("script") == 2
    assert autopilot.next_step(directory, jev=False)["action"] == "escalate"
    checks.results.pop("script")
    assert autopilot.next_step(directory, jev=False, retry=True)["action"] == "import_canvas"


def test_advisory_findings_are_queued_without_blocking(monkeypatch, directory):
    checks = Checks(monkeypatch)
    checks.results["script"] = {
        "jev": {"status": "baseline_unusable", "failures": ["x"]},
        # The script was compared with the attributed transcript.
        "command": ["node", "cine-script.mjs", "validate", "--source", "attributed.json"],
    }
    (directory / "source-transcript-attributed.json").write_text(
        json.dumps({"attributions": [{"text": "我来提示了！", "needs_review": True}]}), encoding="utf-8"
    )
    step = autopilot.next_step(directory)
    assert step["action"] == "import_canvas"
    reasons = {(i["reason"], i["severity"]) for i in queue(directory)}
    assert reasons == {("jev_baseline_unusable", "review"), ("speaker_unsettled", "review")}


def test_canvas_sync_is_recorded_per_storyboard_version(monkeypatch, directory, tmp_path):
    Checks(monkeypatch)
    step = autopilot.next_step(directory, jev=False)
    assert step["arguments"]["action"] == "import_storyboard"
    receipt = tmp_path / "receipt.json"
    receipt.write_text(json.dumps({"status": "imported"}), encoding="utf-8")
    assert autopilot.record_canvas_sync(directory, receipt)["action"] == "done"
    edit(directory, "storyboard", 1)
    assert autopilot.next_step(directory, jev=False)["action"] == "import_canvas"


def test_seed_creates_a_missing_stage_before_checking(monkeypatch, directory):
    checks = Checks(monkeypatch)
    (directory / "art.json").unlink()
    seeded = []

    def seed(d, stage):
        seeded.append(stage)
        (d / f"{stage}.json").write_text("{}", encoding="utf-8")

    monkeypatch.setattr(production, "seed", seed)
    checks.results["art"] = {"status": "failed"}
    step = autopilot.next_step(directory, jev=False)
    assert seeded == ["art"]
    assert (step["action"], step["mode"], step["stage"]) == ("edit", "write", "art")


def test_edit_history_survives_the_stage_passing(monkeypatch, directory):
    checks = Checks(monkeypatch)
    checks.results["cast"] = {"status": "failed"}
    autopilot.next_step(directory, jev=False)
    edit(directory, "cast", 1)
    autopilot.next_step(directory, jev=False)
    edit(directory, "cast", 2)
    checks.results.pop("cast")
    autopilot.next_step(directory, jev=False)
    state = json.loads((directory / autopilot.STATE).read_text(encoding="utf-8"))
    history = state["stages"]["cast"]["history"]
    assert [h["status"] for h in history] == ["failed", "failed", "passed"]
    assert state["stages"]["cast"]["failed_versions"] == []


def test_a_free_adaptation_raises_no_speaker_items(monkeypatch, directory):
    """Without a source comparison, unsettled source speakers are not this script's concern."""
    checks = Checks(monkeypatch)
    checks.results["script"] = {"command": ["node", "cine-script.mjs", "validate"]}
    (directory / "source-transcript-attributed.json").write_text(
        json.dumps({"attributions": [{"text": "x", "needs_review": True}]}), encoding="utf-8"
    )
    autopilot.next_step(directory, jev=False)
    assert queue(directory) == []
