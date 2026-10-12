"""Independently review actual generated video and bind the verdict to its inputs."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import shlex
import sys
import uuid
from pathlib import Path
from typing import Any

from filelock import FileLock

from .client import SeedanceClient
from .media_quality_contract import (
    QUALITY_RUBRIC,
    QualityContractError,
    measured_shots,
    quality_response_schema,
    validate_quality_report,
)
from .production_gate import inside
from .skill_modules import pipeline_module

REVIEW_VERSION = 3


def _hash(value) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False).encode()
    ).hexdigest()


def _bytes_hash(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _review_markdown(review: dict) -> str:
    report = review["report"]
    labels = {"pass": "模型质检通过", "revise": "需要返修", "review": "需要复核"}
    lines = [
        f"# 成片质检：{labels[review['decision']]}",
        "",
        report["summary"],
        "",
        f"评审模型：{review['reviewer']['model']}",
        "",
        "技术输入包含实际视频与音轨；模型采样审查不代表逐帧或人工验收。",
        "",
    ]
    if review.get("review_method") == "observer_assisted":
        observer = review.get("evidence_observer") or {}
        lines += [
            "证据模式：观察者辅助。质检模型直接读取文字与抽帧图片；声画观察来自独立模型。",
            "声画观察模型：" + str(observer.get("model", "未记录")),
            "质检模型未直接读取视频流或音轨。",
            "",
        ]
    detections = review.get("cut_detection") or {}
    if detections:
        lines += ["## 实测硬切（逐帧检测，片段内时间）", ""]
        for identity, row in detections.items():
            if row["status"] != "measured":
                text = f"未测量（{row['status']}）"
            elif row["cut_seconds"]:
                text = "、".join(f"{t:.2f}" for t in row["cut_seconds"]) + " 秒"
            else:
                text = "无，整段是一个连续镜头"
            lines.append(f"- {identity}：{text}")
        lines.append("")
    observations = {row["id"]: row for row in report["observations"]}
    for issue in report["issues"]:
        times = [
            f"{observations[i]['start_seconds']:.2f}–{observations[i]['end_seconds']:.2f} 秒"
            for i in issue["evidence_ids"]
        ]
        lines += [
            f"## {issue['item_id']} · {issue['criterion']} · {issue['severity']}",
            "",
            "位置：" + "；".join(times),
            "",
            "预期：" + issue["expected"],
            "",
        ]
        lines += ["观察：" + observations[i]["observation"] for i in issue["evidence_ids"]]
        lines += [
            "",
            "修复：" + issue["repair"]["instruction"],
            "复检标准：" + issue["repair"]["success_criteria"],
            "",
        ]
    if report.get("regression_checks"):
        lines += ["## 上轮问题复检", ""]
        for row in report["regression_checks"]:
            lines += [f"- {row['issue_id']}：{row['decision']}。{row['rationale']}"]
    if review["limitations"]:
        lines += [
            "",
            "## 未解决的证据限制",
            "",
            *["- " + value for value in review["limitations"]],
        ]
    return "\n".join(lines) + "\n"


def _prompt(
    request: dict, creative: dict, previous: dict | None, *, input_mode="direct_av"
) -> str:
    rubric = (
        QUALITY_RUBRIC
        + "\nRespond in Chinese inside JSON. A well-written prompt cannot prove quality. "
        "The MP4 contains the actual rendered performance and soundtrack; explicitly distinguish "
        "what is visibly/heard from what is merely requested. Frame sampling cannot certify "
        "frame-accurate lip sync. If important evidence is absent, use review.\n"
        "The supplied assembly timeline is the actual playback timeline. Observations use those "
        "absolute seconds and may not cross item boundaries when item_id is set. Use cut_id=null "
        "unless exact observed cut mapping was supplied. Give concrete actions, reactions, "
        "pauses and causal transitions, not generic praise or arbitrary numerical taste scores.\n"
        "The attached media receipt ID is rendered-media. Use exactly that media_receipt_id "
        "in every observation. Include item-local observations for every item; film checks "
        "must cite those observations across the full ordered sequence.\n"
        "film_checks contains exactly story_causality and rhythm. Each item's checks contains "
        "all five criteria. Every revise check must have a matching localized issue of the same "
        "criterion (and item for an item check). Each film check cites evidence from every item.\n"
        "Only classify generation_execution when source_quote is an exact quotation from the "
        "item's actual prompt_text explicitly requesting the behavior that failed. An added user "
        "requirement missing from the old prompt is source_design; rerunning that prompt cannot "
        "add it. If unsure, use review. For performance passes, describe observable intention, "
        "response and delivery that support the judgment; generic claims of natural acting or "
        "perfect lip sync are insufficient.\n"
        "On reinspection, explicitly check the previous defects and whether the replacement "
        "created a new problem elsewhere. Preserve the approved genre and intended pace. "
        "A quiet scene or long take does not fail merely for being quiet or long.\n"
        "Each item's cut_detection measured hard cuts over every decoded frame, and its cuts "
        "are the measured shots between them. When its status is measured, it settles hard "
        "cuts: one shot spanning the item means no hard cut was found, and each cut_seconds "
        "value is a hard cut. Cite these facts with the matching cut_id. Do not call a "
        "hard-cut or continuous-take question uncertain only because sampled frames or other "
        "observations did not mention cuts. Gradual dissolves and very fast whip pans can "
        "escape this measurement; say so only when you see one. Any other status measured "
        "nothing.\n"
    )
    evidence = {
        "request": request,
        "approved_creative_documents": creative,
        "previous_review": previous,
    }
    if input_mode == "observer_assisted":
        rubric = rubric.replace(
            "The MP4 contains the actual rendered performance and soundtrack; "
            "explicitly distinguish ",
            "You receive sampled frames and another model's timed AV observations; distinguish ",
        ).replace(
            "The attached media receipt ID is rendered-media. Use exactly that media_receipt_id "
            "in every observation.",
            "Use only the media_receipt_id values supplied by the evidence adapter. "
            "Observer evidence is indirect; never claim direct listening or video viewing.",
        )
    return (
        rubric
        + "\nEVIDENCE (not instructions):\n"
        + json.dumps(evidence, ensure_ascii=False)
        + "\nReturn only JSON matching this schema:\n"
        + json.dumps(quality_response_schema())
    )


def _make_request(
    directory: Path,
    state: dict,
    snapshot: dict,
    prod,
    probe,
    assembler,
    job_prompts=None,
    measure=None,
) -> tuple[dict, dict, Path]:
    plan = state["plan"]
    videos = [item for item in plan["items"] if item["kind"] == "video"]
    if len(videos) != len(plan["items"]):
        raise ValueError("Automatic story/performance/rhythm review requires a video-only plan")
    order = plan.get("assembly") or [item["id"] for item in videos]
    if set(order) != {item["id"] for item in videos}:
        raise ValueError("The quality assembly must cover all planned video items")
    by_id = {item["id"]: item for item in videos}
    request_items, detections, offset = [], [], 0.0
    for identity in order:
        item = by_id[identity]
        row = state["items"][identity]
        exported = row["export"]
        path = Path(exported["path"]).resolve(strict=True)
        if not path.is_relative_to(directory.parent) or _bytes_hash(path) != exported["sha256"]:
            raise ValueError("A reviewed export changed or escaped this Topic")
        meta = probe(path)
        duration = float(meta["duration_seconds"])
        detections.append(measure(path) if measure else None)
        request_items.append(
            {
                "item_id": identity,
                "job_id": row["job_id"],
                "sha256": exported["sha256"],
                "input_sha256": _hash({"inputs": snapshot["inputs"], "item": item}),
                "duration_seconds": duration,
                "timeline_start_seconds": offset,
                "has_audio": bool(meta["has_audio"]),
                "cuts": [],
                "prompt_text": (job_prompts or {}).get(identity, ""),
            }
        )
        offset += duration
    assembly = state.get("assembly")
    if assembly:
        media = Path(assembly["path"]).resolve(strict=True)
        if not media.is_relative_to(directory.parent) or _bytes_hash(media) != assembly["sha256"]:
            raise ValueError("The assembled video changed or escaped this Topic")
        expected_clips = [
            {
                "job_id": state["items"][i]["job_id"],
                "sha256": state["items"][i]["export"]["sha256"],
            }
            for i in order
        ]
        if assembly.get("clips") != expected_clips:
            raise ValueError("The assembly contains an older or reordered output")
        rebuilt = assembler(
            directory, [state["items"][i]["export"] for i in order], verify_cached=True
        )
        if rebuilt.get("status") != "assembled" or rebuilt.get("sha256") != assembly["sha256"]:
            raise ValueError("The assembled bytes do not match the verified generation exports")
        meta = probe(media)
        duration = float(meta["duration_seconds"])
        if abs(offset - duration) > max(0.2, 0.08 * len(order)):
            raise ValueError("Assembly timing cannot be mapped to the current clips")
        # Concat can adjust audio priming by a few milliseconds; bind the final end.
        request_items[-1]["duration_seconds"] = (
            duration - request_items[-1]["timeline_start_seconds"]
        )
        assembled = {"sha256": assembly["sha256"], "duration_seconds": duration, "item_ids": order}
    elif len(order) == 1:
        media = Path(state["items"][order[0]]["export"]["path"])
        assembled = None
    else:
        raise ValueError("Whole-film quality needs an assembled video, not isolated clip scores")
    for row, detection in zip(request_items, detections, strict=True):
        if detection is None:
            continue
        times = [t for t in detection["cut_seconds"] if 0 < t < row["duration_seconds"]]
        row["cut_detection"] = {**detection, "cut_seconds": times}
        if detection["status"] == "measured":
            row["cuts"] = measured_shots(times, row["duration_seconds"])
    request = {
        "plan_sha256": state["plan_sha256"],
        "requirements": plan["quality"]["requirements"],
        "items": request_items,
        "assembly": assembled,
    }
    creative = {
        stage: prod.read(prod.stage_path(directory, stage))
        for stage in ("outline", "cast", "art", "script", "storyboard")
    }
    creative["authorized_scope"] = plan["authorization"]["scope"]
    return request, creative, media


def _baseline_review(root, directory, state, session_id, generation, prod):
    """Authenticate an explicitly pinned review from an immutable prior revision."""
    from .media_quality_receipts import verify_review

    reference = state["plan"]["quality"].get("previous_review")
    if reference is None:
        return None, None
    if (
        not isinstance(reference, dict)
        or set(reference) != {"production_dir", "receipt_path", "receipt_sha256"}
        or any(not isinstance(value, str) or not value.strip() for value in reference.values())
    ):
        raise ValueError("CINE_MEDIA_REVIEW_BASELINE_INVALID")
    expected_hash = reference["receipt_sha256"]
    if not re.fullmatch(r"[a-f0-9]{64}", expected_hash):
        raise ValueError("CINE_MEDIA_REVIEW_BASELINE_INVALID")
    source = inside(root, reference["production_dir"])
    receipt = inside(root, reference["receipt_path"])
    if source == directory or not source.is_dir() or not receipt.is_relative_to(source):
        raise ValueError("CINE_MEDIA_REVIEW_BASELINE_PATH_INVALID")
    if _bytes_hash(receipt) != expected_hash:
        raise ValueError("CINE_MEDIA_REVIEW_BASELINE_CHANGED")
    prior = verify_review(prod.read(receipt))
    if prior.get("session_id") != session_id:
        raise ValueError("CINE_MEDIA_REVIEW_BASELINE_SESSION_MISMATCH")
    old_state = prod.read(inside(root, str(source / generation.STATE)))
    inside(root, old_state["plan_path"])
    for path in old_state["inputs"]:
        if not Path(path).resolve().is_relative_to(root):
            raise ValueError("CINE_MEDIA_REVIEW_BASELINE_PATH_INVALID")
    if old_state["plan"]["project_id"] != state["plan"]["project_id"]:
        raise ValueError("CINE_MEDIA_REVIEW_BASELINE_PROJECT_MISMATCH")
    old_snapshot = generation.review_snapshot(source, old_state)
    review = prior.get("review") or {}
    if (
        prior.get("snapshot_sha256") != _hash(old_snapshot)
        or review.get("plan_sha256") != old_state["plan_sha256"]
        or review.get("status") != "reviewed"
    ):
        raise ValueError("CINE_MEDIA_REVIEW_BASELINE_BINDING_MISMATCH")
    requirements = {row["id"]: row["text"] for row in state["plan"]["quality"]["requirements"]}
    if any(
        requirements.get(row["id"]) != row["text"]
        for row in old_state["plan"]["quality"]["requirements"]
    ):
        raise ValueError("CINE_MEDIA_REVIEW_BASELINE_REQUIREMENTS_CHANGED")
    identities = {item["id"] for item in state["plan"]["items"]}
    report = review["report"]
    if any(issue["item_id"] not in identities for issue in report["issues"]):
        raise ValueError("CINE_MEDIA_REVIEW_BASELINE_ITEMS_MISSING")
    if _bytes_hash(receipt) != expected_hash:
        raise ValueError("CINE_MEDIA_REVIEW_BASELINE_CHANGED")
    return report, {
        "production_dir": str(source),
        "receipt_path": str(receipt),
        "receipt_sha256": expected_hash,
        "snapshot_sha256": prior["snapshot_sha256"],
        "plan_sha256": old_state["plan_sha256"],
        "session_id": session_id,
    }


def _controller_handoff(directory, generation, prod, cache, envelope, report_path):
    """Persist the signed result for the latest matching controller action."""
    state_path = directory / generation.STATE
    with FileLock(str(state_path.with_suffix(".lock")), timeout=0):
        current = prod.read(state_path)
        if (
            _hash(generation.review_snapshot(directory, current))
            != envelope["payload"]["snapshot_sha256"]
        ):
            raise ValueError("CINE_MEDIA_REVIEW_INPUTS_CHANGED")
        active = current.get("active")
        if not isinstance(active, dict) or active.get("operation") != "quality":
            return {}
        action_id = active.get("action_id")
        if not isinstance(action_id, str) or not action_id:
            raise ValueError("CINE_MEDIA_REVIEW_ACTION_INVALID")
        action_hash = hashlib.sha256(action_id.encode("utf-8")).hexdigest()
        path = cache.with_name(f"{cache.stem}-{action_hash}.receipt.json")
        wrapper = {
            "action_id": action_id,
            "result": {
                "status": "reviewed",
                "envelope": envelope,
                "receipt_path": str(cache),
                "report_path": str(report_path),
            },
        }
        if path.exists():
            if prod.read(path) != wrapper:
                raise ValueError("CINE_MEDIA_REVIEW_CONTROLLER_RECEIPT_CHANGED")
        else:
            prod.atomic_json(path, wrapper)
        argv = [
            sys.executable,
            str(Path(prod.__file__).resolve()),
            "next",
            str(directory),
            "--generation-receipt",
            str(path),
        ]
        command = (
            "& " + " ".join("'" + word.replace("'", "''") + "'" for word in argv)
            if os.name == "nt"
            else shlex.join(argv)
        )
        return {
            "controller_receipt_path": str(path),
            "after": "Run this command directly:\n" + command + "\n"
            "The controller receipt is already saved. Do not copy, reconstruct, edit, "
            "or resave the signed JSON; do not rerun the reviewer to pass it to the controller.",
        }


async def review_media(
    server,
    session_id: str,
    *,
    production_dir: str,
    skills_dir,
    seedance_client=None,
    reviewer=None,
) -> dict:
    """Read inputs and call the independent reviewer; no model-supplied verdict is accepted."""
    from .media_quality_provider import (
        MediaReviewUnavailable,
        measure_cuts,
        probe_video,
        review_video,
    )
    from .media_quality_receipts import review_usage_path, sign_review, verify_review

    response = await server.get(f"/v1/sessions/{session_id}", timeout=10)
    response.raise_for_status()
    session = response.json()
    if session.get("id") != session_id or not session.get("workspace"):
        raise ValueError("CINE_SESSION_WORKSPACE_REQUIRED")
    root = Path(session["workspace"]).resolve(strict=True)
    directory = inside(root, production_dir)
    generation = pipeline_module(skills_dir, "generation")
    prod = pipeline_module(skills_dir, "production")
    state = prod.read(directory / generation.STATE)
    if (session.get("labels") or {}).get("seedance.project_id") != state["plan"]["project_id"]:
        raise ValueError("CINE_PROJECT_BINDING_MISMATCH")
    generation.validate_plan(directory, state["plan"])
    if not state["plan"].get("quality"):
        raise ValueError("No quality requirements or review allowance were configured")
    snapshot = generation.review_snapshot(directory, state)
    snapshot_hash = _hash(snapshot)
    settings = pipeline_module(skills_dir, "transcribe").gateway_settings()
    base, key, default_model = settings
    model = os.environ.get("AGENTNEXUS_MEDIA_REVIEW_MODEL") or default_model
    input_mode = os.environ.get("AGENTNEXUS_MEDIA_REVIEW_MODE", "direct_av")
    if input_mode not in {"direct_av", "observer_assisted"}:
        raise ValueError("Unknown media review input mode")
    observer_model = os.environ.get("AGENTNEXUS_MEDIA_OBSERVER_MODEL") or default_model
    if not all(isinstance(v, str) and v for v in (base, key, model)):
        return {"status": "unavailable", "error_code": "CINE_MEDIA_REVIEW_NOT_CONFIGURED"}
    cache_id = _hash(
        {
            "snapshot": snapshot_hash,
            "version": REVIEW_VERSION,
            "model": model,
            "input_mode": input_mode,
            "observer_model": observer_model if input_mode == "observer_assisted" else None,
            "rubric": QUALITY_RUBRIC,
            "schema": quality_response_schema(),
        }
    )
    output = directory / ".cine-validation/media-quality"
    output.mkdir(parents=True, exist_ok=True)
    cache = output / f"{cache_id}.json"
    usage_identity = _hash({"directory": str(directory), "plan": state["plan_sha256"]})
    usage = await asyncio.to_thread(review_usage_path, session_id, usage_identity)
    lock = FileLock(str(output / "review.lock"), timeout=0)
    # Do not block the runner's event loop behind another long review.
    lock.acquire()
    client = seedance_client or SeedanceClient(
        base_url=(session.get("labels") or {}).get("seedance.base_url")
    )
    try:
        baseline_report, baseline = _baseline_review(
            root, directory, state, session_id, generation, prod
        )
        job_prompts = {}
        for item in state["plan"]["items"]:
            row = state["items"][item["id"]]
            job = await client.get_job(row["job_id"])
            if (
                job.get("id") != row["job_id"]
                or job.get("status") != "succeeded"
                or job.get("kind") != "video"
                or job.get("nodeId") != item["node_id"]
                or job.get("projectId") != state["plan"]["project_id"]
                or (job.get("outputRefs") or [job.get("outputRef")])[0]
                != "local://" + row["export"]["sha256"]
            ):
                raise ValueError("Quality review job binding does not match the actual V3 output")
            job_prompts[item["id"]] = (job.get("input") or {}).get("prompt") or ""
        if cache.exists():
            envelope = prod.read(cache)
            payload = verify_review(envelope)
            if (
                payload["snapshot_sha256"] != snapshot_hash
                or payload["session_id"] != session_id
                or payload.get("baseline_review") != baseline
            ):
                raise ValueError("Cached quality receipt belongs to another run")
            current = prod.read(directory / generation.STATE)
            if _hash(generation.review_snapshot(directory, current)) != snapshot_hash:
                raise ValueError("CINE_MEDIA_REVIEW_INPUTS_CHANGED")
            _, current_baseline = _baseline_review(
                root, directory, current, session_id, generation, prod
            )
            if current_baseline != baseline:
                raise ValueError("CINE_MEDIA_REVIEW_BASELINE_CHANGED")
            human_report = cache.with_suffix(".md")
            if not human_report.exists():
                human_report.write_text(_review_markdown(payload["review"]), encoding="utf-8")
            handoff = _controller_handoff(
                directory, generation, prod, cache, envelope, human_report
            )
            return {
                "status": "reviewed",
                "envelope": envelope,
                "receipt_path": str(cache),
                "report_path": str(human_report),
                "reused": True,
                **handoff,
            }
        usage_data = (
            verify_review(prod.read(usage))
            if usage.exists()
            else {"run_id": state["run_id"], "calls": 0}
        )
        if (
            not isinstance(usage_data, dict)
            or usage_data.get("run_id") != state["run_id"]
            or type(usage_data.get("calls")) is not int
            or usage_data["calls"] < 0
        ):
            raise ValueError("Invalid quality call ledger; do not reset it")
        limit = state["plan"]["quality"]["max_review_calls"]
        calls_required = 2 if input_mode == "observer_assisted" else 1
        if usage_data["calls"] + calls_required > limit:
            return {
                "status": "unavailable",
                "error_code": "CINE_MEDIA_REVIEW_LIMIT",
                "max_review_calls": limit,
            }
        request, creative, media = await asyncio.to_thread(
            _make_request,
            directory,
            state,
            snapshot,
            prod,
            probe_video,
            pipeline_module(skills_dir, "media_assembly").assemble,
            job_prompts,
            measure_cuts,
        )
        history = (state.get("quality") or {}).get("history") or []
        previous = baseline_report
        if history:
            prior = verify_review(history[-1]["envelope"])
            if (
                prior.get("session_id") != session_id
                or prior["review"]["plan_sha256"] != state["plan_sha256"]
            ):
                raise ValueError("Previous quality review belongs to another production")
            previous = prior["review"]["report"]
        with FileLock(str(usage.with_suffix(".lock")), timeout=0):
            latest_usage = verify_review(prod.read(usage)) if usage.exists() else usage_data
            if latest_usage["calls"] + calls_required > limit:
                return {
                    "status": "unavailable",
                    "error_code": "CINE_MEDIA_REVIEW_LIMIT",
                    "max_review_calls": limit,
                }
            usage_data = {**latest_usage, "calls": latest_usage["calls"] + calls_required}
            prod.atomic_json(usage, sign_review(usage_data))
        # The Topic copy is informational; deleting it cannot reset the runner ledger.
        prod.atomic_json(output / "usage.json", usage_data)
        review_call = reviewer or review_video
        extra_arguments: dict[str, Any] = {}
        if input_mode == "observer_assisted":
            from .media_quality_gpt import review_evidence

            review_call = reviewer or review_evidence
            extra_arguments["observer_settings"] = (base, key, observer_model)
        response = await review_call(
            path=media,
            prompt=_prompt(request, creative, previous, input_mode=input_mode),
            response_schema=quality_response_schema(),
            settings=(base, key, model),
            timeout_seconds=state["plan"]["quality"].get("review_timeout_seconds", 300),
            **extra_arguments,
        )
        assembled = request["assembly"]
        identity = None if assembled else request["items"][0]["item_id"]
        expected_hash = assembled["sha256"] if assembled else request["items"][0]["sha256"]
        if response["original_sha256"] != expected_hash:
            raise ValueError("Reviewer input bytes differ from the bound media")
        duration = (
            assembled["duration_seconds"] if assembled else request["items"][0]["duration_seconds"]
        )
        if abs(response["duration_seconds"] - duration) > 0.01:
            raise ValueError("Reviewer input duration differs from the bound media")
        invocation = {
            "call_id": response["invocation"].get("response_id") or uuid.uuid4().hex,
            "model": response["invocation"].get("model_version") or model,
            "independent_context": True,
            "media": [
                {
                    "receipt_id": "rendered-media",
                    "item_id": identity,
                    "sha256": expected_hash,
                    "transport_sha256": response["transport_sha256"],
                    "start_seconds": 0.0,
                    "end_seconds": duration,
                    "modalities": ["video", "audio"] if response["has_audio"] else ["video"],
                }
            ],
        }
        if input_mode == "observer_assisted":
            if response.get("input_mode") != input_mode:
                raise ValueError("Reviewer returned the wrong media input mode")
            observed = response["observer_invocation"]
            invocation.update(
                input_mode=input_mode,
                reviewer_modalities=response["reviewer_modalities"],
                media=[{**row, "item_id": identity} for row in response["reviewer_media"]],
                observer={
                    "call_id": observed["call_id"],
                    "model": observed["model"],
                    "independent_context": True,
                    "media": [{**row, "item_id": identity} for row in observed["media"]],
                },
            )
        checked = validate_quality_report(
            response["report"], request=request, invocation=invocation, previous_report=previous
        )
        current = prod.read(directory / generation.STATE)
        if _hash(generation.review_snapshot(directory, current)) != snapshot_hash:
            raise ValueError("CINE_MEDIA_REVIEW_INPUTS_CHANGED")
        _, current_baseline = _baseline_review(
            root, directory, current, session_id, generation, prod
        )
        if current_baseline != baseline:
            raise ValueError("CINE_MEDIA_REVIEW_BASELINE_CHANGED")
        payload = {
            "schema_version": 1,
            "session_id": session_id,
            "snapshot_sha256": snapshot_hash,
            "review": checked,
            "transport": {k: v for k, v in response.items() if k != "report"},
        }
        if baseline is not None:
            payload["baseline_review"] = baseline
        envelope = sign_review(payload)
        prod.atomic_json(cache, envelope)
        human_report = cache.with_suffix(".md")
        human_report.write_text(_review_markdown(checked), encoding="utf-8")
        handoff = _controller_handoff(directory, generation, prod, cache, envelope, human_report)
        return {
            "status": "reviewed",
            "envelope": envelope,
            "receipt_path": str(cache),
            "report_path": str(human_report),
            "review_calls": usage_data["calls"],
            **handoff,
        }
    except (MediaReviewUnavailable, QualityContractError) as exc:
        return {
            "status": "unavailable",
            "error_code": getattr(exc, "code", "CINE_MEDIA_REVIEW_INVALID"),
            "error": str(exc),
            "acceptance": "pending",
        }
    finally:
        lock.release()
        if seedance_client is None:
            await client.close()
