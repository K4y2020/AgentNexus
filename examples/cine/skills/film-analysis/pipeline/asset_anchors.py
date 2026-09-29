"""Whether a storyboard's characters and scenes have the sheets and prompts it needs.

Standard library only. The canvas importer checks this before its first write,
and the film-analysis pipeline checks it at the storyboard stage in a Topic
whose python has no agentnexus. Two byte-identical copies back those callers:
``agentnexus/seedance/asset_anchors.py`` and the skill's
``pipeline/asset_anchors.py``; a test keeps them identical.
"""

from pathlib import Path
from typing import Any, NamedTuple


def _character_entry(cast_doc: dict, cid: str) -> dict | None:
    return next(
        (c for c in cast_doc.get("characters", []) if isinstance(c, dict) and c.get("id") == cid),
        None,
    )


def _character_sheet(entry: dict) -> str:
    image = entry.get("image") if isinstance(entry.get("image"), dict) else {}
    return str(image.get("sheet") or image.get("prompt") or "").strip()


class CastIndex(NamedTuple):
    """Which cast card describes each character id the storyboard uses."""

    cards: dict[str, dict]  # outline/storyboard id -> the original cast card
    conflicts: dict[str, list[str]]  # id -> labels of every card that claims it
    unlinked: list[str]  # cards that could not be tied to one id, with the reason
    outline_found: bool


def _card_label(card: dict) -> str:
    return str(card.get("name") or card.get("id") or "(unnamed card)").strip()


def _word(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _needs_outline(cast_doc: dict, referenced: list[str]) -> bool:
    """True when some card has no id, or some used id is not any card's own id."""
    cards = [c for c in cast_doc.get("characters", []) if isinstance(c, dict)]
    own_ids = {_word(c.get("id")) for c in cards if _word(c.get("id"))}
    return any(not _word(c.get("id")) for c in cards) or any(
        cid not in own_ids for cid in referenced
    )


def _resolve_cast(cast_doc: dict, outline: tuple[Path | None, dict] | None = None) -> CastIndex:
    """Tie each cast card to the character id the outline and storyboard use.

    The cast schema lets a card omit ``id`` (or carry an id of its own), while
    the storyboard names characters by outline id. A card whose id is an outline
    id (or any explicit id when there is no outline) is used as-is. Otherwise
    the card is linked by its name to exactly one outline character, then by
    name and aliases together — the same rule cine-script applies. A card that
    matches none or several is left unlinked, and an id claimed by two cards
    belongs to neither. Nothing is guessed, and the cast file is never changed.
    """
    outline_path, outline_doc = outline if outline is not None else (None, {})
    outline_chars = [
        c
        for c in (outline_doc or {}).get("characters", [])
        if isinstance(c, dict) and _word(c.get("id"))
    ]
    outline_ids = {_word(c["id"]) for c in outline_chars}
    by_name: dict[str, set[str]] = {}
    by_word: dict[str, set[str]] = {}
    for character in outline_chars:
        oid = _word(character["id"])
        if _word(character.get("name")):
            by_name.setdefault(_word(character["name"]), set()).add(oid)
        for word in (character.get("name"), *(character.get("aliases") or [])):
            if _word(word):
                by_word.setdefault(_word(word), set()).add(oid)

    def lookup(table: dict[str, set[str]], words) -> set[str]:
        return {oid for word in words for oid in table.get(_word(word), ())}

    claims: dict[str, list[dict]] = {}
    unlinked: list[str] = []
    for card in cast_doc.get("characters", []):
        if not isinstance(card, dict):
            continue
        own = _word(card.get("id"))
        target = None
        if own and (not outline_chars or own in outline_ids):
            target = own
        elif outline_chars:
            words = [card.get("name"), *(card.get("aliases") or [])]
            hits = lookup(by_name, [card.get("name")]) or lookup(by_word, words)
            if len(hits) == 1:
                target = next(iter(hits))
            else:
                if hits:
                    reason = f"matches {', '.join(sorted(hits))}"
                else:
                    reason = "matches no outline character"
                unlinked.append(f"'{_card_label(card)}' {reason}")
                # An explicit id stays the card's own; it is just not an outline id.
                target = own or None
        else:
            unlinked.append(f"'{_card_label(card)}' has no id")
        if target:
            claims.setdefault(target, []).append(card)
    return CastIndex(
        cards={cid: cards[0] for cid, cards in claims.items() if len(cards) == 1},
        conflicts={
            cid: [_card_label(c) for c in cards] for cid, cards in claims.items() if len(cards) > 1
        },
        unlinked=unlinked,
        outline_found=outline_path is not None,
    )


def _missing_character(cid: str, index: CastIndex, cast_name: str) -> str:
    if cid in index.conflicts:
        return (
            f"Character {cid} matches more than one card in {cast_name} "
            f"({', '.join(index.conflicts[cid])}); keep exactly one card per character."
        )
    if index.unlinked:
        unmatched = "; ".join(index.unlinked)
        if not index.outline_found:
            return (
                f"Character {cid} has no card with that id in {cast_name}. Cards without an "
                "outline id can only be matched through the production's outline.json, "
                f"which was not found. Unmatched cards: {unmatched}."
            )
        return (
            f"Character {cid} is not defined in {cast_name}: cards without its id could not "
            f"be matched to exactly one outline character. Unmatched cards: {unmatched}."
        )
    return f"Character {cid} is used in the storyboard but not defined in {cast_name}."


def _resolve_scene_id(script_doc: dict, ep_num: Any, s_idx: Any) -> str | None:
    if not isinstance(s_idx, int) or s_idx <= 0:
        return None
    episodes = script_doc.get("episodes", []) if isinstance(script_doc, dict) else []
    ep = next(
        (e for e in episodes if isinstance(e, dict) and str(e.get("ep")) == str(ep_num)),
        None,
    )
    scenes = ep.get("scenes", []) if isinstance(ep, dict) else []
    if 0 < s_idx <= len(scenes):
        sc = scenes[s_idx - 1]
        if isinstance(sc, dict) and sc.get("sceneId"):
            return str(sc["sceneId"]).strip()
    return f"S{int(s_idx):02d}"


def _scene_entry(art_doc: dict, scene_id: str | None, scene_index: Any = None) -> dict | None:
    scenes = [s for s in art_doc.get("scenes", []) if isinstance(s, dict)]
    match = next((s for s in scenes if scene_id and s.get("id") == scene_id), None)
    if match is not None:
        return match
    legacy = [
        s
        for s in scenes
        if scene_index is not None and not s.get("id") and s.get("sceneIndex") == scene_index
    ]
    return legacy[0] if len(legacy) == 1 else None


def _scene_prompt(entry: dict) -> str:
    image = entry.get("image") if isinstance(entry.get("image"), dict) else {}
    return str(image.get("prompt") or "").strip()


def _asset_nodes(nodes: list[dict], key: str) -> dict:
    """Existing image_prompt cards keyed by their characterId/sceneId/sceneIndex."""
    result = {}
    for n in nodes:
        if n.get("type") == "image_prompt":
            data = n.get("data") or {}
            val = data.get(key)
            if val is not None:
                result[val] = n
    return result


def _legacy_scene_nodes(nodes: list[dict]) -> dict:
    result = {}
    ambiguous = set()
    for node in nodes:
        data = node.get("data") or {}
        if node.get("type") != "image_prompt" or data.get("sceneId"):
            continue
        index = data.get("sceneIndex")
        if index is None:
            continue
        if index in result:
            ambiguous.add(index)
        result[index] = node
    return {index: node for index, node in result.items() if index not in ambiguous}


def _ambiguous_scene_indexes(referenced_scenes: dict[str, int]) -> set[int]:
    seen = {}
    ambiguous = set()
    for scene_id, index in referenced_scenes.items():
        if index in seen and seen[index] != scene_id:
            ambiguous.add(index)
        seen[index] = scene_id
    return ambiguous


def _scene_node(scene_id, scene_index, by_id, legacy_by_index, ambiguous_indexes):
    return by_id.get(scene_id) or (
        legacy_by_index.get(scene_index) if scene_index not in ambiguous_indexes else None
    )


def _referenced_characters(segments) -> list[str]:
    return sorted(
        {
            cid
            for _, seg in segments
            for cut in seg.get("cuts") or []
            for cid in cut.get("characters") or []
            if cid
        }
    )


def _referenced_scenes(segments, script_doc: dict | None = None) -> dict[str, int]:
    """Map each referenced scene ID to its 1-based sceneIndex."""
    scenes = {}
    for ep, seg in segments:
        s_idx = seg.get("sceneIndex")
        if s_idx:
            sid = (
                _resolve_scene_id(script_doc or {}, ep.get("ep"), s_idx)
                if script_doc
                else f"S{int(s_idx):02d}"
            )
            if sid and sid not in scenes:
                scenes[sid] = s_idx
    return scenes


def _missing_visual_assets(
    segments,
    nodes,
    cast,
    art,
    script_doc: dict | None = None,
    *,
    outline: tuple[Path | None, dict] | None = None,
    cast_index: CastIndex | None = None,
) -> list[str]:
    """List every character/scene that is neither on the canvas nor fully defined in cast/art.

    Characters are looked up through :func:`_resolve_cast`, so a cast whose
    cards omit ``id`` is matched against ``outline`` rather than rejected.
    """
    (cast_path, cast_doc), (art_path, art_doc) = cast, art
    cast_name = cast_path.name if cast_path else "cast.json (not found)"
    art_name = art_path.name if art_path else "art.json (not found)"
    if cast_index is None:
        cast_index = _resolve_cast(cast_doc, outline)
    on_canvas_characters = _asset_nodes(nodes, "characterId")
    on_canvas_scenes = _asset_nodes(nodes, "sceneId")
    legacy_scenes = _legacy_scene_nodes(nodes)
    problems = []
    for cid in _referenced_characters(segments):
        if cid in on_canvas_characters:
            continue
        entry = cast_index.cards.get(cid)
        if entry is None:
            problems.append(_missing_character(cid, cast_index, cast_name))
        elif not _character_sheet(entry):
            problems.append(
                f"Character {cid} ({entry.get('name', '')}) has no image.sheet in {cast_name}."
            )

    ref_scenes = _referenced_scenes(segments, script_doc)
    ambiguous_indexes = _ambiguous_scene_indexes(ref_scenes)
    for sid, scene_index in ref_scenes.items():
        if _scene_node(sid, scene_index, on_canvas_scenes, legacy_scenes, ambiguous_indexes):
            continue
        entry = _scene_entry(art_doc, sid, scene_index)
        if entry is None:
            problems.append(
                f"Scene {scene_index} is used in the storyboard but not defined in {art_name}."
            )
        elif not _scene_prompt(entry):
            problems.append(
                f"Scene {scene_index} ({entry.get('name', '')}) has no image.prompt in {art_name}."
            )
    return problems
