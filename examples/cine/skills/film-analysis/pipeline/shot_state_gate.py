"""Check that each shot's prompt text carries every on-screen character's body position.

The video model reads only a segment's h3Prompt. The cut's ``frame`` and continuity
ledger never reach it, and a continuing segment's opening frame shows only who was in
the previous segment's last shot. A person still hanging, seated or kneeling from the
shot before must therefore be written into this shot's own text.

JEV is asked twice per cut and character, each request blind to the other side: the
position the storyboard plans (frame, ledger, the character's previous shot in the
scene) and the position the shot text gives. Asked together, the plan leaked into the
reading of the text. An omitted position blocks, as does a confidently different one;
a weaker difference is a review item. Hand activity is only reviewed: its judgments
were too noisy to block on.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

PLANNED = 0.5  # P(position) the plan needs before the text is checked against it
WRITTEN = 0.5  # P(text gives the planned position) that passes
OMITTED = 0.5  # P(text gives no position) that blocks
SURE_PLAN = 0.9  # a different written position blocks only against a plan this sure
SURE_TEXT = 0.6  # ...and when the text gives that other position this surely
BUSY = 0.6  # P(hands still busy) that makes the text's hands worth checking
IDLE_HANDS = 0.25  # P(text says what the hands do) below which it is a review item
WORKERS = 8

POSITIONS = {
    "standing": "upright on their feet, staying in place",
    "moving_on_foot": "walking, running, stepping, pacing or otherwise moving on foot",
    "seated": "sitting on a chair, sofa, bench, bed edge, cushion or the ground",
    "kneeling": "on one or both knees",
    "lying": "lying down, reclining or sprawled",
    "crouching": "crouching or squatting",
    "leaning_or_bowing": "leaning on or over something, or bowing from the waist",
    "hanging": "hanging or suspended, for example upside down from ropes",
    "airborne": "jumping, flying, floating, hovering, falling or otherwise in mid-air",
}
NOT_STATED = "not_stated"
WRITTEN_POSITIONS = {
    **POSITIONS,
    NOT_STATED: "shot_text gives no body position for this character",
}
POSITION_ZH = {
    "moving_on_foot": "在走或跑",
    "seated": "坐着",
    "kneeling": "跪着",
    "lying": "躺着",
    "crouching": "蹲着",
    "leaning_or_bowing": "倚靠或弯着腰",
    "hanging": "悬吊或倒吊着",
    "airborne": "在空中",
    "standing": "站着",
}
COMPATIBLE = {"kneeling": {"crouching"}, "crouching": {"kneeling"}}
# Positions a model draws anyway, or that sit with standing and sitting (leaning on a wall).
UNCHECKED = {"standing", "leaning_or_bowing"}
# A close-up shows only a face and an extreme wide shot a speck; both still show
# someone hanging upside down or lying down.
FRAMING_HIDES = {"close", "extreme-close", "extreme-wide"}
ALWAYS_SHOWN = {"hanging", "lying"}

PLAN_POSITION = (
    "`plan` describes this shot and `previous_shot` the character's last shot before it. "
    "What body position is `character` in as this shot begins? Use only what these texts say about "
    "this character: other people's positions do not apply, and a setting such as a desk or a sofa "
    "does not by itself mean sitting. When neither text gives this character a body position, answer standing."
)
TEXT_POSITION = (
    "A video model draws `character` from `shot_text` alone. What body position does `shot_text` give "
    "this character as the shot begins? A facial expression, a voice, spoken words or the camera moving "
    "toward them does not give a body position, and neither does saying only that they keep their "
    "position from a picture or an earlier frame without naming it."
)
PLAN_HANDS = (
    "`plan` describes this shot and `previous_shot` the character's last shot before it. As this shot "
    "begins, is the character already in the middle of something with their hands: holding or carrying an "
    "object, operating something, gripping, embracing or touching someone, eating or drinking, fighting?"
)
TEXT_HANDS = (
    "A video model draws `character` from `shot_text` alone. Does `shot_text` say what this character's "
    "hands are doing or holding as the shot begins (holding, carrying, operating, gripping, embracing or "
    "touching something or someone)? A facial expression, a voice, spoken words, the camera moving toward "
    "them, or a reference to a picture or an earlier frame does not count."
)
_SUBJECT = re.compile(r"^<Subject (\d+)>\s*[—–-]+\s*([^,\n]+),\s*(.*)$", re.M)


def _words(token: Any) -> str:
    return re.sub(r"[_\-]+", " ", str(token or "")).strip()


def _subjects(h3: str) -> dict[str, str]:
    """``C01`` -> ``<Subject 1>: …`` from the prompt's own subject definitions."""
    return {
        m.group(2).strip().split("@")[0]: f"<Subject {m.group(1)}>: {m.group(3).strip()}"
        for m in _SUBJECT.finditer(h3)
    }


def _shot_texts(h3: str, count: int) -> list[str | None]:
    """Each cut's ``[Shot k]`` passage of the description, as the validator slices it."""
    start, end = h3.find("detailed_description:"), h3.find("overall_soundscape:")
    if start < 0:
        return [None] * count
    body = h3[start : end if end >= 0 else None]
    texts: list[str | None] = []
    for k in range(1, count + 1):
        a = body.find(f"[Shot {k}]")
        b = body.find(f"[Shot {k + 1}]")
        texts.append(body[a : b if b >= 0 else None].strip() if a >= 0 else None)
    return texts


def pair_shots(storyboard: dict) -> list[dict[str, Any]]:
    """Every on-screen character of every cut, with what the plan and the prompt say."""
    pairs = []
    for episode in storyboard.get("episodes") or []:
        scene, last_seen = object(), {}
        for segment in episode.get("segments") or []:
            if segment.get("sceneIndex") != scene:
                scene, last_seen = segment.get("sceneIndex"), {}
            h3 = str(segment.get("h3Prompt") or "")
            subjects = _subjects(h3)
            cuts = segment.get("cuts") or []
            texts = _shot_texts(h3, len(cuts))
            for index, cut in enumerate(cuts):
                actions = (cut.get("continuity") or {}).get("actionStates") or {}
                for character in cut.get("characters") or []:
                    plan = {"frame": str(cut.get("frame") or "")}
                    action = actions.get(character)
                    if isinstance(action, dict):
                        plan.update(
                            enters_as=_words(action.get("in")), leaves_as=_words(action.get("out"))
                        )
                    if texts[index] is not None:
                        pairs.append(
                            {
                                "location": f"{segment.get('id', '?')} [Shot {index + 1}]",
                                "character": character,
                                "size": cut.get("size"),
                                "subject": subjects.get(character, character),
                                "plan": plan,
                                "previous_shot": last_seen.get(
                                    character, "none: first appearance in this scene"
                                ),
                                "shot_text": texts[index],
                            }
                        )
                for character in cut.get("characters") or []:
                    last_seen[character] = str(cut.get("frame") or "")
    return pairs


def _plan_state(pair: dict) -> dict:
    return {
        "character": pair["subject"],
        "plan": pair["plan"],
        "previous_shot": pair["previous_shot"],
    }


def _text_state(pair: dict) -> dict:
    return {"character": pair["subject"], "shot_text": pair["shot_text"]}


def _valid(probabilities: Any, options: set[str]) -> bool:
    return (
        isinstance(probabilities, dict)
        and bool(probabilities)
        and set(probabilities) <= options
        and all(
            not isinstance(p, bool)
            and isinstance(p, (int, float))
            and math.isfinite(p)
            and 0 <= p <= 1
            for p in probabilities.values()
        )
    )


def _top(probabilities: dict[str, float]) -> tuple[str, float]:
    best = max(probabilities, key=probabilities.get)
    return best, probabilities[best]


def _message(pair: dict, names: dict[str, str], text: str) -> str:
    who = f"{names.get(pair['character'], pair['character'])}（{pair['character']}）"
    return f"{pair['location']}：{who}{text}"


def check_shot_states(
    storyboard: dict,
    cast: dict,
    *,
    outline: dict | None = None,
    api_key: str | None = None,
    model: str = "jev-latest",
    cache_dir: Path | None = None,
) -> dict[str, Any]:
    """``status`` passed/failed/unavailable, with ``blocking`` errors and ``review`` items."""
    pairs = pair_shots(storyboard)
    result: dict[str, Any] = {
        "status": "passed",
        "checked": 0,
        "blocking": [],
        "review": [],
        "reused": 0,
    }
    if not pairs:
        return result
    try:
        from .jev_client import _choice, _find_api_key, open_client
    except ImportError:  # Direct CLI execution from the pipeline directory.
        from jev_client import _choice, _find_api_key, open_client

    key = (api_key or _find_api_key()).strip()
    names = {**_names(outline or {}), **_names(cast)}
    errors: list[str] = []
    counting = threading.Lock()

    def ask(kind: str, state: dict, instructions: str, criteria: dict[str, str] | None):
        """Probabilities over ``criteria``, or ``{"yes": p}`` for a yes/no question; None on failure."""
        token = hashlib.sha256(
            json.dumps(
                [kind, model, state, instructions, criteria], sort_keys=True, ensure_ascii=False
            ).encode()
        ).hexdigest()
        options = set(criteria) if criteria else {"yes"}
        target = Path(cache_dir) / f"{token}.json" if cache_dir else None
        if target:
            try:
                saved = json.loads(target.read_text(encoding="utf-8"))
                if saved.get("schema_version") == 1 and saved.get("input_sha256") == token:
                    if _valid(saved.get("answer"), options):
                        with counting:
                            result["reused"] += 1
                        return saved["answer"]
            except (OSError, ValueError, AttributeError):
                pass
        if not key:
            errors.append("TYPESAFE_API_KEY is not configured")
            return None
        from typesafe_sdk import Choice, Noul

        question = (
            Choice(instructions=instructions, criteria=criteria)
            if criteria
            else Noul(instructions=instructions)
        )
        try:
            with open_client(key) as client:
                response = client.system_one(state=state, questions={"q": question}, model=model)
            answer = (getattr(response, "answers", None) or {}).get("q")
            if criteria:
                _c, _p, probabilities, problem = _choice(answer, options)
                if problem or not probabilities:
                    raise ValueError(problem or "missing_probabilities")
            else:
                probabilities = {"yes": float(answer.noul)}
            if not _valid(probabilities, options):
                raise ValueError("malformed_answer")
        except Exception as exc:  # noqa: BLE001 - reported; the shot stays unjudged
            errors.append(f"{type(exc).__name__}: {exc}")
            return None
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
                        "answer": probabilities,
                    },
                )
            except OSError:
                pass
        return probabilities

    def judge(pair: dict) -> None:
        planned = ask("plan_position", _plan_state(pair), PLAN_POSITION, POSITIONS)
        if planned:
            position, likely = _top(planned)
            hidden = pair["size"] in FRAMING_HIDES and position not in ALWAYS_SHOWN
            if position not in UNCHECKED and likely >= PLANNED and not hidden:
                written = ask("text_position", _text_state(pair), TEXT_POSITION, WRITTEN_POSITIONS)
                pair["position"] = (position, likely, written)
        busy = ask("plan_hands", _plan_state(pair), PLAN_HANDS, None)
        if busy and busy["yes"] >= BUSY:
            pair["hands"] = ask("text_hands", _text_state(pair), TEXT_HANDS, None)

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        list(pool.map(judge, pairs))

    for pair in pairs:
        if "position" in pair and pair["position"][2]:
            position, likely, written = pair["position"]
            result["checked"] += 1
            given = written.get(position, 0.0) + sum(
                written.get(c, 0.0) for c in COMPATIBLE.get(position, ())
            )
            other, other_p = _top(written)
            if given >= WRITTEN or other == position:
                pass
            elif written.get(NOT_STATED, 0.0) >= OMITTED:
                result["blocking"].append(
                    _message(
                        pair,
                        names,
                        f"进镜时{POSITION_ZH[position]}，这段正文却没写姿态。视频模型只读 h3Prompt，"
                        "frame、continuity 和尾帧图都代替不了正文：在这一段里写明此刻的姿态和正在做的动作。"
                        "姿态如果确实变了，正文写新姿态，并同步改这一镜的 frame 和 continuity。",
                    )
                )
            else:
                text = (
                    f"按分镜进镜时{POSITION_ZH[position]}，正文却写成{POSITION_ZH.get(other, other)}。"
                    "两边要一致：改正文，或同步改这一镜的 frame 和 continuity。"
                )
                if likely >= SURE_PLAN and other != NOT_STATED and other_p >= SURE_TEXT:
                    result["blocking"].append(_message(pair, names, text))
                else:
                    result["review"].append(
                        {
                            "reason": "position_differs",
                            "message": _message(pair, names, text),
                            "planned": position,
                            "written": written,
                        }
                    )
        hands = pair.get("hands")
        if hands and hands["yes"] < IDLE_HANDS:
            ledger = pair["plan"].get("enters_as")
            text = "手上的动作从上一镜延续到这一镜" + (f"（账本 in：{ledger}）" if ledger else "")
            result["review"].append(
                {
                    "reason": "hands_unwritten",
                    "message": _message(pair, names, f"{text}，正文没写手上在做什么。"),
                }
            )
    if errors:
        result["errors"] = errors[:5]
        if not result["checked"] and not result["blocking"]:
            result["status"] = "unavailable"
            if not key:
                result["error"] = "TYPESAFE_API_KEY is not configured"
    if result["blocking"]:
        result["status"] = "failed"
    return result


def _names(document: dict) -> dict[str, str]:
    """Character id -> name; cast cards may omit ids, which the outline then supplies."""
    return {
        c["id"]: c.get("name") or c["id"]
        for c in document.get("characters") or []
        if isinstance(c, dict) and c.get("id")
    }
