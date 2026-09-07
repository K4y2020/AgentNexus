"""Fail-closed resolution of session-owned Cine source-shot contracts."""

import base64
import hashlib
import json
import re
from pathlib import Path


def _read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def _inside(root, path):
    path = path.resolve(strict=True)
    if not path.is_relative_to(root.resolve()):
        raise ValueError("CINE_PATH_ESCAPE: project/evidence path leaves its workspace")
    return path


def current_ledger(workspace, session_id):
    if not workspace or not session_id or not re.fullmatch(r"[A-Za-z0-9_-]+", session_id):
        raise ValueError("CINE_BINDING_REQUIRED: current session and workspace are required")
    root = Path(workspace).resolve()
    binding = _read(_inside(root, root / ".cine" / "sessions" / f"{session_id}.json"))
    if binding.get("session_id") != session_id:
        raise ValueError("CINE_OWNER_MISMATCH")
    project = _inside(root, Path(binding["project_path"]))
    manifest = _read(_inside(project, project / "project.json"))
    revision = manifest.get("current_revision")
    if not isinstance(revision, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", revision):
        raise ValueError("CINE_REVISION_REQUIRED: no committed current revision")
    return _inside(project, project / "revisions" / revision / "source_shots.json")


def reviewed_shots(workspace, session_id, shot_ids, receipts):
    ledger = current_ledger(workspace, session_id)
    revision_dir = ledger.parent
    source = _read(_inside(revision_dir.parent.parent, revision_dir.parent.parent / "source.json"))
    rows = _read(ledger)
    evidence = _read(_inside(revision_dir, revision_dir / "evidence_index.json"))
    reviews = _read(
        _inside(
            revision_dir.parent.parent,
            revision_dir.parent.parent / "reviews" / f"{revision_dir.name}.json",
        )
    )
    if (
        not isinstance(rows, list)
        or not isinstance(evidence, list)
        or not isinstance(reviews, list)
    ):
        raise ValueError("CINE_SCHEMA_MISMATCH: migrate legacy ledgers before dispatch")
    matched = []
    for shot_id in shot_ids:
        shots = [s for s in rows if s.get("shot_id") == shot_id]
        if len(shots) != 1:
            raise ValueError(f"CINE_SHOT_MISSING_OR_AMBIGUOUS: {shot_id}")
        shot = shots[0]
        review_matches = [r for r in reviews if r.get("shot_id") == shot_id]
        if len(review_matches) != 1:
            raise ValueError(f"CINE_VISUAL_REVIEW_REQUIRED: {shot_id}")
        review = review_matches[0]
        if (
            review.get("source_id") != source["source_id"]
            or review.get("revision_id") != revision_dir.name
        ):
            raise ValueError("CINE_REVIEW_REVISION_MISMATCH")
        shot = {
            **shot,
            "observations": review.get("observations"),
            "visual_review_receipt_ids": review.get("image_receipt_ids", []),
            "verification": {**shot.get("verification", {}), "visual": review.get("status")},
        }
        if (
            shot.get("source_id") != source["source_id"]
            or shot.get("revision_id") != revision_dir.name
        ):
            raise ValueError("CINE_SOURCE_REVISION_MISMATCH")
        interval = shot.get("interval", {})
        if not all(
            isinstance(interval.get(k), int)
            for k in ("in_pts", "out_pts", "time_base_num", "time_base_den")
        ):
            raise ValueError("CINE_INTERVAL_INVALID")
        if (
            interval["out_pts"] <= interval["in_pts"]
            or min(interval["time_base_num"], interval["time_base_den"]) <= 0
        ):
            raise ValueError("CINE_INTERVAL_INVALID")
        if shot.get("verification", {}).get("visual") != "model_reviewed":
            raise ValueError(f"CINE_VISUAL_REVIEW_REQUIRED: {shot_id}")
        if not isinstance(shot.get("observations"), list) or not shot["observations"]:
            raise ValueError("CINE_OBSERVATIONS_REQUIRED")
        ids = shot.get("visual_review_receipt_ids", [])
        if not ids:
            raise ValueError(f"CINE_IMAGE_RECEIPT_REQUIRED: {shot_id}")
        for receipt_id in ids:
            receipt = receipts.get(receipt_id)
            if (
                not receipt
                or receipt.get("source_id") != source["source_id"]
                or receipt.get("revision_id") != revision_dir.name
            ):
                raise ValueError("CINE_RECEIPT_NOT_IN_CURRENT_SESSION")
            evs = [e for e in evidence if e.get("evidence_id") == receipt.get("evidence_id")]
            if len(evs) != 1 or evs[0]["evidence_id"] not in shot.get("evidence_ids", []):
                raise ValueError("CINE_RECEIPT_EVIDENCE_MISMATCH")
            ev = evs[0]
            ev_interval = ev.get("source_interval", {})
            if (
                ev_interval.get("time_base_num") != interval["time_base_num"]
                or ev_interval.get("time_base_den") != interval["time_base_den"]
                or ev_interval.get("in_pts", interval["out_pts"]) >= interval["out_pts"]
                or ev_interval.get("out_pts", interval["in_pts"]) <= interval["in_pts"]
            ):
                raise ValueError("CINE_EVIDENCE_OUTSIDE_SHOT")
            if receipt.get("source_interval") != ev.get("source_interval"):
                raise ValueError("CINE_RECEIPT_TIME_MISMATCH")
            if (
                ev.get("source_id") != source["source_id"]
                or ev.get("revision_id") != revision_dir.name
                or ev.get("extraction_status") != "ok"
            ):
                raise ValueError("CINE_EVIDENCE_INVALID")
            image = _inside(revision_dir, revision_dir / ev["relative_path"])
            if image.stat().st_size > 5 * 1024 * 1024:
                raise ValueError("CINE_EVIDENCE_TOO_LARGE")
            digest = hashlib.sha256(image.read_bytes()).hexdigest()
            if digest != ev.get("sha256") or digest != receipt.get("sha256"):
                raise ValueError("CINE_EVIDENCE_CHANGED")
        matched.append(shot)
    return matched


async def session_image_receipts(client, session_id):
    """Read recorded image tool results, never a model-authored receipt file."""
    receipts = {}
    pending = {}
    ambiguous = set()
    outputs = []
    after = None
    for _ in range(20):
        params = {"limit": 100, "order": "asc"}
        if after:
            params["after"] = after
        response = await client.get(f"/v1/sessions/{session_id}/items", params=params)
        response.raise_for_status()
        page = response.json()
        for item in page.get("data", []):
            if not isinstance(item.get("call_id"), str) or not item["call_id"]:
                continue
            key = (item.get("response_id"), item.get("call_id"))
            if item.get("type") == "function_call":
                queue = pending.setdefault(key, [])
                if queue:
                    ambiguous.add(key)
                queue.append(item.get("name"))
            if item.get("type") != "function_call_output":
                continue
            queue = pending.get(key, [])
            if not queue:
                continue
            name = queue.pop(0)
            if key not in ambiguous and name == "sys_os_view_image":
                outputs.append(item)
            if not queue:
                pending.pop(key, None)
                ambiguous.discard(key)
        if not page.get("has_more"):
            break
        after = page.get("last_id")
        if not after:
            raise ValueError("CINE_RECEIPT_HISTORY_INCOMPLETE")
    else:
        raise ValueError("CINE_RECEIPT_HISTORY_LIMIT: evidence lookup incomplete")
    for item in outputs:
        try:
            output = json.loads(item.get("output", ""))
            meta = output.get("metadata", {})
            image = output.get("image", {})
            if (
                meta.get("receipt_id")
                and image.get("type") == "image"
                and image.get("source", {}).get("data")
            ):
                decoded = base64.b64decode(image["source"]["data"], validate=True)
                if hashlib.sha256(decoded).hexdigest() != meta.get("sha256"):
                    continue
                receipts[meta["receipt_id"]] = {
                    **meta,
                    "tool_result_item_id": item["id"],
                    "tool_call_id": item["call_id"],
                }
        except (ValueError, TypeError, AttributeError):
            continue
    return receipts
