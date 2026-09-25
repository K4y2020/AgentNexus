"""Attribute ASR utterances to characters, and decide how each is delivered.

The ASR transcript labels speakers unreliably — one segment can mash several
people together under a single label, and the same character appears under
different labels in different lines. Downstream that becomes a script that looks
like it misread the characters: a line is given to the person it was addressed
to, and the line that made an exchange land disappears.

The cast document already identifies the characters (names, aliases, persona,
relationships). What was missing is a step that uses those features to decide
*who is speaking each line*, and whether it is spoken aloud at all.

JEV can do this, but only if the questions are shaped correctly. Findings from
probing, all of which this module encodes:

1. Judging each line alone does poorly. Supplying the surrounding exchange and
   asking "who does this line answer" lifts it, because reply structure is
   evidence.
2. A bare list of option labels fails on address terms: a line that calls
   someone by name or kinship term gets attributed to the person addressed
   rather than the person speaking. JEV can tell who a line is directed at; it
   simply cannot express "not that one" in a label. The fix is to write the
   exclusion into the addressee's option description. Which option is the
   addressee is itself a semantic judgment, so it is asked of JEV first; a
   direct vocative of a cast name or alias is also detected deterministically.
3. Unison chorus lines are otherwise attributed to one person's inner
   monologue, so a group character's option states what a chorus line is.
4. Delivery mode (spoken vs voice-over) is judged in the same request as the
   speaker. A voice-over has no addressee and gets no reply, so treating one as
   dialogue stages a conversation the character is not having.

Nothing here knows any particular story. Characters, aliases and relationships
come from the cast document; when there is none, candidate roles come from the
transcript's own self-introductions and kinship address terms, and only a
confident JEV judgment may decide that two of those roles are one person.

Known limit: these questions share one state, so a line that contradicts the
episode's overall tendency scores lower than it would alone. That is what
`needs_review` is for — do not read a flagged line as resolved.
"""

from __future__ import annotations

import json
import math
import re
import sys
from collections import Counter
from itertools import combinations
from pathlib import Path
from typing import Any

_PIPELINE = Path(__file__).resolve().parent
if str(_PIPELINE) not in sys.path:
    sys.path.insert(0, str(_PIPELINE))

from jev_gates import _find_api_key  # noqa: E402
from jev_transport import open_client  # noqa: E402


UNATTRIBUTED = "unclear"
NO_ADDRESSEE = "none"
MIN_CONFIDENCE = 0.55
# Two candidates this close are an ambiguity, not an answer.
MIN_MARGIN = 0.15
# An addressee judgment becomes an exclusion on the speaker question only when
# JEV is sure of it; a wrong exclusion would push the real speaker out.
ADDRESSEE_CONFIDENCE = 0.75
# Merging two derived roles into one person needs a clear yes. The band below
# it is reported for review and never merged.
MERGE_CONFIDENCE = 0.7
MERGE_REVIEW = 0.4
# Silence longer than this separates exchanges. Without it every line in the
# episode becomes one block, and each judgment is diluted by neighbours that
# are not part of the same exchange.
_SCENE_GAP_SECONDS = 1.5
_MAX_STATE_LINES = 150
# "Same person?" is asked for every pair of compared roles, so the number of
# questions grows quadratically. 12 roles is at most 66 questions per call.
_MAX_MERGE_ROLES = 12
_MAX_EVIDENCE_CHARS = 80

# Whether a line is spoken aloud or is voice-over changes what can be staged:
# a voice-over has no addressee, gets no reply, and cannot carry a reaction.
DELIVERY_MODES = {
    "spoken": (
        "说话人当场说出口，场内有角色能听见并回应。有明确的对象，"
        "或直接回答上一句；伴随可见的肢体动作。"
    ),
    "voice_over": (
        "说话人的内心独白或旁白，不是说给场内任何人听的。没有角色对此作出反应；"
        "它从情境之外评论当下，或直接对观众说话——如自报身世、心里吐槽、暗自发怵。"
    ),
    UNATTRIBUTED: "无法从给定信息判定。",
}

GROUP_GENDERS = {"群体", "群像"}
GROUP_CONDITION = (
    "若此句为多人齐声、语气整齐划一如喊口号，则选此项；"
    "这是群体应和，不是某一个人的心声，也不是旁白。"
)


def _scene_groups(segments: list[dict[str, Any]]) -> list[list[tuple[int, dict[str, Any]]]]:
    """Split segments into exchanges, honouring an explicit scene when present.

    Without an explicit scene the split follows the silences: treating the whole
    episode as one block dilutes each judgment with neighbours that belong to a
    different exchange.
    """
    groups: list[list[tuple[int, dict[str, Any]]]] = []
    previous: dict[str, Any] | None = None
    for position, segment in enumerate(segments):
        explicit = segment.get("scene")
        if explicit is not None:
            if not groups or groups[-1][0][1].get("scene") != explicit:
                groups.append([])
        else:
            gap = (
                float(segment.get("start", 0.0)) - float(previous.get("end", 0.0))
                if previous is not None
                else 0.0
            )
            if not groups or gap > _SCENE_GAP_SECONDS:
                groups.append([])
        groups[-1].append((position, segment))
        previous = segment
    return groups


# ---------------------------------------------------------------------------
# Cast without a cast document: candidate roles from the transcript itself
# ---------------------------------------------------------------------------

# Kinship and marital address terms of the language, mapped to the role each
# names. These are language facts ("妈" names a mother), not story facts: the
# table never says which two roles are the same person. Single characters that
# also occur inside unrelated words (娘 in 姑娘, 新娘) are left out.
ADDRESS_TERMS = {
    "妈": "母亲", "妈妈": "母亲", "老妈": "母亲",
    "爸": "父亲", "爸爸": "父亲", "老爸": "父亲", "爹": "父亲",
    "老婆": "妻子", "老公": "丈夫",
    "外婆": "外婆", "姥姥": "外婆", "奶奶": "祖母",
    "外公": "外公", "姥爷": "外公", "爷爷": "祖父",
    "姐姐": "姐姐", "妹妹": "妹妹", "哥哥": "哥哥", "弟弟": "弟弟",
    "嫂子": "嫂子", "表妹": "表妹", "表姐": "表姐", "表哥": "表哥", "表弟": "表弟",
    "外孙女": "外孙女", "孙女": "孙女", "孙子": "孙子",
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
    ranked = sorted(
        range(len(names)), key=lambda i: (-int(roles[names[i]].get("mentions", 0)), i)
    )
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

    dialogue = [
        f"[{i + 1}] {s.get('text', '')}" for i, s in enumerate(segments) if s.get("text")
    ][:_MAX_STATE_LINES]
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
            response = client.system_one(
                state={"dialogue": dialogue}, questions=questions, model=model
            )
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
    characters.append({
        "id": "R90", "name": "群体",
        "persona": {"gender": "群体",
                    "identity": "台词由多人齐声念白，整齐划一像喊口号；不是旁白，也不是某一个人的内心话"},
    })
    characters.append({
        "id": "R91", "name": "旁白",
        "persona": {
            "identity": "不属于任何角色的画外叙述者；某个角色自己的内心独白应归给该角色本人"
        },
    })
    return {"characters": characters, "identity_resolution": resolution}


# ---------------------------------------------------------------------------
# Roster and per-line option conditions
# ---------------------------------------------------------------------------


def _character_key(character: dict[str, Any]) -> str:
    """Cast id, or the name for a cast document whose cards carry no id."""
    return str(character.get("id") or character.get("name") or "").strip()


def _is_group(character: dict[str, Any]) -> bool:
    persona = character.get("persona") or {}
    return (
        str(persona.get("gender") or "") in GROUP_GENDERS
        or "群体" in str(character.get("name") or "")
        or character.get("group") is True
    )


def _name_for(cast: dict[str, Any], speaker_id: str) -> str:
    """Map a cast key back to its display name, falling back to the key."""
    for character in cast.get("characters") or []:
        if _character_key(character) == speaker_id:
            return str(character.get("name") or speaker_id)
    return speaker_id


def _roster(cast: dict[str, Any]) -> dict[str, str]:
    """Character features from the cast document, keyed by cast id (or name)."""
    roster: dict[str, str] = {}
    for character in cast.get("characters") or []:
        cid = _character_key(character)
        if not cid:
            continue
        persona = character.get("persona") or {}
        parts = [str(character.get("name") or cid)]
        if character.get("aliases"):
            parts.append("别名 " + "、".join(str(a) for a in character["aliases"]))
        if character.get("oneLiner"):
            parts.append(str(character["oneLiner"]))
        for field in ("gender", "ageRange", "identity", "appearance", "temperament"):
            if persona.get(field):
                parts.append(str(persona[field]))
        if persona.get("personality"):
            parts.append("性格：" + "、".join(str(p) for p in persona["personality"]))
        relations = [
            f"{r.get('name')}——{r.get('relation')}"
            for r in persona.get("relationships") or []
            if isinstance(r, dict) and r.get("name") and r.get("relation")
        ]
        if relations:
            parts.append("关系：" + "；".join(relations))
        roster[cid] = "｜".join(parts)
    roster[UNATTRIBUTED] = "无法从给定信息判定说话人"
    return roster


def _address_terms(cast: dict[str, Any]) -> dict[str, list[str]]:
    """Every word the cast document says each character is called by."""
    terms: dict[str, list[str]] = {}
    owners: dict[str, set[str]] = {}
    for character in cast.get("characters") or []:
        cid = _character_key(character)
        if not cid:
            continue
        words = [character.get("name"), *(character.get("aliases") or [])]
        for word in words:
            if isinstance(word, str) and word.strip():
                owners.setdefault(word.strip(), set()).add(cid)
                terms.setdefault(cid, []).append(word.strip())
    # A word shared by two characters does not identify either of them.
    return {
        cid: [w for w in words if len(owners[w]) == 1]
        for cid, words in terms.items()
    }


_CLAUSE_SPLIT = re.compile(r"[，,。！？!?；;：:…～~\s]+")
_PARTICLES = "啊呀哎喂嘿哟呢吧嘛哈"


def _vocatives(text: str, terms: dict[str, list[str]]) -> dict[str, str]:
    """Characters the line calls directly by name, alias or title.

    Only a whole clause counts (「妈，…」「喂，<名字>！」): a word inside a clause
    may be a mention or a self-introduction, and those do not exclude anyone.
    """
    parts = [c for c in _CLAUSE_SPLIT.split(text) if c]
    clauses = {*parts, *(c.strip(_PARTICLES) for c in parts)}
    found = {}
    for cid, words in terms.items():
        for word in words:
            if word in clauses:
                found[cid] = word
                break
    return found


def _conditions(
    segment: dict[str, Any],
    roster: dict[str, str],
    *,
    terms: dict[str, list[str]] | None = None,
    addressee: tuple[str, float] | None = None,
    groups: set[str] | None = None,
) -> dict[str, str]:
    """Augment roster entries with per-option conditions for this utterance.

    A candidate's distinguishing condition is what makes attribution work: the
    model needs to know what choosing this option would *mean* for this line,
    not only who the character is. Every condition here is derived from the
    cast document or from JEV's own addressee judgment, never from the wording
    of a particular story.
    """
    text = str(segment.get("text") or "")
    conditions = dict(roster)

    # An address term names the person spoken TO. Without saying so, the model
    # attributes the line to that person.
    for cid, word in _vocatives(text, terms or {}).items():
        if cid in roster:
            conditions[cid] = (
                f"{roster[cid]}｜本句直接以「{word}」称呼此人：此人是听者；"
                "除非是自言自语，没有人会这样称呼自己，所以通常不是说话人。"
            )
    if addressee is not None:
        cid, confidence = addressee
        if cid in roster and conditions[cid] == roster[cid]:
            conditions[cid] = (
                f"{roster[cid]}｜本句是对此人说的（听者判定置信度 {confidence:.2f}）；"
                "说话人通常不是听者本人。"
            )

    # Group characters are only ever chosen by mistake unless the unison nature
    # of their lines is stated, since their text otherwise reads as ordinary
    # speech and loses to an introspective protagonist.
    for cid in groups or ():
        if cid in conditions:
            conditions[cid] = f"{conditions[cid]}｜{GROUP_CONDITION}"

    if segment.get("conditions"):
        for cid, extra in segment["conditions"].items():
            conditions[cid] = f"{conditions.get(cid, roster.get(cid, cid))}｜{extra}"
    return conditions


# ---------------------------------------------------------------------------
# Attribution
# ---------------------------------------------------------------------------


def _unit_interval(value: Any) -> float | None:
    """``value`` as a probability, or None unless it is a finite number in [0, 1].

    NaN compares false with everything, so ``nan < MIN_CONFIDENCE`` would let a
    broken answer pass as settled; infinity and out-of-range numbers are no more
    calibrated. Booleans and strings are not numbers here either.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) and 0.0 <= number <= 1.0 else None


def _choice(answer: Any, allowed: set[str]) -> tuple[str, float, dict[str, float], str | None]:
    """Read a Choice answer; an option outside ``allowed`` is not an answer.

    A confidence or probability that is not a finite number in [0, 1] makes the
    whole answer unresolved (``malformed_answer``) rather than being clamped.
    """
    if answer is None:
        return UNATTRIBUTED, 0.0, {}, "missing_answer"
    raw = str(getattr(answer, "choice", UNATTRIBUTED))
    reported_confidence = getattr(answer, "confidence", None)
    confidence = 0.0 if reported_confidence is None else _unit_interval(reported_confidence)
    reported = getattr(answer, "probabilities", None)
    malformed = confidence is None or not (reported is None or isinstance(reported, dict))
    probabilities: dict[str, float] = {}
    for option, value in reported.items() if isinstance(reported, dict) else ():
        probability = _unit_interval(value)
        if probability is None:
            malformed = True
        else:
            probabilities[str(option)] = probability
    if malformed:
        return UNATTRIBUTED, 0.0, probabilities, "malformed_answer"
    if raw not in allowed:
        return UNATTRIBUTED, 0.0, probabilities, f"unknown_option:{raw}"
    return raw, confidence, probabilities, None


def _ambiguous(choice: str, probabilities: dict[str, float]) -> bool:
    """True when the runner-up option is nearly as likely as the choice."""
    if choice not in probabilities:
        return False
    others = [p for k, p in probabilities.items() if k != choice and k != UNATTRIBUTED]
    return bool(others) and probabilities[choice] - max(others) < MIN_MARGIN


_QUESTION_SUFFIXES = ("__addressee", "__delivery")


def _question_keys(segments: list[dict[str, Any]]) -> list[tuple[str, str]]:
    """(question key, original id) for each segment, by position.

    The key is the segment's own id when that is unique. A repeated id (or one
    that collides with another line's derived keys) gets a suffixed key, so one
    line's question and answer can never overwrite another's.
    """
    taken: set[str] = set()
    keys = []
    for position, segment in enumerate(segments):
        original = str(segment.get("id") or f"seg{position:03d}")
        key, n = original, 2
        while any(k in taken for k in (key, *(key + s for s in _QUESTION_SUFFIXES))):
            key, n = f"{original}#{n}", n + 1
        taken.update((key, *(key + s for s in _QUESTION_SUFFIXES)))
        keys.append((key, original))
    return keys


def _exchange_text(group: list[tuple[int, dict[str, Any]]], offset: int) -> str:
    return "\n".join(
        f"  [{i + 1}]{'  ← determine this line' if i == offset else ''} 「{s.get('text', '')}」"
        for i, (_, s) in enumerate(group)
    )


def attribute_speakers(
    segments: list[dict[str, Any]],
    cast: dict[str, Any] | None = None,
    *,
    scenes: dict[int, str] | None = None,
    api_key: str | None = None,
    model: str = "jev-latest",
) -> dict[str, Any]:
    """Return per-segment speaker attributions with calibrated confidence.

    ``segments`` need ``text`` and may carry ``scene`` (a scene key) and
    ``conditions`` (per-cast-id hints for the disambiguation clauses).
    If ``cast`` is not provided, candidate roles are derived from the dialogue
    (see :func:`derived_cast`).

    Two JEV requests are made: the first asks who each line is addressed to,
    the second asks who speaks it and how, with the confident addressee written
    into its option as an exclusion.
    """
    from typesafe_sdk import Choice

    if cast is None or not cast.get("characters"):
        cast = derived_cast(segments, api_key=api_key, model=model)

    roster = _roster(cast)
    terms = _address_terms(cast)
    groups = {_character_key(c) for c in cast.get("characters") or [] if _is_group(c)}
    speakers = set(roster)
    listeners = {
        **{cid: text for cid, text in roster.items() if cid != UNATTRIBUTED},
        NO_ADDRESSEE: "本句没有明确的听者，或听者不在名单中（如独白、旁白、对不在场的人说话）",
        UNATTRIBUTED: "无法从给定信息判定听者",
    }
    scenes = scenes or {}
    state: dict[str, Any] = {"characters": roster, "scenes": []}
    index: dict[str, tuple[dict[str, Any], str, str]] = {}
    keys = _question_keys(segments)
    counts = Counter(original for _key, original in keys)

    for scene_index, group in enumerate(_scene_groups(segments)):
        scene_key = group[0][1].get("scene", scene_index)
        setup = scenes.get(scene_key, "")
        state["scenes"].append(
            {
                "scene": scene_key,
                "setup": setup,
                "sequence": [{"speaker": "?", "text": s.get("text", "")} for _, s in group],
            }
        )
        for offset, (position, segment) in enumerate(group):
            qid = keys[position][0]
            index[qid] = (segment, setup, _exchange_text(group, offset))
    original_ids = dict(keys)

    if not index:
        return {"model": model, "attributions": []}

    key = (api_key or _find_api_key()).strip()
    if not key:
        return {"error": "TYPESAFE_API_KEY is not configured"}

    def preamble(setup: str, exchange: str) -> str:
        return (f"Scene: {setup}\n\n" if setup else "") + f"The exchange runs:\n{exchange}\n\n"

    addressee_questions = {
        f"{qid}__addressee": Choice(
            instructions=(
                preamble(setup, exchange)
                + "Who is the line marked ← addressed to — the person it calls by name, "
                "kinship term or title, or whose line it directly answers? Pick the "
                "listener, not the speaker."
            ),
            criteria=listeners,
        )
        for qid, (_segment, setup, exchange) in index.items()
    }

    with open_client(key) as client:
        first = client.system_one(state=state, questions=addressee_questions, model=model)
        # A missing or malformed addressee answer only means no exclusion.
        first_answers = getattr(first, "answers", None) or {}
        addressees: dict[str, tuple[str, float]] = {}
        for qid in index:
            choice, confidence, _p, problem = _choice(
                first_answers.get(f"{qid}__addressee"), set(listeners)
            )
            if (
                problem is None
                and choice in roster
                and choice != UNATTRIBUTED
                and confidence >= ADDRESSEE_CONFIDENCE
            ):
                addressees[qid] = (choice, confidence)

        questions: dict[str, Any] = {}
        for qid, (segment, setup, exchange) in index.items():
            questions[qid] = Choice(
                instructions=(
                    preamble(setup, exchange)
                    + "Who speaks the line marked ← ? Decide by what the line replies to, "
                    "who it addresses, and each option's stated condition. The speaker is "
                    "not always the person being addressed."
                ),
                criteria=_conditions(
                    segment, roster, terms=terms, addressee=addressees.get(qid), groups=groups
                ),
            )
            # Asked in the same request: delivery is independent of speaker, and
            # batching costs no extra latency.
            questions[f"{qid}__delivery"] = Choice(
                instructions=(
                    preamble(setup, exchange)
                    + "For the line marked ←, is it spoken aloud to someone in the scene, "
                    "or is it voice-over / inner monologue? Whether anyone reacts to it "
                    "is strong evidence."
                ),
                criteria=DELIVERY_MODES,
            )
        response = client.system_one(state=state, questions=questions, model=model)

    # Every input line gets an attribution. A missing answer is reported as an
    # unresolved line, not dropped: dropping it would silently shorten the
    # transcript the script is later checked against.
    answers = getattr(response, "answers", None) or {}
    attributions = []
    for qid, (segment, _setup, _exchange) in index.items():
        choice, confidence, probabilities, problem = _choice(answers.get(qid), speakers)
        delivery, delivery_confidence, _dp, delivery_problem = _choice(
            answers.get(f"{qid}__delivery"), set(DELIVERY_MODES)
        )

        speaker_reasons = [f"speaker_{problem}"] if problem else []
        if choice == UNATTRIBUTED:
            speaker_reasons.append("speaker_unclear")
        elif confidence < MIN_CONFIDENCE:
            speaker_reasons.append("speaker_low_confidence")
        elif _ambiguous(choice, probabilities):
            speaker_reasons.append("speaker_ambiguous")
        addressee = addressees.get(qid)
        if addressee is not None and addressee[0] == choice:
            # The line was judged to be said *to* this character; crediting it
            # to them as well is a contradiction, not an answer.
            speaker_reasons.append("speaker_is_addressee")
        delivery_reasons = [f"delivery_{delivery_problem}"] if delivery_problem else []
        if delivery == UNATTRIBUTED or delivery_confidence < MIN_CONFIDENCE:
            delivery_reasons.append("delivery_uncertain")
        speaker_review = bool(speaker_reasons)
        delivery_review = bool(delivery_reasons)
        reasons = [*speaker_reasons, *delivery_reasons]

        original_id = original_ids[qid]
        attributions.append(
            {
                "id": original_id,
                "question_id": qid,
                # Two input lines share this id; ``question_id`` tells them apart.
                **({"duplicate_id": True} if counts[original_id] > 1 else {}),
                "text": segment.get("text", ""),
                "speaker": choice,
                # A derived roster uses opaque ids, so carry the display name too —
                # downstream consumers need something readable to write into a script.
                "speaker_name": _name_for(cast, choice),
                "confidence": confidence,
                "delivery": delivery,
                "delivery_confidence": delivery_confidence,
                "addressee": addressee[0] if addressee else None,
                "speaker_needs_review": speaker_review,
                "delivery_needs_review": delivery_review,
                "needs_review": speaker_review or delivery_review,
                "review_reasons": reasons,
                "probabilities": probabilities,
            }
        )
    result: dict[str, Any] = {
        "model": str(getattr(response, "model", model)),
        "attributions": attributions,
    }
    if cast.get("identity_resolution"):
        result["identity_resolution"] = cast["identity_resolution"]
    return result


def main(argv: list[str] | None = None) -> int:
    import argparse

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--transcript", type=Path, required=True)
    parser.add_argument("--cast", type=Path, required=True)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--model", default="jev-latest")
    args = parser.parse_args(argv)

    rows = json.loads(args.transcript.read_text(encoding="utf-8-sig"))
    segments = rows.get("segments") if isinstance(rows, dict) else rows
    scenes = rows.get("scenes") if isinstance(rows, dict) else None
    cast = json.loads(args.cast.read_text(encoding="utf-8-sig"))
    result = attribute_speakers(segments, cast, scenes=scenes, model=args.model)
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.out:
        args.out.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
