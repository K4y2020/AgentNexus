"""Send native creative artifacts to V3's deterministic storyboard importer."""

import hashlib
import json
import logging
import re
import uuid
from pathlib import Path
from typing import Any

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
    return next(
        (
            s
            for s in art_doc.get("scenes", [])
            if isinstance(s, dict)
            and (
                (scene_id and s.get("id") == scene_id)
                or (scene_index is not None and s.get("sceneIndex") == scene_index)
            )
        ),
        None,
    )


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
    segments, nodes, cast, art, script_doc: dict | None = None
) -> list[str]:
    """List every character/scene that is neither on the canvas nor fully defined in cast/art."""
    (cast_path, cast_doc), (art_path, art_doc) = cast, art
    cast_name = cast_path.name if cast_path else "cast.json (not found)"
    art_name = art_path.name if art_path else "art.json (not found)"
    on_canvas_characters = _asset_nodes(nodes, "characterId")
    on_canvas_scenes = {
        **_asset_nodes(nodes, "sceneId"),
        **_asset_nodes(nodes, "sceneIndex"),
    }
    problems = []
    for cid in _referenced_characters(segments):
        if cid in on_canvas_characters:
            continue
        entry = _character_entry(cast_doc, cid)
        if entry is None:
            problems.append(
                f"Character {cid} is used in the storyboard but not defined in {cast_name}."
            )
        elif not _character_sheet(entry):
            problems.append(
                f"Character {cid} ({entry.get('name', '')}) has no image.sheet in {cast_name}."
            )

    ref_scenes = _referenced_scenes(segments, script_doc)
    for sid, scene_index in ref_scenes.items():
        if sid in on_canvas_scenes or scene_index in on_canvas_scenes:
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


def _identify_subject_entity(desc: str, cast_doc: dict, art_doc: dict) -> tuple[str, str | None]:
    """Identify whether a <Subject N> description refers to a character (cid) or a scene (sid)."""
    desc_l = desc.lower()

    # 1. Scene match
    for sc in art_doc.get("scenes", []):
        sid = sc.get("id")
        name = sc.get("name", "")
        candidates = [sid.lower(), name.lower()] if sid else []
        scene_kws = {
            "S01": ["fortune-telling stall", "stall environment", "trestle table", "卦摊"],
            "S02": ["memorial hall", "velvet drapery", "chrysanthemum", "altar", "灵堂"],
            "S03": ["estate gate", "wrought-iron gate", "heraldic emblem", "大门"],
            "S04": ["great hall", "ionic columns", "crystal chandelier", "marble floor", "大厅"],
            "S05": ["vaulted corridor", "corridor environment", "navy runner", "走廊"],
        }
        candidates.extend(scene_kws.get(sid, []))
        if any(kw and kw in desc_l for kw in candidates if len(kw) >= 2):
            return ("scene", sid)

    # 2. Character match (ordered by specific persona descriptors before generic)
    char_kws = [
        ("C09", ["eldest young lady", "yingchun", "迎春"]),
        ("C10", ["second young lady", "tanchun", "探春"]),
        ("C11", ["smallest young lady", "plush rabbit", "xichun", "惜春"]),
        (
            "C08",
            ["household staff", "valet suits", "white cotton gloves", "staff ensemble", "佣人"],
        ),
        (
            "C04",
            [
                "crimson tailored suit",
                "twenty-seven",
                "crocodile tote",
                "wang xifeng",
                "王熙凤",
                "凤姐",
            ],
        ),
        (
            "C03",
            [
                "white-haired matriarch",
                "seventy-five",
                "deep-plum",
                "grandmother",
                "jia mu",
                "贾母",
                "外婆",
            ],
        ),
        (
            "C06",
            [
                "fortune teller",
                "sunglasses",
                "bamboo divination cylinder",
                "indigo",
                "sixties",
                "算命",
            ],
        ),
        (
            "C07",
            [
                "scholarly bureaucrat",
                "scholarly man",
                "pinstripe",
                "spectacles",
                "forty-eight",
                "father",
                "林如海",
            ],
        ),
        (
            "C02",
            [
                "emerald-green",
                "emerald jacket",
                "matriarch in her forties",
                "matriarch in her thirties",
                "jia min",
                "贾敏",
            ],
        ),
        (
            "C01",
            [
                "eighteen-year-old",
                "charcoal wool blazer",
                "pearl-white",
                "slender",
                "heiress",
                "lin daiyu",
                "林黛玉",
                "黛玉",
                "mourning dress",
                "young woman of eighteen",
            ],
        ),
    ]
    for cid, kws in char_kws:
        if any(kw in desc_l for kw in kws):
            return ("char", cid)

    for c in cast_doc.get("characters", []):
        cid = c.get("id")
        name = c.get("name", "")
        aliases = c.get("aliases", [])
        if name and name.lower() in desc_l:
            return ("char", cid)
        if any(a and a.lower() in desc_l for a in aliases):
            return ("char", cid)

    return ("unknown", None)


def align_h3_prompt_to_references(
    prompt: str,
    actual_refs: list[dict],
    cast_doc: dict,
    art_doc: dict,
) -> str:
    """Align <Subject N> and <Picture N> in an H3 Ref2VA prompt with canvas references.

    The order of reference pictures on the canvas is the physical source of truth
    for the video diffusion model: <Picture 1> maps to the 1st reference image,
    <Picture 2> to the 2nd, etc. If the prompt was authored in a vacuum with an
    inconsistent order, this re-aligns the declarations so the character/environment
    definitions 100% strictly match their canvas inputs.
    """
    if not prompt or "subject_definitions:" not in prompt:
        return prompt
    subj_start = prompt.find("subject_definitions:")
    summary_start = prompt.find("summary:")
    if subj_start < 0 or summary_start < 0:
        return prompt

    image_refs = [r for r in actual_refs if (r.get("kind") or "image") == "image"]
    if not image_refs:
        return prompt

    subj_section = prompt[subj_start:summary_start]
    subj_defs = re.findall(r"<Subject (\d+)> — ([^\n]+)", subj_section)
    if not subj_defs:
        return prompt

    old_subj_info: dict[int, dict[str, Any]] = {}
    for num_str, raw_desc in subj_defs:
        num = int(num_str)
        kind, ident = _identify_subject_entity(raw_desc, cast_doc, art_doc)
        clean_desc = (
            re.sub(
                r";\s*(?:face|layout|uniform|uniforms|costume|materials|hair|bearing|posture|glasses|cylinder|gloved hands)[^.]*from\s*<Picture\s*\d+>\.?",
                "",
                raw_desc,
                flags=re.I,
            )
            .strip()
            .rstrip(";")
            .rstrip(".")
        )
        old_subj_info[num] = {"kind": kind, "ident": ident, "desc": clean_desc, "raw": raw_desc}

    # Identify what entity each canvas image reference represents
    canvas_entities: list[tuple[int, str, str, str]] = []
    char_entries = {c["id"]: c for c in cast_doc.get("characters", []) if isinstance(c, dict)}
    scene_entries = {s["id"]: s for s in art_doc.get("scenes", []) if isinstance(s, dict)}

    for idx, ref in enumerate(image_refs, 1):
        label = str(ref.get("label") or "")
        ref_id = str(ref.get("id") or "")
        matched = False
        for cid, c in char_entries.items():
            cname = c.get("name") or cid
            if cid in ref_id or cname in label or any(a in label for a in c.get("aliases", [])):
                canvas_entities.append((idx, "char", cid, label))
                matched = True
                break
        if matched:
            continue
        for sid, s in scene_entries.items():
            sname = s.get("name") or sid
            if sid in ref_id or sname in label:
                canvas_entities.append((idx, "scene", sid, label))
                matched = True
                break
        if not matched:
            canvas_entities.append((idx, "unknown", label, label))

    # Build old_subject_num -> new_subject_num mapping
    old_to_new: dict[int, int] = {}
    new_to_old: dict[int, int] = {}
    for new_idx, kind, ident, label in canvas_entities:
        for old_num, info in old_subj_info.items():
            if (info["kind"], info["ident"]) == (kind, ident):
                old_to_new[old_num] = new_idx
                new_to_old[new_idx] = old_num
                break

    # Build new subject_definitions lines
    new_subj_lines = ["subject_definitions:"]
    for new_idx, kind, ident, label in canvas_entities:
        old_num = new_to_old.get(new_idx)
        if old_num and old_num in old_subj_info:
            desc = old_subj_info[old_num]["desc"]
        else:
            if kind == "char" and ident in char_entries:
                c = char_entries[ident]
                desc = f"character {ident}"
            elif kind == "scene" and ident in scene_entries:
                s = scene_entries[ident]
                desc = f"environment {ident}"
            else:
                clean_lbl = re.sub(r"[一-鿿]", "", label).strip(" ·-_")
                desc = clean_lbl or "continuation frame from preceding shot"
        if kind == "scene":
            citation = f"; layout, materials and daylight come entirely from <Picture {new_idx}>."
        elif kind == "char":
            citation = f"; face, hair and costume come entirely from <Picture {new_idx}>."
        else:
            citation = (
                f"; visual composition and action carry come entirely from <Picture {new_idx}>."
            )
        new_subj_lines.append(f"<Subject {new_idx}> — {desc}{citation}")

    new_subj_text = "\n".join(new_subj_lines) + "\n"
    rest = prompt[summary_start:]

    # 1. Update retention_analysis line with all current subjects
    all_subjects_str = ", ".join(f"<Subject {i}>" for i in range(1, len(image_refs) + 1))
    rest = re.sub(
        r"retention_analysis:\s*[^\n]+",
        f"retention_analysis: {all_subjects_str} are retained exactly as referenced — the same faces, costumes, environment and daylight. Only the action, the camera and the timing are new.",
        rest,
    )

    # 2. Token placeholder replace for Subject and Picture
    for old_num, new_idx in old_to_new.items():
        rest = re.sub(rf"<Subject\s*{old_num}>", f"__TEMP_SUBJ_{new_idx}__", rest)
        rest = re.sub(rf"<Picture\s*{old_num}>", f"__TEMP_PIC_{new_idx}__", rest)

    for i in range(1, len(image_refs) + 1):
        rest = rest.replace(f"__TEMP_SUBJ_{i}__", f"<Subject {i}>")
        rest = rest.replace(f"__TEMP_PIC_{i}__", f"<Picture {i}>")

    return new_subj_text + rest


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


async def _connect_once(client, project_id, edges: set, source, target, kind, tag) -> None:
    """Connect two cards unless that edge already exists (re-imports must not duplicate edges)."""
    if (source, target, kind) in edges or (source, target, None) in edges:
        return
    await client.submit_command(
        project_id,
        {
            "type": "canvas.connect",
            "from": source,
            "to": target,
            "kind": kind,
            "commandId": f"cine-conn-{tag}-{uuid.uuid4().hex[:8]}",
        },
    )
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
):
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
    problems = _missing_visual_assets(segments, nodes_list, cast, art, script_doc=script)
    if problems:
        raise ProductionRejected(
            "CINE_IMPORT_ASSETS_MISSING",
            {"production_dir": str(prod_dir), "problems": problems},
        )
    cast_doc, art_doc = cast[1], art[1]
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
        scene_nodes = {
            **_asset_nodes(list(curr_nodes.values()), "sceneId"),
            **_asset_nodes(list(curr_nodes.values()), "sceneIndex"),
        }

        # Provision character turnaround cards (16:9 sheet) missing from the canvas.
        for cid in _referenced_characters(segments):
            if cid in char_nodes:
                continue
            char_def = _character_entry(cast_doc, cid)
            sheet_prompt = _character_sheet(char_def) if char_def else ""
            if not sheet_prompt:
                # Validated above; only reachable if the canvas changed meanwhile.
                raise ProductionRejected(
                    "CINE_IMPORT_ASSETS_MISSING",
                    {
                        "problems": [
                            f"Character {cid} left the canvas during import and has no sheet."
                        ]
                    },
                )
            cname = char_def.get("name") or cid
            new_cnode, _ = await _create_target(
                client,
                bound,
                node_type="image_prompt",
                title=f"{cname} · 角色三视图",
                data={
                    "characterId": cid,
                    "prompt": sheet_prompt,
                    "brief": char_def.get("oneLiner", ""),
                    "aspectRatio": "16:9",
                    "generationKind": "image",
                    "productionStage": "cast",
                    "role": "character_sheet",
                },
            )
            char_nodes[cid] = new_cnode

        # Provision scene concept cards missing from the canvas.
        referenced_scenes = _referenced_scenes(segments, script)
        for sid, s_idx in referenced_scenes.items():
            if sid in scene_nodes or s_idx in scene_nodes:
                continue
            scene_def = _scene_entry(art_doc, sid, s_idx)
            sprompt = _scene_prompt(scene_def) if scene_def else ""
            if not sprompt:
                raise ProductionRejected(
                    "CINE_IMPORT_ASSETS_MISSING",
                    {
                        "problems": [
                            f"Scene {sid} left the canvas during import and has no prompt."
                        ]
                    },
                )
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
                    "brief": scene_def.get("brief", ""),
                    "aspectRatio": "16:9",
                    "generationKind": "image",
                    "productionStage": "art",
                    "role": "scene_art",
                },
            )
            scene_nodes[sid] = new_snode
            scene_nodes[s_idx] = new_snode

        prompts_aligned = 0
        for ep in storyboard.get("episodes", []):
            # Each episode's cards derive from its own storyboard node and form
            # their own sequence; nothing chains across episode boundaries.
            sb_node_id = episode_nodes.get(str(ep.get("ep")))
            prev_card_id = None
            for seg in ep.get("segments", []):
                seg_id = seg.get("id")
                if not seg_id:
                    continue
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
                }

                existing = video_cards.get(seg_id)
                if existing:
                    card_id = existing["id"]
                    outcome = await _update_existing_card(
                        client, bound, card_id, clean_title, card_data, h3_prompt
                    )
                    if outcome == "preserved":
                        preserved.append(seg_id)
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
                snode = scene_nodes.get(sc_id) or (scene_nodes.get(s_idx) if s_idx else None)
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

                # Read actual canvas references and align H3 prompt <Picture N> tags strictly with canvas
                if hasattr(client, "get_node_references"):
                    try:
                        actual_refs = await client.get_node_references(bound, card_id)
                        if isinstance(actual_refs, list) and actual_refs:
                            current_prompt = card_data.get("prompt", "")
                            aligned_prompt = align_h3_prompt_to_references(
                                current_prompt, actual_refs, cast_doc, art_doc
                            )
                            if aligned_prompt and aligned_prompt != current_prompt:
                                await client.submit_command(
                                    bound,
                                    {
                                        "type": "canvas.update_node",
                                        "nodeId": card_id,
                                        "patch": {
                                            "data": {
                                                "prompt": aligned_prompt,
                                                IMPORTED_PROMPT_KEY: _sha256_text(aligned_prompt),
                                            }
                                        },
                                        "commandId": f"cine-align-vp-{uuid.uuid4().hex[:8]}",
                                    },
                                )
                                seg["h3Prompt"] = aligned_prompt
                                card_data["prompt"] = aligned_prompt
                                prompts_aligned += 1
                    except Exception as exc:
                        logger.debug(
                            "Failed aligning prompt references for card %s: %s", card_id, exc
                        )

        if prompts_aligned > 0 and storyboard_path.is_file():
            try:
                storyboard_path.write_text(
                    json.dumps(storyboard, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
            except Exception as exc:
                logger.warning(
                    "Failed to write aligned storyboard to %s: %s", storyboard_path, exc
                )
    except (ProductionRejected, KeyError, TypeError, ValueError) as exc:
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
        },
        "next_step": next_step,
    }
