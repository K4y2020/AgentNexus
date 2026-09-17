import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "skills/film-analysis"))
from pipeline import production


def example(directory, stage):
    name = production.STAGES[stage]
    source = next((production.SKILLS / name / "examples").glob(f"*-{stage}.json"))
    shutil.copyfile(source, directory / f"{stage}.json")


def test_init_is_incomplete_native_outline_and_never_overwrites(tmp_path):
    result = production.init_outline(tmp_path, "A film", 1, 30, "rescue", "借壳")
    doc = production.read(tmp_path / "outline.json")
    assert doc["params"]["minutesPerEpisode"] == 0.5
    assert doc["episodes"][0]["ep"] == 1
    assert not result["validated"]
    assert doc["characters"] == []
    before = (tmp_path / "outline.json").read_bytes()
    with pytest.raises(FileExistsError):
        production.init_outline(tmp_path, "Other", 2, 60, "rescue", "忠实")
    assert (tmp_path / "outline.json").read_bytes() == before


@pytest.mark.skipif(not shutil.which("node"), reason="native CLI requires node")
@pytest.mark.parametrize("stage", ["cast", "art", "script", "storyboard"])
def test_seed_reuses_native_cli(tmp_path, stage):
    upstream = "script" if stage == "storyboard" else "outline"
    example(tmp_path, upstream)
    result = production.seed(tmp_path, stage)
    assert result["status"] == "draft_seeded"
    assert result["validated"] is False
    doc = production.read(tmp_path / f"{stage}.json")
    assert production.shape_errors(stage, doc) == []
    assert doc["source"] == production.read(tmp_path / f"{upstream}.json")["source"]
    assert result["command"][2] == "seed"
    with pytest.raises(FileExistsError):
        production.seed(tmp_path, stage)


@pytest.mark.skipif(not shutil.which("node"), reason="native CLI requires node")
def test_storyboard_seed_can_replace_only_untouched_empty_seed(tmp_path):
    example(tmp_path, "script")
    production.seed(tmp_path, "storyboard")
    production.seed(tmp_path, "storyboard", replace_empty=True)
    storyboard = production.read(tmp_path / "storyboard.json")
    storyboard["episodes"][0]["segments"] = [{"id": "authored"}]
    production.atomic_json(tmp_path / "storyboard.json", storyboard)
    with pytest.raises(FileExistsError):
        production.seed(tmp_path, "storyboard", replace_empty=True)


@pytest.mark.skipif(not shutil.which("node"), reason="native CLI requires node")
def test_full_native_fixture_executes_five_validators(tmp_path):
    for stage in production.STAGES:
        example(tmp_path, stage)
    source = next((production.SKILLS / "novel-characters/examples").glob("*.txt"))
    result = production.check(tmp_path, source_text=source)
    assert result["status"] == "native_validated", result
    assert len(result["stages"]) == 5
    assert all(r["exit_code"] == 0 and r["input_hashes"] for r in result["stages"])
    assert not result["production_authorized"]
    assert production.read(Path(result["report_path"]))["run_id"] == result["run_id"]


def test_real_live_failures_stay_failed(tmp_path):
    artifacts = Path(__file__).parents[3] / "docs/evaluations/cine-live-20260907/artifacts"
    for stage in production.STAGES:
        shutil.copyfile(artifacts / f"{stage}.json", tmp_path / f"{stage}.json")
    result = production.check(tmp_path)
    assert result["status"] == "failed"
    assert all(r["status"] == "invalid_shape" for r in result["stages"])
    assert all(r["exit_code"] is None for r in result["stages"])
    assert production.read(tmp_path / "script.json").get("meta")


def test_exit_code_not_printed_pass_decides_result(tmp_path, monkeypatch):
    example(tmp_path, "outline")
    monkeypatch.setattr(production.shutil, "which", lambda _: "node")
    monkeypatch.setattr(
        production.subprocess,
        "run",
        lambda *a, **_kw: subprocess.CompletedProcess(a[0], 1, "PASS", "schema rejected"),
    )
    result = production.check(tmp_path, "outline")
    assert result["status"] == "failed"
    assert result["stages"][0]["stdout"] == "PASS"
    assert result["stages"][0]["exit_code"] == 1


def test_timeout_is_recorded(tmp_path, monkeypatch):
    example(tmp_path, "outline")
    monkeypatch.setattr(production.shutil, "which", lambda _: "node")

    def timeout(command, **_kwargs):
        raise subprocess.TimeoutExpired(command, 1)

    monkeypatch.setattr(production.subprocess, "run", timeout)
    result = production.check(tmp_path, "outline", timeout=1)
    assert result["stages"][0]["status"] == "timeout"
    assert Path(result["report_path"]).is_file()


def test_node_missing_does_not_fallback(tmp_path, monkeypatch):
    example(tmp_path, "outline")
    monkeypatch.setattr(production.shutil, "which", lambda _: None)
    result = production.check(tmp_path, "outline")
    assert result["status"] == "failed"
    assert result["stages"][0]["command"] == []


def test_mutated_inputs_cannot_pass(tmp_path, monkeypatch):
    example(tmp_path, "outline")
    monkeypatch.setattr(production.shutil, "which", lambda _: "node")

    def run(command, **_kwargs):
        (tmp_path / "outline.json").write_text("{}", encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, "PASS", "")

    monkeypatch.setattr(production.subprocess, "run", run)
    result = production.check(tmp_path, "outline")
    assert result["stages"][0]["status"] == "inputs_changed"
    assert result["status"] == "failed"


@pytest.mark.skipif(not shutil.which("node"), reason="native CLI requires node")
def test_missing_source_quotes_cannot_be_full_pass(tmp_path):
    example(tmp_path, "cast")
    result = production.check(tmp_path, "cast")
    assert result["stages"][0]["exit_code"] == 0
    assert result["status"] == "incomplete"
    assert result["stages"][0]["skipped_checks"] == ["exact_source_quotes"]


def test_cli_failure_nonzero_and_report_is_not_reused(tmp_path, capsys):
    (tmp_path / "outline.json").write_text(json.dumps({"meta": {}}))
    assert production.main(["check", str(tmp_path), "--stage", "outline"]) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "failed"
    assert len(list((tmp_path / ".cine-validation").glob("*.json"))) == 2


def test_finalize_validates_before_refreshing_pins(tmp_path, monkeypatch):
    names = ("source_material", "outline", "cast", "art", "script", "storyboard", "mapping")
    for name in names:
        (tmp_path / f"{name}.json").write_text(json.dumps({"name": name}), encoding="utf-8")
    production.atomic_json(
        tmp_path / "production.json",
        {
            "schema_version": 1,
            "mode": "adaptation",
            "artifacts": {
                name: {"path": f"{name}.json", "sha256": "old", "inputs": {}} for name in names
            },
        },
    )
    monkeypatch.setattr(
        production, "check", lambda *_args, **_kwargs: {"status": "native_validated"}
    )
    monkeypatch.setattr("pipeline.handoff.validate", lambda _path: {"status": "handoff_validated"})
    result = production.finalize(tmp_path)
    refreshed = production.read(tmp_path / "production.json")["artifacts"]
    assert result["status"] == "production_finalized"
    assert refreshed["storyboard"]["inputs"] == {
        "script": refreshed["script"]["sha256"],
        "cast": refreshed["cast"]["sha256"],
        "art": refreshed["art"]["sha256"],
    }
    assert refreshed["mapping"]["inputs"]["storyboard"] == refreshed["storyboard"]["sha256"]
