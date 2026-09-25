#!/usr/bin/env python3
"""MCP server exposing the Cine film-analysis pipeline as fixed tools.

The analysis pipeline is deterministic — project creation, shot detection,
sampling, frame extraction and report rendering are all native code. The one
step that was not, transcription, was left to the agent as a shell command in
prose, so every run improvised its own invocation. That is how a session ended
up passing a bare name list as the attribution context and labelling a
character who never appears in the episode.

Exposing the pipeline as tools removes that improvisation: the agent picks a
tool and supplies a path, and the arguments that matter are constructed here.

Transport: stdio. Tools:
  film_analyze(video, output, workspace)  — full index; transcribes if needed
  film_status(output)                     — what exists, what the next step is
  film_transcribe(video, output)          — transcription + attribution only
  film_review(output, workspace)          — JEV quality scorecard and defect audit

None of these decides adaptation readiness: that is ``cine_verify_report``
(scope="adaptation"), which checks the story against inspected image receipts.
"""

from __future__ import annotations

import json
import math
import re
import sys
from pathlib import Path
from typing import Any

_SKILL = Path(__file__).resolve().parents[2] / "skills" / "film-analysis"
if str(_SKILL) not in sys.path:
    sys.path.insert(0, str(_SKILL))

from mcp.server.fastmcp import FastMCP  # noqa: E402

mcp = FastMCP("cine-film-analysis")

_REVISION_ID = re.compile(r"[A-Za-z0-9_-]+")
_DELIVERIES = {"spoken", "voice_over", "unclear"}
_INDEXED = {"indexed_unreviewed", "indexed_semantic_reviewed"}
# Transcription outcomes after which the workspace transcript is complete.
_TRANSCRIBED = {"transcribed", "reused", "no_dialogue_detected"}
_VERIFY = (
    "Adaptation readiness is decided only by cine_verify_report(scope='adaptation'), "
    "which checks the story against inspected image receipts."
)


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None


def _attribution_complete(payload: Any) -> bool:
    """Every line carries a finished JEV judgment, not just the file-level marker."""
    if not isinstance(payload, dict) or payload.get("speaker_attribution") != "jev":
        return False
    rows = payload.get("segments")
    if not isinstance(rows, list) or not rows:
        return False
    for row in rows:
        if not isinstance(row, dict):
            return False
        confidence = row.get("attribution_confidence")
        if (
            not (isinstance(row.get("speaker"), str) and row["speaker"].strip())
            or row.get("delivery") not in _DELIVERIES
            or isinstance(confidence, bool)
            or not isinstance(confidence, (int, float))
            or not math.isfinite(confidence)
            or not 0.0 <= confidence <= 1.0
        ):
            return False
    return True


def _asr_rows_usable(rows: Any) -> bool:
    """Timed text rows that attribution can run on without redoing ASR."""
    return isinstance(rows, list) and all(
        isinstance(row, dict)
        and isinstance(row.get("text"), str)
        and all(
            isinstance(row.get(k), (int, float)) and not isinstance(row.get(k), bool)
            for k in ("start", "end")
        )
        for row in rows
    )


def _write_transcript_formats(payload: dict, rows: list[dict], inputs: Path) -> str:
    """Rewrite the JSON, TXT and SRT machine transcripts from the same rows.

    ``inputs/source.srt`` is updated only while it is still the generated mirror
    of the previous SRT; a subtitle someone supplied there is preserved. The JSON
    is written last, so it only claims attribution once the other formats agree.
    Returns what happened to the mirror.
    """
    from pipeline.transcribe import (
        _atomic_text,
        _read_text,
        render_plain,
        render_srt,
        sync_srt_mirror,
    )

    payload["segments"] = rows
    payload["speaker_attribution"] = "jev"
    srt_path = inputs / "source-transcript.srt"
    previous_srt = _read_text(srt_path)
    srt_text = render_srt(rows)
    _atomic_text(inputs / "source-transcript.txt", render_plain(rows))
    _atomic_text(srt_path, srt_text)
    _atomic_text(
        inputs / "source-transcript.json", json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    )
    return sync_srt_mirror(inputs / "source.srt", previous_srt, srt_text)


def _attribute(payload: dict, inputs: Path) -> dict[str, Any]:
    """Attribute every ASR line with JEV and rewrite the transcript formats."""
    from pipeline.attribute_speakers import attribute_speakers, derived_cast

    rows = payload.get("segments") or []
    if not rows:
        return {"status": "no_dialogue_detected"}

    attributed = attribute_speakers(rows, derived_cast(rows), scenes={})
    if "error" in attributed:
        return {"status": "attribution_unavailable", "detail": attributed}

    attributions = attributed["attributions"]
    if len(attributions) != len(rows):
        return {
            "status": "attribution_incomplete",
            "expected": len(rows),
            "received": len(attributions),
        }

    for row, entry in zip(rows, attributions, strict=True):
        row["speaker"] = entry["speaker_name"]
        row["delivery"] = entry["delivery"]
        row["attribution_confidence"] = entry["confidence"]
        # Each attribution replaces the last one: a line that is now settled
        # loses a flag left by an earlier, less certain run.
        if entry["needs_review"]:
            row["needs_review"] = True
            row["review_reasons"] = list(entry.get("review_reasons") or [])
        else:
            row.pop("needs_review", None)
            row.pop("review_reasons", None)

    mirror = _write_transcript_formats(payload, rows, inputs)
    return {
        "status": "transcribed",
        "model": attributed.get("model"),
        "segments": len(rows),
        "needs_review": sum(1 for r in rows if r.get("needs_review")),
        # Whether JEV could decide which dialogue roles are one person. When it
        # could not, roles stay separate and their lines are flagged instead.
        "identity_resolution": attributed.get("identity_resolution"),
        "srt_mirror": mirror,
    }


def _transcribe(video: Path, workspace: Path) -> dict[str, Any]:
    """Transcribe the source and attribute speakers, using JEV for the labels.

    The LLM pass repairs text only. Attribution is JEV's job: it needs no cast
    document (none exists yet at this point), returns a calibrated confidence
    per line, and flags the ones it cannot settle. Nothing here takes a name
    list from the caller.
    """
    from pipeline.transcribe import transcribe

    inputs = workspace / "inputs"
    inputs.mkdir(parents=True, exist_ok=True)
    result = transcribe(
        video,
        inputs / "source-transcript",
        workspace,
        model="large-v3-turbo",
        language="zh",
        separate_vocals=True,
        llm_refine=True,
        context=None,
        # Text repair only; JEV decides who is speaking below. Handing this to
        # the generative pass is what let a session's name list become labels.
        refine_speakers=False,
    )
    if result.get("status") != "qualified_asr_complete":
        return {"status": "transcription_failed", "detail": result}

    payload = json.loads(Path(result["json_path"]).read_text(encoding="utf-8-sig"))
    outcome = _attribute(payload, inputs)
    return {**outcome, "asr": "ran", "json_path": result["json_path"]}


def _transcription_for(video: Path, workspace: Path) -> dict[str, Any]:
    """Reuse the workspace transcript only when it is this video's and complete.

    The transcript must record this video's fingerprint (size plus head/tail
    SHA-256, as in source.json) — a file name cannot tell two uploads apart.
    A matching transcript whose attribution never finished is re-attributed
    without redoing ASR; anything else is transcribed again.
    """
    from pipeline.source_identity import media_fingerprint, transcript_matches_source
    from pipeline.transcribe import _inside

    video = _inside(workspace, video)
    inputs = workspace / "inputs"
    path = inputs / "source-transcript.json"
    payload = _load_json(path) if path.is_file() else None
    if isinstance(payload, dict) and transcript_matches_source(payload, media_fingerprint(video)):
        rows = payload.get("segments")
        if rows == []:
            return {"status": "no_dialogue_detected", "asr": "reused", "json_path": str(path)}
        if _attribution_complete(payload):
            return {
                "status": "reused",
                "asr": "reused",
                "json_path": str(path),
                "segments": len(rows),
                "needs_review": sum(1 for r in rows if r.get("needs_review")),
            }
        if _asr_rows_usable(rows):
            outcome = _attribute(payload, inputs)
            return {**outcome, "asr": "reused", "attribution": "retried", "json_path": str(path)}
    return _transcribe(video, workspace)


def _blocked(reason: str, error: str) -> dict[str, Any]:
    return {"analysis_status": "blocked", "reason": reason, "error": error}


def _index_plan(video: Path, output: Path) -> tuple[bool, dict[str, Any] | None]:
    """Whether to resume ``output``, or why indexing into it must not start.

    A missing directory becomes a new project. An existing one is resumed only
    when it is a Cine project for this directory (project.json names it) whose
    recorded source, if it has one yet, is this video (source.json's size and
    head/tail hashes). Anything else is refused before index_media can create,
    mix or overwrite projects.
    """
    from pipeline.source_identity import fingerprint_of, media_fingerprint

    if not output.exists():
        return False, None
    manifest = _load_json(output / "project.json") if output.is_dir() else None
    if not isinstance(manifest, dict) or manifest.get("project_id") != output.name:
        return False, _blocked(
            "output_not_a_cine_project",
            f"{output} exists but is not a Cine film-analysis project; choose a new output "
            "directory.",
        )
    source_path = output / "source.json"
    if source_path.exists():
        recorded = fingerprint_of(_load_json(source_path))
        if recorded is None:
            return False, _blocked(
                "source_record_invalid", f"{source_path} is unreadable or incomplete."
            )
        if recorded != media_fingerprint(video):
            return False, _blocked(
                "source_changed",
                f"{output} indexes a different video; use a new output directory for this one.",
            )
    return True, None


@mcp.tool()
def film_analyze(video: str, output: str, workspace: str | None = None) -> dict:
    """Run the full film-analysis index on a source video.

    Creates the project, detects shots, samples the timeline, extracts frame
    evidence, renders the report, and transcribes the dialogue with speaker
    attribution. Returns the project paths and the transcription summary.

    Outer status: ``indexed`` only when the index and the transcript are both
    complete; ``index_blocked`` or ``transcription_incomplete`` otherwise.
    Running it again resumes the same project for the same video (a committed,
    validated revision is reused); ASR never starts when indexing did not
    complete.

    Args:
        video: Path to the source video file.
        output: Project directory to create or resume.
        workspace: Session workspace root; inferred from a projects/<name> output.
    """
    from pipeline.entry import index_media

    video_path = Path(video).expanduser().resolve(strict=True)
    output_path = Path(output).expanduser().resolve()
    workspace_path = (
        Path(workspace).expanduser().resolve()
        if workspace
        else output_path.parent.parent
        if output_path.parent.name == "projects"
        else output_path.parent
    )

    resume, result = _index_plan(video_path, output_path)
    if result is None:
        try:
            result = index_media(
                video_path, output_path, workspace=workspace_path, resume=resume
            )
        except (ValueError, OSError, RuntimeError) as exc:
            # index_media's own refusals (changed source, a Topic bound to
            # another project) and missing tools or files end the run here.
            result = _blocked("index_failed", f"{type(exc).__name__}: {exc}")

    if result.get("analysis_status") not in _INDEXED:
        # No ASR for an index that did not complete: it would transcribe into
        # a workspace whose project is missing, foreign or of another video.
        return {
            "status": "index_blocked",
            "result": result,
            "transcription": {"status": "not_run", "reason": "index_blocked"},
            "next": (
                "Indexing did not complete; resolve `result.reason` (a changed source or a "
                "non-project directory needs a new output directory), then run film_analyze "
                f"again. {_VERIFY}"
            ),
        }

    try:
        transcription = _transcription_for(video_path, workspace_path)
    except Exception as exc:  # noqa: BLE001 - reported, never reported as success
        transcription = {"status": "transcription_failed", "error": f"{type(exc).__name__}: {exc}"}

    if transcription.get("status") not in _TRANSCRIBED:
        status = "transcription_incomplete"
        next_step = (
            "The index is ready but the transcript is not; run film_analyze again (it "
            "re-attributes a matching transcript without redoing ASR) or treat dialogue as "
            "unverified."
        )
    else:
        status = "indexed"
        next_step = str(result.get("next_step") or "Read the story plan and fill the story draft.")
    return {
        "status": status,
        "result": result,
        "transcription": transcription,
        "next": f"{next_step} {_VERIFY}",
    }


@mcp.tool()
def film_transcribe(video: str, workspace: str) -> dict:
    """Transcribe a source video and attribute each line to a speaker.

    Text is repaired by the LLM; speaker labels are decided by JEV from the
    dialogue's own address terms, so this works before any cast document
    exists. Lines JEV cannot settle are marked `needs_review` rather than
    returned as if certain.
    """
    return _transcribe(Path(video).expanduser().resolve(strict=True),
                       Path(workspace).expanduser().resolve())


def _find_transcript(project: Path) -> Path | None:
    """Locate the session's transcript.

    A project normally sits at ``<workspace>/projects/<name>``, so the inputs
    directory is two levels up, not one — walking up beats guessing a depth.
    """
    for candidate in (
        project / "inputs" / "source-transcript.json",
        project.parent / "inputs" / "source-transcript.json",
        project.parent.parent / "inputs" / "source-transcript.json",
    ):
        if candidate.is_file():
            return candidate
    return None


def _transcript_status(payload: Any, source: Any) -> str:
    from pipeline.source_identity import fingerprint_of, transcript_matches_source

    if not isinstance(payload, dict):
        return "unreadable"
    if fingerprint_of(payload.get("source_fingerprint")) is None or fingerprint_of(source) is None:
        return "unverified_source"
    if not transcript_matches_source(payload, source):
        return "source_mismatch"
    if payload.get("segments") == []:
        return "no_dialogue"
    return "attributed" if _attribution_complete(payload) else "attribution_incomplete"


@mcp.tool()
def film_review(output: str, workspace: str | None = None) -> dict:
    """Run a TypeSafe JEV semantic quality review and scoring on an analysis project.

    Evaluates dialogue fidelity, character coherence, dramatic causality, and
    overall adaptation readiness, returning a typed 100-point scorecard and
    actionable findings.
    """
    from pipeline.film_review import review_film_analysis

    project = Path(output).expanduser().resolve()
    ws = Path(workspace).expanduser().resolve() if workspace else None
    return review_film_analysis(project, ws)


@mcp.tool()
def film_status(output: str) -> dict:
    """Report what the current revision has produced and what comes next.

    Only the committed revision's own files count. ``story_drafted`` means the
    draft fills every documented field and plan batch; it is not adaptation
    readiness, which only cine_verify_report establishes from image receipts.
    Status: missing | incomplete | story_incomplete | story_drafted.
    """
    from pipeline.draft_check import story_draft_gaps

    project = Path(output).expanduser().resolve()
    if not project.is_dir():
        return {"status": "missing", "path": str(project)}
    manifest = _load_json(project / "project.json")
    revision = manifest.get("current_revision") if isinstance(manifest, dict) else None
    revision = revision if isinstance(revision, str) and _REVISION_ID.fullmatch(revision) else None
    revision_dir = project / "revisions" / revision if revision else None
    story_path = project / "story" / f"{revision}.json" if revision else None
    has = {
        "project.json": isinstance(manifest, dict),
        "report": bool(revision_dir and (revision_dir / "report" / "report.html").is_file()),
        "evidence": bool(revision_dir and (revision_dir / "evidence_index.json").is_file()),
        "story_plan": bool(revision_dir and (revision_dir / "story_plan.json").is_file()),
        "story": bool(story_path and story_path.is_file()),
    }

    source = _load_json(project / "source.json")
    story_gaps: list[str] = []
    if has["story"]:
        story_gaps = story_draft_gaps(
            _load_json(story_path),
            _load_json(revision_dir / "story_plan.json"),
            revision_id=revision,
            source_id=source.get("source_id") if isinstance(source, dict) else None,
        )

    transcript = _find_transcript(project)
    has["transcript"] = transcript is not None
    payload = _load_json(transcript) if transcript is not None else None
    rows = payload.get("segments") if isinstance(payload, dict) else None
    lines_needing_review = (
        sum(1 for r in rows if isinstance(r, dict) and r.get("needs_review"))
        if isinstance(rows, list)
        else 0
    )

    if not (has["project.json"] and revision and has["report"] and has["evidence"]):
        status = "incomplete"
        next_step = "The current revision is not fully indexed; run film_analyze."
    elif not has["story"] or story_gaps:
        status = "story_incomplete"
        next_step = (
            f"Read story_plan.json and fill story/{revision}.json batch by batch through the "
            "ending, with the summary and characters."
        )
    else:
        status = "story_drafted"
        next_step = "The story draft is filled in."

    payload_out: dict[str, Any] = {
        "status": status,
        "path": str(project),
        "revision_id": revision,
        "artifacts": has,
        "story_gaps": story_gaps if has["story"] else ["story_draft_missing"],
        "transcript_path": str(transcript) if transcript else None,
        "transcript_status": (
            _transcript_status(payload, source) if transcript is not None else "absent"
        ),
        "lines_needing_review": lines_needing_review,
        "next": f"{next_step} {_VERIFY}",
    }

    # A quality review counts only for the revision it reviewed.
    for review_path in (
        *((revision_dir / "film_review.json",) if revision_dir else ()),
        project / "film_review.json",
    ):
        review = _load_json(review_path) if review_path.is_file() else None
        if not isinstance(review, dict):
            continue
        if revision and review.get("revision_id") == revision:
            payload_out["quality_score"] = review.get("total_score")
            payload_out["quality_verdict"] = review.get("verdict")
            payload_out.pop("stale_quality_review_revision", None)
            break
        payload_out["stale_quality_review_revision"] = review.get("revision_id")
    return payload_out


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
