"""Cine pipeline — scene cut detection via PySceneDetect; graceful fallback."""
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

def _seconds_to_pts(seconds: float, tb_num: int, tb_den: int) -> int:
    """Convert wall-clock seconds to source PTS using rational time_base."""
    tb = Fraction(tb_num, tb_den)
    if tb == 0:
        return 0
    return int(round(seconds / float(tb)))


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

    If PySceneDetect is not installed, returns a single placeholder candidate
    with detector_status='unavailable'.

    PTS values are mapped from PySceneDetect's frame-time via the source
    stream's rational time_base — never from avg_frame_rate arithmetic.
    """
    # Find primary video stream
    video_stream = next(
        (s for s in source.streams if s.codec_type == "video"),
        None,
    )
    tb_num = source.time_base_num
    tb_den = source.time_base_den
    stream_index = video_stream.index if video_stream else 0

    if not _PSD_AVAILABLE:
        return [
            CutCandidate(
                source_id=source.source_id,
                revision_id=revision_id,
                stream_index=stream_index,
                pts=0,
                time_base_num=tb_num,
                time_base_den=tb_den,
                detector=detector_name,
                parameters={"threshold": threshold},
                score=None,
                status=CandidateStatus.candidate,
                detector_status=DetectorStatus.unavailable,
            )
        ]

    try:
        return _run_pyscenedetect(
            media_path=media_path,
            source=source,
            revision_id=revision_id,
            stream_index=stream_index,
            tb_num=tb_num,
            tb_den=tb_den,
            detector_name=detector_name,
            threshold=threshold,
            adaptive_ratio=adaptive_ratio,
            scope_in_pts=scope_in_pts,
            scope_out_pts=scope_out_pts,
        )
    except Exception as exc:
        # Detection failed — return unavailable placeholder
        return [
            CutCandidate(
                source_id=source.source_id,
                revision_id=revision_id,
                stream_index=stream_index,
                pts=0,
                time_base_num=tb_num,
                time_base_den=tb_den,
                detector=detector_name,
                parameters={"threshold": threshold, "error": str(exc)},
                score=None,
                status=CandidateStatus.candidate,
                detector_status=DetectorStatus.unavailable,
            )
        ]


def _run_pyscenedetect(
    *,
    media_path: Path,
    source: SourceMediaRecord,
    revision_id: str,
    stream_index: int,
    tb_num: int,
    tb_den: int,
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

    # Compute start/end in seconds for scoped detection
    tb = Fraction(tb_num, tb_den)
    start_time = None
    end_time = None
    if scope_in_pts is not None:
        start_time = float(scope_in_pts * tb)
    if scope_out_pts is not None:
        end_time = float(scope_out_pts * tb)

    sm.detect_scenes(video, show_progress=False, start_time=start_time, end_time=end_time)
    scenes = sm.get_scene_list()

    candidates: List[CutCandidate] = []
    for scene_in, scene_out in scenes:
        # scene_in.get_seconds() gives wall-clock position — convert to PTS
        cut_seconds = scene_in.get_seconds()
        cut_pts = _seconds_to_pts(cut_seconds, tb_num, tb_den)
        c = _make_candidate(
            source_id=source.source_id,
            revision_id=revision_id,
            stream_index=stream_index,
            pts=cut_pts,
            tb_num=tb_num,
            tb_den=tb_den,
            detector=detector_name,
            score=None,
            params={"threshold": threshold, "cut_seconds": cut_seconds},
        )
        candidates.append(c)

    return candidates
