"""Complete offscreen/unclear visual readings using bounded context, then rules."""

from __future__ import annotations

import base64
import hashlib
import json
import math
import re
import subprocess
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

try:
    from .frame_cache import FrameCache
    from .line_performance import (
        BATCH_ROWS,
        FRAME_QUALITY,
        FRAME_WIDTH,
        POINTS,
        SHORT_LINE,
        TIMEOUT,
        WORKERS,
        _extract,
        _parse,
        eligible,
        frame_times,
        performance_current,
    )
except ImportError:
    from frame_cache import FrameCache
    from line_performance import (
        BATCH_ROWS,
        FRAME_QUALITY,
        FRAME_WIDTH,
        POINTS,
        SHORT_LINE,
        TIMEOUT,
        WORKERS,
        _extract,
        _parse,
        eligible,
        frame_times,
        performance_current,
    )

METHOD = "visual_then_context_v1"
SCHEMA_VERSION = 1
CONTEXT_ROWS = 3
# A voice heard before its speaker walks in: the next shots in the same place count.
LOOKAHEAD_SHOTS = 2
LOOKAHEAD_SECONDS = 6.0
DELIVERIES = {"spoken", "voice_over"}
BASES = {"context", "caption", "none"}
_PERSON = re.compile(r"P[1-9][0-9]*\Z")

PROMPT = (
    "这些台词已经完成A步逐句画面读取，下面只补判断说话人和表达方式。"
    "A步结论为offscreen（未见说话人）或unclear（看不清或无法区分）。"
    "不要重新判断嘴型，也不要输出情绪、可见表演或表演者字段。"
    "每句独立判断，只使用该句附帧、人物表、该句前后台词及镜头记录；"
    "批中其他句仅为并行任务，不替代该句给出的前后文。\n"
    "speaker只能是人物表里的P编号、crowd（现场无法可靠编号的人）或unclear。"
    "delivery只能是spoken（现场说话，包括现场画外接话）或voice_over（旁白、内心独白）。"
    "voice_over不限在场人物，不要默认归某个主角。"
    "A步offscreen时，取帧所在镜头里的人物不能选为spoken：A已看过，未见他们在说话。"
    "带人名字幕的画面人物也不能因此当作现场说话人。"
    "这条限制只指frame_people，不指每个重叠镜头的人物；"
    "台词末尾擦到下一镜头而帧未取到那里时，下一镜头的人仍可为画外说话人。"
    "A步unclear时，可由前后文或人名字幕辨认现场人物。"
    "spoken的P编号须在实际重叠镜头、同一已知地点的紧邻普通镜头，"
    "或台词之后同一地点接着出现的镜头（最多两个、6秒内）中出现："
    "画外先闻其声、随后入画的人可以是说话人，看镜头记录里谁刚走进来。"
    "不能跨过插入画面、预告或未知地点借现场人物。"
    "crowd只能依据实际重叠镜头中的人群或已对应非角色的人；"
    "能确定说话者属于这种匿名人群但无法唯一选择P编号时，选crowd；"
    "crowd同样可以是spoken或voice_over。"
    "相邻镜头有人群不构成本句人群证据，缺证据选unclear。"
    "照片或物件中的人物不自动等于说话人；可由前后文选择实际对物件说话的人。\n"
    "basis只能是context（前后文、称呼及镜头内容）、caption（字幕或人名卡）或none。"
    "禁止mouth，不能编造张嘴说话或嘴型依据。"
    "reason不超过30字，说明具体依据。证据不足，或在不同明确编号人物之间无法分辨时，"
    "speaker写unclear、basis写none；匿名人群已有实际证据时仍选crowd。"
    "不要返回置信度。仅返回JSON数组，本批每个n恰好一项，不回答上下文句：\n"
    '[{"n":0,"speaker":"P1","delivery":"spoken","basis":"context","reason":"前句称呼指向现场接话者"}]'
)


def _digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()


def _people(notes):
    return {
        p["id"]
        for p in notes.get("people") or []
        if isinstance(p.get("id"), str) and _PERSON.fullmatch(p["id"])
    }


def _overlaps(notes, row):
    return [
        i
        for i, shot in enumerate(notes.get("shots") or [])
        if shot["start"] < row["end"] and shot["end"] > row["start"]
    ]


def _ordinary(shot):
    return shot.get("place") != "insert" and not shot.get("teaser")


def _known_place(shot, notes):
    place = shot.get("place")
    if not isinstance(place, str) or not re.fullmatch(r"L[1-9][0-9]*", place):
        return False
    locations = notes.get("locations")
    return locations is None or place in {location.get("id") for location in locations}


def _adjacent(notes, overlap):
    shots = notes.get("shots") or []
    if not overlap:
        return []
    found = []
    for anchor, neighbor in ((overlap[0], overlap[0] - 1), (overlap[-1], overlap[-1] + 1)):
        if neighbor < 0 or neighbor >= len(shots):
            continue
        current, other = shots[anchor], shots[neighbor]
        left, right = (other, current) if neighbor < anchor else (current, other)
        if (
            _ordinary(current)
            and _ordinary(other)
            and _known_place(current, notes)
            and _known_place(other, notes)
            and current["place"] == other["place"]
            and math.isclose(left["end"], right["start"], rel_tol=0, abs_tol=1e-6)
        ):
            found.append(neighbor)
    return found


def _ahead(notes, row, overlap):
    """Ordinary shots right after the line in its known location, where a voice's owner enters."""
    shots = notes.get("shots") or []
    if not overlap or not _known_place(shots[overlap[-1]], notes):
        return []
    found = []
    for index in range(overlap[-1] + 1, min(len(shots), overlap[-1] + 1 + LOOKAHEAD_SHOTS)):
        previous, shot = shots[index - 1], shots[index]
        if not (
            _ordinary(shot)
            and _known_place(shot, notes)
            and shot["place"] == shots[overlap[-1]]["place"]
            and math.isclose(previous["end"], shot["start"], rel_tol=0, abs_tol=1e-6)
            and shot["start"] - row["end"] <= LOOKAHEAD_SECONDS
        ):
            break
        found.append(index)
    return found


def _frame_people(notes, row):
    times = (row.get("performance") or {}).get("at")
    times = times if isinstance(times, list) and times else frame_times(row)
    shots = notes.get("shots") or []
    selected = [
        i
        for i, shot in enumerate(shots)
        if any(isinstance(at, (int, float)) and shot["start"] <= at < shot["end"] for at in times)
    ]
    return selected, sorted(
        {p for i in selected for p in shots[i].get("people") or []} & _people(notes)
    )


def check_constraints(judgment, notes, row, person_map=None):
    """Apply only deterministic rules, including the visual step's negative evidence."""
    people = _people(notes)
    shots = notes.get("shots") or []
    overlap = _overlaps(notes, row)
    actual = [i for i in overlap if _ordinary(shots[i])]
    adjacent = _adjacent(notes, overlap)
    ahead = _ahead(notes, row, overlap)
    overlap_people = {p for i in actual for p in shots[i].get("people") or []} & people
    adjacent_people = {p for i in adjacent for p in shots[i].get("people") or []} & people
    ahead_people = {p for i in ahead for p in shots[i].get("people") or []} & people
    frame_shots, frame_people = _frame_people(notes, row)
    mapped = {p.get("person_id"): p for p in (person_map or {}).get("people") or []}
    extra_people = {p for p in overlap_people if (mapped.get(p) or {}).get("status") == "extra"}
    crowd = any(shots[i].get("crowd") is True for i in actual)
    speaker, delivery, basis = (judgment.get(k) for k in ("speaker", "delivery", "basis"))
    reasons = []
    valid_speaker = isinstance(speaker, str) and speaker in {*people, "crowd", "unclear"}
    if not valid_speaker:
        reasons.append("说话人不在编号人物表或允许值中")
    if not isinstance(delivery, str) or delivery not in DELIVERIES:
        reasons.append("delivery不合法")
    if not isinstance(basis, str) or basis not in BASES:
        reasons.append("B步不能重新判断嘴型或使用非法依据")
    elif basis == "none":
        reasons.append("判断依据为none，需人工确认")
    if speaker == "unclear":
        reasons.append("说话人无法确定")
    if valid_speaker and speaker in people and delivery == "spoken":
        if speaker not in overlap_people | adjacent_people | ahead_people:
            reasons.append("现场说话人未在重叠、同地点紧邻或随后入画的普通镜头中出现")
        if (row.get("performance") or {}).get(
            "speaker"
        ) == "offscreen" and speaker in frame_people:
            reasons.append("A步未见说话人，取帧镜头人物不能由B步改判现场发言")
    post_mapping_required = bool(
        speaker == "crowd"
        and not crowd
        and not extra_people
        and any(
            (mapped.get(p) or {}).get("status") not in {"matched", "extra"} for p in overlap_people
        )
    )
    if speaker == "crowd" and not crowd and not extra_people:
        reasons.append(
            "无重叠人群标记；非角色映射未完成，需后验"
            if post_mapping_required
            else "重叠镜头无人群标记或已对应非角色人物"
        )
    if row.get("sentence_boundary_review"):
        reasons.append("台词缺少可靠切句时间，需分别核对发言")
    return {
        "status": "review" if reasons else "passed",
        "needs_review": bool(reasons),
        "review_reasons": reasons,
        "overlap_shots": overlap,
        "adjacent_shots": adjacent,
        "frame_shots": frame_shots,
        "frame_people": frame_people,
        "overlap_people": sorted(overlap_people),
        "adjacent_people": sorted(adjacent_people),
        "ahead_shots": ahead,
        "ahead_people": sorted(ahead_people),
        "spoken_people": sorted(overlap_people | adjacent_people | ahead_people),
        "crowd": crowd,
        "extra_people": sorted(extra_people),
        "post_mapping_required": post_mapping_required,
        "counts_as_present": bool(not reasons and delivery == "spoken" and speaker in people),
    }


def _shot_context(notes, row):
    shots = notes.get("shots") or []
    overlap = _overlaps(notes, row)
    if not overlap:
        return []
    selected = sorted(
        {
            *overlap,
            max(0, overlap[0] - 1),
            min(len(shots) - 1, overlap[-1] + 1),
            *_ahead(notes, row, overlap),
        }
    )
    return [
        {
            "index": shots[i].get("index", i),
            "relation": "overlap" if i in overlap else "before" if i < overlap[0] else "after",
            **{key: shots[i].get(key) for key in ("start", "end", "place", "what")},
            "people": list(shots[i].get("people") or []),
            "crowd": shots[i].get("crowd") is True,
            "teaser": bool(shots[i].get("teaser")),
        }
        for i in selected
    ]


def _line_text(index, row):
    return {
        "source_index": index,
        **{k: row.get(k) for k in ("id", "start", "end", "text", "teaser") if k in row},
    }


def _line_state(notes, rows, index):
    row = rows[index]
    return {
        "n": index,
        "line": _line_text(index, row),
        "a_step": {
            "speaker": (row.get("performance") or {}).get("speaker"),
            "at": (row.get("performance") or {}).get("at") or frame_times(row),
            "frame_people": _frame_people(notes, row)[1],
        },
        "context_lines": [
            _line_text(i, rows[i])
            for i in range(max(0, index - CONTEXT_ROWS), min(len(rows), index + CONTEXT_ROWS + 1))
            if i != index
        ],
        "shots": _shot_context(notes, row),
    }


def _person_table(notes):
    return [
        {key: p.get(key) for key in ("id", "look", "name_caption")}
        for p in notes.get("people") or []
    ]


def judgment_inputs(notes, rows, index, model):
    """Local context keys let unchanged lines reuse B independently of batch membership."""
    a = rows[index].get("performance") or {}
    return {
        "prompt": _digest(PROMPT),
        "model": model,
        "line_context": _digest(_line_state(notes, rows, index)),
        "people": _digest(_person_table(notes)),
        "a": _digest(
            {k: a.get(k) for k in ("speaker", "at", "model", "schema_version", "input_sha256")}
        ),
        "sampling": _digest(
            {
                "width": FRAME_WIDTH,
                "quality": FRAME_QUALITY,
                "points": POINTS,
                "short_line": SHORT_LINE,
            }
        ),
    }


def _target(notes, row, model):
    return (
        eligible(row)
        and performance_current(notes, row, model)
        and (row.get("performance") or {}).get("speaker") in {"offscreen", "unclear"}
    )


def judgment_current(notes, rows, index, model=None):
    seen = rows[index].get("judgment") or {}
    model = model if model is not None else (rows[index].get("performance") or {}).get("model")
    return bool(
        _target(notes, rows[index], model)
        and seen.get("schema_version") == SCHEMA_VERSION
        and seen.get("method") == METHOD
        and seen.get("model") == model
        and seen.get("input_sha256") == judgment_inputs(notes, rows, index, model)
        and seen.get("read") is True
    )


def build_request(batch, notes, rows):
    content = [
        {"type": "text", "text": PROMPT},
        {"type": "text", "text": json.dumps({"people": _person_table(notes)}, ensure_ascii=False)},
    ]
    for index, _row, frames in batch:
        content.append(
            {
                "type": "text",
                "text": json.dumps(_line_state(notes, rows, index), ensure_ascii=False),
            }
        )
        for data in frames:
            url = "data:image/jpeg;base64," + base64.b64encode(data).decode("ascii")
            content.append({"type": "image_url", "image_url": {"url": url}})
    return [{"role": "user", "content": content}]


class _BatchReplyError(ValueError):
    def __init__(self, message, receipt):
        super().__init__(message)
        self.receipt = receipt


class _Reply(dict):
    def __init__(self, reply, raw_response):
        super().__init__(reply)
        self.receipt = {"raw_response": raw_response}


def _ask(batch, gateway, notes, rows):
    import urllib.request

    base_url, api_key, model = gateway
    body = {"model": model, "messages": build_request(batch, notes, rows), "temperature": 0.1}
    request = urllib.request.Request(
        base_url.rstrip("/") + "/chat/completions",
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
        raw_response = response.read().decode("utf-8")
    receipt = {"raw_response": raw_response, "reply": None}
    try:
        reply = json.loads(raw_response)
        receipt["reply"] = reply
        if not isinstance(reply, dict):
            raise ValueError("reply envelope is not a JSON object")
    except ValueError as exc:
        raise _BatchReplyError(f"invalid reply envelope: {exc}", receipt) from exc
    return _Reply(reply, raw_response)


def _batch_items(reply, wanted):
    items = _parse(reply["choices"][0]["message"]["content"])
    found = {}
    for item in items:
        if not isinstance(item, dict) or type(item.get("n")) is not int or item["n"] not in wanted:
            raise ValueError("reply item has invalid source index")
        if item["n"] in found:
            raise ValueError("reply repeats a source index")
        found[item["n"]] = item
    if set(found) != wanted:
        raise ValueError("reply omitted a source index")
    return found


def _normalize(item, notes):
    reasons = []
    speaker, delivery, basis = (item.get(k) for k in ("speaker", "delivery", "basis"))
    if not isinstance(speaker, str) or speaker not in {*_people(notes), "crowd", "unclear"}:
        reasons.append("模型返回非法speaker")
        speaker = "unclear"
    if not isinstance(delivery, str) or delivery not in DELIVERIES:
        reasons.append("模型返回非法delivery")
        delivery = "unclear"
    if not isinstance(basis, str) or basis not in BASES:
        reasons.append("B步返回mouth或非法basis")
        basis = "none"
    reason = item.get("reason")
    if not isinstance(reason, str) or not reason.strip():
        reasons.append("模型缺少判断说明")
        reason = ""
    elif len(reason.strip()) > 30:
        reasons.append("模型判断说明超过30字")
    return {
        "speaker": speaker,
        "delivery": delivery,
        "basis": basis,
        "reason": reason.strip()[:30],
    }, reasons


def _with_constraints(judgment, notes, row, person_map):
    constraints = check_constraints(judgment, notes, row, person_map)
    reasons = [*judgment.get("model_review_reasons", []), *constraints["review_reasons"]]
    return {
        **judgment,
        "constraints": constraints,
        "needs_review": bool(reasons),
        "review_reasons": list(dict.fromkeys(reasons)),
    }


def read_line_judgments(
    media, rows, *, shot_notes, gateway=None, frame_cache=None, person_map=None
):
    """Fill only current A-step gaps; failed batches remain unread for the next run."""
    started = time.perf_counter()
    if gateway is None:
        try:
            from .transcribe import gateway_settings
        except ImportError:
            from transcribe import gateway_settings
        gateway = gateway_settings()
    targets = [i for i, row in enumerate(rows) if _target(shot_notes, row, gateway[2])]
    pending = [i for i in targets if not judgment_current(shot_notes, rows, i, gateway[2])]
    summary = {
        "method": METHOD,
        "model": gateway[2],
        "status": "unavailable",
        "lines": len(targets),
        "requested": len(pending),
        "read": 0,
        "reused": len(targets) - len(pending),
        "results": [],
        "batches": [],
        "errors": [],
        "seconds": 0.0,
        "frame_seconds": 0.0,
        "request_seconds": 0.0,
        "model_calls": 0,
    }
    failures = {}
    for i in pending:
        rows[i].pop("judgment", None)
    if pending and not all(gateway):
        summary["errors"].append("no vision model configured")
        failures = dict.fromkeys(pending, "视觉模型未配置")
    elif pending:
        cache = FrameCache(Path(media), frame_cache)
        numbered = []
        with tempfile.TemporaryDirectory(prefix="cine-line-judgment-") as folder:
            frame_started = time.perf_counter()
            for i in pending:
                row = rows[i]
                try:
                    frames = [
                        cache.get(
                            at,
                            FRAME_WIDTH,
                            lambda at=at, k=k, i=i: _extract(
                                Path(media), at, Path(folder) / f"{i}_{k}.jpg"
                            ),
                            quality=FRAME_QUALITY,
                        )
                        for k, at in enumerate(frame_times(row))
                    ]
                except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
                    failures[i] = f"frames for line {i}: {type(exc).__name__}: {exc}"
                    summary["errors"].append(failures[i])
                    continue
                numbered.append((i, row, frames))
            summary["frame_seconds"] = time.perf_counter() - frame_started
            batches = [numbered[k : k + BATCH_ROWS] for k in range(0, len(numbered), BATCH_ROWS)]

            def run(batch):
                wanted = {i for i, _row, _frames in batch}
                receipt = {"source_indices": sorted(wanted), "attempts": [], "status": "unread"}
                for _attempt in range(2):
                    asked = time.perf_counter()
                    attempt = {"reply": None, "usage": {}, "seconds": 0.0}
                    found = None
                    try:
                        attempt["reply"] = _ask(batch, gateway, shot_notes, rows)
                        attempt.update(getattr(attempt["reply"], "receipt", {}))
                        attempt["usage"] = attempt["reply"].get("usage") or {}
                        found = _batch_items(attempt["reply"], wanted)
                    except Exception as exc:  # noqa: BLE001 - preserve both failures as receipts
                        attempt.update(getattr(exc, "receipt", {}))
                        detail = (
                            exc.read().decode("utf-8", "replace") if hasattr(exc, "read") else ""
                        )
                        attempt["error"] = f"{type(exc).__name__}: {exc} {detail}".strip()
                    attempt["seconds"] = time.perf_counter() - asked
                    receipt["attempts"].append(attempt)
                    if found is not None:
                        receipt["status"] = "read"
                        return receipt, found
                return receipt, None

            request_started = time.perf_counter()
            with ThreadPoolExecutor(max_workers=WORKERS) as pool:
                batches_results = list(pool.map(run, batches))
            summary["request_seconds"] = time.perf_counter() - request_started
        for receipt, found in batches_results:
            receipt["receipt_sha256"] = _digest(receipt)
            summary["batches"].append(receipt)
            summary["model_calls"] += len(receipt["attempts"])
            if found is None:
                error = receipt["attempts"][-1].get("error", "batch unread")
                summary["errors"].append(error)
                for i in receipt["source_indices"]:
                    failures[i] = "两次批次结构或请求失败，下一次运行补读"
                continue
            for i, item in found.items():
                judgment, reasons = _normalize(item, shot_notes)
                rows[i]["judgment"] = _with_constraints(
                    {
                        **judgment,
                        "schema_version": SCHEMA_VERSION,
                        "method": METHOD,
                        "model": gateway[2],
                        "input_sha256": judgment_inputs(shot_notes, rows, i, gateway[2]),
                        "read": True,
                        "model_review_reasons": reasons,
                        "at": frame_times(rows[i]),
                        "receipt_sha256": receipt["receipt_sha256"],
                    },
                    shot_notes,
                    rows[i],
                    person_map,
                )
    for i in targets:
        row = rows[i]
        if judgment_current(shot_notes, rows, i, gateway[2]):
            row["judgment"] = _with_constraints(row["judgment"], shot_notes, row, person_map)
            value = row["judgment"]
            summary["read"] += 1
        else:
            value = _with_constraints(
                {
                    "speaker": "unclear",
                    "delivery": "unclear",
                    "basis": "none",
                    "reason": "",
                    "read": False,
                    "model_review_reasons": [failures.get(i, "B步尚未读取")],
                },
                shot_notes,
                row,
                person_map,
            )
        summary["results"].append(
            {
                "source_index": i,
                "id": row.get("id") or f"seg{i:03d}",
                **{k: row.get(k) for k in ("text", "start", "end")},
                **value,
            }
        )
    summary["status"] = (
        "read"
        if summary["read"] == len(targets)
        else "partial"
        if summary["read"]
        else "unavailable"
    )
    summary["seconds"] = time.perf_counter() - started
    return summary
