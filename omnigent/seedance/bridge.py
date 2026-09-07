"""Seedance V3 Bridge for AgentNexus / Cine Topic integration."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Mapping

import httpx

from omnigent.coordination.channels import (
    A2A_CHANNEL_SCOPE_KEY,
    binding_for_session,
)
from omnigent.seedance.client import (
    DEFAULT_SEEDANCE_UI_BASE_URL,
    SeedanceClient,
    SeedanceNotFoundError,
    SeedanceSecurityError,
)

logger = logging.getLogger(__name__)

SEEDANCE_PROJECT_LABEL = "seedance.project_id"
SEEDANCE_SESSION_LABEL = "seedance.agent_session_id"
SEEDANCE_BASE_URL_LABEL = "seedance.base_url"
SEEDANCE_CURSOR_LABEL = "seedance.last_event_cursor"
SEEDANCE_STATUS_LABEL = "seedance.status"


def find_cine_shot_ledgers(workspace_dir, *, session_id=None):
    from omnigent.seedance.cine_contracts import current_ledger

    return [current_ledger(workspace_dir, session_id)]


def read_matching_shots(workspace_dir, shot_ids, *, session_id=None, receipts=None):
    from omnigent.seedance.cine_contracts import reviewed_shots

    return reviewed_shots(workspace_dir, session_id, shot_ids, receipts or {})


def format_shot_contract_message(
    task, shot_ids=None, generation_allowed=False, workspace_dir=None,
    *, session_id=None, receipts=None,
):
    if generation_allowed and not shot_ids:
        raise ValueError("CINE_SHOTS_REQUIRED: generation needs reviewed source shots")
    lines = [task.strip()]
    if shot_ids:
        shots = read_matching_shots(workspace_dir, shot_ids, session_id=session_id, receipts=receipts)
        lines.append("--- CINE SOURCE SHOT CONTRACT ---")
        lines.append(json.dumps(shots, ensure_ascii=False))
        lines.append("Model-reviewed visual observations only. Motion/audio require separate review; "
                     "do not present newly invented details as original-source facts.")
    else:
        lines.append("[SOURCE STATUS]: unverified draft; no original-video accuracy is certified.")
    if generation_allowed:
        lines.append("[AUTHORIZATION]: generation_allowed is TRUE. The user has explicitly authorized generation.")
    else:
        lines.append("[SAFETY POLICY]: generation_allowed is FALSE. You are STRICTLY FORBIDDEN from submitting image or video generation jobs. Draft cards only.")
    return "\n".join(lines)

async def resolve_or_create_topic_project_and_session(
    server_client: httpx.AsyncClient,
    conversation_id: str,
    seedance_client: SeedanceClient,
    project_name_hint: str | None = None,
    model: str | None = None,
) -> tuple[str, str, str]:
    session_data: dict[str, Any] = {}
    labels: dict[str, Any] = {}
    try:
        resp = await server_client.get(
            f"/v1/sessions/{conversation_id}",
            params={"include_items": "false"},
            timeout=15.0,
        )
        if resp.status_code == 200:
            session_data = resp.json()
            labels = session_data.get("labels") or {}
    except Exception as exc:
        logger.debug("Failed to fetch session %s: %s", conversation_id, exc)

    channel_binding = binding_for_session(
        conversation_id,
        purpose=session_data.get("purpose"),
        root_session_id=session_data.get("root_conversation_id"),
        labels=labels,
    )
    channel_scope = channel_binding.scope

    project_id = str(labels.get(SEEDANCE_PROJECT_LABEL) or "").strip() or None
    agent_session_id = str(labels.get(SEEDANCE_SESSION_LABEL) or "").strip() or None

    if project_id:
        try:
            await seedance_client.get_snapshot(project_id)
        except SeedanceNotFoundError:
            logger.info("Bound Seedance project %s no longer exists; re-creating.", project_id)
            project_id = None
            agent_session_id = None
        except Exception as exc:
            logger.warning("Error verifying Seedance project %s: %s", project_id, exc)

    if not project_id:
        title = project_name_hint or session_data.get("title") or f"Topic {conversation_id[:8]}"
        project_name = f"Cine: {title}"
        created_project = await seedance_client.create_project(project_name)
        project_id = created_project["id"]

    if not agent_session_id:
        created_session = await seedance_client.create_agent_session(project_id, model=model)
        agent_session_id = created_session["id"]

    labels_to_save = {
        SEEDANCE_PROJECT_LABEL: project_id,
        SEEDANCE_SESSION_LABEL: agent_session_id,
        SEEDANCE_BASE_URL_LABEL: seedance_client.base_url,
        SEEDANCE_STATUS_LABEL: "bound",
    }
    try:
        await server_client.patch(
            f"/v1/sessions/{conversation_id}",
            json={"labels": labels_to_save},
            timeout=10.0,
        )
    except Exception as exc:
        logger.warning("Failed updating session %s labels: %s", conversation_id, exc)

    return project_id, agent_session_id, channel_scope


async def execute_seedance_agent_message(
    server_client: httpx.AsyncClient,
    conversation_id: str,
    task: str,
    *,
    shot_ids: list[str] | None = None,
    generation_allowed: bool = False,
    model: str | None = None,
    wait: bool = True,
    timeout_seconds: int = 60,
    workspace_dir: str | Path | None = None,
    seedance_client: SeedanceClient | None = None,
) -> dict[str, Any]:
    # Compatibility endpoint: the general V3 Agent cannot enforce read-only QA.
    _ = (server_client, conversation_id, task, shot_ids, generation_allowed, model,
         wait, timeout_seconds, workspace_dir, seedance_client)
    return {
        "status": "rejected", "outcome": "failed",
        "error_code": "CINE_AGENT_DELEGATION_DISABLED",
        "error": "V3 Agent delegation is disabled. Use seedance_read_canvas for QA, "
                 "seedance_edit_canvas initialize for binding, and native-validated direct submission.",
    }



async def read_seedance_canvas_snapshot(
    server_client: httpx.AsyncClient,
    conversation_id: str,
    *,
    project_id: str | None = None,
    node_types: list[str] | None = None,
    shot_ids: list[str] | None = None,
    include_edges: bool = True,
    detail_level: str = "full",
    seedance_client: SeedanceClient | None = None,
) -> dict[str, Any]:
    """Read the full contents of the Seedance V3 canvas for the current Topic/project.

    Pure read-only operation:
    - Does NOT create agent sessions or send prompts.
    - Does NOT call LLMs or external models.
    - Does NOT submit or trigger generation jobs.
    Directly retrieves nodes, edges, prompts, and status from the project snapshot.
    """
    client = seedance_client or SeedanceClient()
    try:
        resolved_project_id = (project_id or "").strip() or None
        if not resolved_project_id:
            try:
                resp = await server_client.get(
                    f"/v1/sessions/{conversation_id}",
                    params={"include_items": "false"},
                    timeout=10.0,
                )
                if resp.status_code == 200:
                    data = resp.json()
                    labels = data.get("labels") or {}
                    resolved_project_id = str(labels.get(SEEDANCE_PROJECT_LABEL) or "").strip() or None
            except Exception as exc:
                logger.debug("Failed resolving project label for session %s: %s", conversation_id, exc)

        if not resolved_project_id:
            return {
                "status": "not_bound",
                "bound": False,
                "seedance_project_id": None,
                "message": "当前 Topic 尚未绑定 Seedance 画布项目，画布暂无内容（尚未执行过初始化或卡片派发）。",
                "summary": "当前 Topic 尚未绑定 Seedance 画布项目。",
                "counts": {"total_nodes": 0, "edges": 0},
                "nodes": [],
                "edges": [],
                "summary_markdown": "### Seedance 画布内容概览\n\n*当前 Topic 尚未绑定 Seedance 画布项目（尚未初始化或创建分镜卡）。*",
            }

        try:
            snapshot = await client.get_snapshot(resolved_project_id)
        except SeedanceNotFoundError:
            return {
                "status": "not_found",
                "bound": False,
                "seedance_project_id": resolved_project_id,
                "message": f"绑定的 Seedance 项目 {resolved_project_id} 在画布服务端未找到或已被删除。",
                "summary": f"项目 {resolved_project_id} 未找到",
                "counts": {"total_nodes": 0, "edges": 0},
                "nodes": [],
                "edges": [],
                "summary_markdown": f"### Seedance 画布内容概览\n\n*项目 `{resolved_project_id}` 在画布服务端未找到。*",
            }

        raw_nodes = snapshot.get("nodes", []) if isinstance(snapshot, dict) else []
        raw_edges = snapshot.get("edges", []) if isinstance(snapshot, dict) else []
        revision = snapshot.get("revision", 0)

        # Filter by node_types if specified
        if node_types:
            type_set = {t.lower().strip() for t in node_types if t.strip()}
            raw_nodes = [n for n in raw_nodes if str(n.get("type", "")).lower() in type_set]

        # Filter by shot_ids if specified
        if shot_ids:
            shot_set = {s.lower().strip() for s in shot_ids if s.strip()}
            filtered_nodes = []
            for n in raw_nodes:
                title = str(n.get("title", "")).lower()
                data = n.get("data") if isinstance(n.get("data"), dict) else {}
                shot_val = str(data.get("shot", "")).lower()
                nid = str(n.get("id", "")).lower()
                if any(s in title or s == shot_val or s in nid for s in shot_set):
                    filtered_nodes.append(n)
            raw_nodes = filtered_nodes

        # Format nodes
        formatted_nodes: list[dict[str, Any]] = []
        for n in raw_nodes:
            nid = n.get("id")
            ntype = n.get("type", "unknown")
            ntitle = n.get("title", "")
            nstatus = n.get("status", "draft")
            ndata = n.get("data") if isinstance(n.get("data"), dict) else {}

            node_dict: dict[str, Any] = {
                "id": nid,
                "type": ntype,
                "title": ntitle,
                "status": nstatus,
            }

            if detail_level == "summary":
                if ntype == "video_prompt":
                    node_dict["duration_seconds"] = ndata.get("durationSec")
                    node_dict["camera"] = ndata.get("camera")
                    brief = ndata.get("brief") or ndata.get("prompt") or ""
                    node_dict["brief_preview"] = (brief[:150] + "...") if len(brief) > 150 else brief
                elif ntype == "image_prompt":
                    node_dict["aspect_ratio"] = ndata.get("aspectRatio")
                    prompt = ndata.get("prompt") or ""
                    node_dict["prompt_preview"] = (prompt[:150] + "...") if len(prompt) > 150 else prompt
                elif ntype == "storyboard":
                    node_dict["shot_count"] = ndata.get("shotCount")
                elif ntype == "script":
                    content = ndata.get("content") or ""
                    node_dict["content_preview"] = (content[:150] + "...") if len(content) > 150 else content
            else:
                node_dict["data"] = ndata

            formatted_nodes.append(node_dict)

        ui_base_url = os.environ.get("SEEDANCE_UI_BASE_URL", DEFAULT_SEEDANCE_UI_BASE_URL).rstrip("/")
        canvas_url = f"{ui_base_url}/?project={resolved_project_id}"

        # Group by types for markdown summary
        img_cards = [n for n in raw_nodes if n.get("type") == "image_prompt"]
        sb_cards = [n for n in raw_nodes if n.get("type") == "storyboard"]
        vid_cards = [n for n in raw_nodes if n.get("type") == "video_prompt"]
        script_cards = [n for n in raw_nodes if n.get("type") == "script"]
        other_cards = [n for n in raw_nodes if n.get("type") not in ("image_prompt", "storyboard", "video_prompt", "script")]

        md_lines = [
            f"### Seedance 画布内容概览 (项目: `{resolved_project_id}`)",
            f"- **画布链接**: [{canvas_url}]({canvas_url})",
            f"- **节点总数**: {len(raw_nodes)} 个节点（设定卡: {len(img_cards)}, 故事大纲: {len(sb_cards)}, 视频分镜: {len(vid_cards)}, 剧本: {len(script_cards)}" + (f", 其他: {len(other_cards)}" if other_cards else "") + "）",
            f"- **连线总数**: {len(raw_edges)} 条",
            f"- **版本号/Revision**: {revision}",
            "",
        ]

        if img_cards:
            md_lines.append("#### [设定卡] 角色与场景设定卡 (image_prompt):")
            for c in img_cards:
                cid = c.get("id")
                ctitle = c.get("title", "未命名卡片")
                cdata = c.get("data") if isinstance(c.get("data"), dict) else {}
                prompt = cdata.get("prompt", "")
                aspect = cdata.get("aspectRatio", "")
                preview = (prompt[:120] + "...") if len(prompt) > 120 else prompt
                status_badge = f"[{c.get('status')}]" if c.get("status") else ""
                aspect_str = f"({aspect})" if aspect else ""
                md_lines.append(f"- **{ctitle}** {status_badge} `{cid}` {aspect_str}")
                if preview:
                    md_lines.append(f"  > 提示词: {preview}")
            md_lines.append("")

        if sb_cards:
            md_lines.append("#### [大纲卡] 分镜大纲卡 (storyboard):")
            for c in sb_cards:
                cid = c.get("id")
                ctitle = c.get("title", "故事大纲")
                cdata = c.get("data") if isinstance(c.get("data"), dict) else {}
                shots_cnt = cdata.get("shotCount") or len(cdata.get("shots", []))
                outline = cdata.get("outline", "")
                outline_preview = (outline[:150] + "...") if len(outline) > 150 else outline
                md_lines.append(f"- **{ctitle}** `{cid}`（规划镜头数: {shots_cnt}）")
                if outline_preview:
                    md_lines.append(f"  > 大纲预览: {outline_preview}")
            md_lines.append("")

        if vid_cards:
            md_lines.append(f"#### [分镜卡] 分镜执行卡 (video_prompt, 共 {len(vid_cards)} 镜):")
            for c in vid_cards:
                cid = c.get("id")
                ctitle = c.get("title", "分镜卡")
                cdata = c.get("data") if isinstance(c.get("data"), dict) else {}
                dur = cdata.get("durationSec", "?")
                brief = cdata.get("brief") or cdata.get("prompt") or ""
                cam = cdata.get("camera")
                cam_desc = ""
                if isinstance(cam, dict):
                    cam_desc = f" [{cam.get('angle', '')} / {cam.get('motion', '')}]"
                brief_preview = (brief[:140] + "...") if len(brief) > 140 else brief
                status_badge = f"[{c.get('status')}]" if c.get("status") else ""
                md_lines.append(f"- **{ctitle}** {status_badge} `{cid}` ({dur}s){cam_desc}")
                if brief_preview:
                    md_lines.append(f"  > 镜头要求: {brief_preview}")
            md_lines.append("")

        if script_cards:
            md_lines.append("#### [剧本卡] 剧本卡 (script):")
            for c in script_cards:
                cid = c.get("id")
                ctitle = c.get("title", "剧本")
                cdata = c.get("data") if isinstance(c.get("data"), dict) else {}
                content = cdata.get("content", "")
                preview = (content[:150] + "...") if len(content) > 150 else content
                md_lines.append(f"- **{ctitle}** `{cid}`")
                if preview:
                    md_lines.append(f"  > 剧本正文: {preview}")
            md_lines.append("")

        summary_markdown = "\n".join(md_lines)
        summary_text = f"成功读取画布项目 {resolved_project_id}：共 {len(raw_nodes)} 个节点，{len(raw_edges)} 条连线。"

        return {
            "status": "completed",
            "outcome": "succeeded",
            "bound": True,
            "seedance_project_id": resolved_project_id,
            "seedance_canvas_url": canvas_url,
            "revision": revision,
            "counts": {
                "total_nodes": len(raw_nodes),
                "image_prompts": len(img_cards),
                "video_prompts": len(vid_cards),
                "storyboards": len(sb_cards),
                "scripts": len(script_cards),
                "other_nodes": len(other_cards),
                "edges": len(raw_edges) if include_edges else 0,
            },
            "nodes": formatted_nodes,
            "edges": raw_edges if include_edges else [],
            "summary": summary_text,
            "response": summary_text,
            "summary_markdown": summary_markdown,
        }
    finally:
        if seedance_client is None:
            await client.close()


async def execute_seedance_canvas_edit(
    server_client: httpx.AsyncClient,
    conversation_id: str,
    action: str,
    *,
    project_id: str | None = None,
    node_id: str | None = None,
    node_type: str | None = None,
    title: str | None = None,
    prompt: str | None = None,
    brief: str | None = None,
    duration_seconds: int | None = None,
    camera: dict[str, Any] | None = None,
    aspect_ratio: str | None = None,
    data: dict[str, Any] | None = None,
    patch: dict[str, Any] | None = None,
    parent_id: str | None = None,
    expected_revision: int | None = None,
    confirm: bool = False,
    from_node_id: str | None = None,
    to_node_id: str | None = None,
    kind: str | None = None,
    edge_id: str | None = None,
    generation_kind: str = "video",
    generation_allowed: bool = False,
    production_dir: str | None = None,
    source_text: str | None = None,
    production_stage: str | None = None,
    production_pointer: str | None = None,
    trusted_skills_dir: Path | None = None,
    model: str | None = None,
    seedance_client: SeedanceClient | None = None,
) -> dict[str, Any]:
    """Execute a direct canvas mutation on Seedance V3 with revision checks and read-after-write verification."""
    import uuid

    client = seedance_client or SeedanceClient()
    try:
        if action == "initialize":
            pid, sid, scope = await resolve_or_create_topic_project_and_session(
                server_client, conversation_id, client, model=model,
            )
            return {"status": "completed", "outcome": "succeeded", "bound": True,
                    "seedance_project_id": pid, "seedance_agent_session_id": sid,
                    "channel_scope": scope}
        if action == "validate_generation":
            from omnigent.seedance.production_gate import validate_submission

            proof = await validate_submission(
                server_client, conversation_id, skills_dir=trusted_skills_dir,
                production_dir=production_dir, source_text=source_text,
                production_stage=production_stage, production_pointer=production_pointer,
                generation_kind=generation_kind, prompt=prompt, project_id=project_id,
            )
            return {"status": "validated", "outcome": "succeeded", "submitted": False,
                    "validation_reports": proof["report_paths"]}
        resolved_project_id = (project_id or "").strip() or None
        if not resolved_project_id:
            try:
                resp = await server_client.get(
                    f"/v1/sessions/{conversation_id}",
                    params={"include_items": "false"},
                    timeout=10.0,
                )
                if resp.status_code == 200:
                    data_resp = resp.json()
                    labels = data_resp.get("labels") or {}
                    resolved_project_id = str(labels.get(SEEDANCE_PROJECT_LABEL) or "").strip() or None
            except Exception as exc:
                logger.debug("Failed resolving project label for session %s: %s", conversation_id, exc)

        if not resolved_project_id:
            return {
                "status": "not_bound",
                "outcome": "failed",
                "bound": False,
                "error": "当前 Topic 尚未绑定 Seedance 画布项目，无法执行画布修改。请先初始化或指定 project_id。",
            }

        ui_base_url = os.environ.get("SEEDANCE_UI_BASE_URL", DEFAULT_SEEDANCE_UI_BASE_URL).rstrip("/")
        canvas_url = f"{ui_base_url}/?project={resolved_project_id}"
        cmd_id = f"cmd_{uuid.uuid4().hex[:8]}"

        # ACTION: create_node
        if action == "create_node":
            node_data = dict(data or {})
            if prompt is not None:
                node_data["prompt"] = prompt
            if brief is not None:
                node_data["brief"] = brief
            if duration_seconds is not None:
                node_data["durationSec"] = duration_seconds
            if camera is not None:
                node_data["camera"] = camera
            if aspect_ratio is not None:
                node_data["aspectRatio"] = aspect_ratio

            cmd = {
                "type": "canvas.create_node",
                "nodeType": node_type or "video_prompt",
                "title": title or "新卡片",
                "parentId": parent_id,
                "data": node_data,
                "commandId": cmd_id,
            }
            res = await client.submit_command(resolved_project_id, cmd)
            if not res.get("accepted"):
                return {"status": "failed", "outcome": "failed", "error": f"画布服务端拒绝创建命令: {res}"}

            # Read-after-write verification
            snap = await client.get_snapshot(resolved_project_id)
            nodes = snap.get("nodes", []) if isinstance(snap, dict) else []
            # Find matching node by title or latest
            created = next((n for n in reversed(nodes) if n.get("title") == cmd["title"]), None)
            return {
                "status": "completed",
                "outcome": "succeeded",
                "action": "create_node",
                "seedance_project_id": resolved_project_id,
                "seedance_canvas_url": canvas_url,
                "verified": created is not None,
                "node": created,
                "summary": f"已在画布直接创建卡片【{cmd['title']}】 (类型: {cmd['nodeType']})，读后验证通过。",
            }

        # ACTION: update_node
        elif action == "update_node":
            if not node_id:
                return {"status": "failed", "outcome": "failed", "error": "update_node 需要指定 node_id"}

            # Concurrency revision check if expected_revision provided
            if expected_revision is not None:
                snap_pre = await client.get_snapshot(resolved_project_id)
                nodes_pre = snap_pre.get("nodes", []) if isinstance(snap_pre, dict) else []
                current_node = next((n for n in nodes_pre if n.get("id") == node_id), None)
                if current_node:
                    curr_rev = current_node.get("revision", 1)
                    if curr_rev != expected_revision:
                        return {
                            "status": "conflict",
                            "outcome": "failed",
                            "error": (
                                f"版本冲突：画布节点 {node_id} 当前版本为 {curr_rev}，与传入的 expected_revision "
                                f"({expected_revision}) 不一致。该卡片近期可能已被用户在画布上手动编辑，为防覆盖已中止。"
                            ),
                            "current_revision": curr_rev,
                            "current_node": current_node,
                        }

            node_patch = dict(patch or {})
            if title is not None:
                node_patch["title"] = title
            patch_data = dict(node_patch.get("data") or {})
            if prompt is not None:
                patch_data["prompt"] = prompt
            if brief is not None:
                patch_data["brief"] = brief
            if duration_seconds is not None:
                patch_data["durationSec"] = duration_seconds
            if camera is not None:
                patch_data["camera"] = camera
            if aspect_ratio is not None:
                patch_data["aspectRatio"] = aspect_ratio
            if patch_data:
                node_patch["data"] = patch_data

            cmd = {
                "type": "canvas.update_node",
                "nodeId": node_id,
                "patch": node_patch,
                "commandId": cmd_id,
            }
            if expected_revision is not None:
                cmd["expectedRevision"] = expected_revision

            res = await client.submit_command(resolved_project_id, cmd)
            if not res.get("accepted"):
                return {"status": "failed", "outcome": "failed", "error": f"画布服务端拒绝更新命令: {res}"}

            # Read-after-write verification
            snap_post = await client.get_snapshot(resolved_project_id)
            nodes_post = snap_post.get("nodes", []) if isinstance(snap_post, dict) else []
            updated = next((n for n in nodes_post if n.get("id") == node_id), None)
            new_rev = updated.get("revision", 1) if updated else None
            return {
                "status": "completed",
                "outcome": "succeeded",
                "action": "update_node",
                "seedance_project_id": resolved_project_id,
                "seedance_canvas_url": canvas_url,
                "verified": updated is not None,
                "node_id": node_id,
                "new_revision": new_rev,
                "node": updated,
                "summary": f"已直接更新画布节点 {node_id} (最新版本 {new_rev})，读后验证通过。",
            }

        # ACTION: delete_node
        elif action == "delete_node":
            if not node_id:
                return {"status": "failed", "outcome": "failed", "error": "delete_node 需要指定 node_id"}
            if not confirm:
                return {
                    "status": "rejected",
                    "outcome": "failed",
                    "error": f"安全拦截：删除节点 {node_id} 属于破坏性操作，必须显式传递 confirm=true 进行确认。",
                }

            cmd = {
                "type": "canvas.delete_node",
                "nodeId": node_id,
                "commandId": cmd_id,
            }
            if expected_revision is not None:
                cmd["expectedRevision"] = expected_revision

            res = await client.submit_command(resolved_project_id, cmd)
            if not res.get("accepted"):
                return {"status": "failed", "outcome": "failed", "error": f"画布服务端拒绝删除命令: {res}"}

            # Read-after-write verification
            snap_del = await client.get_snapshot(resolved_project_id)
            nodes_del = snap_del.get("nodes", []) if isinstance(snap_del, dict) else []
            still_exists = any(n.get("id") == node_id for n in nodes_del)
            return {
                "status": "completed",
                "outcome": "succeeded",
                "action": "delete_node",
                "seedance_project_id": resolved_project_id,
                "seedance_canvas_url": canvas_url,
                "verified": not still_exists,
                "node_id": node_id,
                "summary": f"已确认并安全删除画布节点 {node_id}，读后验证该节点已不在画布中。",
            }

        # ACTION: connect
        elif action == "connect":
            if not from_node_id or not to_node_id:
                return {"status": "failed", "outcome": "failed", "error": "connect 需要指定 from_node_id 和 to_node_id"}

            cmd = {
                "type": "canvas.connect",
                "from": from_node_id,
                "to": to_node_id,
                "kind": kind or "references",
                "commandId": cmd_id,
            }
            res = await client.submit_command(resolved_project_id, cmd)
            if not res.get("accepted"):
                return {"status": "failed", "outcome": "failed", "error": f"画布服务端拒绝连线命令: {res}"}

            snap_conn = await client.get_snapshot(resolved_project_id)
            edges = snap_conn.get("edges", []) if isinstance(snap_conn, dict) else []
            connected = next((e for e in edges if e.get("from") == from_node_id and e.get("to") == to_node_id), None)
            return {
                "status": "completed",
                "outcome": "succeeded",
                "action": "connect",
                "seedance_project_id": resolved_project_id,
                "seedance_canvas_url": canvas_url,
                "verified": connected is not None,
                "edge": connected,
                "summary": f"已在画布建立连线：{from_node_id} -> {to_node_id} (类型: {cmd['kind']})，读后验证通过。",
            }

        # ACTION: disconnect
        elif action == "disconnect":
            if not edge_id:
                return {"status": "failed", "outcome": "failed", "error": "disconnect 需要指定 edge_id"}

            cmd = {
                "type": "canvas.disconnect",
                "edgeId": edge_id,
                "commandId": cmd_id,
            }
            res = await client.submit_command(resolved_project_id, cmd)
            if not res.get("accepted"):
                return {"status": "failed", "outcome": "failed", "error": f"画布服务端拒绝断开连线命令: {res}"}

            snap_dc = await client.get_snapshot(resolved_project_id)
            edges_dc = snap_dc.get("edges", []) if isinstance(snap_dc, dict) else []
            still_has_edge = any(e.get("id") == edge_id for e in edges_dc)
            return {
                "status": "completed",
                "outcome": "succeeded",
                "action": "disconnect",
                "seedance_project_id": resolved_project_id,
                "seedance_canvas_url": canvas_url,
                "verified": not still_has_edge,
                "edge_id": edge_id,
                "summary": f"已断开连线 {edge_id}，读后验证通过。",
            }

        # ACTION: submit_generation
        elif action == "submit_generation":
            if generation_allowed is not True:
                return {
                    "status": "rejected",
                    "outcome": "failed",
                    "error": "安全策略拦截：generation_allowed 为 false。提交图片或视频生成任务需要消耗 GPU 算力资源，必须经过用户明确授权 (generation_allowed=true)。",
                }

            target_prompt = prompt or ""
            target_aspect = aspect_ratio
            if node_id and not target_prompt:
                snap_node = await client.get_snapshot(resolved_project_id)
                nodes_src = snap_node.get("nodes", []) if isinstance(snap_node, dict) else []
                src_n = next((n for n in nodes_src if n.get("id") == node_id), None)
                if src_n:
                    src_data = src_n.get("data") or {}
                    target_prompt = src_data.get("prompt") or src_data.get("brief") or ""
                    if not target_aspect:
                        target_aspect = src_data.get("aspectRatio")

            if not target_prompt:
                return {"status": "failed", "outcome": "failed", "error": "submit_generation 需要提供 prompt 或指定具有提示词的 node_id"}

            from omnigent.seedance.production_gate import assert_current, validate_submission

            proof = await validate_submission(
                server_client, conversation_id, skills_dir=trusted_skills_dir,
                production_dir=production_dir, source_text=source_text,
                production_stage=production_stage, production_pointer=production_pointer,
                generation_kind=generation_kind, prompt=target_prompt,
                project_id=resolved_project_id,
            )
            if node_id:
                current = await client.get_snapshot(resolved_project_id)
                node = next((n for n in current.get("nodes", []) if n.get("id") == node_id), None)
                if not node or (node.get("data", {}).get("prompt") or node.get("data", {}).get("brief")) != target_prompt:
                    return {"status": "rejected", "outcome": "failed", "error_code": "CINE_NODE_CHANGED"}
            assert_current(proof)

            gen_input: dict[str, Any] = {"prompt": target_prompt}
            if target_aspect:
                gen_input["aspectRatio"] = target_aspect
            if model:
                gen_input["model"] = model

            cmd = {
                "type": "generation.submit",
                "kind": generation_kind or "video",
                "input": gen_input,
                "nodeId": node_id,
                "commandId": cmd_id,
            }
            res = await client.submit_command(resolved_project_id, cmd)
            if not res.get("accepted"):
                return {"status": "failed", "outcome": "failed", "error": f"生成提交被拒绝: {res}"}

            snap_jobs = await client.get_snapshot(resolved_project_id)
            jobs = snap_jobs.get("jobs", []) if isinstance(snap_jobs, dict) else []
            latest_job = jobs[-1] if jobs else {}
            return {
                "status": "completed",
                "outcome": "succeeded",
                "action": "submit_generation",
                "validation_reports": proof["report_paths"],
                "seedance_project_id": resolved_project_id,
                "seedance_canvas_url": canvas_url,
                "verified": bool(jobs),
                "job": latest_job,
                "summary": f"已成功提交 {cmd['kind']} 生成任务 (Job: {latest_job.get('id', 'queued')})，读后验证任务已在队列中。",
            }

        else:
            return {"status": "failed", "outcome": "failed", "error": f"未知的 action: {action}"}

    except Exception as exc:
        from omnigent.seedance.production_gate import ProductionRejected

        if isinstance(exc, ProductionRejected):
            return {"status": "rejected", "outcome": "failed", "error_code": exc.code,
                    "error": str(exc), "validation": exc.detail}
        raise
    finally:
        if seedance_client is None:
            await client.close()
