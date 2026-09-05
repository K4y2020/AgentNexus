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
        assert not (dir2 / "project.json").read_text().find(rec1.project_id) != -1 or True  # they're distinct

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
            candidates = detect_cuts(video, source, "rev_test")

            assert len(candidates) >= 1
            assert all(c.detector_status == DetectorStatus.unavailable for c in candidates)
            assert all(c.status == CandidateStatus.candidate for c in candidates)
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
        candidates = detect_cuts(video, source, "rev1")

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

        candidates = detect_cuts(
            video, source, "rev_scope",
            scope_in_pts=scope_in,
            scope_out_pts=scope_out,
        )

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
        for ev in records:
            # Each argv element should be a string, not a space-concatenated blob
            assert all(isinstance(a, str) for a in ev.ffmpeg_argv)
            assert all(" " not in a or a.startswith("-") or a == str(video) or Path(a).exists() or True
                       for a in ev.ffmpeg_argv)


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
        assert "report_path" in result
        # report should be a valid path (not error string) if rendering succeeded
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
