"""Current-session, evidence-derived visual coverage; imported labels are not proof."""

import math
from fractions import Fraction
from pathlib import Path
from typing import cast

from agentnexus.seedance.cine_contracts import (
    _inside,
    _read,
    current_ledger,
    reviewed_shots,
    session_image_receipts,
)


def interval_seconds(interval):
    values = [interval.get(k) for k in ("in_pts", "out_pts", "time_base_num", "time_base_den")]
    if any(type(v) is not int for v in values):
        raise ValueError("CINE_INTERVAL_INVALID")
    start, end, num, den = values
    if start < 0 or end <= start or num <= 0 or den <= 0:
        raise ValueError("CINE_INTERVAL_INVALID")
    base = Fraction(num, den)
    return start * base, end * base


def covered(intervals, start, end):
    cursor = start
    for low, high in sorted(intervals):
        if low > cursor:
            return False
        cursor = max(cursor, high)
        if cursor >= end:
            return True
    return False


async def verify_report(
    client, session_id, *, scope="full", start_seconds=None, end_seconds=None, ledger_path=None
):
    response = await client.get(f"/v1/sessions/{session_id}", timeout=10)
    response.raise_for_status()
    session = response.json()
    if session.get("id") != session_id or not session.get("workspace"):
        raise ValueError("CINE_SESSION_WORKSPACE_REQUIRED")
    workspace = Path(session["workspace"]).resolve()
    ledger = current_ledger(workspace, session_id)
    project = ledger.parent.parent.parent
    source = _read(_inside(project, project / "source.json"))
    duration = source.get("duration_pts")
    num, den = source.get("time_base_num"), source.get("time_base_den")
    if any(type(v) is not int or v <= 0 for v in (duration, num, den)):
        raise ValueError("CINE_SOURCE_DURATION_REQUIRED")
    total = Fraction(duration * num, den)
    if scope == "adaptation":
        if start_seconds is not None or end_seconds is not None:
            raise ValueError("CINE_ADAPTATION_READINESS_REQUIRES_FULL_SCOPE")
        from agentnexus.seedance.story_review import verify_story

        return await verify_story(client, session_id, workspace, ledger, source, ledger_path)
    if scope == "full":
        if start_seconds is not None or end_seconds is not None:
            raise ValueError("CINE_FULL_SCOPE_CANNOT_BE_SHORTENED")
        start, end = Fraction(0), total
    elif scope == "sample":
        if any(
            type(v) not in (int, float) or not math.isfinite(cast(float, v))
            for v in (start_seconds, end_seconds)
        ):
            raise ValueError("CINE_SAMPLE_RANGE_REQUIRED")
        start, end = Fraction(str(start_seconds)), Fraction(str(end_seconds))
        if start < 0 or end <= start or end > total:
            raise ValueError("CINE_SAMPLE_RANGE_INVALID")
    else:
        raise ValueError("CINE_SCOPE_INVALID")
    rows = _read(ledger)
    if not isinstance(rows, list) or any(not isinstance(r, dict) for r in rows):
        raise ValueError("CINE_CANONICAL_LEDGER_REQUIRED")
    ids = [r.get("shot_id") for r in rows]
    if any(not isinstance(i, str) or not i for i in ids) or len(set(ids)) != len(ids):
        raise ValueError("CINE_CANONICAL_SHOTS_AMBIGUOUS")
    receipts = await session_image_receipts(client, session_id)
    checks, verified = [], {}
    indexed_ranges, reviewed_ranges = [], []
    for row in rows:
        low, high = interval_seconds(row.get("interval", {}))
        if high > total:
            raise ValueError("CINE_SHOT_OUTSIDE_SOURCE")
        if low >= end or high <= start:
            continue
        indexed_ranges.append((max(low, start), min(high, end)))
        try:
            shot = reviewed_shots(workspace, session_id, [row["shot_id"]], receipts)[0]
            verified[row["shot_id"]] = shot
            reviewed_ranges.append((max(low, start), min(high, end)))
            checks.append({"shot_id": row["shot_id"], "status": "frame_review_verified"})
        except (OSError, ValueError, KeyError, TypeError) as exc:
            checks.append({"shot_id": row["shot_id"], "status": "unverified", "reason": str(exc)})
    imported = []
    if ledger_path is not None:
        if not isinstance(ledger_path, str) or not ledger_path.strip():
            raise ValueError("CINE_REPORT_LEDGER_PATH_INVALID")
        claimed = _read(_inside(workspace, workspace / ledger_path))
        if not isinstance(claimed, list):
            raise ValueError("CINE_REPORT_LEDGER_MUST_BE_ARRAY")
        seen = set()
        for row in claimed:
            if not isinstance(row, dict):
                raise ValueError("CINE_REPORT_ROW_INVALID")
            shot_id = row.get("canonical_shot_id") or row.get("shot_id")
            if not isinstance(shot_id, str):
                raise ValueError("CINE_REPORT_SHOT_ID_INVALID")
            actual = verified.get(shot_id)
            valid = (
                actual is not None
                and shot_id not in seen
                and row.get("observations") == actual.get("observations")
            )
            if valid:
                for field in ("source_id", "revision_id", "interval"):
                    if field in row and row[field] != actual.get(field):
                        valid = False
                for field, interval_field in (
                    ("source_in_pts", "in_pts"),
                    ("source_out_pts", "out_pts"),
                ):
                    if field in row and row[field] != actual["interval"][interval_field]:
                        valid = False
            imported.append(
                {
                    "shot_id": shot_id,
                    "claimed_status": row.get("review_status"),
                    "effective_status": "frame_review_verified"
                    if valid
                    else "historical_unverified",
                }
            )
            seen.add(shot_id)
    indexed = covered(indexed_ranges, start, end)
    frames = covered(reviewed_ranges, start, end)
    accepted = (
        indexed
        and frames
        and all(r["effective_status"] == "frame_review_verified" for r in imported)
    )
    return {
        "status": "accepted_visual_scope" if accepted else "rejected",
        "scope": scope,
        "source_id": source["source_id"],
        "revision_id": ledger.parent.name,
        "session_id": session_id,
        "source_duration_seconds": float(total),
        "requested_range_seconds": [float(start), float(end)],
        "canonical_shot_count": len(rows),
        "scoped_shot_count": len(checks),
        "verified_shot_count": len(verified),
        "receipt_count": len(receipts),
        "indexed_scope_complete": indexed,
        "frame_reviewed_shot_scope_complete": frames,
        "shot_checks": checks,
        "imported_rows": imported,
        "audio_review": "unverified",
        "motion_review": "unverified",
        "can_claim_full_audiovisual_analysis": False,
        "qualification": "Still-image review per shot, not continuous video/audio review "
        "or proof of semantic accuracy.",
    }
