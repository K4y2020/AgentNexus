"""Deterministic structure check of a story draft against its temporal plan.

It says whether the draft is *filled in*: the documented summary fields, a
forms list (empty when the film uses none), a character list, and one section
per plan batch with events and a connection.
It does not look at image receipts and does not judge whether the reading is
right — adaptation readiness belongs to ``cine_verify_report``, which checks
the inspected images. A skeleton written by the indexer fails this check.

Standard library only, so the MCP server can use it without the JEV SDK.
"""

from __future__ import annotations

from typing import Any

# The whole-film shooting forms; form_gate.py describes each one.
FORMS = (
    "pov",
    "direct_address",
    "mockumentary",
    "one_take",
    "screen_life",
    "found_footage",
    "split_screen",
    "other",
)


def _text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def story_draft_issues(
    draft: Any,
    plan: Any,
    *,
    revision_id: str | None = None,
    source_id: str | None = None,
) -> list[dict[str, Any]]:
    """Every documented field or temporal batch the draft leaves unfilled.

    Each issue is ``{"reason": code}``, plus ``batch_id`` for a plan batch.
    agentnexus's cine_verify_report loads this module for the same check.
    """
    if not isinstance(draft, dict):
        return [{"reason": "story_draft_invalid"}]
    issues: list[dict[str, Any]] = []
    if revision_id is not None and draft.get("revision_id") != revision_id:
        issues.append({"reason": "story_revision_mismatch"})
    if source_id is not None and draft.get("source_id") != source_id:
        issues.append({"reason": "story_source_mismatch"})

    summary = draft.get("summary")
    if not isinstance(summary, dict):
        issues.append({"reason": "summary_missing"})
    else:
        for field in ("premise", "conflict", "ending"):
            if not _text(summary.get(field)):
                issues.append({"reason": f"summary_{field}_missing"})
        turning_points = summary.get("turning_points")
        if not isinstance(turning_points, list) or not all(_text(t) for t in turning_points):
            issues.append({"reason": "turning_points_list_required"})

    forms = draft.get("forms")
    if not isinstance(forms, list):
        issues.append({"reason": "forms_list_required"})
    elif not all(
        isinstance(f, dict) and f.get("form") in FORMS and _text(f.get("how")) for f in forms
    ):
        issues.append({"reason": "form_entry_invalid"})

    characters = draft.get("characters")
    if not isinstance(characters, list) or not characters or not all(_text(c) for c in characters):
        issues.append({"reason": "characters_required"})

    batches = plan.get("batches") if isinstance(plan, dict) else None
    if not isinstance(batches, list) or not batches:
        issues.append({"reason": "story_plan_missing"})
        return issues
    if revision_id is not None and plan.get("revision_id") != revision_id:
        issues.append({"reason": "story_plan_revision_mismatch"})
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
            issues.append({"reason": "section_missing", "batch_id": batch_id})
        elif not (
            isinstance(row.get("events"), list)
            and row["events"]
            and all(_text(e) for e in row["events"])
            and _text(row.get("connection"))
        ):
            issues.append({"reason": "section_incomplete", "batch_id": batch_id})
    return issues


def story_draft_gaps(
    draft: Any,
    plan: Any,
    *,
    revision_id: str | None = None,
    source_id: str | None = None,
) -> list[str]:
    """The same issues as codes, with ``:<batch_id>`` for a plan batch."""
    return [
        f"{issue['reason']}:{issue['batch_id']}" if "batch_id" in issue else issue["reason"]
        for issue in story_draft_issues(draft, plan, revision_id=revision_id, source_id=source_id)
    ]
