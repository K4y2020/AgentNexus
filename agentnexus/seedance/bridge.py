"""Seedance V3 Bridge for AgentNexus / Cine Topic integration."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Any, Mapping

import httpx

from agentnexus.coordination.channels import (
    A2A_CHANNEL_SCOPE_KEY,
    binding_for_session,
)
from agentnexus.seedance.client import (
    DEFAULT_SEEDANCE_UI_BASE_URL,
    SeedanceClient,
    SeedanceError,
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
    from agentnexus.seedance.cine_contracts import current_ledger

    return [current_ledger(workspace_dir, session_id)]


def read_matching_shots(workspace_dir, shot_ids, *, session_id=None, receipts=None):
    from agentnexus.seedance.cine_contracts import reviewed_shots

    return reviewed_shots(workspace_dir, session_id, shot_ids, receipts or {})


def format_shot_contract_message(
    task,
    shot_ids=None,
    generation_allowed=False,
    workspace_dir=None,
    *,
    session_id=None,
    receipts=None,
):
    if generation_allowed and not shot_ids:
        raise ValueError("CINE_SHOTS_REQUIRED: generation needs reviewed source shots")
    lines = [task.strip()]
    if shot_ids:
        shots = read_matching_shots(
            workspace_dir, shot_ids, session_id=session_id, receipts=receipts
        )
        lines.append("--- CINE SOURCE SHOT CONTRACT ---")
        lines.append(json.dumps(shots, ensure_ascii=False))
        lines.append(
            "Model-reviewed visual observations only. Motion/audio require separate review; "
            "do not present newly invented details as original-source facts."
        )
    else:
        lines.append("[SOURCE STATUS]: unverified draft; no original-video accuracy is certified.")
    if generation_allowed:
        lines.append(
            "[AUTHORIZATION]: generation_allowed is TRUE. The user has explicitly authorized generation."
        )
    else:
        lines.append(
            "[SAFETY POLICY]: generation_allowed is FALSE. You are STRICTLY FORBIDDEN from submitting image or video generation jobs. Draft cards only."
        )
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
    _ = (
        server_client,
        conversation_id,
        task,
        shot_ids,
        generation_allowed,
        model,
        wait,
        timeout_seconds,
        workspace_dir,
        seedance_client,
    )
    return {
        "status": "rejected",
        "outcome": "failed",
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
                    resolved_project_id = (
                        str(labels.get(SEEDANCE_PROJECT_LABEL) or "").strip() or None
                    )
            except Exception as exc:
                logger.debug(
                    "Failed resolving project label for session %s: %s", conversation_id, exc
                )

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
        node_media = {m["nodeId"]: m for m in snapshot.get("node_media", [])}

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

        # Resolve canvas references for video_prompt cards so agents can read their exact Picture order
        vid_nodes = [n for n in raw_nodes if n.get("type") == "video_prompt"]
        refs_by_node: dict[str, list[dict[str, Any]]] = {}
        if vid_nodes and hasattr(client, "get_node_references"):
            try:
                results = await asyncio.gather(
                    *[client.get_node_references(resolved_project_id, n["id"]) for n in vid_nodes],
                    return_exceptions=True,
                )
                for n, res in zip(vid_nodes, results):
                    if isinstance(res, list):
                        refs_by_node[n["id"]] = res
            except Exception as exc:
                logger.debug("Failed resolving node references: %s", exc)

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
                "revision": n.get("revision"),
            }
            if ntype in ("image_prompt", "video_prompt"):
                output_ref = ndata.get("activeOutputRef")
                node_dict["active_output_ref"] = output_ref
                media = node_media.get(nid)
                if media:
                    node_dict.update(
                        media_state=media["mediaState"],
                        output_url=media["outputUrl"],
                        latest_job=media["latestJob"],
                        pending_job_count=media["pendingJobCount"],
                        next_action=media["nextAction"],
                    )
                else:
                    node_dict["media_state"] = "unknown"  # Older V3: absence is not failure.
                    node_dict["next_action"] = "read_exact_job"

            if ntype == "video_prompt":
                node_refs = refs_by_node.get(nid, [])
                ref_list = []
                img_idx = 0
                for r in node_refs:
                    kind = r.get("kind") or "image"
                    if kind == "image":
                        img_idx += 1
                        tag = f"<Picture {img_idx}>"
                    elif kind == "video":
                        tag = "<Video>"
                    else:
                        tag = "<Audio>"
                    ref_list.append({
                        "tag": tag,
                        "label": r.get("label", ""),
                        "role": r.get("role", "reference_image"),
                        "id": r.get("id", ""),
                    })
                node_dict["references"] = ref_list

            if detail_level == "summary":
                if ntype == "video_prompt":
                    node_dict["duration_seconds"] = ndata.get("durationSec")
                    node_dict["camera"] = ndata.get("camera")
                    brief = ndata.get("brief") or ndata.get("prompt") or ""
                    node_dict["brief_preview"] = (
                        (brief[:150] + "...") if len(brief) > 150 else brief
                    )
                elif ntype == "image_prompt":
                    node_dict["aspect_ratio"] = ndata.get("aspectRatio")
                    prompt = ndata.get("prompt") or ""
                    node_dict["prompt_preview"] = (
                        (prompt[:150] + "...") if len(prompt) > 150 else prompt
                    )
                elif ntype == "storyboard":
                    node_dict["shot_count"] = ndata.get("shotCount")
                elif ntype == "script":
                    content = ndata.get("content") or ""
                    node_dict["content_preview"] = (
                        (content[:150] + "...") if len(content) > 150 else content
                    )
            else:
                node_dict["data"] = ndata

            formatted_nodes.append(node_dict)

        ui_base_url = os.environ.get("SEEDANCE_UI_BASE_URL", DEFAULT_SEEDANCE_UI_BASE_URL).rstrip(
            "/"
        )
        canvas_url = f"{ui_base_url}/?project={resolved_project_id}"

        # Group by types for markdown summary
        img_cards = [n for n in raw_nodes if n.get("type") == "image_prompt"]
        sb_cards = [n for n in raw_nodes if n.get("type") == "storyboard"]
        vid_cards = [n for n in raw_nodes if n.get("type") == "video_prompt"]
        script_cards = [n for n in raw_nodes if n.get("type") == "script"]
        other_cards = [
            n
            for n in raw_nodes
            if n.get("type") not in ("image_prompt", "storyboard", "video_prompt", "script")
        ]

        md_lines = [
            f"### Seedance 画布内容概览 (项目: `{resolved_project_id}`)",
            f"- **画布链接**: [{canvas_url}]({canvas_url})",
            f"- **节点总数**: {len(raw_nodes)} 个节点（设定卡: {len(img_cards)}, 故事大纲: {len(sb_cards)}, 视频分镜: {len(vid_cards)}, 剧本: {len(script_cards)}"
            + (f", 其他: {len(other_cards)}" if other_cards else "")
            + "）",
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
                media = node_media.get(cid)
                if media:
                    md_lines.append(
                        f"  > 媒体状态: {media['mediaState']}; 下一步: {media['nextAction']}; 图片: {media['outputUrl'] or '无当前显示图'}"
                    )
                elif cdata.get("activeOutputRef"):
                    md_lines.append(
                        f"  > 已有关联图片，尚需核验内容: `{cdata['activeOutputRef']}`"
                    )
                else:
                    md_lines.append("  > 媒体汇总不可用，请读取本次 job；不要修改输出字段。")
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
                node_refs = refs_by_node.get(cid, [])
                if node_refs:
                    img_idx = 0
                    md_lines.append("  > 画布参考素材顺序 (References - 视频提示词引用序号以此为唯一基准):")
                    for r in node_refs:
                        kind = r.get("kind") or "image"
                        tag = f"<Picture {img_idx + 1}>" if kind == "image" else f"<{kind.capitalize()}>"
                        if kind == "image":
                            img_idx += 1
                        md_lines.append(f"    - **{tag}**: {r.get('label', '')} (`{r.get('id', '')}`)")
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


async def read_seedance_generation(server_client, conversation_id, *, action, job_id=None):
    """Read generation metadata without starting workers or guessing endpoints."""
    resp = await server_client.get(f"/v1/sessions/{conversation_id}", timeout=10)
    resp.raise_for_status()
    labels = resp.json().get("labels") or {}
    async with SeedanceClient(base_url=labels.get(SEEDANCE_BASE_URL_LABEL)) as client:
        if action == "models":
            return {"status": "completed", **await client.get_generation_models()}
        if not job_id:
            return {"status": "rejected", "error_code": "CINE_JOB_ID_REQUIRED"}
        job = await client.get_job(job_id)
        if (
            not labels.get(SEEDANCE_PROJECT_LABEL)
            or job.get("projectId") != labels[SEEDANCE_PROJECT_LABEL]
        ):
            return {"status": "rejected", "error_code": "CINE_PROJECT_BINDING_MISMATCH"}
        terminal = job.get("status") in ("succeeded", "failed", "cancelled")
        return {
            "status": "completed",
            "job": job,
            "terminal": terminal,
            "image_export": {
                "tool": "seedance_edit_canvas",
                "action": "export_image",
                "job_id": job_id,
            }
            if job.get("status") == "succeeded" and job.get("kind") == "image"
            else None,
            "poll_after_seconds": 0 if terminal else 20,
            "next_action": "review_once_then_report"
            if job.get("status") == "succeeded"
            else "report_job_failure"
            if terminal
            else "wait_for_job",
            "visual_review_required": job.get("status") == "succeeded",
        }


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
    generation_kind: str | None = None,
    generation_allowed: bool = False,
    production_dir: str | None = None,
    source_text: str | None = None,
    production_stage: str | None = None,
    production_pointer: str | None = None,
    storyboard_file: str | None = None,
    script_file: str | None = None,
    episode_nodes: dict[str, str] | None = None,
    job_id: str | None = None,
    output_path: str | None = None,
    output_index: int = 0,
    trusted_skills_dir: Path | None = None,
    model: str | None = None,
    seedance_client: SeedanceClient | None = None,
) -> dict[str, Any]:
    """Execute a direct canvas mutation on Seedance V3 with revision checks and read-after-write verification."""
    import uuid

    if generation_kind is None:
        generation_kind = (
            "image"
            if production_stage in ("cast", "art")
            or (production_stage == "storyboard" and (production_pointer or "").endswith("/frame"))
            else "video"
        )
    client = seedance_client or SeedanceClient()
    try:
        if action == "export_image":
            from agentnexus.seedance.image_export import export_job_image

            return await export_job_image(
                server_client,
                conversation_id,
                client,
                job_id=job_id,
                output_path=output_path,
                output_index=output_index,
            )
        if action == "import_storyboard":
            from agentnexus.seedance.storyboard_import import import_storyboard

            return await import_storyboard(
                server_client,
                conversation_id,
                client,
                storyboard_file=storyboard_file,
                script_file=script_file,
                summary_node_id=node_id,
                episode_nodes=episode_nodes,
                project_id=project_id,
            )
        if action == "initialize":
            pid, sid, scope = await resolve_or_create_topic_project_and_session(
                server_client,
                conversation_id,
                client,
                model=model,
            )
            return {
                "status": "completed",
                "outcome": "succeeded",
                "bound": True,
                "seedance_project_id": pid,
                "seedance_agent_session_id": sid,
                "channel_scope": scope,
            }
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
                    resolved_project_id = (
                        str(labels.get(SEEDANCE_PROJECT_LABEL) or "").strip() or None
                    )
            except Exception as exc:
                logger.debug(
                    "Failed resolving project label for session %s: %s", conversation_id, exc
                )

        if not resolved_project_id:
            return {
                "status": "not_bound",
                "outcome": "failed",
                "bound": False,
                "error": "当前 Topic 尚未绑定 Seedance 画布项目，无法执行画布修改。请先初始化或指定 project_id。",
            }

        if action in ("validate_generation", "submit_generation"):
            from agentnexus.seedance.production_gate import validate_submission

            if action == "submit_generation" and generation_allowed is not True:
                return {
                    "status": "rejected",
                    "outcome": "failed",
                    "error_code": "CINE_GENERATION_AUTHORIZATION_REQUIRED",
                    "error": "generation_allowed must be true within the user's authorized scope.",
                }
            # Validate source contracts first; this never authorizes or submits a job.
            proof = await validate_submission(
                server_client,
                conversation_id,
                skills_dir=trusted_skills_dir,
                production_dir=production_dir,
                source_text=source_text,
                production_stage=production_stage,
                production_pointer=production_pointer,
                generation_kind=generation_kind,
                prompt=prompt,
                project_id=resolved_project_id,
            )
            gen_input = {"prompt": proof["prompt"]}
            if model:
                gen_input["model"] = model
            if aspect_ratio:
                gen_input["aspectRatio"] = aspect_ratio
            generation_command = {"kind": generation_kind, "input": gen_input}
            state_requirements = proof.get("state_requirements") or []
            if state_requirements:
                if not node_id:
                    raise ProductionRejected("CINE_CHARACTER_STATE_REFERENCE_REQUIRED")
                references = await client.get_node_references(resolved_project_id, node_id)
                reference_ids = {row.get("id") for row in references}
                missing = [
                    row
                    for row in state_requirements
                    if f"node:{row['asset_node_id']}" not in reference_ids
                ]
                if missing:
                    raise ProductionRejected(
                        "CINE_CHARACTER_STATE_REFERENCE_REQUIRED", {"missing": missing}
                    )
            if node_id:
                for attempt in range(2):
                    snapshot = await client.get_snapshot(resolved_project_id)
                    nodes = snapshot.get("nodes", []) if isinstance(snapshot, dict) else []
                    target_node = next((node for node in nodes if node.get("id") == node_id), None)
                    if target_node is None:
                        raise SeedanceError("Generation node was not found", code="NODE_NOT_FOUND")
                    node_data = target_node.get("data") or {}
                    if (
                        node_data.get("production_stage") != production_stage
                        or node_data.get("production_pointer") != production_pointer
                    ):
                        raise SeedanceError(
                            "Generation node is not bound to the selected creative artifact",
                            code="CINE_GENERATION_NODE_BINDING_MISMATCH",
                        )
                    if node_data.get("prompt") == proof["prompt"]:
                        break
                    try:
                        update = await client.submit_command(
                            resolved_project_id,
                            {
                                "type": "canvas.update_node",
                                "nodeId": node_id,
                                "expectedRevision": snapshot.get("revision"),
                                "patch": {
                                    "data": {
                                        "prompt": proof["prompt"],
                                        "promptProvenance": None,
                                    }
                                },
                                "commandId": f"cine-sync-prompt-{uuid.uuid4().hex}",
                            },
                        )
                    except SeedanceError as exc:
                        if exc.code == "REVISION_CONFLICT" and attempt == 0:
                            continue
                        raise
                    if not update.get("accepted"):
                        raise SeedanceError(
                            "V3 rejected canonical prompt synchronization",
                            code="GENERATION_NODE_CHANGED",
                        )
                    break
                generation_command.update(nodeId=node_id, expectedPrompt=proof["prompt"])
            if action == "validate_generation":
                preflight = await client.validate_generation(
                    resolved_project_id, generation_command
                )
                return {
                    "status": "validated",
                    "outcome": "succeeded",
                    "submitted": False,
                    "model": preflight.get("model"),
                    "reference_count": preflight.get("referenceCount"),
                    "validation_reports": proof["report_paths"],
                    "next_step": "Submit with the same inputs only within existing generation authorization.",
                }
            prompt_optimized = False
            try:
                await client.validate_generation(resolved_project_id, generation_command)
            except SeedanceError as exc:
                if (
                    generation_kind != "video"
                    or exc.code != "PROMPT_FORMAT_INVALID"
                    or not node_id
                ):
                    raise
                snapshot = await client.get_snapshot(resolved_project_id)
                nodes = snapshot.get("nodes", []) if isinstance(snapshot, dict) else []
                target_node = next((node for node in nodes if node.get("id") == node_id), None)
                if target_node is None:
                    raise
                references = await client.get_node_references(resolved_project_id, node_id)
                node_data = target_node.get("data") or {}
                optimized = await client.optimize_prompt(
                    {
                        "prompt": proof["prompt"],
                        "model": model or node_data.get("model"),
                        "provider": node_data.get("provider"),
                        "durationSec": duration_seconds or node_data.get("durationSec"),
                        "aspectRatio": aspect_ratio or node_data.get("aspectRatio") or "16:9",
                        "references": references,
                        "segmentId": node_data.get("segmentId"),
                        "shotMode": "multi-shot-container",
                        "constraints": [
                            "Preserve exact <d> dialogue from the approved native storyboard.",
                            "Treat connected character and environment images as Ref2VA subjects, not keyframes.",
                        ],
                    }
                )
                final_prompt = optimized.get("prompt")
                validation = optimized.get("validation") or {}
                if (
                    not optimized.get("ok")
                    or not isinstance(final_prompt, str)
                    or not final_prompt.strip()
                    or validation.get("valid") is not True
                ):
                    raise SeedanceError(
                        "V3 Prompt Agent did not return a valid final prompt",
                        code="PROMPT_OPTIMIZATION_FAILED",
                    )
                update = await client.submit_command(
                    resolved_project_id,
                    {
                        "type": "canvas.update_node",
                        "nodeId": node_id,
                        "expectedRevision": snapshot.get("revision"),
                        "patch": {
                            "data": {
                                "prompt": final_prompt,
                                "promptProvenance": optimized.get("provenance"),
                                "sourcePrompt": proof["prompt"],
                            }
                        },
                        "commandId": f"cine-optimize-{uuid.uuid4().hex}",
                    },
                )
                if not update.get("accepted"):
                    raise SeedanceError(
                        "V3 rejected the optimized prompt update",
                        code="PROMPT_OPTIMIZATION_UPDATE_FAILED",
                    )
                gen_input["prompt"] = final_prompt
                generation_command["expectedPrompt"] = final_prompt
                await client.validate_generation(resolved_project_id, generation_command)
                prompt_optimized = True

        ui_base_url = os.environ.get("SEEDANCE_UI_BASE_URL", DEFAULT_SEEDANCE_UI_BASE_URL).rstrip(
            "/"
        )
        canvas_url = f"{ui_base_url}/?project={resolved_project_id}"
        cmd_id = f"cmd_{uuid.uuid4().hex[:8]}"

        # ACTION: create_node
        if action == "create_node":
            node_data = dict(data or {})

            # Handle field mapping based on node type
            # - script/text nodes: content should be in data.content
            # - video_prompt/image_prompt: use prompt field
            actual_node_type = node_type or "video_prompt"

            if actual_node_type in ("script", "text"):
                # For script/text nodes, if prompt is provided, treat it as content
                if prompt is not None and "content" not in node_data:
                    node_data["content"] = prompt
                # Brief can still be used for summary
                if brief is not None:
                    node_data["brief"] = brief
            else:
                # For video_prompt/image_prompt/other types, use prompt field
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
                "nodeType": actual_node_type,
                "title": title or "新卡片",
                "parentId": parent_id,
                "data": node_data,
                "commandId": cmd_id,
            }
            res = await client.submit_command(resolved_project_id, cmd)
            if not res.get("accepted"):
                return {
                    "status": "failed",
                    "outcome": "failed",
                    "error": f"画布服务端拒绝创建命令: {res}",
                }

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
                return {
                    "status": "failed",
                    "outcome": "failed",
                    "error": "update_node 需要指定 node_id",
                }

            # Concurrency revision check if expected_revision provided
            project_revision = None
            if expected_revision is not None:
                snap_pre = await client.get_snapshot(resolved_project_id)
                project_revision = snap_pre.get("revision")
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
                cmd["expectedRevision"] = project_revision

            res = await client.submit_command(resolved_project_id, cmd)
            if not res.get("accepted"):
                return {
                    "status": "failed",
                    "outcome": "failed",
                    "error": f"画布服务端拒绝更新命令: {res}",
                }

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
                return {
                    "status": "failed",
                    "outcome": "failed",
                    "error": "delete_node 需要指定 node_id",
                }
            if not confirm:
                return {
                    "status": "rejected",
                    "outcome": "failed",
                    "error": f"安全拦截：删除节点 {node_id} 属于破坏性操作，必须显式传递 confirm=true 进行确认。",
                }

            project_revision = None
            if expected_revision is not None:
                snap_pre = await client.get_snapshot(resolved_project_id)
                nodes_pre = snap_pre.get("nodes", []) if isinstance(snap_pre, dict) else []
                current_node = next((n for n in nodes_pre if n.get("id") == node_id), None)
                if current_node and current_node.get("revision", 1) != expected_revision:
                    return {
                        "status": "conflict",
                        "outcome": "failed",
                        "error": f"版本冲突：画布节点 {node_id} 已更新。",
                        "current_revision": current_node.get("revision", 1),
                    }
                project_revision = snap_pre.get("revision")

            cmd = {
                "type": "canvas.delete_node",
                "nodeId": node_id,
                "commandId": cmd_id,
            }
            if expected_revision is not None:
                cmd["expectedRevision"] = project_revision

            res = await client.submit_command(resolved_project_id, cmd)
            if not res.get("accepted"):
                return {
                    "status": "failed",
                    "outcome": "failed",
                    "error": f"画布服务端拒绝删除命令: {res}",
                }

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
                return {
                    "status": "failed",
                    "outcome": "failed",
                    "error": "connect 需要指定 from_node_id 和 to_node_id",
                }

            cmd = {
                "type": "canvas.connect",
                "from": from_node_id,
                "to": to_node_id,
                "kind": kind or "references",
                "commandId": cmd_id,
            }
            res = await client.submit_command(resolved_project_id, cmd)
            if not res.get("accepted"):
                return {
                    "status": "failed",
                    "outcome": "failed",
                    "error": f"画布服务端拒绝连线命令: {res}",
                }

            snap_conn = await client.get_snapshot(resolved_project_id)
            edges = snap_conn.get("edges", []) if isinstance(snap_conn, dict) else []
            connected = next(
                (e for e in edges if e.get("from") == from_node_id and e.get("to") == to_node_id),
                None,
            )
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
                return {
                    "status": "failed",
                    "outcome": "failed",
                    "error": "disconnect 需要指定 edge_id",
                }

            requested_edge_id = edge_id
            snap_before = await client.get_snapshot(resolved_project_id)
            current_edges = snap_before.get("edges", []) if isinstance(snap_before, dict) else []
            exact_edge = next((edge for edge in current_edges if edge.get("id") == edge_id), None)
            if exact_edge is None:
                # Older Cine runs prefixed a real V3 edge id with a local label
                # (for example edge_ref_C08_E01-08_<real-edge-id>). Resolve only
                # a unique suffix match; never guess between multiple edges.
                candidates = [
                    edge
                    for edge in current_edges
                    if isinstance(edge.get("id"), str)
                    and (
                        edge_id.endswith(edge["id"])
                        or edge["id"].endswith(edge_id)
                        or edge_id.endswith(edge["id"].removeprefix("edge_"))
                    )
                    and len(edge["id"]) >= 16
                ]
                if len(candidates) == 1:
                    edge_id = candidates[0]["id"]

            cmd = {
                "type": "canvas.disconnect",
                "edgeId": edge_id,
                "commandId": cmd_id,
            }
            try:
                res = await client.submit_command(resolved_project_id, cmd)
            except SeedanceNotFoundError as exc:
                if exc.code != "CANVAS_EDGE_NOT_FOUND":
                    raise
                # Disconnect is idempotent. If the read model still exposes the
                # edge, report reconciliation rather than claiming deletion.
                snap_missing = await client.get_snapshot(resolved_project_id)
                edge_still_visible = any(
                    edge.get("id") == edge_id
                    for edge in (snap_missing.get("edges", []) if isinstance(snap_missing, dict) else [])
                )
                if edge_still_visible:
                    return {
                        "status": "reconcile_required",
                        "outcome": "failed",
                        "error_code": "CANVAS_EDGE_READ_WRITE_DRIFT",
                        "edge_id": edge_id,
                        "error": (
                            f"Seedance 快照仍能读到连线 {edge_id}，但命令仓库报告不存在；"
                            "已停止重试，等待画布数据对账。"
                        ),
                    }
                return {
                    "status": "completed",
                    "outcome": "succeeded",
                    "action": "disconnect",
                    "seedance_project_id": resolved_project_id,
                    "seedance_canvas_url": canvas_url,
                    "verified": True,
                    "already_absent": True,
                    "edge_id": edge_id,
                    "summary": f"连线 {edge_id} 已不存在，按幂等删除处理。",
                }
            if not res.get("accepted"):
                return {
                    "status": "failed",
                    "outcome": "failed",
                    "error": f"画布服务端拒绝断开连线命令: {res}",
                }

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
                "requested_edge_id": requested_edge_id if requested_edge_id != edge_id else None,
                "summary": f"已断开连线 {edge_id}，读后验证通过。",
            }

        # ACTION: submit_generation
        elif action == "submit_generation":
            from agentnexus.seedance.production_gate import assert_current

            assert_current(proof)

            cmd = {
                **generation_command,
                "type": "generation.submit",
                "commandId": cmd_id,
            }
            res = await client.submit_command(resolved_project_id, cmd)
            if not res.get("accepted"):
                return {"status": "failed", "outcome": "failed", "error": f"生成提交被拒绝: {res}"}

            # The command response owns this job; a project's latest job may be unrelated.
            latest_job = (res.get("response") or {}).get("job") or res.get("job") or {}
            return {
                "status": "submitted",
                "outcome": "succeeded",
                "action": "submit_generation",
                "validation_reports": proof["report_paths"],
                "seedance_project_id": resolved_project_id,
                "seedance_canvas_url": canvas_url,
                "verified": bool(latest_job.get("id")),
                "prompt_optimized": prompt_optimized,
                "job": latest_job,
                "poll_after_seconds": 20,
                "summary": f"已成功提交 {cmd['kind']} 生成任务 (Job: {latest_job.get('id', 'queued')})，读后验证任务已在队列中。",
            }

        else:
            return {"status": "failed", "outcome": "failed", "error": f"未知的 action: {action}"}

    except Exception as exc:
        from agentnexus.seedance.production_gate import ProductionRejected

        if isinstance(exc, SeedanceError):
            return {
                "status": "rejected",
                "outcome": "failed",
                "error_code": exc.code,
                "error": str(exc),
                "next_step": "Resolve the V3 input error. Use seedance_read_canvas action=models for choices; do not guess settings, resubmit blindly or inspect source code.",
            }
        if isinstance(exc, ProductionRejected):
            detail = exc.detail
            if isinstance(detail, list):
                detail = [
                    {
                        "stage": row.get("stage"),
                        "status": row.get("status"),
                        "errors": row.get("errors", []),
                        "message": row.get("stderr", "")[:1800],
                    }
                    for report in detail
                    for row in report.get("stages", [])
                    if row.get("status") != "passed"
                ]
            return {
                "status": "rejected",
                "outcome": "failed",
                "error_code": exc.code,
                "error": str(exc),
                "validation": detail,
                "next_step": "Fix only the listed creative artifact/input. Do not invent upstream story data, write repair scripts or inspect service source code.",
            }
        raise
    finally:
        if seedance_client is None:
            await client.close()
