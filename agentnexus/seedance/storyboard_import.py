"""Send native creative artifacts to V3's deterministic storyboard importer."""

import hashlib
import json
import logging
import os
import re
import tempfile
import uuid
from pathlib import Path
from typing import Any

from .asset_anchors import (  # noqa: F401 - re-exported for callers and tests
    CastIndex,
    _ambiguous_scene_indexes,
    _asset_nodes,
    _card_label,
    _character_entry,
    _character_sheet,
    _character_state,
    _legacy_scene_nodes,
    _missing_character,
    _missing_visual_assets,
    _needs_outline,
    _referenced_characters,
    _referenced_scenes,
    _referenced_states,
    _resolve_cast,
    _resolve_scene_id,
    _scene_entry,
    _scene_node,
    _scene_prompt,
    _segment_character_states,
    _state_nodes,
    _word,
)
from .client import SeedanceError
from .jev import ask_system_one
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


def _cast_view(index: CastIndex) -> dict:
    """The resolved cards under their outline ids, for matching only. Never written."""
    return {"characters": [{**card, "id": cid} for cid, card in index.cards.items()]}


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


def _image_pointer(document: dict, collection: str, entry: dict, fields: tuple[str, ...]) -> str:
    for index, candidate in enumerate(document.get(collection, [])):
        if candidate is entry:
            image = entry.get("image") if isinstance(entry.get("image"), dict) else {}
            for field in fields:
                if str(image.get(field) or "").strip():
                    return f"/{collection}/{index}/image/{field}"
    raise ProductionRejected("CINE_IMPORT_ASSETS_MISSING")


def _state_pointer(document: dict, card: dict, state: dict) -> str:
    """Production pointer of a costume state's sheet inside its character card."""
    for c_index, candidate in enumerate(document.get("characters", [])):
        if candidate is not card:
            continue
        for s_index, row in enumerate(card.get("states") or []):
            if row is state:
                image = state.get("image") if isinstance(state.get("image"), dict) else {}
                field = "sheet" if str(image.get("sheet") or "").strip() else "prompt"
                return f"/characters/{c_index}/states/{s_index}/image/{field}"
    raise ProductionRejected("CINE_IMPORT_ASSETS_MISSING")


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
    referenced_text = prompt[subj_start:]
    for token in _SUBJECT_TOKEN.findall(referenced_text):
        if int(token) not in subject_map:
            problems.append(f"<Subject {token}> is used but not defined.")
    for token in _PICTURE_TOKEN.findall(referenced_text):
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
        elif kind == "frame":
            desc = (
                f"{ident}, the last frame of the previous segment; positions, framing and "
                f"light at the opening come entirely from <Picture {idx}>."
            )
        else:
            clean_lbl = re.sub(r"[一-鿿]", "", label).strip(" ·-_")
            desc = (
                f"{clean_lbl or 'continuation frame from preceding shot'}; visual composition "
                f"and action carry come entirely from <Picture {idx}>."
            )
        lines.append(f"<Subject {idx}> — {desc}")

    # Standalone frame, video and audio definitions retain their authored roles.
    for line in prompt[subj_start + len("subject_definitions:") : summary_start].splitlines():
        if line.strip() and not _SUBJECT_LINE.match(line.lstrip()):
            lines.append(renumber(line))

    rest = renumber(rest)
    retention = re.search(r"retention_analysis:([\s\S]*?)(?=\ndetailed_description:|\Z)", rest)
    frame_entries = [
        f"<Subject {idx}> ([Shot 1] opening frame): fully_preserved - positions, framing "
        "and light at the opening carry from the preceding segment's last frame."
        for idx, kind, _ident, _label in extra
        if kind == "frame"
    ]
    if retention and frame_entries:
        end = retention.end()
        separator = "" if rest[:end].endswith("\n") else "\n"
        rest = rest[:end] + separator + "\n".join(frame_entries) + rest[end:]
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
    """Ask JEV through the runner's TypeSafe client."""
    return await ask_system_one(state, questions)


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
    frame_ids: tuple[str, ...] = (),
) -> str:
    """Align a prompt, asking JEV about subjects the project documents cannot name.

    ``frame_ids`` are the segments whose last frame this card continues from: a
    subject that names one of them is that frame (``("frame", id)``), not a
    character or scene.
    """
    issues = issues if issues is not None else []
    subjects = _subject_definitions(prompt)
    if not subjects:
        images = [r for r in actual_refs if (r.get("kind") or "image") == "image"]
        if images:
            # Without subject definitions the references are unnamed: a keyframe
            # alignment line would pass a character sheet off as a shot's frame.
            labels = ", ".join(str(r.get("label") or r.get("id") or "?") for r in images)
            issues.append(
                f"The prompt defines no <Subject N>, so none of the card's {len(images)} "
                f"reference images ({labels}) is tied to it; rewrite it in Ref2VA form."
            )
        return prompt
    matched: dict[int, tuple[str, str]] = {}
    unresolved = {}
    for num, desc in subjects.items():
        frame = next((f for f in frame_ids if _term_pattern(f.lower()).search(desc.lower())), None)
        if frame:
            matched[num] = ("frame", frame)
            continue
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


_IMAGE_NODE_TYPES = ("image", "image_prompt", "lookdev")


def planned_image_references(snapshot: dict, card_id: str) -> list[dict]:
    """The image references a video card will generate with, once every source has its image.

    Seedance numbers <Picture N> in canvas node order (not edge order) and lists only
    sources that already have media, so before the sheets are generated it reports
    none. This is the same selection without the media requirement.
    """
    nodes = [n for n in snapshot.get("nodes") or [] if isinstance(n, dict)]
    card = next((n for n in nodes if n.get("id") == card_id), {})
    data = card.get("data") if isinstance(card.get("data"), dict) else {}
    excluded = {i for i in data.get("excludedReferenceIds") or [] if isinstance(i, str)}
    sources = {
        e.get("from") or e.get("source")
        for e in snapshot.get("edges") or []
        if isinstance(e, dict) and card_id in (e.get("to"), e.get("target"))
    }
    planned: list[dict] = []
    for node in nodes:
        ref_id = f"node:{node.get('id')}"
        label = str(node.get("title") or "")
        if (
            node.get("id") == card_id
            or node.get("id") not in sources
            or node.get("type") not in _IMAGE_NODE_TYPES
            or ref_id in excluded
            or any(p["label"] == label for p in planned)  # Seedance keeps one per label
        ):
            continue
        planned.append({"id": ref_id, "label": label, "kind": "image"})
    return planned


def _references_for_alignment(actual: list[dict], planned: list[dict]) -> list[dict]:
    """Align against the planned order while some connected sheet has no image yet."""
    images = [r for r in actual if (r.get("kind") or "image") == "image"]
    if len(images) >= len(planned):
        return actual
    return [*planned, *(r for r in actual if (r.get("kind") or "image") != "image")]


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


def _position(node: dict) -> tuple[float, float]:
    position = (node.get("data") or {}).get("position") or node.get("position") or {}
    return float(position.get("x") or 0), float(position.get("y") or 0)


async def _continuation_frame(
    client, project_id, previous: dict, card_id: str
) -> tuple[dict, dict]:
    """The image node that will hold ``previous``'s last frame for this card.

    Seedance fills a node carrying these two ids when ``previous`` finishes
    generating, instead of making a new one; creating it now fixes the frame's
    place among the card's references, so its <Picture N> is known before it exists.
    Returns the node and the latest snapshot.
    """
    snapshot = await client.get_snapshot(project_id)
    for node in snapshot.get("nodes", []):
        data = node.get("data") or {}
        if (
            node.get("type") == "image"
            and data.get("continuationSourceNodeId") == previous["id"]
            and data.get("continuationTargetNodeId") == card_id
        ):
            return node, snapshot
    card = next((n for n in snapshot.get("nodes", []) if n.get("id") == card_id), {})
    (px, py), (cx, cy) = _position(previous), _position(card)
    title = f"{(previous.get('title') or '').strip()} 尾帧"[:120]
    return await _create_target(
        client,
        project_id,
        node_type="image",
        title=title,
        data={
            "position": {"x": round((px + cx) / 2 - 160), "y": round(min(py, cy) - 80)},
            "description": "等上一段生成后自动填入它的最后一帧，这一段从这一帧接着拍。",
            "continuationSourceNodeId": previous["id"],
            "continuationTargetNodeId": card_id,
        },
    )


def _asset_id(node: dict) -> str | None:
    """The character or scene an importer-made asset card holds, else None."""
    data = node.get("data") or {}
    return data.get("characterId") or data.get("sceneId") or None


async def _exclude_references(client, project_id, card_id, add: set, remove: set) -> None:
    """Keep references this card no longer uses out of it: stray last frames, moved assets."""
    snapshot = await client.get_snapshot(project_id)
    node = next((n for n in snapshot.get("nodes", []) if n.get("id") == card_id), None)
    if node is None:
        return
    data = node.get("data") or {}
    current = [i for i in data.get("excludedReferenceIds") or [] if isinstance(i, str)]
    wanted = [i for i in current if i not in remove] + sorted(add - set(current))
    if wanted == current:
        return
    command = {
        "type": "canvas.update_node",
        "nodeId": card_id,
        "patch": {"data": {**data, "excludedReferenceIds": wanted}},
        "commandId": f"cine-frame-refs-{uuid.uuid4().hex[:8]}",
    }
    if snapshot.get("revision") is not None:
        command["expectedRevision"] = snapshot["revision"]
    result = await client.submit_command(project_id, command)
    if not result.get("accepted"):
        raise ProductionRejected("CINE_IMPORT_FRAME_REFERENCES_REJECTED", {"node_id": card_id})


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


RETIRED_TITLE_PREFIX = "[已作废] "


def _media_index(snapshot: dict) -> dict[str, dict] | None:
    entries = snapshot.get("node_media")
    if not isinstance(entries, list):
        return None
    return {m["nodeId"]: m for m in entries if isinstance(m, dict) and m.get("nodeId")}


def _holds_user_work(node: dict, media: dict[str, dict] | None) -> bool:
    """Whether a card holds generated media (even hidden), any job, or a prompt edited by hand."""
    data = node.get("data") or {}
    if data.get("activeOutputRef") or data.get("assetRef"):
        return True
    if node.get("type") == "video_prompt" and _prompt_edited_on_canvas(data):
        return True
    state = (media or {}).get(str(node.get("id")))
    if state is None:
        return True  # the server did not report this card's media: never delete blind
    return bool(
        state.get("outputUrl")
        or state.get("latestJob")
        or state.get("pendingJobCount")
        or state.get("mediaState") not in (None, "no_output")
    )


def _stale_storyboard_nodes(
    snapshot: dict, kept: set, boards: set, used_frames: set
) -> tuple[list[dict], list[dict]]:
    """Imported cards whose segment left the storyboard, and last-frame slots no segment uses."""
    derived = {
        e.get("to")
        for e in snapshot.get("edges") or []
        if isinstance(e, dict) and e.get("kind") == "derives" and e.get("from") in boards
    }
    imported = [
        n
        for n in snapshot.get("nodes", [])
        if n.get("type") == "video_prompt"
        and n.get("id") in derived
        and (n.get("data") or {}).get("production_stage") == "storyboard"
    ]
    cards = [
        n for n in imported if (n.get("data") or {}).get("segmentId") not in (None, "", *kept)
    ]
    removed = {n["id"] for n in cards}
    targets = {n["id"] for n in imported}
    slots = [
        n
        for n in snapshot.get("nodes", [])
        if n.get("type") == "image"
        and n.get("id") not in used_frames
        and (
            (n.get("data") or {}).get("continuationTargetNodeId") in targets
            or (n.get("data") or {}).get("continuationSourceNodeId") in removed
        )
    ]
    return cards, slots


async def _retire_removed_segments(
    client, project_id, storyboard: dict, episode_nodes: dict, used_frames: set
) -> dict[str, list[str]]:
    """Clear what a changed storyboard left behind without losing anything generated.

    Cards of segments that are gone and last-frame slots no segment uses are deleted
    when empty; a card holding media or a hand edit is renamed 已作废 and unbound from
    the storyboard instead, and a filled last frame stays as it is.
    """
    kept = {
        seg.get("id") for ep in storyboard.get("episodes", []) for seg in ep.get("segments", [])
    }
    boards = {str(node_id) for node_id in episode_nodes.values() if node_id}
    cards, slots = _stale_storyboard_nodes(
        await client.get_snapshot(project_id), kept, boards, used_frames
    )
    report: dict[str, list[str]] = {"removed": [], "retired": [], "frames": [], "conflicts": []}
    for node in [*slots, *cards]:
        latest = await client.get_snapshot(project_id)
        current = next((n for n in latest.get("nodes", []) if n.get("id") == node["id"]), None)
        if current is None:
            continue
        is_card = current.get("type") == "video_prompt"
        data = current.get("data") or {}
        label = str(data.get("segmentId") if is_card else current.get("title") or current["id"])
        if not _holds_user_work(current, _media_index(latest)):
            command = {"type": "canvas.delete_node", "nodeId": current["id"]}
            outcome = "removed" if is_card else "frames"
        elif is_card:
            title = str(current.get("title") or label)
            unbound = {
                k: v
                for k, v in data.items()
                if k not in ("production_stage", "production_pointer")
            }
            command = {
                "type": "canvas.update_node",
                "nodeId": current["id"],
                "patch": {
                    "title": title
                    if title.startswith(RETIRED_TITLE_PREFIX)
                    else RETIRED_TITLE_PREFIX + title,
                    "data": {**unbound, "retired": True},
                },
            }
            outcome = "retired"
        else:
            continue  # a filled last frame is an output and stays as it is
        command["commandId"] = f"cine-prune-{uuid.uuid4().hex[:8]}"
        if latest.get("revision") is not None:
            command["expectedRevision"] = latest["revision"]
        try:
            result = await client.submit_command(project_id, command)
        except SeedanceError as exc:
            if exc.code == "CANVAS_NODE_NOT_FOUND":
                continue
            if exc.code != "REVISION_CONFLICT":
                raise
            result = {"accepted": False}
        report[outcome if result.get("accepted") else "conflicts"].append(label)
        if outcome == "frames" and result.get("accepted"):
            # The card it pointed at may stay; its exclusion of the slot is now moot.
            target = data.get("continuationTargetNodeId")
            await _exclude_references(client, project_id, target, set(), {f"node:{node['id']}"})
    return report


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
    by id, name or alias; it defaults to the runner's TypeSafe client.
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
            if n.get("type") == "video_prompt"
            and (n.get("data") or {}).get("segmentId")
            and not (n.get("data") or {}).get("retired")
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

        # Provision a sheet card for each costume state on screen. It references the
        # main sheet, so the generated state keeps that face.
        state_nodes = _state_nodes(list(curr_nodes.values()))
        referenced_states = _referenced_states(segments)
        for cid, state_id in referenced_states:
            char_def = cast_index.cards.get(cid)
            state_def = _character_state(char_def, state_id) if char_def else None
            node = state_nodes.get((cid, state_id))
            if char_def and state_def and _character_sheet(state_def):
                pointer = _state_pointer(cast_doc, char_def, state_def)
                if node is None:
                    label = str(state_def.get("label") or state_id).strip()
                    node, _ = await _create_target(
                        client,
                        bound,
                        node_type="image_prompt",
                        title=f"{char_def.get('name') or cid} · {label[:24]} · 状态三视图",
                        data={
                            "characterId": cid,
                            "characterState": state_id,
                            "prompt": _character_sheet(state_def),
                            "brief": label,
                            "aspectRatio": "16:9",
                            "generationKind": "image",
                            "productionStage": "cast",
                            "production_stage": "cast",
                            "production_pointer": pointer,
                            "role": "character_sheet",
                        },
                    )
                    state_nodes[(cid, state_id)] = node
                else:
                    await _ensure_node_binding(client, bound, node["id"], "cast", pointer)
            elif node is None:
                # Validated above; only reachable if the canvas changed meanwhile.
                raise ProductionRejected(
                    "CINE_IMPORT_ASSETS_MISSING",
                    {"problems": [f"Character {cid} state {state_id} has no sheet."]},
                )
            if cid in char_nodes:
                await _connect_once(
                    client,
                    bound,
                    edges,
                    char_nodes[cid]["id"],
                    node["id"],
                    "references",
                    f"cs-{cid}",
                )

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
        used_frames: set[str] = set()
        if subject_judge is None:
            subject_judge = _default_subject_judge
        for ep_index, ep in enumerate(storyboard.get("episodes", [])):
            # Each episode's cards derive from its own storyboard node and form
            # their own sequence; nothing chains across episode boundaries.
            sb_node_id = episode_nodes.get(str(ep.get("ep")))
            prev_card_id = prev_seg_id = None
            for seg_index, seg in enumerate(ep.get("segments", [])):
                seg_id = seg.get("id")
                if not seg_id:
                    continue
                # Picks up from the previous segment's last frame, which Seedance
                # attaches to this card once that segment is generated.
                continues = bool(prev_card_id) and seg.get("continuesFrom") == prev_seg_id
                continued_from = prev_seg_id if continues else None
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
                    "continuesFrom": continued_from,
                    # Seedance attaches the previous segment's last frame only when true.
                    "lastFrameContinuation": continues,
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
                # Each character is referenced through the sheet of the costume worn here.
                segment_assets: dict[str, str] = {}
                for cid, states in _segment_character_states(seg).items():
                    for state_id in states:
                        cnode = (
                            char_nodes.get(cid)
                            if state_id == "default"
                            else state_nodes.get((cid, state_id))
                        )
                        if cnode:
                            segment_assets[cnode["id"]] = cid
                for node_id, cid in segment_assets.items():
                    await _connect_once(
                        client, bound, edges, node_id, card_id, "references", f"c-{cid}"
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
                frame_entities: dict[str, tuple[str, str]] = {}
                frame_ref = None
                if continues:
                    previous = await client.get_snapshot(bound)
                    prev_node = next(
                        (n for n in previous.get("nodes", []) if n.get("id") == prev_card_id), None
                    )
                    if prev_node is None:
                        raise ProductionRejected(
                            "CINE_IMPORT_PREVIOUS_CARD_MISSING", {"segment": seg_id}
                        )
                    frame, _ = await _continuation_frame(client, bound, prev_node, card_id)
                    await _connect_once(
                        client, bound, edges, frame["id"], card_id, "references", f"lf-{seg_id}"
                    )
                    frame_ref = f"node:{frame['id']}"
                    used_frames.add(frame["id"])
                    frame_entities = {frame_ref: ("frame", str(continued_from))}
                wanted = set(segment_assets) | ({snode["id"]} if snode else set())
                snapshot = await client.get_snapshot(bound)
                linked = {
                    e.get("from")
                    for e in snapshot.get("edges") or []
                    if isinstance(e, dict)
                    and e.get("to") == card_id
                    and e.get("kind") == "references"
                }
                # A re-import that moved a segment leaves its old cast, scene and
                # frame edges behind; only the importer's own asset cards are dropped.
                stale = {
                    f"node:{n['id']}"
                    for n in snapshot.get("nodes", [])
                    if f"node:{n['id']}" != frame_ref
                    and (
                        (
                            n.get("type") == "image"
                            and (n.get("data") or {}).get("continuationTargetNodeId") == card_id
                        )
                        or (
                            n["id"] in linked
                            and n["id"] not in wanted
                            and _asset_id(n) is not None
                        )
                    )
                }
                await _exclude_references(
                    client,
                    bound,
                    card_id,
                    stale,
                    {f"node:{i}" for i in wanted} | ({frame_ref} if frame_ref else set()),
                )
                prev_card_id, prev_seg_id = card_id, seg_id

                # Read actual canvas references and align H3 prompt <Picture N> tags
                # strictly with canvas
                if outcome not in ("preserved", "conflict") and hasattr(
                    client, "get_node_references"
                ):
                    actual_refs = await client.get_node_references(bound, card_id)
                    if isinstance(actual_refs, list):
                        actual_refs = _references_for_alignment(
                            actual_refs,
                            planned_image_references(await client.get_snapshot(bound), card_id),
                        )
                    if isinstance(actual_refs, list) and actual_refs:
                        current_prompt = card_data.get("prompt", "")
                        alignment_issues: list[str] = []
                        aligned_prompt = await align_prompt_with_references(
                            current_prompt,
                            actual_refs,
                            cast_view,
                            art_doc,
                            ref_entities={
                                **_reference_entities(
                                    [
                                        *curr_nodes.values(),
                                        *char_nodes.values(),
                                        *state_nodes.values(),
                                        *scene_nodes.values(),
                                    ],
                                    referenced_scenes,
                                    legacy_scene_nodes,
                                    ambiguous_scene_indexes,
                                ),
                                **frame_entities,
                            },
                            judge=subject_judge,
                            issues=alignment_issues,
                            frame_ids=(continued_from,) if continued_from else (),
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

        pruned = await _retire_removed_segments(
            client, bound, storyboard, episode_nodes, used_frames
        )
        conflicts.extend(pruned["conflicts"])

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
    if referenced_states:
        next_step += (
            f" {len(referenced_states)} costume-state sheet card(s) reference their character's "
            "main sheet; generate the main sheet first."
        )
    if preserved:
        next_step += (
            " Left unchanged because their prompts were edited on the canvas after the last "
            f"import: {', '.join(preserved)}. Compare them with the new storyboard "
            "before generation."
        )
    if pruned["removed"]:
        next_step += (
            " Deleted the cards of segments the storyboard no longer has; nothing had been "
            f"generated on them: {', '.join(pruned['removed'])}."
        )
    if pruned["frames"]:
        next_step += (
            f" Deleted {len(pruned['frames'])} empty last-frame slot(s) that no segment "
            "continues from any more."
        )
    if pruned["retired"]:
        next_step += (
            f" Renamed {RETIRED_TITLE_PREFIX.strip()} and unbound, not deleted, because they hold "
            "generated media or canvas edits while their segments left the storyboard: "
            f"{', '.join(pruned['retired'])}."
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
            "character_state_assets_linked": len(referenced_states),
            "scene_assets_linked": len(scene_nodes),
            "preserved_edited_cards": preserved,
            "removed_stale_cards": pruned["removed"],
            "removed_stale_frames": len(pruned["frames"]),
            "retired_stale_cards": pruned["retired"],
            "update_conflicts": conflicts,
            "subject_alignment_review": alignment_review,
        },
        "next_step": next_step,
    }
