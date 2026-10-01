"""Map the recorded visual and context judgments without another model call."""

import copy

try:
    from .line_performance import performance_current
    from .person_map import EXTRA
except ImportError:
    from line_performance import performance_current
    from person_map import EXTRA

METHOD = "visual_then_context_v1"


def overlapping_shots(notes, row):
    return [
        i
        for i, shot in enumerate(notes.get("shots") or [])
        if shot["start"] < row["end"] and shot["end"] > row["start"]
    ]


def present_people(notes, row):
    """Observed on-site people; a voice-over or neighboring shot adds nobody."""
    ids = {
        pid
        for i in overlapping_shots(notes, row)
        for shot in [(notes.get("shots") or [])[i]]
        if shot.get("place") != "insert" and not shot.get("teaser")
        for pid in shot.get("people") or []
    }
    return [p["id"] for p in notes.get("people") or [] if p["id"] in ids]


def mapping_ids(mapping):
    return {
        p["person_id"]: p["character_id"]
        for p in (mapping or {}).get("people") or []
        if p.get("status") == "matched" and p.get("character_id")
    }


def attribute(
    rows, cast, notes, mapping, *, api_key=None, model=None, context_rows=None, vision_model=None
):
    """A visible P wins; only current, valid B evidence can fill A's gaps."""
    del api_key, model
    try:
        from .line_judgment import check_constraints, judgment_current
    except ImportError:
        from line_judgment import check_constraints, judgment_current

    people = {p["id"]: p for p in notes.get("people") or []}
    characters = {c["id"]: c for c in (cast or {}).get("characters") or [] if c.get("id")}
    mapped = {
        pid: cid
        for pid, cid in mapping_ids(mapping).items()
        if pid in people and cid in characters
    }
    extras = {
        p.get("person_id")
        for p in (mapping or {}).get("people") or []
        if p.get("status") == "extra"
    }
    source_rows = rows if context_rows is None else context_rows
    positions = {id(row): i for i, row in enumerate(source_rows)}
    entries = []
    models = []
    for position, row in enumerate(rows):
        source_index = positions.get(id(row), position)
        observed = row.get("performance") or {}
        configured = (
            vision_model
            if vision_model is not None
            else observed.get("model") or notes.get("model")
        )
        current_a = performance_current(notes, row, configured)
        a_speaker = observed.get("speaker", "unclear") if current_a else "unclear"
        a = {
            "speaker": a_speaker,
            "person_id": a_speaker,
            "current": current_a,
            "model": observed.get("model"),
            "character_id": mapped.get(a_speaker),
            "status": "missing_or_stale"
            if not current_a
            else "extra"
            if a_speaker in extras
            else "mapped"
            if a_speaker in mapped
            else "unmapped"
            if a_speaker in people
            else a_speaker,
        }
        if not current_a:
            a["reason"] = "逐句画面证据缺失或已过期"
        recorded_b = row.get("judgment") or {}
        b = (
            {k: recorded_b.get(k) for k in ("speaker", "delivery", "basis", "reason")}
            if recorded_b
            else {}
        )
        current_b = False
        constraints = {
            "status": "review",
            "needs_review": True,
            "review_reasons": [],
            "counts_as_present": False,
        }
        reasons = []
        candidate, delivery = "unclear", "unclear"
        if not current_a:
            reasons.append(a["reason"])
        elif a_speaker in people:
            candidate, delivery = a_speaker, "spoken"
            constraints = {
                "source": "a",
                "status": "passed",
                "needs_review": False,
                "review_reasons": [],
                "counts_as_present": True,
            }
        elif a_speaker in ("offscreen", "unclear"):
            current_b = judgment_current(notes, source_rows, source_index, configured)
            if recorded_b:
                b.update(current=current_b, model=recorded_b.get("model"))
            if not current_b:
                reasons.append("上下文补缺证据缺失或已过期，需补读B步")
            else:
                constraints = check_constraints(recorded_b, notes, row, mapping)
                previous_constraints = (recorded_b.get("constraints") or {}).get(
                    "review_reasons"
                ) or []
                structural = [
                    r
                    for r in recorded_b.get("review_reasons") or []
                    if r not in previous_constraints
                ]
                reasons.extend(structural)
                reasons.extend(constraints.get("review_reasons") or [])
                if (
                    recorded_b.get("needs_review")
                    and not (recorded_b.get("review_reasons") or [])
                    and not reasons
                ):
                    reasons.append("上下文补缺尚待人工确认")
                b["needs_review"] = bool(reasons)
                if not reasons and constraints.get("status") == "passed":
                    candidate, delivery = recorded_b["speaker"], recorded_b["delivery"]
        else:
            reasons.append("画面返回的说话人编号不合法")
        if row.get("sentence_boundary_review"):
            reasons.append("句中有句末标点，但无可靠时间切点；请分别核对发言")
            constraints = {
                **constraints,
                "status": "review",
                "needs_review": True,
                "counts_as_present": False,
                "review_reasons": [*constraints.get("review_reasons", []), reasons[-1]],
            }
        speaker = candidate
        if candidate == "crowd" or candidate in extras:
            speaker = EXTRA
        elif candidate in people and cast:
            speaker = mapped.get(candidate, "unclear")
            if speaker == "unclear":
                reasons.append("画面人物尚未可靠对应角色卡")
        if candidate == "unclear" and not reasons:
            reasons.append("A、B两步都未确定说话人")
        review = bool(reasons)
        if review:
            speaker = "unclear"
            delivery = "unclear"
        name = (
            "非角色人物"
            if speaker == EXTRA
            else (characters.get(speaker) or {}).get("name")
            or (people.get(speaker) or {}).get("name_caption")
            or speaker
        )
        entries.append(
            {
                "id": row.get("id") or f"seg{source_index:03d}",
                "source_index": source_index,
                **{k: row.get(k) for k in ("text", "start", "end")},
                "speaker": speaker,
                "speaker_name": name,
                "confidence": 0.0 if review else 1.0,
                "delivery": delivery,
                "delivery_confidence": 0.0 if review else 1.0,
                "speaker_needs_review": review,
                "delivery_needs_review": review,
                "needs_review": review,
                "review_reasons": list(dict.fromkeys(reasons)),
                **({"performance": copy.deepcopy(observed)} if observed else {}),
                "evidence": {
                    "a": a,
                    "b": b,
                    "present_people": present_people(notes, row),
                    "constraints": constraints,
                },
            }
        )
        if current_a and observed.get("model"):
            models.append(observed["model"])
    return {
        "method": METHOD,
        "model": (vision_model or None)
        if vision_model is not None
        else (models[0] if models else notes.get("model")),
        "attributions": entries,
    }
