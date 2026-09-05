"""Cine pipeline — keyframe extraction + boundary clips via ffmpeg argv lists.

B4: All seek times offset by source.start_pts so non-zero start_time is preserved.
B9: Each artifact written atomically via temp file + os.replace; existing files skipped (revision immutability).
B11: When ffmpeg absent, returns one EvidenceRecord with extraction_status="unavailable".
"""
from __future__ import annotations

import hashlib
import os
import subprocess
import tempfile
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


def _pts_to_playback_seconds(pts: int, start_pts: int, tb_num: int, tb_den: int) -> float:
    """
    Convert source PTS to playback-relative seconds, subtracting start_pts.
    (B4) Non-zero start_pts must be offset so ffmpeg -ss is relative to file start.
    """
    return max(0.0, float((pts - start_pts) * Fraction(tb_num, tb_den)))


def _pts_from_playback_seconds(seconds: float, start_pts: int, tb_num: int, tb_den: int) -> int:
    """Inverse: playback seconds back to source PTS."""
    tb = Fraction(tb_num, tb_den)
    if tb == 0:
        return start_pts
    return start_pts + int(round(seconds / float(tb)))


# ---------------------------------------------------------------------------
# Atomic single-frame extraction (B9)
# ---------------------------------------------------------------------------

def _extract_frame(
    media_path: Path,
    seek_seconds: float,
    output_path: Path,
) -> List[str]:
    """
    Extract a single keyframe at seek_seconds (playback-relative).
    Writes atomically: temp -> os.replace. Skips if output already exists.
    Returns ffmpeg argv list.
    """
    # B9: skip if already committed (revision immutability)
    if output_path.exists():
        argv = ["ffmpeg", "-ss", f"{seek_seconds:.6f}", "-i", str(media_path),
                "-frames:v", "1", "-q:v", "2", str(output_path)]
        return argv

    tmp_fd, tmp_path = tempfile.mkstemp(
        dir=str(output_path.parent), suffix=".tmp.jpg"
    )
    os.close(tmp_fd)
    argv = [
        "ffmpeg",
        "-y",
        "-ss", f"{seek_seconds:.6f}",
        "-i", str(media_path),
        "-frames:v", "1",
        "-q:v", "2",
        tmp_path,
    ]
    try:
        subprocess.run(argv, check=True, capture_output=True)
        # Verify non-zero size before replacing
        if os.path.getsize(tmp_path) == 0:
            raise RuntimeError("ffmpeg produced empty output")
        os.replace(tmp_path, str(output_path))
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
    # Return argv with final path (not tmp) for record-keeping
    return [
        "ffmpeg", "-y",
        "-ss", f"{seek_seconds:.6f}",
        "-i", str(media_path),
        "-frames:v", "1",
        "-q:v", "2",
        str(output_path),
    ]


# ---------------------------------------------------------------------------
# Atomic clip extraction (B9)
# ---------------------------------------------------------------------------

def _extract_clip(
    media_path: Path,
    start_seconds: float,
    duration_seconds: float,
    output_path: Path,
) -> List[str]:
    """
    Extract a short clip atomically. Skips if output already exists.
    Returns ffmpeg argv list.
    """
    if output_path.exists():
        return ["ffmpeg", "-ss", f"{start_seconds:.6f}", "-i", str(media_path),
                "-t", f"{duration_seconds:.6f}", "-c", "copy", str(output_path)]

    suffix = output_path.suffix or ".mp4"
    tmp_fd, tmp_path = tempfile.mkstemp(
        dir=str(output_path.parent), suffix=f".tmp{suffix}"
    )
    os.close(tmp_fd)
    argv = [
        "ffmpeg",
        "-y",
        "-ss", f"{max(0.0, start_seconds):.6f}",
        "-i", str(media_path),
        "-t", f"{duration_seconds:.6f}",
        "-c", "copy",
        tmp_path,
    ]
    try:
        subprocess.run(argv, check=True, capture_output=True)
        if os.path.getsize(tmp_path) == 0:
            raise RuntimeError("ffmpeg produced empty clip")
        os.replace(tmp_path, str(output_path))
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
    return [
        "ffmpeg", "-y",
        "-ss", f"{max(0.0, start_seconds):.6f}",
        "-i", str(media_path),
        "-t", f"{duration_seconds:.6f}",
        "-c", "copy",
        str(output_path),
    ]


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

    B4: All seek times are playback-relative (subtract source.start_pts).
    B9: Each artifact written atomically via tempfile + os.replace; existing skipped.
    B11: When ffmpeg absent, returns one EvidenceRecord with extraction_status='unavailable'.

    Returns list of EvidenceRecord.
    """
    # B11: unavailable → return marker record rather than empty list
    if not _ffmpeg_available():
        return [EvidenceRecord(
            source_id=source.source_id,
            revision_id=candidate.revision_id,
            candidate_id=candidate.candidate_id,
            kind="frame_mid",
            relative_path=None,
            extraction_status="unavailable",
            tool_version="unavailable",
        )]

    ev_dir = revision_dir / "evidence"
    ev_dir.mkdir(parents=True, exist_ok=True)

    tb_num = candidate.time_base_num
    tb_den = candidate.time_base_den
    start_pts = source.start_pts  # B4: offset

    # B4: convert candidate PTS to playback-relative seconds
    cut_playback = _pts_to_playback_seconds(candidate.pts, start_pts, tb_num, tb_den)
    records: List[EvidenceRecord] = []
    tool_ver = _ffmpeg_version()

    # Clamp window to available duration
    total_dur = source.duration_seconds or 0.0
    win = clip_window_seconds

    # ── pre-frame: win seconds before cut (playback-relative) ──
    pre_play = max(0.0, cut_playback - win)
    pre_pts = _pts_from_playback_seconds(pre_play, start_pts, tb_num, tb_den)
    pre_path = ev_dir / f"{candidate.candidate_id}_pre.jpg"
    try:
        argv = _extract_frame(media_path, pre_play, pre_path)
        sha = _sha256_file(pre_path) if pre_path.exists() else None
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
            extraction_status="ok",
        ))
    except (subprocess.CalledProcessError, RuntimeError):
        pass

    # ── mid-frame: at cut point ──
    mid_path = ev_dir / f"{candidate.candidate_id}_mid.jpg"
    try:
        argv = _extract_frame(media_path, cut_playback, mid_path)
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
            extraction_status="ok",
        ))
    except (subprocess.CalledProcessError, RuntimeError):
        pass

    # ── post-frame: win seconds after cut ──
    post_play = cut_playback + win
    if total_dur > 0:
        post_play = min(post_play, total_dur - 0.01)
    post_pts = _pts_from_playback_seconds(post_play, start_pts, tb_num, tb_den)
    post_path = ev_dir / f"{candidate.candidate_id}_post.jpg"
    try:
        argv = _extract_frame(media_path, post_play, post_path)
        sha = _sha256_file(post_path) if post_path.exists() else None
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
            extraction_status="ok",
        ))
    except (subprocess.CalledProcessError, RuntimeError):
        pass

    # ── boundary clip: 2*win seconds centred on cut ──
    clip_start_play = max(0.0, cut_playback - win)
    clip_end_play = clip_start_play + 2 * win
    # B4: clamp clip end to source duration (avoid seeking past EOF)
    if source.duration_pts is not None and source.duration_pts > 0:
        tb_frac = Fraction(tb_num, tb_den)
        max_playback_end = float(source.duration_pts * tb_frac)
        clip_end_play = min(clip_end_play, max_playback_end)
    clip_duration = clip_end_play - clip_start_play
    clip_ext = Path(media_path).suffix or ".mp4"
    clip_path = ev_dir / f"{candidate.candidate_id}_clip{clip_ext}"
    clip_in_pts = _pts_from_playback_seconds(clip_start_play, start_pts, tb_num, tb_den)
    clip_out_pts = _pts_from_playback_seconds(clip_end_play, start_pts, tb_num, tb_den)
    if clip_out_pts <= clip_in_pts:
        clip_out_pts = clip_in_pts + 1
    if clip_duration > 0:
        try:
            argv = _extract_clip(media_path, clip_start_play, clip_duration, clip_path)
            sha = _sha256_file(clip_path) if clip_path.exists() else None
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
                extraction_status="ok",
            ))
        except (subprocess.CalledProcessError, RuntimeError):
            pass

    return records
