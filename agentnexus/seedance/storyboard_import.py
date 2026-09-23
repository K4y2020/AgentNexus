"""Send native creative artifacts to V3's deterministic storyboard importer."""

import hashlib
import json
import logging
import uuid
from pathlib import Path

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


def _scene_entry(art_doc: dict, scene_index) -> dict | None:
    try:
        scene_id = f"S{int(scene_index):02d}"
    except (TypeError, ValueError):
        scene_id = None
    return next(
        (
            s
            for s in art_doc.get("scenes", [])
            if isinstance(s, dict)
            and (s.get("sceneIndex") == scene_index or (scene_id and s.get("id") == scene_id))
        ),
        None,
    )


def _scene_prompt(entry: dict) -> str:
    image = entry.get("image") if isinstance(entry.get("image"), dict) else {}
    return str(image.get("prompt") or "").strip()


def _asset_nodes(nodes: list[dict], key: str) -> dict:
    """Existing image_prompt cards keyed by their characterId/sceneIndex."""
    return {
        (n.get("data") or {}).get(key): n
        for n in nodes
        if n.get("type") == "image_prompt" and (n.get("data") or {}).get(key)
    }


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


def _referenced_scenes(segments) -> list:
    return sorted({seg.get("sceneIndex") for _, seg in segments if seg.get("sceneIndex")}, key=str)


def _missing_visual_assets(segments, nodes, cast, art) -> list[str]:
    """List every character/scene that is neither on the canvas nor fully defined in cast/art."""
    (cast_path, cast_doc), (art_path, art_doc) = cast, art
    cast_name = cast_path.name if cast_path else "cast.json (not found)"
    art_name = art_path.name if art_path else "art.json (not found)"
    on_canvas_characters = _asset_nodes(nodes, "characterId")
    on_canvas_scenes = _asset_nodes(nodes, "sceneIndex")
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
    for scene_index in _referenced_scenes(segments):
        if scene_index in on_canvas_scenes:
            continue
        entry = _scene_entry(art_doc, scene_index)
        if entry is None:
            problems.append(
                f"Scene {scene_index} is used in the storyboard but not defined in {art_name}."
            )
        elif not _scene_prompt(entry):
            problems.append(
                f"Scene {scene_index} ({entry.get('name', '')}) has no image.prompt in {art_name}."
            )
    return problems


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
    problems = _missing_visual_assets(segments, nodes_list, cast, art)
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
        scene_nodes = _asset_nodes(list(curr_nodes.values()), "sceneIndex")

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
        for s_idx in _referenced_scenes(segments):
            if s_idx in scene_nodes:
                continue
            scene_def = _scene_entry(art_doc, s_idx)
            sprompt = _scene_prompt(scene_def) if scene_def else ""
            if not sprompt:
                raise ProductionRejected(
                    "CINE_IMPORT_ASSETS_MISSING",
                    {
                        "problems": [
                            f"Scene {s_idx} left the canvas during import and has no prompt."
                        ]
                    },
                )
            try:
                sid = f"S{int(s_idx):02d}"
            except (TypeError, ValueError):
                sid = str(scene_def.get("id") or s_idx)
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
            scene_nodes[s_idx] = new_snode

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
                if s_idx in scene_nodes:
                    await _connect_once(
                        client,
                        bound,
                        edges,
                        scene_nodes[s_idx]["id"],
                        card_id,
                        "references",
                        f"s-{s_idx}",
                    )
                if prev_card_id:
                    await _connect_once(
                        client, bound, edges, prev_card_id, card_id, "sequence", f"seq-{seg_id}"
                    )
                prev_card_id = card_id
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
