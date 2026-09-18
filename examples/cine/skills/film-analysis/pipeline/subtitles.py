"""Parse common subtitle formats and reconcile them with qualified ASR."""

from __future__ import annotations

import re
from contextlib import suppress
from difflib import SequenceMatcher
from html import unescape
from pathlib import Path

_TIMING_RE = re.compile(
    r"(?P<start>\d{1,2}:\d{2}(?::\d{2})?[,.]\d{1,3})\s*--?>\s*"
    r"(?P<end>\d{1,2}:\d{2}(?::\d{2})?[,.]\d{1,3})"
)
_ASS_RE = re.compile(
    r"Dialogue:\s*[^,]*,(?P<start>\d+:\d{2}:\d{2}\.\d{2}),"
    r"(?P<end>\d+:\d{2}:\d{2}\.\d{2}),[^,]*,(?P<body>.*)"
)


def parse_timestamp(value: str) -> float:
    """Parse SRT/VTT or ASS time into seconds."""
    value = value.strip().replace(",", ".")
    parts = value.split(":")
    if len(parts) == 2:
        hours = 0
        minutes, seconds = parts
    elif len(parts) == 3:
        hours, minutes, seconds = parts
    else:
        raise ValueError(f"Invalid subtitle timestamp: {value!r}")
    result = int(hours) * 3600 + int(minutes) * 60 + float(seconds)
    if result < 0:
        raise ValueError("Subtitle timestamp must be non-negative")
    return result


def clean_text(value: str) -> str:
    value = re.sub(r"\{[^}]*\}", "", value)
    value = re.sub(r"<[^>]+>", "", value)
    value = value.replace("\\N", "\n").replace("\\n", "\n")
    return unescape(value).strip()


def _row(start: str, end: str, text: str, index: int, fmt: str) -> dict:
    start_s = parse_timestamp(start)
    end_s = parse_timestamp(end)
    text = clean_text(text)
    if end_s <= start_s or not text:
        raise ValueError("Subtitle interval or text is empty")
    return {
        "id": f"{fmt.upper()} {index:03d}",
        "start": round(start_s, 3),
        "end": round(end_s, 3),
        "text": text,
        "source": fmt,
    }


def parse_subtitle_text(text: str, suffix: str) -> list[dict]:
    """Parse UTF-8 subtitle text without external dependencies."""
    fmt = suffix.lower().lstrip(".")
    if fmt in {"ssa"}:
        fmt = "ass"
    if fmt not in {"srt", "vtt", "ass"}:
        raise ValueError(f"Unsupported subtitle format: .{fmt}")
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    rows: list[dict] = []
    if fmt == "ass":
        for line in lines:
            match = _ASS_RE.match(line.strip())
            if not match:
                continue
            try:
                rows.append(_row(match["start"], match["end"], match["body"], len(rows) + 1, fmt))
            except ValueError:
                continue
        return rows
    index = 0
    while index < len(lines):
        match = _TIMING_RE.search(lines[index])
        if not match:
            index += 1
            continue
        body: list[str] = []
        index += 1
        while index < len(lines) and lines[index].strip():
            body.append(lines[index])
            index += 1
        with suppress(ValueError):
            rows.append(_row(match["start"], match["end"], "\n".join(body), len(rows) + 1, fmt))
    return rows


def parse_subtitle_file(path: Path) -> list[dict]:
    return parse_subtitle_text(path.read_text(encoding="utf-8-sig"), path.suffix)


def normalized_text(value: str) -> str:
    return re.sub(r"[^\w\u4e00-\u9fff]+", "", value.casefold())


def reconcile_dialogue(
    subtitles: list[dict], asr: list[dict], *, duration: float | None = None
) -> tuple[list[dict], list[str]]:
    """Use subtitle wording, ASR timing/gaps, and preserve disagreements."""
    cues: list[dict] = []
    warnings: list[str] = []
    used_asr: set[int] = set()
    conflict_count = 0
    for index, subtitle in enumerate(subtitles, 1):
        overlaps = [
            (i, row)
            for i, row in enumerate(asr)
            if row["end"] > subtitle["start"] - 0.75 and row["start"] < subtitle["end"] + 0.75
        ]
        asr_text = " ".join(
            row["text"].strip() for _, row in overlaps if row.get("text", "").strip()
        )
        speaker_values = {
            row.get("speaker", "").strip() for _, row in overlaps if row.get("speaker")
        }
        ratio = (
            SequenceMatcher(
                None, normalized_text(subtitle["text"]), normalized_text(asr_text)
            ).ratio()
            if asr_text
            else 0
        )
        conflicts = []
        if asr_text and ratio < 0.72:
            conflicts.append("字幕与 ASR 文本不一致")
            conflict_count += 1
        for i, _ in overlaps:
            used_asr.add(i)
        cue = {
            "id": f"dialogue:subtitle:{index}",
            "sourceId": subtitle["id"],
            "type": "dialogue",
            "start": subtitle["start"],
            "end": min(subtitle["end"], duration) if duration else subtitle["end"],
            "title": subtitle["text"],
            "text": subtitle["text"],
            "subtitleText": subtitle["text"],
            "asrText": asr_text or None,
            "provenance": "subtitle+asr" if asr_text else "subtitle",
            "conflicts": conflicts,
            "rawInterval": [subtitle["start"], subtitle["end"]],
            "timingNote": "" if not overlaps else "字幕时间轴 · ASR 已交叉核对",
            "status": (
                "字幕 + ASR · 文本冲突"
                if conflicts
                else "字幕 + ASR · 已对齐"
                if asr_text
                else "字幕 · 待核验"
            ),
            "speaker": next(iter(speaker_values)) if len(speaker_values) == 1 else "说话人未标注",
        }
        if cue["end"] > cue["start"]:
            cues.append(cue)
    for index, row in enumerate(asr, 1):
        if index - 1 in used_asr or not row.get("text", "").strip():
            continue
        start, end = row["start"], row["end"]
        if duration is not None:
            end = min(end, duration)
        if end <= start:
            continue
        cues.append(
            {
                "id": f"dialogue:asr:{index}",
                "sourceId": f"ASR {index:03d}",
                "type": "dialogue",
                "start": start,
                "end": end,
                "title": row["text"],
                "text": row["text"],
                "subtitleText": None,
                "asrText": row["text"],
                "provenance": "asr",
                "conflicts": ["ASR 片段未找到对应字幕"],
                "rawInterval": [row["start"], row["end"]],
                "timingNote": "ASR 补充 · 字幕未覆盖",
                "status": "ASR 补充 · 待核验",
                "speaker": row.get("speaker") or "说话人未标注",
            }
        )
    if conflict_count:
        warnings.append(f"发现 {conflict_count} 条字幕与 ASR 文本差异，请结合画面复核。")
    return cues, warnings
