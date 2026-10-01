"""Bind visual people to cast cards using appearance and bounded shot evidence."""

import hashlib
import json
from contextlib import ExitStack
from pathlib import Path

EXTRA = "extra"
UNCLEAR = "unclear"
MIN_CONFIDENCE = 0.7
EXTRA_CRITERION = "不是角色卡上的任何人（工作人员、路人、群演等）；外貌可辨，但没有对应角色卡"
UNCLEAR_CRITERION = "人物看不清或外貌证据不足，无法区分角色卡人选与非角色人物"
MAX_CONTEXT_SHOTS = 12
MAX_SHOT_DESCRIPTION_CHARS = 120


def appearance_question(person, characters, *, context=None, excluded=None):
    from typesafe_sdk import Choice

    options = {c["id"]: appearance(c) for c in characters if c["id"] not in (excluded or {})}
    options.update({EXTRA: EXTRA_CRITERION, UNCLEAR: UNCLEAR_CRITERION})
    return Choice(
        instructions=(
            "Identify this on-screen person using appearance, apparent sex/age, name captions, "
            "their own shot observations, co-visible people and confirmed caption matches. "
            "Use observed evidence, never a guessed plot. An observation of another person's "
            "action is not this person's action. Different P ids may be the same character "
            "after a costume change, except two live people in one shot cannot be the same "
            "character; excluded options are omitted. A clearly observed occupation or "
            "function outweighs superficial clothing resemblance when it conflicts with "
            "a cast role. When both appearance and observed activity match a cast role, "
            "match that role even without a name caption. Changed clothes or a missing "
            "caption alone do not make someone extra. Choose extra for positive evidence "
            "distinguishing this person from the cast, or unclear for insufficient evidence.\n"
            + json.dumps(
                {"person": person_definition(person), "context": context or {}},
                ensure_ascii=False,
            )
        ),
        criteria=options,
    )


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()


def appearance(character):
    persona = character.get("persona") or {}
    image = character.get("image") or {}
    return "｜".join(
        str(v)
        for v in (
            character.get("name"),
            persona.get("gender"),
            persona.get("ageRange"),
            persona.get("appearance"),
            image.get("prompt"),
            character.get("oneLiner"),
        )
        if v
    )


def person_definition(person):
    return {key: person.get(key) for key in ("id", "look", "name_caption")}


def identity_cast(cast):
    return [
        {
            "id": c["id"],
            "name": c.get("name"),
            "aliases": c.get("aliases") or [],
            "look": appearance(c),
        }
        for c in cast.get("characters") or []
        if c.get("id")
    ]


def _caption_rows(people, characters, notes):
    rows = {}
    for person in people:
        names = [
            c
            for c in characters
            if person.get("name_caption")
            and person["name_caption"] in [c.get("name"), *(c.get("aliases") or [])]
        ]
        if names:
            row = _empty_row(person["id"])
            if len(names) == 1:
                row.update(
                    character_id=names[0]["id"],
                    status="matched",
                    confidence=1.0,
                    method="name_caption",
                )
            else:
                row["reason"] = "人名字幕对应多个角色"
            rows[person["id"]] = row
    conflicts = set()
    for shot in notes.get("shots") or []:
        seen = {}
        for pid in shot.get("people") or []:
            row = rows.get(pid) or {}
            if row.get("status") == "matched":
                cid = row["character_id"]
                if cid in seen:
                    conflicts.update((seen[cid], pid))
                seen[cid] = pid
    for pid in conflicts:
        rows[pid].update(
            status="review",
            character_id=None,
            confidence=0.0,
            reason="同框人物的人名字幕对应同一角色，字幕与人物表冲突",
        )
    return rows


def _empty_row(pid):
    return {"person_id": pid, "character_id": None, "status": "review", "confidence": 0.0}


def appearance_context(person, notes, characters, resolved, captions):
    """Only this person's shots and co-visible identities inform its question."""
    definitions = {p["id"]: p for p in notes.get("people") or []}
    names = {c["id"]: c.get("name") or c["id"] for c in characters}
    shots = [s for s in notes.get("shots") or [] if person["id"] in (s.get("people") or [])]
    indices = (
        sorted(
            {
                round(i * (len(shots) - 1) / (MAX_CONTEXT_SHOTS - 1))
                for i in range(MAX_CONTEXT_SHOTS)
            }
        )
        if len(shots) > MAX_CONTEXT_SHOTS
        else range(len(shots))
    )
    co_visible = sorted({p for s in shots for p in s.get("people") or [] if p != person["id"]})
    others, excluded = [], {}
    for pid in co_visible:
        row = resolved.get(pid) or {}
        cid = row.get("character_id") if row.get("status") == "matched" else None
        others.append(
            {
                "person_id": pid,
                "look": (definitions.get(pid) or {}).get("look"),
                "status": row.get("status")
                if row.get("status") in ("matched", EXTRA)
                else "unresolved",
                "character_id": cid,
                "character_name": names.get(cid),
            }
        )
        if cid:
            reason = excluded.setdefault(
                cid, {"person_ids": [], "shots": [], "reason": "与已对应此角色的现场人物同框"}
            )
            reason["person_ids"].append(pid)
            reason["shots"] = sorted(
                set(reason["shots"])
                | {
                    s.get("index", i)
                    for i, s in enumerate(notes.get("shots") or [])
                    if person["id"] in (s.get("people") or []) and pid in s["people"]
                }
            )
    return {
        "shots": [
            {
                "index": shots[i].get("index"),
                "what": str(shots[i].get("what") or "")[:MAX_SHOT_DESCRIPTION_CHARS],
                "people": list(shots[i].get("people") or []),
            }
            for i in indices
        ],
        "co_visible": others,
        "caption_matches": [
            {
                "person_id": pid,
                "character_id": row["character_id"],
                "character_name": names[row["character_id"]],
            }
            for pid, row in captions.items()
            if row["status"] == "matched"
        ],
    }, excluded


def map_people(notes, cast, *, model="jev-latest", api_key=None, cached=None):
    try:
        from .jev_client import (
            _ambiguous,
            _ask_batched,
            _choice,
            _find_api_key,
            open_client,
        )
    except ImportError:
        from jev_client import (
            _ambiguous,
            _ask_batched,
            _choice,
            _find_api_key,
            open_client,
        )

    characters = [c for c in cast.get("characters") or [] if c.get("id")]
    people = notes.get("people") or []
    captions = _caption_rows(people, characters, notes)
    resolved = dict(captions)
    pending = [p for p in people if p["id"] not in captions]
    common = {
        "cast": digest(identity_cast(cast)),
        "source": digest(
            {key: notes.get(key) for key in ("schema_version", "source_id", "revision_id")}
        ),
        "mapper": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    }
    cache = cached if isinstance(cached, dict) else {}
    previous = (
        {
            row["person_id"]: row
            for row in cache.get("people") or []
            if isinstance(row, dict) and isinstance(row.get("person_id"), str)
        }
        if cache.get("schema_version") == 1 and cache.get("model") == model
        else {}
    )
    reused = 0
    key = _find_api_key().strip() if api_key is None else api_key.strip()
    with ExitStack() as stack:
        client = None
        while pending:
            batch, deferred = [], []
            selected = set()
            for person in pending:
                neighbors = {
                    p
                    for s in notes.get("shots") or []
                    if person["id"] in (s.get("people") or [])
                    for p in s.get("people") or []
                }
                if selected & neighbors:
                    deferred.append(person)
                else:
                    batch.append(person)
                    selected.add(person["id"])
            pending = deferred
            questions, inputs = {}, {}
            for person in batch:
                pid = person["id"]
                context, excluded = appearance_context(
                    person, notes, characters, resolved, captions
                )
                inputs[pid] = {
                    **common,
                    "person": digest(person_definition(person)),
                    "context": digest(context),
                    "excluded": digest(excluded),
                }
                old = previous.get(pid)
                if (
                    old
                    and old.get("input_sha256") == inputs[pid]
                    and not old.get("error")
                    and old.get("character_id") not in excluded
                    and _valid_mapping({"people": [old]}, {"people": [person]}, cast)
                ):
                    resolved[pid] = dict(old)
                    reused += 1
                    continue
                questions[pid] = appearance_question(
                    person, characters, context=context, excluded=excluded
                )
                resolved[pid] = {
                    **_empty_row(pid),
                    "input_sha256": inputs[pid],
                    "context": context,
                    "excluded": excluded,
                }
            if not questions:
                continue
            answers, error = {}, None
            try:
                if not key:
                    raise ValueError("TYPESAFE_API_KEY is not configured")
                if client is None:
                    client = stack.enter_context(open_client(key))
                reply = _ask_batched(client, {}, questions, model)
                answers = getattr(reply, "answers", None) or {}
            except Exception as exc:  # noqa: BLE001 - missing identity is reviewed
                error = f"{type(exc).__name__}: {exc}"
            for pid, question in questions.items():
                row = resolved[pid]
                choice, confidence, probabilities, problem = _choice(
                    answers.get(pid), set(question.criteria)
                )
                row.update(confidence=confidence, method="appearance", probabilities=probabilities)
                if error or problem:
                    row["error"] = error or problem
                if (
                    error
                    or problem
                    or confidence < MIN_CONFIDENCE
                    or _ambiguous(choice, probabilities)
                    or choice == UNCLEAR
                ):
                    row["reason"] = error or problem or "外貌对应置信度不足"
                elif choice == EXTRA:
                    row["status"] = "extra"
                else:
                    row.update(character_id=choice, status="matched")
    rows = [resolved[p["id"]] for p in people]
    errors = [row["error"] for row in rows if row.get("error")]
    return {
        "schema_version": 1,
        "model": model,
        "people": rows,
        "errors": errors,
        "reused": reused,
        "input_sha256": {**common, "people": digest([row.get("input_sha256") for row in rows])},
        "status": "partial" if any(r["status"] == "review" for r in rows) else "mapped",
    }


def ensure_person_map(directory, notes, cast_path, *, model="jev-latest"):
    try:
        from .production import atomic_json
    except ImportError:
        from production import atomic_json

    cast_path = Path(cast_path)
    raw = cast_path.read_bytes()
    cast = json.loads(raw)
    target = Path(directory) / "person-map.json"
    try:
        cached = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        cached = {}
    if not isinstance(cached, dict):
        cached = {}
    result = map_people(notes, cast, model=model, cached=cached)
    persisted = {k: v for k, v in result.items() if k != "reused"}
    if persisted != cached:
        atomic_json(target, persisted)
    return result


def _valid_mapping(value, notes, cast):
    rows = value.get("people")
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        return False
    wanted = {p["id"] for p in notes.get("people") or []}
    ids = [row.get("person_id") for row in rows]
    if any(not isinstance(pid, str) for pid in ids):
        return False
    characters = {c["id"] for c in cast.get("characters") or []}
    return (
        len(set(ids)) == len(ids)
        and set(ids) == wanted
        and all(
            row.get("status") in ("matched", "extra", "review")
            and (row["status"] != "matched" or row.get("character_id") in characters)
            for row in rows
        )
    )
