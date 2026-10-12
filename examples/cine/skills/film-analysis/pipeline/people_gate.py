"""Source presence constrains a faithful script's scene cast."""

try:
    from .form_gate import kept_pov_holder
    from .location_gate import _label, line_locations
    from .performance_gate import pair_lines
    from .speaker_evidence import mapping_ids
except ImportError:
    from form_gate import kept_pov_holder
    from location_gate import _label, line_locations
    from performance_gate import pair_lines
    from speaker_evidence import mapping_ids


def used_unsettled(script, attributions):
    pairs = pair_lines(script, attributions, readable_only=False)
    seen, result = set(), []
    for pair in pairs:
        row = attributions[pair["source_index"]]
        if (
            row.get("speaker_needs_review", row.get("needs_review", True))
            or row.get("speaker") in (None, "", "unclear")
        ) and pair["source_index"] not in seen:
            seen.add(pair["source_index"])
            result.append(
                {
                    "source_id": row.get("id"),
                    "source_index": pair["source_index"],
                    "text": row.get("text"),
                    "scene": pair["scene"],
                    "start": row.get("start"),
                    "end": row.get("end"),
                    "evidence": row.get("evidence"),
                    "reasons": row.get("review_reasons") or [],
                }
            )
    return result


def check_people(script, outline, material, cast, mapping, attributions):
    mode = (outline.get("params") or {}).get("adaptMode")
    result = {
        "status": "passed",
        "blocking": [],
        "review": [],
        "speaker_check": "native_source_order",
    }
    if mode not in ("忠实", "抽核"):
        return {**result, "status": "skipped", "reason": "free adaptation"}
    notes = (material or {}).get("source_locations") or {}
    if notes.get("schema_version") not in (2, 3):
        result["review"].append(
            {"reason": "source_people_missing", "message": "原片人物表缺失，请完成 v2 视觉拉片。"}
        )
        return result
    ids = mapping_ids(mapping)
    cast_names = {c["id"]: c.get("name") or c["id"] for c in cast.get("characters") or []}
    known = {location["id"]: location for location in notes.get("locations") or []}
    by_scene = {scene["id"]: scene for scene in outline.get("scenes") or []}
    facts = {}
    complete = notes.get("status") == "read" and not notes.get("unread_shots")
    extras = {
        p["person_id"] for p in (mapping or {}).get("people") or [] if p.get("status") == "extra"
    }
    for lid in known:
        shots = [
            s for s in notes.get("shots") or [] if s.get("place") == lid and not s.get("teaser")
        ]
        counts = {}
        for shot in shots:
            for cid in {ids[p] for p in shot.get("people") or [] if p in ids}:
                counts[cid] = counts.get(cid, 0) + 1
        spoken = {
            a["speaker"]
            for a in attributions
            if not a.get("speaker_needs_review", a.get("needs_review", False))
            and a.get("delivery") == "spoken"
            and a.get("speaker") in cast_names
            and lid
            in {loc["id"] for loc in line_locations(known.values(), a.get("start"), a.get("end"))}
        }
        required = spoken | {cid for cid, count in counts.items() if count > len(shots) / 2}
        unmapped = {
            p for s in shots for p in s.get("people") or [] if p not in ids and p not in extras
        }
        unsettled = any(
            (
                a.get("speaker_needs_review", a.get("needs_review", True))
                or a.get("speaker") in (None, "", "unclear")
            )
            and lid
            in {loc["id"] for loc in line_locations(known.values(), a.get("start"), a.get("end"))}
            for a in attributions
            if not a.get("teaser")
        )
        facts[lid] = (required, set(counts) | spoken, complete and not unmapped and not unsettled)
    for episode in script.get("episodes") or []:
        for index, scene in enumerate(episode.get("scenes") or [], 1):
            origins = (by_scene.get(scene.get("sceneId")) or {}).get("from") or []
            if not origins or any(lid not in facts for lid in origins):
                continue
            required, allowed, certain = set(), set(), True
            for lid in origins:
                required.update(facts[lid][0])
                allowed.update(facts[lid][1])
                certain = certain and facts[lid][2]
            # The camera holder may be heard without appearing in the source's visible cast.
            holder = kept_pov_holder(outline)
            if holder:
                allowed.add(holder)
            actual = set(scene.get("characters") or []) - {"VO"}
            label = f"第 {index} 场 {scene.get('sceneId')}"
            times = "、".join(_label(known[lid]) for lid in origins)
            if not certain:
                result["review"].append(
                    {
                        "reason": "source_people_partial",
                        "message": (
                            f"{label} 对应原片 {times} 的人物证据不完整，不能据此确认谁未在场。"
                        ),
                    }
                )
            for cid in sorted(required - actual):
                message = (
                    f"{label} 漏掉在场主要人物 {cast_names.get(cid, cid)}（{cid}）；"
                    f"原片在 {times}。"
                )
                _finding(result, mode, "source_person_missing", message)
            for cid in sorted(actual - allowed):
                message = (
                    f"{label} 多出不在场人物 {cast_names.get(cid, cid)}（{cid}）；"
                    f"原片 {times} 既未露面也未说话。"
                )
                _finding(result, mode if certain else "抽核", "source_person_extra", message)
    if mode == "忠实":
        for row in used_unsettled(script, attributions):
            result["review"].append({"reason": "speaker_unsettled", **row})
    if result["blocking"]:
        result["status"] = "failed"
    return result


def _finding(result, mode, reason, message):
    if mode == "忠实":
        result["blocking"].append(message)
    else:
        result["review"].append({"reason": reason, "message": message})
