"""Name the source film's rendering medium, so an adaptation draws in the same medium by default.

The cast, art and storyboard skills each offer a few style presets. Without a reading of the
source, every production fell back to one default; a live-action source then came out as a
painted illustration. A few frames spread over the film proper (opening teaser left out) are
shown to the vision model, which picks the matching preset or ``other``.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import subprocess
import tempfile
import urllib.request
from pathlib import Path

try:
    from .frame_cache import FrameCache
    from .production import atomic_json
    from .transcribe import gateway_settings
except ImportError:
    from frame_cache import FrameCache
    from production import atomic_json
    from transcribe import gateway_settings

SCHEMA_VERSION = 1
# The preset ids of cine-characters, cine-art and cine-storyboard; ``other`` keeps their default.
STYLES = ("photoreal", "semi-realistic", "ghibli", "other")
FRAMES = 6
FRAME_WIDTH = 768
FRAME_QUALITY = 3
TIMEOUT = 180

PROMPT = (
    "下面是同一部影片里均匀抽取的几帧画面。请判断这部片的画面媒介（怎么画出来的，不是题材）：\n"
    "photoreal = 真人实拍，或照片级写实的画面（包括照片级的 AI 生成画面）："
    "真实的皮肤、布料和光影，没有笔触和描线；\n"
    "semi-realistic = 半写实插画、厚涂或数字绘画：比例接近真人，但能看出笔触、绘画或插画质感；\n"
    "ghibli = 二维手绘动画或动漫：描线、平涂或赛璐璐上色；\n"
    "other = 三维动画、卡通、定格、混合媒介等以上都不是的。\n"
    "片头字幕、平台水印和烧录字幕不算画面媒介。"
    '必须仅返回 JSON：{"style": "photoreal|semi-realistic|ghibli|other", '
    '"evidence": "不超过 40 字，说明是哪些画面特征决定的"}'
)


def _frame(media: Path, seconds: float, target: Path) -> bytes:
    subprocess.run(
        [
            "ffmpeg",
            "-loglevel",
            "error",
            "-y",
            "-ss",
            f"{seconds:.3f}",
            "-i",
            str(media),
            "-frames:v",
            "1",
            "-vf",
            f"scale={FRAME_WIDTH}:-2",
            "-q:v",
            str(FRAME_QUALITY),
            str(target),
        ],
        check=True,
        capture_output=True,
        stdin=subprocess.DEVNULL,
    )
    data = target.read_bytes()
    if not data:
        raise RuntimeError(f"ffmpeg wrote an empty frame at {seconds:.3f}s")
    return data


def sample_times(shots: list[dict], count: int = FRAMES) -> list[float]:
    """Midpoints of up to ``count`` shots spread evenly over the film proper."""
    proper = [s for s in shots if not s.get("teaser") and s["end"] > s["start"]] or [
        s for s in shots if s["end"] > s["start"]
    ]
    if not proper:
        return []
    if len(proper) <= count:
        picked = proper
    else:
        step = (len(proper) - 1) / (count - 1)
        picked = [proper[round(i * step)] for i in range(count)]
    return [round(s["start"] + (s["end"] - s["start"]) / 2, 3) for s in picked]


def _ask(frames: list[bytes], gateway) -> tuple[dict, dict]:
    base_url, api_key, model = gateway
    content: list[dict] = [{"type": "text", "text": PROMPT}]
    for data in frames:
        url = "data:image/jpeg;base64," + base64.b64encode(data).decode("ascii")
        content.append({"type": "image_url", "image_url": {"url": url}})
    body = {"model": model, "messages": [{"role": "user", "content": content}], "temperature": 0}
    request = urllib.request.Request(
        base_url.rstrip("/") + "/chat/completions",
        data=json.dumps(body).encode("utf-8"),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
        reply = json.loads(response.read().decode("utf-8"))
    text = reply["choices"][0]["message"]["content"]
    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        raise ValueError("reply holds no JSON object")
    value = json.loads(match.group(0))
    if not isinstance(value, dict) or value.get("style") not in STYLES:
        raise ValueError(f"reply names no known style: {text[:200]}")
    evidence = str(value.get("evidence") or "").strip()[:80]
    return {"style": value["style"], "evidence": evidence}, reply.get("usage") or {}


def read_visual_style(media: Path, project: Path, *, gateway=None, frame_cache=None) -> dict:
    """Read once per source revision and shot notes; reuse the saved reading while they match."""
    project = Path(project).resolve()
    manifest = json.loads((project / "project.json").read_text(encoding="utf-8"))
    revision = manifest.get("current_revision")
    if not isinstance(revision, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", revision):
        raise ValueError("committed revision required")
    folder = (project / "revisions" / revision).resolve()
    if not folder.is_relative_to(project):
        raise ValueError("revision escapes project")
    source = json.loads((project / "source.json").read_text(encoding="utf-8"))
    notes_raw = (folder / "shot_notes.json").read_bytes()
    notes = json.loads(notes_raw)
    if notes.get("source_id") != source["source_id"] or notes.get("revision_id") != revision:
        raise ValueError("shot notes belong to another source revision")
    gateway = gateway if gateway is not None else gateway_settings()
    times = sample_times(notes.get("shots") or [])
    hashes = {
        "prompt": hashlib.sha256(PROMPT.encode("utf-8")).hexdigest(),
        "shot_notes": hashlib.sha256(notes_raw).hexdigest(),
        "sampling": hashlib.sha256(
            json.dumps({"times": times, "width": FRAME_WIDTH, "quality": FRAME_QUALITY}).encode()
        ).hexdigest(),
    }
    target = folder / "visual_style.json"
    try:
        cached = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        cached = {}
    if (
        isinstance(cached, dict)
        and cached.get("schema_version") == SCHEMA_VERSION
        and cached.get("status") == "read"
        and cached.get("style") in STYLES
        and cached.get("model") == gateway[2]
        and cached.get("input_sha256") == hashes
    ):
        return cached
    result = {
        "schema_version": SCHEMA_VERSION,
        "source_id": source["source_id"],
        "revision_id": revision,
        "model": gateway[2],
        "status": "unavailable",
        "style": None,
        "evidence": "",
        "frame_seconds": times,
        "input_sha256": hashes,
        "errors": [],
    }
    if not all(gateway):
        result["errors"].append("no vision model configured")
    elif not times:
        result["errors"].append("no shots to sample")
    else:
        cache = FrameCache(media, frame_cache)
        try:
            with tempfile.TemporaryDirectory(prefix="cine-style-frames-") as temp:
                frames = [
                    cache.get(
                        seconds,
                        FRAME_WIDTH,
                        lambda seconds=seconds, k=k: _frame(
                            Path(media), seconds, Path(temp) / f"{k}.jpg"
                        ),
                        quality=FRAME_QUALITY,
                    )
                    for k, seconds in enumerate(times)
                ]
            reading, usage = _ask(frames, gateway)
            result.update(status="read", usage=usage, **reading)
        except Exception as exc:  # noqa: BLE001 - recorded; the default style still applies
            result["errors"].append(f"{type(exc).__name__}: {exc}")
    atomic_json(target, result)
    return result
