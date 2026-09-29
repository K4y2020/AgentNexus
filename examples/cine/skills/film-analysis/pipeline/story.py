"""Deterministic whole-scope sampling for adaptation-oriented story reading."""

import subprocess
from fractions import Fraction

from .evidence import _extract_frame, _ffmpeg_available, _ffmpeg_version, _sha256_file
from .schemas import EvidenceRecord, PtsInterval


def extract_story_plan(
    media, source, revision_id, revision_dir, start, end, batch_seconds=30, opening_teaser=None
):
    base = source.time_base
    step = max(1, int(Fraction(batch_seconds) / base))
    if end <= start or batch_seconds <= 0:
        raise ValueError("story sampling requires a positive scope and batch size")
    # A film that opens with a teaser of its own later footage: the first still must not
    # be a frame of it, or the story reads a scene's outcome as the opening.
    teaser = opening_teaser if (opening_teaser or {}).get("status") == "found" else None
    teaser_pts = int(Fraction(str(teaser["end_seconds"])) / base) if teaser else 0
    folder = revision_dir / "evidence"
    folder.mkdir(exist_ok=True)
    records, batches = [], []
    for i, low in enumerate(range(start, end, step), 1):
        high = min(end, low + step)
        batch_id = f"B{i:03d}"
        # Three representative seeks per temporal batch, independent of cut density.
        points = sorted(
            {low + (high - low) // 10, low + (high - low) // 2, low + (high - low) * 9 // 10}
        )
        if i == 1:
            points[0] = min(low + teaser_pts, high - 1)
            points = sorted(set(points))
        ids = []
        for j, pts in enumerate(points):
            pts = min(pts, high - 1)
            path = folder / f"{batch_id}_{j}.jpg"
            ev = EvidenceRecord(
                source_id=source.source_id,
                revision_id=revision_id,
                kind="story_sample",
                source_interval=PtsInterval(
                    in_pts=pts,
                    out_pts=pts + 1,
                    time_base_num=source.time_base_num,
                    time_base_den=source.time_base_den,
                ),
                extraction_status="unavailable",
                tool_version=_ffmpeg_version(),
            )
            if _ffmpeg_available():
                try:
                    ev.ffmpeg_argv = _extract_frame(
                        media, float((pts - source.start_pts) * base), path
                    )
                    ev.relative_path = str(path.relative_to(revision_dir))
                    ev.sha256 = _sha256_file(path)
                    ev.extraction_status = "ok"
                except (OSError, RuntimeError, subprocess.SubprocessError):
                    ev.extraction_status = "failed"
            ids.append(ev.evidence_id)
            records.append(ev)
        batches.append(
            {
                "batch_id": batch_id,
                "interval": {
                    "in_pts": low,
                    "out_pts": high,
                    "time_base_num": source.time_base_num,
                    "time_base_den": source.time_base_den,
                },
                "start_seconds": float((low - source.start_pts) * base),
                "end_seconds": float((high - source.start_pts) * base),
                "evidence_ids": ids,
            }
        )
    plan = {
        "schema_version": 1,
        "profile": "adaptation",
        "source_id": source.source_id,
        "revision_id": revision_id,
        "batch_seconds": batch_seconds,
        "batches": batches,
        "sampling_note": "Representative stills at requested seeks, "
        "not exact frame-PTS or continuous viewing.",
        "accuracy_percent": None,
    }
    if opening_teaser is not None:
        plan["opening_teaser"] = opening_teaser
    if teaser:
        plan["sampling_note"] += (
            f" The film opens with a teaser of its own later footage (0-{teaser['end_seconds']} s);"
            " the first still is taken after it."
        )
    return plan, records
