"""Chinese text must survive the trip from the source project to the production.

A shell redirect on Windows re-encodes what a program printed, so a source-material
export once arrived with a replacement character for every Chinese character. The
story draft, the scene notes derived from it, and everything built on them were
unreadable while every check passed. These tests pin the three defences: the export
writes UTF-8 itself, the reader refuses a damaged file, and the run stops at its
first call rather than an hour in.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "skills/film-analysis"))
from pipeline import autopilot, handoff, production

PIPELINE = Path(__file__).parents[1] / "skills/film-analysis/pipeline"
DAMAGE = chr(0xFFFD)
EVENTS = ["街头卦摊前，算命先生断言贾敏命中带苦。", "灵堂遗照前，林黛玉决定去外婆家借住。"]


def _write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def _project(tmp_path, *, events=EVENTS):
    """A committed analysis project with a story plan and a Chinese story draft."""
    project = tmp_path / "analysis"
    _write(project / "project.json", {"current_revision": "r1"})
    _write(project / "source.json", {"source_id": "film"})
    _write(project / "revisions/r1/source_shots.json", [])
    _write(
        project / "revisions/r1/story_plan.json",
        {
            "source_id": "film",
            "revision_id": "r1",
            "batches": [{"batch_id": "B001", "start_seconds": 0.0, "end_seconds": 30.0}],
        },
    )
    _write(
        project / "story/r1.json",
        {
            "source_id": "film",
            "revision_id": "r1",
            "sections": [{"batch_id": "B001", "events": events}],
        },
    )
    return project


# ---------------------------------------------------------------------------
# read_text refuses what a wrong-encoding write leaves behind
# ---------------------------------------------------------------------------


def test_chinese_text_reads_back_unchanged(tmp_path):
    path = tmp_path / "a.json"
    _write(path, {"events": EVENTS})
    assert production.read(path) == {"events": EVENTS}


def test_a_utf8_bom_is_accepted(tmp_path):
    path = tmp_path / "a.json"
    path.write_bytes(b"\xef\xbb\xbf" + json.dumps({"k": "值"}, ensure_ascii=False).encode())
    assert production.read(path) == {"k": "值"}


def test_utf16_is_refused_as_a_powershell_redirect(tmp_path):
    path = tmp_path / "source-material.json"
    path.write_bytes(json.dumps({"k": "值"}).encode("utf-16"))
    with pytest.raises(ValueError, match=r"UTF-16.*PowerShell") as failure:
        production.read(path)
    assert "--output" in str(failure.value)


def test_bytes_that_are_not_utf8_are_refused(tmp_path):
    path = tmp_path / "a.json"
    # A whole sentence: two GBK bytes can happen to form one valid UTF-8 character.
    path.write_bytes(json.dumps({"k": EVENTS[0]}, ensure_ascii=False).encode("gbk"))
    with pytest.raises(ValueError, match="not valid UTF-8"):
        production.read(path)


def test_replacement_characters_from_a_bad_write_are_refused(tmp_path):
    path = tmp_path / "a.json"
    _write(path, {"events": [DAMAGE * 40]})
    with pytest.raises(ValueError, match="40 replacement characters"):
        production.read(path)


def test_a_stray_replacement_character_is_tolerated(tmp_path):
    """Whisper emits one now and then; that is not a damaged file."""
    path = tmp_path / "a.json"
    _write(path, {"events": ["林黛玉" + DAMAGE * (production.DAMAGED_MIN - 1)]})
    assert production.read(path)["events"][0].startswith("林黛玉")


# ---------------------------------------------------------------------------
# The export writes UTF-8 itself
# ---------------------------------------------------------------------------


def test_output_writes_the_material_as_utf8(tmp_path, capsys):
    project = _project(tmp_path)
    target = tmp_path / "production" / "source-material.json"

    assert handoff.main(["--source-project", str(project), "--output", str(target)]) == 0

    summary = json.loads(capsys.readouterr().out)
    assert summary["status"] == "written" and summary["bytes"] == len(target.read_bytes())
    material = production.read(target)
    assert material["adaptation_story"]["sections"][0]["events"] == EVENTS


def test_output_needs_a_source_project(tmp_path):
    manifest = tmp_path / "production.json"
    manifest.write_text("{}", encoding="utf-8")
    with pytest.raises(SystemExit) as failure:
        handoff.main([str(manifest), "--output", str(tmp_path / "x.json")])
    assert failure.value.code == 2


def test_nothing_is_written_when_the_export_is_blocked(tmp_path, capsys):
    target = tmp_path / "source-material.json"
    assert (
        handoff.main(["--source-project", str(tmp_path / "missing"), "--output", str(target)]) == 1
    )
    assert json.loads(capsys.readouterr().out)["status"] == "blocked"
    assert not target.exists()


def test_printed_material_is_utf8_whatever_the_console_code_page(tmp_path):
    """The redirect path: the child inherits a legacy code page unless it overrides it."""
    project = _project(tmp_path)
    done = subprocess.run(
        [sys.executable, str(PIPELINE / "handoff.py"), "--source-project", str(project)],
        capture_output=True,
        env={"PYTHONIOENCODING": "gbk", "PYTHONUTF8": "0", "SYSTEMROOT": "C:\\Windows"},
        check=True,
    )
    material = json.loads(done.stdout.decode("utf-8"))
    assert material["adaptation_story"]["sections"][0]["events"] == EVENTS


def test_a_damaged_story_draft_is_not_exported(tmp_path, capsys):
    project = _project(tmp_path, events=[DAMAGE * 60])
    target = tmp_path / "source-material.json"

    assert handoff.main(["--source-project", str(project), "--output", str(target)]) == 1

    blocked = json.loads(capsys.readouterr().out)
    assert "STORY_DRAFT_DAMAGED" in blocked["error"] and "60" in blocked["error"]
    assert not target.exists()


# ---------------------------------------------------------------------------
# A damaged export stops the run where it starts
# ---------------------------------------------------------------------------


def _damaged(directory, name):
    directory.mkdir(parents=True, exist_ok=True)
    _write(directory / name, {"adaptation_story": {"sections": [{"events": [DAMAGE * 30]}]}})


def test_next_stops_at_the_first_call_on_a_damaged_export(tmp_path):
    directory = tmp_path / "production"
    _damaged(directory, "source-material.json")
    with pytest.raises(ValueError, match=r"handoff\.py --output"):
        autopilot.next_step(directory)


def test_next_ignores_unused_legacy_scene_notes(tmp_path):
    directory = tmp_path / "production"
    _damaged(directory, "scene-notes.json")
    assert autopilot.next_step(directory)["action"] == "intake"


def test_intake_names_the_safe_export_command(tmp_path):
    step = autopilot.next_step(tmp_path)
    assert step["action"] == "intake" and "--output" in step["detail"]
