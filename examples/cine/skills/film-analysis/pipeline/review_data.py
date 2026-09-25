"""Read-only playback view of source records, story drafts and qualified ASR."""

import json
import math
from fractions import Fraction
from pathlib import Path
from urllib.parse import quote

from .source_identity import transcript_matches_source
from .subtitles import parse_subtitle_file, reconcile_dialogue


def relative_media_url(path, report_dir):
    import os

    return quote(Path(os.path.relpath(path, report_dir)).as_posix(), safe="/:")


def _workspace_file(workspace, value):
    if workspace is None or not isinstance(value, str) or not value.strip():
        raise ValueError("Workspace-relative dialogue file is required")
    root = Path(workspace).resolve()
    path = (root / value).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise ValueError("Dialogue file is missing or leaves the Topic workspace")
    if path.stat().st_size > 8 * 1024 * 1024:
        raise ValueError("Dialogue file is too large")
    return path


def _text_or_none(path):
    try:
        return path.read_text(encoding="utf-8-sig").replace("\r\n", "\n")
    except (OSError, UnicodeDecodeError):
        return None


def _discover_subtitle(workspace, source):
    """A subtitle file placed in inputs/, never the ASR tool's own output.

    ``source-transcript.*`` is machine ASR — shown only through its JSON record,
    whose source fingerprint is checked — and ``source.srt`` is just its
    generated mirror while the two are identical. Neither is a subtitle.
    """
    if workspace is None:
        return None
    root = Path(workspace).resolve()
    machine_srt = _text_or_none(root / "inputs" / "source-transcript.srt")
    stem = Path(source.path).stem
    for name in (stem, "source", "transcript"):
        if name == "source-transcript":
            continue
        for suffix in (".srt", ".vtt", ".ass", ".ssa"):
            path = root / "inputs" / f"{name}{suffix}"
            if path.is_file():
                if machine_srt is not None and _text_or_none(path) == machine_srt:
                    continue
                return path
    return None


def _asr_rows(workspace, path_value, source, revision_id, input_files):
    root = Path(workspace).resolve()
    declared_path = (root / path_value).resolve()
    if not declared_path.is_relative_to(root):
        raise ValueError("Dialogue file leaves the Topic workspace")
    transcript_path = (
        declared_path
        if declared_path.suffix.lower() == ".json"
        else declared_path.with_suffix(".json")
    )
    if not transcript_path.is_file():
        raise FileNotFoundError(transcript_path)
    transcript = read_json_inside(Path(workspace).resolve(), transcript_path)
    input_files.append(str(transcript_path.resolve()))
    # Identity, not file name: every upload may be called source.mp4. A
    # transcript that does not record the committed source's fingerprint is
    # not shown as this film's dialogue.
    if transcript.get("kind") != "qualified_asr_transcript" or not transcript_matches_source(
        transcript, source
    ):
        raise ValueError("Transcript source mismatch")
    for field, expected in (("source_id", source.source_id), ("revision_id", revision_id)):
        if field in transcript and transcript[field] != expected:
            raise ValueError("Transcript identity mismatch")
    return transcript["segments"]


def read_json_inside(root, path):
    path = path.resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError("Review input escapes its project/workspace")
    return json.loads(path.read_text(encoding="utf-8"))


def build_review_data(revision_dir, source, revision_id, shots, evidence, workspace=None):
    revision_dir = revision_dir.resolve()
    report_dir = revision_dir / "report"
    offset = source.start_pts * source.time_base
    duration = source.duration_seconds
    warnings, cues, images = [], [], []
    input_files = []
    summary, characters = {}, []

    def window(start, end, *, clip=False):
        if any(
            isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v)
            for v in (start, end)
        ):
            raise ValueError("Invalid review interval")
        if (
            start < 0
            or end <= start
            or (
                duration is not None and (start >= duration or (not clip and end > duration + 0.1))
            )
        ):
            raise ValueError("Review interval outside source")
        return [start, min(end, duration) if duration is not None else end]

    def pts_window(interval):
        base = Fraction(interval["time_base_num"], interval["time_base_den"])
        return window(
            float(interval["in_pts"] * base - offset), float(interval["out_pts"] * base - offset)
        )

    def matches(row):
        return row.get("source_id") == source.source_id and row.get("revision_id") == revision_id

    for ev in evidence:
        if (
            ev.source_id != source.source_id
            or ev.revision_id != revision_id
            or ev.extraction_status != "ok"
            or not ev.relative_path
            or not ev.source_interval
        ):
            continue
        if not (ev.kind.startswith("frame") or ev.kind == "story_sample"):
            continue
        path = (revision_dir / ev.relative_path.replace("\\", "/")).resolve()
        if not path.is_relative_to(revision_dir) or not path.is_file():
            warnings.append("部分截图缺失或路径无效。")
            continue
        try:
            start, end = pts_window(ev.source_interval.model_dump())
        except (ValueError, KeyError, ZeroDivisionError):
            warnings.append("部分截图时间无效。")
            continue
        images.append(
            {
                "id": ev.evidence_id,
                "start": start,
                "end": end,
                "url": relative_media_url(path, report_dir),
                "kind": ev.kind,
            }
        )
    images.sort(key=lambda row: row["start"])

    project = revision_dir.parent.parent
    reviews = {}
    review_path = project / "reviews" / f"{revision_id}.json"
    if review_path.exists():
        try:
            rows = read_json_inside(project, review_path)
            if not isinstance(rows, list):
                raise ValueError("Invalid source reviews")
            for row in rows:
                if (
                    not matches(row)
                    or row["shot_id"] in reviews
                    or not isinstance(row.get("observations"), list)
                    or not all(isinstance(text, str) for text in row["observations"])
                ):
                    raise ValueError("Invalid source review")
                reviews[row["shot_id"]] = row
            input_files.append(str(review_path))
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            reviews = {}
            warnings.append("逐镜复核文件无效或版本不匹配，未载入。")

    for index, shot in enumerate(shots, 1):
        if shot.source_id != source.source_id or shot.revision_id != revision_id:
            warnings.append("已跳过来源或版本不匹配的镜头。")
            continue
        start, end = pts_window(shot.interval.model_dump())
        cues.append(
            {
                "id": f"shot:{shot.shot_id}",
                "sourceId": shot.shot_id,
                "type": "shot",
                "start": start,
                "end": end,
                "title": f"镜头 {index:03d}",
                "text": "\n".join(
                    reviews.get(shot.shot_id, {}).get("observations", shot.observations)
                ),
                "status": "检测区间 · 待核验",
                "imageIds": [
                    image["id"]
                    for image in images
                    if image["start"] < end and image["end"] > start
                ],
            }
        )

    # Drafts belong to the source project, never a similarly named production folder.
    plan_path = revision_dir / "story_plan.json"
    draft = {}
    if plan_path.is_file():
        try:
            plan = read_json_inside(revision_dir, plan_path)
            input_files.append(str(plan_path))
            if not matches(plan):
                raise ValueError("Story plan revision mismatch")
            draft_path = project / "story" / f"{revision_id}.json"
            if draft_path.is_file():
                draft = read_json_inside(project, draft_path)
                input_files.append(str(draft_path))
                if not matches(draft):
                    raise ValueError("Story draft revision mismatch")
            sections = {s["batch_id"]: s for s in draft.get("sections", [])}
            summary = draft.get("summary") or {}
            characters = draft.get("characters") or []
            if not isinstance(summary, dict) or not isinstance(characters, list):
                raise ValueError("Invalid story summary")
            story_cues = []
            image_ids = {image["id"] for image in images}
            for batch in plan["batches"]:
                start, end = pts_window(batch["interval"])
                section = sections.get(batch["batch_id"], {})
                events = section.get("events") or []
                story_cues.append(
                    {
                        "id": f"story:{batch['batch_id']}",
                        "sourceId": batch["batch_id"],
                        "type": "story",
                        "start": start,
                        "end": end,
                        "title": str(events[0]) if events else batch["batch_id"],
                        "text": "\n".join(events),
                        "connection": section.get("connection", ""),
                        "uncertainties": section.get("uncertainties") or [],
                        "status": "剧情批次 · 待核验" if events else "剧情待填写",
                        "imageIds": [
                            value for value in batch.get("evidence_ids", []) if value in image_ids
                        ],
                    }
                )
            cues.extend(story_cues)
        except (OSError, ValueError, KeyError, TypeError, AttributeError, ZeroDivisionError):
            warnings.append("剧情文件无效或版本不匹配，未载入。")
            draft, summary, characters = {}, {}, []

    if workspace is None:
        workspace = next(
            (p for p in revision_dir.parents if p.parent.name.lower() == "topics"), None
        )
    provenance = draft.get("dialogue_provenance") or {}
    if not isinstance(provenance, dict):
        warnings.append("对白来源声明无效，未载入对白轨。")
        provenance = {}
    status = provenance.get("status")
    subtitle_path_value = provenance.get("subtitle_path")
    asr_path_value = provenance.get("asr_path")
    if status in ("asr", "subtitle_asr") and not asr_path_value:
        asr_path_value = provenance.get("source_path")
    if status in ("trusted_subtitles", "visible_subtitles") and not subtitle_path_value:
        subtitle_path_value = provenance.get("source_path")
    subtitle_path = None
    if not subtitle_path_value:
        subtitle_path = _discover_subtitle(workspace, source)
        if subtitle_path is not None:
            subtitle_path_value = str(subtitle_path.relative_to(Path(workspace).resolve()))
    if (
        subtitle_path_value
        or asr_path_value
        or status in ("asr", "trusted_subtitles", "visible_subtitles", "subtitle_asr")
    ):
        try:
            if workspace is None:
                raise ValueError("Workspace required for dialogue files")
            workspace = Path(workspace).resolve()
            if not project.is_relative_to(workspace):
                raise ValueError("Project outside workspace")
            subtitles = []
            if subtitle_path_value:
                subtitle_path = _workspace_file(workspace, subtitle_path_value)
                subtitles = parse_subtitle_file(subtitle_path)
                input_files.append(str(subtitle_path.resolve()))
            asr = []
            if asr_path_value:
                asr = _asr_rows(workspace, asr_path_value, source, revision_id, input_files)
            if subtitles:
                dialogue, dialogue_warnings = reconcile_dialogue(subtitles, asr, duration=duration)
                warnings.extend(dialogue_warnings)
            elif asr:
                dialogue = []
                for index, row in enumerate(asr, 1):
                    try:
                        start, end = window(row["start"], row["end"], clip=True)
                    except (ValueError, KeyError, TypeError):
                        warnings.append("部分 ASR 时间记录无效，已跳过。")
                        continue
                    if not isinstance(row["text"], str) or not row["text"].strip():
                        continue
                    timing_note = ""
                    if end != row["end"]:
                        timing_note = "ASR 结束时间超出原片，显示范围已截到片尾。"
                        warnings.append(timing_note)
                    dialogue.append(
                        {
                            "id": f"dialogue:{index}",
                            "sourceId": f"ASR {index:03d}",
                            "type": "dialogue",
                            "start": start,
                            "end": end,
                            "title": row["text"],
                            "text": row["text"],
                            "asrText": row["text"],
                            "provenance": "asr",
                            "conflicts": [],
                            "rawInterval": [row["start"], row["end"]],
                            "timingNote": timing_note,
                            "status": "ASR · 待核验",
                            "speaker": row.get("speaker")
                            if isinstance(row.get("speaker"), str) and row["speaker"].strip()
                            else "说话人未标注",
                        }
                    )
            else:
                if status in ("trusted_subtitles", "visible_subtitles", "subtitle_asr"):
                    warnings.append("已声明字幕来源，但未找到可解析的字幕文件，未载入字幕对白轨。")
                dialogue = []
            for row in dialogue:
                row["imageIds"] = [
                    image["id"]
                    for image in images
                    if image["start"] < row["end"] and image["end"] > row["start"]
                ]
            cues.extend(dialogue)
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            warnings.append("声明的字幕或 ASR 时间记录不可用或来源不匹配，未载入对白轨。")

    cues.sort(key=lambda row: (row["start"], row["end"], row["id"]))
    return {
        "duration": duration or max((c["end"] for c in cues), default=0),
        "sourceId": source.source_id,
        "revisionId": revision_id,
        "cues": cues,
        "images": images,
        "summary": summary,
        "characters": characters,
        "warnings": list(dict.fromkeys(warnings)),
        "inputFiles": input_files,
    }
