"""Derive candidate identities; merge the recorded A/B judgments deterministically."""

from __future__ import annotations

import re
from itertools import combinations
from typing import Any

try:
    from .jev_client import _ask_batched, _find_api_key, _unit_interval, open_client
except ImportError:
    from jev_client import _ask_batched, _find_api_key, _unit_interval, open_client

MERGE_CONFIDENCE = 0.7
MERGE_REVIEW = 0.4
_MAX_STATE_LINES = 150
_MAX_MERGE_ROLES = 12
_MAX_EVIDENCE_CHARS = 80

ADDRESS_TERMS = {
    "妈": "母亲",
    "妈妈": "母亲",
    "老妈": "母亲",
    "爸": "父亲",
    "爸爸": "父亲",
    "老爸": "父亲",
    "爹": "父亲",
    "老婆": "妻子",
    "老公": "丈夫",
    "外婆": "外婆",
    "姥姥": "外婆",
    "奶奶": "祖母",
    "外公": "外公",
    "姥爷": "外公",
    "爷爷": "祖父",
    "姐姐": "姐姐",
    "妹妹": "妹妹",
    "哥哥": "哥哥",
    "弟弟": "弟弟",
    "嫂子": "嫂子",
    "表妹": "表妹",
    "表姐": "表姐",
    "表哥": "表哥",
    "表弟": "表弟",
    "外孙女": "外孙女",
    "孙女": "孙女",
    "孙子": "孙子",
}

# What each kinship role means for who addresses whom. A role name alone ("母亲")
# does not say who speaks to whom; the model needs the relation to use address
# terms.
ROLE_DESCRIPTIONS = {
    "母亲": "被子女叫「妈」的女性",
    "父亲": "被子女叫「爸」的男性",
    "妻子": "被丈夫叫「老婆」的已婚女性",
    "丈夫": "被妻子叫「老公」、或叫妻子「老婆」的已婚男性",
    "外婆": "被外孙辈叫「外婆」「姥姥」的年长女性",
    "祖母": "被孙辈叫「奶奶」的年长女性",
    "外公": "被外孙辈叫「外公」「姥爷」的年长男性",
    "祖父": "被孙辈叫「爷爷」的年长男性",
    "姐姐": "被平辈年幼者叫「姐姐」的女性",
    "妹妹": "被平辈年长者叫「妹妹」的女性",
    "哥哥": "被平辈年幼者叫「哥哥」的男性",
    "弟弟": "被平辈年长者叫「弟弟」的男性",
    "嫂子": "被叫「嫂子」的已婚女性，是兄长的妻子",
    "表妹": "被表亲叫「表妹」的平辈年幼女性",
    "表姐": "被表亲叫「表姐」的平辈年长女性",
    "表哥": "被表亲叫「表哥」的平辈年长男性",
    "表弟": "被表亲叫「表弟」的平辈年幼男性",
    "外孙女": "被外祖辈叫「外孙女」的女孩",
    "孙女": "被祖辈叫「孙女」的女孩",
    "孙子": "被祖辈叫「孙子」的男孩",
}

# A self-introduction names the speaker. The name must end at punctuation (so
# 「我叫小明今年十岁」 is not read as 小明今年) and cannot start with a pronoun
# (so 「我叫你别去！」 does not produce a person called 你别去).
_SELF_NAME = re.compile(
    r"(?:我叫|我名叫|我的名字(?:是|叫))([一-龥]{2,4})(?=[，。！？、,.!?；;…～~\s]|$)"
)
_PRONOUN_START = tuple("你您他她它我咱")


def _self_introduced_names(text: str) -> list[str]:
    names = []
    for match in _SELF_NAME.finditer(text):
        name = match.group(1)
        if not name.startswith(_PRONOUN_START) and name not in names:
            names.append(name)
    return names


def _address_roles(text: str) -> dict[str, list[str]]:
    """Kinship roles addressed in ``text``, with the terms that named them.

    Longer terms are matched first and masked, so 「外孙女」 does not also
    produce 孙女 and 「妈妈」 is not counted twice. Roles are returned in the
    order the dialogue first uses them.
    """
    remaining = text
    roles: dict[str, list[str]] = {}
    first_seen: dict[str, int] = {}
    for term in sorted(ADDRESS_TERMS, key=len, reverse=True):
        at = remaining.find(term)
        if at < 0:
            continue
        role = ADDRESS_TERMS[term]
        roles.setdefault(role, []).append(term)
        first_seen[role] = min(first_seen.get(role, at), at)
        remaining = remaining.replace(term, "□" * len(term))
    return {role: roles[role] for role in sorted(roles, key=first_seen.__getitem__)}


def _evidence(segments: list[dict[str, Any]], terms: list[str], limit: int = 4) -> list[str]:
    lines = []
    for segment in segments:
        text = str(segment.get("text") or "")
        if any(term in text for term in terms):
            lines.append(text[:_MAX_EVIDENCE_CHARS])
            if len(lines) >= limit:
                break
    return lines


def _mentions(segments: list[dict[str, Any]], terms: list[str]) -> int:
    return sum(1 for s in segments if any(t in str(s.get("text") or "") for t in terms))


def _same_person_groups(
    roles: dict[str, dict[str, Any]],
    segments: list[dict[str, Any]],
    *,
    api_key: str | None = None,
    model: str = "jev-latest",
) -> tuple[list[list[str]], dict[str, Any]]:
    """Group candidate roles into people. Only a confident JEV judgment merges.

    Without JEV (no key, a failed call, a missing answer) every role stays its
    own person: a split roster costs attribution confidence, which is flagged,
    whereas a wrong merge silently gives one person another's lines.

    Pairwise questions grow quadratically, so only the ``_MAX_MERGE_ROLES`` most
    mentioned roles are compared. The rest are never merged and are listed in
    ``unasked_roles``, with status ``partial`` when JEV judged the others.
    """
    names = list(roles)
    singletons = [[name] for name in names]
    ranked = sorted(range(len(names)), key=lambda i: (-int(roles[names[i]].get("mentions", 0)), i))
    asked = sorted(ranked[:_MAX_MERGE_ROLES])
    unasked = [names[i] for i in sorted(ranked[_MAX_MERGE_ROLES:])]
    pairs = list(combinations(asked, 2))

    def outcome(status: str, **extra: Any) -> dict[str, Any]:
        return {"status": status, "merged": [], "review": [], "unasked_roles": unasked, **extra}

    if not pairs:
        return singletons, outcome("not_needed")

    key = _find_api_key().strip() if api_key is None else api_key.strip()
    if not key:
        return singletons, outcome("unavailable")

    from typesafe_sdk import Noul

    dialogue = [f"[{i + 1}] {s.get('text', '')}" for i, s in enumerate(segments) if s.get("text")][
        :_MAX_STATE_LINES
    ]
    questions = {}
    for i, j in pairs:
        a, b = names[i], names[j]
        questions[f"same__{i}__{j}"] = Noul(
            instructions=(
                f"对白中的「{a}」（{roles[a]['description']}；出现于："
                f"{' / '.join(roles[a]['evidence']) or '无'}）与「{b}」（"
                f"{roles[b]['description']}；出现于：{' / '.join(roles[b]['evidence']) or '无'}）"
                "是同一个人吗？只有当对白里的称呼关系或自我介绍能说明两者是同一个人时才回答是；"
                "两个称呼只是可能指同一个人、或无法判断时，回答否。"
            )
        )
    try:
        with open_client(key) as client:
            response = _ask_batched(client, {"dialogue": dialogue}, questions, model)
    except Exception as exc:  # noqa: BLE001 - any failure means "no merge"
        return singletons, outcome("failed", error=f"{type(exc).__name__}: {exc}")

    answers = getattr(response, "answers", None) or {}
    probability: dict[tuple[int, int], float] = {}
    review = []
    for i, j in pairs:
        answer = answers.get(f"same__{i}__{j}")
        value = getattr(answer, "noul", None) if answer is not None else None
        p_same = _unit_interval(value)
        if p_same is None:
            reason = "missing_answer" if value is None else "malformed_answer"
            review.append({"roles": [names[i], names[j]], "reason": reason})
            continue
        probability[(i, j)] = p_same
        if MERGE_REVIEW <= p_same < MERGE_CONFIDENCE:
            review.append({"roles": [names[i], names[j]], "p_same": p_same})

    # Complete linkage: two groups join only when every cross pair is a
    # confident "same person". A chain of pairwise yeses (A=B, B=C) must not
    # merge A with C when JEV was not sure of that pair.
    groups = [[i] for i in range(len(names))]
    for (i, j), p_same in sorted(probability.items(), key=lambda item: -item[1]):
        if p_same < MERGE_CONFIDENCE:
            break
        gi = next(g for g in groups if i in g)
        gj = next(g for g in groups if j in g)
        if gi is gj:
            continue
        if all(
            probability.get((min(x, y), max(x, y)), 0.0) >= MERGE_CONFIDENCE
            for x in gi
            for y in gj
        ):
            gi.extend(gj)
            groups.remove(gj)
    resolution = outcome("partial" if unasked else "judged", review=review)
    resolution["merged"] = [[names[i] for i in sorted(g)] for g in groups if len(g) > 1]
    return [[names[i] for i in sorted(g)] for g in groups], resolution


def derived_cast(
    segments: list[dict[str, Any]],
    *,
    api_key: str | None = None,
    model: str = "jev-latest",
) -> dict[str, Any]:
    """Build a candidate character set from the dialogue alone.

    Transcription runs before any cast exists, so attribution cannot wait for
    one. Candidates are the names people introduce themselves by and the
    kinship roles the dialogue addresses. Whether two candidates are one person
    depends on the story, so that is asked of JEV and merged only on a
    confident answer; the result says how identity was resolved.
    """
    roles: dict[str, dict[str, Any]] = {}
    text_all = "\n".join(str(s.get("text") or "") for s in segments)
    for name in _self_introduced_names(text_all):
        roles[name] = {
            "description": f"台词中自报姓名为「{name}」的人",
            "terms": [name],
            "evidence": _evidence(segments, [name]),
            "mentions": _mentions(segments, [name]),
            "named": True,
        }
    for role, terms in _address_roles(text_all).items():
        if role in roles:
            continue
        roles[role] = {
            "description": ROLE_DESCRIPTIONS.get(role, role),
            "terms": terms,
            "evidence": _evidence(segments, terms),
            "mentions": _mentions(segments, terms),
        }

    groups, resolution = _same_person_groups(roles, segments, api_key=api_key, model=model)

    characters: list[dict[str, Any]] = []
    for index, members in enumerate(groups, 1):
        named = [m for m in members if roles[m].get("named")]
        aliases: list[str] = []
        for member in members:
            for alias in (member, *roles[member]["terms"]):
                if alias not in aliases:
                    aliases.append(alias)
        characters.append(
            {
                "id": f"R{index:02d}",
                "name": named[0] if named else "/".join(members),
                "aliases": aliases,
                "persona": {"identity": "；".join(roles[m]["description"] for m in members)},
            }
        )
    characters.append(
        {
            "id": "R90",
            "name": "群体",
            "persona": {
                "gender": "群体",
                "identity": (
                    "台词由多人齐声念白，整齐划一像喊口号；不是旁白，也不是某一个人的内心话"
                ),
            },
        }
    )
    characters.append(
        {
            "id": "R91",
            "name": "旁白",
            "persona": {
                "identity": "不属于任何角色的画外叙述者；某个角色自己的内心独白应归给该角色本人"
            },
        }
    )
    return {"characters": characters, "identity_resolution": resolution}


def attribute_speakers(
    segments,
    cast=None,
    *,
    scenes=None,
    api_key=None,
    model="jev-latest",
    shot_notes=None,
    person_map=None,
    context_rows=None,
    vision_model=None,
):
    """Merge existing visual/context evidence; never request a textual attribution."""
    del scenes
    try:
        from .speaker_evidence import attribute
    except ImportError:
        from speaker_evidence import attribute
    result = attribute(
        segments,
        cast,
        shot_notes or {},
        person_map,
        api_key=api_key,
        model=model,
        context_rows=context_rows,
        vision_model=vision_model,
    )
    if (cast or {}).get("identity_resolution"):
        result["identity_resolution"] = cast["identity_resolution"]
    return result
