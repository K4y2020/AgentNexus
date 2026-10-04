"""Deterministic controller for unattended Cine production, source to canvas prompt cards.

Cine writes the creative artifacts; this module decides what happens next:
which stage to write or repair, which mechanical steps to run itself, and when
to stop and queue an item for a person. Its state lives beside the production,
so a restarted agent resumes from what the files say rather than from memory.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

try:
    from . import production as prod
    from .draft_check import story_draft_gaps
except ImportError:  # Direct CLI execution from the pipeline directory.
    import production as prod
    from draft_check import story_draft_gaps

ORDER = ("outline", "cast", "art", "script", "storyboard")
# Edits one stage gets before it is handed to a person: the authoring pass
# plus four repairs.
MAX_EDITS = 5
# Calls that find the same failing file unedited before the controller gives up.
MAX_UNCHANGED = 3
# Failures of the environment rather than the artifact; retried once, never edited.
TECHNICAL = {"blocked", "attribution_error", "timeout", "inputs_changed"}
STATE = Path(".cine-validation") / "autopilot.json"
QUEUE = "review-queue.json"
# Copies of the passed stages an open edit must leave alone, and of edits undone.
FROZEN = Path(".cine-validation") / "frozen"
REVERTED = Path(".cine-validation") / "reverted"
TEXT_DONE = (
    "Text delivery is complete: every stage file passed its checks. Report the files, "
    "these receipts and the review queue, then stop. Nothing else needs to run: no canvas "
    "import, finalize, check, validate or render, and no production.json."
)


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


def _location_inputs(directory: Path, stage: str) -> list[Path]:
    if stage not in ("outline", "script"):
        return []
    return [
        prod.source_material_path(directory),
        Path(prod.__file__).with_name("location_gate.py"),
        Path(prod.__file__).with_name("performance_gate.py"),
    ]


def _dependencies(directory: Path, stage: str, row: dict[str, Any]) -> dict[str, str | None]:
    """Files whose change must re-open a passed stage."""
    paths = [Path(p) for p in row.get("input_hashes", {})]
    paths.extend(_required_inputs(directory, stage))
    return {str(p): prod.fingerprint(p) for p in paths}


def _required_inputs(directory: Path, stage: str) -> list[Path]:
    paths = _location_inputs(directory, stage)
    paths.append(prod.script_path(stage))
    if stage == "script":
        paths.append(directory / "person-map.json")
        paths.extend(
            Path(prod.__file__).with_name(name)
            for name in (
                "speaker_evidence.py",
                "people_gate.py",
                "person_map.py",
                "attribute_speakers.py",
                "line_judgment.py",
                "jev_client.py",
                "line_performance.py",
            )
        )
        paths.extend(prod.source_transcript_candidates(directory))
        paths.append(directory / "source-transcript-attributed.json")
    return paths


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
                {
                    "severity": "review",
                    "reason": "speaker_unsettled",
                    "detail": {"lines": unsettled},
                }
            )
    performance = row.get("performance") or {}
    people = row.get("people") or {}
    found["people"] = [
        {"severity": "review", "reason": item["reason"], "detail": item}
        for item in people.get("review") or []
    ]
    found["people"].extend(
        {"severity": "review", "reason": "source_speaker_review", "detail": {"message": message}}
        for message in (row.get("source_review") or {}).get("bad", {}).get("review", [])
    )
    locations = row.get("locations") or {}
    found["locations"] = [
        {"severity": "review", "reason": item["reason"], "detail": item}
        for item in locations.get("review") or []
    ]
    found["performance"] = [
        {"severity": "review", "reason": "performance_differs", "detail": item}
        for item in performance.get("review") or []
    ]
    if performance.get("status") == "unavailable":
        found["performance"].append(
            {
                "severity": "review",
                "reason": "performance_unchecked",
                "detail": {"error": performance.get("error") or performance.get("errors")},
            }
        )
    anchors = row.get("asset_anchors") or {}
    if anchors.get("status") in ("unavailable", "error"):
        found["assets"].append(
            {"severity": "review", "reason": f"asset_check_{anchors['status']}", "detail": anchors}
        )
    return found


def _receipt(row: dict[str, Any]) -> str | None:
    """The validator's own pass line, quoted in a text delivery instead of a rerun."""
    lines = [line.strip() for line in (row.get("stdout") or "").splitlines() if line.strip()]
    return lines[-1][:300] if lines else None


def _freeze_others(run: _Run, stage: str, target_stage: str) -> list[str]:
    """Snapshot the passed upstream files an edit of ``target_stage`` must leave alone."""
    directory = run.directory
    target = prod.stage_path(directory, target_stage, must_exist=False).resolve()
    frozen: dict[str, str | None] = {}
    for name in ORDER[: ORDER.index(stage)]:
        if name == target_stage:
            continue
        for recorded in run.stage(name).get("passed_inputs") or {}:
            path = Path(recorded).resolve()
            if path == target or path.is_dir() or not path.is_relative_to(directory):
                continue
            relative = path.relative_to(directory).as_posix()
            if relative.startswith(".cine-validation/") or relative in frozen:
                continue
            if path.is_file():
                copy = directory / FROZEN / relative
                copy.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(path, copy)
            # An input that did not exist must not appear either.
            frozen[relative] = prod.fingerprint(path)
    run.state["pending_edit"] = {"stage": stage, "target_stage": target_stage, "frozen": frozen}
    return sorted(frozen)


def _restore_frozen(run: _Run) -> list[dict[str, str | None]]:
    """Undo changes to files the last edit froze, keeping each changed version aside."""
    pending = run.state.pop("pending_edit", None)
    if not pending:
        return []
    directory = run.directory
    reverted = []
    for relative, sha in pending["frozen"].items():
        path = directory / relative
        snapshot = directory / FROZEN / relative
        if path.is_dir() or prod.fingerprint(path) == sha:
            continue
        if sha is not None and not snapshot.is_file():
            continue
        kept = None
        if path.is_file():
            kept = REVERTED / f"{path.stem}-{prod.digest(path)[:12]}{path.suffix}"
            (directory / kept).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, directory / kept)
        if sha is None:
            path.unlink(missing_ok=True)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            prod._atomic_bytes(path, snapshot.read_bytes())
        reverted.append({"file": relative, "edited_copy": kept.as_posix() if kept else None})
    if reverted:
        stage = pending["stage"]
        earlier = [
            i for i in run.queue["items"] if (i["stage"], i["source"]) == (stage, "upstream")
        ]
        run.set_items(
            stage,
            "upstream",
            earlier
            + [
                {"severity": "review", "reason": "upstream_edit_reverted", "detail": item}
                for item in reverted
            ],
        )
    run.save()
    return reverted


def _escalate(
    run: _Run, stage: str, reason: str, detail: Any, file: Path | None
) -> dict[str, Any]:
    # A person takes over from here and may change any stage.
    run.state.pop("pending_edit", None)
    run.stage(stage)["escalated"] = {
        "reason": reason,
        "file": str(file) if file else None,
        "sha": prod.fingerprint(file) if file else None,
    }
    run.set_items(
        stage, "controller", [{"severity": "blocking", "reason": reason, "detail": detail}]
    )
    run.save()
    return {
        "action": "escalate",
        "stage": stage,
        "reason": reason,
        "detail": detail,
        **run.summary(),
    }


def _analysis_gaps(directory: Path) -> list[str]:
    """What the film analysis behind this production left unfinished."""
    path = prod.source_material_path(directory)
    if not path.is_file():
        return []
    material = prod.read(path)
    if not isinstance(material, dict) or not material.get("revision_id"):
        return []
    gaps = []
    notes = material.get("source_locations")
    if not isinstance(notes, dict):
        gaps.append("shot_notes_missing")
    elif notes.get("status") != "read" or notes.get("unread_shots"):
        gaps.append(f"shots_unread:{len(notes.get('unread_shots') or [])}")
    story = material.get("adaptation_story")
    if not isinstance(story, dict):
        gaps.append("story_draft_missing")
    else:
        plan = {
            "revision_id": material.get("revision_id"),
            "batches": material.get("adaptation_story_batches") or [],
        }
        gaps += story_draft_gaps(
            story,
            plan,
            revision_id=material.get("revision_id"),
            source_id=material.get("source_id"),
        )
    return gaps


def _refuse_damaged_inputs(directory: Path) -> None:
    """Stop at the first call, not an hour in, when the text a run is built on is damaged."""
    for path, remedy in (
        (prod.source_material_path(directory), "export it again with `handoff.py --output`"),
    ):
        if path.is_file():
            try:
                prod.read_text(path)
            except ValueError as exc:
                raise ValueError(f"{exc} Fix: {remedy}, then run `next` again.") from exc


def next_step(
    directory: Path,
    *,
    source_text: Path | None = None,
    jev: bool = True,
    retry: bool = False,
    text_only: bool = False,
) -> dict[str, Any]:
    """Return the one next action for an unattended production run."""
    directory = Path(directory).resolve()
    _refuse_damaged_inputs(directory)
    if not prod.stage_path(directory, "outline", must_exist=False).exists():
        return {
            "action": "intake",
            "detail": "No outline yet: finish film analysis, export source-material.json with "
            "`handoff.py --source-project <project> --output <production>/source-material.json` "
            "(not a shell redirect), then run production.py init with the user's adaptation mode.",
        }
    gaps = _analysis_gaps(directory)
    if gaps:
        return {
            "action": "intake",
            "detail": "The film analysis this production is built on is unfinished ("
            + ", ".join(gaps[:8])
            + "). In film-analysis, run film_analyze again for unread shots and write the story "
            "draft batch by batch through the ending; export source-material.json again with "
            "`handoff.py --source-project <project> --output <production>/source-material.json`, "
            "then run next.",
            "gaps": gaps,
        }
    run = _Run(directory)
    if text_only:
        run.state["delivery"] = "text"
    reverted = _restore_frozen(run)
    result = _decide(run, source_text or _default_source_text(directory), jev=jev, retry=retry)
    if reverted:
        notice = (
            "Undid changes to "
            + ", ".join(item["file"] for item in reverted)
            + ": while a stage is written or repaired, every other passed stage is frozen. "
            "Change only the named `file`; report an upstream problem instead of editing it. "
            "The changed versions are kept as "
            + ", ".join(item["edited_copy"] or "(deleted)" for item in reverted)
            + "."
        )
        result["reverted_upstream"] = reverted
        if result.get("action") == "edit":
            result["errors"] = [notice, *result["errors"]]
        else:
            result["notice"] = notice
    return result


def _decide(run: _Run, source_text: Path | None, *, jev: bool, retry: bool) -> dict[str, Any]:
    directory = run.directory
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
        recorded = record.get("passed_inputs") or {}
        model_current = stage != "script" or record.get("vision_model") == prod.vision_model()
        if (
            model_current
            and _still_current(recorded)
            and all(str(path) in recorded for path in _required_inputs(directory, stage))
        ):
            continue
        row = prod.check_stage(directory, stage, source_text, advise_jev=jev)
        if row["status"] in TECHNICAL:
            row = prod.check_stage(directory, stage, source_text, advise_jev=jev)
        if row["status"] == "passed":
            if record["failed_versions"]:
                record.setdefault("history", []).append({"status": "passed"})
            record.update(
                passed_inputs=_dependencies(directory, stage, row),
                vision_model=prod.vision_model() if stage == "script" else None,
                failed_versions=[],
                unchanged=0,
                receipt=_receipt(row),
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
        frozen = _freeze_others(run, stage, target_stage)
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
            "frozen": frozen,
            **run.summary(),
        }

    storyboard = prod.stage_path(directory, "storyboard")
    run.save()
    if prod.adapt_mode(directory) == "忠实":
        try:
            from .people_gate import used_unsettled
        except ImportError:
            from people_gate import used_unsettled
        attributed = _read_json(directory / "source-transcript-attributed.json", {})
        uncertain = used_unsettled(
            prod.read(prod.stage_path(directory, "script")), attributed.get("attributions") or []
        )
        if uncertain:
            run.set_items(
                "script",
                "speakers",
                [
                    {"severity": "review", "reason": "speaker_unsettled", "detail": item}
                    for item in uncertain
                ],
            )
            run.save()
            return {
                "action": "human_review_speakers",
                "stage": "script",
                "lines": uncertain,
                **run.summary(),
            }
        run.set_items("script", "speakers", [])
        run.save()
    if run.state.get("canvas_synced", {}).get("sha") == prod.digest(storyboard):
        return {"action": "done", **run.summary()}
    if run.state.get("delivery") == "text":
        return {
            "action": "done",
            "delivery": "text",
            "files": {stage: str(prod.stage_path(directory, stage)) for stage in ORDER},
            "receipts": {stage: run.stage(stage).get("receipt") for stage in ORDER},
            "detail": TEXT_DONE,
            **run.summary(),
        }
    return {
        "action": "import_canvas",
        "tool": "seedance_edit_canvas",
        "arguments": {
            "action": "import_storyboard",
            "storyboard_file": str(storyboard),
            "script_file": str(prod.stage_path(directory, "script")),
        },
        "after": "run production.py next with --canvas-synced <receipt file> "
        "once the import succeeds",
        "text_only": "If the user asked for text only or no canvas change, do not import: "
        "run next again with --text-only.",
        **run.summary(),
    }


def record_canvas_sync(directory: Path, receipt: Path) -> dict[str, Any]:
    """Mark the current storyboard as imported, keyed by its hash."""
    directory = Path(directory).resolve()
    run = _Run(directory)
    recorded = _read_json(receipt, None)
    run.state["canvas_synced"] = {
        "sha": prod.digest(prod.stage_path(directory, "storyboard")),
        "receipt": recorded,
    }
    # Cards whose prompt could not be tied to their reference images must not be
    # generated as they are: the model would take a character sheet for a shot.
    body = recorded if isinstance(recorded, dict) else {}
    review = body.get("subject_alignment_review")
    if review is None and isinstance(body.get("receipt"), dict):
        review = body["receipt"].get("subject_alignment_review")
    run.set_items(
        "storyboard",
        "canvas",
        [
            {"severity": "blocking", "reason": "prompt_references_unaligned", "detail": item}
            for item in review or []
            if isinstance(item, dict)
        ],
    )
    run.save()
    return next_step(directory, jev=False)
