"""Read each source shot's visible location and action without changing the ledger."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import math
import re
import subprocess
import tempfile
import time
from pathlib import Path

try:
    from .production import atomic_json
    from .source_identity import fingerprint_of, media_fingerprint
    from .transcribe import gateway_settings
except ImportError:
    from production import atomic_json
    from source_identity import fingerprint_of, media_fingerprint
    from transcribe import gateway_settings

BATCH_SHOTS = 24
FRAME_WIDTH = 384
FRAME_QUALITY = 4
POINTS = (0.3, 0.7)
TIMEOUT = 300
LOCATION_ID = re.compile(r"L[1-9][0-9]*")
PERSON_ID = re.compile(r"P[1-9][0-9]*")
SCHEMA_VERSION = 3
PROMPT = """你在给影片逐镜头拉片，整理拍摄地点和可见事件。每个镜头附两帧。
地点指布景/实景本身，不是景别、机位、人物或故事章节。
同一地点的不同角度、特写仍用同一 id；只在可见布景支持地点变化时新建地点。
已知地点直接用它的 id；新地点用 L 加下一个序号，给简短中文名和可见布景描述。
人物特写若和邻近镜头明显连续，沿用地点；实在无法判断填 ?。
照片、字幕卡、黑屏这类插入画面填 insert。
what 用一句不超过 40 字的中文描述可见事件，不猜画面外的剧情。
同时逐镜头列出能认出的可见人物 people。同一个人换机位或服装仍沿用编号。
群像、远景和反应镜头也必须逐人列出能认出的已有 P 编号，不要因为无人说话就省略。
crowd 是必填布尔值：画面里还有无法可靠编号的现场人群时 true，否则 false。
people 和 crowd 可以同时存在；不要给看不清的人强造编号，也不要把已编号人物重复当成人群。
已知人物用原 id；新人物用 P 加下一个序号。look 写可见性别、年龄段、发型和服装。
只把明确标注人物姓名的烧录字幕写进 name_caption，普通对白字幕不是人名；没有就填 null。
照片中的人物也可登记入人物表，但镜头 people 只列现场真人，不列照片或屏幕中的人物。
不要把照片、反应镜头里的人当成正在说话。
仅输出 JSON，所有镜头都必须返回：
{"new":[{"id":"L2","name":"地下停车场","setting":"车位标线、承重柱、出口标牌"}],
 "people":[{"id":"P1","look":"成年男子，短发，灰色外套","name_caption":null}],
 "shots":[{"n":8,"place":"L2","what":"一人走向停在柱边的汽车","people":["P1"],"crowd":false}]}"""


class ReplyError(ValueError):
    def __init__(self, message, request_stats):
        super().__init__(message)
        self.request_stats = request_stats


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
        raise RuntimeError(f"empty frame at {seconds:.3f}s")
    return data


def _ask(known: list[dict], batch: list[tuple[dict, list[bytes]]], gateway, *, people) -> dict:
    import urllib.request

    base_url, key, model = gateway
    content = [
        {"type": "text", "text": PROMPT},
        {"type": "text", "text": "已知地点：" + json.dumps(known, ensure_ascii=False)},
        {"type": "text", "text": "已知人物：" + json.dumps(people, ensure_ascii=False)},
    ]
    for shot, frames in batch:
        content.append(
            {
                "type": "text",
                "text": f"镜头 {shot['index']} [{shot['start']:.2f}s-{shot['end']:.2f}s]",
            }
        )
        for data in frames:
            url = "data:image/jpeg;base64," + base64.b64encode(data).decode("ascii")
            content.append({"type": "image_url", "image_url": {"url": url}})
    request = urllib.request.Request(
        base_url.rstrip("/") + "/chat/completions",
        data=json.dumps(
            {
                "model": model,
                "messages": [{"role": "user", "content": content}],
                "temperature": 0.1,
            }
        ).encode("utf-8"),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
    )
    started = time.monotonic()
    with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
        envelope = json.loads(response.read().decode("utf-8"))
    request_seconds = time.monotonic() - started
    stats = {
        "seconds": round(request_seconds, 3),
        "usage": envelope.get("usage"),
    }
    started = time.monotonic()
    try:
        content = envelope["choices"][0]["message"]["content"]
        stats["output_characters"] = len(content)
        if "```json" in content:
            content = content.split("```json", 1)[1].split("```", 1)[0]
        elif "```" in content:
            content = content.split("```", 1)[1].split("```", 1)[0]
        reply = json.loads(content.strip())
        if not isinstance(reply, dict):
            raise ValueError("reply is not a JSON object")
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        stats["parse_seconds"] = round(time.monotonic() - started, 3)
        raise ReplyError(str(exc), stats) from exc
    stats["parse_seconds"] = round(time.monotonic() - started, 3)
    reply["_request_stats"] = stats
    return reply


def locations_for(shots: list[dict], known: list[dict], teaser_end: float = 0.0) -> list[dict]:
    """Only consecutive body shots of the same location share a span."""
    locations = {item["id"]: {**item, "spans": [], "seconds": 0.0} for item in known}
    previous = None
    for shot in shots:
        place = shot["place"]
        start, end = max(shot["start"], teaser_end), shot["end"]
        if place not in locations or start >= end:
            previous = None
            continue
        location = locations[place]
        if previous == place and location["spans"][-1]["end"] == start:
            location["spans"][-1]["end"] = end
            location["spans"][-1]["shots"].append(shot["index"])
        else:
            location["spans"].append({"start": start, "end": end, "shots": [shot["index"]]})
        previous = place
    for location in locations.values():
        location["seconds"] = round(sum(s["end"] - s["start"] for s in location["spans"]), 6)
    return [item for item in locations.values() if item["spans"]]


def _accept(
    reply: dict, known: list[dict], wanted: set[int], people: list[dict]
) -> tuple[list[dict], dict[int, dict], list[dict]]:
    locations = {item["id"]: item for item in known}
    new = reply.get("new", [])
    if not isinstance(new, list) or not isinstance(reply.get("shots"), list):
        raise ValueError("reply needs new and shots arrays")
    used = {item.get("place") for item in reply["shots"] if isinstance(item, dict)}
    for item in new:
        if not isinstance(item, dict) or not LOCATION_ID.fullmatch(str(item.get("id", ""))):
            raise ValueError("invalid new location")
        if item["id"] in locations:
            continue
        if not all(isinstance(item.get(k), str) and item[k].strip() for k in ("name", "setting")):
            if item["id"] not in used:
                continue
            raise ValueError("invalid new location")
        value = {k: item[k].strip() for k in ("id", "name", "setting")}
        locations[value["id"]] = value
    persons = {item["id"]: dict(item) for item in people}
    if not isinstance(reply.get("people"), list):
        raise ValueError("reply needs a people array")
    for item in reply["people"]:
        if not _valid_person(item):
            raise ValueError("invalid person")
        caption = (item["name_caption"] or "").strip() or None
        if item["id"] in persons:
            if not persons[item["id"]]["name_caption"]:
                persons[item["id"]]["name_caption"] = caption
        else:
            persons[item["id"]] = {
                "id": item["id"],
                "look": item["look"].strip(),
                "name_caption": caption,
            }
    found = {}
    for item in reply["shots"]:
        if not isinstance(item, dict) or type(item.get("n")) is not int or item["n"] not in wanted:
            raise ValueError("unexpected shot in reply")
        if item["n"] in found:
            raise ValueError("duplicate shot in reply")
        place, what = item.get("place"), item.get("what")
        if place not in {"insert", "?", *locations}:
            raise ValueError(f"unknown location {place}")
        if not isinstance(what, str) or not what.strip():
            raise ValueError("shot action is missing")
        visible = item.get("people")
        if not isinstance(visible, list) or any(
            not isinstance(p, str) or p not in persons for p in visible
        ):
            raise ValueError("invalid shot people")
        if type(item.get("crowd")) is not bool:
            raise ValueError("shot crowd must be a boolean")
        found[item["n"]] = {
            "place": place,
            "what": what.strip()[:40],
            "people": list(dict.fromkeys(visible)),
            "crowd": item["crowd"],
        }
    return list(locations.values()), found, list(persons.values())


def _valid_person(item):
    return (
        isinstance(item, dict)
        and PERSON_ID.fullmatch(str(item.get("id", ""))) is not None
        and isinstance(item.get("look"), str)
        and bool(item["look"].strip())
        and "name_caption" in item
        and (item["name_caption"] is None or isinstance(item["name_caption"], str))
    )


def read_shot_notes(media: Path, project: Path, *, gateway=None, frame_cache=None) -> dict:
    """Reuse complete readings; on a partial reading request only unfinished shots."""
    project = Path(project).resolve()
    manifest = json.loads((project / "project.json").read_text(encoding="utf-8"))
    revision = manifest.get("current_revision")
    if not isinstance(revision, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", revision):
        raise ValueError("committed revision required")
    folder = (project / "revisions" / revision).resolve()
    if not folder.is_relative_to(project):
        raise ValueError("revision escapes project")
    source = json.loads((project / "source.json").read_text(encoding="utf-8"))
    expected = fingerprint_of(source)
    if expected is not None and media_fingerprint(Path(media)) != expected:
        raise ValueError("media does not match the project's source")
    raw = (folder / "source_shots.json").read_bytes()
    ledger = json.loads(raw)
    plan = json.loads((folder / "story_plan.json").read_text(encoding="utf-8"))
    teaser = plan.get("opening_teaser") or {}
    teaser_end = float(teaser.get("end_seconds") or 0) if teaser.get("status") == "found" else 0.0
    gateway = gateway if gateway is not None else gateway_settings()
    hashes = {
        "source_shots": hashlib.sha256(raw).hexdigest(),
        "opening_teaser": hashlib.sha256(
            json.dumps(teaser, sort_keys=True).encode("utf-8")
        ).hexdigest(),
        "prompt": hashlib.sha256(PROMPT.encode("utf-8")).hexdigest(),
    }
    shots = []
    for index, shot in enumerate(ledger):
        if shot.get("source_id") != source["source_id"] or shot.get("revision_id") != revision:
            raise ValueError("source shot identity mismatch")
        interval = shot["interval"]
        base = interval.get("time_base_num", 1) / interval["time_base_den"]
        start = (interval["in_pts"] - source.get("start_pts", 0)) * base
        end = (interval["out_pts"] - source.get("start_pts", 0)) * base
        if not math.isfinite(start) or not math.isfinite(end) or start < 0 or end <= start:
            raise ValueError("invalid source shot interval")
        shots.append(
            {
                "index": index,
                "shot_id": shot["shot_id"],
                "start": start,
                "end": end,
                "place": "?",
                "what": "",
                "people": [],
                "crowd": False,
                "teaser": end <= teaser_end,
            }
        )
    target = folder / "shot_notes.json"
    try:
        cached = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        cached = {}
    cache_shape = isinstance(cached, dict) and isinstance(cached.get("locations"), list)
    if cache_shape:
        try:
            catalog = cached.get("location_catalog", cached["locations"])
            ids = {item["id"] for item in catalog}
            persons = cached["people"]
            person_ids = {item["id"] for item in persons}
            unread_cached = set(cached.get("unread_shots") or [])
            cache_shape = (
                isinstance(persons, list)
                and len(person_ids) == len(persons)
                and all(_valid_person(item) for item in persons)
                and all(
                    LOCATION_ID.fullmatch(str(item["id"]))
                    and all(
                        isinstance(item.get(k), str) and item[k].strip()
                        for k in ("name", "setting")
                    )
                    for item in catalog
                )
                and unread_cached <= {s["index"] for s in shots}
                and len(ids) == len(catalog)
                and all(
                    isinstance(row.get("place"), str)
                    and row["place"] in {"?", "insert", *ids}
                    and isinstance(row.get("what"), str)
                    and isinstance(row.get("people"), list)
                    and type(row.get("crowd")) is bool
                    and all(p in person_ids for p in row["people"])
                    and (row["index"] in unread_cached or bool(row["what"].strip()))
                    for row in cached.get("shots") or []
                )
            )
        except (KeyError, TypeError, AttributeError):
            cache_shape = False
    matches = (
        cache_shape
        and cached.get("schema_version") == SCHEMA_VERSION
        and cached.get("source_id") == source["source_id"]
        and cached.get("revision_id") == revision
        and cached.get("model") == gateway[2]
        and cached.get("input_sha256") == hashes
        and len(cached.get("shots") or []) == len(shots)
        and all(
            isinstance(old, dict)
            and old.get("shot_id") == shot["shot_id"]
            and old.get("index") == shot["index"]
            and old.get("start") == shot["start"]
            and old.get("end") == shot["end"]
            for old, shot in zip(cached.get("shots") or [], shots, strict=False)
        )
    )
    if matches and cached.get("status") == "read" and not cached.get("unread_shots"):
        return cached
    known, people, unread = [], [], {shot["index"] for shot in shots}
    if matches and cached.get("unread_shots"):
        unread = set(cached["unread_shots"])
        known = [
            {k: item[k] for k in ("id", "name", "setting")}
            for item in cached.get("location_catalog", cached["locations"])
        ]
        people = cached["people"]
        for old, shot in zip(cached["shots"], shots, strict=True):
            if shot["index"] not in unread:
                shot.update(
                    place=old["place"], what=old["what"], people=old["people"], crowd=old["crowd"]
                )
    result = {
        "schema_version": SCHEMA_VERSION,
        "source_id": source["source_id"],
        "revision_id": revision,
        "model": gateway[2],
        "status": "unavailable",
        "locations": [],
        "people": people,
        "shots": shots,
        "input_sha256": hashes,
        "errors": [],
        "unread_shots": sorted(unread),
        "attempts": list(cached.get("attempts") or []) if matches else [],
    }
    if not all(gateway):
        result["errors"].append("no vision model configured")
    else:
        attempt = {"batches": []}
        started = time.monotonic()
        from .frame_cache import FrameCache

        cache = FrameCache(media, frame_cache)
        with tempfile.TemporaryDirectory(prefix="cine-shot-frames-") as temp:
            for offset in range(0, len(shots), BATCH_SHOTS):
                batch = [s for s in shots[offset : offset + BATCH_SHOTS] if s["index"] in unread]
                if not batch:
                    continue
                metric = {
                    "shots": [s["index"] for s in batch],
                    "known_people": len(people),
                    "known_locations": len(known),
                    "status": "failed",
                }
                attempt["batches"].append(metric)
                phase = time.monotonic()
                try:
                    numbered = []
                    for shot in batch:
                        frames = [
                            cache.get(
                                shot["start"] + (shot["end"] - shot["start"]) * p,
                                FRAME_WIDTH,
                                lambda p=p, k=k, shot=shot: _frame(
                                    Path(media),
                                    shot["start"] + (shot["end"] - shot["start"]) * p,
                                    Path(temp) / f"{shot['index']}_{k}.jpg",
                                ),
                                quality=FRAME_QUALITY,
                            )
                            for k, p in enumerate(POINTS)
                        ]
                        numbered.append((shot, frames))
                    metric["frame_seconds"] = round(time.monotonic() - phase, 3)
                    phase = time.monotonic()
                    reply = _ask(known, numbered, gateway, people=people)
                    metric["request_seconds"] = round(time.monotonic() - phase, 3)
                    metric["request"] = reply.get("_request_stats")
                    phase = time.monotonic()
                    updated, found, people = _accept(
                        reply,
                        known,
                        {s["index"] for s in batch},
                        people,
                    )
                    known = updated
                    for shot in batch:
                        if shot["index"] in found:
                            shot.update(found[shot["index"]])
                            unread.discard(shot["index"])
                    missing = [s["index"] for s in batch if s["index"] in unread]
                    if missing:
                        result["errors"].append(f"shots missing from reply: {missing}")
                    metric["status"] = "partial" if missing else "read"
                    metric["accept_seconds"] = round(time.monotonic() - phase, 3)
                except Exception as exc:  # noqa: BLE001 - failed shots stay unknown
                    metric["failure_seconds"] = round(time.monotonic() - phase, 3)
                    if isinstance(exc, ReplyError):
                        metric["request"] = exc.request_stats
                        metric["request_seconds"] = metric["failure_seconds"]
                    result["errors"].append(
                        f"shots {batch[0]['index']}-{batch[-1]['index']}: "
                        f"{type(exc).__name__}: {exc}"
                    )
        attempt["seconds"] = round(time.monotonic() - started, 3)
        result["attempts"].append(attempt)
        result["status"] = "partial" if unread else "read"
    result["unread_shots"] = sorted(unread)
    result["locations"] = locations_for(shots, known, teaser_end)
    result["location_catalog"] = known
    result["people"] = people
    atomic_json(target, result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("media", type=Path)
    parser.add_argument("project", type=Path)
    args = parser.parse_args()
    result = read_shot_notes(args.media, args.project)
    print(
        json.dumps(
            {
                "status": result["status"],
                "locations": len(result["locations"]),
                "model": result["model"],
                "errors": result["errors"],
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
