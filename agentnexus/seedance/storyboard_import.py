"""Send native creative artifacts to V3's deterministic storyboard importer."""

import hashlib
import json
import logging
import uuid
from pathlib import Path

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
        cut_flow = flow[first - 1:last]
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
                beat.get("action")
                or f"{beat.get('speaker') or 'VO'}: {beat.get('line') or ''}"
            ).strip()
            for beat in cut_flow
            if isinstance(beat, dict)
        ).strip()
        shots.append({
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
        })
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
    matches = [node for node in snapshot.get("nodes", []) if (node.get("title") or "").strip() == clean_title]
    if len(matches) != 1:
        raise ProductionRejected("CINE_IMPORT_TARGET_CREATE_UNVERIFIED", result)
    return matches[0], snapshot


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
    documents = []
    hashes = {}
    for value in (storyboard_file, script_file):
        path = inside(root, value)
        if not path.is_file() or path.stat().st_size > 8 * 1024 * 1024:
            raise ProductionRejected("CINE_IMPORT_FILE_INVALID")
        raw = path.read_bytes()
        hashes[path] = hashlib.sha256(raw).hexdigest()
        documents.append(json.loads(raw.decode("utf-8-sig")))
    snapshot = await client.get_snapshot(bound)
    nodes_list = snapshot.get("nodes", [])
    episode_numbers = [str(ep.get("ep")) for ep in documents[0].get("episodes", [])]
    if not episode_numbers or any(value == "None" for value in episode_numbers):
        raise ProductionRejected("CINE_IMPORT_EPISODES_REQUIRED")
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
            "document": documents[0],
            "script": documents[1],
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

    # Auto-project all segments in the storyboard to video_prompt cards on the canvas,
    # ensuring 100% strict alignment between storyboard cuts, durations, and video prompts.
    segments_to_project = [
        (ep, seg)
        for ep in documents[0].get("episodes", [])
        for seg in ep.get("segments", [])
        if seg.get("id")
    ]
    try:
        if not segments_to_project:
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
        fresh_snap = await client.get_snapshot(bound)
        curr_nodes = {n["id"]: n for n in fresh_snap.get("nodes", [])}
        video_cards = {
            n.get("data", {}).get("segmentId"): n
            for n in curr_nodes.values()
            if n.get("type") == "video_prompt" and n.get("data", {}).get("segmentId")
        }
        sb_node_id = episode_nodes.get(episode_numbers[0])
        prev_card_id = None

        prod_dir = path.parent
        cast_file = prod_dir / "cast.json"
        cast_doc = json.loads(cast_file.read_text(encoding="utf-8-sig")) if cast_file.is_file() else {}
        art_file = prod_dir / "art.json"
        art_doc = json.loads(art_file.read_text(encoding="utf-8-sig")) if art_file.is_file() else {}

        # Discover all referenced characters across all cuts in this storyboard
        referenced_cids = {
            cid
            for ep, seg in segments_to_project
            for cut in seg.get("cuts", [])
            for cid in cut.get("characters", [])
            if cid
        }
        char_nodes = {
            n.get("data", {}).get("characterId"): n
            for n in curr_nodes.values()
            if n.get("type") == "image_prompt" and n.get("data", {}).get("characterId")
        }

        # Auto-provision missing character turnaround cards (16:9 sheet)
        for cid in sorted(referenced_cids):
            if cid in char_nodes:
                continue
            char_def = next((c for c in cast_doc.get("characters", []) if c.get("id") == cid), None)
            if not char_def:
                raise ProductionRejected(
                    "CINE_IMPORT_CHARACTER_UNSET",
                    {"error": f"Storyboard cut references character {cid}, but it is not defined in {cast_file.name}."},
                )
            sheet_prompt = (
                char_def.get("image", {}).get("sheet")
                or char_def.get("image", {}).get("prompt")
                or ""
            ).strip()
            if not sheet_prompt:
                raise ProductionRejected(
                    "CINE_IMPORT_CHARACTER_SHEET_MISSING",
                    {"error": f"Character {cid} ({char_def.get('name', '')}) has no image.sheet in {cast_file.name}. Visual assets must be specified before deploying video cards."},
                )
            cname = char_def.get("name") or cid
            new_cnode, fresh_snap = await _create_target(
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
            curr_nodes[new_cnode["id"]] = new_cnode

        # Discover all referenced scenes and auto-provision scene concept cards
        scene_nodes = {
            n.get("data", {}).get("sceneIndex"): n
            for n in curr_nodes.values()
            if n.get("type") == "image_prompt" and n.get("data", {}).get("sceneIndex")
        }
        referenced_scenes = {
            seg.get("sceneIndex")
            for ep, seg in segments_to_project
            if seg.get("sceneIndex")
        }
        for s_idx in sorted(referenced_scenes):
            if s_idx in scene_nodes:
                continue
            sid = f"S{int(s_idx):02d}"
            scene_def = next((s for s in art_doc.get("scenes", []) if s.get("sceneIndex") == s_idx or s.get("id") == sid), None)
            if scene_def:
                sprompt = (scene_def.get("image", {}).get("prompt") or "").strip()
                if sprompt:
                    sname = scene_def.get("name") or sid
                    new_snode, fresh_snap = await _create_target(
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
                    curr_nodes[new_snode["id"]] = new_snode

        for ep, seg in segments_to_project:
            seg_id = seg["id"]
            cuts = seg.get("cuts", [])
            dur_sec = round(sum(c.get("seconds", 0) for c in cuts), 1)
            h3_prompt = seg.get("h3Prompt", "")
            structured_shots = build_structured_shots(seg, documents[1], ep.get("ep"))
            brief = (
                seg.get("brief")
                or (cuts[0].get("description") or cuts[0].get("frame") or f"{seg_id} 生成段" if cuts else f"{seg_id} 生成段")
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
                existing_data = existing.get("data", {})
                await client.submit_command(
                    bound,
                    {
                        "type": "canvas.update_node",
                        "nodeId": existing["id"],
                        "patch": {
                            "title": clean_title,
                            "data": {**existing_data, **card_data},
                        },
                        "commandId": f"cine-sync-vp-{uuid.uuid4().hex}",
                    },
                )
                card_id = existing["id"]
            else:
                new_card, snapshot = await _create_target(
                    client,
                    bound,
                    node_type="video_prompt",
                    title=clean_title,
                    data={
                        **card_data,
                        "aspectRatio": "16:9",
                        "model": "minimax-h3-autodl-lightx2v-v5-15s",
                        "submissionModel": "minimax_h3_lightx2v_v5_15s",
                        "provider": "autodl-comfy",
                        "size": "768p",
                    },
                )
                card_id = new_card["id"]
                if sb_node_id:
                    await client.submit_command(
                        bound,
                        {
                            "type": "canvas.connect",
                            "from": sb_node_id,
                            "to": card_id,
                            "kind": "derives",
                            "commandId": f"cine-conn-vp-{uuid.uuid4().hex}",
                        },
                    )

            # Connect character references (for both new and existing cards)
            connected_cids = set()
            for cut in cuts:
                for cid in cut.get("characters", []):
                    if cid in connected_cids:
                        continue
                    cnode = char_nodes.get(cid)
                    if cnode:
                        await client.submit_command(
                            bound,
                            {
                                "type": "canvas.connect",
                                "from": cnode["id"],
                                "to": card_id,
                                "kind": "references",
                                "commandId": f"cine-conn-c-{cid}-{card_id[:8]}-{uuid.uuid4().hex[:4]}",
                            },
                        )
                        connected_cids.add(cid)

            # Connect scene concept reference (for both new and existing cards)
            s_idx = seg.get("sceneIndex")
            if s_idx in scene_nodes:
                snode = scene_nodes[s_idx]
                await client.submit_command(
                    bound,
                    {
                        "type": "canvas.connect",
                        "from": snode["id"],
                        "to": card_id,
                        "kind": "references",
                        "commandId": f"cine-conn-s-{s_idx}-{card_id[:8]}-{uuid.uuid4().hex[:4]}",
                    },
                )

            if prev_card_id:
                await client.submit_command(
                    bound,
                    {
                        "type": "canvas.connect",
                        "from": prev_card_id,
                        "to": card_id,
                        "kind": "sequence",
                        "commandId": f"cine-conn-seq-{seg_id}-{uuid.uuid4().hex[:6]}",
                    },
                )
            prev_card_id = card_id
    except (ProductionRejected, KeyError, TypeError, ValueError) as exc:
        logger.exception("Failed to auto-project video prompt nodes during storyboard import")
        raise ProductionRejected(
            "CINE_IMPORT_VIDEO_CARD_PROJECTION_FAILED",
            {"error": str(exc)},
        ) from exc

    return {
        "status": "synced",
        "outcome": "succeeded",
        "verified": True,
        "generation_submitted": False,
        "receipt": {
            **receipt,
            "character_assets_linked": len(char_nodes),
            "scene_assets_linked": len(scene_nodes),
        },
        "next_step": (
            f"Storyboard data and visual asset cards are synced on canvas ({len(char_nodes)} character sheets, {len(scene_nodes)} scene concept cards linked as references). "
            "Visual character sheets must be reviewed before submitting video generation to ensure character consistency across cuts."
        ),
    }
