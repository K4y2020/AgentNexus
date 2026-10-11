"""Use authenticated media reviews for bounded local repairs and reinspection."""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

try:
    from . import production as prod
    from .stage_graph import ORDER, STAGES
except ImportError:
    import production as prod
    from stage_graph import ORDER, STAGES


def digest(value) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()
    ).hexdigest()


def validate(plan: dict) -> None:
    config = plan.get("quality")
    if config is None:
        return
    if not isinstance(config, dict):
        raise ValueError("quality must define review requirements and limits")
    requirements = config.get("requirements")
    if not isinstance(requirements, list) or not requirements:
        raise ValueError("Quality review needs the user's actual requirements")
    seen = set()
    for row in requirements:
        if (
            not isinstance(row, dict)
            or set(row) != {"id", "text"}
            or any(not isinstance(row[k], str) or not row[k].strip() for k in ("id", "text"))
            or row["id"] in seen
        ):
            raise ValueError("Quality requirements need unique IDs and nonempty text")
        seen.add(row["id"])
    for key, maximum in (
        ("max_review_calls", 20),
        ("max_rework_rounds", 5),
        ("max_design_edits", 10),
    ):
        value = config.get(key)
        minimum = 1 if key == "max_review_calls" else 0
        if type(value) is not int or not minimum <= value <= maximum:
            raise ValueError(f"quality.{key} must be in {minimum}..{maximum}")
    timeout = config.get("review_timeout_seconds", 300)
    if type(timeout) is not int or not 30 <= timeout <= 540:
        raise ValueError("quality.review_timeout_seconds must be in 30..540")
    previous = config.get("previous_review")
    if previous is not None:
        if not isinstance(previous, dict) or set(previous) != {
            "production_dir",
            "receipt_path",
            "receipt_sha256",
        }:
            raise ValueError(
                "quality.previous_review must pin the prior directory and signed receipt"
            )
        if any(
            not isinstance(previous[key], str) or not previous[key].strip()
            for key in ("production_dir", "receipt_path")
        ):
            raise ValueError("quality.previous_review paths must be nonempty")
        expected = previous["receipt_sha256"]
        if (
            not isinstance(expected, str)
            or len(expected) != 64
            or any(c not in "0123456789abcdef" for c in expected)
        ):
            raise ValueError("quality.previous_review requires the receipt's SHA-256")
    items = plan["items"]
    if any(item["kind"] != "video" for item in items):
        raise ValueError(
            "Story/performance/rhythm review currently requires reviewed assets and video items"
        )
    if len(items) > 1 and set(plan.get("assembly", [])) != {item["id"] for item in items}:
        raise ValueError("Quality review requires every video in the full-film assembly")


def snapshot(directory: Path, state: dict) -> dict:
    if (
        digest(prod.read(Path(state["plan_path"]))) != state["plan_sha256"]
        or digest(state["plan"]) != state["plan_sha256"]
    ):
        raise ValueError("Quality plan changed")
    for name, expected in state["inputs"].items():
        if prod.fingerprint(Path(name)) != expected:
            raise ValueError("Quality source inputs changed")
    outputs = []
    for item in state["plan"]["items"]:
        row = state["items"][item["id"]]
        if row["status"] != "exported":
            raise ValueError("Quality review requires all selected outputs to be exported")
        exported = row["export"]
        path = Path(exported["path"]).resolve(strict=True)
        if not path.is_relative_to(directory.parent) or prod.digest(path) != exported["sha256"]:
            raise ValueError("Quality media changed or escaped the Topic")
        outputs.append(
            {
                "item": item,
                "attempts": row["attempts"],
                "job_id": row["job_id"],
                "request_id": row["request_id"],
                "export": {k: exported[k] for k in ("path", "sha256", "media_kind")},
            }
        )
    assembly = state.get("assembly")
    if assembly:
        path = Path(assembly["path"]).resolve(strict=True)
        if not path.is_relative_to(directory.parent) or prod.digest(path) != assembly["sha256"]:
            raise ValueError("Quality assembly changed or escaped the Topic")
        assembly = {k: assembly[k] for k in ("path", "sha256", "clips")}
    return {
        "schema_version": 1,
        "production_dir": str(directory),
        "run_id": state["run_id"],
        "project_id": state["plan"]["project_id"],
        "plan_sha256": state["plan_sha256"],
        "requirements": state["plan"]["quality"]["requirements"],
        "inputs": state["inputs"],
        "outputs": outputs,
        "assembly": assembly,
    }


def _verify(directory, state, envelope):
    try:
        from agentnexus.seedance.media_quality_receipts import verify_review
    except ModuleNotFoundError as exc:
        if not (exc.name == "agentnexus" or (exc.name or "").startswith("agentnexus.")):
            raise
        try:
            from .quality_receipt import verify_review
        except ImportError:
            from quality_receipt import verify_review

    payload = verify_review(envelope)
    if payload.get("snapshot_sha256") != digest(snapshot(directory, state)):
        raise ValueError("The quality receipt belongs to older or different inputs/media")
    review = payload.get("review") or {}
    if review.get("status") != "reviewed" or review.get("plan_sha256") != state["plan_sha256"]:
        raise ValueError("Invalid quality review binding")
    if review.get("decision") not in ("pass", "revise", "review"):
        raise ValueError("Unknown quality disposition")
    return review


def receive(directory, state, result):
    envelope = result.get("envelope")
    review = _verify(directory, state, envelope)
    quality = state.setdefault("quality", {})
    quality.setdefault("history", []).append(
        {"envelope": envelope, "receipt_path": result.get("receipt_path")}
    )
    quality.update(
        envelope=envelope,
        last_review=review,
        disposition=review["decision"],
        receipt_path=result.get("receipt_path"),
    )


def _affected(state, identities):
    affected = set(identities)
    if not affected or not affected <= state["items"].keys():
        raise ValueError("Repair must identify existing generated items")
    while True:
        added = {
            item["id"]
            for item in state["plan"]["items"]
            if any(dep in affected for dep in item.get("depends_on", []))
        }
        if added <= affected:
            return affected
        affected |= added


def _reserve_possible(state, affected):
    limits = state["plan"]["authorization"]
    return state["submissions"] + len(affected) <= limits["max_submissions"] and all(
        state["items"][identity]["attempts"] < limits["max_attempts_per_item"]
        for identity in affected
    )


def _reset(state, affected, *, sync_prompt=False):
    quality = state["quality"]
    quality.setdefault("versions", []).append(
        {
            "items": {identity: copy.deepcopy(state["items"][identity]) for identity in affected},
            "assembly": state.pop("assembly", None),
            "review_receipt": quality.get("receipt_path"),
        }
    )
    for identity in affected:
        previous = state["items"][identity]
        state["items"][identity] = {
            "status": "pending",
            "attempts": previous["attempts"],
            "history": previous.get("history", []),
        }
        if sync_prompt:
            # Existing cards still hold the old prompt; preflight writes the repaired one.
            state["items"][identity]["sync_prompt"] = True
    quality.pop("envelope", None)
    quality.pop("disposition", None)


def advance(directory, state):
    """Return a quality action, or None after a bounded regeneration was scheduled."""
    try:
        from . import generation
    except ImportError:
        import generation
    quality = state.setdefault("quality", {"rounds": 0})
    config = state["plan"]["quality"]
    envelope = quality.get("envelope")
    if not envelope:
        return generation._action(
            state,
            {"id": "__film__"},
            "quality",
            {
                "production_dir": str(directory),
            },
        )
    review = _verify(directory, state, envelope)
    if review["decision"] == "pass":
        return {
            "action": "done",
            "delivery": "media",
            "acceptance": "model_reviewed_with_observer"
            if review.get("review_method") == "observer_assisted"
            else "model_reviewed",
            "exports": {identity: row["export"] for identity, row in state["items"].items()},
            "assembly": state.get("assembly"),
            "quality": review,
            "quality_receipt": quality.get("receipt_path"),
            "detail": "Independent review passed for these exact outputs and requirements. "
            "Report model-reviewed delivery and sampling limits; this is not human approval.",
        }
    repair = review["repair_plan"]
    limitations = review.get("limitations") or []
    design_only_with_global_uncertainty = (
        bool(repair.get("stage_edits"))
        and not repair.get("regenerate_item_ids")
        and not repair.get("review_item_ids")
        and set(limitations) <= {"some_checks_or_issues_are_uncertain"}
    )
    if review["decision"] == "review" or (limitations and not design_only_with_global_uncertainty):
        return generation._blocked("quality_requires_review", review)
    if repair.get("review_item_ids"):
        return generation._blocked("quality_requires_review", review)
    if quality.get("rounds", 0) >= config["max_rework_rounds"]:
        return generation._blocked("quality_rework_exhausted", review)
    target_ids = list(repair.get("regenerate_item_ids") or [])
    target_ids += [entry["item_id"] for entry in repair.get("stage_edits", [])]
    affected = _affected(state, target_ids)
    if not _reserve_possible(state, affected):
        return generation._blocked(
            "quality_generation_allowance_exhausted",
            {"affected_items": sorted(affected), "review": review},
        )
    if repair.get("stage_edits"):
        stage = min((entry["stage"] for entry in repair["stage_edits"]), key=ORDER.index)
        if config["max_design_edits"] == 0:
            return generation._blocked("quality_design_edits_not_authorized", review)
        quality["rounds"] = quality.get("rounds", 0) + 1
        quality["pending_design"] = {
            "stage": stage,
            "file": str(prod.stage_path(directory, stage)),
            "last_inputs": dict(state["inputs"]),
            "edits": 0,
            "affected": sorted(affected),
            "issues": review["report"]["issues"],
            "waiting_for_edit": True,
            "unchanged_calls": 0,
            "original_storyboard": prod.read(prod.stage_path(directory, "storyboard")),
        }
        return design_step(directory, state)
    quality["rounds"] = quality.get("rounds", 0) + 1
    _reset(state, affected)
    return None


def design_step(directory, state, *, retry=False):
    """Let Cine repair native artifacts; validate and reimport before regenerating them."""
    try:
        from . import autopilot, generation
    except ImportError:
        import autopilot
        import generation
    quality = state["quality"]
    pending = quality["pending_design"]
    config = state["plan"]["quality"]
    current = {name: prod.fingerprint(Path(name)) for name in pending["last_inputs"]}
    changed = {
        name for name, previous in pending["last_inputs"].items() if current[name] != previous
    }
    if changed:
        allowed = {pending["file"]} if pending.get("waiting_for_edit") else set()
        if changed - allowed:
            return generation._blocked("quality_unrequested_edit", sorted(changed - allowed))
        if pending["edits"] >= config["max_design_edits"]:
            return generation._blocked("quality_design_edits_exhausted", pending["issues"])
        pending["edits"] += 1
        pending["unchanged_calls"] = 0
        pending["last_inputs"] = current
        pending["waiting_for_edit"] = False
    original = pending.get("original_storyboard")
    if original is not None:
        current_board = prod.read(prod.stage_path(directory, "storyboard"))

        def segment_ids(board):
            return [
                [segment.get("id") for segment in episode.get("segments", [])]
                for episode in board.get("episodes", [])
            ]

        if segment_ids(original) != segment_ids(current_board):
            return generation._blocked(
                "quality_revision_scope_changed",
                "Segment IDs/order changed; create a new production revision and bounded plan.",
            )
    if pending.get("waiting_for_edit"):
        pending["unchanged_calls"] = pending.get("unchanged_calls", 0) + 1
        if pending["unchanged_calls"] > 3:
            return generation._blocked("quality_design_not_progressing", pending["issues"])
        return {
            "action": "edit",
            "mode": "repair",
            "stage": pending["stage"],
            "skill": STAGES[pending["stage"]].skill,
            "file": pending["file"],
            "errors": pending["issues"],
            "quality_repair": True,
            "detail": "Repair the located defects in this file. Preserve user requirements, "
            "item identities, unchanged story facts and other stage files. Then run next again.",
        }
    draft_run = autopilot._Run(directory)
    draft_run.state.pop("delivery", None)
    decision = autopilot._decide(
        draft_run, Path(state["source_text"]) if state.get("source_text") else None, retry=retry
    )
    unexpected = [
        name
        for name, expected in pending["last_inputs"].items()
        if prod.fingerprint(Path(name)) != expected
    ]
    if unexpected:
        return generation._blocked("quality_unrequested_edit", unexpected)
    if decision["action"] == "edit":
        pending.update(
            stage=decision["target_stage"], file=decision["file"], waiting_for_edit=True
        )
        if pending["edits"] >= config["max_design_edits"]:
            return generation._blocked("quality_design_edits_exhausted", decision)
        return {**decision, "quality_repair": True}
    # existing_nodes never imports the full storyboard; only the selected cards are synced.
    existing_nodes = state["plan"].get("canvas_strategy") == "existing_nodes"
    if decision["action"] == "import_canvas" and not existing_nodes:
        pending["waiting_for_canvas"] = True
        return decision
    if decision["action"] not in ("done", "import_canvas"):
        return decision
    if decision["action"] == "done" and decision.get("delivery") != "canvas":
        return generation._blocked("quality_canvas_sync_required", decision)
    if decision.get("blocking_items"):
        return generation._blocked("quality_canvas_review_blocking", draft_run.queue)
    generation.validate_plan(directory, state["plan"])
    # Upstream design changes affect every generated item; prompt-only changes stay local.
    storyboard_path = str(prod.stage_path(directory, "storyboard"))
    changed_paths = {
        name
        for name, previous in state["inputs"].items()
        if prod.fingerprint(Path(name)) != previous
    }
    affected = set(pending["affected"])
    if changed_paths - {storyboard_path}:
        affected = set(state["items"])
    else:
        original = pending.get("original_storyboard")
        current_storyboard = prod.read(Path(storyboard_path))
        if original is None:
            affected = set(state["items"])
        else:
            for item in state["plan"]["items"]:
                pointer = item["production_pointer"].rsplit("/", 1)[0]
                if generation._pointer(original, pointer) != generation._pointer(
                    current_storyboard, pointer
                ):
                    affected.add(item["id"])
            affected = _affected(state, affected)
    if not _reserve_possible(state, affected):
        return generation._blocked(
            "quality_generation_allowance_exhausted", {"affected_items": sorted(affected)}
        )
    state["inputs"] = generation._inputs(
        directory, Path(state["source_text"]) if state.get("source_text") else None
    )
    _reset(state, affected, sync_prompt=existing_nodes)
    quality.pop("pending_design")
    return None


def acknowledge_canvas(directory, receipt):
    """Only a successful import may normalize the storyboard during design repair."""
    try:
        from . import generation
    except ImportError:
        import generation
    from filelock import FileLock

    state_path = directory / generation.STATE
    if not state_path.exists():
        return
    with FileLock(str(state_path.with_suffix(".lock")), timeout=5):
        state = prod.read(state_path)
        pending = (state.get("quality") or {}).get("pending_design")
        if not pending:
            return
        if not pending.get("waiting_for_canvas") or not isinstance(receipt, dict):
            raise ValueError("No quality repair canvas import is pending")
        if receipt.get("status") != "synced" or receipt.get("verified") is not True:
            raise ValueError("Quality repair requires a verified canvas import receipt")
        storyboard = str(prod.stage_path(directory, "storyboard"))
        for name, expected in pending["last_inputs"].items():
            if name != storyboard and prod.fingerprint(Path(name)) != expected:
                raise ValueError("An unrelated input changed during repair canvas sync")
        pending["last_inputs"][storyboard] = prod.fingerprint(Path(storyboard))
        pending["waiting_for_canvas"] = False
        prod.atomic_json(state_path, state)
