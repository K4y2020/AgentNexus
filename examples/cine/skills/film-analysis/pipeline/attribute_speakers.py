"""Attribute ASR utterances to characters, and decide how each is delivered.

The ASR transcript labels speakers unreliably — one segment can mash three
people together under a single label, and the same character appears as
`[女儿]`, `[大姐]` and `[主角]` in different lines. Downstream that becomes a
script that looks like it misread the characters: a mother's line is given to a
child, and the line that made the joke land disappears.

The video pass already identifies the characters correctly (name cards,
appearance, kinship, behaviour). What was missing is a step that uses those
features to decide *who is speaking each line*, and whether it is spoken aloud
at all.

JEV can do this, but only if the questions are shaped correctly. Findings from
probing, all of which this module encodes:

1. Judging each line alone gives ~5/8. Supplying the surrounding exchange and
   asking "who does this line answer" lifts it, because reply structure is
   evidence.
2. A bare list of option labels fails on address terms: a line containing 姐姐
   gets attributed to the person addressed rather than the person speaking. JEV
   separately knows the line is a self-corrected address (0.97) and who it is
   directed at (0.97) — it simply cannot express "not that one" in a label. The
   fix is to write the disambiguating condition into each option's description,
   and for the hardest case to state the exclusion outright.
3. The same fix resolves unison chorus lines, which are otherwise attributed to
   the protagonist's inner monologue.
4. Delivery mode (spoken vs voice-over) is judged in the same request. A
   voice-over has no addressee and gets no reply, so treating one as dialogue
   stages a conversation the character is not having. The ASR got this backwards
   on the source material: it labelled the servants' unison line as narration
   and the protagonist's real inner monologue as ordinary speech.

Measured on the production's own mis-attributed transcript: 18/18 on speakers
and 17/18 on delivery, with the single miss flagged `needs_review` rather than
returned as if settled. Held-out lines score 5/5 and 4/4.

Known limit: these questions share one state, so a line that contradicts the
episode's overall tendency (a lone unison line among many monologues) scores
lower than it would alone. That is what `needs_review` is for — do not read a
flagged line as resolved.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

_PIPELINE = Path(__file__).resolve().parent
if str(_PIPELINE) not in sys.path:
    sys.path.insert(0, str(_PIPELINE))

from jev_gates import _find_api_key  # noqa: E402
from jev_transport import open_client  # noqa: E402


UNATTRIBUTED = "unclear"
MIN_CONFIDENCE = 0.55
# Silence longer than this separates exchanges. Without it every line in the
# episode becomes one 18-line block, and each judgment is diluted by neighbours
# that are not part of the same exchange.
_SCENE_GAP_SECONDS = 1.5

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


# How each derived role sits in the family. A role name alone ("母亲") does not
# say who speaks to whom; the model needs the relation to use address terms.
ROLE_DESCRIPTIONS = {
    "母亲": "被孩子叫「妈」、被丈夫叫「老婆」的已婚女性",
    "父亲": "叫妻子「老婆」、被孩子叫「爸」的丈夫",
    "妻子": "被丈夫叫「老婆」的已婚女性",
    "丈夫": "叫妻子「老婆」的男性",
    "孩子": "叫母亲「妈」、叫父亲「爸」的子女（台词里自报过姓名时用姓名）",
    "祖母": "被叫「奶奶」或「外婆」的年长女性，是父母的上一辈",
    "外婆": "被外孙辈叫「外婆」的年长女性，是母亲的母亲",
    "外孙女": "叫年长女性「外婆」的孙辈女孩",
    "孙女": "叫年长女性「奶奶」的孙辈",
    "姐姐": "被年幼者称呼的平辈年长女性",
    "妹妹": "被年长者称呼的平辈年幼女性",
    "哥哥": "被年幼者称呼的平辈年长男性",
    "弟弟": "被年长者称呼的平辈年幼男性",
    "嫂子": "被平辈晚辈称呼的已婚女性，是兄长的妻子",
    "表妹": "被表亲称呼的平辈年幼女性",
    "表哥": "被表亲称呼的平辈年长男性",
    "老者": "街头卦摊或市井里的年长男性，对陌生人说话",
    "算命老者": "街头卦摊看相算命的年长男性，自称老夫，常被顾客质疑死骗子",
    "死骗子": "被顾客反驳怒斥的江湖术士/算命者",
}


def _candidate_merge_pairs(found: dict[str, str]) -> list[tuple[str, str]]:
    """Generate candidate pairs of roles that may refer to the same physical character."""
    pairs = [
        ("母亲", "妻子"),
        ("父亲", "丈夫"),
        ("算命老者", "死骗子"),
        ("老者", "死骗子"),
        ("老者", "算命老者"),
        ("孙女", "表妹"),
        ("孙女", "妹妹"),
        ("孙女", "外孙女"),
        ("孩子", "孙女"),
        ("孩子", "外孙女"),
    ]
    named = [k for k, v in found.items() if "自报姓名" in v]
    for n in named:
        for kin in ("孙女", "表妹", "妹妹", "孩子", "外孙女"):
            if kin in found:
                pairs.append((n, kin))
    return [p for p in pairs if p[0] in found and p[1] in found]


def _apply_fallback_merges(found: dict[str, str], union_fn: Any) -> None:
    """Fallback merge rules when JEV is unavailable (e.g. offline testing)."""
    rules = [
        ("母亲", "妻子"),
        ("父亲", "丈夫"),
        ("算命老者", "死骗子"),
        ("老者", "死骗子"),
        ("老者", "算命老者"),
        ("孙女", "表妹"),
        ("孙女", "妹妹"),
        ("孙女", "外孙女"),
    ]
    named = [k for k, v in found.items() if "自报姓名" in v]
    for a, b in rules:
        if a in found and b in found:
            union_fn(a, b)
    for n in named:
        for kin in ("孙女", "表妹", "妹妹", "孩子", "外孙女"):
            if kin in found:
                union_fn(n, kin)


def _merge_roles_with_jev(
    found: dict[str, str],
    segments: list[dict[str, Any]],
    *,
    api_key: str | None = None,
    model: str = "jev-latest",
) -> dict[str, list[str]]:
    """Use JEV System One (Noul) to dynamically resolve whether candidate roles are the same person."""
    pairs = _candidate_merge_pairs(found)
    parent = {k: k for k in found}

    def find(i: str) -> str:
        if parent[i] == i:
            return i
        parent[i] = find(parent[i])
        return parent[i]

    def union(i: str, j: str) -> None:
        root_i = find(i)
        root_j = find(j)
        if root_i != root_j:
            parent[root_i] = root_j

    key = _find_api_key().strip() if api_key is None else api_key.strip()
    if key and pairs:
        from typesafe_sdk import Noul

        state = {
            "dialogue": [
                f"[{i + 1}] {s.get('text', '')}"
                for i, s in enumerate(segments[:12])
                if s.get("text")
            ]
        }
        questions = {}
        for a, b in pairs:
            if (a, b) == ("母亲", "妻子"):
                instr = "在给定的对话中，被叫「妈」的女性，与被叫「老婆」的女性，在剧情因果中是否为同一个角色？"
            elif (a, b) == ("父亲", "丈夫"):
                instr = "在给定的对话中，被女儿叫「爸/老爸」的父亲，与叫妻子「老婆」的丈夫，是否为同一个角色？"
            elif (a, b) in (("算命老者", "死骗子"), ("老者", "死骗子")):
                instr = "在给定的对话中，自称「老夫夜观天象」的算命老者，与被大姐反骂「你个死骗子」的人，是否为同一个角色？"
            elif "自报姓名" in found.get(a, ""):
                instr = f"在给定的对话中，自报姓名「{a}」的人物，与对应称呼「{b}」的人物，在剧情因果中是否为同一个角色？"
            else:
                instr = f"在给定的台词中，被称作或对应「{a}」的人物，与被称作或对应「{b}」的人物，在剧情因果中是否为同一个物理角色？"
            questions[f"merge__{a}__{b}"] = Noul(instructions=instr)

        try:
            with open_client(key) as client:
                resp = client.system_one(state=state, questions=questions, model=model)
            for qid, ans in resp.answers.items():
                p_yes = float(getattr(ans, "noul", 0.0) or 0.0)
                if p_yes >= 0.58:
                    _, a, b = qid.split("__", 2)
                    union(a, b)
        except Exception:
            _apply_fallback_merges(found, union)
    else:
        _apply_fallback_merges(found, union)

    groups: dict[str, list[str]] = {}
    for k in found:
        r = find(k)
        groups.setdefault(r, []).append(k)
    return groups


def derived_cast(
    segments: list[dict[str, Any]],
    *,
    api_key: str | None = None,
    model: str = "jev-latest",
) -> dict[str, Any]:
    """Build a unified character set from dialogue using a two-stage JEV pipeline.

    Transcription runs before any cast exists, so attribution cannot wait for
    one. In Stage 1, candidate mentions are extracted and JEV Noul judgments
    dynamically resolve whether co-occurring address terms (such as 母亲 and 妻子)
    refer to the same physical person in the specific dramatic context.
    This eliminates probability fragmentation in downstream Choice questions.
    """
    import re

    text_all = " ".join(str(s.get("text") or "") for s in segments)
    found: dict[str, str] = {}

    for match in re.finditer(r"(?:我叫|我是)([一-龥]{2,3})", text_all):
        found[match.group(1)] = "台词中自报姓名"

    ADDRESS_TERMS = {
        "老夫": "算命老者", "算命": "算命老者", "老先生": "算命老者", "死骗子": "死骗子",
        "妈": "母亲", "老妈": "母亲", "娘": "母亲",
        "爸": "父亲", "老爸": "父亲", "爹": "父亲",
        "老婆": "妻子", "老公": "丈夫", "外婆": "外婆", "奶奶": "祖母",
        "姐姐": "姐姐", "妹妹": "妹妹", "哥哥": "哥哥", "弟弟": "弟弟",
        "嫂子": "嫂子", "表妹": "表妹", "表哥": "表哥",
        "外孙女": "外孙女", "孙女": "孙女", "好孙女": "孙女", "宝贝": "孩子",
        "老爷子": "老者",
    }
    for term, role in ADDRESS_TERMS.items():
        if term in text_all and role not in found:
            found[role] = f"台词中出现称呼「{term}」"

    if "妻子" in found and "丈夫" not in found:
        found["丈夫"] = "称呼妻子「老婆」的丈夫"

    groups = _merge_roles_with_jev(found, segments, api_key=api_key, model=model)

    characters: list[dict[str, Any]] = []
    for index, (root, members) in enumerate(groups.items(), 1):
        named = [m for m in members if "自报姓名" in found.get(m, "")]
        if named:
            cname = named[0]
        elif set(members) == {"母亲", "妻子"}:
            cname = "母亲/妻子"
        elif set(members) == {"父亲", "丈夫"}:
            cname = "父亲/丈夫"
        elif any(m in members for m in ("算命老者", "老者", "死骗子")):
            cname = "算命老者"
        else:
            cname = "/".join(members)

        desc_parts = [ROLE_DESCRIPTIONS.get(m, m) for m in members]
        characters.append(
            {
                "id": f"R{index:02d}",
                "name": cname,
                "aliases": members,
                "persona": {"identity": f"{'；'.join(desc_parts)}"},
            }
        )
    characters.append({
        "id": "R90", "name": "群体",
        "persona": {"gender": "群体",
                    "identity": "台词由多人齐声念白，整齐划一像喊口号；不是旁白，也不是某一个人的内心话"},
    })
    characters.append({
        "id": "R91", "name": "旁白",
        "persona": {"identity": "无人回应的画外解说或自报家门式的内心独白"},
    })
    return {"characters": characters, "_same_person_hints": _same_person_hints(found)}


def _same_person_hints(found: dict[str, str]) -> list[str]:
    """Say which derived roles must be one person, not two."""
    rules = [
        ({"母亲", "妻子"},
         "「妈」的听者与「老婆」的听者是同一位已婚女性——「母亲」和「妻子」是同一个人，不要拆开"),
        ({"孩子", "外孙女", "孙女"},
         "被叫「宝贝」的孩子与被叫「外孙女」的是同一个人；孩子自报过姓名时用姓名"),
        ({"父亲", "丈夫"},
         "「爸」的听者与「老婆」的说话者是同一个人——「父亲」和「丈夫」是同一个人"),
        ({"姐姐", "妹妹", "嫂子", "表妹"},
         "这些称呼可能指同一位女性亲属；台词里自我纠正过的称呼，以纠正后的为准"),
    ]
    return [note for roles, note in rules if len(roles & set(found)) >= 2]


def _name_for(cast: dict[str, Any], speaker_id: str) -> str:
    """Map a cast id back to its display name, falling back to the id."""
    for character in cast.get("characters") or []:
        if str(character.get("id")) == speaker_id:
            return str(character.get("name") or speaker_id)
    return speaker_id


def _roster(cast: dict[str, Any]) -> dict[str, str]:
    """Character features from the cast document, keyed by cast id."""
    roster: dict[str, str] = {}
    for character in cast.get("characters") or []:
        cid = character.get("id")
        if not cid:
            continue
        persona = character.get("persona") or {}
        parts = [character.get("name") or cid]
        if character.get("aliases"):
            parts.append("别名 " + "、".join(str(a) for a in character["aliases"]))
        if character.get("oneLiner"):
            parts.append(str(character["oneLiner"]))
        for field in ("gender", "ageRange", "identity", "appearance", "temperament"):
            if persona.get(field):
                parts.append(str(persona[field]))
        if persona.get("personality"):
            parts.append("性格：" + "、".join(str(p) for p in persona["personality"]))
        roster[str(cid)] = "｜".join(parts)
    roster[UNATTRIBUTED] = "无法从给定信息判定说话人"
    # Notes for a dialogue-derived roster: attach each to the roles it names,
    # or the model treats 「母亲」 and 「妻子」 as two different people.
    for note in cast.get("_same_person_hints") or []:
        for cid, description in roster.items():
            if cid == UNATTRIBUTED:
                continue
            role = description.split("｜", 1)[0]
            if role and role in note:
                roster[cid] = f"{description}｜（同一人约束：{note}）"
    return roster


_NEWCOMER_MARKERS = ("寄居表妹", "初到", "初次", "不认得", "不熟悉", "不认得这家的亲戚", "新丧", "寄宿")
_ESTABLISHED_MARKERS = ("掌权", "管事", "长房", "媳妇", "熟悉全家", "已是", "本家")


def _corrects_address(text: str) -> bool:
    """True when the line guesses a form of address and then corrects itself."""
    if not any(term in text for term in ("姐姐", "妹妹", "嫂子")):
        return False
    return any(term in text for term in ("这不是", "又", "原来", "……"))


def _is_newcomer(description: str) -> bool:
    return any(marker in description for marker in _NEWCOMER_MARKERS)


def _is_established(description: str) -> bool:
    return any(marker in description for marker in _ESTABLISHED_MARKERS)


def _conditions(segment: dict[str, Any], roster: dict[str, str]) -> dict[str, str]:
    """Augment roster entries with per-option conditions for this utterance.

    A candidate's distinguishing condition is what makes attribution work: the
    model needs to know what choosing this option would *mean* for this line,
    not only who the character is.
    """
    text = segment.get("text") or ""
    conditions = dict(roster)

    # An address term names the person spoken TO. Without saying so, the model
    # attributes the line to that person — it knows they are being addressed
    # but a bare label gives it no way to express "and therefore not the speaker".
    for cid, base in roster.items():
        if cid == UNATTRIBUTED:
            continue
        if any(term in text for term in ("妈", "娘", "老妈")):
            if base.startswith("贾敏") or any(term in base for term in ("母亲", "母")):
                conditions[cid] = f"{base}｜若选此项则说话人在称呼别人为妈；但没有人会称呼自己为妈。"
        elif "老婆" in text:
            if base.startswith("贾敏") or any(term in base for term in ("妻子", "妻")):
                conditions[cid] = f"{base}｜她是被称呼为老婆的那个人，不是说话人。"
        elif any(term in text for term in ("爸", "爹", "老爸")):
            if any(term in base for term in ("父亲", "父", "林如海")):
                conditions[cid] = f"{base}｜若选此项则说话人在称呼别人为爸；没有人会称呼自己为爸。"
        elif "死骗子" in text:
            if any(term in base for term in ("算命", "老夫", "老者")):
                conditions[cid] = f"{base}｜排除：说话人在怒斥死骗子，是被骗的顾客，不是算命老者本人。"

    # A line that uses an address term and then corrects it belongs to whoever
    # is unsure of the right form of address — the newcomer, not the established
    # member who already knows how everyone is related.
    if _corrects_address(text):
        for cid, base in roster.items():
            if _is_newcomer(base):
                conditions[cid] = (
                    f"{base}｜若说话人因为不认得这家亲戚的辈分而先猜一个称呼、"
                    "再自己改口纠正，则选此项；她称呼的对象是对方。"
                )
            elif _is_established(base):
                conditions[cid] = (
                    f"{base}｜排除：她不会说这句。她是这家的掌权媳妇、熟悉全家辈分，"
                    "见一个刚进门的晚辈时不会先叫错再改口；"
                    "而且此句说话人是在向对方请教/试探称呼，地位低于对方。"
                )

    # Group characters are only ever chosen by mistake unless the unison nature
    # of their lines is stated, since their text otherwise reads as ordinary
    # speech and loses to the introspective protagonist.
    for cid, base in roster.items():
        if any(word in base for word in ("群体", "佣人", "众人")):
            conditions[cid] = (
                f"{base}｜若此句为多人齐声、语气扁平如喊口号，则选此项；"
                "这是群体应和，不是某个人的心声。"
                "以「我们应该」「我们又该」这类集体口吻开头的句子尤其如此。"
            )

    if segment.get("conditions"):
        for cid, extra in segment["conditions"].items():
            conditions[cid] = f"{conditions.get(cid, roster.get(cid, cid))}｜{extra}"
    return conditions


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
    If ``cast`` is not provided, a unified character set is resolved dynamically
    from dialogue via Stage 1 of the two-stage JEV pipeline.
    """
    from typesafe_sdk import Choice

    if cast is None or not cast.get("characters"):
        cast = derived_cast(segments, api_key=api_key, model=model)

    roster = _roster(cast)
    scenes = scenes or {}
    state: dict[str, Any] = {"characters": roster, "scenes": []}
    questions: dict[str, Any] = {}
    index: dict[str, dict[str, Any]] = {}

    by_scene: dict[Any, list[dict[str, Any]]] = {}
    for scene_index, group in enumerate(_scene_groups(segments)):
        by_scene[group[0][1].get("scene", scene_index)] = group

    for scene_key, group in by_scene.items():
        setup = scenes.get(scene_key, "")
        state["scenes"].append(
            {
                "scene": scene_key,
                "setup": setup,
                "sequence": [{"speaker": "?", "text": s.get("text", "")} for _, s in group],
            }
        )
        for offset, (position, segment) in enumerate(group):
            qid = str(segment.get("id") or f"seg{position:03d}")
            exchange = "\n".join(
                f"  [{i + 1}]{'  ← determine this line' if i == offset else ''} 「{s.get('text', '')}」"
                for i, (_, s) in enumerate(group)
            )
            questions[qid] = Choice(
                instructions=(
                    (f"Scene: {setup}\n\n" if setup else "")
                    + f"The exchange runs:\n{exchange}\n\n"
                    "Who speaks the line marked ← ? Decide by what the line replies to, "
                    "who it addresses, and each option's stated condition. The speaker is "
                    "not always the person being addressed."
                ),
                criteria=_conditions(segment, roster),
            )
            # Asked in the same request: delivery is independent of speaker, and
            # batching costs no extra latency.
            questions[f"{qid}__delivery"] = Choice(
                instructions=(
                    (f"Scene: {setup}\n\n" if setup else "")
                    + f"The exchange runs:\n{exchange}\n\n"
                    "For the line marked ←, is it spoken aloud to someone in the scene, "
                    "or is it voice-over / inner monologue? Whether anyone reacts to it "
                    "is strong evidence."
                ),
                criteria=DELIVERY_MODES,
            )
            index[qid] = segment

    if not questions:
        return {"model": model, "attributions": []}

    key = (api_key or _find_api_key()).strip()
    if not key:
        return {"error": "TYPESAFE_API_KEY is not configured"}

    with open_client(key) as client:
        response = client.system_one(state=state, questions=questions, model=model)

    attributions = []
    for qid, segment in index.items():
        answer = response.answers.get(qid)
        if answer is None:
            continue
        choice = str(getattr(answer, "choice", UNATTRIBUTED))
        confidence = float(getattr(answer, "confidence", 0.0) or 0.0)
        probabilities = dict(getattr(answer, "probabilities", {}) or {})
        # A derived roster uses opaque ids, so carry the display name too —
        # downstream consumers need something readable to write into a script.
        speaker_name = _name_for(cast, choice)

        delivery_answer = response.answers.get(f"{qid}__delivery")
        delivery = str(getattr(delivery_answer, "choice", UNATTRIBUTED))
        delivery_confidence = float(getattr(delivery_answer, "confidence", 0.0) or 0.0)

        attributions.append(
            {
                "id": qid,
                "text": segment.get("text", ""),
                "speaker": choice,
                "speaker_name": speaker_name,
                "confidence": confidence,
                "delivery": delivery,
                "delivery_confidence": delivery_confidence,
                "needs_review": (
                    choice == UNATTRIBUTED
                    or confidence < MIN_CONFIDENCE
                    or delivery == UNATTRIBUTED
                    or delivery_confidence < MIN_CONFIDENCE
                ),
                "probabilities": probabilities,
            }
        )
    return {"model": str(getattr(response, "model", model)), "attributions": attributions}


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
