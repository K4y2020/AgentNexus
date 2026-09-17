import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "skills/film-analysis"))
from pipeline import entry


def test_index_entry_binds_only_committed_revision(tmp_path, monkeypatch):
    video = tmp_path / "source.mp4"
    video.write_bytes(b"source")
    output = tmp_path / "projects/film"

    def run(media, projects, **kwargs):
        assert media == video
        assert kwargs["scope_out_seconds"] == 30
        manifest = output / "project.json"
        data = json.loads(manifest.read_text())
        data["current_revision"] = "r1"
        manifest.write_text(json.dumps(data))
        return {"revision_id": "r1", "validation_passed": True, "report_path": "report.html"}

    monkeypatch.setattr(entry, "run_pipeline", run)
    result = entry.index_media(
        video, output, session_id="s1", workspace=tmp_path, profile="forensic"
    )
    assert result["analysis_status"] == "indexed_unreviewed"
    assert result["reviewed_shot_count"] == 0
    binding = json.loads((tmp_path / ".cine/sessions/s1.json").read_text())
    assert binding["project_path"] == str(output)
    assert json.loads((output / "reviews/r1.json").read_text()) == []
    assert video.read_bytes() == b"source"
    with pytest.raises(FileExistsError):
        entry.index_media(video, output)
    with pytest.raises(ValueError, match="different project"):
        entry.index_media(video, tmp_path / "projects/other", session_id="s1", workspace=tmp_path)


def test_render_failure_does_not_bind(tmp_path, monkeypatch):
    video = tmp_path / "source.mp4"
    video.write_bytes(b"source")
    monkeypatch.setattr(
        entry,
        "run_pipeline",
        lambda *a, **kw: {
            "revision_id": "r1",
            "validation_passed": True,
            "reason": "render_failed",
        },
    )
    result = entry.index_media(video, tmp_path / "film", session_id="s1", workspace=tmp_path)
    assert result["analysis_status"] == "blocked"
    assert not (tmp_path / ".cine/sessions/s1.json").exists()


def test_topic_workspace_infers_session_binding_and_rejects_conflict(tmp_path, monkeypatch):
    workspace = tmp_path / "topics/topic123"
    workspace.mkdir(parents=True)
    video = workspace / "source.mp4"
    video.write_bytes(b"source")
    output = workspace / "projects/film"

    def run(*_args, **_kwargs):
        manifest = output / "project.json"
        data = json.loads(manifest.read_text())
        data["current_revision"] = "r1"
        manifest.write_text(json.dumps(data))
        return {"revision_id": "r1", "validation_passed": True, "report_path": "report.html"}

    monkeypatch.setattr(entry, "run_pipeline", run)
    entry.index_media(video, output, workspace=workspace, profile="forensic")
    assert (workspace / ".cine/sessions/topic123.json").is_file()
    with pytest.raises(ValueError, match="conflicts with the Topic workspace"):
        entry.index_media(
            video,
            workspace / "projects/other",
            session_id="made_up",
            workspace=workspace,
            profile="forensic",
        )


@pytest.mark.parametrize("start,end", [(0, 0), (-1, 30), (0, float("nan")), (0, float("inf"))])
def test_invalid_scope_does_not_create_project(tmp_path, start, end):
    video = tmp_path / "source.mp4"
    video.write_bytes(b"source")
    with pytest.raises(ValueError):
        entry.index_media(video, tmp_path / "film", start=start, end=end)
    assert not (tmp_path / "film").exists()
