"""Deterministic structure check of a story draft against its temporal plan.

It says whether the draft is *filled in*: the documented summary fields, a
character list, and one section per plan batch with events and a connection.
It does not look at image receipts and does not judge whether the reading is
right — adaptation readiness belongs to ``cine_verify_report``, which checks
the inspected images. A skeleton written by the indexer fails this check.

Standard library only, so the MCP server can use it without the JEV SDK.
"""

from __future__ import annotations

from typing import Any


def _text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def story_draft_gaps(
    draft: Any,
    plan: Any,
    *,
    revision_id: str | None = None,
    source_id: str | None = None,
) -> list[str]:
    """Every documented field or temporal batch the draft leaves unfilled."""
    if not isinstance(draft, dict):
        return ["story_draft_invalid"]
    gaps: list[str] = []
    if revision_id is not None and draft.get("revision_id") != revision_id:
        gaps.append("story_revision_mismatch")
    if source_id is not None and draft.get("source_id") != source_id:
        gaps.append("story_source_mismatch")

    summary = draft.get("summary")
    if not isinstance(summary, dict):
        gaps.append("summary_missing")
    else:
        for field in ("premise", "conflict", "ending"):
            if not _text(summary.get(field)):
                gaps.append(f"summary_{field}_missing")
        turning_points = summary.get("turning_points")
        if not isinstance(turning_points, list) or not all(_text(t) for t in turning_points):
            gaps.append("turning_points_list_required")

    characters = draft.get("characters")
    if not isinstance(characters, list) or not characters or not all(_text(c) for c in characters):
        gaps.append("characters_required")

    batches = plan.get("batches") if isinstance(plan, dict) else None
    if not isinstance(batches, list) or not batches:
        gaps.append("story_plan_missing")
        return gaps
    if revision_id is not None and plan.get("revision_id") != revision_id:
        gaps.append("story_plan_revision_mismatch")
    sections = draft.get("sections")
    by_id = (
        {s.get("batch_id"): s for s in sections if isinstance(s, dict)}
        if isinstance(sections, list)
        else {}
    )
    for batch in batches:
        batch_id = batch.get("batch_id") if isinstance(batch, dict) else None
        row = by_id.get(batch_id)
        if row is None:
            gaps.append(f"section_missing:{batch_id}")
        elif not (
            isinstance(row.get("events"), list)
            and row["events"]
            and all(_text(e) for e in row["events"])
            and _text(row.get("connection"))
        ):
            gaps.append(f"section_incomplete:{batch_id}")
    return gaps
