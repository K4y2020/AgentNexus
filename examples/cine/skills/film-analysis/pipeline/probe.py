"""Cine pipeline — ffprobe wrapper; integer PTS + rational time_base; VFR detection."""
from __future__ import annotations

import json
import os
import subprocess
from fractions import Fraction
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .schemas import SourceMediaRecord, StreamInfo
from .source_identity import CHUNK_SIZE, _sha256_chunk  # noqa: F401 - one identity rule

# ---------------------------------------------------------------------------
# Dependency check
# ---------------------------------------------------------------------------

def _ffprobe_available() -> bool:
    try:
        subprocess.run(
            ["ffprobe", "-version"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=True,
        )
        return True
    except (FileNotFoundError, subprocess.CalledProcessError):
        return False


# ---------------------------------------------------------------------------
# Hashing helpers (the head/tail hash itself lives in source_identity)
# ---------------------------------------------------------------------------

def fast_changed(record: SourceMediaRecord, path: Path) -> bool:
    """Return True if size or mtime has changed since last probe."""
    st = path.stat()
    if st.st_size != record.size_bytes:
        return True
    mtime_ns = int(st.st_mtime_ns)
    return mtime_ns != record.mtime_ns


def content_changed(record: SourceMediaRecord, path: Path) -> bool:
    """Return True if head or tail SHA-256 differs (content change)."""
    new_head = _sha256_chunk(path, tail=False)
    new_tail = _sha256_chunk(path, tail=True)
    return new_head != record.sha256_head or new_tail != record.sha256_tail


# ---------------------------------------------------------------------------
# ffprobe call — argv list only
# ---------------------------------------------------------------------------

def _run_ffprobe(path: Path) -> Dict[str, Any]:
    argv = [
        "ffprobe",
        "-v", "quiet",
        "-print_format", "json",
        "-show_format",
        "-show_streams",
        "-show_packets",       # needed for VFR detection via PTS gaps
        str(path),
    ]
    result = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8", timeout=120)
    if result.returncode != 0:
        raise RuntimeError(
            f"ffprobe failed (exit {result.returncode}): {result.stderr.strip()}"
        )
    return json.loads(result.stdout)


def _run_ffprobe_no_packets(path: Path) -> Dict[str, Any]:
    """Lighter ffprobe call (no packets) for quick metadata."""
    argv = [
        "ffprobe",
        "-v", "quiet",
        "-print_format", "json",
        "-show_format",
        "-show_streams",
        str(path),
    ]
    result = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8", timeout=120)
    if result.returncode != 0:
        raise RuntimeError(
            f"ffprobe failed (exit {result.returncode}): {result.stderr.strip()}"
        )
    return json.loads(result.stdout)


# ---------------------------------------------------------------------------
# VFR detection
# ---------------------------------------------------------------------------

def _detect_vfr(packets: List[Dict[str, Any]], stream_index: int) -> bool:
    """
    Detect VFR by checking whether PTS deltas are uniform.
    Returns True if the stream is variable-frame-rate.
    """
    pts_list = [
        int(p["pts"])
        for p in packets
        if p.get("codec_type") == "video"
        and p.get("stream_index") == stream_index
        and p.get("pts") not in (None, "N/A")
    ]
    if len(pts_list) < 4:
        return False
    pts_list.sort()
    deltas = [pts_list[i + 1] - pts_list[i] for i in range(len(pts_list) - 1)]
    return len(set(deltas)) > 1


# ---------------------------------------------------------------------------
# Parse time_base string "num/den"
# ---------------------------------------------------------------------------

def _parse_tb(s: str) -> Tuple[int, int]:
    parts = s.split("/")
    if len(parts) == 2:
        return int(parts[0]), int(parts[1])
    return 1, int(parts[0])


# ---------------------------------------------------------------------------
# Main probe entry point
# ---------------------------------------------------------------------------

def probe_media(path: Path, *, with_vfr_check: bool = True) -> SourceMediaRecord:
    """
    Probe a media file and return a SourceMediaRecord.
    Raises RuntimeError if ffprobe is unavailable or fails.
    """
    if not _ffprobe_available():
        raise RuntimeError("ffprobe is not available on this system")

    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Media file not found: {path}")

    st = path.stat()
    size_bytes = st.st_size
    mtime_ns = int(st.st_mtime_ns)
    sha256_head = _sha256_chunk(path, tail=False)
    sha256_tail = _sha256_chunk(path, tail=True)

    # Use heavy probe (with packets) only if VFR check requested and file < 1 GiB
    if with_vfr_check and size_bytes < 1 * 1024 ** 3:
        raw = _run_ffprobe(path)
    else:
        raw = _run_ffprobe_no_packets(path)

    streams_raw = raw.get("streams", [])
    streams: List[StreamInfo] = []
    primary_tb_num, primary_tb_den = 1, 1
    primary_start_pts = 0
    primary_duration_pts = None

    packets = raw.get("packets", [])

    for s in streams_raw:
        tb_str = s.get("time_base", "1/1")
        tb_num, tb_den = _parse_tb(tb_str)
        start_pts_raw = s.get("start_pts")
        start_pts = int(start_pts_raw) if start_pts_raw not in (None, "N/A") else None
        nb_frames_raw = s.get("nb_frames")
        nb_frames = int(nb_frames_raw) if nb_frames_raw not in (None, "N/A") else None

        # W1: prefer duration_ts integer timestamps when present and non-zero
        stream_dur_pts = None
        dur_ts_raw = s.get("duration_ts")
        if dur_ts_raw not in (None, "N/A"):
            try:
                val = int(dur_ts_raw)
                if val != 0:
                    stream_dur_pts = val
            except (ValueError, TypeError):
                pass
        if stream_dur_pts is None:
            dur_s_raw = s.get("duration")
            if dur_s_raw not in (None, "N/A"):
                tb = Fraction(tb_num, tb_den)
                if tb != 0:
                    try:
                        stream_dur_pts = int(round(Fraction(str(dur_s_raw)) / tb))
                    except Exception:
                        pass

        is_vfr = False
        if with_vfr_check and s.get("codec_type") == "video" and packets:
            is_vfr = _detect_vfr(packets, s.get("index", 0))

        si = StreamInfo(
            index=s.get("index", 0),
            codec_type=s.get("codec_type", "unknown"),
            codec_name=s.get("codec_name", "unknown"),
            width=s.get("width"),
            height=s.get("height"),
            avg_frame_rate=s.get("avg_frame_rate"),  # stored only, never used for PTS math
            r_frame_rate=s.get("r_frame_rate"),
            time_base_num=tb_num,
            time_base_den=tb_den,
            start_pts=start_pts,
            duration_pts=stream_dur_pts,
            nb_frames=nb_frames,
            is_vfr=is_vfr,
            extra={k: v for k, v in s.items() if k not in {
                "index", "codec_type", "codec_name", "width", "height",
                "avg_frame_rate", "r_frame_rate", "time_base",
                "start_pts", "duration_ts", "duration", "nb_frames",
            }},
        )
        streams.append(si)

        # Pick first video stream as primary
        if s.get("codec_type") == "video" and primary_duration_pts is None:
            primary_tb_num, primary_tb_den = tb_num, tb_den
            primary_start_pts = start_pts or 0
            if stream_dur_pts is not None and stream_dur_pts != 0:
                primary_duration_pts = stream_dur_pts

    # Fallback: use format duration if stream duration not available
    if primary_duration_pts is None:
        fmt = raw.get("format", {})
        dur_s = fmt.get("duration")
        if dur_s and dur_s != "N/A":
            tb = Fraction(primary_tb_num, primary_tb_den)
            if tb != 0:
                try:
                    primary_duration_pts = int(round(Fraction(str(dur_s)) / tb))
                except Exception:
                    primary_duration_pts = int(round(float(dur_s) / float(tb)))

    return SourceMediaRecord(
        path=str(path),
        size_bytes=size_bytes,
        mtime_ns=mtime_ns,
        sha256_head=sha256_head,
        sha256_tail=sha256_tail,
        streams=streams,
        duration_pts=primary_duration_pts,
        start_pts=primary_start_pts,
        time_base_num=primary_tb_num,
        time_base_den=primary_tb_den,
        probe_raw=raw,
    )
