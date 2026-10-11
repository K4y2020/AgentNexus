"""Persist authorized media work until export, without claiming artistic acceptance."""

from __future__ import annotations

import hashlib
import json
import math
import re
import time
from pathlib import Path
from typing import Any

from filelock import FileLock

try:
    from . import production as prod
    from . import quality_loop
    from .stage_graph import ORDER
except ImportError:
    import production as prod
    import quality_loop
    from stage_graph import ORDER

STATE = Path(".cine-validation/generation.json")
MAX_IO_FAILURES = 3
POLL_SECONDS = 20


def _hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False).encode("utf-8")
    ).hexdigest()


def _positive(value: Any, name: str, maximum: int) -> int:
    if type(value) is not int or not 1 <= value <= maximum:
        raise ValueError(f"{name} must be an integer in 1..{maximum}")
    return value


def _text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} is required")
    return value


def _pointer(document: Any, pointer: str) -> Any:
    if not pointer.startswith("/"):
        raise ValueError("production_pointer must be an absolute JSON pointer")
    try:
        for part in pointer[1:].split("/"):
            part = part.replace("~1", "/").replace("~0", "~")
            document = document[int(part)] if isinstance(document, list) else document[part]
    except (IndexError, KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"Unknown production pointer: {pointer}") from exc
    return document


def validate_plan(directory: Path, plan: dict) -> None:
    """A plan records an existing authorization; creating one never grants permission."""
    if not isinstance(plan, dict) or plan.get("schema_version") != 1:
        raise ValueError("generation plan schema_version must be 1")
    if plan.get("canvas_strategy", "import_storyboard") not in (
        "import_storyboard",
        "existing_nodes",
    ):
        raise ValueError("canvas_strategy must be import_storyboard or existing_nodes")
    _text(plan.get("project_id"), "project_id")
    authorization = plan.get("authorization")
    if not isinstance(authorization, dict) or authorization.get("allowed") is not True:
        raise ValueError("Explicit media generation authorization is required")
    _text(authorization.get("scope"), "authorization.scope (the user's actual request)")
    _positive(authorization.get("max_submissions"), "max_submissions", 10000)
    _positive(authorization.get("max_attempts_per_item"), "max_attempts_per_item", 10)
    _positive(plan.get("max_wait_seconds", 3600), "max_wait_seconds", 86400)
    models = authorization.get("models")
    if (
        not isinstance(models, list)
        or not models
        or any(not isinstance(m, str) or not m for m in models)
    ):
        raise ValueError("authorization.models must name the authorized generation models")
    items = plan.get("items")
    if not isinstance(items, list) or not items:
        raise ValueError("generation plan needs items")
    ids, nodes = set(), set()
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("Each generation item must be an object")
        identity = _text(item.get("id"), "item.id")
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", identity) or identity in ids:
            raise ValueError("Generation item IDs must be unique simple identifiers")
        ids.add(identity)
        node = _text(item.get("node_id"), "item.node_id")
        if node in nodes:
            raise ValueError("A generation plan may target each node only once")
        nodes.add(node)
        stage = item.get("production_stage")
        kind = item.get("kind")
        if kind not in ("image", "video") or stage not in ("cast", "art", "storyboard"):
            raise ValueError("Unsupported generation kind or production stage")
        if kind == "video" and stage != "storyboard":
            raise ValueError("Videos must belong to the storyboard")
        if item.get("model") not in models:
            raise ValueError("Item model is outside the authorized scope")
        pointer = _text(item.get("production_pointer"), "production_pointer")
        _text(_pointer(prod.read(prod.stage_path(directory, stage)), pointer), "prompt")
        depends = item.get("depends_on", [])
        if not isinstance(depends, list) or any(not isinstance(d, str) for d in depends):
            raise ValueError("depends_on must be a list of item IDs")
    by_id = {item["id"]: item for item in items}
    visiting, visited = set(), set()

    def visit(identity: str) -> None:
        if identity not in ids or identity in visiting:
            raise ValueError("Generation dependencies must exist and be acyclic")
        if identity in visited:
            return
        visiting.add(identity)
        for dependency in by_id[identity].get("depends_on", []):
            visit(dependency)
        visiting.remove(identity)
        visited.add(identity)

    for identity in ids:
        visit(identity)
    video_segments = {}
    for item in items:
        if item["kind"] != "video":
            continue
        pointer = item["production_pointer"]
        if not re.fullmatch(r"/episodes/\d+/segments/\d+/h3Prompt", pointer):
            raise ValueError("Video items must bind a segment h3Prompt")
        segment = _pointer(
            prod.read(prod.stage_path(directory, "storyboard")), pointer.rsplit("/", 1)[0]
        )
        video_segments[segment["id"]] = (item, segment)
    for item, segment in video_segments.values():
        previous = video_segments.get(segment.get("continuesFrom"))
        if previous and previous[0]["id"] not in item.get("depends_on", []):
            raise ValueError("A continued segment must depend on its planned predecessor")
    assembly = plan.get("assembly", [])
    if not isinstance(assembly, list) or any(not isinstance(i, str) for i in assembly):
        raise ValueError("assembly must be an ordered list of video item IDs")
    if len(set(assembly)) != len(assembly) or any(
        i not in by_id or by_id[i]["kind"] != "video" for i in assembly
    ):
        raise ValueError("assembly must contain unique video item IDs from this plan")
    quality_loop.validate(plan)


def review_snapshot(directory: Path, state: dict) -> dict:
    return quality_loop.snapshot(directory, state)


def _inputs(directory: Path, source_text: Path | None) -> dict:
    paths = [prod.stage_path(directory, stage) for stage in ORDER]
    paths += [prod.source_material_path(directory), directory / "production.json"]
    if source_text is not None:
        paths.append(source_text.resolve(strict=True))
    return {str(path): prod.fingerprint(path) for path in paths}


def _blocked(reason: str, detail: Any, **extra) -> dict:
    return {
        "action": "escalate",
        "stage": "generation",
        "reason": reason,
        "detail": detail,
        **extra,
    }


def _job(result: dict, state: dict, item: dict, record: dict) -> dict:
    job = result.get("job")
    if not isinstance(job, dict) or not isinstance(job.get("id"), str) or not job["id"]:
        raise ValueError("Generation receipt must contain the exact returned job")
    if job.get("projectId") != state["plan"]["project_id"] or job.get("kind") != item["kind"]:
        raise ValueError("Generation receipt project/kind mismatch")
    if job.get("nodeId") != item["node_id"]:
        raise ValueError("Generation receipt node mismatch")
    if record.get("job_id") and record["job_id"] != job["id"]:
        raise ValueError("Generation receipt belongs to another job")
    return job


def _delay(result: dict) -> float:
    seconds = result.get("poll_after_seconds", POLL_SECONDS)
    if not isinstance(seconds, (int, float)) or not math.isfinite(seconds):
        return POLL_SECONDS
    return min(300, max(5, seconds))


def _export(directory: Path, result: dict, item: dict, record: dict) -> dict:
    if result.get("job_id") != record.get("job_id") or result.get("media_kind") != item["kind"]:
        raise ValueError("Export receipt job/kind mismatch")
    path = Path(_text(result.get("path"), "export.path")).resolve(strict=True)
    if not path.is_relative_to(directory.parent) or not path.is_file():
        raise ValueError("Export must be a file inside this Topic")
    if not path.stat().st_size or prod.digest(path) != result.get("sha256"):
        raise ValueError("Export bytes do not match the receipt")
    job = record.get("job") or {}
    references = job.get("outputRefs") or [job.get("outputRef")]
    if not references or references[0] != "local://" + str(result.get("sha256")):
        raise ValueError("Export does not match the exact job's first output")
    return {**result, "path": str(path), "acceptance": "pending"}


def _predecessor(directory: Path, plan: dict, item: dict) -> str | None:
    if item["kind"] != "video":
        return None
    storyboard = prod.read(prod.stage_path(directory, "storyboard"))
    segment = _pointer(storyboard, item["production_pointer"].rsplit("/", 1)[0])
    previous = segment.get("continuesFrom")
    if not previous:
        return None
    for candidate in plan["items"]:
        if (
            candidate["kind"] == "video"
            and _pointer(storyboard, candidate["production_pointer"].rsplit("/", 1)[0]).get("id")
            == previous
        ):
            return candidate["id"]
    return None


def _continuation(directory: Path, state: dict, item: dict, result: dict) -> None:
    plan = state["plan"]
    previous = _predecessor(directory, plan, item)
    source = next(i for i in plan["items"] if i["id"] == previous)
    exported = state["items"][previous]
    if result.get("seedance_project_id") != plan["project_id"]:
        raise ValueError("Continuation snapshot project mismatch")
    slots = [
        node
        for node in result.get("nodes", [])
        if node.get("type") == "image"
        and (node.get("data") or {}).get("continuationSourceNodeId") == source["node_id"]
        and (node.get("data") or {}).get("continuationTargetNodeId") == item["node_id"]
    ]
    if len(slots) != 1:
        raise ValueError("Continuation needs exactly one source/target frame slot")
    data = slots[0]["data"]
    if (
        data.get("continuationSourceJobId") != exported["job_id"]
        or data.get("continuationSourceOutputRef") != "local://" + exported["export"]["sha256"]
        or not re.fullmatch(r"local://[a-f0-9]{64}", str(data.get("activeOutputRef")))
    ):
        raise ValueError("Continuation frame is missing or belongs to an older video job")
    target = next((n for n in result.get("nodes", []) if n.get("id") == item["node_id"]), {})
    if not any(r.get("id") == "node:" + slots[0]["id"] for r in target.get("references", [])):
        raise ValueError("Continuation frame is not a resolved image reference of the target")
    state["items"][item["id"]]["continuation_verified"] = exported["job_id"]


def _record(directory: Path, state: dict, envelope: dict, now: float) -> None:
    if not isinstance(envelope, dict):
        raise ValueError("generation receipt must contain action_id and result")
    action_id = envelope.get("action_id")
    if not isinstance(action_id, str):
        raise ValueError("generation receipt action_id is required")
    digest = _hash(envelope)
    if action_id in state["receipts"]:
        if state["receipts"][action_id] != digest:
            raise ValueError("A consumed generation receipt cannot be replaced")
        return
    active = state.get("active")
    if not active or active["action_id"] != action_id:
        raise ValueError("Receipt is not for the outstanding generation action")
    result = envelope.get("result")
    if not isinstance(result, dict):
        raise ValueError("generation receipt result must be the tool's JSON object")
    if active["operation"] == "quality":
        if result.get("status") == "reviewed":
            quality_loop.receive(directory, state, result)
            state["receipts"][action_id] = digest
            state.update(active=None, io_failures=0, retry_at=0, last_error=None)
        else:
            state["halted"] = "quality_review_unavailable"
            state["last_error"] = result
        return
    item = next(i for i in state["plan"]["items"] if i["id"] == active["item_id"])
    record = state["items"][item["id"]]
    operation = active["operation"]
    success = result.get("status")
    if operation == "continuation" and success == "completed":
        try:
            _continuation(directory, state, item, result)
        except ValueError as exc:
            state["halted"] = "generation_continuation_unverified"
            state["last_error"] = {"item_id": item["id"], "error": str(exc)}
            return
    elif operation == "preflight" and success == "validated":
        record["status"] = "ready"
    elif operation in ("submit", "poll") and success in ("submitted", "completed"):
        if operation == "submit" and result.get("generation_request_id") != record["request_id"]:
            raise ValueError("Submission receipt request ID mismatch")
        job = _job(result, state, item, record)
        status = job.get("status")
        if status not in (
            "queued",
            "pending",
            "running",
            "processing",
            "succeeded",
            "failed",
            "cancelled",
        ):
            raise ValueError("Unknown generation job status")
        record["job_id"] = job["id"]
        record["job"] = job
        record["status"] = status if status in ("succeeded", "failed", "cancelled") else "running"
        record["poll_at"] = now + _delay(result)
    elif operation == "export" and success == "exported":
        record["export"] = _export(directory, result, item, record)
        record["status"] = "exported"
    else:
        # Unknown submission outcomes keep the same request and reserved allowance.
        state["io_failures"] += 1
        state["retry_at"] = now + _delay(result)
        state["last_error"] = result
        if success == "rejected" or state["io_failures"] >= MAX_IO_FAILURES:
            state["halted"] = f"generation_{operation}_failed"
        return
    state["receipts"][action_id] = digest
    record.setdefault("history", []).append({"operation": operation, "result": result})
    state.update(active=None, io_failures=0, retry_at=0, last_error=None)


def _action(state: dict, item: dict, operation: str, arguments: dict) -> dict:
    state["sequence"] += 1
    active = {
        "action": "generation_tool",
        "action_id": f"{state['run_id']}-{state['sequence']}",
        "item_id": item["id"],
        "operation": operation,
        "tool": "cine_review_media"
        if operation == "quality"
        else "seedance_read_canvas"
        if operation in ("poll", "continuation")
        else "seedance_edit_canvas",
        "arguments": arguments,
        "after": "Save {action_id, result: <unaltered tool JSON>} and run production.py next "
        "with --generation-receipt <file>. Reuse this action/request after interruption.",
    }
    state["active"] = active
    return active


def _generation_args(directory: Path, state: dict, item: dict) -> dict:
    args = {
        "project_id": state["plan"]["project_id"],
        "node_id": item["node_id"],
        "generation_kind": item["kind"],
        "production_dir": str(directory),
        "production_stage": item["production_stage"],
        "production_pointer": item["production_pointer"],
        "model": item["model"],
    }
    if state.get("source_text"):
        args["source_text"] = state["source_text"]
    previous = _predecessor(directory, state["plan"], item)
    if previous:
        args["expected_continuation_job_id"] = state["items"][previous]["job_id"]
    return args


def _advance(directory: Path, state: dict, now: float, assemble: bool) -> dict:
    if state.get("halted"):
        return _blocked(state["halted"], state.get("last_error"))
    if state.get("active"):
        if state.get("retry_at", 0) > now:
            return {
                "action": "generation_wait",
                "retry_after_seconds": math.ceil(state["retry_at"] - now),
            }
        return state["active"]
    plan = state["plan"]
    limits = plan["authorization"]
    waits = []
    blocked = []
    reviews = []
    for item in plan["items"]:
        record = state["items"][item["id"]]
        if record["status"] == "exported":
            _export(directory, record["export"], item, record)
    # Collect already-paid results before considering new submissions or blockers.
    items = sorted(
        plan["items"],
        key=lambda item: (state["items"][item["id"]]["status"] not in ("running", "succeeded"),),
    )
    for item in items:
        record = state["items"][item["id"]]
        status = record["status"]
        if status == "exported":
            continue
        if status == "cancelled":
            blocked.append(
                _blocked(
                    "generation_cancelled", {"item_id": item["id"], "job_id": record["job_id"]}
                )
            )
            continue
        if status == "failed":
            if record["attempts"] >= limits["max_attempts_per_item"]:
                blocked.append(
                    _blocked(
                        "generation_attempts_exhausted",
                        {"item_id": item["id"], "job": record["job"]},
                    )
                )
                continue
            record.update(status="pending", job_id=None)
            status = "pending"
        if any(state["items"][dep]["status"] != "exported" for dep in item.get("depends_on", [])):
            continue
        image_dependencies = [
            dep
            for dep in item.get("depends_on", [])
            if next(i for i in plan["items"] if i["id"] == dep)["kind"] == "image"
        ]
        if status in ("pending", "ready") and image_dependencies:
            reviews.append(
                {
                    "action": "review_media",
                    "delivery": "media",
                    "acceptance": "pending",
                    "exports": {dep: state["items"][dep]["export"] for dep in image_dependencies},
                    "pending_items": [item["id"]],
                    "detail": "New reference images require visual acceptance before dependent "
                    "generation. This controller cannot authenticate an image review; use the "
                    "existing reviewed-assets workflow after inspection. Keep acceptance pending.",
                }
            )
            continue
        previous = _predecessor(directory, plan, item)
        if (
            status in ("pending", "ready")
            and previous
            and (record.get("continuation_verified") != state["items"][previous].get("job_id"))
        ):
            return _action(
                state,
                item,
                "continuation",
                {
                    "action": "canvas",
                    "project_id": plan["project_id"],
                    "detail_level": "full",
                    "node_types": ["image", "video_prompt"],
                },
            )
        arguments = _generation_args(directory, state, item)
        if status == "pending":
            if state["submissions"] >= limits["max_submissions"]:
                blocked.append(
                    _blocked(
                        "generation_submission_limit",
                        {"max_submissions": limits["max_submissions"]},
                    )
                )
                continue
            return _action(
                state, item, "preflight", {"action": "validate_generation", **arguments}
            )
        if status == "ready":
            if state["submissions"] >= limits["max_submissions"]:
                blocked.append(
                    _blocked(
                        "generation_submission_limit",
                        {"max_submissions": limits["max_submissions"]},
                    )
                )
                continue
            record["attempts"] += 1
            state["submissions"] += 1
            record["request_id"] = f"cine-{state['run_id']}-{item['id']}-{record['attempts']}"
            record.update(status="submitting", started_at=now)
            return _action(
                state,
                item,
                "submit",
                {
                    "action": "submit_generation",
                    **arguments,
                    "generation_allowed": True,
                    "generation_request_id": record["request_id"],
                },
            )
        if status == "running":
            if now - record["started_at"] >= plan.get("max_wait_seconds", 3600):
                blocked.append(
                    _blocked(
                        "generation_wait_timeout",
                        {"item_id": item["id"], "job_id": record["job_id"]},
                    )
                )
                continue
            if record["poll_at"] > now:
                waits.append(record["poll_at"] - now)
                continue
            return _action(state, item, "poll", {"action": "job", "job_id": record["job_id"]})
        if status == "succeeded":
            return _action(
                state,
                item,
                "export",
                {
                    "action": "export_video" if item["kind"] == "video" else "export_image",
                    "job_id": record["job_id"],
                },
            )
    if waits:
        return {"action": "generation_wait", "retry_after_seconds": math.ceil(min(waits))}
    exports = {
        identity: row["export"]
        for identity, row in state["items"].items()
        if row["status"] == "exported"
    }
    if blocked:
        return {**blocked[0], "failures": blocked, "exports": exports, "acceptance": "pending"}
    if reviews:
        return {**reviews[0], "reviews": reviews, "exports": exports}
    if len(exports) != len(plan["items"]):
        return _blocked(
            "generation_dependencies_pending", "No dependency is ready; inspect the saved jobs."
        )
    if plan.get("assembly"):
        if assemble:
            try:
                from .media_assembly import assemble as assemble_clips
            except ImportError:
                from media_assembly import assemble as assemble_clips
            receipt = assemble_clips(
                directory,
                [exports[i] for i in plan["assembly"]],
                directory.parent / "outputs/cine",
            )
            if receipt.get("status") != "assembled":
                return _blocked("generation_assembly_failed", receipt)
            state["assembly"] = receipt
        receipt = state.get("assembly")
        if not receipt:
            return {
                "action": "assemble_media",
                "detail": "Run production.py next <production> --assemble-media.",
                "items": plan["assembly"],
            }
        path = Path(receipt["path"]).resolve(strict=True)
        if not path.is_relative_to(directory.parent) or prod.digest(path) != receipt["sha256"]:
            return _blocked("generation_assembly_changed", str(path))
    if plan.get("quality"):
        decision = quality_loop.advance(directory, state)
        return (
            decision if decision is not None else _advance(directory, state, now, assemble=False)
        )
    return {
        "action": "review_media",
        "delivery": "media",
        "acceptance": "pending",
        "exports": exports,
        "assembly": state.get("assembly"),
        "unverified": [
            "adaptation_quality",
            "visual_quality",
            "motion",
            "audio",
            "dialogue",
            "continuity",
        ],
        "detail": "Requested media is exported. Inspect the actual outputs and listen to the "
        "complete soundtrack. This controller does not certify creative or audiovisual quality. "
        "Report pending acceptance; never manufacture a passed review receipt or resubmit "
        "completed jobs.",
    }


def next_step(
    directory: Path,
    *,
    plan_path: Path | None = None,
    receipt_path: Path | None = None,
    source_text: Path | None = None,
    retry: bool = False,
    assemble: bool = False,
) -> dict:
    """Return one durable tool action; this function never submits a generation itself."""
    directory = directory.resolve()
    path = directory / STATE
    path.parent.mkdir(parents=True, exist_ok=True)
    with FileLock(str(path.with_suffix(".lock")), timeout=5):
        state = prod.read(path) if path.exists() else None
        if path.exists() and (
            not isinstance(state, dict)
            or state.get("schema_version") != 1
            or not {
                "plan",
                "plan_sha256",
                "plan_path",
                "inputs",
                "run_id",
                "items",
                "submissions",
                "sequence",
                "receipts",
                "active",
                "io_failures",
            }
            <= state.keys()
        ):
            raise ValueError("Invalid generation state; do not reset it and resubmit")
        if plan_path:
            plan_path = plan_path.resolve(strict=True)
            plan = prod.read(plan_path)
            validate_plan(directory, plan)
            if state and state["plan_sha256"] != _hash(plan):
                return _blocked(
                    "generation_plan_changed",
                    "Keep this run's plan; use a new production revision for a new scope.",
                )
            if state is None:
                state = {
                    "schema_version": 1,
                    "plan": plan,
                    "plan_sha256": _hash(plan),
                    "plan_path": str(plan_path),
                    "inputs": _inputs(directory, source_text),
                    "source_text": str(source_text) if source_text else None,
                    "run_id": _hash(
                        [str(directory), _hash(plan), _inputs(directory, source_text)]
                    )[:24],
                    "items": {
                        i["id"]: {"status": "pending", "attempts": 0} for i in plan["items"]
                    },
                    "submissions": 0,
                    "sequence": 0,
                    "receipts": {},
                    "active": None,
                    "io_failures": 0,
                }
        if state is None:
            raise ValueError(
                "No generation plan: pass --generation-plan with the authorized scope first"
            )
        current_plan = prod.read(Path(state["plan_path"]))
        if _hash(current_plan) != state["plan_sha256"]:
            return _blocked("generation_plan_changed", state["plan_path"])
        if (state.get("quality") or {}).get("pending_design"):
            decision = quality_loop.design_step(directory, state, retry=retry)
            prod.atomic_json(path, state)
            if decision is not None:
                return {**decision, "generation_state": str(path)}
        changed = [p for p, h in state["inputs"].items() if prod.fingerprint(Path(p)) != h]
        if changed:
            return _blocked("generation_inputs_changed", changed)
        now = time.time()
        if retry:
            state.update(halted=None, io_failures=0, retry_at=0)
            for row in state["items"].values():
                if row["status"] == "running":
                    row["started_at"] = now
        if receipt_path:
            _record(directory, state, prod.read(receipt_path), now)
        result = _advance(directory, state, now, assemble)
        prod.atomic_json(path, state)
        return {
            **result,
            "generation_state": str(path),
            "submissions_reserved": state["submissions"],
            "max_submissions": state["plan"]["authorization"]["max_submissions"],
        }
