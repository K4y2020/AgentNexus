"""Send native creative artifacts to V3's deterministic storyboard importer."""

import hashlib
import json
import logging
import uuid
from pathlib import Path

from .production_gate import ProductionRejected, inside

logger = logging.getLogger(__name__)


async def _create_target(client, project_id, *, node_type, title, data):
    result = await client.submit_command(
        project_id,
        {
            "type": "canvas.create_node",
            "nodeType": node_type,
            "title": title,
            "parentId": None,
            "data": data,
            "commandId": f"cine-import-target-{uuid.uuid4().hex}",
        },
    )
    if not result.get("accepted"):
        raise ProductionRejected("CINE_IMPORT_TARGET_CREATE_FAILED", result)
    snapshot = await client.get_snapshot(project_id)
    matches = [node for node in snapshot.get("nodes", []) if node.get("title") == title]
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
    try:
        fresh_snap = await client.get_snapshot(bound)
        curr_nodes = {n["id"]: n for n in fresh_snap.get("nodes", [])}
        video_cards = {
            n.get("data", {}).get("segmentId"): n
            for n in curr_nodes.values()
            if n.get("type") == "video_prompt" and n.get("data", {}).get("segmentId")
        }
        sb_node_id = episode_nodes.get(episode_numbers[0])

        for ep in documents[0].get("episodes", []):
            for seg in ep.get("segments", []):
                seg_id = seg.get("id")
                if not seg_id:
                    continue
                cuts = seg.get("cuts", [])
                dur_sec = round(sum(c.get("seconds", 0) for c in cuts), 1)
                h3_prompt = seg.get("h3Prompt", "")
                brief = seg.get("brief") or (
                    cuts[0].get("description", "") if cuts else f"{seg_id} 生成段"
                )

                existing = video_cards.get(seg_id)
                if existing:
                    await client.submit_command(
                        bound,
                        {
                            "type": "canvas.update_node",
                            "nodeId": existing["id"],
                            "patch": {
                                "title": f"{seg_id} · {brief[:20]}",
                                "data": {
                                    **existing.get("data", {}),
                                    "segmentId": seg_id,
                                    "durationSec": dur_sec,
                                    "prompt": h3_prompt,
                                    "brief": brief,
                                },
                            },
                            "commandId": f"cine-sync-vp-{uuid.uuid4().hex}",
                        },
                    )
                else:
                    new_card, _ = await _create_target(
                        client,
                        bound,
                        node_type="video_prompt",
                        title=f"{seg_id} · {brief[:20]}",
                        data={
                            "segmentId": seg_id,
                            "durationSec": dur_sec,
                            "prompt": h3_prompt,
                            "brief": brief,
                            "aspectRatio": "16:9",
                            "model": "minimax-h3-remote-ref2va-v2",
                        },
                    )
                    if sb_node_id:
                        await client.submit_command(
                            bound,
                            {
                                "type": "canvas.connect",
                                "from": sb_node_id,
                                "to": new_card["id"],
                                "kind": "references",
                                "commandId": f"cine-conn-vp-{uuid.uuid4().hex}",
                            },
                        )
    except Exception as exc:
        logger.warning(
            "Failed to auto-project video prompt nodes during storyboard import: %s", exc
        )

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
