"""Send native creative artifacts to V3's deterministic storyboard importer."""

import hashlib
import json
import logging
import os
import re
import tempfile
import uuid
from pathlib import Path
from typing import Any, NamedTuple

from .client import SeedanceError
from .production_gate import ProductionRejected, inside

logger = logging.getLogger(__name__)


def build_structured_shots(segment: dict, script: dict, episode_number: int) -> list[dict]:
    """Project native storyboard cuts into V3 PromptTask structured shots.

    The native H3 prompt is a render artifact. The structured cut contract is the
    source of truth for downstream optimization, especially speaker ownership.
    Keep dialogue as ``speaker`` + verbatim ``exactText`` instead of trying to
    recover it from the rendered H3 prose later.
    """
    scene_index = segment.get("sceneIndex")
    episodes = script.get("episodes") if isinstance(script, dict) else None
    episode = next((item for item in episodes or [] if item.get("ep") == episode_number), None)
    scenes = episode.get("scenes") if isinstance(episode, dict) else None
    scene = (
        scenes[scene_index - 1]
        if isinstance(scene_index, int) and scenes and 0 < scene_index <= len(scenes)
        else None
    )
    flow = scene.get("flow", []) if isinstance(scene, dict) else []

    shots: list[dict] = []
    for cut_index, cut in enumerate(segment.get("cuts") or [], start=1):
        beats = cut.get("beats") or []
        if not isinstance(beats, list) or len(beats) != 2:
            continue
        first, last = beats
        if not isinstance(first, int) or not isinstance(last, int) or first < 1 or last < first:
            continue
        cut_flow = flow[first - 1 : last]
        dialogue_beats = [
            {
                "sourceRef": f"script:{episode_number}:{scene_index}:{first + offset}",
                "speaker": str(beat.get("speaker") or "未标注说话人").strip(),
                "exactText": str(beat.get("line") or "").strip(),
                **(
                    {"delivery": str(beat["delivery"]).strip()}
                    if str(beat.get("delivery") or "").strip()
                    else {}
                ),
            }
            for offset, beat in enumerate(cut_flow)
            if isinstance(beat, dict) and str(beat.get("line") or "").strip()
        ]
        action_beats = [
            {"action": str(beat.get("action") or "").strip()}
            for beat in cut_flow
            if isinstance(beat, dict) and str(beat.get("action") or "").strip()
        ]
        description = "\n".join(
            str(
                beat.get("action") or f"{beat.get('speaker') or 'VO'}: {beat.get('line') or ''}"
            ).strip()
            for beat in cut_flow
            if isinstance(beat, dict)
        ).strip()
        shots.append(
            {
                "id": f"{segment.get('id', 'segment')}-f{cut_index}",
                "durationSec": cut.get("seconds"),
                "description": description or str(cut.get("frame") or "").strip(),
                "shotSize": cut.get("size"),
                "composition": cut.get("frame") or "",
                "camera": {"motion": cut.get("camera")} if cut.get("camera") else None,
                "lightingIntent": scene.get("lighting", "") if isinstance(scene, dict) else "",
                "actionBeats": action_beats,
                "dialogueBeats": dialogue_beats,
                "visibleSubjects": cut.get("characters") or [],
                "referenceNeeds": cut.get("props") or [],
            }
        )
    return shots


async def _create_target(client, project_id, *, node_type, title, data):
    clean_title = (title or "").strip()
    result = await client.submit_command(
        project_id,
        {
            "type": "canvas.create_node",
            "nodeType": node_type,
            "title": clean_title,
            "parentId": None,
            "data": data,
            "commandId": f"cine-import-target-{uuid.uuid4().hex}",
        },
    )
    if not result.get("accepted"):
        raise ProductionRejected("CINE_IMPORT_TARGET_CREATE_FAILED", result)
    snapshot = await client.get_snapshot(project_id)
    matches = [
        node
        for node in snapshot.get("nodes", [])
        if (node.get("title") or "").strip() == clean_title
    ]
    if len(matches) != 1:
        raise ProductionRejected("CINE_IMPORT_TARGET_CREATE_UNVERIFIED", result)
    return matches[0], snapshot


# Hash of the prompt the importer last wrote to a video card. A card whose current
# prompt no longer matches was edited on the canvas (by hand or by the prompt
# optimizer) and must not be overwritten by a re-import.
IMPORTED_PROMPT_KEY = "importedPromptSha256"


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _stage_document(prod_dir: Path, stage: str) -> tuple[Path | None, dict]:
    """Read cast/art like production.py: an explicit binding, else one unambiguous file."""
    selected = None
    manifest = prod_dir / "production.json"
    if manifest.is_file():
        try:
            document = json.loads(manifest.read_text(encoding="utf-8-sig"))
        except ValueError as exc:
            raise ProductionRejected(
                "CINE_IMPORT_FILE_INVALID", {"file": manifest.name, "error": str(exc)}
            ) from exc
        artifacts = document.get("artifacts") if isinstance(document, dict) else None
        entry = artifacts.get(stage) if isinstance(artifacts, dict) else None
        relative = entry.get("path") if isinstance(entry, dict) else None
        if isinstance(relative, str) and relative.strip():
            selected = (prod_dir / relative).resolve()
            if not selected.is_relative_to(prod_dir):
                raise ProductionRejected("CINE_PRODUCTION_PATH_ESCAPE", {"stage": stage})
    if selected is None:
        candidates = [
            path
            for path in (prod_dir / f"{stage}.json", *sorted(prod_dir.glob(f"*-{stage}.json")))
            if path.is_file()
        ]
        if len(candidates) > 1:
            raise ProductionRejected(
                "CINE_IMPORT_STAGE_AMBIGUOUS",
                {"stage": stage, "files": [path.name for path in candidates]},
            )
        selected = candidates[0] if candidates else None
    if selected is None or not selected.is_file():
        return None, {}
    try:
        document = json.loads(selected.read_text(encoding="utf-8-sig"))
    except ValueError as exc:
        raise ProductionRejected(
            "CINE_IMPORT_FILE_INVALID", {"file": selected.name, "error": str(exc)}
        ) from exc
    return selected, document if isinstance(document, dict) else {}


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


def _cast_view(index: CastIndex) -> dict:
    """The resolved cards under their outline ids, for matching only. Never written."""
    return {"characters": [{**card, "id": cid} for cid, card in index.cards.items()]}


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


def _reference_entities(
    nodes: list[dict], referenced_scenes: dict, legacy_scene_nodes: dict, ambiguous_indexes: set
) -> dict[str, tuple[str, str]]:
    """Reference id -> the character or scene its image card holds.

    The canvas reports a reference as ``node:<id>``; the card's own data says
    which entity it depicts, so nothing has to be guessed from a label.
    """
    entities: dict[str, tuple[str, str]] = {}

    def bind(node_id: str, entity: tuple[str, str]) -> None:
        entities[node_id] = entity
        entities[f"node:{node_id}"] = entity

    for node in nodes:
        data = node.get("data") or {}
        if node.get("type") != "image_prompt" or not node.get("id"):
            continue
        if data.get("characterId"):
            bind(node["id"], ("char", str(data["characterId"])))
        elif data.get("sceneId"):
            bind(node["id"], ("scene", str(data["sceneId"])))
    for sid, index in referenced_scenes.items():
        node = legacy_scene_nodes.get(index) if index not in ambiguous_indexes else None
        if node and node.get("id") and node["id"] not in entities:
            bind(node["id"], ("scene", sid))
    return entities


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


def _image_pointer(document: dict, collection: str, entry: dict, fields: tuple[str, ...]) -> str:
    for index, candidate in enumerate(document.get(collection, [])):
        if candidate is entry:
            image = entry.get("image") if isinstance(entry.get("image"), dict) else {}
            for field in fields:
                if str(image.get(field) or "").strip():
                    return f"/{collection}/{index}/image/{field}"
    raise ProductionRejected("CINE_IMPORT_ASSETS_MISSING")


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


_SUBJECT_LINE = re.compile(r"<Subject (\d+)> — ([^\n]+)")
_PICTURE_TOKEN = re.compile(r"<Picture\s*(\d+)>")
_SUBJECT_TOKEN = re.compile(r"<Subject\s*(\d+)>")
# A JEV subject match below this confidence leaves the prompt untouched.
SUBJECT_MATCH_CONFIDENCE = 0.7
_UNCLEAR = "unclear"


def _entity_terms(cast_doc: dict, art_doc: dict) -> list[tuple[str, str, str]]:
    """(term, kind, ident) for every id, name and alias in the project's cast and art.

    A term that two entities share identifies neither and is left out. Longer
    terms come first so a name that contains another name wins. The importer
    passes the resolved cast view (see :func:`_cast_view`), so cards that carry
    no id of their own are matched under the outline id they resolved to.
    """
    owners: dict[str, set[tuple[str, str]]] = {}

    def claim(term: Any, kind: str, ident: str) -> None:
        if isinstance(term, str) and len(term.strip()) >= 2:
            owners.setdefault(term.strip().lower(), set()).add((kind, ident))

    for character in cast_doc.get("characters", []) if isinstance(cast_doc, dict) else []:
        if not isinstance(character, dict) or not character.get("id"):
            continue
        cid = str(character["id"])
        for term in (cid, character.get("name"), *(character.get("aliases") or [])):
            claim(term, "char", cid)
    for scene in art_doc.get("scenes", []) if isinstance(art_doc, dict) else []:
        if not isinstance(scene, dict) or not scene.get("id"):
            continue
        sid = str(scene["id"])
        for term in (sid, scene.get("name"), *(scene.get("aliases") or [])):
            claim(term, "scene", sid)
    terms = [
        (term, *next(iter(entities))) for term, entities in owners.items() if len(entities) == 1
    ]
    return sorted(terms, key=lambda item: -len(item[0]))


def _term_pattern(term: str) -> re.Pattern:
    if re.fullmatch(r"[a-z0-9_\-]+", term):
        # ASCII ids and names match as whole tokens: "c1" must not hit "c10".
        return re.compile(rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])")
    return re.compile(re.escape(term))


def _identify_subject_entity(desc: str, cast_doc: dict, art_doc: dict) -> tuple[str, str | None]:
    """Identify which cast character or art scene a <Subject N> line names.

    Only explicit references count: the entity's id as a whole token, its name,
    or an alias, all taken from the project's own cast and art documents. A line
    that names two different entities, or none, is ``unknown`` — describing
    appearance without a name is a semantic match, which belongs to JEV.
    """
    remaining = desc.lower()
    found: set[tuple[str, str]] = set()
    for term, kind, ident in _entity_terms(cast_doc, art_doc):
        pattern = _term_pattern(term)
        if pattern.search(remaining):
            found.add((kind, ident))
            remaining = pattern.sub(" ", remaining)
    if len(found) == 1:
        return next(iter(found))
    return ("unknown", None)


def _canvas_ref_entity(
    ref: dict, cast_doc: dict, art_doc: dict, ref_entities: dict | None
) -> tuple[str, str | None]:
    """Which entity a canvas reference image is, from the card it comes from."""
    ref_id = str(ref.get("id") or "")
    if ref_entities and ref_id in ref_entities:
        return ref_entities[ref_id]
    label = str(ref.get("label") or "")
    # Cards created by the importer are titled "<name> · 角色三视图/场景概念图".
    head = label.split(" · ", 1)[0].strip().lower()
    exact = {
        (kind, ident) for term, kind, ident in _entity_terms(cast_doc, art_doc) if term == head
    }
    if len(exact) == 1:
        return next(iter(exact))
    return _identify_subject_entity(label, cast_doc, art_doc)


def _subject_definitions(prompt: str) -> dict[int, str]:
    if not prompt or "subject_definitions:" not in prompt:
        return {}
    start = prompt.find("subject_definitions:")
    end = prompt.find("summary:")
    if end < 0:
        return {}
    return {int(num): desc for num, desc in _SUBJECT_LINE.findall(prompt[start:end])}


def align_h3_prompt_to_references(
    prompt: str,
    actual_refs: list[dict],
    cast_doc: dict,
    art_doc: dict,
    *,
    ref_entities: dict[str, tuple[str, str]] | None = None,
    subject_entities: dict[int, tuple[str, str]] | None = None,
    issues: list[str] | None = None,
) -> str:
    """Align <Subject N> and <Picture N> in an H3 Ref2VA prompt with canvas references.

    The order of reference pictures on the canvas is the physical source of truth
    for the video diffusion model: <Picture 1> maps to the 1st reference image,
    <Picture 2> to the 2nd, etc. If the prompt was authored with a different
    order, this renumbers the subject definitions and every token that refers to
    them so each definition cites its own canvas input.

    ``ref_entities`` maps a reference id to the entity its card holds;
    ``subject_entities`` supplies subjects already matched elsewhere (by JEV).
    The prompt is only rewritten when every subject maps to exactly one distinct
    canvas reference. Anything less — an unidentified subject, two subjects on
    one entity, a subject with no canvas image — leaves it unchanged and appends
    the reason to ``issues``: a guessed renumbering puts one character's face on
    another.
    """
    subjects = _subject_definitions(prompt)
    image_refs = [r for r in actual_refs if (r.get("kind") or "image") == "image"]
    if not subjects or not image_refs:
        return prompt
    problems: list[str] = []
    subj_start = prompt.find("subject_definitions:")
    summary_start = prompt.find("summary:")

    matched = dict(subject_entities or {})
    entity_of: dict[int, tuple[str, str]] = {}
    for num, desc in subjects.items():
        entity = matched.get(num) or _identify_subject_entity(desc, cast_doc, art_doc)
        if entity[0] == "unknown" or entity[1] is None:
            problems.append(f"<Subject {num}> does not identify one cast character or art scene.")
        else:
            entity_of[num] = (entity[0], str(entity[1]))

    canvas: list[tuple[int, str, str | None, str]] = []
    position: dict[tuple[str, str], int] = {}
    for idx, ref in enumerate(image_refs, 1):
        kind, ident = _canvas_ref_entity(ref, cast_doc, art_doc, ref_entities)
        label = str(ref.get("label") or "")
        canvas.append((idx, kind, ident, label))
        if kind != "unknown" and ident is not None:
            key = (kind, str(ident))
            if key in position:
                problems.append(f"Canvas references {position[key]} and {idx} are both {ident}.")
            position[key] = idx

    seen: dict[tuple[str, str], int] = {}
    for num, entity in entity_of.items():
        if entity in seen:
            problems.append(
                f"<Subject {seen[entity]}> and <Subject {num}> both describe {entity[1]}."
            )
        seen[entity] = num
        if entity not in position:
            problems.append(f"<Subject {num}> ({entity[1]}) has no reference image on the canvas.")

    # Which canvas picture each authored <Picture p> meant: the one whose
    # subject definition cites it (or, without a citation, the same number).
    subject_map = {
        num: position[entity] for num, entity in entity_of.items() if entity in position
    }
    picture_map: dict[int, int] = {}
    for num, desc in subjects.items():
        if num not in subject_map:
            continue
        cited = [int(p) for p in _PICTURE_TOKEN.findall(desc)] or [num]
        for pic in cited:
            if picture_map.get(pic, subject_map[num]) != subject_map[num]:
                problems.append(f"<Picture {pic}> is cited for two different subjects.")
            picture_map[pic] = subject_map[num]
    rest = prompt[summary_start:]
    for token in _SUBJECT_TOKEN.findall(rest):
        if int(token) not in subject_map:
            problems.append(f"<Subject {token}> is used but not defined.")
    for token in _PICTURE_TOKEN.findall(rest):
        if int(token) not in picture_map:
            problems.append(f"<Picture {token}> is used but belongs to no defined subject.")
    if problems:
        if issues is not None:
            issues.extend(problems)
        return prompt

    extra = [entry for entry in canvas if entry[0] not in subject_map.values()]
    if (
        not extra
        and all(num == new for num, new in subject_map.items())
        and all(pic == new for pic, new in picture_map.items())
    ):
        return prompt

    def renumber(text: str) -> str:
        text = _SUBJECT_TOKEN.sub(lambda m: f"__SUBJ_{subject_map[int(m.group(1))]}__", text)
        text = _PICTURE_TOKEN.sub(
            lambda m: f"__PIC_{picture_map.get(int(m.group(1)), int(m.group(1)))}__", text
        )
        return re.sub(
            r"__(SUBJ|PIC)_(\d+)__",
            lambda m: f"<{'Subject' if m.group(1) == 'SUBJ' else 'Picture'} {m.group(2)}>",
            text,
        )

    by_new = {new: num for num, new in subject_map.items()}
    lines = ["subject_definitions:"]
    for idx, kind, ident, label in canvas:
        if idx in by_new:
            num = by_new[idx]
            lines.append(renumber(f"<Subject {num}> — {subjects[num]}"))
            continue
        if kind == "char":
            desc = f"character {ident}; face, hair and costume come entirely from <Picture {idx}>."
        elif kind == "scene":
            desc = (
                f"environment {ident}; layout, materials and lighting come entirely "
                f"from <Picture {idx}>."
            )
        else:
            clean_lbl = re.sub(r"[一-鿿]", "", label).strip(" ·-_")
            desc = (
                f"{clean_lbl or 'continuation frame from preceding shot'}; visual composition "
                f"and action carry come entirely from <Picture {idx}>."
            )
        lines.append(f"<Subject {idx}> — {desc}")

    rest = renumber(rest)
    everyone = [f"<Subject {i}>" for i in range(1, len(image_refs) + 1)]
    retention = re.search(r"retention_analysis:\s*[^\n]+", rest)
    if retention and not all(token in retention.group(0) for token in everyone):
        rest = (
            rest[: retention.start()]
            + f"retention_analysis: {', '.join(everyone)} are retained exactly as referenced — "
            "the same faces, costumes, environment and lighting. Only the action, the camera "
            "and the timing are new." + rest[retention.end() :]
        )
    return prompt[:subj_start] + "\n".join(lines) + "\n" + rest


def _entity_brief(kind: str, ident: str, cast_doc: dict, art_doc: dict) -> str:
    """What a reference image shows, from the project's own cast or art entry."""
    if kind == "char":
        entry = _character_entry(cast_doc, ident) or {}
        persona = entry.get("persona") if isinstance(entry.get("persona"), dict) else {}
        image = entry.get("image") if isinstance(entry.get("image"), dict) else {}
        parts = [
            f"character {ident}",
            str(entry.get("name") or ""),
            "aka " + ", ".join(str(a) for a in entry["aliases"]) if entry.get("aliases") else "",
            str(entry.get("oneLiner") or ""),
            *(str(persona.get(k) or "") for k in ("gender", "ageRange", "identity", "appearance")),
            str(image.get("prompt") or image.get("sheet") or "")[:600],
        ]
    else:
        entry = next(
            (s for s in art_doc.get("scenes", []) if isinstance(s, dict) and s.get("id") == ident),
            {},
        )
        image = entry.get("image") if isinstance(entry.get("image"), dict) else {}
        parts = [
            f"environment {ident}",
            str(entry.get("name") or ""),
            str(entry.get("brief") or ""),
            str(image.get("prompt") or "")[:600],
        ]
    return " | ".join(part for part in parts if part.strip())


async def _default_subject_judge(state: dict, questions: dict) -> dict:
    """Ask JEV through the runner's cine_jev_judge transport."""
    try:
        from agentnexus.runner.tool_dispatch import _execute_jev_tool
    except Exception as exc:  # noqa: BLE001 - no JEV means no semantic match
        return {"error": f"JEV unavailable: {type(exc).__name__}: {exc}"}
    return json.loads(await _execute_jev_tool({"state": state, "questions": questions}))


async def _judge_subjects(
    unresolved: dict[int, str],
    candidates: list[tuple[str, str]],
    cast_doc: dict,
    art_doc: dict,
    judge,
) -> tuple[dict[int, tuple[str, str]], list[str]]:
    """Match unnamed subject definitions to canvas entities with JEV.

    Only a confident, specific answer counts. "unclear", a low confidence, an
    unknown option, or a failed call all leave the subject unmatched, which
    keeps the prompt as authored.
    """
    options = {
        f"{kind}:{ident}": _entity_brief(kind, ident, cast_doc, art_doc)
        for kind, ident in candidates
    }
    options[_UNCLEAR] = "The definition does not clearly describe exactly one of these references."
    questions = {
        f"subject_{num}": {
            "type": "choice",
            "instructions": (
                "An H3 video prompt defines a subject that must come from one of the project's "
                f"reference images.\n<Subject {num}> — {desc}\n"
                "Which reference does this definition describe? Judge by identity, age, "
                "costume and setting; answer unclear if more than one could fit."
            ),
            "criteria": options,
        }
        for num, desc in unresolved.items()
    }
    try:
        references = {k: v for k, v in options.items() if k != _UNCLEAR}
        result = await judge({"references": references}, questions)
    except Exception as exc:  # noqa: BLE001
        return {}, [f"JEV subject match failed: {type(exc).__name__}: {exc}"]
    if not isinstance(result, dict) or result.get("error"):
        reason = result.get("error") if isinstance(result, dict) else result
        return {}, [f"JEV subject match failed: {reason}"]
    answers = result.get("answers") if isinstance(result.get("answers"), dict) else {}
    matched: dict[int, tuple[str, str]] = {}
    for num in unresolved:
        answer = answers.get(f"subject_{num}") or {}
        choice = str(answer.get("choice") or "")
        try:
            confidence = float(answer.get("confidence") or 0.0)
        except (TypeError, ValueError):
            confidence = 0.0
        if choice in options and choice != _UNCLEAR and confidence >= SUBJECT_MATCH_CONFIDENCE:
            kind, ident = choice.split(":", 1)
            matched[num] = (kind, ident)
    return matched, []


async def align_prompt_with_references(
    prompt: str,
    actual_refs: list[dict],
    cast_doc: dict,
    art_doc: dict,
    *,
    ref_entities: dict[str, tuple[str, str]] | None = None,
    judge=None,
    issues: list[str] | None = None,
) -> str:
    """Align a prompt, asking JEV about subjects the project documents cannot name."""
    issues = issues if issues is not None else []
    subjects = _subject_definitions(prompt)
    if not subjects:
        return prompt
    matched: dict[int, tuple[str, str]] = {}
    unresolved = {}
    for num, desc in subjects.items():
        kind, ident = _identify_subject_entity(desc, cast_doc, art_doc)
        if kind == "unknown" or ident is None:
            unresolved[num] = desc
        else:
            matched[num] = (kind, ident)
    if unresolved and judge is not None:
        candidates = []
        for ref in actual_refs:
            if (ref.get("kind") or "image") != "image":
                continue
            kind, ident = _canvas_ref_entity(ref, cast_doc, art_doc, ref_entities)
            if kind != "unknown" and ident is not None and (kind, ident) not in candidates:
                candidates.append((kind, str(ident)))
        if candidates:
            judged, problems = await _judge_subjects(
                unresolved, candidates, cast_doc, art_doc, judge
            )
            matched.update(judged)
            issues.extend(problems)
    return align_h3_prompt_to_references(
        prompt,
        actual_refs,
        cast_doc,
        art_doc,
        ref_entities=ref_entities,
        subject_entities=matched,
        issues=issues,
    )


def _prompt_edited_on_canvas(data: dict) -> bool:
    recorded = data.get(IMPORTED_PROMPT_KEY)
    if isinstance(recorded, str):
        return _sha256_text(str(data.get("prompt") or "")) != recorded
    # Cards imported before the hash was recorded: the importer always cleared
    # promptProvenance, so a value now means the optimizer rewrote the prompt.
    return data.get("promptProvenance") is not None


async def _update_existing_card(client, project_id, card_id, title, card_data, prompt) -> str:
    """Refresh an imported card; leave it alone if its prompt was edited on the canvas."""
    latest = await client.get_snapshot(project_id)
    node = next((n for n in latest.get("nodes", []) if n.get("id") == card_id), None)
    if node is None:
        return "missing"
    data = node.get("data") or {}
    if _prompt_edited_on_canvas(data):
        return "preserved"
    patch_data = {**data, **card_data, IMPORTED_PROMPT_KEY: _sha256_text(prompt)}
    if patch_data == data and node.get("title") == title:
        return "unchanged"
    command = {
        "type": "canvas.update_node",
        "nodeId": card_id,
        "patch": {"title": title, "data": patch_data},
        "commandId": f"cine-sync-vp-{uuid.uuid4().hex}",
    }
    if latest.get("revision") is not None:
        command["expectedRevision"] = latest["revision"]
    try:
        result = await client.submit_command(project_id, command)
    except SeedanceError as exc:
        if exc.code != "REVISION_CONFLICT":
            raise
        return "conflict"
    return "updated" if result.get("accepted") else "conflict"


async def _ensure_node_binding(client, project_id, node_id, stage, pointer) -> None:
    latest = await client.get_snapshot(project_id)
    node = next((n for n in latest.get("nodes", []) if n.get("id") == node_id), None)
    if node is None:
        raise ProductionRejected("CINE_IMPORT_BINDING_NODE_MISSING", {"node_id": node_id})
    data = node.get("data") or {}
    if data.get("production_stage") == stage and data.get("production_pointer") == pointer:
        return
    command = {
        "type": "canvas.update_node",
        "nodeId": node_id,
        "patch": {"data": {**data, "production_stage": stage, "production_pointer": pointer}},
        "commandId": f"cine-bind-{uuid.uuid4().hex}",
    }
    if latest.get("revision") is not None:
        command["expectedRevision"] = latest["revision"]
    result = await client.submit_command(project_id, command)
    if not result.get("accepted"):
        raise ProductionRejected("CINE_IMPORT_BINDING_REJECTED", {"node_id": node_id})


async def _connect_once(client, project_id, edges: set, source, target, kind, tag) -> None:
    """Connect two cards unless that edge already exists (re-imports must not duplicate edges)."""
    if (source, target, kind) in edges or (source, target, None) in edges:
        return
    result = await client.submit_command(
        project_id,
        {
            "type": "canvas.connect",
            "from": source,
            "to": target,
            "kind": kind,
            "commandId": f"cine-conn-{tag}-{uuid.uuid4().hex[:8]}",
        },
    )
    if not result.get("accepted"):
        raise ProductionRejected("CINE_IMPORT_CONNECT_REJECTED", {"from": source, "to": target})
    edges.add((source, target, kind))


async def import_storyboard(
    server,
    session_id,
    client,
    *,
    storyboard_file,
    script_file,
    summary_node_id,
    episode_nodes,
    project_id=None,
    subject_judge=None,
):
    """Import a native storyboard and project its segments to video prompt cards.

    ``subject_judge`` answers JEV questions (``async (state, questions) -> dict``)
    for H3 subject definitions that the cast and art documents cannot identify
    by id, name or alias; it defaults to the runner's JEV transport.
    """
    response = await server.get(f"/v1/sessions/{session_id}", timeout=10)
    response.raise_for_status()
    session = response.json()
    bound = (session.get("labels") or {}).get("seedance.project_id")
    if not bound or (project_id and project_id != bound):
        raise ProductionRejected("CINE_PROJECT_BINDING_MISMATCH")
    root = Path(session["workspace"]).resolve(strict=True)
    storyboard_path = inside(root, storyboard_file)
    script_path = inside(root, script_file)
    documents = []
    hashes = {}
    for path in (storyboard_path, script_path):
        if not path.is_file() or path.stat().st_size > 8 * 1024 * 1024:
            raise ProductionRejected("CINE_IMPORT_FILE_INVALID")
        raw = path.read_bytes()
        hashes[path] = hashlib.sha256(raw).hexdigest()
        documents.append(json.loads(raw.decode("utf-8-sig")))
    storyboard, script = documents
    # One production directory holds storyboard, script, cast and art; importing
    # across directories would link visual assets from a different production.
    prod_dir = storyboard_path.parent
    if script_path.parent != prod_dir:
        raise ProductionRejected(
            "CINE_IMPORT_SPLIT_PRODUCTION_DIR",
            {
                "storyboard_file": str(storyboard_path),
                "script_file": str(script_path),
                "error": "storyboard.json and script.json must share one production directory.",
            },
        )
    snapshot = await client.get_snapshot(bound)
    nodes_list = snapshot.get("nodes", [])
    episode_numbers = [str(ep.get("ep")) for ep in storyboard.get("episodes", [])]
    if not episode_numbers or any(value == "None" for value in episode_numbers):
        raise ProductionRejected("CINE_IMPORT_EPISODES_REQUIRED")
    segments = [
        (ep, seg)
        for ep in storyboard.get("episodes", [])
        for seg in ep.get("segments", [])
        if seg.get("id")
    ]
    # Validate every visual asset before the first canvas command, so a missing
    # character or scene can never leave the canvas half-imported.
    cast = _stage_document(prod_dir, "cast") if segments else (None, {})
    art = _stage_document(prod_dir, "art") if segments else (None, {})
    # A cast card without the storyboard's id is matched through the outline.
    # Two outline documents raise CINE_IMPORT_STAGE_AMBIGUOUS here, and a
    # missing one leaves such cards unmatched; both stop before any canvas write.
    outline = (
        _stage_document(prod_dir, "outline")
        if segments and _needs_outline(cast[1], _referenced_characters(segments))
        else None
    )
    cast_index = _resolve_cast(cast[1], outline)
    problems = _missing_visual_assets(
        segments, nodes_list, cast, art, script_doc=script, cast_index=cast_index
    )
    if problems:
        raise ProductionRejected(
            "CINE_IMPORT_ASSETS_MISSING",
            {"production_dir": str(prod_dir), "problems": problems},
        )
    cast_doc, art_doc = cast[1], art[1]
    # Matching subjects and references needs every resolved card under its id.
    cast_view = _cast_view(cast_index)
    if not summary_node_id:
        title = "Cine 分镜同步总卡"
        matches = [
            node
            for node in nodes_list
            if node.get("type") == "text" and node.get("title") == title
        ]
        if len(matches) > 1:
            raise ProductionRejected("CINE_IMPORT_TARGETS_AMBIGUOUS")
        if matches:
            summary_node_id = matches[0]["id"]
        else:
            created, snapshot = await _create_target(
                client,
                bound,
                node_type="text",
                title=title,
                data={"content": "", "sourceArtifact": "storyboard.json"},
            )
            summary_node_id = created["id"]
            nodes_list = snapshot.get("nodes", [])
    if episode_nodes is None:
        episode_nodes = {}
    if not isinstance(episode_nodes, dict):
        raise ProductionRejected("CINE_IMPORT_TARGETS_REQUIRED")
    for ep in episode_numbers:
        if episode_nodes.get(ep):
            continue
        title = f"EP{int(ep):02d} 分镜表"
        matches = [
            node
            for node in nodes_list
            if node.get("type") == "storyboard" and node.get("title") == title
        ]
        if len(matches) > 1:
            raise ProductionRejected("CINE_IMPORT_TARGETS_AMBIGUOUS")
        if matches:
            episode_nodes[ep] = matches[0]["id"]
        else:
            created, snapshot = await _create_target(
                client,
                bound,
                node_type="storyboard",
                title=title,
                data={"outline": "", "shots": []},
            )
            episode_nodes[ep] = created["id"]
            nodes_list = snapshot.get("nodes", [])
    if set(episode_nodes) != set(episode_numbers):
        raise ProductionRejected("CINE_IMPORT_TARGETS_REQUIRED")
    target_ids = [summary_node_id, *episode_nodes.values()]
    if any(not isinstance(value, str) for value in target_ids):
        raise ProductionRejected("CINE_IMPORT_TARGETS_REQUIRED")
    nodes = {n["id"]: n for n in snapshot.get("nodes", [])}
    if any(value not in nodes for value in target_ids):
        raise ProductionRejected("CINE_IMPORT_TARGETS_REQUIRED")
    result = await client.submit_command(
        bound,
        {
            "type": "storyboard.import",
            "commandId": f"cine-import-{uuid.uuid4().hex}",
            "document": storyboard,
            "script": script,
            "summaryNodeId": summary_node_id,
            "episodeNodes": episode_nodes,
            "nodeRevisions": {key: nodes[key]["revision"] for key in target_ids},
        },
    )
    receipt = result.get("response") or {}
    if not result.get("accepted") or receipt.get("verified") is not True:
        raise ProductionRejected("CINE_IMPORT_UNVERIFIED", result)
    if any(
        hashlib.sha256(path.read_bytes()).hexdigest() != value for path, value in hashes.items()
    ):
        raise ProductionRejected("CINE_IMPORT_SOURCE_CHANGED", {"receipt": receipt})

    # Project every storyboard segment to a video_prompt card, keeping cuts,
    # durations and prompts aligned with the storyboard.
    if not segments:
        return {
            "status": "synced",
            "outcome": "succeeded",
            "verified": True,
            "generation_submitted": False,
            "receipt": receipt,
            "next_step": (
                "Storyboard data is synced, not generated or visually approved. "
                "Do not repeat import unless the source changes."
            ),
        }
    preserved: list[str] = []
    conflicts: list[str] = []
    alignment_review: list[dict] = []
    try:
        fresh_snap = await client.get_snapshot(bound)
        curr_nodes = {n["id"]: n for n in fresh_snap.get("nodes", [])}
        edges = {
            (e.get("from"), e.get("to"), e.get("kind"))
            for e in fresh_snap.get("edges") or []
            if isinstance(e, dict)
        }
        video_cards = {
            (n.get("data") or {}).get("segmentId"): n
            for n in curr_nodes.values()
            if n.get("type") == "video_prompt" and (n.get("data") or {}).get("segmentId")
        }
        char_nodes = _asset_nodes(list(curr_nodes.values()), "characterId")
        scene_nodes = _asset_nodes(list(curr_nodes.values()), "sceneId")
        legacy_scene_nodes = _legacy_scene_nodes(list(curr_nodes.values()))
        referenced_scenes = _referenced_scenes(segments, script)
        ambiguous_scene_indexes = _ambiguous_scene_indexes(referenced_scenes)

        # Provision character turnaround cards (16:9 sheet) missing from the canvas.
        for cid in _referenced_characters(segments):
            # The original card, so its production pointer indexes the real file.
            char_def = cast_index.cards.get(cid)
            sheet_prompt = _character_sheet(char_def) if char_def else ""
            if cid in char_nodes:
                if char_def and sheet_prompt:
                    pointer = _image_pointer(cast_doc, "characters", char_def, ("sheet", "prompt"))
                    await _ensure_node_binding(
                        client, bound, char_nodes[cid]["id"], "cast", pointer
                    )
                continue
            if not char_def or not sheet_prompt:
                # Validated above; only reachable if the canvas changed meanwhile.
                raise ProductionRejected(
                    "CINE_IMPORT_ASSETS_MISSING",
                    {
                        "problems": [
                            f"Character {cid} left the canvas during import and has no sheet."
                        ]
                    },
                )
            pointer = _image_pointer(cast_doc, "characters", char_def, ("sheet", "prompt"))
            cname = (char_def or {}).get("name") or cid
            new_cnode, _ = await _create_target(
                client,
                bound,
                node_type="image_prompt",
                title=f"{cname} · 角色三视图",
                data={
                    "characterId": cid,
                    "prompt": sheet_prompt,
                    "brief": (char_def or {}).get("oneLiner", ""),
                    "aspectRatio": "16:9",
                    "generationKind": "image",
                    "productionStage": "cast",
                    "production_stage": "cast",
                    "production_pointer": pointer,
                    "role": "character_sheet",
                },
            )
            char_nodes[cid] = new_cnode

        # Provision scene concept cards missing from the canvas.
        for sid, s_idx in referenced_scenes.items():
            scene_def = _scene_entry(art_doc, sid, s_idx)
            sprompt = _scene_prompt(scene_def) if scene_def else ""
            existing_scene = _scene_node(
                sid, s_idx, scene_nodes, legacy_scene_nodes, ambiguous_scene_indexes
            )
            if existing_scene:
                if scene_def and sprompt:
                    pointer = _image_pointer(art_doc, "scenes", scene_def, ("prompt",))
                    await _ensure_node_binding(client, bound, existing_scene["id"], "art", pointer)
                continue
            if not scene_def or not sprompt:
                raise ProductionRejected(
                    "CINE_IMPORT_ASSETS_MISSING",
                    {
                        "problems": [
                            f"Scene {sid} left the canvas during import and has no prompt."
                        ]
                    },
                )
            pointer = _image_pointer(art_doc, "scenes", scene_def, ("prompt",))
            sname = scene_def.get("name") or sid
            new_snode, _ = await _create_target(
                client,
                bound,
                node_type="image_prompt",
                title=f"{sname} · 场景概念图",
                data={
                    "sceneId": sid,
                    "sceneIndex": s_idx,
                    "prompt": sprompt,
                    "brief": (scene_def or {}).get("brief", ""),
                    "aspectRatio": "16:9",
                    "generationKind": "image",
                    "productionStage": "art",
                    "production_stage": "art",
                    "production_pointer": pointer,
                    "role": "scene_art",
                },
            )
            scene_nodes[sid] = new_snode

        prompts_aligned = 0
        if subject_judge is None:
            subject_judge = _default_subject_judge
        for ep_index, ep in enumerate(storyboard.get("episodes", [])):
            # Each episode's cards derive from its own storyboard node and form
            # their own sequence; nothing chains across episode boundaries.
            sb_node_id = episode_nodes.get(str(ep.get("ep")))
            prev_card_id = None
            for seg_index, seg in enumerate(ep.get("segments", [])):
                seg_id = seg.get("id")
                if not seg_id:
                    continue
                pointer = f"/episodes/{ep_index}/segments/{seg_index}/h3Prompt"
                cuts = seg.get("cuts", [])
                dur_sec = round(sum(c.get("seconds", 0) for c in cuts), 1)
                h3_prompt = seg.get("h3Prompt", "")
                structured_shots = build_structured_shots(seg, script, ep.get("ep"))
                brief = (
                    seg.get("brief")
                    or (
                        cuts[0].get("description") or cuts[0].get("frame") or f"{seg_id} 生成段"
                        if cuts
                        else f"{seg_id} 生成段"
                    )
                ).strip()
                clean_title = f"{seg_id} · {brief[:20]}".strip()
                card_data = {
                    "segmentId": seg_id,
                    "shot": seg_id,
                    "shotMode": (
                        "multi-shot-container" if len(structured_shots) > 1 else "single-take"
                    ),
                    "shotCount": len(structured_shots),
                    "durationSec": dur_sec,
                    "prompt": h3_prompt,
                    "brief": brief,
                    "shots": structured_shots,
                    "promptProvenance": None,
                    "production_stage": "storyboard",
                    "production_pointer": pointer,
                }

                existing = video_cards.get(seg_id)
                outcome = "created"
                if existing:
                    card_id = existing["id"]
                    outcome = await _update_existing_card(
                        client, bound, card_id, clean_title, card_data, h3_prompt
                    )
                    if outcome == "preserved":
                        preserved.append(seg_id)
                        await _ensure_node_binding(client, bound, card_id, "storyboard", pointer)
                    elif outcome in ("conflict", "missing"):
                        conflicts.append(seg_id)
                    if outcome == "missing":
                        continue
                else:
                    new_card, _ = await _create_target(
                        client,
                        bound,
                        node_type="video_prompt",
                        title=clean_title,
                        data={
                            **card_data,
                            IMPORTED_PROMPT_KEY: _sha256_text(h3_prompt),
                            "aspectRatio": "16:9",
                            "model": "minimax-h3-autodl-lightx2v-v5-15s",
                            "submissionModel": "minimax_h3_lightx2v_v5_15s",
                            "provider": "autodl-comfy",
                            "size": "768p",
                        },
                    )
                    card_id = new_card["id"]

                if sb_node_id:
                    await _connect_once(client, bound, edges, sb_node_id, card_id, "derives", "vp")
                segment_characters = dict.fromkeys(
                    name for cut in cuts for name in cut.get("characters") or [] if name
                )
                for cid in segment_characters:
                    cnode = char_nodes.get(cid)
                    if cnode:
                        await _connect_once(
                            client, bound, edges, cnode["id"], card_id, "references", f"c-{cid}"
                        )
                s_idx = seg.get("sceneIndex")
                sc_id = _resolve_scene_id(script, ep.get("ep"), s_idx) if s_idx else None
                snode = _scene_node(
                    sc_id, s_idx, scene_nodes, legacy_scene_nodes, ambiguous_scene_indexes
                )
                if snode:
                    await _connect_once(
                        client,
                        bound,
                        edges,
                        snode["id"],
                        card_id,
                        "references",
                        f"s-{sc_id or s_idx}",
                    )
                if prev_card_id:
                    await _connect_once(
                        client, bound, edges, prev_card_id, card_id, "sequence", f"seq-{seg_id}"
                    )
                prev_card_id = card_id

                # Read actual canvas references and align H3 prompt <Picture N> tags
                # strictly with canvas
                if outcome not in ("preserved", "conflict") and hasattr(
                    client, "get_node_references"
                ):
                    actual_refs = await client.get_node_references(bound, card_id)
                    if isinstance(actual_refs, list) and actual_refs:
                        current_prompt = card_data.get("prompt", "")
                        alignment_issues: list[str] = []
                        aligned_prompt = await align_prompt_with_references(
                            current_prompt,
                            actual_refs,
                            cast_view,
                            art_doc,
                            ref_entities=_reference_entities(
                                [
                                    *curr_nodes.values(),
                                    *char_nodes.values(),
                                    *scene_nodes.values(),
                                ],
                                referenced_scenes,
                                legacy_scene_nodes,
                                ambiguous_scene_indexes,
                            ),
                            judge=subject_judge,
                            issues=alignment_issues,
                        )
                        if alignment_issues:
                            alignment_review.append(
                                {"segmentId": seg_id, "issues": alignment_issues}
                            )
                        if aligned_prompt and aligned_prompt != current_prompt:
                            current_snapshot = await client.get_snapshot(bound)
                            current_node = next(
                                (
                                    n
                                    for n in current_snapshot.get("nodes", [])
                                    if n.get("id") == card_id
                                ),
                                None,
                            )
                            if current_node is None:
                                raise ProductionRejected("CINE_IMPORT_ALIGNMENT_NODE_MISSING")
                            command = {
                                "type": "canvas.update_node",
                                "nodeId": card_id,
                                "patch": {
                                    "data": {
                                        **(current_node.get("data") or {}),
                                        "prompt": aligned_prompt,
                                        IMPORTED_PROMPT_KEY: _sha256_text(aligned_prompt),
                                    }
                                },
                                "commandId": f"cine-align-vp-{uuid.uuid4().hex[:8]}",
                            }
                            if current_snapshot.get("revision") is not None:
                                command["expectedRevision"] = current_snapshot["revision"]
                            update = await client.submit_command(bound, command)
                            if not update.get("accepted"):
                                raise ProductionRejected("CINE_IMPORT_ALIGNMENT_REJECTED", update)
                            seg["h3Prompt"] = aligned_prompt
                            prompts_aligned += 1

        if prompts_aligned > 0 and storyboard_path.is_file():
            if hashlib.sha256(storyboard_path.read_bytes()).hexdigest() != hashes[storyboard_path]:
                raise ProductionRejected("CINE_IMPORT_SOURCE_CHANGED")
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=storyboard_path.parent, delete=False
            ) as file:
                temp = Path(file.name)
                file.write(json.dumps(storyboard, ensure_ascii=False, indent=2) + "\n")
            try:
                os.replace(temp, storyboard_path)
            finally:
                temp.unlink(missing_ok=True)
            hashes[storyboard_path] = hashlib.sha256(storyboard_path.read_bytes()).hexdigest()
            current_snapshot = await client.get_snapshot(bound)
            current_nodes = {n["id"]: n for n in current_snapshot.get("nodes", [])}
            if any(node_id not in current_nodes for node_id in target_ids):
                raise ProductionRejected("CINE_IMPORT_TARGETS_REQUIRED")
            final = await client.submit_command(
                bound,
                {
                    "type": "storyboard.import",
                    "commandId": f"cine-import-aligned-{uuid.uuid4().hex}",
                    "document": storyboard,
                    "script": script,
                    "summaryNodeId": summary_node_id,
                    "episodeNodes": episode_nodes,
                    "nodeRevisions": {
                        node_id: current_nodes[node_id]["revision"] for node_id in target_ids
                    },
                },
            )
            receipt = final.get("response") or {}
            if not final.get("accepted") or receipt.get("verified") is not True:
                raise ProductionRejected("CINE_IMPORT_UNVERIFIED", final)
            if any(
                hashlib.sha256(path.read_bytes()).hexdigest() != value
                for path, value in hashes.items()
            ):
                raise ProductionRejected("CINE_IMPORT_SOURCE_CHANGED", {"receipt": receipt})
    except (ProductionRejected, KeyError, OSError, TypeError, ValueError) as exc:
        logger.exception("Failed to auto-project video prompt nodes during storyboard import")
        detail = {"error": str(exc)}
        if isinstance(exc, ProductionRejected) and exc.detail:
            detail["detail"] = exc.detail
        raise ProductionRejected("CINE_IMPORT_VIDEO_CARD_PROJECTION_FAILED", detail) from exc

    next_step = (
        "Storyboard data and visual asset cards are synced on canvas "
        f"({len(char_nodes)} character sheets, {len(scene_nodes)} scene concept cards "
        "linked as references). Visual character sheets must be reviewed before submitting "
        "video generation to ensure character consistency across cuts."
    )
    if preserved:
        next_step += (
            " Left unchanged because their prompts were edited on the canvas after the last "
            f"import: {', '.join(preserved)}. Compare them with the new storyboard "
            "before generation."
        )
    if conflicts:
        next_step += (
            f" Not updated because the canvas changed during import: {', '.join(conflicts)}. "
            "Re-run the import once the canvas is idle."
        )
    if alignment_review:
        next_step += (
            " Subject/Picture numbering was left as authored because the subjects could not "
            "be matched to canvas references with certainty: "
            f"{', '.join(item['segmentId'] for item in alignment_review)}. Check each "
            "<Subject N> against the card's reference order before generation."
        )
    return {
        "status": "synced",
        "outcome": "succeeded",
        "verified": True,
        "generation_submitted": False,
        "receipt": {
            **receipt,
            "character_assets_linked": len(char_nodes),
            "scene_assets_linked": len(scene_nodes),
            "preserved_edited_cards": preserved,
            "update_conflicts": conflicts,
            "subject_alignment_review": alignment_review,
        },
        "next_step": next_step,
    }
