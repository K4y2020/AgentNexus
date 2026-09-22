"""Canonical, bounded C0+C1 entry point for the film-analysis skill."""

import argparse
import json
import math
import os
import sys
from pathlib import Path

from .probe import _sha256_chunk
from .project import ProjectLock, _atomic_write, create_project, validate_identifier
from .runner import run_pipeline


def _resolved_session_id(session_id: str | None, workspace: Path) -> str | None:
    """Infer Bot Topic ownership from its canonical workspace path."""
    workspace = workspace.resolve()
    inferred = workspace.name if workspace.parent.name.lower() == "topics" else None
    if inferred:
        validate_identifier(inferred, "session_id")
        if session_id and session_id != inferred:
            raise ValueError("Explicit session_id conflicts with the Topic workspace")
        return inferred
    return session_id


def index_media(
    video: Path,
    output: Path,
    *,
    end: float | None = None,
    start: float = 0,
    resume: bool = False,
    session_id: str | None = None,
    workspace: Path | None = None,
    profile: str = "adaptation",
) -> dict:
    video = video.resolve(strict=True)
    output = output.resolve()
    workspace = (workspace or Path.cwd()).resolve()
    session_id = _resolved_session_id(session_id, workspace)
    if profile not in ("adaptation", "forensic"):
        raise ValueError("unknown analysis profile")
    if profile == "forensic" and end is None:
        end = 30
    if (
        not math.isfinite(start)
        or start < 0
        or (end is not None and (not math.isfinite(end) or end <= start))
    ):
        raise ValueError("Require 0 <= start < end")
    binding = None
    if session_id:
        validate_identifier(session_id, "session_id")
        if not output.is_relative_to(workspace):
            raise ValueError("Bound projects must remain within this session workspace")
        binding = workspace / ".cine" / "sessions" / f"{session_id}.json"
        if binding.exists():
            existing = json.loads(binding.read_text(encoding="utf-8"))
            if existing.get("project_path") != str(output):
                raise ValueError("Session already has a different project; use a new Topic")
    if not resume:
        create_project(output.parent, output.name, project_id=output.name)
    result = None
    if resume and profile == "adaptation":
        manifest = json.loads((output / "project.json").read_text(encoding="utf-8"))
        rev = manifest.get("current_revision")
        if rev:
            validate_identifier(rev, "revision_id")
            plan_path = output / "revisions" / rev / "story_plan.json"
            if plan_path.is_file():
                source = json.loads((output / "source.json").read_text(encoding="utf-8"))
                if (
                    _sha256_chunk(video, tail=False) != source["sha256_head"]
                    or _sha256_chunk(video, tail=True) != source["sha256_tail"]
                ):
                    raise ValueError("Source media changed; use a new project")
                plan = json.loads(plan_path.read_text(encoding="utf-8"))
                batches = plan["batches"]
                validation = json.loads(
                    (plan_path.parent / "validation.json").read_text(encoding="utf-8")
                )
                duration = (
                    source["duration_pts"] * source["time_base_num"] / source["time_base_den"]
                )
                wanted_end = min(end, duration) if end is not None else duration
                report = plan_path.parent / "report" / "report.html"
                if (
                    validation.get("passed") is True
                    and plan.get("source_id") == source["source_id"]
                    and plan.get("revision_id") == rev
                    and batches
                    and batches[0]["start_seconds"] == start
                    and batches[-1]["end_seconds"] == wanted_end
                    and report.is_file()
                ):
                    result = {
                        "revision_id": rev,
                        "source_id": source["source_id"],
                        "validation_passed": True,
                        "report_path": str(report),
                        "story_plan_path": str(plan_path),
                        "reused_revision": True,
                    }
    if result is None:
        result = run_pipeline(
            video,
            output.parent,
            project_id=output.name,
            scope_in_seconds=start,
            scope_out_seconds=end,
            evidence_mode="story" if profile == "adaptation" else "boundary",
        )
    # Index completion is not visual, audio or semantic verification.
    committed = json.loads((output / "project.json").read_text(encoding="utf-8"))
    ready = (
        result.get("validation_passed") is True
        and not result.get("reason")
        and committed.get("current_revision") == result.get("revision_id")
        and bool(result.get("report_path"))
    )
    result["analysis_status"] = "indexed_unreviewed" if ready else "blocked"
    result["reviewed_shot_count"] = 0
    result["semantic_reviewed_shot_count"] = 0
    result["profile"] = profile
    result["next_step"] = (
        "Read story_plan_path, inspect each temporal batch and save to story_draft_path; "
        "continue automatically through the ending, then cine_verify_report scope=adaptation."
        if profile == "adaptation"
        else "Review source shots with image receipts."
    )
    # Source-shot JEV review is opt-in and semantic only.  It must not silently
    # turn a text/optical-flow judgment into visual evidence review.
    reviews = output / "reviews" / f"{result['revision_id']}.json"
    if result["analysis_status"] == "indexed_unreviewed" and os.environ.get("CINE_AUTO_JEV") == "1":
        # Auto-review shots with JEV (TypeSafe System One) when configured
        try:
            from .jev_review import has_jev_configured, review_shots_with_jev

            if has_jev_configured():
                jev_res = review_shots_with_jev(output, result["revision_id"])
                if jev_res.get("status") == "ok" and jev_res.get("reviewed_count", 0) > 0:
                    result["analysis_status"] = "indexed_semantic_reviewed"
                    result["semantic_reviewed_shot_count"] = jev_res["reviewed_count"]
                    result["jev_review_path"] = jev_res["review_path"]
                    result["next_step"] = (
                        "JEV semantic metadata review complete; inspect evidence images before making visual claims."
                    )
                    try:
                        from .render import refresh_report

                        refresh_report(output, workspace, with_receipt=True)
                    except Exception as exc:
                        result["jev_report_warning"] = f"report refresh failed: {type(exc).__name__}: {exc}"
        except Exception as exc:
            result["jev_error"] = f"{type(exc).__name__}: {exc}"
            result["next_step"] = (
                "JEV semantic review failed; inspect the recorded jev_error before proceeding."
            )

    if not reviews.exists():
        _atomic_write(reviews, [])
    if result.get("story_plan_path"):
        plan = json.loads(Path(result["story_plan_path"]).read_text(encoding="utf-8"))
        draft = output / "story" / f"{result['revision_id']}.json"
        if not draft.exists():
            _atomic_write(
                draft,
                {
                    "schema_version": 1,
                    "source_id": result["source_id"],
                    "revision_id": result["revision_id"],
                    "dialogue_provenance": {
                        "status": "unverified",
                        "source_path": None,
                    },
                    "characters": [],
                    "summary": {
                        "premise": "",
                        "conflict": "",
                        "turning_points": [],
                        "ending": "",
                    },
                    "sections": [
                        {
                            "batch_id": b["batch_id"],
                            "events": [],
                            "connection": "",
                            "image_receipt_ids": [],
                            "uncertainties": [],
                        }
                        for b in plan["batches"]
                    ],
                },
            )
        result["story_draft_path"] = str(draft)
    if binding and result["analysis_status"] in {"indexed_unreviewed", "indexed_semantic_reviewed"}:
        binding.parent.mkdir(parents=True, exist_ok=True)
        with ProjectLock(binding.with_suffix(".lock"), timeout=5):
            if binding.exists() and json.loads(binding.read_text(encoding="utf-8")).get(
                "project_path"
            ) != str(output):
                raise ValueError("Session project binding changed during indexing")
            _atomic_write(binding, {"session_id": session_id, "project_path": str(output)})
    return result


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("--video", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--refresh-report", action="store_true")
    parser.add_argument("--start", type=float, default=0)
    parser.add_argument("--end", type=float)
    parser.add_argument("--profile", choices=["adaptation", "forensic"], default="adaptation")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--session-id")
    parser.add_argument("--workspace", type=Path, default=Path.cwd())
    args = parser.parse_args()
    if args.refresh_report:
        try:
            from .render import refresh_report

            result = refresh_report(args.output, args.workspace, with_receipt=True)
        except (OSError, ValueError, KeyError, TypeError, RuntimeError, ImportError) as exc:
            if isinstance(exc, FileNotFoundError):
                code = "REPORT_INPUT_MISSING"
                action = "Check the supplied project path or restore the named input file."
            elif isinstance(exc, (ValueError, KeyError, TypeError)):
                code = "REPORT_INPUT_INVALID"
                action = "Check the project/workspace arguments and the named input data."
            else:
                code = "REPORT_TOOL_ERROR"
                action = "Report this receipt to maintenance; do not inspect or rewrite tool code."
            result = {
                "status": "report_refresh_failed",
                "error_code": code,
                "error": str(exc),
                "project_path": str(args.output),
                "next_action": action,
            }
        print(json.dumps(result, ensure_ascii=False))
        if result["status"] != "report_refreshed":
            raise SystemExit(2)
        return
    if args.video is None:
        parser.error("--video is required unless --refresh-report is selected")
    result = index_media(
        args.video,
        args.output,
        start=args.start,
        end=args.end,
        resume=args.resume,
        session_id=args.session_id,
        workspace=args.workspace,
        profile=args.profile,
    )
    print(json.dumps(result, ensure_ascii=False))
    if result["analysis_status"] == "blocked":
        raise SystemExit(2)
