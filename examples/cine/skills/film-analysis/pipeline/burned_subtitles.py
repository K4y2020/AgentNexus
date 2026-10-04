"""Read the subtitles burned into a video's frames, and fold them into the transcript.

ASR hears speech. A film's own captions also carry what it never says aloud: an
inner voice, a mumbled aside, a line under music. Those lines are part of the
source, and a faithful adaptation has to keep them. The captions sit in a band
near the bottom of the frame, so the band is read with OCR at a fixed rate and
consecutive identical readings become one timed row.

Merging never removes a heard line: a caption that ASR already holds lends the
row its spelling where the two align (a homophone, a dropped character), a
caption that ASR heard badly corrects that row's wording, and a caption with no
matching speech becomes a new row with no speaker (attribution assigns it).
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher
from pathlib import Path

SOURCE_TAG = "burned_subtitle"
ENGINE = "rapidocr"
BAND = (0.72, 0.95)  # top and bottom of the caption band, as fractions of frame height
SAMPLE_STEP = 0.35  # seconds between samples; captions stay on screen longer
LONG_VIDEO = 1800.0  # seconds; sampling is halved beyond this
MIN_CONFIDENCE = 0.8
CENTER = (0.12, 0.88)  # a caption's box centre lies between these fractions of the width
BASELINE = 0.04  # captions share one baseline: their bottom edge stays within this band
MIN_BASELINE_HITS = 3
SAME_TEXT = 0.85  # readings this alike are one caption
REPRESENTED = 0.65  # share of a caption's characters ASR already holds
CORRECTS = 0.4
SHORT_FLOOR = 0.5  # the same, for a caption of 4 characters or fewer, beside a heard row
TOUCH = 0.5  # seconds a heard row may sit apart from a caption and still be its wording
STABLE_HITS = 2  # a caption read this often may respell heard words; one reading may be OCR noise
WORDING = 0.5  # share of a caption's characters that must align before it respells a row
MAX_SWAP = 3  # characters a caption may replace at one spot
MAX_INSERT = 2  # characters a caption may add between two aligned ones
MERGE_METHOD = "caption_wording_v1"
_CJK = re.compile(r"[㐀-鿿]")
_KEEP = re.compile(r"[\w㐀-鿿]+")
_ANNOTATION = re.compile(r"[（(][^）)]*[）)]")  # an on-screen note such as （口误）, not speech


def _norm(text: str) -> str:
    return "".join(_KEEP.findall(text.casefold()))


def _similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio() if a and b else 0.0


def _coverage(needle: str, hay: str) -> float:
    """Share of ``needle``'s characters found, in order, in ``hay``."""
    if not needle or not hay:
        return 0.0
    blocks = SequenceMatcher(None, needle, hay, autojunk=False).get_matching_blocks()
    return sum(block.size for block in blocks) / len(needle)


def _is_cjk(text: str) -> bool:
    letters = [c for c in text if c.isalpha()]
    return bool(letters) and sum(1 for c in letters if _CJK.match(c)) / len(letters) >= 0.5


def _lines(boxes: list[tuple[str, float, float, float]], prefer_cjk: bool) -> tuple[str, str]:
    """Caption text in the target script, and whatever else shares the band.

    ``boxes`` are ``(text, centre_x, centre_y, height)``. Boxes on one row form
    a line; a bilingual caption stacks two lines.
    """
    rows: list[list[tuple[str, float, float, float]]] = []
    for box in sorted(boxes, key=lambda b: b[2]):
        if rows and abs(box[2] - rows[-1][0][2]) <= max(box[3], rows[-1][0][3]) * 0.6:
            rows[-1].append(box)
        else:
            rows.append([box])
    lines = ["".join(b[0] for b in sorted(row, key=lambda b: b[1])).strip() for row in rows]
    lines = [_ANNOTATION.sub("", line).strip() for line in lines]
    lines = [line for line in lines if _norm(line)]
    wanted = [line for line in lines if _is_cjk(line) == prefer_cjk]
    other = [line for line in lines if _is_cjk(line) != prefer_cjk]
    # Text only in the other script is a label or a sign in the scene, not a caption.
    return (" ".join(wanted), " ".join(other)) if wanted else ("", "")


def _on_baseline(readings: list[tuple[float, str, str, float]]) -> list[tuple[float, str, str]]:
    """Keep the readings that sit on the video's caption baseline.

    Captions are anchored to one edge, so their bottom edge (a fraction of the
    frame height) clusters; a sign, a label or a logo lands anywhere. With no
    cluster there is no caption track, and nothing is kept.
    """
    seen = sorted(r[3] for r in readings if r[1])
    best: tuple[int, float] = (0, 0.0)
    for i, low in enumerate(seen):
        hits = sum(1 for y in seen[i:] if y - low <= BASELINE)
        if hits > best[0]:
            best = (hits, low)
    hits, low = best
    if hits < MIN_BASELINE_HITS or hits < 0.5 * len(seen):
        return [(at, "", "") for at, *_ in readings]
    return [
        (at, text, alt) if text and low - 0.005 <= base <= low + BASELINE + 0.005 else (at, "", "")
        for at, text, alt, base in readings
    ]


def _runs(samples: list[tuple[float, str, str]], duration: float) -> list[dict]:
    """Timed rows from per-sample readings: alike neighbours are one caption."""
    rows: list[dict] = []
    current: dict | None = None
    gap = 0
    for at, text, alt in samples:
        if text and current and _similarity(_norm(text), _norm(current["text"])) >= SAME_TEXT:
            current["last"], current["hits"], gap = at, current["hits"] + 1, 0
            if len(text) > len(current["text"]):
                current["text"], current["alt"] = text, alt
            continue
        if not text and current and gap < 1:
            gap += 1  # one blank frame inside a caption is a transition, not its end
            continue
        if current:
            rows.append(current)
            current = None
        gap = 0
        if text:
            current = {"first": at, "last": at, "hits": 1, "text": text, "alt": alt}
    if current:
        rows.append(current)
    out = []
    for run in rows:
        chars = len(_norm(run["text"]))
        if run["hits"] < 2 and chars < 4:
            continue  # a single stray reading of a short string is noise
        row = {
            "start": round(max(0.0, run["first"] - SAMPLE_STEP / 2), 3),
            "end": round(
                min(duration, run["last"] + SAMPLE_STEP / 2) if duration else run["last"], 3
            ),
            "text": run["text"],
            "hits": run["hits"],
        }
        if run["alt"]:
            row["alt_text"] = run["alt"]
        out.append(row)
    return out


def read_burned_subtitles(media: Path, *, language: str | None = "zh") -> dict:
    """OCR the caption band. ``status`` is ``read``, ``none_found`` or ``unavailable``."""
    try:
        import cv2
        from rapidocr_onnxruntime import RapidOCR
    except ImportError as exc:
        return {"status": "unavailable", "engine": ENGINE, "rows": [], "error": str(exc)}
    prefer_cjk = (language or "zh").lower().startswith(("zh", "ja", "ko"))
    cap = cv2.VideoCapture(str(media))
    try:
        fps = cap.get(cv2.CAP_PROP_FPS) or 0.0
        frames = cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0.0
        width, height = cap.get(cv2.CAP_PROP_FRAME_WIDTH), cap.get(cv2.CAP_PROP_FRAME_HEIGHT)
        if fps <= 0 or frames <= 0 or width <= 0 or height <= 0:
            return {
                "status": "unavailable",
                "engine": ENGINE,
                "rows": [],
                "error": "unreadable video",
            }
        duration = frames / fps
        step = SAMPLE_STEP * (2 if duration > LONG_VIDEO else 1)
        # The band is a thin strip: keep detection at its own size instead of
        # letting the default resize it to 736 px tall (12x slower, same text).
        ocr = RapidOCR(det_limit_type="max", det_limit_side_len=int(width))
        top, bottom = int(height * BAND[0]), int(height * BAND[1])
        readings: list[tuple[float, str, str, float]] = []
        index, at = 0, 0.0
        while at < duration:
            target = round(at * fps)
            while index < target and cap.grab():
                index += 1
            ok, frame = cap.read()
            index += 1
            if not ok:
                break
            found, _ = ocr(frame[top:bottom, :], use_cls=False)
            boxes = []
            for polygon, text, confidence in found or []:
                xs = [p[0] for p in polygon]
                ys = [p[1] for p in polygon]
                middle = (min(xs) + max(xs)) / 2 / width
                if float(confidence) >= MIN_CONFIDENCE and CENTER[0] <= middle <= CENTER[1]:
                    boxes.append((text, middle, (min(ys) + max(ys)) / 2, max(ys) - min(ys)))
            text, alt = _lines(boxes, prefer_cjk) if boxes else ("", "")
            base = (top + max((b[2] + b[3] / 2 for b in boxes), default=0.0)) / height
            readings.append((round(at, 3), text, alt, base))
            at += step
    finally:
        cap.release()
    rows = _runs(_on_baseline(readings), duration)
    return {"status": "read" if rows else "none_found", "engine": ENGINE, "rows": rows}


def _chars(text: str) -> list[tuple[int, str]]:
    """Word characters of ``text`` with their positions."""
    return [(i, ch) for i, ch in enumerate(text) if _KEEP.fullmatch(ch)]


def _take_wording(row: dict, caption: dict) -> bool:
    """Spell a heard row the way the film's caption does, where the two align.

    Only characters next to an aligned stretch change: a homophone swapped, a
    dropped character put back. Words only ASR heard stay.
    """
    if row.get("source") or caption.get("hits", STABLE_HITS) < STABLE_HITS:
        return False
    text = str(row.get("text") or "")
    heard, shown = _chars(text), _chars(str(caption.get("text") or ""))
    a = "".join(ch.casefold() for _, ch in heard)
    b = "".join(ch.casefold() for _, ch in shown)
    if not a or not b:
        return False
    ops = SequenceMatcher(None, a, b, autojunk=False).get_opcodes()
    if sum(i2 - i1 for tag, i1, i2, _, _ in ops if tag == "equal") / len(b) < WORDING:
        return False
    edits = []
    for k, (tag, i1, i2, j1, j2) in enumerate(ops):
        before = k > 0 and ops[k - 1][0] == "equal"
        after = k + 1 < len(ops) and ops[k + 1][0] == "equal"
        width = j2 - j1
        if tag == "insert" and before and after and width <= MAX_INSERT:
            at = heard[i1][0]
            edits.append((at, at, "".join(ch for _, ch in shown[j1:j2])))
            continue
        if tag != "replace" or width > MAX_SWAP or not (before or after):
            continue
        if before and after:
            if i2 - i1 > MAX_SWAP:
                continue
            span = heard[i1:i2]
        elif after:  # leading edge: the heard characters right before the aligned stretch
            if i2 - i1 < width:
                continue
            span = heard[i2 - width : i2]
        else:  # trailing edge: the heard characters right after it
            if i2 - i1 < width:
                continue
            span = heard[i1 : i1 + width]
        if span[-1][0] - span[0][0] != len(span) - 1:
            continue  # punctuation in between: not one spelling
        edits.append((span[0][0], span[-1][0] + 1, "".join(ch for _, ch in shown[j1:j2])))
    spelled = text
    for start, end, replacement in sorted(edits, reverse=True):
        spelled = spelled[:start] + replacement + spelled[end:]
    if spelled == text:
        return False
    row.setdefault("asr_text", text)
    row["text"] = spelled
    row["wording"] = SOURCE_TAG
    return True


def _best_row(rows: list[dict], text: str) -> dict | None:
    scored = [(_coverage(text, _norm(str(r.get("text") or ""))), r) for r in rows]
    scored = [item for item in scored if item[0] > 0]
    return max(scored, key=lambda item: item[0])[1] if scored else None


def merge_into_transcript(rows: list[dict], captions: list[dict]) -> tuple[list[dict], dict]:
    """Add what ASR missed and correct what it misheard; never drop a heard line."""
    merged = [dict(row) for row in rows]
    added = corrected = worded = 0
    for caption in captions:
        text = _norm(caption["text"])
        if not text:
            continue
        lo, hi = caption["start"] - 1.0, caption["end"] + 1.0
        near = [r for r in merged if r["end"] > lo and r["start"] < hi]
        heard = _norm("".join(str(r.get("text") or "") for r in near))
        short = len(text) < 3
        alt = _norm(caption.get("alt_text") or "")
        if max(_coverage(text, heard), _coverage(alt, heard) if alt else 0.0) >= (
            1.0 if short else REPRESENTED
        ):
            row = _best_row(near, text)
            if row is not None and _take_wording(row, caption):
                worded += 1
            continue
        overlap = [
            r
            for r in near
            if min(r["end"], caption["end"]) - max(r["start"], caption["start"])
            >= 0.5 * (r["end"] - r["start"])
        ]
        if len(overlap) == 1 and not short:
            spoken = _norm(str(overlap[0].get("text") or ""))
            if (
                _coverage(text, spoken) >= CORRECTS
                and 0.6 <= len(text) / max(len(spoken), 1) <= 1.6
                and not overlap[0].get("source")
            ):
                overlap[0].setdefault("asr_text", overlap[0]["text"])
                overlap[0]["text"] = caption["text"]
                overlap[0]["source"] = SOURCE_TAG
                corrected += 1
                continue
        touching = [
            r
            for r in near
            if not r.get("source")
            and r["start"] - TOUCH < caption["end"]
            and caption["start"] < r["end"] + TOUCH
        ]
        floor = SHORT_FLOOR if len(text) <= 4 else CORRECTS
        if any(_coverage(text, _norm(str(r.get("text") or ""))) >= floor for r in touching):
            # ASR heard this caption in its own homophones: the caption spells them.
            row = _best_row(touching, text)
            if row is not None and _take_wording(row, caption):
                worded += 1
            continue
        merged.append(
            {
                "start": caption["start"],
                "end": caption["end"],
                "text": caption["text"],
                "source": SOURCE_TAG,
            }
        )
        added += 1
    merged.sort(key=lambda r: (r["start"], r["end"]))
    return merged, {"added": added, "corrected": corrected, "worded": worded}
