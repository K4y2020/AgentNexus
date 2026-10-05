"""Read how each line of dialogue is performed, from frames taken while it is spoken.

The story reading samples a few stills per 30-second batch, which is enough to
follow the plot but misses most lines: a character sobbing through one line and
laughing through the next looks the same in a still taken ten seconds away. A
faithful script then has to guess the performance from the words, and a line of
complaint can come out as a joke. So every spoken line gets its own frames, taken
while it is said, and a vision model names the emotion the faces show.

Only the frames decide. The line's text is given so the model knows which line a
frame belongs to, and it is told not to infer the emotion from the words. A frame
that shows no readable face is recorded as unreadable, not guessed.
"""

from __future__ import annotations

import base64
import hashlib
import json
import subprocess
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

try:
    from .frame_cache import FrameCache
except ImportError:
    from frame_cache import FrameCache

METHOD = "frames_at_line"
UNREADABLE = "看不清"
BATCH_ROWS = 8  # lines per request; consecutive lines also give each other context
WORKERS = 4
FRAME_WIDTH = 512
FRAME_QUALITY = 5
SHORT_LINE = 0.8  # seconds; a shorter line gets one frame, at its middle
POINTS = (0.3, 0.75)  # where in a longer line its two frames are taken
TIMEOUT = 180

PROMPT = (
    "下面是一段影片里连续的若干句台词。"
    "每句台词后面附有说这句话时的画面（按时间先后，1 到 2 张）。\n"
    "请只根据画面里人物的表情、眼泪、嘴型和肢体动作，判断每句台词说出时的情绪和可见表演。"
    "台词文字只用来说明画面属于哪一句，不要凭台词字面意思猜情绪：同一句话哭着说和笑着说都可能。\n"
    "画面里看不清人脸，或者是空镜、字卡、黑场时，emotion 写「看不清」，其余字段留空。\n"
    "必须仅返回 JSON 数组，每句一项，不要遗漏：\n"
    '[{"n": 序号, "emotion": "2 到 6 个字的情绪，如 哭诉、强忍泪水、冷笑、平静、惊喜、调侃", '
    '"visible": "不超过 30 字的可见表演，如 满脸泪水边哭边说、嘴角上扬斜眼看人", '
    '"who": "画面里表现出这个情绪的人，按外貌描述，如 中年妇人、白衣少女"}]'
    "\n每句另附候选人物。返回 speaker：看得到嘴型或明确说话动作时用对应的 P 编号；"
    "旁白、画外音或反应镜头里看不到说话人时填 offscreen；无法判断填 unclear。"
    "只允许选择该句候选编号，不要把听者或照片里的人当成说话人。情绪字段继续只描述画面。"
    "一句里若有多个人轮流说话，无法唯一对应时填 unclear。"
)


def people_for_line(notes, row):
    """Any visible candidate must occur in a shot overlapping this whole line."""
    ids = {
        person
        for shot in notes.get("shots") or []
        if shot["start"] < row["end"] and shot["end"] > row["start"]
        for person in shot.get("people") or []
    }
    return [person for person in notes.get("people") or [] if person["id"] in ids]


def performance_inputs(notes, row):
    table = {k: notes.get(k) for k in ("schema_version", "source_id", "revision_id")}
    table["people"] = people_for_line(notes, row)
    return {
        "prompt": hashlib.sha256(PROMPT.encode("utf-8")).hexdigest(),
        "sampling": {
            "width": FRAME_WIDTH,
            "quality": FRAME_QUALITY,
            "points": list(POINTS),
            "short_line": SHORT_LINE,
        },
        "shot_notes": hashlib.sha256(
            json.dumps(table, sort_keys=True, ensure_ascii=False).encode("utf-8")
        ).hexdigest(),
        "line": hashlib.sha256(
            json.dumps({k: row.get(k) for k in ("text", "start", "end")}, sort_keys=True).encode(
                "utf-8"
            )
        ).hexdigest(),
    }


def migrate_performance_inputs(notes, rows):
    """Keep old readings only when their full-table evidence still matches."""
    if (FRAME_WIDTH, FRAME_QUALITY, SHORT_LINE, POINTS) != (512, 5, 0.8, (0.3, 0.75)):
        return
    table = {
        k: notes.get(k) for k in ("schema_version", "source_id", "revision_id", "people", "shots")
    }
    legacy_hash = hashlib.sha256(
        json.dumps(table, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()
    for row in rows:
        seen = row.get("performance") or {}
        inputs = performance_inputs(notes, row)
        legacy_inputs = {k: v for k, v in inputs.items() if k != "sampling"}
        if (
            seen.get("schema_version") == 2
            and seen.get("model")
            and seen.get("input_sha256") == {**legacy_inputs, "shot_notes": legacy_hash}
            and seen.get("speaker")
            in {"offscreen", "unclear", *(p["id"] for p in people_for_line(notes, row))}
        ):
            seen["input_sha256"] = inputs


def performance_current(notes, row, model=None):
    """A visible speaker is usable only for the table and line actually read."""
    seen = row.get("performance") or {}
    return (
        seen.get("schema_version") == 2
        and seen.get("input_sha256") == performance_inputs(notes, row)
        and (model is None or seen.get("model") == model)
        and seen.get("speaker")
        in {"offscreen", "unclear", *(p["id"] for p in people_for_line(notes, row))}
    )


def eligible(row: dict) -> bool:
    """A spoken line of the film proper: teaser rows are heard again where they belong."""
    return bool(str(row.get("text") or "").strip()) and not row.get("teaser")


def frame_times(row: dict) -> list[float]:
    start, end = float(row["start"]), float(row["end"])
    span = max(0.0, end - start)
    if span < SHORT_LINE:
        return [round(start + span / 2, 3)]
    return [round(start + span * p, 3) for p in POINTS]


def _extract(media: Path, seconds: float, target: Path) -> bytes:
    subprocess.run(
        [
            "ffmpeg",
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


def _request(batch: list[tuple[int, dict, list[bytes]]]) -> list[dict]:
    content: list[dict] = [{"type": "text", "text": PROMPT}]
    for n, row, frames in batch:
        content.append(
            {
                "type": "text",
                "text": f"第 {n} 句 [{row['start']:.1f}s-{row['end']:.1f}s]「{row['text']}」"
                + (
                    "\n候选人物：" + json.dumps(row["_speaker_candidates"], ensure_ascii=False)
                    if "_speaker_candidates" in row
                    else ""
                ),
            }
        )
        for data in frames:
            url = "data:image/jpeg;base64," + base64.b64encode(data).decode("ascii")
            content.append({"type": "image_url", "image_url": {"url": url}})
    return [{"role": "user", "content": content}]


def _parse(content: str) -> list:
    if "```json" in content:
        content = content.split("```json", 1)[1].split("```", 1)[0]
    elif "```" in content:
        content = content.split("```", 1)[1].split("```", 1)[0]
    value = json.loads(content.strip())
    if not isinstance(value, list):
        raise ValueError("reply is not a JSON array")
    return value


class _BatchReplyError(ValueError):
    def __init__(self, message, receipt):
        super().__init__(message)
        self.receipt = receipt


class _BatchReading(dict):
    def __init__(self, found, receipt):
        super().__init__(found)
        self.receipt = receipt


def _ask(batch, gateway) -> dict[int, dict]:
    import urllib.request

    base_url, api_key, model = gateway
    body = {"model": model, "messages": _request(batch), "temperature": 0.1}
    request = urllib.request.Request(
        base_url.rstrip("/") + "/chat/completions",
        data=json.dumps(body).encode("utf-8"),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
        raw_response = response.read().decode("utf-8")
    receipt = {"raw_response": raw_response, "reply": None, "usage": {}}
    try:
        reply = json.loads(raw_response)
        receipt.update(reply=reply, usage=reply.get("usage") or {})
        items = _parse(reply["choices"][0]["message"]["content"])
    except (ValueError, TypeError, AttributeError, KeyError, IndexError) as exc:
        raise _BatchReplyError(f"invalid batch JSON: {exc}", receipt) from exc
    wanted = {n for n, _row, _frames in batch}
    seen = set()
    for item in items:
        if not isinstance(item, dict) or type(item.get("n")) is not int:
            raise _BatchReplyError("batch contains an invalid line number", receipt)
        if item["n"] not in wanted:
            continue
        if item["n"] in seen:
            raise _BatchReplyError(f"batch repeats line {item['n']}", receipt)
        seen.add(item["n"])
    if seen != wanted:
        raise _BatchReplyError(f"batch omits lines {sorted(wanted - seen)}", receipt)
    candidates = {
        n: {p["id"] for p in row.get("_speaker_candidates", [])} for n, row, _frames in batch
    }
    constrained = {n for n, row, _frames in batch if "_speaker_candidates" in row}
    found: dict[int, dict] = {}
    for item in items:
        if not isinstance(item, dict) or item.get("n") not in wanted:
            continue
        emotion = str(item.get("emotion") or "").strip()
        if not emotion:
            continue
        speaker = item.get("speaker")
        if item["n"] in constrained and speaker not in {
            "offscreen",
            "unclear",
            *candidates[item["n"]],
        }:
            continue
        found[item["n"]] = {
            "emotion": emotion[:12],
            "visible": ""
            if emotion == UNREADABLE
            else str(item.get("visible") or "").strip()[:60],
            "who": "" if emotion == UNREADABLE else str(item.get("who") or "").strip()[:20],
            **({"speaker": speaker} if item["n"] in constrained else {}),
        }
    return _BatchReading(found, receipt)


def read_line_performance(
    media: Path,
    rows: list[dict],
    *,
    gateway=None,
    shot_notes=None,
    frame_cache: Path | None = None,
) -> dict:
    """Record ``performance`` on each spoken row that lacks one; returns a summary.

    Rows already read are kept, so a partial run is finished by the next one.
    ``gateway`` is ``(base_url, api_key, model)``; the AgentNexus gateway by default.
    """
    started = time.perf_counter()
    if gateway is None:
        try:
            from .transcribe import gateway_settings
        except ImportError:  # loaded as a standalone module
            from transcribe import gateway_settings
        gateway = gateway_settings()
    hashes = {}
    if shot_notes is not None:
        migrate_performance_inputs(shot_notes, rows)
        for i, row in enumerate(rows):
            if eligible(row):
                hashes[i] = performance_inputs(shot_notes, row)

    def current(_i, row):
        seen = row.get("performance") or {}
        return bool(seen) and (
            shot_notes is None
            or (gateway[2] is not None and performance_current(shot_notes, row, gateway[2]))
        )

    pending = [(i, row) for i, row in enumerate(rows) if eligible(row) and not current(i, row)]
    original_indices = {id(row): i for i, row in enumerate(rows)}
    if shot_notes is not None:
        for _i, row in pending:
            row.pop("performance", None)
    total = sum(1 for row in rows if eligible(row))
    summary: dict = {
        "method": METHOD,
        "model": gateway[2],
        "lines": total,
        "batches": [],
        "model_calls": 0,
        "usage": {},
        "frame_seconds": 0.0,
        "request_seconds": 0.0,
        "seconds": 0.0,
    }
    if not all(gateway):
        summary["seconds"] = time.perf_counter() - started
        return {**summary, "status": "unavailable", "error": "no vision model configured"}
    if not pending:
        summary["seconds"] = time.perf_counter() - started
        return {**summary, "status": "read", "read": total, "unreadable": _unreadable(rows)}

    errors: list[str] = []
    cache = FrameCache(media, frame_cache)
    with tempfile.TemporaryDirectory(prefix="cine-line-frames-") as folder:
        frame_started = time.perf_counter()
        numbered = []
        for n, (i, row) in enumerate(pending, 1):
            if shot_notes is not None:
                row["_speaker_candidates"] = people_for_line(shot_notes, row)
            times = frame_times(row)
            try:
                frames = [
                    cache.get(
                        t,
                        FRAME_WIDTH,
                        lambda t=t, k=k, i=i: _extract(media, t, Path(folder) / f"{i}_{k}.jpg"),
                        quality=FRAME_QUALITY,
                    )
                    for k, t in enumerate(times)
                ]
            except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
                errors.append(f"frames for line {i}: {type(exc).__name__}: {exc}")
                continue
            numbered.append((n, row, frames, times))
        batches = [numbered[k : k + BATCH_ROWS] for k in range(0, len(numbered), BATCH_ROWS)]
        summary["frame_seconds"] = time.perf_counter() - frame_started

        def run(batch):
            wanted = {n for n, _row, _frames, _t in batch}
            receipt = {
                "line_numbers": sorted(wanted),
                "source_indices": [original_indices[id(row)] for _n, row, _f, _t in batch],
                "attempts": [],
                "request_count": 0,
                "usage": {},
                "seconds": 0.0,
                "status": "unread",
            }
            error = None
            for attempt in range(1, 3):
                asked = time.perf_counter()
                record = {"attempt": attempt, "reply": None, "usage": {}}
                retry = False
                found = {}
                try:
                    found = _ask([(n, row, frames) for n, row, frames, _t in batch], gateway)
                    record.update(getattr(found, "receipt", {}))
                    if set(found) != wanted:
                        raise _BatchReplyError("batch did not return every requested line", record)
                except _BatchReplyError as exc:
                    record.update(exc.receipt)
                    error = f"{type(exc).__name__}: {exc}"
                    record["error"] = error
                    retry = True
                except Exception as exc:  # noqa: BLE001 - reported; those lines stay unread
                    detail = exc.read().decode("utf-8", "replace") if hasattr(exc, "read") else ""
                    error = f"{type(exc).__name__}: {exc} {detail}".strip()
                    record["error"] = error
                record["seconds"] = time.perf_counter() - asked
                receipt["attempts"].append(record)
                receipt["request_count"] += 1
                receipt["seconds"] += record["seconds"]
                for key, value in record["usage"].items():
                    if type(value) in (int, float):
                        receipt["usage"][key] = receipt["usage"].get(key, 0) + value
                if "error" not in record:
                    receipt["status"] = "read"
                    return found, None, receipt
                if not retry:
                    break
            return {}, error, receipt

        request_started = time.perf_counter()
        with ThreadPoolExecutor(max_workers=WORKERS) as pool:
            results = list(pool.map(run, batches))
        summary["request_seconds"] = time.perf_counter() - request_started
    for batch, (found, error, receipt) in zip(batches, results, strict=True):
        summary["batches"].append(receipt)
        summary["model_calls"] += len(receipt["attempts"])
        for attempt in receipt["attempts"]:
            for key, value in attempt["usage"].items():
                if type(value) in (int, float):
                    summary["usage"][key] = summary["usage"].get(key, 0) + value
        if error:
            errors.append(error)
        for n, row, _frames, times in batch:
            if n in found:
                if shot_notes is not None:
                    allowed = {
                        "offscreen",
                        "unclear",
                        *(p["id"] for p in row["_speaker_candidates"]),
                    }
                    if found[n].get("speaker") not in allowed:
                        errors.append(f"invalid speaker for line {n}")
                        continue
                row["performance"] = {
                    **found[n],
                    "at": times,
                    **(
                        {
                            "schema_version": 2,
                            "model": gateway[2],
                            "input_sha256": hashes[original_indices[id(row)]],
                        }
                        if shot_notes is not None
                        else {}
                    ),
                }
    for row in rows:
        row.pop("_speaker_candidates", None)
    done = sum(1 for i, row in enumerate(rows) if eligible(row) and current(i, row))
    summary.update(read=done, unreadable=_unreadable(rows))
    if errors:
        summary["errors"] = errors[:5]
    summary["status"] = "read" if done == total else ("partial" if done else "unavailable")
    summary["seconds"] = time.perf_counter() - started
    return summary


def _unreadable(rows: list[dict]) -> int:
    return sum(1 for row in rows if (row.get("performance") or {}).get("emotion") == UNREADABLE)


def describe(performance: dict | None, *, who: bool = True) -> str:
    """The short note a transcript or a reviewer reads.

    For example ``蓑衣老船夫，焦急催促：皱眉朝岸上连连挥手``. The person is named
    because a frame can show the listener rather than the speaker.
    """
    if not isinstance(performance, dict) or not performance.get("emotion"):
        return ""
    emotion = performance["emotion"]
    if emotion == UNREADABLE:
        return ""
    visible = performance.get("visible") or ""
    note = f"{emotion}：{visible}" if visible else emotion
    person = performance.get("who") if who else ""
    return f"{person}，{note}" if person else note
