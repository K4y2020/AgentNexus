"""Deterministic controller for unattended Cine production, source to canvas prompt cards.

Cine writes the creative artifacts; this module decides what happens next:
which stage to write or repair, which mechanical steps to run itself, and when
to stop and queue an item for a person. Its state lives beside the production,
so a restarted agent resumes from what the files say rather than from memory.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

try:
    from . import production as prod
except ImportError:  # Direct CLI execution from the pipeline directory.
    import production as prod

ORDER = ("outline", "cast", "art", "script", "storyboard")
# Failing versions of one stage before it is handed to a person: the first
# authoring pass plus two repairs.
MAX_EDITS = 3
# Calls that find the same failing file unedited before the controller gives up.
MAX_UNCHANGED = 3
# Failures of the environment rather than the artifact; retried once, never edited.
TECHNICAL = {"blocked", "attribution_error", "timeout", "inputs_changed"}
STATE = Path(".cine-validation") / "autopilot.json"
QUEUE = "review-queue.json"


def _read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    prod.atomic_json(path, value)


class _Run:
    """Controller state and review queue for one production directory."""

    def __init__(self, directory: Path):
        self.directory = directory
        self.state = _read_json(directory / STATE, {"stages": {}})
        self.queue = _read_json(directory / QUEUE, {"items": []})

    def stage(self, name: str) -> dict[str, Any]:
        return self.state["stages"].setdefault(
            name, {"passed_inputs": None, "failed_versions": [], "unchanged": 0, "escalated": None}
        )

    def set_items(self, stage: str, source: str, items: list[dict[str, Any]]) -> None:
        """Replace the queue items one source raised for one stage."""
        kept = [i for i in self.queue["items"] if (i["stage"], i["source"]) != (stage, source)]
        self.queue["items"] = kept + [{"stage": stage, "source": source, **i} for i in items]

    def save(self) -> None:
        _write_json(self.directory / STATE, self.state)
        _write_json(self.directory / QUEUE, self.queue)

    def summary(self) -> dict[str, Any]:
        items = self.queue["items"]
        return {
            "queue": str(self.directory / QUEUE),
            "blocking_items": sum(i["severity"] == "blocking" for i in items),
            "review_items": sum(i["severity"] == "review" for i in items),
        }


def _dependencies(directory: Path, stage: str, row: dict[str, Any]) -> dict[str, str | None]:
    """Files whose change must re-open a passed stage."""
    paths = [Path(p) for p in row.get("input_hashes", {})]
    if stage == "script":
        paths.append(directory / "scene-notes.json")
    return {str(p): prod.fingerprint(p) for p in paths}


def _still_current(recorded: dict[str, str | None] | None) -> bool:
    return bool(recorded) and all(prod.fingerprint(Path(p)) == h for p, h in recorded.items())


def _default_source_text(directory: Path) -> Path | None:
    """The raw transcript beside the production, used only when no attribution exists."""
    for candidate in prod.source_transcript_candidates(directory):
        text = candidate.with_suffix(".txt")
        if text.is_file():
            return text
    return None


def _errors(row: dict[str, Any]) -> list[str]:
    errors = list(row.get("errors") or [])
    errors += (row.get("asset_anchors") or {}).get("problems") or []
    for stream in ("stderr", "stdout"):
        text = (row.get(stream) or "").strip()
        if text:
            errors.append(text[-2000:])
    return errors


def _repair_target(stage: str, row: dict[str, Any]) -> str:
    """Missing sheets are fixed in cast or art, not in the storyboard that found them."""
    if row.get("status") != "assets_missing":
        return stage
    problems = (row.get("asset_anchors") or {}).get("problems") or []
    return "cast" if any(p.startswith("Character") for p in problems) else "art"


def _findings(directory: Path, stage: str, row: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """Non-blocking review items from a passed stage, keyed by their source."""
    found: dict[str, list[dict[str, Any]]] = {"jev": [], "attribution": [], "assets": []}
    jev = row.get("jev")
    if jev and jev.get("status") != "passed":
        found["jev"].append(
            {
                "severity": "review",
                "reason": f"jev_{jev.get('status')}",
                "detail": {
                    "failures": jev.get("failures"),
                    "error": jev.get("error"),
                    "next_action": jev.get("next_action")
                    or (jev.get("scheduler") or {}).get("next_action"),
                    "receipt": jev.get("receipt_path"),
                },
            }
        )
    if stage == "script" and "--source" in row.get("command", []):
        attributed = _read_json(directory / "source-transcript-attributed.json", {})
        unsettled = [
            a.get("text") for a in attributed.get("attributions") or [] if a.get("needs_review")
        ]
        if unsettled:
            found["attribution"].append(
                {"severity": "review", "reason": "speaker_unsettled", "detail": {"lines": unsettled}}
            )
    anchors = row.get("asset_anchors") or {}
    if anchors.get("status") in ("unavailable", "error"):
        found["assets"].append(
            {"severity": "review", "reason": f"asset_check_{anchors['status']}", "detail": anchors}
        )
    return found


def _escalate(
    run: _Run, stage: str, reason: str, detail: Any, file: Path | None
) -> dict[str, Any]:
    run.stage(stage)["escalated"] = {
        "reason": reason,
        "file": str(file) if file else None,
        "sha": prod.fingerprint(file) if file else None,
    }
    run.set_items(stage, "controller", [{"severity": "blocking", "reason": reason, "detail": detail}])
    run.save()
    return {"action": "escalate", "stage": stage, "reason": reason, "detail": detail, **run.summary()}


def next_step(
    directory: Path,
    *,
    source_text: Path | None = None,
    jev: bool = True,
    retry: bool = False,
) -> dict[str, Any]:
    """Return the one next action for an unattended production run."""
    directory = Path(directory).resolve()
    if not prod.stage_path(directory, "outline", must_exist=False).exists():
        return {
            "action": "intake",
            "detail": "No outline yet: finish film analysis, export source-material.json with "
            "handoff.py, then run production.py init with the user's adaptation mode.",
        }
    run = _Run(directory)
    source_text = source_text or _default_source_text(directory)
    for stage in ORDER:
        record = run.stage(stage)
        escalated = record.get("escalated")
        # Technical escalations have no file to watch; only an explicit retry clears them.
        edited = bool(escalated and escalated["file"]) and (
            prod.fingerprint(Path(escalated["file"])) != escalated["sha"]
        )
        if escalated and (retry or edited):
            # A person edited the file or asked for a retry: the stage gets a fresh budget.
            record.update(escalated=None, failed_versions=[], unchanged=0)
            run.set_items(stage, "controller", [])
        elif escalated:
            return {"action": "escalate", "stage": stage, **escalated, **run.summary()}

        path = prod.stage_path(directory, stage, must_exist=False)
        if not path.exists():
            try:
                prod.seed(directory, stage)
            except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
                return _escalate(run, stage, "seed_failed", str(exc), None)
        if _still_current(record.get("passed_inputs")):
            continue
        if stage == "script":
            record["scene_notes"] = prod.write_scene_notes(directory)

        row = prod.check_stage(directory, stage, source_text, advise_jev=jev)
        if row["status"] in TECHNICAL:
            row = prod.check_stage(directory, stage, source_text, advise_jev=jev)
        if row["status"] == "passed":
            if record["failed_versions"]:
                record.setdefault("history", []).append({"status": "passed"})
            record.update(
                passed_inputs=_dependencies(directory, stage, row),
                failed_versions=[],
                unchanged=0,
            )
            for source, items in _findings(directory, stage, row).items():
                run.set_items(stage, source, items)
            run.save()
            continue
        if row["status"] in TECHNICAL:
            return _escalate(run, stage, f"technical_{row['status']}", _errors(row), None)

        target_stage = _repair_target(stage, row)
        target = prod.stage_path(directory, target_stage)
        version = prod.digest(target)
        if version in record["failed_versions"]:
            record["unchanged"] += 1
        else:
            record["failed_versions"].append(version)
            record["unchanged"] = 0
            # Kept across passes (failed_versions is the budget and resets): how
            # many versions each stage needed, for reporting.
            record.setdefault("history", []).append(
                {"stage": target_stage, "status": row["status"], "version": version[:12]}
            )
        if len(record["failed_versions"]) > MAX_EDITS:
            return _escalate(run, stage, "repair_exhausted", _errors(row), target)
        if record["unchanged"] >= MAX_UNCHANGED:
            return _escalate(run, stage, "not_progressing", _errors(row), target)
        run.save()
        edit = len(record["failed_versions"])
        return {
            "action": "edit",
            "mode": "write" if edit == 1 else "repair",
            "stage": stage,
            "target_stage": target_stage,
            "skill": prod.STAGES[target_stage],
            "file": str(target),
            "status": row["status"],
            "errors": _errors(row),
            "edit": edit,
            "max_edits": MAX_EDITS,
            **run.summary(),
        }

    storyboard = prod.stage_path(directory, "storyboard")
    run.save()
    if run.state.get("canvas_synced", {}).get("sha") == prod.digest(storyboard):
        return {"action": "done", **run.summary()}
    return {
        "action": "import_canvas",
        "tool": "seedance_edit_canvas",
        "arguments": {
            "action": "import_storyboard",
            "storyboard_file": str(storyboard),
            "script_file": str(prod.stage_path(directory, "script")),
        },
        "after": "run production.py next with --canvas-synced <receipt file> once the import succeeds",
        **run.summary(),
    }


def record_canvas_sync(directory: Path, receipt: Path) -> dict[str, Any]:
    """Mark the current storyboard as imported, keyed by its hash."""
    directory = Path(directory).resolve()
    run = _Run(directory)
    run.state["canvas_synced"] = {
        "sha": prod.digest(prod.stage_path(directory, "storyboard")),
        "receipt": _read_json(receipt, None),
    }
    run.save()
    return next_step(directory, jev=False)
