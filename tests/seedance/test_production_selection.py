import hashlib
import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from omnigent.seedance.production_gate import native_checker

SKILLS = Path(__file__).resolve().parents[2] / "examples/cine/skills"


@pytest.fixture
def native():
    return native_checker(SKILLS)


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def bind(root, stage, relative):
    write(
        root / "production.json",
        {
            "schema_version": 1,
            "mode": "adaptation",
            "artifacts": {stage: {"path": relative}},
        },
    )


def test_canonical_does_not_hide_a_second_script(native, tmp_path):
    write(tmp_path / "script.json", {"source": "original", "episodes": []})
    write(tmp_path / "adapted-script.json", {"source": "adapted", "episodes": []})
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    with pytest.raises(ValueError, match="Ambiguous script"):
        native.stage_path(tmp_path, "script")
    assert before == {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    bind(tmp_path, "script", "adapted-script.json")
    assert native.stage_path(tmp_path, "script") == tmp_path / "adapted-script.json"


@pytest.mark.parametrize("name", ["script.json", "title-script.json"])
def test_single_artifact_remains_compatible(native, tmp_path, name):
    write(tmp_path / name, {"source": "one", "episodes": []})
    assert native.stage_path(tmp_path, "script") == tmp_path / name


@pytest.mark.parametrize("relative", ["../outside.json", "production.json"])
def test_binding_cannot_escape_or_select_itself(native, tmp_path, relative):
    bind(tmp_path, "script", relative)
    with pytest.raises(ValueError):
        native.stage_path(tmp_path, "script")


def test_missing_bound_file_never_falls_back(native, tmp_path):
    write(tmp_path / "script.json", {"source": "old", "episodes": []})
    bind(tmp_path, "script", "missing.json")
    with pytest.raises(FileNotFoundError, match="Bound script"):
        native.stage_path(tmp_path, "script")


def test_manifest_requires_binding_for_requested_stage(native, tmp_path):
    write(tmp_path / "script.json", {"source": "old", "episodes": []})
    bind(tmp_path, "outline", "outline.json")
    with pytest.raises(ValueError, match="Missing explicit script"):
        native.stage_path(tmp_path, "script")


@pytest.mark.skipif(not shutil.which("node"), reason="requires native Node seeder")
def test_seed_uses_the_same_legacy_upstream_resolver(native, tmp_path):
    original = next((SKILLS / "novel-outline/examples").glob("*-outline.json"))
    legacy = tmp_path / "selected-outline.json"
    shutil.copyfile(original, legacy)
    before = legacy.read_bytes()
    result = native.seed(tmp_path, "script")
    assert result["command"][-1] == str(legacy)
    assert result["input_hashes"][str(legacy)] == hashlib.sha256(before).hexdigest()
    assert result["input_hashes"][str(tmp_path / "production.json")] is None
    assert legacy.read_bytes() == before
    assert not (tmp_path / "outline.json").exists()


def test_seed_never_creates_another_version_beside_existing_legacy(native, tmp_path):
    existing = tmp_path / "old-script.json"
    write(existing, {"source": "existing", "episodes": []})
    with pytest.raises(FileExistsError):
        native.seed(tmp_path, "script")
    assert not (tmp_path / "script.json").exists()


def test_new_binding_during_seed_blocks_output(native, tmp_path, monkeypatch):
    write(
        tmp_path / "outline.json",
        {
            "source": "source",
            "characters": [],
            "scenes": [],
            "beats": [],
            "episodes": [],
        },
    )
    monkeypatch.setattr(native.shutil, "which", lambda _: "node")

    def run(*args, **kwargs):
        bind(tmp_path, "outline", "outline.json")
        return SimpleNamespace(returncode=0, stdout='{"source":"source","episodes":[]}', stderr="")

    monkeypatch.setattr(native.subprocess, "run", run)
    with pytest.raises(ValueError, match="upstream changed"):
        native.seed(tmp_path, "script")
    assert not (tmp_path / "script.json").exists()


def test_binding_change_during_resolution_cannot_be_stamped_as_current(
    native, tmp_path, monkeypatch
):
    write(tmp_path / "cast.json", {"source": "one", "characters": []})
    write(tmp_path / "next-cast.json", {"source": "two", "characters": []})
    bind(tmp_path, "cast", "cast.json")
    resolve = native.stage_path

    def switch_after_resolution(directory, stage):
        path = resolve(directory, stage)
        bind(tmp_path, "cast", "next-cast.json")
        return path

    monkeypatch.setattr(native, "stage_path", switch_after_resolution)
    monkeypatch.setattr(native.shutil, "which", lambda _: "node")
    monkeypatch.setattr(
        native.subprocess,
        "run",
        lambda *a, **k: SimpleNamespace(returncode=0, stdout="", stderr=""),
    )
    assert native.check_stage(tmp_path, "cast")["status"] == "inputs_changed"
