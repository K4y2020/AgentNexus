"""Check a faithful script's performance of each line against what the source shows.

The transcript records, per spoken line, the emotion its frames show
(:mod:`line_performance`). A faithful script keeps the words, the speaker and the
order; this check adds the performance: a line the source delivers in tears must
not be written as a joke.

The shared speaker evidence determines whose performance the frame shows. JEV
only compares the script's delivery and preceding action with that emotion.
Only a confident contradiction on a confirmed speaker's frame blocks; a weaker
one is a review item. Each line is asked alone because later judgments drifted
in a shared request.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from concurrent.futures import ThreadPoolExecutor
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

BLOCK = 0.9  # P(contradicts) that fails the stage
REVIEW = 0.6  # P(contradicts) or P(no_cue) that goes to the review queue
SIMILAR = 0.6  # a script line this close to a source line is that line
WINDOW = 8  # source rows searched ahead of the last matched one
WORKERS = 8
UNREADABLE = "看不清"
_KEEP = re.compile(r"[\w㐀-鿿]+")

FIT = {
    "consistent": "the script's delivery and action show the same emotion as the frame, or a "
    "compatible one (e.g. crying and tearful complaint, anger and scolding)",
    "contradicts": "the script's delivery or action gives the line an opposite or clearly "
    "different emotion from the frame (e.g. the frame shows sobbing, the script says "
    "teasing or smiling)",
    "no_cue": "the script gives no delivery or action that indicates how this line is performed",
}
FIT_INSTRUCTIONS = (
    "Suppose the person in `source_frame` is the speaker of `line`. Compare the "
    "emotion the frame shows with how the script performs the line "
    "(`script.delivery`, and `script.action_before` when it describes this "
    "speaker). Does the script keep the source's emotion?"
)


def _norm(text: str) -> str:
    return "".join(_KEEP.findall(str(text or "").casefold()))


def _same_line(script_line: str, source_line: str) -> float:
    a, b = _norm(script_line), _norm(source_line)
    if not a or not b:
        return 0.0
    if min(len(a), len(b)) >= 4 and (a in b or b in a):
        return 1.0
    return SequenceMatcher(None, a, b).ratio()


def pair_lines(
    script: dict, attributions: list[dict], *, readable_only: bool = True
) -> list[dict]:
    """Script dialogue beats matched, in order, to the source rows they reproduce.

    Split beats share a row; merged beats retain their source rows in order.
    Location checks need every line, including unreadable faces.
    """
    rows = [(i, row) for i, row in enumerate(attributions) if not row.get("teaser")]
    pairs, cursor, remainder = [], 0, None
    for episode in script.get("episodes") or []:
        for scene_index, scene in enumerate(episode.get("scenes") or [], 1):
            before = ""
            for beat in scene.get("flow") or []:
                if "line" not in beat:
                    before = str(beat.get("action") or "")
                    continue
                stop = min(len(rows), cursor + WINDOW) if readable_only else len(rows)
                window = range(cursor, stop)
                scored = [
                    (
                        _same_line(
                            beat["line"],
                            remainder
                            if i == cursor and remainder is not None
                            else rows[i][1].get("text"),
                        ),
                        i,
                    )
                    for i in window
                ]
                score, index = max(scored, key=lambda item: (item[0], -item[1]), default=(0.0, -1))
                if score < SIMILAR:
                    continue
                source_index, source = rows[index]
                text = (
                    remainder
                    if index == cursor and remainder is not None
                    else _norm(source.get("text"))
                )
                part = _norm(beat["line"])
                position = text.find(part)
                remainder = text[position + len(part) :] if position >= 0 else ""
                cursor = index if remainder else index + 1
                remainder = remainder or None
                matched = [(source_index, source)]
                position = part.find(text) if text else -1
                if position >= 0 and len(part) > len(text):
                    position += len(text)
                    for following_index in range(cursor, stop):
                        following = _norm(rows[following_index][1].get("text"))
                        found = part.find(following, position) if following else -1
                        if found < 0:
                            continue
                        matched.append(rows[following_index])
                        position = found + len(following)
                        cursor = following_index + 1
                for source_index, source in matched:
                    performance = source.get("performance") or {}
                    if readable_only and (
                        not performance.get("emotion") or performance["emotion"] == UNREADABLE
                    ):
                        continue
                    pairs.append(
                        {
                            "scene": scene.get("sceneId") or "",
                            "characters": list(scene.get("characters") or []),
                            "speaker": beat.get("speaker"),
                            "line": beat["line"],
                            "delivery": str(beat.get("delivery") or ""),
                            "action_before": before,
                            "source_text": source.get("text", ""),
                            "source_index": source_index,
                            "source_id": source.get("id"),
                            "source_start": source.get("start"),
                            "source_end": source.get("end"),
                            "episode": episode.get("ep"),
                            "scene_index": scene_index,
                            "performance": performance,
                            "source_speaker": source.get("speaker"),
                            "speaker_needs_review": source.get(
                                "speaker_needs_review", source.get("needs_review", True)
                            ),
                            "evidence": source.get("evidence") or {},
                        }
                    )
    return pairs


def _questions(pair: dict, names: dict[str, str]):
    from typesafe_sdk import Choice

    state = {
        "line": pair["line"],
        "speaker": names.get(pair["speaker"], pair["speaker"]),
        "source_frame": {k: pair["performance"].get(k, "") for k in ("who", "emotion", "visible")},
        "script": {"delivery": pair["delivery"], "action_before": pair["action_before"]},
    }
    questions = {
        "fit": Choice(
            instructions=FIT_INSTRUCTIONS,
            criteria=FIT,
        ),
    }
    return state, questions


def _message(pair: dict, verdict: str) -> str:
    seen = pair["performance"]
    source = (
        f"{seen.get('emotion')}：{seen.get('visible')}"
        if seen.get("visible")
        else seen.get("emotion")
    )
    written = f"「{pair['delivery']}」" if pair["delivery"] else "没有写表演"
    return (
        f"{pair['scene']}「{pair['line'][:20]}」{verdict}：原片说这句时画面是{source}；"
        f"剧本{written}。按原片情绪改 delivery（和紧邻的动作），台词不动。"
    )


def check_performance(
    script: dict,
    cast: dict,
    attributions: list[dict],
    *,
    api_key: str | None = None,
    model: str = "jev-latest",
    cache_dir: Path | None = None,
) -> dict[str, Any]:
    """``status`` passed/failed/unavailable, with ``blocking`` errors and ``review`` items."""
    pairs = [
        p
        for p in pair_lines(script, attributions)
        if (p["speaker"] in _names(cast) or p["speaker"] == "extra")
        and not p["speaker_needs_review"]
        and p["source_speaker"] == p["speaker"]
        and (p["evidence"].get("a") or {}).get("current", False)
        and (
            (p["evidence"].get("a") or {}).get("character_id") == p["source_speaker"]
            or (
                p["source_speaker"] == "extra"
                and (p["evidence"].get("a") or {}).get("status") == "extra"
            )
        )
    ]
    result: dict[str, Any] = {
        "status": "passed",
        "checked": len(pairs),
        "blocking": [],
        "review": [],
    }
    if not pairs:
        return result
    try:
        from .jev_client import _choice, _find_api_key, open_client
    except ImportError:  # Direct CLI execution from the pipeline directory.
        from jev_client import _choice, _find_api_key, open_client

    key = (api_key or _find_api_key()).strip()
    names = {"extra": "非角色人物", **_names(cast)}

    def ask(pair):
        state, questions = _questions(pair, names)
        inputs = {
            "model": model,
            "state": state,
            "question": {"instructions": FIT_INSTRUCTIONS, "criteria": FIT},
            "source": {
                key: pair.get(key)
                for key in ("source_id", "source_index", "source_start", "source_end", "evidence")
            },
            "visual_inputs": pair["performance"].get("input_sha256"),
        }
        token = hashlib.sha256(
            json.dumps(inputs, sort_keys=True, ensure_ascii=False).encode()
        ).hexdigest()
        target = Path(cache_dir) / f"{token}.json" if cache_dir else None
        if target:
            try:
                saved = json.loads(target.read_text(encoding="utf-8"))
                fit = saved.get("fit")
                if (
                    saved.get("schema_version") == 1
                    and saved.get("input_sha256") == token
                    and isinstance(fit, dict)
                    and fit
                    and set(fit) <= set(FIT)
                    and all(
                        not isinstance(p, bool)
                        and isinstance(p, (int, float))
                        and math.isfinite(p)
                        and 0 <= p <= 1
                        for p in fit.values()
                    )
                ):
                    return fit, None, True
            except (OSError, ValueError, AttributeError):
                pass
        if not key:
            return None, "TYPESAFE_API_KEY is not configured", False
        try:
            with open_client(key) as client:
                response = client.system_one(state=state, questions=questions, model=model)
        except Exception as exc:  # noqa: BLE001 - reported; the line stays unjudged
            return None, f"{type(exc).__name__}: {exc}", False
        answers = getattr(response, "answers", None) or {}
        _f, _c, fit, fit_problem = _choice(answers.get("fit"), set(FIT))
        if fit_problem or not fit:
            return None, fit_problem or "missing_probabilities", False
        if target:
            try:
                from .production import atomic_json
            except ImportError:
                from production import atomic_json
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                atomic_json(
                    target,
                    {
                        "schema_version": 1,
                        "input_sha256": token,
                        "requested_model": model,
                        "model": str(getattr(response, "model", model)),
                        "fit": fit,
                    },
                )
            except OSError:
                pass
        return fit, None, False

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        answers = list(pool.map(ask, pairs))
    result["reused"] = sum(cached for _fit, _error, cached in answers)
    errors = []
    for pair, (judged, error, _cached) in zip(pairs, answers, strict=True):
        if error:
            errors.append(f"{pair['line'][:20]}: {error}")
            continue
        fit = judged
        pair_record = {
            "scene": pair["scene"],
            "line": pair["line"],
            "delivery": pair["delivery"],
            "source": pair["performance"],
            "speaker_evidence": pair["evidence"],
            "fit": {k: round(v, 3) for k, v in fit.items()},
        }
        contradicts, no_cue = fit.get("contradicts", 0.0), fit.get("no_cue", 0.0)
        if contradicts >= BLOCK:
            result["blocking"].append(_message(pair, "表演和原片相反"))
        elif contradicts >= REVIEW:
            result["review"].append(
                {**pair_record, "message": _message(pair, "表演可能和原片不一致")}
            )
        elif no_cue >= REVIEW:
            result["review"].append({**pair_record, "message": _message(pair, "剧本没写表演")})
    if errors:
        result["errors"] = errors[:5]
        if len(errors) == len(pairs):
            result["status"] = "unavailable"
            if not key:
                result["error"] = "TYPESAFE_API_KEY is not configured"
    if result["blocking"]:
        result["status"] = "failed"
    return result


def _names(cast: dict) -> dict[str, str]:
    return {
        c["id"]: c.get("name") or c["id"]
        for c in cast.get("characters") or []
        if isinstance(c, dict) and c.get("id")
    }
