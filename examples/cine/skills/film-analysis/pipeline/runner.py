"""Cine pipeline — C0+C1 end-to-end orchestrator."""
from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import List, Optional

from .schemas import (
    CutCandidate,
    EvidenceRecord,
    ProjectRecord,
    RunState,
    RunStatus,
    SourceMediaRecord,
    SourceShot,
    PtsInterval,
    ValidationResult,
)
from .project import (
    ProjectLock,
    _atomic_write,
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
from .detect import detect_cuts
from .evidence import extract_evidence
from .validate import validate
from .render import render_report


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

    - Creates or resumes a project (C0)
    - Probes source media
    - Detects scene cuts in the specified scope (C1)
    - Extracts evidence frames/clips
    - Validates results
    - Renders HTML report

    Returns a summary dict with project_id, revision_id, run_id, report_path,
    and validation result.
    """
    media_path = Path(media_path)
    if not media_path.exists():
        raise FileNotFoundError(f"Media file not found: {media_path}")

    projects_dir = Path(projects_dir)
    projects_dir.mkdir(parents=True, exist_ok=True)

    name = display_name or media_path.stem

    # --- C0: Create or resume project ---
    if project_id is None:
        record = create_project(projects_dir, display_name=name)
        project_id = record.project_id
    else:
        record = resume_project(projects_dir, project_id)

    lp = lock_path(projects_dir, project_id)
    with ProjectLock(lp, timeout=5.0):
        # Probe source
        source = probe_media(media_path)

        # Save source.json
        src_path = project_root(projects_dir, project_id) / "source.json"
        _atomic_write(src_path, source.model_dump())

        # Update project with source_id
        record.source_id = source.source_id
        update_project(projects_dir, record)

        # Create revision
        revision_id = create_revision(projects_dir, project_id)
        record.current_revision = revision_id
        update_project(projects_dir, record)

        rev_dir = revision_root(projects_dir, project_id, revision_id)

        # Create run state
        run_state = RunState(
            project_id=project_id,
            revision_id=revision_id,
            status=RunStatus.running,
            phase="C1",
            source_sha256_head=source.sha256_head,
            source_sha256_tail=source.sha256_tail,
        )
        save_run(projects_dir, run_state)

        # --- C1: Detect cuts ---
        scope_in_pts: Optional[int] = None
        scope_out_pts: Optional[int] = None
        tb_num = source.time_base_num
        tb_den = source.time_base_den

        from fractions import Fraction
        tb = Fraction(tb_num, tb_den)

        if scope_in_seconds is not None and tb != 0:
            scope_in_pts = int(round(scope_in_seconds / float(tb)))
        if scope_out_seconds is not None and tb != 0:
            scope_out_pts = int(round(scope_out_seconds / float(tb)))

        candidates: List[CutCandidate] = detect_cuts(
            media_path,
            source,
            revision_id,
            detector_name=detector_name,
            threshold=threshold,
            scope_in_pts=scope_in_pts,
            scope_out_pts=scope_out_pts,
        )

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

        # Build shots from candidates (one shot per interval between cuts)
        shots: List[SourceShot] = _build_shots(
            source, revision_id, candidates, scope_in_pts, scope_out_pts,
        )

        # Save source_shots.json
        _atomic_write(
            rev_dir / "source_shots.json",
            [s.model_dump() for s in shots],
        )

        # Validate
        validation = validate(
            revision_id=revision_id,
            source=source,
            candidates=candidates,
            shots=shots,
            evidence=all_evidence,
            revision_dir=rev_dir,
        )
        _atomic_write(rev_dir / "validation.json", validation.model_dump())

        # Render HTML
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
        except Exception as exc:
            report_path_str = f"render_failed: {exc}"

        # Mark run complete
        run_state.status = RunStatus.finished
        run_state.stages_complete = ["C0", "probe", "detect", "evidence", "validate", "render"]
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
    """Build SourceShot records as intervals between consecutive cut points."""
    from .schemas import SourceShot

    if not candidates:
        return []

    tb_num = source.time_base_num
    tb_den = source.time_base_den

    # Gather valid candidate PTS values (skip unavailable)
    pts_values = sorted(
        set(
            c.pts
            for c in candidates
            if c.detector_status.value == "ok"
        )
    )

    if not pts_values:
        return []

    # Build boundary list
    start = scope_in_pts if scope_in_pts is not None else (source.start_pts or 0)
    end = scope_out_pts if scope_out_pts is not None else (
        (source.start_pts or 0) + (source.duration_pts or 0)
    )
    if end <= start:
        return []

    boundaries = [start] + [p for p in pts_values if start < p < end] + [end]

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
