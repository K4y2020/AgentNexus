"""Practical adaptation readiness: temporal coverage plus a usable story outline."""

import hashlib
import re
from fractions import Fraction

from omnigent.seedance.cine_contracts import _inside, _read, session_image_receipts
from omnigent.seedance.report_review import covered, interval_seconds


def nonempty(value):
    return isinstance(value, str) and bool(value.strip())


_DIALOGUE_QUOTE = re.compile(r"[“「『][^”」』]+[”」』]|(?<![A-Za-z0-9])[\"'][^\"'\r\n]{2,}[\"']")
_DIALOGUE_PROVENANCE = {"unverified", "asr", "trusted_subtitles", "visible_subtitles"}


def _contains_quoted_dialogue(value):
    if isinstance(value, str):
        return bool(_DIALOGUE_QUOTE.search(value))
    if isinstance(value, list):
        return any(_contains_quoted_dialogue(item) for item in value)
    if isinstance(value, dict):
        return any(_contains_quoted_dialogue(item) for item in value.values())
    return False


async def verify_story(client, session_id, workspace, ledger, source, story_path=None):
    project = ledger.parent.parent.parent
    plan = _read(_inside(project, ledger.parent / "story_plan.json"))
    draft = _read(
        _inside(
            workspace,
            workspace / story_path
            if story_path
            else project / "story" / f"{ledger.parent.name}.json",
        )
    )
    if not isinstance(plan, dict) or not isinstance(draft, dict):
        raise ValueError("CINE_STORY_OBJECT_REQUIRED")
    for obj in (plan, draft):
        if (
            obj.get("source_id") != source["source_id"]
            or obj.get("revision_id") != ledger.parent.name
        ):
            raise ValueError("CINE_STORY_REVISION_MISMATCH")
    batches = plan.get("batches")
    sections = draft.get("sections")
    if not isinstance(batches, list) or not batches or not isinstance(sections, list):
        raise ValueError("CINE_STORY_SECTIONS_REQUIRED")
    ids = [b.get("batch_id") for b in batches if isinstance(b, dict)]
    section_ids = [s.get("batch_id") for s in sections if isinstance(s, dict)]
    if (
        len(ids) != len(batches)
        or any(not nonempty(i) for i in ids)
        or len(set(ids)) != len(ids)
        or len(section_ids) != len(sections)
        or any(not nonempty(i) for i in section_ids)
        or len(set(section_ids)) != len(section_ids)
        or set(section_ids) - set(ids)
    ):
        raise ValueError("CINE_STORY_BATCH_IDS_INVALID")
    receipts = await session_image_receipts(client, session_id)
    evidence_rows = _read(_inside(project, ledger.parent / "evidence_index.json"))
    evidence = {e["evidence_id"]: e for e in evidence_rows}
    sections_by_id = {s["batch_id"]: s for s in sections}
    base = Fraction(source["time_base_num"], source["time_base_den"])
    offset = source.get("start_pts", 0) * base
    total = source["duration_pts"] * base
    windows, completed, issues, warnings, resolved = [], [], [], [], []
    if plan.get("cut_detection_warning"):
        warnings.append({"detail": plan["cut_detection_warning"]})
    cursor = Fraction(0)
    for batch in batches:
        low, high = interval_seconds(batch["interval"])
        low, high = low - offset, high - offset
        if low < 0 or high > total:
            raise ValueError("CINE_STORY_BATCH_OUTSIDE_SOURCE")
        if low != cursor:
            raise ValueError("CINE_STORY_BATCH_SEQUENCE_INVALID")
        cursor = high
        windows.append((low, high))
        row = sections_by_id.get(batch["batch_id"])
        if not row:
            issues.append({"batch_id": batch["batch_id"], "reason": "missing_section"})
            continue
        if (
            not isinstance(row.get("events"), list)
            or not row["events"]
            or not all(nonempty(e) for e in row["events"])
            or not nonempty(row.get("connection"))
        ):
            issues.append(
                {"batch_id": batch["batch_id"], "reason": "events_or_connection_missing"}
            )
            continue
        actual = []
        receipt_ids = row.get("image_receipt_ids", [])
        if not isinstance(receipt_ids, list) or not all(nonempty(r) for r in receipt_ids):
            raise ValueError("CINE_STORY_RECEIPTS_INVALID")
        for receipt_id in receipt_ids:
            receipt = receipts.get(receipt_id)
            if (
                not receipt
                or receipt.get("source_id") != source["source_id"]
                or receipt.get("revision_id") != ledger.parent.name
            ):
                continue
            ev = evidence.get(receipt.get("evidence_id"))
            if (
                not ev
                or ev["evidence_id"] not in batch["evidence_ids"]
                or ev.get("extraction_status") != "ok"
                or ev.get("source_id") != source["source_id"]
                or ev.get("revision_id") != ledger.parent.name
                or ev.get("source_interval") != receipt.get("source_interval")
            ):
                continue
            ev_low, ev_high = interval_seconds(ev["source_interval"])
            if ev_low - offset < low or ev_high - offset > high:
                continue
            path = _inside(ledger.parent, ledger.parent / ev["relative_path"])
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            if digest == ev.get("sha256") == receipt.get("sha256"):
                actual.append(receipt_id)
        if not actual:
            issues.append({"batch_id": batch["batch_id"], "reason": "no_current_batch_image"})
            continue
        uncertainty = row.get("uncertainties", [])
        if not isinstance(uncertainty, list) or not all(nonempty(u) for u in uncertainty):
            raise ValueError("CINE_STORY_UNCERTAINTIES_INVALID")
        warnings.extend({"batch_id": batch["batch_id"], "detail": u} for u in uncertainty)
        completed.append((low, high))
        resolved.append(
            {
                "batch_id": batch["batch_id"],
                "start_seconds": float(low),
                "end_seconds": float(high),
                "shot_ids": batch.get("shot_ids", []),
                "events": row["events"],
                "connection": row["connection"],
                "image_receipt_ids": actual,
                "uncertainties": uncertainty,
            }
        )
    summary = draft.get("summary", {})
    if not isinstance(summary, dict):
        raise ValueError("CINE_STORY_SUMMARY_REQUIRED")
    for field in ("premise", "conflict", "ending"):
        if not nonempty(summary.get(field)):
            issues.append({"reason": f"summary_{field}_missing"})
    if not isinstance(summary.get("turning_points"), list) or not all(
        nonempty(s) for s in summary["turning_points"]
    ):
        issues.append({"reason": "turning_points_list_required"})
    if (
        not isinstance(draft.get("characters"), list)
        or not draft["characters"]
        or not all(nonempty(c) for c in draft["characters"])
    ):
        issues.append({"reason": "characters_required"})
    provenance = draft.get("dialogue_provenance") or {"status": "unverified"}
    if not isinstance(provenance, dict) or provenance.get("status") not in _DIALOGUE_PROVENANCE:
        raise ValueError("CINE_DIALOGUE_PROVENANCE_INVALID")
    provenance_status = provenance["status"]
    if provenance_status in {"asr", "trusted_subtitles"}:
        source_path = provenance.get("source_path")
        if not nonempty(source_path):
            issues.append({"reason": "dialogue_provenance_source_missing"})
        else:
            try:
                transcript = _inside(workspace, workspace / source_path)
            except ValueError:
                issues.append({"reason": "dialogue_provenance_source_outside_workspace"})
            else:
                if not transcript.is_file():
                    issues.append({"reason": "dialogue_provenance_source_missing"})
    quoted_dialogue = _contains_quoted_dialogue(
        {"characters": draft.get("characters"), "summary": summary, "sections": sections}
    )
    if quoted_dialogue and provenance_status == "unverified":
        issues.append({"reason": "quoted_dialogue_without_provenance"})
    if provenance_status == "asr":
        warnings.append({"detail": "Dialogue is ASR-derived and remains qualified."})
    elif provenance_status == "visible_subtitles":
        warnings.append(
            {"detail": "Dialogue is limited to subtitles visible in inspected image receipts."}
        )
    elif provenance_status == "unverified":
        warnings.append({"detail": "Audio and dialogue remain unverified."})
    complete = covered(windows, Fraction(0), total) and covered(completed, Fraction(0), total)
    return {
        "status": "ready_for_adaptation" if complete and not issues else "needs_story_completion",
        "scope": "adaptation",
        "source_id": source["source_id"],
        "revision_id": ledger.parent.name,
        "source_duration_seconds": float(total),
        "batch_count": len(batches),
        "completed_batch_count": len(completed),
        "full_timeline_sampled": complete,
        "issues": issues,
        "warnings": warnings,
        "sections": resolved,
        "summary": summary,
        "accuracy_percent": None,
        "audio_review": provenance_status,
        "cut_precision_required": False,
        "can_claim_full_audiovisual_analysis": False,
        "qualification": "Adaptation working brief, not frame-perfect reconstruction. "
        "Temporal coverage does not prove every plot event was understood.",
    }
