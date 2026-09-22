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
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

_PIPELINE = Path(__file__).resolve().parent / "skills" / "film-analysis" / "pipeline"
if str(_PIPELINE) not in sys.path:
    sys.path.insert(0, str(_PIPELINE))

from mcp.server.fastmcp import FastMCP  # noqa: E402

mcp = FastMCP("cine-film-analysis")


def _transcribe(video: Path, workspace: Path) -> dict[str, Any]:
    """Transcribe the source and attribute speakers, using JEV for the labels.

    The LLM pass repairs text only. Attribution is JEV's job: it needs no cast
    document (none exists yet at this point), returns a calibrated confidence
    per line, and flags the ones it cannot settle. Nothing here takes a name
    list from the caller.
    """
    from attribute_speakers import attribute_speakers, derived_cast
    from transcribe import transcribe

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
    rows = payload.get("segments") or []
    if not rows:
        return {"status": "no_dialogue_detected", **result}

    attributed = attribute_speakers(rows, derived_cast(rows), scenes={})
    if "error" in attributed:
        return {"status": "attribution_unavailable", "detail": attributed, **result}

    attributions = attributed["attributions"]
    if len(attributions) != len(rows):
        return {
            "status": "attribution_incomplete",
            "expected": len(rows),
            "received": len(attributions),
            "detail": result,
        }

    for row, entry in zip(rows, attributions):
        row["speaker"] = entry["speaker_name"]
        row["delivery"] = entry["delivery"]
        row["attribution_confidence"] = entry["confidence"]
        if entry["needs_review"]:
            row["needs_review"] = True

    _write_transcript_formats(payload, rows, inputs)
    return {
        "status": "transcribed",
        "model": attributed.get("model"),
        "segments": len(rows),
        "needs_review": sum(1 for r in rows if r.get("needs_review")),
        "json_path": result["json_path"],
    }


def _write_transcript_formats(payload: dict, rows: list[dict], inputs: Path) -> None:
    payload["segments"] = rows
    payload["speaker_attribution"] = "jev"
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    (inputs / "source-transcript.json").write_text(text, encoding="utf-8")
    lines = [
        "# ASR transcript (qualified, not manually verified verbatim)",
        "",
        *[
            f"[{r['start']:08.3f}-{r['end']:08.3f}] "
            + (f"[{r['speaker']}] " if r.get("speaker") else "")
            + r["text"]
            for r in rows
        ],
        "",
    ]
    (inputs / "source-transcript.txt").write_text("\n".join(lines), encoding="utf-8")


@mcp.tool()
def film_analyze(video: str, output: str, workspace: str | None = None) -> dict:
    """Run the full film-analysis index on a source video.

    Creates the project, detects shots, samples the timeline, extracts frame
    evidence, renders the report, and transcribes the dialogue with speaker
    attribution. Returns the project paths and the transcription summary.

    Args:
        video: Path to the source video file.
        output: Project directory to create or resume.
        workspace: Session workspace root; defaults to the output's parent.
    """
    from entry import index_media

    video_path = Path(video).expanduser().resolve(strict=True)
    output_path = Path(output).expanduser().resolve()
    workspace_path = Path(workspace).expanduser().resolve() if workspace else output_path.parent

    result = index_media(video_path, output_path, workspace=workspace_path)
    status: dict[str, Any] = {"status": "indexed", "result": result}

    transcript = workspace_path / "inputs" / "source-transcript.json"
    if not transcript.is_file():
        status["transcription"] = _transcribe(video_path, workspace_path)
    else:
        status["transcription"] = {"status": "already_present", "json_path": str(transcript)}
    return status


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


@mcp.tool()
def film_review(output: str, workspace: str | None = None) -> dict:
    """Run a TypeSafe JEV semantic quality review and scoring on an analysis project.

    Evaluates dialogue fidelity, character coherence, dramatic causality, and
    overall adaptation readiness, returning a typed 100-point scorecard and
    actionable findings.
    """
    from film_review import review_film_analysis

    project = Path(output).expanduser().resolve()
    ws = Path(workspace).expanduser().resolve() if workspace else None
    return review_film_analysis(project, ws)


@mcp.tool()
def film_status(output: str) -> dict:
    """Report what the analysis project has produced and what comes next."""
    project = Path(output).expanduser().resolve()
    if not project.is_dir():
        return {"status": "missing", "path": str(project)}
    has = {
        "project.json": (project / "project.json").is_file(),
        "report": any(project.rglob("report/report.html")),
        "evidence": any(project.rglob("evidence_index.json")),
        "story": any(project.rglob("story/*.json")),
    }
    transcript = _find_transcript(project)
    has["transcript"] = transcript is not None
    review = 0
    if transcript is not None:
        try:
            rows = json.loads(transcript.read_text(encoding="utf-8-sig")).get("segments") or []
            review = sum(1 for r in rows if r.get("needs_review"))
        except (OSError, ValueError):
            pass

    jev_review_data = None
    review_file = project / "film_review.json"
    if review_file.is_file():
        try:
            jev_review_data = json.loads(review_file.read_text(encoding="utf-8"))
        except Exception:
            pass

    status_payload = {
        "status": "ready" if all(has.values()) else "incomplete",
        "path": str(project),
        "artifacts": has,
        "transcript_path": str(transcript) if transcript else None,
        "lines_needing_review": review,
        "next": "write <project>/story/<revision>.json, then run production.py",
    }
    if jev_review_data:
        status_payload["quality_score"] = jev_review_data.get("total_score")
        status_payload["quality_verdict"] = jev_review_data.get("verdict")
    return status_payload


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
