"""Cine pipeline — validation: interval non-overlap, reference integrity, media existence.

B7: Added scope coverage check, candidate bounds check, evidence ownership check,
    shot→candidate reference completeness.
"""
from __future__ import annotations

from pathlib import Path
from typing import List, Optional

from .schemas import (
    CutCandidate,
    EvidenceRecord,
    PtsInterval,
    SourceMediaRecord,
    SourceShot,
    ValidationIssue,
    ValidationResult,
)


# ---------------------------------------------------------------------------
# Interval helpers
# ---------------------------------------------------------------------------

def _overlaps(a: PtsInterval, b: PtsInterval) -> bool:
    """Return True if half-open intervals [a.in, a.out) and [b.in, b.out) overlap."""
    return a.in_pts < b.out_pts and b.in_pts < a.out_pts


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def validate(
    revision_id: str,
    source: SourceMediaRecord,
    candidates: List[CutCandidate],
    shots: List[SourceShot],
    evidence: List[EvidenceRecord],
    *,
    revision_dir: Path,
    scope: Optional[PtsInterval] = None,   # B7: requested scope for coverage check
) -> ValidationResult:
    """
    Run all C0+C1 validation checks. Returns a ValidationResult.

    B7 additions:
    - scope coverage: if scope given, shot intervals must cover [scope.in_pts, scope.out_pts)
      with no gaps and no overlaps
    - candidate bounds: each candidate pts must be within [source.start_pts, start_pts+duration)
    - evidence ownership: source_id and revision_id on each EvidenceRecord must match
    - shot candidate reference completeness
    """
    issues: List[ValidationIssue] = []

    # 1. Media file must exist
    media_path = Path(source.path)
    if not media_path.exists():
        issues.append(ValidationIssue(
            severity="error",
            code="MEDIA_NOT_FOUND",
            message=f"Source media not found: {source.path}",
        ))

    # 2. Each evidence file must exist on disk (only when relative_path is set)
    for ev in evidence:
        if ev.relative_path is not None and ev.extraction_status == "ok":
            ev_path = revision_dir / ev.relative_path
            if not ev_path.exists():
                issues.append(ValidationIssue(
                    severity="error",
                    code="EVIDENCE_FILE_MISSING",
                    message=f"Evidence file missing: {ev.relative_path}",
                    context={"evidence_id": ev.evidence_id},
                ))

    # 3. Evidence must reference valid candidate IDs
    candidate_ids = {c.candidate_id for c in candidates}
    for ev in evidence:
        if ev.candidate_id and ev.candidate_id not in candidate_ids:
            issues.append(ValidationIssue(
                severity="error",
                code="EVIDENCE_INVALID_CANDIDATE_REF",
                message=f"Evidence {ev.evidence_id} references unknown candidate {ev.candidate_id}",
                context={"evidence_id": ev.evidence_id, "candidate_id": ev.candidate_id},
            ))

    # B7c: Evidence ownership — source_id and revision_id must match
    for ev in evidence:
        if ev.source_id != source.source_id:
            issues.append(ValidationIssue(
                severity="error",
                code="EVIDENCE_SOURCE_MISMATCH",
                message=f"Evidence {ev.evidence_id} source_id {ev.source_id!r} != {source.source_id!r}",
                context={"evidence_id": ev.evidence_id},
            ))
        if ev.revision_id != revision_id:
            issues.append(ValidationIssue(
                severity="error",
                code="EVIDENCE_REVISION_MISMATCH",
                message=f"Evidence {ev.evidence_id} revision_id {ev.revision_id!r} != {revision_id!r}",
                context={"evidence_id": ev.evidence_id},
            ))

    # 4. Shot intervals must be non-empty and non-overlapping
    for shot in shots:
        if shot.interval.out_pts <= shot.interval.in_pts:
            issues.append(ValidationIssue(
                severity="error",
                code="EMPTY_INTERVAL",
                message=f"Shot {shot.shot_id} has empty interval [{shot.interval.in_pts}, {shot.interval.out_pts})",
                context={"shot_id": shot.shot_id},
            ))

    for i, a in enumerate(shots):
        for b in shots[i + 1:]:
            if _overlaps(a.interval, b.interval):
                issues.append(ValidationIssue(
                    severity="error",
                    code="OVERLAPPING_INTERVALS",
                    message=(
                        f"Shots {a.shot_id} and {b.shot_id} have overlapping intervals: "
                        f"[{a.interval.in_pts},{a.interval.out_pts}) vs "
                        f"[{b.interval.in_pts},{b.interval.out_pts})"
                    ),
                    context={"shot_a": a.shot_id, "shot_b": b.shot_id},
                ))

    # 5. Shot candidate_ids must reference known candidates (B7d)
    for shot in shots:
        for cid in shot.candidate_ids:
            if cid not in candidate_ids:
                issues.append(ValidationIssue(
                    severity="error",
                    code="SHOT_INVALID_CANDIDATE_REF",
                    message=f"Shot {shot.shot_id} references unknown candidate {cid}",
                    context={"shot_id": shot.shot_id, "candidate_id": cid},
                ))

    # 6. All shots must have source_id matching source
    for shot in shots:
        if shot.source_id != source.source_id:
            issues.append(ValidationIssue(
                severity="error",
                code="SHOT_SOURCE_MISMATCH",
                message=f"Shot {shot.shot_id} has source_id {shot.source_id!r}, expected {source.source_id!r}",
                context={"shot_id": shot.shot_id},
            ))

    # B7: SHOT_REVISION_MISMATCH — each shot must belong to this revision
    for shot in shots:
        if shot.revision_id != revision_id:
            issues.append(ValidationIssue(
                severity="error",
                code="SHOT_REVISION_MISMATCH",
                message=(
                    f"Shot {shot.shot_id} has revision_id {shot.revision_id!r}, "
                    f"expected {revision_id!r}"
                ),
                context={"shot_id": shot.shot_id},
            ))

    # B7: SHOT_OUTSIDE_SCOPE — each shot interval must lie within scope
    if scope is not None:
        for shot in shots:
            if shot.interval.in_pts < scope.in_pts or shot.interval.out_pts > scope.out_pts:
                issues.append(ValidationIssue(
                    severity="error",
                    code="SHOT_OUTSIDE_SCOPE",
                    message=(
                        f"Shot {shot.shot_id} interval [{shot.interval.in_pts}, {shot.interval.out_pts}) "
                        f"extends outside scope [{scope.in_pts}, {scope.out_pts})"
                    ),
                    context={"shot_id": shot.shot_id},
                ))

    # 7. Candidate status must all be "candidate" (not auto-accepted)
    for c in candidates:
        if c.status.value not in ("candidate", "rejected"):
            issues.append(ValidationIssue(
                severity="warning",
                code="CANDIDATE_AUTO_ACCEPTED",
                message=f"Candidate {c.candidate_id} has status {c.status!r} — all cuts must remain candidate until human review",
                context={"candidate_id": c.candidate_id},
            ))

    # B7b: Candidate PTS bounds — must be within [start_pts, start_pts + duration_pts)
    if source.duration_pts is not None:
        media_end_pts = source.start_pts + source.duration_pts
        for c in candidates:
            if c.detector_status.value == "ok":
                if not (source.start_pts <= c.pts < media_end_pts):
                    issues.append(ValidationIssue(
                        severity="error",
                        code="CANDIDATE_OUT_OF_BOUNDS",
                        message=(
                            f"Candidate {c.candidate_id} pts={c.pts} is outside "
                            f"[{source.start_pts}, {media_end_pts})"
                        ),
                        context={"candidate_id": c.candidate_id},
                    ))

    # B7a: Scope coverage — shots must cover [scope.in_pts, scope.out_pts) with no gaps
    if scope is not None:
        if not shots:
            # B7: empty shot list with a scope is always an error
            issues.append(ValidationIssue(
                severity="error",
                code="COVERAGE_EMPTY",
                message=(
                    f"No shots produced but scope [{scope.in_pts}, {scope.out_pts}) was requested; "
                    "expected at least one shot covering the scope"
                ),
                context={"scope_in": scope.in_pts, "scope_out": scope.out_pts},
            ))
        else:
            sorted_shots = sorted(shots, key=lambda s: s.interval.in_pts)
            # Check coverage start
            if sorted_shots[0].interval.in_pts > scope.in_pts:
                issues.append(ValidationIssue(
                    severity="error",
                    code="SCOPE_COVERAGE_GAP_START",
                    message=(
                        f"Shots start at PTS {sorted_shots[0].interval.in_pts} but scope starts "
                        f"at {scope.in_pts}; gap at beginning"
                    ),
                    context={"scope_in": scope.in_pts},
                ))
            # Check coverage end
            if sorted_shots[-1].interval.out_pts < scope.out_pts:
                issues.append(ValidationIssue(
                    severity="error",
                    code="SCOPE_COVERAGE_GAP_END",
                    message=(
                        f"Shots end at PTS {sorted_shots[-1].interval.out_pts} but scope ends "
                        f"at {scope.out_pts}; gap at end"
                    ),
                    context={"scope_out": scope.out_pts},
                ))
            # Check internal gaps
            for i in range(len(sorted_shots) - 1):
                a_out = sorted_shots[i].interval.out_pts
                b_in = sorted_shots[i + 1].interval.in_pts
                if a_out < b_in:
                    issues.append(ValidationIssue(
                        severity="error",
                        code="SCOPE_COVERAGE_GAP",
                        message=f"Gap between shots: [{a_out}, {b_in}) is uncovered",
                        context={"gap_in": a_out, "gap_out": b_in},
                    ))

    errors = [i for i in issues if i.severity == "error"]
    return ValidationResult(
        revision_id=revision_id,
        source_id=source.source_id,
        passed=len(errors) == 0,
        issues=issues,
    )
