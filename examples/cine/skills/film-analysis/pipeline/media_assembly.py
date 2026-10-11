"""Losslessly assemble verified local exports without claiming creative acceptance."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import tempfile
from fractions import Fraction
from pathlib import Path

UNVERIFIED = ["visual_quality", "motion", "audio_quality", "dialogue", "continuity"]
STREAM_FIELDS = (
    "codec_type",
    "codec_name",
    "profile",
    "codec_tag_string",
    "time_base",
    "width",
    "height",
    "pix_fmt",
    "sample_aspect_ratio",
    "field_order",
    "color_range",
    "color_space",
    "color_transfer",
    "color_primaries",
    "r_frame_rate",
    "avg_frame_rate",
    "sample_fmt",
    "sample_rate",
    "channels",
    "channel_layout",
    "extradata_hash",
    "disposition",
)


class AssemblyBlocked(ValueError):
    def __init__(self, code: str, detail):
        super().__init__(str(detail))
        self.code = code
        self.detail = detail


def _digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def _json_bytes(value) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")


def _inside(topic: Path, value: Path, *, exists: bool = False) -> Path:
    path = value if value.is_absolute() else topic / value
    path = path.resolve(strict=exists)
    if not path.is_relative_to(topic):
        raise AssemblyBlocked("CINE_ASSEMBLY_PATH_ESCAPE", str(path))
    return path


def _run(command: list[str], *, timeout: float = 120, diagnostics: list | None = None) -> str:
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
    )
    if result.returncode:
        raise AssemblyBlocked(
            "CINE_ASSEMBLY_TOOL_FAILED",
            {
                "tool": Path(command[0]).name,
                "exit_code": result.returncode,
                "stderr": result.stderr[-4000:],
            },
        )
    if diagnostics is not None and result.stderr.strip():
        diagnostics.append(result.stderr[-4000:].strip())
    return result.stdout


def _probe(ffprobe: str, path: Path) -> dict:
    data = json.loads(
        _run(
            [
                ffprobe,
                "-v",
                "error",
                "-protocol_whitelist",
                "file,pipe",
                "-show_streams",
                "-show_format",
                "-show_data_hash",
                "sha256",
                "-of",
                "json",
                str(path),
            ]
        )
    )
    streams = data.get("streams") or []
    video = [s for s in streams if s.get("codec_type") == "video"]
    if len(video) != 1 or any(s.get("codec_type") not in ("video", "audio") for s in streams):
        raise AssemblyBlocked("CINE_ASSEMBLY_STREAMS_UNSUPPORTED", str(path))
    duration = float((data.get("format") or {}).get("duration", 0))
    if not math.isfinite(duration) or duration <= 0:
        raise AssemblyBlocked("CINE_ASSEMBLY_DURATION_INVALID", str(path))
    for stream in streams:
        if not stream.get("codec_name") or not stream.get("time_base"):
            raise AssemblyBlocked("CINE_ASSEMBLY_METADATA_INCOMPLETE", str(path))
        if stream["codec_type"] == "video":
            if not stream.get("width") or not stream.get("height") or not stream.get("pix_fmt"):
                raise AssemblyBlocked("CINE_ASSEMBLY_METADATA_INCOMPLETE", str(path))
            rate = Fraction(stream.get("avg_frame_rate", "0/1"))
            if rate <= 0 or rate != Fraction(stream.get("r_frame_rate", "0/1")):
                raise AssemblyBlocked("CINE_ASSEMBLY_VARIABLE_RATE_UNSUPPORTED", str(path))
        elif not all(stream.get(k) for k in ("sample_rate", "channels", "channel_layout")):
            raise AssemblyBlocked("CINE_ASSEMBLY_METADATA_INCOMPLETE", str(path))
    profile = [{key: stream.get(key) for key in STREAM_FIELDS} for stream in streams]
    return {
        "duration_seconds": duration,
        "streams": profile,
        "video_frames": int(video[0]["nb_frames"])
        if str(video[0].get("nb_frames", "")).isdigit()
        else None,
        "format_name": (data.get("format") or {}).get("format_name"),
    }


def _verify_inputs(inputs: list[tuple[Path, dict]], topic: Path) -> None:
    for path, clip in inputs:
        if _inside(topic, path, exists=True) != path or _digest(path) != clip["sha256"]:
            raise AssemblyBlocked("CINE_ASSEMBLY_CLIP_CHANGED", {"job_id": clip["job_id"]})


def _cached(receipt_path: Path, target: Path, identity: str, clips: list[dict]) -> dict | None:
    if not receipt_path.exists():
        return None
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    if (
        not isinstance(receipt, dict)
        or receipt.get("schema_version") != 1
        or receipt.get("assembly_id") != identity
        or receipt.get("status") != "assembled"
        or receipt.get("clips") != clips
        or receipt.get("path") != str(target)
        or receipt.get("acceptance") != "pending"
        or receipt.get("unverified") != UNVERIFIED
        or not isinstance(receipt.get("metadata"), dict)
        or receipt["metadata"].get("decode_verified") is not True
        or receipt["metadata"].get("lossless_concat") is not True
        or not target.is_file()
        or target.stat().st_size != receipt.get("bytes")
        or _digest(target) != receipt.get("sha256")
    ):
        raise AssemblyBlocked("CINE_ASSEMBLY_RECEIPT_MISMATCH", str(receipt_path))
    return {**receipt, "reused": True}


def assemble(
    directory: Path, clips: list[dict], output: Path | None = None, *, verify_cached: bool = False
) -> dict:
    """Concatenate current export receipts into a content-addressed, unreviewed film."""
    try:
        directory = Path(directory).resolve(strict=True)
        if not directory.is_dir():
            raise AssemblyBlocked("CINE_ASSEMBLY_DIRECTORY_INVALID", str(directory))
        topic = directory.parent
        canonical = topic / "outputs" / "cine"
        if _inside(topic, canonical) != canonical:
            raise AssemblyBlocked(
                "CINE_ASSEMBLY_PATH_ESCAPE", "Output directory must not redirect."
            )
        output = canonical if output is None else _inside(topic, Path(output))
        if output != canonical:
            raise AssemblyBlocked("CINE_ASSEMBLY_OUTPUT_INVALID", str(output))
        if not isinstance(clips, list) or not clips:
            raise AssemblyBlocked(
                "CINE_ASSEMBLY_CLIPS_REQUIRED", "Supply ordered video export receipts."
            )
        inputs, compact = [], []
        seen_jobs, seen_hashes, seen_paths = set(), set(), set()
        for clip in clips:
            if (
                not isinstance(clip, dict)
                or clip.get("media_kind") != "video"
                or not isinstance(clip.get("job_id"), str)
                or not clip["job_id"].strip()
                or not isinstance(clip.get("sha256"), str)
                or not re.fullmatch(r"[a-f0-9]{64}", clip["sha256"])
                or not isinstance(clip.get("path"), str)
                or not clip["path"].strip()
                or "\n" in clip["path"]
                or "\r" in clip["path"]
            ):
                raise AssemblyBlocked(
                    "CINE_ASSEMBLY_RECEIPT_INVALID", "A clip is not a video export receipt."
                )
            path = _inside(topic, Path(clip["path"]), exists=True)
            if not path.is_file():
                raise AssemblyBlocked("CINE_ASSEMBLY_CLIP_INVALID", str(path))
            if clip["job_id"] in seen_jobs or clip["sha256"] in seen_hashes or path in seen_paths:
                raise AssemblyBlocked("CINE_ASSEMBLY_DUPLICATE_CLIP", clip["job_id"])
            seen_jobs.add(clip["job_id"])
            seen_hashes.add(clip["sha256"])
            seen_paths.add(path)
            compact.append({"job_id": clip["job_id"], "sha256": clip["sha256"]})
            inputs.append((path, compact[-1]))
        _verify_inputs(inputs, topic)
        identity = hashlib.sha256(_json_bytes({"schema_version": 1, "clips": compact})).hexdigest()
        target = output / f"{identity}.mp4"
        receipt_path = output / f"{identity}.json"
        if _inside(topic, target) != target or _inside(topic, receipt_path) != receipt_path:
            raise AssemblyBlocked("CINE_ASSEMBLY_PATH_ESCAPE", "Output paths must not redirect.")
        cached = None if verify_cached else _cached(receipt_path, target, identity, compact)
        if cached is not None:
            return cached
        ffprobe, ffmpeg = shutil.which("ffprobe"), shutil.which("ffmpeg")
        if not ffprobe or not ffmpeg:
            raise AssemblyBlocked(
                "CINE_ASSEMBLY_TOOL_MISSING",
                {
                    "missing": [
                        name
                        for name, path in (("ffprobe", ffprobe), ("ffmpeg", ffmpeg))
                        if not path
                    ],
                },
            )
        metadata = [_probe(ffprobe, path) for path, _ in inputs]
        if any(item["streams"] != metadata[0]["streams"] for item in metadata[1:]):
            raise AssemblyBlocked(
                "CINE_ASSEMBLY_INCOMPATIBLE_CLIPS",
                {
                    "message": (
                        "Stream codecs, dimensions, frame rates and audio layouts must match; "
                        "no transcoding was attempted."
                    ),
                    "clips": [
                        {**clip, "streams": meta["streams"]}
                        for clip, meta in zip(compact, metadata, strict=True)
                    ],
                },
            )
        _verify_inputs(inputs, topic)
        output.mkdir(parents=True, exist_ok=True)
        if _inside(topic, output, exists=True) != output:
            raise AssemblyBlocked("CINE_ASSEMBLY_PATH_ESCAPE", str(output))
        duration = sum(item["duration_seconds"] for item in metadata)
        timeout = max(120, min(3600, duration * 4 + 30))
        with tempfile.TemporaryDirectory(prefix=".assemble-", dir=output) as temp_name:
            temp = Path(temp_name)
            listing = temp / "clips.ffconcat"
            escaped = [path.as_posix().replace("'", "'\\''") for path, _ in inputs]
            listing.write_text(
                "ffconcat version 1.0\n" + "".join(f"file '{p}'\n" for p in escaped),
                encoding="utf-8",
            )
            rendered = temp / "film.mp4"
            remux_warnings = []
            _run(
                [
                    ffmpeg,
                    "-hide_banner",
                    "-nostdin",
                    "-v",
                    "warning",
                    "-protocol_whitelist",
                    "file,pipe",
                    "-f",
                    "concat",
                    "-safe",
                    "0",
                    "-i",
                    str(listing),
                    "-map",
                    "0",
                    "-c",
                    "copy",
                    "-movflags",
                    "+faststart",
                    "-n",
                    str(rendered),
                ],
                timeout=timeout,
                diagnostics=remux_warnings,
            )
            final = _probe(ffprobe, rendered)
            if final["streams"] != metadata[0]["streams"]:
                raise AssemblyBlocked("CINE_ASSEMBLY_OUTPUT_STREAMS_CHANGED", final["streams"])
            video = next(s for s in final["streams"] if s["codec_type"] == "video")
            tolerance = max(
                0.15, (2 / float(Fraction(video["avg_frame_rate"])) + 0.03) * len(clips)
            )
            if abs(final["duration_seconds"] - duration) > tolerance:
                raise AssemblyBlocked(
                    "CINE_ASSEMBLY_DURATION_MISMATCH",
                    {"expected": duration, "actual": final["duration_seconds"]},
                )
            counts = [item["video_frames"] for item in metadata]
            if all(n is not None for n in counts) and final["video_frames"] != sum(counts):
                raise AssemblyBlocked("CINE_ASSEMBLY_FRAME_COUNT_MISMATCH", final["video_frames"])
            _run(
                [
                    ffmpeg,
                    "-hide_banner",
                    "-nostdin",
                    "-v",
                    "error",
                    "-xerror",
                    "-err_detect",
                    "explode",
                    "-protocol_whitelist",
                    "file,pipe",
                    "-i",
                    str(rendered),
                    "-map",
                    "0",
                    "-f",
                    "null",
                    "-",
                ],
                timeout=timeout,
            )
            _verify_inputs(inputs, topic)
            if _inside(topic, target) != target or _inside(topic, receipt_path) != receipt_path:
                raise AssemblyBlocked("CINE_ASSEMBLY_PATH_ESCAPE", str(output))
            digest = _digest(rendered)
            receipt = {
                "schema_version": 1,
                "assembly_id": identity,
                "status": "assembled",
                "media_kind": "video",
                "path": str(target),
                "sha256": digest,
                "bytes": rendered.stat().st_size,
                "duration_seconds": final["duration_seconds"],
                "clips": compact,
                "acceptance": "pending",
                "unverified": UNVERIFIED,
                "metadata": {
                    **final,
                    "decode_verified": True,
                    "lossless_concat": True,
                    "remux_warnings": remux_warnings,
                },
                "receipt_path": str(receipt_path),
            }
            try:
                os.link(rendered, target)
            except FileExistsError:
                if not target.is_file() or _digest(target) != digest:
                    raise AssemblyBlocked("CINE_ASSEMBLY_OUTPUT_EXISTS", str(target)) from None
            saved = temp / "receipt.json"
            saved.write_bytes(_json_bytes(receipt))
            try:
                os.link(saved, receipt_path)
            except FileExistsError:
                concurrent = _cached(receipt_path, target, identity, compact)
                if concurrent is None:
                    raise AssemblyBlocked(
                        "CINE_ASSEMBLY_RECEIPT_MISMATCH", str(receipt_path)
                    ) from None
                return concurrent
            return receipt
    except AssemblyBlocked as exc:
        return {
            "status": "blocked",
            "error_code": exc.code,
            "detail": exc.detail,
            "acceptance": "pending",
        }
    except (
        OSError,
        ValueError,
        TypeError,
        KeyError,
        ZeroDivisionError,
        subprocess.TimeoutExpired,
    ) as exc:
        return {
            "status": "blocked",
            "error_code": "CINE_ASSEMBLY_FAILED",
            "detail": str(exc),
            "acceptance": "pending",
        }
