"""Deterministic source-location coverage and faithful dialogue placement checks."""

from __future__ import annotations

import math

try:
    from .performance_gate import pair_lines
except ImportError:
    from performance_gate import pair_lines

MIN_LOCATION_SHARE = 0.3


def line_locations(locations, start, end):
    """Accept locations covering at least 30% of the whole timed source line."""
    if (
        not all(
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and math.isfinite(value)
            for value in (start, end)
        )
        or end <= start
    ):
        return []
    duration = end - start
    overlaps = [
        (
            sum(
                max(0, min(end, span["end"]) - max(start, span["start"]))
                for span in location.get("spans") or []
            ),
            location,
        )
        for location in locations
    ]
    return [
        location
        for seconds, location in sorted(overlaps, key=lambda item: item[0], reverse=True)
        if seconds / duration >= MIN_LOCATION_SHARE - 1e-9
    ]


def attach_dialogue(notes, rows):
    """Export the same overlap assignments used by outline and script checks."""
    locations = notes.get("locations") or []
    for location in locations:
        location["line_numbers"], location["lines"] = [], []
    for line_number, row in enumerate(rows, 1):
        if row.get("teaser") or not str(row.get("text") or "").strip():
            continue
        for location in line_locations(locations, row.get("start"), row.get("end")):
            location["line_numbers"].append(line_number)
            location["lines"].append(
                {
                    "line_number": line_number,
                    "id": row.get("id"),
                    "text": row["text"],
                    "start": row["start"],
                    "end": row["end"],
                }
            )


def _result(status="passed", **values):
    return {"status": status, "blocking": [], "review": [], "checked": 0, **values}


def _review(reason, message, **values):
    return {"reason": reason, "message": message, **values}


def _context(material):
    notes = material.get("source_locations") if isinstance(material, dict) else None
    if not isinstance(notes, dict) or notes.get("status") == "unavailable":
        return {}, _result(
            "unavailable",
            review=[
                _review(
                    "source_locations_missing",
                    "原片场景表缺失或不可用，请完成逐镜头拉片并重新导出。",
                )
            ],
        )
    locations = notes.get("locations")
    if not isinstance(locations, list) or not isinstance(notes.get("shots"), list):
        return {}, _result(
            "unavailable",
            review=[_review("source_locations_missing", "原片场景表结构不完整，请重新导出。")],
        )
    known = {item["id"]: item for item in locations}
    result = _result()
    if notes.get("status") != "read":
        result["review"].append(
            _review(
                "source_locations_partial",
                "原片场景表仅部分完成，未识别镜头的地点仍需核对。",
                errors=notes.get("errors") or [],
            )
        )
    unknown = [s["index"] for s in notes["shots"] if s.get("place") == "?" and not s.get("teaser")]
    if unknown:
        result["review"].append(
            _review(
                "source_locations_unknown",
                "原片仍有无法判断地点的镜头。",
                shots=unknown,
            )
        )
    if notes.get("transcript_status") not in (None, "matched"):
        result["review"].append(
            _review(
                "source_location_dialogue_missing",
                "场景表没有匹配的原片台词，短地点的台词覆盖仍需核对。",
            )
        )
    return known, result


def _label(location):
    spans = location.get("spans") or []
    times = "、".join(f"{s['start']:.1f}–{s['end']:.1f}s" for s in spans)
    return f"{location['id']} {location['name']}（{times}）"


def _ids(value, known, label, result):
    if not isinstance(value, list) or not value or not all(isinstance(v, str) for v in value):
        result["blocking"].append(f"{label} 的 locations/from 必须是非空地点 id 数组。")
        return set()
    if len(value) != len(set(value)):
        result["blocking"].append(f"{label} 的地点 id 重复。")
    for item in value:
        if item not in known:
            result["blocking"].append(f"{label} 引用了原片不存在的地点 {item}。")
    return set(value) & known.keys()


def check_outline(outline, material):
    mode = (outline.get("params") or {}).get("adaptMode")
    if mode == "借壳":
        return _result("skipped", reason="free adaptation", skipped="free adaptation")
    if mode not in ("忠实", "抽核"):
        return _result("skipped", reason="adaptation mode not specified")
    known, result = _context(material)
    if result["status"] == "unavailable":
        return result
    adaptation = outline.get("adaptation") or {}
    declarations = {"cut": [], "merge": []}
    for kind in declarations:
        for index, item in enumerate(adaptation.get(kind) or [], 1):
            if "locations" in item:
                declarations[kind].append(
                    _ids(
                        item["locations"],
                        known,
                        f"adaptation.{kind} 第 {index} 条",
                        result,
                    )
                )
    covered = set()
    for scene in outline.get("scenes") or []:
        label = f"场景 {scene['id']} {scene['name']}"
        origins = scene.get("from")
        if origins is None or origins == []:
            message = f"{label} 没有原片地点来源（from 缺失或为空）。"
            if mode == "忠实":
                result["blocking"].append(message)
            else:
                result["review"].append(_review("scene_location_original", message))
            continue
        ids = _ids(origins, known, label, result)
        covered.update(ids)
        if mode == "忠实" and len(ids) > 1 and ids not in declarations["merge"]:
            result["blocking"].append(
                f"{label} 合并了 {'、'.join(sorted(ids))}，"
                "但 adaptation.merge.locations 没有对应的合并声明。"
            )
    cut = set().union(*declarations["cut"]) if declarations["cut"] else set()
    for location in known.values():
        if not location.get("line_numbers") and location.get("seconds", 0) < 3.0:
            continue
        result["checked"] += 1
        if location["id"] in covered or (mode == "抽核" and location["id"] in cut):
            continue
        sample = next(
            (line.get("text") for line in location.get("lines") or [] if line.get("text")), None
        )
        quote = f"，台词「{sample}」" if sample else ""
        remedy = (
            "给它建场景，或在 adaptation.merge 中声明合并并补齐场景 from"
            if mode == "忠实"
            else "给它建场景，或在 adaptation.cut.locations 中声明删去"
        )
        result["blocking"].append(f"原片地点 {_label(location)}{quote} 没有对应的场景：{remedy}。")
    result["status"] = "failed" if result["blocking"] else "passed"
    return result


def check_script(script, outline, material, rows):
    mode = (outline.get("params") or {}).get("adaptMode")
    if mode != "忠实":
        reason = "free adaptation" if mode == "借壳" else "only faithful adaptation"
        return _result("skipped", reason=reason, skipped=reason)
    known, result = _context(material)
    if result["status"] == "unavailable":
        return result
    scenes = {s["id"]: s for s in outline.get("scenes") or []}
    if rows is None:
        result["review"].append(
            _review("source_location_dialogue_missing", "原片台词文件缺失，台词地点未检查。")
        )
        return result
    pairs = pair_lines(script, rows, readable_only=False)
    dialogue_count = sum(
        "line" in beat
        for episode in script.get("episodes") or []
        for scene in episode.get("scenes") or []
        for beat in scene.get("flow") or []
    )
    if len(pairs) < dialogue_count:
        result["review"].append(
            _review(
                "dialogue_location_unpaired",
                "部分剧本台词未能对齐原片，地点仍需核对。",
                lines=dialogue_count - len(pairs),
            )
        )
    for pair in pairs:
        locations = line_locations(known.values(), pair["source_start"], pair["source_end"])
        if not locations:
            result["review"].append(
                _review(
                    "dialogue_location_unknown",
                    f"「{pair['line']}」的原片地点无法判断。",
                    line_number=pair["source_index"] + 1,
                    start=pair["source_start"],
                    end=pair["source_end"],
                )
            )
            continue
        origins = {location["id"] for location in locations}
        allowed = {s["id"] for s in scenes.values() if origins.intersection(s.get("from") or [])}
        result["checked"] += 1
        if pair["scene"] in allowed:
            continue
        name = (scenes.get(pair["scene"]) or {}).get("name", "未知场景")
        label = "、".join(_label(location) for location in locations)
        result["blocking"].append(
            f"「{pair['line']}」原片在 {label}，"
            f"剧本放在第 {pair['scene_index']} 场 {name}（{pair['scene']}）。"
        )
    result["status"] = "failed" if result["blocking"] else "passed"
    return result
