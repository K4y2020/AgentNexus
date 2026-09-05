"""Cine pipeline — keyframe extraction + boundary clips via ffmpeg argv lists."""
from __future__ import annotations

import hashlib
import subprocess
import uuid
from fractions import Fraction
from pathlib import Path
from typing import List, Optional

from .schemas import (
    CutCandidate,
    EvidenceRecord,
    PtsInterval,
    SourceMediaRecord,
)

_FFMPEG_VERSION_CACHE: Optional[str] = None


def _ffmpeg_version() -> str:
    global _FFMPEG_VERSION_CACHE
    if _FFMPEG_VERSION_CACHE is None:
        try:
            r = subprocess.run(
                ["ffmpeg", "-version"],
                capture_output=True, text=True
            )
            _FFMPEG_VERSION_CACHE = r.stdout.split("\n")[0] if r.stdout else "unknown"
        except FileNotFoundError:
            _FFMPEG_VERSION_CACHE = "unavailable"
    return _FFMPEG_VERSION_CACHE


def _ffmpeg_available() -> bool:
    return "unavailable" not in _ffmpeg_version()


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _pts_to_seconds(pts: int, tb_num: int, tb_den: int) -> float:
    return float(pts * Fraction(tb_num, tb_den))


# ---------------------------------------------------------------------------
# Single-frame extraction (no shell=True, no string concat)
# ---------------------------------------------------------------------------

def _extract_frame(
    media_path: Path,
    seek_seconds: float,
    output_path: Path,
) -> List[str]:
    """Extract a single keyframe at seek_seconds. Returns ffmpeg argv."""
    argv = [
        "ffmpeg",
        "-y",
        "-ss", f"{seek_seconds:.6f}",
        "-i", str(media_path),
        "-frames:v", "1",
        "-q:v", "2",
        str(output_path),
    ]
    subprocess.run(argv, check=True, capture_output=True)
    return argv


# ---------------------------------------------------------------------------
# Clip extraction — ±window seconds around a boundary
# ---------------------------------------------------------------------------

def _extract_clip(
    media_path: Path,
    start_seconds: float,
    duration_seconds: float,
    output_path: Path,
) -> List[str]:
    """Extract a short clip. Returns ffmpeg argv."""
    argv = [
        "ffmpeg",
        "-y",
        "-ss", f"{max(0.0, start_seconds):.6f}",
        "-i", str(media_path),
        "-t", f"{duration_seconds:.6f}",
        "-c", "copy",
        str(output_path),
    ]
    subprocess.run(argv, check=True, capture_output=True)
    return argv


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def extract_evidence(
    media_path: Path,
    source: SourceMediaRecord,
    candidate: CutCandidate,
    revision_dir: Path,
    *,
    clip_window_seconds: float = 1.5,
) -> List[EvidenceRecord]:
    """
    Extract pre/mid/post keyframes and a boundary clip for a cut candidate.

    Returns list of EvidenceRecord. On ffmpeg unavailability, returns empty list.
    """
    if not _ffmpeg_available():
        return []

    ev_dir = revision_dir / "evidence"
    ev_dir.mkdir(parents=True, exist_ok=True)

    tb_num = candidate.time_base_num
    tb_den = candidate.time_base_den
    cut_seconds = _pts_to_seconds(candidate.pts, tb_num, tb_den)
    records: List[EvidenceRecord] = []
    tool_ver = _ffmpeg_version()

    # Clamp to valid range
    total_dur = source.duration_seconds or 0.0
    win = clip_window_seconds

    # pre-frame: win seconds before cut
    pre_seek = max(0.0, cut_seconds - win)
    pre_path = ev_dir / f"{candidate.candidate_id}_pre.jpg"
    try:
        argv = _extract_frame(media_path, pre_seek, pre_path)
        sha = _sha256_file(pre_path) if pre_path.exists() else None
        pre_pts = int(round(pre_seek / float(Fraction(tb_num, tb_den))))
        records.append(EvidenceRecord(
            source_id=source.source_id,
            revision_id=candidate.revision_id,
            candidate_id=candidate.candidate_id,
            kind="frame_pre",
            relative_path=str(pre_path.relative_to(revision_dir)),
            sha256=sha,
            source_interval=PtsInterval(
                in_pts=pre_pts, out_pts=pre_pts + 1,
                time_base_num=tb_num, time_base_den=tb_den,
            ),
            ffmpeg_argv=argv,
            tool_version=tool_ver,
        ))
    except subprocess.CalledProcessError:
        pass

    # mid-frame: at cut point
    mid_path = ev_dir / f"{candidate.candidate_id}_mid.jpg"
    try:
        argv = _extract_frame(media_path, cut_seconds, mid_path)
        sha = _sha256_file(mid_path) if mid_path.exists() else None
        records.append(EvidenceRecord(
            source_id=source.source_id,
            revision_id=candidate.revision_id,
            candidate_id=candidate.candidate_id,
            kind="frame_mid",
            relative_path=str(mid_path.relative_to(revision_dir)),
            sha256=sha,
            source_interval=PtsInterval(
                in_pts=candidate.pts, out_pts=candidate.pts + 1,
                time_base_num=tb_num, time_base_den=tb_den,
            ),
            ffmpeg_argv=argv,
            tool_version=tool_ver,
        ))
    except subprocess.CalledProcessError:
        pass

    # post-frame: win seconds after cut
    post_seek = min(total_dur - 0.01, cut_seconds + win) if total_dur > 0 else cut_seconds + win
    post_path = ev_dir / f"{candidate.candidate_id}_post.jpg"
    try:
        argv = _extract_frame(media_path, post_seek, post_path)
        sha = _sha256_file(post_path) if post_path.exists() else None
        post_pts = int(round(post_seek / float(Fraction(tb_num, tb_den))))
        records.append(EvidenceRecord(
            source_id=source.source_id,
            revision_id=candidate.revision_id,
            candidate_id=candidate.candidate_id,
            kind="frame_post",
            relative_path=str(post_path.relative_to(revision_dir)),
            sha256=sha,
            source_interval=PtsInterval(
                in_pts=post_pts, out_pts=post_pts + 1,
                time_base_num=tb_num, time_base_den=tb_den,
            ),
            ffmpeg_argv=argv,
            tool_version=tool_ver,
        ))
    except subprocess.CalledProcessError:
        pass

    # boundary clip: 2*win seconds centred on cut
    clip_start = max(0.0, cut_seconds - win)
    clip_duration = 2 * win
    clip_ext = Path(media_path).suffix or ".mp4"
    clip_path = ev_dir / f"{candidate.candidate_id}_clip{clip_ext}"
    try:
        argv = _extract_clip(media_path, clip_start, clip_duration, clip_path)
        sha = _sha256_file(clip_path) if clip_path.exists() else None
        clip_in_pts = int(round(clip_start / float(Fraction(tb_num, tb_den))))
        clip_out_pts = int(round((clip_start + clip_duration) / float(Fraction(tb_num, tb_den))))
        if clip_out_pts <= clip_in_pts:
            clip_out_pts = clip_in_pts + 1
        records.append(EvidenceRecord(
            source_id=source.source_id,
            revision_id=candidate.revision_id,
            candidate_id=candidate.candidate_id,
            kind="clip_boundary",
            relative_path=str(clip_path.relative_to(revision_dir)),
            sha256=sha,
            source_interval=PtsInterval(
                in_pts=clip_in_pts, out_pts=clip_out_pts,
                time_base_num=tb_num, time_base_den=tb_den,
            ),
            ffmpeg_argv=argv,
            tool_version=tool_ver,
        ))
    except subprocess.CalledProcessError:
        pass

    return records
