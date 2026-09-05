"""Cine pipeline — scene cut detection via PySceneDetect; graceful fallback.

B3: scope in/out seconds apply source.start_pts offset; PTS mapping back adds start_pts.
    VFR caveat comment included — PySceneDetect uses frame-time approximation.
B5: unavailable → return [] (empty), not a fabricated pts=0 candidate.
    First scene (index 0) is video start, not a cut — skipped.
"""
from __future__ import annotations

import uuid
from fractions import Fraction
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

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
) -> List[CutCandidate]:
    """
    Detect scene cuts in *media_path* and return a list of CutCandidate records.

    B5: If PySceneDetect is not installed (or detection fails), returns [] — no
    fabricated candidates. Callers must handle the empty-list case separately.

    B3: PTS values are mapped from PySceneDetect's frame-time via the source
    stream's rational time_base with start_pts offset applied.
    VFR CAVEAT: PySceneDetect's scene_in.get_seconds() is a nominal frame-time
    approximation, not a decoded PTS. For VFR content, candidates should be
    flagged for boundary verification before use.
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

    # B5: unavailable → empty list, no fabricated candidate
    if not _PSD_AVAILABLE:
        return []

    try:
        return _run_pyscenedetect(
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
    except Exception:
        # Detection failed — return empty list (B5: no fabrication)
        return []


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

    candidates: List[CutCandidate] = []
    # B5: Skip index 0 — scene_in of the first scene is the video/scope start, not a cut.
    # Only scenes at index >= 1 have a genuine cut at their scene_in.
    for idx, (scene_in, scene_out) in enumerate(scenes):
        if idx == 0:
            continue  # B5: video/scope start is not a cut

        # B3: scene_in.get_seconds() is playback-relative nominal frame time.
        # Convert back to source PTS by adding start_pts.
        # VFR CAVEAT: this is an approximation for VFR streams; boundary verification required.
        cut_play_seconds = scene_in.get_seconds()
        cut_pts = _seconds_to_pts(cut_play_seconds, start_pts, tb_num, tb_den)

        is_vfr = video_stream_is_vfr(source)
        params: Dict[str, Any] = {
            "threshold": threshold,
            "cut_play_seconds": cut_play_seconds,
        }
        if is_vfr:
            params["vfr_warning"] = (
                "VFR stream: PTS derived from nominal frame-time; "
                "boundary verification required before accepting this candidate."
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
