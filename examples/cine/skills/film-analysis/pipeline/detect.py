"""Cine pipeline — scene cut detection via PySceneDetect; graceful fallback.

B3: scope in/out seconds apply source.start_pts offset; PTS mapping back adds start_pts.
    VFR IMPORTANT: PySceneDetect does not expose decoded packet PTS. For VFR streams,
    scene_in.get_seconds() is a nominal frame-time approximation — NOT an authoritative
    source PTS. Candidates from VFR streams are tagged vfr_pts_approximate=True and
    requires_pts_verification=True. They MUST be verified against decoded packet
    timestamps before acceptance.
B5: unavailable → DetectionResult(status="unavailable", candidates=[]).
    Runtime exception → DetectionResult(status="failed", candidates=[]).
    First scene (index 0) is video start, not a cut — skipped.
B6: detect_cuts() returns DetectionResult namedtuple with .status and .candidates.
"""
from __future__ import annotations

import uuid
from fractions import Fraction
from pathlib import Path
from typing import Any, Dict, List, NamedTuple, Optional, Tuple

from .schemas import CutCandidate, CandidateStatus, DetectorStatus, SourceMediaRecord

# ---------------------------------------------------------------------------
# Dependency detection — never raise at import time
# ---------------------------------------------------------------------------

try:
    from scenedetect import open_video, SceneManager
    from scenedetect.detectors import ContentDetector, AdaptiveDetector
    _PSD_AVAILABLE = True
except ImportError:
    _PSD_AVAILABLE = False


# ---------------------------------------------------------------------------
# Public result type (B6)
# ---------------------------------------------------------------------------

class DetectionResult(NamedTuple):
    """Result returned by detect_cuts().

    status: "ok"          — detection ran; candidates may be empty if no cuts found
            "unavailable" — PySceneDetect not installed; candidates is []
            "failed"      — runtime exception during detection; candidates is []
    candidates: list of CutCandidate records (empty for unavailable/failed)
    """
    status: str
    candidates: List[CutCandidate]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _seconds_to_pts(seconds: float, start_pts: int, tb_num: int, tb_den: int) -> int:
    """
    Convert playback-relative seconds to source PTS.
    B3: adds start_pts so PTS is in the source stream's coordinate space.
    """
    tb = Fraction(tb_num, tb_den)
    if tb == 0:
        return start_pts
    return start_pts + int(round(seconds / float(tb)))


def _pts_to_playback_seconds(pts: int, start_pts: int, tb_num: int, tb_den: int) -> float:
    """Convert source PTS to playback-relative seconds (subtract start_pts). B3."""
    return float((pts - start_pts) * Fraction(tb_num, tb_den))


def _make_candidate(
    source_id: str,
    revision_id: str,
    stream_index: int,
    pts: int,
    tb_num: int,
    tb_den: int,
    detector: str,
    score: Optional[float],
    params: Dict[str, Any],
) -> CutCandidate:
    return CutCandidate(
        source_id=source_id,
        revision_id=revision_id,
        stream_index=stream_index,
        pts=pts,
        time_base_num=tb_num,
        time_base_den=tb_den,
        detector=detector,
        parameters=params,
        score=score,
        status=CandidateStatus.candidate,
        detector_status=DetectorStatus.ok,
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def detect_cuts(
    media_path: Path,
    source: SourceMediaRecord,
    revision_id: str,
    *,
    detector_name: str = "ContentDetector",
    threshold: float = 27.0,
    adaptive_ratio: float = 3.0,
    scope_in_pts: Optional[int] = None,
    scope_out_pts: Optional[int] = None,
) -> DetectionResult:
    """
    Detect scene cuts in *media_path* and return a DetectionResult.

    B6: Returns DetectionResult(status, candidates) where status is one of:
      "ok"          -- detection ran successfully (candidates may be empty)
      "unavailable" -- PySceneDetect not installed
      "failed"      -- runtime exception during detection

    B5: No fabricated candidates ever returned for unavailable/failed.

    B3: PTS values are mapped from PySceneDetect's frame-time via the source
    stream's rational time_base with start_pts offset applied.
    VFR CAVEAT: PySceneDetect's scene_in.get_seconds() is a nominal frame-time
    approximation, not a decoded PTS. For VFR content, candidates are tagged
    vfr_pts_approximate=True and requires_pts_verification=True.
    """
    # Find primary video stream
    video_stream = next(
        (s for s in source.streams if s.codec_type == "video"),
        None,
    )
    tb_num = source.time_base_num
    tb_den = source.time_base_den
    stream_index = video_stream.index if video_stream else 0
    start_pts = source.start_pts  # B3

    # B5/B6: unavailable → DetectionResult with status="unavailable"
    if not _PSD_AVAILABLE:
        return DetectionResult(status="unavailable", candidates=[])

    try:
        candidates = _run_pyscenedetect(
            media_path=media_path,
            source=source,
            revision_id=revision_id,
            stream_index=stream_index,
            tb_num=tb_num,
            tb_den=tb_den,
            start_pts=start_pts,
            detector_name=detector_name,
            threshold=threshold,
            adaptive_ratio=adaptive_ratio,
            scope_in_pts=scope_in_pts,
            scope_out_pts=scope_out_pts,
        )
        return DetectionResult(status="ok", candidates=candidates)
    except Exception:
        # Detection failed — B6: return "failed" status, no fabricated candidates
        return DetectionResult(status="failed", candidates=[])


def _run_pyscenedetect(
    *,
    media_path: Path,
    source: SourceMediaRecord,
    revision_id: str,
    stream_index: int,
    tb_num: int,
    tb_den: int,
    start_pts: int,
    detector_name: str,
    threshold: float,
    adaptive_ratio: float,
    scope_in_pts: Optional[int],
    scope_out_pts: Optional[int],
) -> List[CutCandidate]:
    video = open_video(str(media_path))
    sm = SceneManager()

    if detector_name == "AdaptiveDetector":
        sm.add_detector(AdaptiveDetector(adaptive_threshold=adaptive_ratio))
    else:
        sm.add_detector(ContentDetector(threshold=threshold))

    # B3: Compute playback-relative start/end seconds for scoped detection.
    # scope_*_pts are in source PTS space; subtract start_pts to get playback seconds.
    tb = Fraction(tb_num, tb_den)
    start_time = None
    end_time = None
    if scope_in_pts is not None:
        start_time = float((scope_in_pts - start_pts) * tb)
    if scope_out_pts is not None:
        end_time = float((scope_out_pts - start_pts) * tb)

    sm.detect_scenes(video, show_progress=False, start_time=start_time, end_time=end_time)
    scenes = sm.get_scene_list()

    # VFR: PySceneDetect does not expose decoded packet PTS. For VFR streams,
    # scene_in.get_seconds() is a nominal frame-time approximation — NOT authoritative.
    # Candidates are tagged so callers can require boundary verification.
    is_vfr = video_stream_is_vfr(source)

    candidates: List[CutCandidate] = []
    # B5: Skip index 0 — scene_in of the first scene is the video/scope start, not a cut.
    # Only scenes at index >= 1 have a genuine cut at their scene_in.
    for idx, (scene_in, scene_out) in enumerate(scenes):
        if idx == 0:
            continue  # B5: video/scope start is not a cut

        # B3: scene_in.get_seconds() is playback-relative nominal frame time.
        # Convert back to source PTS by adding start_pts.
        cut_play_seconds = scene_in.get_seconds()
        cut_pts = _seconds_to_pts(cut_play_seconds, start_pts, tb_num, tb_den)

        params: Dict[str, Any] = {
            "threshold": threshold,
            "cut_play_seconds": cut_play_seconds,
        }
        if is_vfr:
            # B3: mark VFR candidates as approximate -- do NOT claim authoritative PTS
            params["vfr_pts_approximate"] = True
            params["requires_pts_verification"] = True
            params["vfr_warning"] = (
                "VFR stream: PTS derived from nominal frame-time approximation "
                "via PySceneDetect (no decoded packet PTS access); "
                "MUST be verified against decoded packet timestamps before acceptance."
            )

        c = _make_candidate(
            source_id=source.source_id,
            revision_id=revision_id,
            stream_index=stream_index,
            pts=cut_pts,
            tb_num=tb_num,
            tb_den=tb_den,
            detector=detector_name,
            score=None,
            params=params,
        )
        candidates.append(c)

    return candidates


def video_stream_is_vfr(source: SourceMediaRecord) -> bool:
    """Return True if the primary video stream was flagged VFR during probe."""
    for s in source.streams:
        if s.codec_type == "video":
            return s.is_vfr
    return False
