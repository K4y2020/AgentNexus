"""Cine pipeline — C0+C1 end-to-end orchestrator.

B2: On resume, load existing source.json and compare hashes. Reuse source_id if same; raise if different.
B6: 0 cuts → single shot covering full scope; unavailable detection → RunStatus.interrupted.
B8: current_revision written only after validation passes; render failures go to stages_failed.
"""
from __future__ import annotations

import json
import os
from fractions import Fraction
from pathlib import Path
from typing import List, Optional

from .schemas import (
    CutCandidate,
    EvidenceRecord,
    ProjectRecord,
    PtsInterval,
    RunState,
    RunStatus,
    SourceMediaRecord,
    SourceShot,
    ValidationResult,
)
from .project import (
    ProjectLock,
    _atomic_write,
    _read_json,
    create_project,
    create_revision,
    lock_path,
    project_root,
    resume_project,
    revision_root,
    save_run,
    update_project,
)
from .probe import probe_media
from .detect import detect_cuts, DetectionResult
from .evidence import extract_evidence
from .validate import validate
from .render import render_report


# ---------------------------------------------------------------------------
# Source identity helpers (B2)
# ---------------------------------------------------------------------------

def _load_existing_source(projects_dir: Path, project_id: str) -> Optional[SourceMediaRecord]:
    """Load source.json if it exists; return None otherwise."""
    src_path = project_root(projects_dir, project_id) / "source.json"
    if not src_path.exists():
        return None
    return SourceMediaRecord.model_validate(_read_json(src_path))


def _resolve_source(
    projects_dir: Path,
    project_id: str,
    media_path: Path,
    is_resume: bool,
) -> SourceMediaRecord:
    """
    B2: For new projects, probe and save a fresh source record.
    For resumed projects, compare sha256 of existing source.json against the
    provided media file. Reuse existing source_id if same content; raise if different.
    """
    if is_resume:
        existing = _load_existing_source(projects_dir, project_id)
        if existing is not None:
            # Compare content identity (head + tail hash)
            from .probe import _sha256_chunk
            new_head = _sha256_chunk(media_path, tail=False)
            new_tail = _sha256_chunk(media_path, tail=True)
            if new_head != existing.sha256_head or new_tail != existing.sha256_tail:
                raise ValueError(
                    f"Source media content has changed since project was created "
                    f"(sha256 mismatch). Create a new project for a different source. "
                    f"project_id={project_id}"
                )
            # Same content — reuse source record (keep existing source_id)
            return existing

    # New project or no existing source — probe fresh
    source = probe_media(media_path)
    src_path = project_root(projects_dir, project_id) / "source.json"
    _atomic_write(src_path, source.model_dump())
    return source


# ---------------------------------------------------------------------------
# Public pipeline entry point
# ---------------------------------------------------------------------------

def run_pipeline(
    media_path: Path,
    projects_dir: Path,
    *,
    display_name: Optional[str] = None,
    project_id: Optional[str] = None,
    detector_name: str = "ContentDetector",
    threshold: float = 27.0,
    scope_in_seconds: Optional[float] = None,
    scope_out_seconds: Optional[float] = None,
    clip_window_seconds: float = 1.5,
) -> dict:
    """
    Run C0+C1 pipeline for a single media file.

    B2: On resume, verifies source identity via sha256; raises ValueError on mismatch.
    B6: 0 detected cuts → one shot covering full scope; unavailable detection → interrupted.
    B8: current_revision only committed after validation passes; render errors → stages_failed.
    """
    media_path = Path(media_path)
    if not media_path.exists():
        raise FileNotFoundError(f"Media file not found: {media_path}")

    projects_dir = Path(projects_dir)
    projects_dir.mkdir(parents=True, exist_ok=True)

    name = display_name or media_path.stem
    is_resume = project_id is not None

    # --- C0: Create or resume project ---
    if not is_resume:
        record = create_project(projects_dir, display_name=name)
        project_id = record.project_id
    else:
        record = resume_project(projects_dir, project_id)

    lp = lock_path(projects_dir, project_id)
    with ProjectLock(lp, timeout=5.0):
        # B2: resolve source (reuse on resume, probe on new)
        source = _resolve_source(projects_dir, project_id, media_path, is_resume)

        # Update project with source_id
        record.source_id = source.source_id
        update_project(projects_dir, record)

        # Create revision dir (immutable once committed)
        revision_id = create_revision(projects_dir, project_id)
        rev_dir = revision_root(projects_dir, project_id, revision_id)

        # Create run state
        run_state = RunState(
            project_id=project_id,
            revision_id=revision_id,
            source_id=source.source_id,
            status=RunStatus.running,
            phase="C1",
            source_sha256_head=source.sha256_head,
            source_sha256_tail=source.sha256_tail,
        )
        save_run(projects_dir, run_state)
        run_state.stages_complete.append("C0")
        run_state.stages_complete.append("probe")
        save_run(projects_dir, run_state)

        # --- C1: Detect cuts ---
        tb_num = source.time_base_num
        tb_den = source.time_base_den
        tb = Fraction(tb_num, tb_den)

        scope_in_pts: Optional[int] = None
        scope_out_pts: Optional[int] = None
        if scope_in_seconds is not None and tb != 0:
            # scope in/out are user-visible playback seconds; convert to source PTS
            scope_in_pts = source.start_pts + int(round(scope_in_seconds / float(tb)))
        if scope_out_seconds is not None and tb != 0:
            scope_out_pts = source.start_pts + int(round(scope_out_seconds / float(tb)))

        # Build scope PtsInterval for validation (B7)
        scope_start = scope_in_pts if scope_in_pts is not None else source.start_pts
        scope_end_raw = scope_out_pts if scope_out_pts is not None else (
            source.start_pts + (source.duration_pts or 0)
        )
        scope_interval: Optional[PtsInterval] = None
        if scope_end_raw > scope_start:
            scope_interval = PtsInterval(
                in_pts=scope_start,
                out_pts=scope_end_raw,
                time_base_num=tb_num,
                time_base_den=tb_den,
            )

        # B6: detect_cuts() returns DetectionResult(status, candidates)
        detection: DetectionResult = detect_cuts(
            media_path,
            source,
            revision_id,
            detector_name=detector_name,
            threshold=threshold,
            scope_in_pts=scope_in_pts,
            scope_out_pts=scope_out_pts,
        )

        if detection.status == "unavailable":
            # PySceneDetect not installed — mark interrupted, do not continue
            run_state.status = RunStatus.interrupted
            run_state.error = "detection unavailable: PySceneDetect not installed"
            run_state.stages_failed.append("detect")
            save_run(projects_dir, run_state)
            return {
                "project_id": project_id,
                "revision_id": revision_id,
                "run_id": run_state.run_id,
                "source_id": source.source_id,
                "status": "interrupted",
                "reason": "detection_unavailable",
                "candidate_count": 0,
            }

        if detection.status == "failed":
            # Runtime detection error — mark failed
            run_state.status = RunStatus.failed
            run_state.error = "detection failed at runtime"
            run_state.stages_failed.append("detect")
            save_run(projects_dir, run_state)
            return {
                "project_id": project_id,
                "revision_id": revision_id,
                "run_id": run_state.run_id,
                "source_id": source.source_id,
                "status": "failed",
                "reason": "detection_failed",
                "candidate_count": 0,
            }

        candidates: List[CutCandidate] = detection.candidates

        run_state.stages_complete.append("detect")
        save_run(projects_dir, run_state)

        # Save cut_candidates.json
        _atomic_write(
            rev_dir / "cut_candidates.json",
            [c.model_dump() for c in candidates],
        )

        # Extract evidence for each candidate
        all_evidence: List[EvidenceRecord] = []
        for c in candidates:
            evs = extract_evidence(
                media_path, source, c, rev_dir,
                clip_window_seconds=clip_window_seconds,
            )
            all_evidence.extend(evs)

        # Save evidence index
        _atomic_write(
            rev_dir / "evidence_index.json",
            [e.model_dump() for e in all_evidence],
        )
        run_state.stages_complete.append("evidence")
        # B11: surface unavailable evidence in run state
        unavailable_ev = [e for e in all_evidence if e.extraction_status == "unavailable"]
        if unavailable_ev:
            run_state.stages_failed.append("evidence")
        save_run(projects_dir, run_state)

        # Build shots (B6: 0 cuts → single shot covering scope)
        shots: List[SourceShot] = _build_shots(
            source, revision_id, candidates, scope_in_pts, scope_out_pts,
        )

        # Save source_shots.json
        _atomic_write(
            rev_dir / "source_shots.json",
            [s.model_dump() for s in shots],
        )

        # Validate (B7: pass scope_interval)
        validation = validate(
            revision_id=revision_id,
            source=source,
            candidates=candidates,
            shots=shots,
            evidence=all_evidence,
            revision_dir=rev_dir,
            scope=scope_interval,
        )
        _atomic_write(rev_dir / "validation.json", validation.model_dump())

        if not validation.passed:
            # B8(a): validation failed — halt run, do NOT render
            run_state.stages_failed.append("validate")
            run_state.status = RunStatus.failed
            run_state.error = (
                f"validation failed with {sum(1 for i in validation.issues if i.severity == 'error')} error(s)"
            )
            save_run(projects_dir, run_state)
            return {
                "project_id": project_id,
                "revision_id": revision_id,
                "run_id": run_state.run_id,
                "source_id": source.source_id,
                "status": "failed",
                "reason": "validation_failed",
                "validation_passed": False,
                "validation_issues": len(validation.issues),
                "candidate_count": len(candidates),
                "shot_count": len(shots),
            }

        run_state.stages_complete.append("validate")
        save_run(projects_dir, run_state)

        # Render HTML (B8: render failure → stages_failed, not finished)
        report_path_str: str
        try:
            report_path = render_report(
                revision_dir=rev_dir,
                source=source,
                revision_id=revision_id,
                candidates=candidates,
                shots=shots,
                evidence=all_evidence,
                validation=validation,
            )
            report_path_str = str(report_path)
            run_state.stages_complete.append("render")
            # B8(b): only commit current_revision after render succeeds
            record.current_revision = revision_id
            update_project(projects_dir, record)
        except Exception as exc:
            report_path_str = f"render_failed: {exc}"
            run_state.stages_failed.append("render")  # B8
            run_state.status = RunStatus.failed
            run_state.error = f"render failed: {exc}"
            save_run(projects_dir, run_state)
            return {
                "project_id": project_id,
                "revision_id": revision_id,
                "run_id": run_state.run_id,
                "source_id": source.source_id,
                "status": "failed",
                "reason": "render_failed",
                "error": str(exc),
                "validation_passed": validation.passed,
                "candidate_count": len(candidates),
                "shot_count": len(shots),
            }

        # Mark run complete
        run_state.status = RunStatus.finished
        save_run(projects_dir, run_state)

        return {
            "project_id": project_id,
            "revision_id": revision_id,
            "run_id": run_state.run_id,
            "source_id": source.source_id,
            "candidate_count": len(candidates),
            "shot_count": len(shots),
            "evidence_count": len(all_evidence),
            "validation_passed": validation.passed,
            "report_path": report_path_str,
        }


def _build_shots(
    source: SourceMediaRecord,
    revision_id: str,
    candidates: List[CutCandidate],
    scope_in_pts: Optional[int],
    scope_out_pts: Optional[int],
) -> List[SourceShot]:
    """
    Build SourceShot records as intervals between consecutive cut points.

    B6: When no cuts detected (empty candidates or all unavailable),
    create ONE shot covering the full validated scope [scope_in_pts, scope_out_pts).
    """
    tb_num = source.time_base_num
    tb_den = source.time_base_den

    start = scope_in_pts if scope_in_pts is not None else source.start_pts
    end = scope_out_pts if scope_out_pts is not None else (
        source.start_pts + (source.duration_pts or 0)
    )
    if end <= start:
        return []

    # Gather valid candidate PTS values (skip unavailable)
    pts_values = sorted(
        set(
            c.pts
            for c in candidates
            if c.detector_status.value == "ok"
            and start < c.pts < end
        )
    )

    # B6: no valid cuts → single shot covering full scope
    if not pts_values:
        return [SourceShot(
            source_id=source.source_id,
            revision_id=revision_id,
            interval=PtsInterval(
                in_pts=start,
                out_pts=end,
                time_base_num=tb_num,
                time_base_den=tb_den,
            ),
        )]

    boundaries = [start] + pts_values + [end]
    shots: List[SourceShot] = []
    for i in range(len(boundaries) - 1):
        in_pts = boundaries[i]
        out_pts = boundaries[i + 1]
        if out_pts <= in_pts:
            continue
        shots.append(SourceShot(
            source_id=source.source_id,
            revision_id=revision_id,
            interval=PtsInterval(
                in_pts=in_pts,
                out_pts=out_pts,
                time_base_num=tb_num,
                time_base_den=tb_den,
            ),
        ))

    return shots
