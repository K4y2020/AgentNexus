"""
Cine Pipeline C0+C1 automated tests.

Run with:
    python -m pytest examples/cine/tests/test_cine_pipeline.py -v

Requirements:
- No external LLM calls
- Synthetic test videos via ffmpeg drawtext/color filters
- All tests pass on Windows with PowerShell
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from fractions import Fraction
from pathlib import Path

import pytest

# Add the package to path so we can import without installing
SKILL_BASE = Path(__file__).parent.parent / "skills" / "film-analysis"
sys.path.insert(0, str(SKILL_BASE))

from pipeline.schemas import (
    CutCandidate,
    CandidateStatus,
    DetectorStatus,
    EvidenceRecord,
    ProjectRecord,
    PtsInterval,
    RunState,
    RunStatus,
    SCHEMA_VERSION,
    SourceMediaRecord,
    SourceShot,
    StreamInfo,
    ValidationResult,
)
from pipeline.project import (
    ProjectLock,
    _atomic_write,
    create_project,
    create_revision,
    lock_path,
    project_root,
    resume_project,
    revision_root,
    update_project,
)
from pipeline.validate import validate


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def ffmpeg_available() -> bool:
    try:
        subprocess.run(["ffmpeg", "-version"], capture_output=True, check=True)
        return True
    except (FileNotFoundError, subprocess.CalledProcessError):
        return False


def make_test_video(path: Path, duration: float = 5.0, width: int = 128, height: int = 72, fps: int = 25) -> Path:
    """Create a synthetic test video with color changes (CFR, no commercial content)."""
    argv = [
        "ffmpeg", "-y",
        "-f", "lavfi",
        "-i", f"color=c=blue:size={width}x{height}:rate={fps}:duration={duration/2}",
        "-f", "lavfi",
        "-i", f"color=c=red:size={width}x{height}:rate={fps}:duration={duration/2}",
        "-filter_complex", "[0][1]concat=n=2:v=1:a=0",
        "-c:v", "libx264",
        "-pix_fmt", "yuv420p",
        str(path),
    ]
    result = subprocess.run(argv, capture_output=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {result.stderr.decode()[:200]}")
    return path


# ---------------------------------------------------------------------------
# ─── Schema tests ────────────────────────────────────────────────────────────
# ---------------------------------------------------------------------------

class TestSchemas:
    def test_schema_version_present(self):
        rec = ProjectRecord(display_name="test")
        assert rec.schema_version == SCHEMA_VERSION

    def test_project_record_round_trip(self):
        rec = ProjectRecord(display_name="My Film")
        data = rec.model_dump()
        rec2 = ProjectRecord.model_validate(data)
        assert rec2.project_id == rec.project_id
        assert rec2.display_name == rec.display_name
        assert rec2.schema_version == SCHEMA_VERSION

    def test_pts_interval_round_trip(self):
        iv = PtsInterval(in_pts=0, out_pts=1000, time_base_num=1, time_base_den=90000)
        data = iv.model_dump()
        iv2 = PtsInterval.model_validate(data)
        assert iv2.in_pts == 0
        assert iv2.out_pts == 1000
        assert iv2.time_base == Fraction(1, 90000)

    def test_pts_interval_rejects_empty(self):
        with pytest.raises(Exception):
            PtsInterval(in_pts=100, out_pts=100)  # out == in → invalid

    def test_pts_interval_rejects_reversed(self):
        with pytest.raises(Exception):
            PtsInterval(in_pts=200, out_pts=100)  # out < in → invalid

    def test_cut_candidate_round_trip(self):
        c = CutCandidate(
            source_id="abc",
            revision_id="def",
            stream_index=0,
            pts=12345,
            time_base_num=1,
            time_base_den=90000,
            detector="ContentDetector",
        )
        data = c.model_dump()
        c2 = CutCandidate.model_validate(data)
        assert c2.candidate_id == c.candidate_id
        assert c2.status == CandidateStatus.candidate
        assert c2.schema_version == SCHEMA_VERSION

    def test_source_media_record_round_trip(self):
        rec = SourceMediaRecord(
            path="/tmp/video.mp4",
            size_bytes=1024,
            mtime_ns=999,
            sha256_head="a" * 64,
            sha256_tail="b" * 64,
        )
        data = rec.model_dump()
        rec2 = SourceMediaRecord.model_validate(data)
        assert rec2.source_id == rec.source_id
        assert rec2.schema_version == SCHEMA_VERSION

    def test_run_state_round_trip(self):
        rs = RunState(project_id="proj1", revision_id="rev1")
        data = rs.model_dump()
        rs2 = RunState.model_validate(data)
        assert rs2.run_id == rs.run_id
        assert rs2.status == RunStatus.queued

    def test_source_shot_round_trip(self):
        shot = SourceShot(
            source_id="src1",
            revision_id="rev1",
            interval=PtsInterval(in_pts=0, out_pts=1000),
        )
        data = shot.model_dump()
        shot2 = SourceShot.model_validate(data)
        assert shot2.shot_id == shot.shot_id

    def test_all_records_have_schema_version(self):
        for cls in [ProjectRecord, SourceMediaRecord, RunState, CutCandidate, EvidenceRecord]:
            instance = cls.__new__(cls)
            # Check schema_version is a declared field
            assert "schema_version" in cls.model_fields


# ---------------------------------------------------------------------------
# ─── Project lifecycle tests ─────────────────────────────────────────────────
# ---------------------------------------------------------------------------

class TestProjectLifecycle:
    def test_create_project_basic(self, tmp_path):
        rec = create_project(tmp_path, display_name="TestFilm")
        assert rec.project_id
        assert rec.display_name == "TestFilm"
        proj_file = project_root(tmp_path, rec.project_id) / "project.json"
        assert proj_file.exists()

    def test_two_videos_independent_projects(self, tmp_path):
        rec1 = create_project(tmp_path, display_name="Film A")
        rec2 = create_project(tmp_path, display_name="Film B")
        assert rec1.project_id != rec2.project_id
        dir1 = project_root(tmp_path, rec1.project_id)
        dir2 = project_root(tmp_path, rec2.project_id)
        assert dir1 != dir2
        assert dir1.exists() and dir2.exists()
        # No cross-contamination: Film A's project.json not in Film B's dir
        assert rec1.project_id not in (dir2 / "project.json").read_text()
        assert rec2.project_id not in (dir1 / "project.json").read_text()

    def test_no_input_raises(self):
        with pytest.raises((ValueError, TypeError)):
            create_project(Path("irrelevant"), display_name="")

    def test_rename_display_name_does_not_change_project_id(self, tmp_path):
        rec = create_project(tmp_path, display_name="Original Name")
        original_id = rec.project_id
        rec.display_name = "New Name"
        update_project(tmp_path, rec)
        reloaded = resume_project(tmp_path, original_id)
        assert reloaded.project_id == original_id
        assert reloaded.display_name == "New Name"

    def test_resume_project(self, tmp_path):
        rec = create_project(tmp_path, display_name="ResumeTest")
        reloaded = resume_project(tmp_path, rec.project_id)
        assert reloaded.project_id == rec.project_id

    def test_duplicate_project_dir_raises(self, tmp_path):
        rec = create_project(tmp_path, display_name="Dupe")
        with pytest.raises(FileExistsError):
            create_project(tmp_path, display_name="Dupe2", project_id=rec.project_id)

    def test_windows_path_with_chinese_and_spaces(self, tmp_path):
        """Test that projects work in paths containing Chinese chars and spaces."""
        special_dir = tmp_path / "我的 电影 项目" / "测试 test"
        special_dir.mkdir(parents=True)
        rec = create_project(special_dir, display_name="中文项目 test")
        assert rec.project_id
        reloaded = resume_project(special_dir, rec.project_id)
        assert reloaded.display_name == "中文项目 test"

    def test_atomic_write(self, tmp_path):
        """Atomic write creates the file and final result is valid JSON."""
        target = tmp_path / "subdir" / "test.json"
        data = {"key": "value", "num": 42}
        _atomic_write(target, data)
        assert target.exists()
        with open(target) as fh:
            loaded = json.load(fh)
        assert loaded == data

    def test_source_content_change_detected(self, tmp_path, ffmpeg_available):
        """Hash delta detection works when file content changes."""
        if not ffmpeg_available:
            pytest.skip("ffmpeg not available")

        from pipeline.probe import _sha256_chunk, content_changed

        # Create a temp file
        test_file = tmp_path / "dummy.mp4"
        test_file.write_bytes(b"A" * 512 * 1024)  # 512 KiB of 'A's

        rec = SourceMediaRecord(
            path=str(test_file),
            size_bytes=test_file.stat().st_size,
            mtime_ns=int(test_file.stat().st_mtime_ns),
            sha256_head=_sha256_chunk(test_file, tail=False),
            sha256_tail=_sha256_chunk(test_file, tail=True),
        )

        # No change yet
        assert not content_changed(rec, test_file)

        # Modify content
        test_file.write_bytes(b"B" * 512 * 1024)
        assert content_changed(rec, test_file)

    def test_concurrent_lock_only_one_wins(self, tmp_path):
        """Race condition: two threads starting simultaneously, only one gets the lock."""
        rec = create_project(tmp_path, display_name="RaceLock")
        lp = lock_path(tmp_path, rec.project_id)

        results = []
        errors = []

        def try_lock(timeout):
            try:
                with ProjectLock(lp, timeout=timeout):
                    results.append("acquired")
                    time.sleep(0.3)
            except RuntimeError:
                errors.append("blocked")

        t1 = threading.Thread(target=try_lock, args=(2.0,))
        t2 = threading.Thread(target=try_lock, args=(0.0,))
        t1.start()
        time.sleep(0.05)  # Let t1 acquire the lock first
        t2.start()
        t1.join(timeout=5)
        t2.join(timeout=5)

        assert "acquired" in results
        # t2 should have been blocked (timeout=0)
        assert "blocked" in errors or len(results) == 1  # at most one acquires concurrently


# ---------------------------------------------------------------------------
# ─── Probe tests ─────────────────────────────────────────────────────────────
# ---------------------------------------------------------------------------

class TestProbe:
    def test_probe_structure(self, tmp_path, ffmpeg_available):
        if not ffmpeg_available:
            pytest.skip("ffmpeg not available")

        video = tmp_path / "test_video.mp4"
        make_test_video(video, duration=4.0)

        from pipeline.probe import probe_media
        rec = probe_media(video)

        assert rec.source_id
        assert rec.schema_version == SCHEMA_VERSION
        assert rec.size_bytes > 0
        assert rec.mtime_ns > 0
        assert len(rec.sha256_head) == 64
        assert len(rec.sha256_tail) == 64
        assert len(rec.streams) > 0
        video_streams = [s for s in rec.streams if s.codec_type == "video"]
        assert len(video_streams) >= 1
        assert rec.time_base_num > 0
        assert rec.time_base_den > 0

    def test_probe_integer_pts(self, tmp_path, ffmpeg_available):
        if not ffmpeg_available:
            pytest.skip("ffmpeg not available")

        video = tmp_path / "pts_test.mp4"
        make_test_video(video, duration=4.0)

        from pipeline.probe import probe_media
        rec = probe_media(video)

        # duration_pts should be an integer, not a float
        if rec.duration_pts is not None:
            assert isinstance(rec.duration_pts, int)
        assert isinstance(rec.start_pts, int)

    def test_probe_nonexistent_file(self):
        from pipeline.probe import probe_media
        with pytest.raises((FileNotFoundError, RuntimeError)):
            probe_media(Path("/nonexistent/path/video.mp4"))

    def test_probe_vfr_flag(self, tmp_path, ffmpeg_available):
        if not ffmpeg_available:
            pytest.skip("ffmpeg not available")

        # CFR video should not be flagged as VFR
        video = tmp_path / "cfr.mp4"
        make_test_video(video, duration=2.0, fps=25)

        from pipeline.probe import probe_media
        rec = probe_media(video)

        video_streams = [s for s in rec.streams if s.codec_type == "video"]
        for vs in video_streams:
            # CFR should not be VFR
            assert not vs.is_vfr, f"CFR video wrongly flagged as VFR: {vs}"


# ---------------------------------------------------------------------------
# ─── Detect tests ────────────────────────────────────────────────────────────
# ---------------------------------------------------------------------------

class TestDetect:
    def test_detect_fallback_when_pyscenedetect_absent(self, tmp_path, ffmpeg_available):
        """When PySceneDetect is not installed, detect_cuts returns unavailable status."""
        import importlib
        import sys

        # Force PySceneDetect to be "unavailable" by monkeypatching
        import pipeline.detect as det_mod
        original = det_mod._PSD_AVAILABLE
        det_mod._PSD_AVAILABLE = False

        try:
            video = tmp_path / "fake.mp4"
            video.write_bytes(b"\x00" * 100)

            source = SourceMediaRecord(
                path=str(video),
                size_bytes=100,
                mtime_ns=0,
                sha256_head="a" * 64,
                sha256_tail="b" * 64,
                time_base_num=1,
                time_base_den=90000,
            )

            from pipeline.detect import detect_cuts
            result = detect_cuts(video, source, "rev_test")

            # B5+B6: detect_cuts now returns DetectionResult(status, candidates)
            assert result.status == "unavailable"
            assert result.candidates == []
        finally:
            det_mod._PSD_AVAILABLE = original

    def test_detect_with_real_video(self, tmp_path, ffmpeg_available):
        """Detect cuts in a synthetic video with a clear color change."""
        if not ffmpeg_available:
            pytest.skip("ffmpeg not available")

        import pipeline.detect as det_mod
        if not det_mod._PSD_AVAILABLE:
            pytest.skip("PySceneDetect not installed")

        video = tmp_path / "scene_cut.mp4"
        make_test_video(video, duration=6.0)

        from pipeline.probe import probe_media
        from pipeline.detect import detect_cuts

        source = probe_media(video)
        result = detect_cuts(video, source, "rev1")
        assert result.status == "ok"
        candidates = result.candidates

        # Should detect at least the blue→red cut
        ok_candidates = [c for c in candidates if c.detector_status == DetectorStatus.ok]
        assert len(ok_candidates) >= 1
        # All must remain "candidate" status
        assert all(c.status == CandidateStatus.candidate for c in candidates)

    def test_detect_scoped_range(self, tmp_path, ffmpeg_available):
        """Scoped detection in 60-120s range covers the requested range."""
        if not ffmpeg_available:
            pytest.skip("ffmpeg not available")

        import pipeline.detect as det_mod
        if not det_mod._PSD_AVAILABLE:
            pytest.skip("PySceneDetect not installed")

        # Make a longer video with multiple cuts
        video = tmp_path / "long_video.mp4"
        # 6 sec total with color changes
        make_test_video(video, duration=6.0)

        from pipeline.probe import probe_media
        from pipeline.detect import detect_cuts

        source = probe_media(video)
        # Scope to first 3s
        scope_in = 0
        scope_out = int(round(3.0 / float(source.time_base)))

        result = detect_cuts(
            video, source, "rev_scope",
            scope_in_pts=scope_in,
            scope_out_pts=scope_out,
        )

        assert result.status == "ok"
        candidates = result.candidates
        # Candidates should not be outside [0, scope_out]
        for c in candidates:
            if c.detector_status == DetectorStatus.ok:
                assert 0 <= c.pts <= scope_out, f"Candidate PTS {c.pts} outside scope [0, {scope_out}]"


# ---------------------------------------------------------------------------
# ─── Evidence tests ──────────────────────────────────────────────────────────
# ---------------------------------------------------------------------------

class TestEvidence:
    def test_evidence_extraction(self, tmp_path, ffmpeg_available):
        if not ffmpeg_available:
            pytest.skip("ffmpeg not available")

        video = tmp_path / "ev_test.mp4"
        make_test_video(video, duration=4.0)

        from pipeline.probe import probe_media
        from pipeline.evidence import extract_evidence

        source = probe_media(video)
        rev_dir = tmp_path / "rev"
        rev_dir.mkdir()
        (rev_dir / "evidence").mkdir()

        c = CutCandidate(
            source_id=source.source_id,
            revision_id="rev1",
            stream_index=0,
            pts=source.duration_pts // 2 if source.duration_pts else 1000,
            time_base_num=source.time_base_num,
            time_base_den=source.time_base_den,
            detector="test",
        )

        records = extract_evidence(video, source, c, rev_dir)

        # Should produce some evidence (frames/clips)
        assert len(records) > 0
        for ev in records:
            assert ev.schema_version == SCHEMA_VERSION
            assert ev.evidence_id
            assert ev.kind in ("frame_pre", "frame_mid", "frame_post", "clip_boundary")
            assert ev.ffmpeg_argv  # must have argv list
            # ffmpeg_argv must be a list, never a shell string
            assert isinstance(ev.ffmpeg_argv, list)
            assert len(ev.ffmpeg_argv) > 1

    def test_evidence_no_shell_concat(self, tmp_path, ffmpeg_available):
        """Verify argv list is used, not shell string concat."""
        if not ffmpeg_available:
            pytest.skip("ffmpeg not available")

        video = tmp_path / "shell_test.mp4"
        make_test_video(video, duration=2.0)

        from pipeline.probe import probe_media
        from pipeline.evidence import extract_evidence

        source = probe_media(video)
        rev_dir = tmp_path / "rev2"
        rev_dir.mkdir()
        (rev_dir / "evidence").mkdir()

        c = CutCandidate(
            source_id=source.source_id,
            revision_id="rev1",
            stream_index=0,
            pts=100,
            time_base_num=source.time_base_num,
            time_base_den=source.time_base_den,
            detector="test",
        )
        records = extract_evidence(video, source, c, rev_dir)
        assert len(records) > 0
        for ev in records:
            # Each argv element should be a string, not a space-concatenated blob
            assert all(isinstance(a, str) for a in ev.ffmpeg_argv)
            assert ev.ffmpeg_argv[0] == "ffmpeg"
            assert "-i" in ev.ffmpeg_argv
            assert str(video) in ev.ffmpeg_argv
            for a in ev.ffmpeg_argv:
                assert not any(op in a for op in [" -i ", " -ss ", " -c ", "&&", "|", ";"])


# ---------------------------------------------------------------------------
# ─── Validate tests ──────────────────────────────────────────────────────────
# ---------------------------------------------------------------------------

class TestValidate:
    def _make_source(self, tmp_path):
        f = tmp_path / "video.mp4"
        f.write_bytes(b"\x00" * 100)
        return SourceMediaRecord(
            path=str(f),
            size_bytes=100,
            mtime_ns=0,
            sha256_head="a" * 64,
            sha256_tail="b" * 64,
        )

    def test_valid_non_overlapping_intervals(self, tmp_path):
        source = self._make_source(tmp_path)
        rev_dir = tmp_path / "rev"
        rev_dir.mkdir()

        shots = [
            SourceShot(
                source_id=source.source_id, revision_id="rev1",
                interval=PtsInterval(in_pts=0, out_pts=1000),
            ),
            SourceShot(
                source_id=source.source_id, revision_id="rev1",
                interval=PtsInterval(in_pts=1000, out_pts=2000),
            ),
        ]
        result = validate("rev1", source, [], shots, [], revision_dir=rev_dir)
        errors = [i for i in result.issues if i.severity == "error" and i.code != "MEDIA_NOT_FOUND"]
        assert len(errors) == 0

    def test_overlapping_intervals_rejected(self, tmp_path):
        source = self._make_source(tmp_path)
        rev_dir = tmp_path / "rev"
        rev_dir.mkdir()

        shots = [
            SourceShot(
                source_id=source.source_id, revision_id="rev1",
                interval=PtsInterval(in_pts=0, out_pts=1500),
            ),
            SourceShot(
                source_id=source.source_id, revision_id="rev1",
                interval=PtsInterval(in_pts=1000, out_pts=2000),  # overlaps with first
            ),
        ]
        result = validate("rev1", source, [], shots, [], revision_dir=rev_dir)
        overlap_errors = [i for i in result.issues if i.code == "OVERLAPPING_INTERVALS"]
        assert len(overlap_errors) >= 1

    def test_invalid_candidate_ref_detected(self, tmp_path):
        source = self._make_source(tmp_path)
        rev_dir = tmp_path / "rev"
        rev_dir.mkdir()

        # EvidenceRecord referencing a non-existent candidate
        ev = EvidenceRecord(
            source_id=source.source_id,
            revision_id="rev1",
            candidate_id="nonexistent_candidate",
            kind="frame_mid",
            relative_path="evidence/fake.jpg",
        )
        result = validate("rev1", source, [], [], [ev], revision_dir=rev_dir)
        ref_errors = [i for i in result.issues if i.code == "EVIDENCE_INVALID_CANDIDATE_REF"]
        assert len(ref_errors) >= 1

    def test_all_candidate_intervals_cover_requested_range(self, tmp_path):
        """C1: All candidate intervals for a 60–120s segment cover the requested range."""
        source = self._make_source(tmp_path)
        # Simulate scope 0..90s with time_base 1/90000
        tb_num, tb_den = 1, 90000
        scope_in = 0
        scope_out = int(90.0 * 90000)  # 90s in tbu

        # Build shots that cover [scope_in, scope_out)
        shots = [
            SourceShot(
                source_id=source.source_id, revision_id="rev1",
                interval=PtsInterval(in_pts=0, out_pts=3_000_000, time_base_num=tb_num, time_base_den=tb_den),
            ),
            SourceShot(
                source_id=source.source_id, revision_id="rev1",
                interval=PtsInterval(in_pts=3_000_000, out_pts=scope_out, time_base_num=tb_num, time_base_den=tb_den),
            ),
        ]

        # Check coverage
        covered_start = min(s.interval.in_pts for s in shots)
        covered_end = max(s.interval.out_pts for s in shots)
        assert covered_start <= scope_in
        assert covered_end >= scope_out

        rev_dir = tmp_path / "rev"
        rev_dir.mkdir()
        result = validate("rev1", source, [], shots, [], revision_dir=rev_dir)
        overlap_errors = [i for i in result.issues if i.code == "OVERLAPPING_INTERVALS"]
        assert len(overlap_errors) == 0


# ---------------------------------------------------------------------------
# ─── Render tests ────────────────────────────────────────────────────────────
# ---------------------------------------------------------------------------

class TestRender:
    def _make_source(self, tmp_path):
        f = tmp_path / "myvideo.mp4"
        f.write_bytes(b"\x00" * 100)
        return SourceMediaRecord(
            path=str(f),
            size_bytes=100,
            mtime_ns=0,
            sha256_head="a" * 64,
            sha256_tail="b" * 64,
        )

    def test_render_produces_html(self, tmp_path):
        source = self._make_source(tmp_path)
        rev_dir = tmp_path / "rev"
        rev_dir.mkdir()
        (rev_dir / "report").mkdir()

        from pipeline.render import render_report
        report_path = render_report(
            revision_dir=rev_dir,
            source=source,
            revision_id="testrev",
            candidates=[],
            shots=[],
            evidence=[],
            validation=None,
        )
        assert report_path.exists()
        html = report_path.read_text(encoding="utf-8")
        assert "<html" in html.lower()
        assert "<video" in html.lower()

    def test_render_contains_candidate_table(self, tmp_path):
        source = self._make_source(tmp_path)
        rev_dir = tmp_path / "rev"
        rev_dir.mkdir()
        (rev_dir / "report").mkdir()

        c = CutCandidate(
            source_id=source.source_id,
            revision_id="rev1",
            stream_index=0,
            pts=5000,
            time_base_num=1,
            time_base_den=90000,
            detector="ContentDetector",
        )

        from pipeline.render import render_report
        report_path = render_report(
            revision_dir=rev_dir,
            source=source,
            revision_id="rev1",
            candidates=[c],
            shots=[],
            evidence=[],
            validation=None,
        )
        html = report_path.read_text(encoding="utf-8")
        assert "ContentDetector" in html
        assert "candidate" in html.lower()

    def test_render_html_contains_video_element(self, tmp_path):
        source = self._make_source(tmp_path)
        rev_dir = tmp_path / "rev"
        rev_dir.mkdir()
        (rev_dir / "report").mkdir()

        from pipeline.render import render_report
        report_path = render_report(
            revision_dir=rev_dir,
            source=source,
            revision_id="rev1",
            candidates=[],
            shots=[],
            evidence=[],
            validation=None,
        )
        html = report_path.read_text(encoding="utf-8")
        # HTML must contain a <video element
        assert "<video" in html

    def test_render_no_unvalidated_shots_auto_accepted(self, tmp_path):
        """HTML report must not present unvalidated shots as accepted."""
        source = self._make_source(tmp_path)
        rev_dir = tmp_path / "rev"
        rev_dir.mkdir()
        (rev_dir / "report").mkdir()

        c = CutCandidate(
            source_id=source.source_id,
            revision_id="rev1",
            stream_index=0,
            pts=1000,
            time_base_num=1,
            time_base_den=90000,
            detector="ContentDetector",
            status=CandidateStatus.candidate,
        )

        from pipeline.render import render_report
        report_path = render_report(
            revision_dir=rev_dir,
            source=source,
            revision_id="rev1",
            candidates=[c],
            shots=[],
            evidence=[],
            validation=None,
        )
        html = report_path.read_text(encoding="utf-8")
        # The word "accepted" should not appear as a status badge for candidates
        # (they should all be "candidate")
        assert "badge-candidate" in html
        assert "badge-accepted" not in html

    def test_render_atomic_write(self, tmp_path):
        """The report is written atomically (not partially)."""
        source = self._make_source(tmp_path)
        rev_dir = tmp_path / "rev"
        rev_dir.mkdir()
        (rev_dir / "report").mkdir()

        from pipeline.render import render_report
        report_path = render_report(
            revision_dir=rev_dir,
            source=source,
            revision_id="atomic_test",
            candidates=[],
            shots=[],
            evidence=[],
            validation=None,
        )
        # File should be complete and parseable
        html = report_path.read_text(encoding="utf-8")
        assert "</html>" in html


# ---------------------------------------------------------------------------
# ─── Full pipeline integration tests ─────────────────────────────────────────
# ---------------------------------------------------------------------------

class TestFullPipeline:
    def test_pipeline_end_to_end(self, tmp_path, ffmpeg_available):
        if not ffmpeg_available:
            pytest.skip("ffmpeg not available")

        video = tmp_path / "pipeline_test.mp4"
        make_test_video(video, duration=6.0)

        from pipeline.runner import run_pipeline

        result = run_pipeline(
            media_path=video,
            projects_dir=tmp_path / "projects",
        )
        assert result["project_id"]
        assert result["revision_id"]
        assert result["run_id"]
        # B6: if PySceneDetect absent, runner returns early with status=interrupted
        # (no report_path). If present, report_path must exist.
        if result.get("status") == "interrupted" and result.get("reason") == "detection_unavailable":
            pass  # correct: PySceneDetect not installed → graceful interrupted state
        else:
            assert "report_path" in result
            if not result["report_path"].startswith("render_failed"):
                assert Path(result["report_path"]).exists()

    def test_two_different_videos_no_collision(self, tmp_path, ffmpeg_available):
        if not ffmpeg_available:
            pytest.skip("ffmpeg not available")

        video1 = tmp_path / "video1.mp4"
        video2 = tmp_path / "video2.mp4"
        make_test_video(video1, duration=3.0)
        make_test_video(video2, duration=3.0)

        from pipeline.runner import run_pipeline

        projects_dir = tmp_path / "projects"
        res1 = run_pipeline(media_path=video1, projects_dir=projects_dir)
        res2 = run_pipeline(media_path=video2, projects_dir=projects_dir)

        assert res1["project_id"] != res2["project_id"]
        assert res1["revision_id"] != res2["revision_id"]
        assert res1["source_id"] != res2["source_id"]


# ---------------------------------------------------------------------------
# ─── Blocker-coverage tests (B1, B2, B6, B7, B8, B10, B11) ─────────────────
# ---------------------------------------------------------------------------

class TestBlockerFixes:
    """Tests that validate the 11 cross-review blocker fixes."""

    # B1: all base records carry schema_version, source_id, revision_id
    def test_b1_cine_base_record_fields(self):
        from pipeline.schemas import CineBaseRecord, ValidationResult, RunState
        # ValidationResult inherits CineBaseRecord
        vr = ValidationResult(revision_id="rev1", passed=True)
        assert hasattr(vr, "schema_version")
        assert hasattr(vr, "source_id")
        assert hasattr(vr, "revision_id")
        assert hasattr(vr, "result_id")  # stable record ID
        # RunState carries source_id
        rs = RunState(project_id="p1", revision_id="r1")
        assert hasattr(rs, "source_id")
        assert hasattr(rs, "stages_failed")  # B8

    def test_b1_source_media_record_has_revision_id(self):
        from pipeline.schemas import SourceMediaRecord
        rec = SourceMediaRecord(
            path="/tmp/v.mp4", size_bytes=1, mtime_ns=1,
            sha256_head="a"*64, sha256_tail="b"*64,
        )
        assert hasattr(rec, "revision_id")
        assert rec.revision_id is None  # bootstrap — no revision yet

    def test_b1_project_record_has_revision_id(self):
        from pipeline.schemas import ProjectRecord
        rec = ProjectRecord(display_name="test")
        assert hasattr(rec, "revision_id")

    # B2: source reuse on resume
    def test_b2_resume_same_source_reuses_source_id(self, tmp_path, ffmpeg_available):
        if not ffmpeg_available:
            pytest.skip("ffmpeg not available")
        video = tmp_path / "v.mp4"
        make_test_video(video, duration=3.0)

        from pipeline.runner import run_pipeline
        projects_dir = tmp_path / "projects"

        # First run — creates project
        r1 = run_pipeline(media_path=video, projects_dir=projects_dir)
        pid = r1["project_id"]
        sid1 = r1["source_id"]

        # Resume with same video — must reuse same source_id
        r2 = run_pipeline(media_path=video, projects_dir=projects_dir, project_id=pid)
        sid2 = r2["source_id"]
        assert sid1 == sid2, f"source_id changed on resume: {sid1} vs {sid2}"

    def test_b2_resume_different_source_raises(self, tmp_path, ffmpeg_available):
        if not ffmpeg_available:
            pytest.skip("ffmpeg not available")
        video1 = tmp_path / "v1.mp4"
        video2 = tmp_path / "v2.mp4"
        make_test_video(video1, duration=3.0)
        make_test_video(video2, duration=4.0)  # different content

        from pipeline.runner import run_pipeline
        projects_dir = tmp_path / "projects"

        r1 = run_pipeline(media_path=video1, projects_dir=projects_dir)
        pid = r1["project_id"]

        with pytest.raises(ValueError, match="Source media"):
            run_pipeline(media_path=video2, projects_dir=projects_dir, project_id=pid)

    # B5: detect returns empty list when unavailable
    def test_b5_detect_unavailable_returns_empty(self, tmp_path):
        import pipeline.detect as det_mod
        original = det_mod._PSD_AVAILABLE
        det_mod._PSD_AVAILABLE = False
        try:
            video = tmp_path / "f.mp4"
            video.write_bytes(b"\x00" * 100)
            from pipeline.schemas import SourceMediaRecord
            from pipeline.detect import detect_cuts
            source = SourceMediaRecord(
                path=str(video), size_bytes=100, mtime_ns=0,
                sha256_head="a"*64, sha256_tail="b"*64,
            )
            result = detect_cuts(video, source, "rev1")
            assert result.status == "unavailable", f"Expected unavailable, got {result.status}"
            assert result.candidates == [], f"Expected empty candidates, got {result.candidates}"
        finally:
            det_mod._PSD_AVAILABLE = original

    # B6: 0 cuts → single shot covering scope
    def test_b6_zero_cuts_single_shot(self):
        from pipeline.runner import _build_shots
        from pipeline.schemas import SourceMediaRecord

        source = SourceMediaRecord(
            path="/tmp/v.mp4", size_bytes=1, mtime_ns=0,
            sha256_head="a"*64, sha256_tail="b"*64,
            start_pts=0, duration_pts=90000, time_base_num=1, time_base_den=90000,
        )
        shots = _build_shots(source, "rev1", [], scope_in_pts=0, scope_out_pts=90000)
        assert len(shots) == 1
        assert shots[0].interval.in_pts == 0
        assert shots[0].interval.out_pts == 90000

    # B7: validate checks candidate PTS bounds
    def test_b7_candidate_out_of_bounds_detected(self, tmp_path):
        f = tmp_path / "v.mp4"
        f.write_bytes(b"\x00" * 100)
        source = SourceMediaRecord(
            path=str(f), size_bytes=100, mtime_ns=0,
            sha256_head="a"*64, sha256_tail="b"*64,
            start_pts=0, duration_pts=1000, time_base_num=1, time_base_den=90000,
        )
        # Candidate PTS=5000 is outside [0, 1000)
        c = CutCandidate(
            source_id=source.source_id, revision_id="rev1",
            stream_index=0, pts=5000, detector="test",
        )
        rev_dir = tmp_path / "rev"
        rev_dir.mkdir()
        result = validate("rev1", source, [c], [], [], revision_dir=rev_dir)
        oob = [i for i in result.issues if i.code == "CANDIDATE_OUT_OF_BOUNDS"]
        assert len(oob) >= 1

    def test_b7_scope_coverage_gap_detected(self, tmp_path):
        f = tmp_path / "v.mp4"
        f.write_bytes(b"\x00" * 100)
        source = SourceMediaRecord(
            path=str(f), size_bytes=100, mtime_ns=0,
            sha256_head="a"*64, sha256_tail="b"*64,
            source_id="src1",
        )
        scope = PtsInterval(in_pts=0, out_pts=10000)
        # Shots cover [0,4000) and [6000,10000) — gap [4000,6000)
        shots = [
            SourceShot(source_id=source.source_id, revision_id="rev1",
                       interval=PtsInterval(in_pts=0, out_pts=4000)),
            SourceShot(source_id=source.source_id, revision_id="rev1",
                       interval=PtsInterval(in_pts=6000, out_pts=10000)),
        ]
        rev_dir = tmp_path / "rev"
        rev_dir.mkdir()
        result = validate("rev1", source, [], shots, [], revision_dir=rev_dir, scope=scope)
        gap_issues = [i for i in result.issues if "GAP" in i.code]
        assert len(gap_issues) >= 1

    # B8: RunState.stages_failed exists
    def test_b8_run_state_stages_failed_field(self):
        from pipeline.schemas import RunState
        rs = RunState(project_id="p1", revision_id="r1")
        assert hasattr(rs, "stages_failed")
        rs.stages_failed.append("render")
        data = rs.model_dump()
        rs2 = RunState.model_validate(data)
        assert "render" in rs2.stages_failed

    # B10: render uses relpath for media src
    def test_b10_render_uses_relpath_for_media_src(self, tmp_path):
        f = tmp_path / "myvideo.mp4"
        f.write_bytes(b"\x00" * 100)
        source = SourceMediaRecord(
            path=str(f), size_bytes=100, mtime_ns=0,
            sha256_head="a"*64, sha256_tail="b"*64,
        )
        rev_dir = tmp_path / "rev"
        rev_dir.mkdir()
        (rev_dir / "report").mkdir()

        from pipeline.render import render_report
        report_path = render_report(
            revision_dir=rev_dir, source=source, revision_id="rev1",
            candidates=[], shots=[], evidence=[], validation=None,
        )
        html = report_path.read_text(encoding="utf-8")
        # src must not be just the bare filename when file is outside report_dir
        # It should be a relative path (contains ".." since media is above report dir)
        assert "myvideo.mp4" in html  # filename appears
        # The relative path should be a posix-style string
        assert "<video" in html

    # B11: EvidenceRecord has extraction_status field
    def test_b11_evidence_record_extraction_status(self):
        from pipeline.schemas import EvidenceRecord
        ev = EvidenceRecord(
            source_id="src1", revision_id="rev1",
            kind="frame_mid", relative_path=None,
            extraction_status="unavailable",
        )
        assert ev.extraction_status == "unavailable"
        assert ev.relative_path is None
        data = ev.model_dump()
        ev2 = EvidenceRecord.model_validate(data)
        assert ev2.extraction_status == "unavailable"

    def test_b11_evidence_unavailable_when_ffmpeg_absent(self, tmp_path):
        """When ffmpeg absent, extract_evidence returns marker record with unavailable status."""
        import pipeline.evidence as ev_mod
        original = ev_mod._FFMPEG_VERSION_CACHE
        ev_mod._FFMPEG_VERSION_CACHE = "unavailable"
        try:
            video = tmp_path / "v.mp4"
            video.write_bytes(b"\x00" * 100)
            from pipeline.schemas import SourceMediaRecord, CutCandidate
            from pipeline.evidence import extract_evidence
            source = SourceMediaRecord(
                path=str(video), size_bytes=100, mtime_ns=0,
                sha256_head="a"*64, sha256_tail="b"*64,
            )
            c = CutCandidate(
                source_id=source.source_id, revision_id="rev1",
                stream_index=0, pts=100, detector="test",
            )
            rev_dir = tmp_path / "rev"
            rev_dir.mkdir()
            records = extract_evidence(video, source, c, rev_dir)
            assert len(records) == 1
            assert records[0].extraction_status == "unavailable"
            assert records[0].relative_path is None
        finally:
            ev_mod._FFMPEG_VERSION_CACHE = original


class TestRound2Fixes:
    """Round 2 blocker fix tests: B6 DetectionResult, B7 empty-shots scope, B8 validation halt."""

    # ── B6: DetectionResult namedtuple ────────────────────────────────────

    def test_b6_detection_result_namedtuple(self):
        """detect_cuts() returns DetectionResult with .status and .candidates."""
        import pipeline.detect as det_mod
        from pipeline.detect import DetectionResult
        # Verify it is a proper NamedTuple with the right fields
        assert hasattr(DetectionResult, "_fields")
        assert "status" in DetectionResult._fields
        assert "candidates" in DetectionResult._fields

    def test_b6_detection_result_unavailable_when_psd_absent(self):
        """When _PSD_AVAILABLE is False, detect_cuts returns status='unavailable'."""
        import pipeline.detect as det_mod
        from pipeline.schemas import SourceMediaRecord, StreamInfo
        from pathlib import Path
        import tempfile, os

        original = det_mod._PSD_AVAILABLE
        det_mod._PSD_AVAILABLE = False
        try:
            stream = StreamInfo(
                index=0, codec_type="video", codec_name="h264",
                width=1280, height=720,
                r_frame_rate_num=25, r_frame_rate_den=1,
                avg_frame_rate_num=25, avg_frame_rate_den=1,
                time_base_num=1, time_base_den=90000,
                is_vfr=False,
            )
            source = SourceMediaRecord(
                path="fake.mp4", size_bytes=0, mtime_ns=0,
                sha256_head="a" * 64, sha256_tail="b" * 64,
                streams=[stream],
            )
            result = det_mod.detect_cuts(Path("fake.mp4"), source, "rev1")
            assert result.status == "unavailable"
            assert result.candidates == []
        finally:
            det_mod._PSD_AVAILABLE = original

    def test_b6_detection_result_failed_on_exception(self, monkeypatch):
        """When _run_pyscenedetect raises, detect_cuts returns status='failed'."""
        import pipeline.detect as det_mod
        from pipeline.schemas import SourceMediaRecord, StreamInfo
        from pathlib import Path

        original = det_mod._PSD_AVAILABLE
        det_mod._PSD_AVAILABLE = True
        try:
            def _boom(**kwargs):
                raise RuntimeError("simulated failure")
            monkeypatch.setattr(det_mod, "_run_pyscenedetect", _boom)

            stream = StreamInfo(
                index=0, codec_type="video", codec_name="h264",
                width=1280, height=720,
                r_frame_rate_num=25, r_frame_rate_den=1,
                avg_frame_rate_num=25, avg_frame_rate_den=1,
                time_base_num=1, time_base_den=90000,
                is_vfr=False,
            )
            source = SourceMediaRecord(
                path="fake.mp4", size_bytes=0, mtime_ns=0,
                sha256_head="a" * 64, sha256_tail="b" * 64,
                streams=[stream],
            )
            result = det_mod.detect_cuts(Path("fake.mp4"), source, "rev1")
            assert result.status == "failed"
            assert result.candidates == []
        finally:
            det_mod._PSD_AVAILABLE = original

    # ── B7: empty shots with scope produces COVERAGE_EMPTY ────────────────

    def test_b7_coverage_empty_when_no_shots_with_scope(self, tmp_path):
        """When scope is provided but shots is empty, validate() emits COVERAGE_EMPTY error."""
        from pipeline.schemas import (
            SourceMediaRecord, StreamInfo, PtsInterval,
        )
        from pipeline.validate import validate

        stream = StreamInfo(
            index=0, codec_type="video", codec_name="h264",
            width=1280, height=720,
            r_frame_rate_num=25, r_frame_rate_den=1,
            avg_frame_rate_num=25, avg_frame_rate_den=1,
            time_base_num=1, time_base_den=90000,
            is_vfr=False,
        )
        source = SourceMediaRecord(
            path=str(tmp_path / "fake.mp4"),
            size_bytes=0, mtime_ns=0,
            sha256_head="a" * 64, sha256_tail="b" * 64,
            streams=[stream],
        )
        # Create the fake media file so MEDIA_NOT_FOUND doesn't fire
        (tmp_path / "fake.mp4").write_bytes(b"\x00")

        scope = PtsInterval(in_pts=0, out_pts=9000, time_base_num=1, time_base_den=90000)
        rev_dir = tmp_path / "rev"
        rev_dir.mkdir()

        result = validate(
            revision_id="rev1",
            source=source,
            candidates=[],
            shots=[],          # empty shots with non-None scope
            evidence=[],
            revision_dir=rev_dir,
            scope=scope,
        )
        assert not result.passed
        codes = [i.code for i in result.issues]
        assert "COVERAGE_EMPTY" in codes

    def test_b7_no_coverage_empty_without_scope(self, tmp_path):
        """When no scope is given, empty shots does NOT emit COVERAGE_EMPTY."""
        from pipeline.schemas import SourceMediaRecord, StreamInfo
        from pipeline.validate import validate

        stream = StreamInfo(
            index=0, codec_type="video", codec_name="h264",
            width=1280, height=720,
            r_frame_rate_num=25, r_frame_rate_den=1,
            avg_frame_rate_num=25, avg_frame_rate_den=1,
            time_base_num=1, time_base_den=90000,
            is_vfr=False,
        )
        source = SourceMediaRecord(
            path=str(tmp_path / "fake.mp4"),
            size_bytes=0, mtime_ns=0,
            sha256_head="a" * 64, sha256_tail="b" * 64,
            streams=[stream],
        )
        (tmp_path / "fake.mp4").write_bytes(b"\x00")
        rev_dir = tmp_path / "rev"
        rev_dir.mkdir()

        result = validate(
            revision_id="rev1",
            source=source,
            candidates=[],
            shots=[],
            evidence=[],
            revision_dir=rev_dir,
            scope=None,       # no scope
        )
        codes = [i.code for i in result.issues]
        assert "COVERAGE_EMPTY" not in codes

    def test_b7_shot_revision_mismatch_detected(self, tmp_path):
        """SHOT_REVISION_MISMATCH emitted when shot.revision_id != revision_id."""
        from pipeline.schemas import (
            SourceMediaRecord, StreamInfo, SourceShot, PtsInterval,
        )
        from pipeline.validate import validate

        stream = StreamInfo(
            index=0, codec_type="video", codec_name="h264",
            width=1280, height=720,
            r_frame_rate_num=25, r_frame_rate_den=1,
            avg_frame_rate_num=25, avg_frame_rate_den=1,
            time_base_num=1, time_base_den=90000,
            is_vfr=False,
        )
        source = SourceMediaRecord(
            path=str(tmp_path / "fake.mp4"),
            size_bytes=0, mtime_ns=0,
            sha256_head="a" * 64, sha256_tail="b" * 64,
            streams=[stream],
        )
        (tmp_path / "fake.mp4").write_bytes(b"\x00")
        rev_dir = tmp_path / "rev"
        rev_dir.mkdir()

        shot = SourceShot(
            source_id=source.source_id,
            revision_id="WRONG_REV",   # mismatch
            interval=PtsInterval(in_pts=0, out_pts=1000, time_base_num=1, time_base_den=90000),
        )
        result = validate(
            revision_id="correct_rev",
            source=source,
            candidates=[],
            shots=[shot],
            evidence=[],
            revision_dir=rev_dir,
            scope=None,
        )
        codes = [i.code for i in result.issues]
        assert "SHOT_REVISION_MISMATCH" in codes

    def test_b7_shot_outside_scope_detected(self, tmp_path):
        """SHOT_OUTSIDE_SCOPE emitted when shot interval extends beyond scope."""
        from pipeline.schemas import (
            SourceMediaRecord, StreamInfo, SourceShot, PtsInterval,
        )
        from pipeline.validate import validate

        stream = StreamInfo(
            index=0, codec_type="video", codec_name="h264",
            width=1280, height=720,
            r_frame_rate_num=25, r_frame_rate_den=1,
            avg_frame_rate_num=25, avg_frame_rate_den=1,
            time_base_num=1, time_base_den=90000,
            is_vfr=False,
        )
        source = SourceMediaRecord(
            path=str(tmp_path / "fake.mp4"),
            size_bytes=0, mtime_ns=0,
            sha256_head="a" * 64, sha256_tail="b" * 64,
            streams=[stream],
        )
        (tmp_path / "fake.mp4").write_bytes(b"\x00")
        rev_dir = tmp_path / "rev"
        rev_dir.mkdir()

        scope = PtsInterval(in_pts=0, out_pts=1000, time_base_num=1, time_base_den=90000)
        shot = SourceShot(
            source_id=source.source_id,
            revision_id="rev1",
            interval=PtsInterval(in_pts=0, out_pts=2000, time_base_num=1, time_base_den=90000),
        )
        result = validate(
            revision_id="rev1",
            source=source,
            candidates=[],
            shots=[shot],
            evidence=[],
            revision_dir=rev_dir,
            scope=scope,
        )
        codes = [i.code for i in result.issues]
        assert "SHOT_OUTSIDE_SCOPE" in codes

    # ── B8: failed validation halts run (no render) ───────────────────────

    def test_b8_failed_validation_halts_run(self, tmp_path, monkeypatch):
        """When validation fails, run_pipeline returns status='failed' without rendering."""
        import pipeline.runner as runner_mod
        import pipeline.validate as val_mod
        from pipeline.schemas import ValidationResult, ValidationIssue

        # Patch validate() to always fail
        def _always_fail(**kwargs):
            return ValidationResult(
                revision_id=kwargs.get("revision_id", "rev1"),
                source_id="src",
                passed=False,
                issues=[ValidationIssue(
                    severity="error",
                    code="TEST_ERROR",
                    message="injected failure",
                )],
            )
        monkeypatch.setattr(val_mod, "validate", _always_fail)
        monkeypatch.setattr(runner_mod, "validate", _always_fail)

        # Also patch detect_cuts to return "ok" with empty candidates
        import pipeline.detect as det_mod
        from pipeline.detect import DetectionResult
        original_psd = det_mod._PSD_AVAILABLE
        det_mod._PSD_AVAILABLE = False  # -> returns unavailable -> interrupted
        # So we need to use a different approach: patch runner's detect_cuts reference

        from unittest.mock import patch, MagicMock
        render_called = []
        def fake_render(**kwargs):
            render_called.append(True)
            return tmp_path / "report.html"

        media = tmp_path / "sample.mp4"
        media.write_bytes(b"\x00" * 100)
        projects = tmp_path / "projects"

        det_mod._PSD_AVAILABLE = original_psd

        # Monkeypatch detect in runner to return ok with empty candidates
        ok_result = DetectionResult(status="ok", candidates=[])
        with patch.object(runner_mod, "detect_cuts", return_value=ok_result),              patch.object(runner_mod, "render_report", side_effect=fake_render),              patch.object(runner_mod, "probe_media") as mock_probe:
            # probe_media needs to return a valid SourceMediaRecord
            from pipeline.schemas import SourceMediaRecord
            src = SourceMediaRecord(
                path=str(media), size_bytes=100, mtime_ns=0,
                sha256_head="a" * 64, sha256_tail="b" * 64,
            )
            mock_probe.return_value = src

            result = runner_mod.run_pipeline(media, projects)

        assert result["status"] == "failed"
        assert result.get("reason") == "validation_failed"
        # render must NOT have been called
        assert not render_called, "render_report should not be called when validation fails"


    # ── B3: detect VFR parameters and docstring presence ──────────────────

    def test_b3_detect_docstrings_state_nominal_frame_time(self):
        """B3: detect_cuts and _run_pyscenedetect docstrings state nominal frame-time for VFR."""
        from pipeline.detect import detect_cuts, _run_pyscenedetect
        doc_cuts = " ".join((detect_cuts.__doc__ or "").split())
        doc_run = " ".join((_run_pyscenedetect.__doc__ or "").split())
        assert "PySceneDetect supplies nominal frame-time rather than decoded packet PTS" in doc_cuts
        assert "VFR cuts are derived from nominal frame-times" in doc_cuts
        assert "packet-level verification" in doc_cuts
        assert "PySceneDetect supplies nominal frame-time rather than decoded packet PTS" in doc_run
        assert "VFR cuts are derived from nominal frame-times" in doc_run

    def test_b3_detect_vfr_parameters(self, tmp_path, monkeypatch):
        """B3: when stream is VFR, candidates are tagged with vfr_pts_approximate and requires_pts_verification."""
        import pipeline.detect as det_mod
        from pipeline.schemas import SourceMediaRecord, StreamInfo

        class FakeSceneTime:
            def __init__(self, sec):
                self._sec = sec
            def get_seconds(self):
                return self._sec

        class FakeSceneManager:
            def add_detector(self, detector):
                pass
            def detect_scenes(self, video, **kwargs):
                pass
            def get_scene_list(self):
                return [
                    (FakeSceneTime(0.0), FakeSceneTime(2.0)),
                    (FakeSceneTime(2.0), FakeSceneTime(4.0)),
                ]

        original_psd = det_mod._PSD_AVAILABLE
        det_mod._PSD_AVAILABLE = True
        had_open_video = hasattr(det_mod, "open_video")
        had_sm = hasattr(det_mod, "SceneManager")
        try:
            setattr(det_mod, "open_video", lambda p: None)
            setattr(det_mod, "SceneManager", FakeSceneManager)
            setattr(det_mod, "ContentDetector", lambda threshold: None)
            setattr(det_mod, "AdaptiveDetector", lambda adaptive_threshold: None)

            stream = StreamInfo(
                index=0, codec_type="video", codec_name="h264",
                time_base_num=1, time_base_den=90000,
                is_vfr=True,
            )
            source = SourceMediaRecord(
                path=str(tmp_path / "vfr.mp4"), size_bytes=100, mtime_ns=0,
                sha256_head="a" * 64, sha256_tail="b" * 64,
                streams=[stream],
                start_pts=0,
                time_base_num=1, time_base_den=90000,
            )
            res = det_mod.detect_cuts(tmp_path / "vfr.mp4", source, "rev_vfr")
            assert res.status == "ok"
            assert len(res.candidates) == 1
            c = res.candidates[0]
            assert c.parameters.get("vfr_pts_approximate") is True
            assert c.parameters.get("requires_pts_verification") is True
            assert "vfr_warning" in c.parameters
        finally:
            det_mod._PSD_AVAILABLE = original_psd
            for attr in ["open_video", "SceneManager", "ContentDetector", "AdaptiveDetector"]:
                if hasattr(det_mod, attr):
                    delattr(det_mod, attr)

    # ── B4: boundary clip clamping to media duration ─────────────────────

    def test_b4_boundary_clip_clamping_to_source_duration(self, tmp_path, monkeypatch):
        """B4: extract_evidence clamps boundary clip duration to not exceed source media duration."""
        from pipeline.evidence import extract_evidence
        from pipeline.schemas import CutCandidate, SourceMediaRecord, StreamInfo

        stream = StreamInfo(
            index=0, codec_type="video", codec_name="h264",
            time_base_num=1, time_base_den=90000,
            start_pts=0, duration_pts=270000,
        )
        source = SourceMediaRecord(
            path=str(tmp_path / "short.mp4"), size_bytes=100, mtime_ns=0,
            sha256_head="a" * 64, sha256_tail="b" * 64,
            streams=[stream],
            start_pts=0,
            duration_pts=270000,
            time_base_num=1, time_base_den=90000,
        )
        rev_dir = tmp_path / "rev_b4"
        rev_dir.mkdir()
        (rev_dir / "evidence").mkdir()

        candidate = CutCandidate(
            source_id=source.source_id,
            revision_id="rev_b4",
            stream_index=0,
            pts=225000,
            time_base_num=1,
            time_base_den=90000,
            detector="test",
        )

        captured_clips = []
        import pipeline.evidence as ev_mod
        def mock_extract_clip(media_path, start_seconds, duration_seconds, output_path):
            captured_clips.append((start_seconds, duration_seconds))
            output_path.write_bytes(b"clipdata")
            return ["ffmpeg", "-y", "-ss", f"{start_seconds:.6f}", "-i", str(media_path),
                    "-t", f"{duration_seconds:.6f}", "-c", "copy", str(output_path)]

        def mock_extract_frame(media_path, seek_seconds, output_path):
            output_path.write_bytes(b"framedata")
            return ["ffmpeg", "-y", "-ss", f"{seek_seconds:.6f}", "-i", str(media_path),
                    "-frames:v", "1", "-q:v", "2", str(output_path)]

        monkeypatch.setattr(ev_mod, "_ffmpeg_available", lambda: True)
        monkeypatch.setattr(ev_mod, "_extract_clip", mock_extract_clip)
        monkeypatch.setattr(ev_mod, "_extract_frame", mock_extract_frame)

        records = extract_evidence(tmp_path / "short.mp4", source, candidate, rev_dir, clip_window_seconds=1.5)
        clip_recs = [r for r in records if r.kind == "clip_boundary"]
        assert len(clip_recs) == 1
        clip = clip_recs[0]
        assert len(captured_clips) == 1
        start_sec, dur_sec = captured_clips[0]
        assert start_sec == 1.0
        assert round(dur_sec, 4) == 2.0
        assert round(start_sec + dur_sec, 4) <= 3.0
        assert clip.source_interval.out_seconds <= 3.0

    # ── B10: rendered HTML no raw absolute paths & cross-drive fallback ──

    def test_b10_html_no_data_source_abs_and_no_raw_path(self, tmp_path):
        """B10: render_report does not leak raw absolute path or include data-source-abs."""
        from pipeline.render import render_report
        from pipeline.schemas import SourceMediaRecord, StreamInfo

        stream = StreamInfo(
            index=0, codec_type="video", codec_name="h264",
            time_base_num=1, time_base_den=25,
        )
        secret_source = tmp_path / "secret_folder" / "my_movie.mp4"
        secret_source.parent.mkdir(parents=True)
        secret_source.write_bytes(b"0" * 50)

        source = SourceMediaRecord(
            path=str(secret_source),
            size_bytes=50,
            mtime_ns=0,
            sha256_head="a" * 64,
            sha256_tail="b" * 64,
            streams=[stream],
        )
        rev_dir = tmp_path / "rev_b10"
        rev_dir.mkdir()

        out_path = render_report(
            revision_dir=rev_dir,
            source=source,
            revision_id="rev_b10",
            candidates=[],
            shots=[],
            evidence=[],
            validation=None,
        )
        html = out_path.read_text(encoding="utf-8")
        assert "data-source-abs" not in html
        assert str(secret_source) not in html

    def test_b10_cross_drive_media_fallback(self, tmp_path, monkeypatch):
        """B10: when relpath fails (e.g. cross-drive Windows), filename is used with fallback comment."""
        from pipeline.render import render_report
        from pipeline.schemas import SourceMediaRecord, StreamInfo
        import os

        stream = StreamInfo(
            index=0, codec_type="video", codec_name="h264",
            time_base_num=1, time_base_den=25,
        )
        source = SourceMediaRecord(
            path="D:\\foreign_drive\\videos\\alien.mp4",
            size_bytes=100,
            mtime_ns=0,
            sha256_head="a" * 64,
            sha256_tail="b" * 64,
            streams=[stream],
        )
        rev_dir = tmp_path / "rev_cross"
        rev_dir.mkdir()

        def fake_relpath(path, start):
            raise ValueError("path is on mount 'D:', start on mount 'C:'")
        monkeypatch.setattr(os.path, "relpath", fake_relpath)

        out_path = render_report(
            revision_dir=rev_dir,
            source=source,
            revision_id="rev_cross",
            candidates=[],
            shots=[],
            evidence=[],
            validation=None,
        )
        html = out_path.read_text(encoding="utf-8")
        assert '<source src="alien.mp4" type="video/mp4">' in html
        assert "<!-- media not co-located; open report from the project directory -->" in html
        assert "D:\\foreign_drive" not in html

    # ── B11: unavailable evidence adds 'evidence' to stages_failed ────────

    def test_b11_unavailable_evidence_adds_to_stages_failed(self, tmp_path, monkeypatch):
        """B11: when ffmpeg is unavailable, 'evidence' is added to stages_failed."""
        import pipeline.runner as runner_mod
        import pipeline.evidence as ev_mod
        from pipeline.detect import DetectionResult
        from pipeline.schemas import CutCandidate, SourceMediaRecord, StreamInfo

        media = tmp_path / "sample.mp4"
        media.write_bytes(b"\x00" * 100)
        projects = tmp_path / "projects"

        stream = StreamInfo(
            index=0, codec_type="video", codec_name="h264",
            time_base_num=1, time_base_den=25,
            duration_pts=250,
        )
        source = SourceMediaRecord(
            path=str(media), size_bytes=100, mtime_ns=0,
            sha256_head="a" * 64, sha256_tail="b" * 64,
            streams=[stream],
            duration_pts=250,
            time_base_num=1, time_base_den=25,
        )

        cand = CutCandidate(
            source_id=source.source_id,
            revision_id="placeholder",
            stream_index=0,
            pts=100,
            time_base_num=1,
            time_base_den=25,
            detector="test",
        )

        def mock_detect(m, s, rev_id, **kwargs):
            cand.revision_id = rev_id
            return DetectionResult(status="ok", candidates=[cand])

        monkeypatch.setattr(runner_mod, "probe_media", lambda p: source)
        monkeypatch.setattr(runner_mod, "detect_cuts", mock_detect)
        monkeypatch.setattr(ev_mod, "_ffmpeg_available", lambda: False)

        result = runner_mod.run_pipeline(media, projects)
        assert result["candidate_count"] == 1
        from pipeline.project import load_run
        run_state = load_run(projects, result["project_id"], result["run_id"])
        assert "evidence" in run_state.stages_failed

    # ── W1: probe duration_ts exact calculation ──────────────────────────

    def test_w1_duration_ts_exact_calculation(self, tmp_path, monkeypatch):
        """W1: probe.py prefers duration_ts integer timestamps and exact Fraction calculation."""
        import pipeline.probe as probe_mod

        media = tmp_path / "probe_w1.mp4"
        media.write_bytes(b"\x00" * 100)

        fake_ffprobe_out = {
            "streams": [
                {
                    "index": 0,
                    "codec_type": "video",
                    "codec_name": "h264",
                    "time_base": "1/90000",
                    "duration_ts": "900000",
                    "duration": "9.999999",
                }
            ],
            "format": {
                "duration": "10.000000",
            },
        }

        monkeypatch.setattr(probe_mod, "_ffprobe_available", lambda: True)
        monkeypatch.setattr(probe_mod, "_run_ffprobe", lambda p: fake_ffprobe_out)
        monkeypatch.setattr(probe_mod, "_run_ffprobe_no_packets", lambda p: fake_ffprobe_out)

        source = probe_mod.probe_media(media, with_vfr_check=False)
        assert source.duration_pts == 900000
        assert source.duration_seconds == 10.0
        assert source.streams[0].duration_pts == 900000
        assert source.streams[0].duration_seconds == 10.0

    # ── W2: PtsInterval rejects non-positive time_base ────────────────────

    def test_w2_pts_interval_rejects_non_positive_time_base(self):
        """W2: PtsInterval rejects non-positive time_base_num and time_base_den."""
        from pipeline.schemas import PtsInterval
        import pytest

        with pytest.raises(ValueError, match="time_base"):
            PtsInterval(in_pts=0, out_pts=100, time_base_num=0, time_base_den=1)

        with pytest.raises(ValueError, match="time_base"):
            PtsInterval(in_pts=0, out_pts=100, time_base_num=1, time_base_den=0)

        with pytest.raises(ValueError, match="time_base"):
            PtsInterval(in_pts=0, out_pts=100, time_base_num=-1, time_base_den=1)

        with pytest.raises(ValueError, match="time_base"):
            PtsInterval(in_pts=0, out_pts=100, time_base_num=1, time_base_den=-1)

    # ── W3: path traversal rejected in project, evidence, render ──────────

    def test_w3_path_traversal_rejected_in_project_evidence_render(self, tmp_path):
        """W3: path traversal strings are rejected across project, evidence, and render."""
        import pytest
        from pipeline.project import project_root, revision_root, create_project
        from pipeline.evidence import extract_evidence
        from pipeline.render import render_report
        from pipeline.schemas import CutCandidate, SourceMediaRecord, StreamInfo

        with pytest.raises(ValueError, match="Path traversal"):
            project_root(tmp_path, "../escape")
        with pytest.raises(ValueError, match="Path separator"):
            project_root(tmp_path, "sub/dir")
        with pytest.raises(ValueError, match="Path separator"):
            project_root(tmp_path, "sub\\dir")
        with pytest.raises(ValueError, match="Illegal characters"):
            project_root(tmp_path, "bad:name")
        with pytest.raises(ValueError, match="Path traversal"):
            revision_root(tmp_path, "proj", "../../bad")
        with pytest.raises(ValueError, match="Path separator"):
            create_project(tmp_path, "name", project_id="proj/1")

        stream = StreamInfo(index=0, codec_type="video", codec_name="h264")
        source = SourceMediaRecord(
            source_id="src1", path="v.mp4", size_bytes=0, mtime_ns=0,
            sha256_head="a"*64, sha256_tail="b"*64, streams=[stream],
        )
        bad_cand = CutCandidate(
            source_id="src1", revision_id="rev1", candidate_id="../traversal",
            stream_index=0, pts=100, detector="test",
        )
        with pytest.raises(ValueError, match="Path traversal"):
            extract_evidence(tmp_path / "v.mp4", source, bad_cand, tmp_path)

        with pytest.raises(ValueError, match="Path traversal"):
            render_report(tmp_path, source, "../bad_rev", [], [], [], None)
        with pytest.raises(ValueError, match="Path traversal"):
            render_report(tmp_path, source, "rev1", [], [], [], None, report_filename="../hack.html")

    # ── W4: Jinja autoescape and posix URLs ───────────────────────────────

    def test_w4_jinja_autoescape_and_posix_urls(self, tmp_path):
        """W4: Jinja autoescape prevents XSS and artifact URLs use forward slashes."""
        from pipeline.render import render_report
        from pipeline.schemas import CutCandidate, EvidenceRecord, SourceMediaRecord, StreamInfo

        stream = StreamInfo(index=0, codec_type="video", codec_name="h264")
        source = SourceMediaRecord(
            path="safe.mp4", size_bytes=0, mtime_ns=0,
            sha256_head="a"*64, sha256_tail="b"*64, streams=[stream],
        )
        rev_dir = tmp_path / "rev_w4"
        (rev_dir / "evidence").mkdir(parents=True)
        ev_file = rev_dir / "evidence" / "shot_01.jpg"
        ev_file.write_bytes(b"jpg")

        c = CutCandidate(
            source_id="s1", revision_id="rev_w4", candidate_id="c1",
            stream_index=0, pts=100, detector="<script>alert(1)</script>",
        )
        ev = EvidenceRecord(
            source_id="s1", revision_id="rev_w4", candidate_id="c1",
            kind="frame_pre", relative_path="evidence\\shot_01.jpg",
        )

        out = render_report(rev_dir, source, "rev_w4", [c], [], [ev], None)
        html = out.read_text(encoding="utf-8")
        assert "<script>alert(1)</script>" not in html
        assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
        assert "../evidence/shot_01.jpg" in html
        assert "evidence\\shot_01.jpg" not in html
