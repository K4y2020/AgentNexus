import json
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parents[1] / "skills/film-analysis"))
from pipeline import entry, story
from pipeline.runner import _build_shots
from pipeline.schemas import SourceMediaRecord


def source():
    return SourceMediaRecord(
        source_id="source",
        path="video.mp4",
        size_bytes=1,
        mtime_ns=1,
        sha256_head="head",
        sha256_tail="tail",
        duration_pts=8087999,
        time_base_num=1,
        time_base_den=16000,
    )


def test_story_sampling_covers_ending_with_bounded_frames(tmp_path, monkeypatch):
    monkeypatch.setattr(story, "_ffmpeg_available", lambda: True)
    monkeypatch.setattr(story, "_ffmpeg_version", lambda: "test")

    def extract(_media, _seek, path):
        path.write_bytes(b"test frame")
        return ["ffmpeg", "test"]

    monkeypatch.setattr(story, "_extract_frame", extract)
    plan, evidence = story.extract_story_plan(Path("video"), source(), "rev", tmp_path, 0, 8087999)
    assert len(plan["batches"]) == 17
    assert len(evidence) == 51
    assert plan["batches"][0]["interval"]["in_pts"] == 0
    assert plan["batches"][-1]["interval"]["out_pts"] == 8087999
    assert all(e.extraction_status == "ok" and e.kind == "story_sample" for e in evidence)
    assert all(e.source_interval.out_pts <= 8087999 for e in evidence)
    assert plan["accuracy_percent"] is None


def test_missing_frame_capability_does_not_fabricate_images(tmp_path, monkeypatch):
    monkeypatch.setattr(story, "_ffmpeg_available", lambda: False)
    monkeypatch.setattr(story, "_ffmpeg_version", lambda: "unavailable")
    _, evidence = story.extract_story_plan(Path("video"), source(), "rev", tmp_path, 0, 480000)
    assert all(e.relative_path is None and e.extraction_status == "unavailable" for e in evidence)


def test_missing_cut_detector_does_not_stop_temporal_planning(tmp_path, monkeypatch):
    from pipeline import runner

    media = tmp_path / "video"
    media.write_bytes(b"media")
    media_source = source()
    media_source.path = str(media)
    monkeypatch.setattr(runner, "_resolve_source", lambda *_a, **_kw: media_source)
    monkeypatch.setattr(
        runner, "detect_cuts", lambda *_a, **_kw: SimpleNamespace(status="unavailable")
    )
    monkeypatch.setattr(story, "_ffmpeg_available", lambda: True)
    monkeypatch.setattr(story, "_ffmpeg_version", lambda: "test")

    def extract(_media, _seek, path):
        path.write_bytes(b"test frame")
        return ["ffmpeg", "test"]

    monkeypatch.setattr(story, "_extract_frame", extract)
    result = runner.run_pipeline(media, tmp_path / "projects", evidence_mode="story")
    assert result.get("detection_warning"), result
    assert result["candidate_count"] == 0
    assert Path(result["story_plan_path"]).is_file()


def test_final_shot_clamped_to_exact_source_pts():
    shots = _build_shots(source(), "rev", [], 0, 8088000)
    assert shots[-1].interval.out_pts == 8087999


def test_default_entry_is_full_adaptation_not_opening_only(tmp_path, monkeypatch):
    media = tmp_path / "video"
    media.write_bytes(b"media")

    def run(_media, projects, **kwargs):
        assert kwargs["scope_out_seconds"] is None
        assert kwargs["evidence_mode"] == "story"
        path = projects / kwargs["project_id"] / "project.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        data["current_revision"] = "rev"
        path.write_text(json.dumps(data), encoding="utf-8")
        return {"revision_id": "rev", "validation_passed": True, "report_path": "report"}

    monkeypatch.setattr(entry, "run_pipeline", run)
    result = entry.index_media(media, tmp_path / "project")
    assert result["profile"] == "adaptation"
    assert result["reviewed_shot_count"] == 0


def test_resume_preserves_story_progress_and_refuses_changed_source(tmp_path, monkeypatch):
    media = tmp_path / "video"
    media.write_bytes(b"media")
    project = tmp_path / "project"
    rev = project / "revisions/rev"
    (rev / "report").mkdir(parents=True)
    (rev / "report/report.html").write_text("index")
    (project / "story").mkdir()
    draft = project / "story/rev.json"
    draft.write_text('{"saved_progress": true}')
    (project / "project.json").write_text(json.dumps({"current_revision": "rev"}))
    (rev / "validation.json").write_text('{"passed": true}')
    (rev / "story_plan.json").write_text(
        json.dumps(
            {
                "source_id": "s",
                "revision_id": "rev",
                "batches": [{"batch_id": "B001", "start_seconds": 0, "end_seconds": 60}],
            }
        )
    )
    (project / "source.json").write_text(
        json.dumps(
            {
                "source_id": "s",
                "duration_pts": 60,
                "time_base_num": 1,
                "time_base_den": 1,
                "sha256_head": entry._sha256_chunk(media, tail=False),
                "sha256_tail": entry._sha256_chunk(media, tail=True),
            }
        )
    )

    def no_reindex(*_args, **_kwargs):
        raise AssertionError("unchanged source must resume the current revision")

    monkeypatch.setattr(entry, "run_pipeline", no_reindex)
    result = entry.index_media(media, project, resume=True)
    assert result["reused_revision"] is True
    assert json.loads(draft.read_text()) == {"saved_progress": True}
    media.write_bytes(b"different movie")
    import pytest

    with pytest.raises(ValueError, match="Source media changed"):
        entry.index_media(media, project, resume=True)


# ---------------------------------------------------------------------------
# A film that opens with a teaser of its own later footage
# ---------------------------------------------------------------------------

TEASER = {"status": "found", "method": "frame_reuse", "end_seconds": 4.75, "clips": []}


def _sampled(tmp_path, monkeypatch, teaser, end=8087999):
    monkeypatch.setattr(story, "_ffmpeg_available", lambda: True)
    monkeypatch.setattr(story, "_ffmpeg_version", lambda: "test")

    def extract(_media, _seek, path):
        path.write_bytes(b"test frame")
        return ["ffmpeg", "test"]

    monkeypatch.setattr(story, "_extract_frame", extract)
    return story.extract_story_plan(
        Path("video"), source(), "rev", tmp_path, 0, end, opening_teaser=teaser
    )


def _first_still(evidence):
    return next(e for e in evidence if e.relative_path.endswith("B001_0.jpg"))


def test_the_first_still_is_taken_after_an_opening_teaser(tmp_path, monkeypatch):
    plan, evidence = _sampled(tmp_path, monkeypatch, TEASER)

    assert _first_still(evidence).source_interval.in_pts == int(4.75 * 16000)
    assert plan["opening_teaser"] == TEASER
    assert "teaser of its own later footage" in plan["sampling_note"]


def test_without_a_teaser_the_first_still_is_the_first_frame(tmp_path, monkeypatch):
    plan, evidence = _sampled(tmp_path, monkeypatch, None)

    assert _first_still(evidence).source_interval.in_pts == 0
    assert "opening_teaser" not in plan


def test_a_scan_that_found_nothing_is_recorded_and_moves_nothing(tmp_path, monkeypatch):
    plan, evidence = _sampled(tmp_path, monkeypatch, {"status": "none", "method": "frame_reuse"})

    assert _first_still(evidence).source_interval.in_pts == 0
    assert plan["opening_teaser"]["status"] == "none"
    assert "sampling_note" in plan and "teaser" not in plan["sampling_note"]


def test_a_teaser_longer_than_a_batch_keeps_the_still_inside_the_batch(tmp_path, monkeypatch):
    plan, evidence = _sampled(tmp_path, monkeypatch, {**TEASER, "end_seconds": 45.0})

    batch = plan["batches"][0]["interval"]
    assert _first_still(evidence).source_interval.in_pts < batch["out_pts"]


def _pipeline(tmp_path, monkeypatch, **kwargs):
    from pipeline import runner

    media = tmp_path / "video"
    media.write_bytes(b"media")
    media_source = source()
    media_source.path = str(media)
    monkeypatch.setattr(runner, "_resolve_source", lambda *_a, **_kw: media_source)
    monkeypatch.setattr(
        runner, "detect_cuts", lambda *_a, **_kw: SimpleNamespace(status="unavailable")
    )
    monkeypatch.setattr(story, "_ffmpeg_available", lambda: True)
    monkeypatch.setattr(story, "_ffmpeg_version", lambda: "test")

    def extract(_media, _seek, path):
        path.write_bytes(b"test frame")
        return ["ffmpeg", "test"]

    monkeypatch.setattr(story, "_extract_frame", extract)
    result = runner.run_pipeline(media, tmp_path / "projects", evidence_mode="story", **kwargs)
    return json.loads(Path(result["story_plan_path"]).read_text(encoding="utf-8"))


def test_indexing_scans_the_opening_and_keeps_the_result_in_the_plan(tmp_path, monkeypatch):
    from pipeline import opening_teaser

    monkeypatch.setattr(opening_teaser, "detect_opening_teaser", lambda _media: TEASER)

    plan = _pipeline(tmp_path, monkeypatch)

    assert plan["opening_teaser"] == TEASER


def test_a_scope_that_starts_mid_film_does_not_look_for_a_teaser(tmp_path, monkeypatch):
    from pipeline import opening_teaser

    def refuse(_media):
        raise AssertionError("only a scope that starts at the beginning can see a teaser")

    monkeypatch.setattr(opening_teaser, "detect_opening_teaser", refuse)

    plan = _pipeline(tmp_path, monkeypatch, scope_in_seconds=10.0, scope_out_seconds=200.0)

    assert "opening_teaser" not in plan


def test_a_failed_scan_does_not_stop_indexing(tmp_path, monkeypatch):
    """The fake media is not a video, so the real scan reports itself unavailable."""
    plan = _pipeline(tmp_path, monkeypatch)

    assert plan["opening_teaser"]["status"] == "unavailable"
    assert Path(tmp_path / "projects").is_dir()
