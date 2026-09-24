"""Standalone Cine playback review, rendered from the canonical source revision."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path, PureWindowsPath

from .project import validate_identifier
from .review_data import build_review_data, read_json_inside, relative_media_url
from .schemas import CutCandidate, EvidenceRecord, SourceMediaRecord, SourceShot, ValidationResult

try:
    from jinja2 import Environment, FileSystemLoader

    _JINJA2_OK = True
except ImportError:
    _JINJA2_OK = False


def render_report(
    revision_dir: Path,
    source: SourceMediaRecord,
    revision_id: str,
    candidates: list[CutCandidate],
    shots: list[SourceShot],
    evidence: list[EvidenceRecord],
    validation: ValidationResult | None,
    *,
    report_filename: str = "report.html",
    workspace: Path | None = None,
    with_receipt: bool = False,
) -> Path | dict:
    """Render a self-contained interactive report without changing source ledgers."""
    if not _JINJA2_OK:
        raise RuntimeError("Jinja2 is not installed; cannot render report")
    validate_identifier(revision_id, "revision_id")
    validate_identifier(report_filename, "report_filename")
    for candidate in candidates:
        validate_identifier(candidate.candidate_id, "candidate_id")
    for shot in shots:
        validate_identifier(shot.shot_id, "shot_id")
    revision_dir = revision_dir.resolve()
    report_dir = revision_dir / "report"
    if not report_dir.resolve().is_relative_to(revision_dir):
        raise ValueError("Report directory escapes revision")
    report_dir.mkdir(parents=True, exist_ok=True)
    out_path = report_dir / report_filename
    if not out_path.resolve().is_relative_to(report_dir):
        raise ValueError("Report path escapes report directory")

    offset = source.start_pts * source.time_base
    candidate_rows = [
        {
            "pts": candidate.pts,
            "pts_seconds": float(candidate.pts * candidate.time_base - offset),
            "detector": candidate.detector,
            "status": candidate.status.value,
            "evidence": [
                relative_media_url(revision_dir / ev.relative_path.replace("\\", "/"), report_dir)
                for ev in evidence
                if ev.candidate_id == candidate.candidate_id
                and ev.relative_path
                and (revision_dir / ev.relative_path.replace("\\", "/"))
                .resolve()
                .is_relative_to(revision_dir)
                and (revision_dir / ev.relative_path.replace("\\", "/")).is_file()
            ],
        }
        for candidate in candidates
    ]
    media_path = Path(source.path)
    source_name = PureWindowsPath(source.path).name if "\\" in source.path else media_path.name
    cross_drive = False
    try:
        media_rel_path = relative_media_url(media_path, report_dir)
    except ValueError:
        media_rel_path = source_name
        cross_drive = True

    review = build_review_data(revision_dir, source, revision_id, shots, evidence, workspace)
    env = Environment(
        loader=FileSystemLoader(Path(__file__).parent / "templates"),
        autoescape=True,
    )
    # These includes are packaged code; all source content stays escaped or tojson-encoded.
    env.autoescape = lambda name: name is None or name.endswith(".html")
    html = env.get_template("review.html").render(
        source_name=source_name,
        duration_s=f"{source.duration_seconds:.3f}s" if source.duration_seconds else "unknown",
        revision_id=revision_id,
        media_rel_path=media_rel_path,
        cross_drive=cross_drive,
        candidate_count=len(candidates),
        candidate_rows=candidate_rows,
        shots=shots,
        review={key: value for key, value in review.items() if key != "inputFiles"},
        has_validation=validation is not None,
        validation_passed=validation.passed if validation else False,
        validation_issues=[
            issue.model_dump() for issue in (validation.issues if validation else [])
        ],
    )

    tmp_fd, tmp_path = tempfile.mkstemp(dir=str(report_dir), suffix=".tmp.html")
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(html)
        os.replace(tmp_path, str(out_path))
    except Exception:
        Path(tmp_path).unlink(missing_ok=True)
        raise
    # The app reads data, never executes an agent-authored HTML document.
    snapshot = {key: value for key, value in review.items() if key != "inputFiles"}
    snapshot["sourceName"] = media_path.name
    snapshot["reportSha256"] = hashlib.sha256(html.encode("utf-8")).hexdigest()
    data_path = report_dir / "review.json"
    if not data_path.resolve().is_relative_to(report_dir):
        raise ValueError("Review data path escapes report directory")
    fd, temporary = tempfile.mkstemp(dir=str(report_dir), suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(snapshot, handle, ensure_ascii=False)
        os.replace(temporary, data_path)
    except Exception:
        Path(temporary).unlink(missing_ok=True)
        raise
    if not with_receipt:
        return out_path
    counts = {
        kind: sum(cue["type"] == kind for cue in review["cues"])
        for kind in ("story", "shot", "dialogue")
    }
    media_status = (
        "select_local_file"
        if cross_drive
        else "relative_file_available"
        if media_path.is_file()
        else "missing"
    )
    return {
        "status": "report_refreshed",
        "report_path": str(out_path),
        "report_bytes": len(html.encode("utf-8")),
        "report_sha256": hashlib.sha256(html.encode("utf-8")).hexdigest(),
        "source_id": source.source_id,
        "revision_id": revision_id,
        "duration_seconds": source.duration_seconds,
        "counts": {
            "story_batches": counts["story"],
            "source_shots": counts["shot"],
            "dialogue_segments": counts["dialogue"],
            "evidence_images": len(review["images"]),
        },
        "input_files": review["inputFiles"],
        "media": {"status": media_status, "playback_verified": False},
        "warnings": review["warnings"],
        "content_review": "unverified",
        "next_action": "Deliver the report link, counts and warnings. Refresh is complete; "
        "no source reads or browser call are needed.",
    }


def refresh_report(
    project: Path, workspace: Path | None = None, *, with_receipt: bool = False
) -> Path | dict:
    """Refresh the committed revision's report after story/ASR changes; no reindex."""
    project = project.resolve(strict=True)
    if workspace is not None and not project.is_relative_to(workspace.resolve()):
        raise ValueError("Project outside workspace")
    manifest = read_json_inside(project, project / "project.json")
    revision_id = validate_identifier(manifest["current_revision"], "revision_id")
    revision_dir = project / "revisions" / revision_id
    source = SourceMediaRecord.model_validate(read_json_inside(project, project / "source.json"))

    def records(name, model):
        return [
            model.model_validate(row) for row in read_json_inside(project, revision_dir / name)
        ]

    validation_path = revision_dir / "validation.json"
    validation = (
        ValidationResult.model_validate(read_json_inside(project, validation_path))
        if validation_path.exists()
        else None
    )
    result = render_report(
        revision_dir,
        source,
        revision_id,
        records("cut_candidates.json", CutCandidate),
        records("source_shots.json", SourceShot),
        records("evidence_index.json", EvidenceRecord),
        validation,
        workspace=workspace,
        with_receipt=with_receipt,
    )
    if isinstance(result, dict):
        result["input_files"] = [
            str(project / "project.json"),
            str(project / "source.json"),
            *[
                str(revision_dir / name)
                for name in ("cut_candidates.json", "source_shots.json", "evidence_index.json")
            ],
            *([str(validation_path)] if validation is not None else []),
            *result["input_files"],
        ]
    return result
