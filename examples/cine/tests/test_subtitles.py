import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "skills/film-analysis"))

from pipeline.review_data import build_review_data
from pipeline.schemas import EvidenceRecord, PtsInterval, SourceMediaRecord, SourceShot
from pipeline.subtitles import parse_subtitle_text, reconcile_dialogue


def test_parse_srt_vtt_and_ass():
    srt = parse_subtitle_text("1\n00:00:01,200 --> 00:00:02,500\n<i>你好</i>\n", ".srt")
    vtt = parse_subtitle_text("WEBVTT\n\n00:01.000 --> 00:02.000\nHello\n", ".vtt")
    ass = parse_subtitle_text("Dialogue: 0,0:00:03.00,0:00:04.50,Default,你好\\N世界\n", ".ass")
    assert srt[0]["start"] == 1.2 and srt[0]["text"] == "你好"
    assert vtt[0]["end"] == 2
    assert ass[0]["text"] == "你好\n世界"


def test_reconcile_keeps_subtitle_text_and_surfaces_asr_conflict():
    cues, warnings = reconcile_dialogue(
        [{"id": "SRT 001", "start": 1, "end": 2, "text": "字幕原句"}],
        [{"start": 1.1, "end": 1.9, "text": "ASR另一句", "speaker": "A"}],
    )
    assert len(cues) == 1
    assert cues[0]["text"] == "字幕原句"
    assert cues[0]["subtitleText"] == "字幕原句"
    assert cues[0]["asrText"] == "ASR另一句"
    assert cues[0]["provenance"] == "subtitle+asr"
    assert cues[0]["conflicts"]
    assert warnings


def test_review_data_loads_declared_subtitle_and_asr_together(tmp_path):
    source = SourceMediaRecord(
        source_id="source",
        path=str(tmp_path / "source.mp4"),
        size_bytes=0,
        mtime_ns=0,
        sha256_head="a" * 64,
        sha256_tail="b" * 64,
        start_pts=0,
        duration_pts=100,
        time_base_num=1,
        time_base_den=1,
    )
    revision = tmp_path / "projects/film/revisions/r1"
    revision.mkdir(parents=True)
    interval = PtsInterval(in_pts=0, out_pts=10)
    shots = [SourceShot(source_id="source", revision_id="r1", shot_id="shot1", interval=interval)]
    evidence = [
        EvidenceRecord(
            source_id="source",
            revision_id="r1",
            evidence_id="ev1",
            kind="story_sample",
            relative_path=None,
            source_interval=interval,
        )
    ]
    project = revision.parent.parent
    (project / "story").mkdir(parents=True)
    (tmp_path / "inputs").mkdir()
    (tmp_path / "inputs/source.srt").write_text(
        "1\n00:00:01,000 --> 00:00:02,000\n字幕原句\n", encoding="utf-8"
    )
    (tmp_path / "inputs/transcript.json").write_text(
        json.dumps(
            {
                "kind": "qualified_asr_transcript",
                "source_media": "source.mp4",
                "segments": [{"start": 1.1, "end": 1.9, "text": "ASR另一句"}],
            }
        ),
        encoding="utf-8",
    )
    (revision / "story_plan.json").write_text(
        json.dumps({"source_id": "source", "revision_id": "r1", "batches": []}), encoding="utf-8"
    )
    (project / "story/r1.json").write_text(
        json.dumps(
            {
                "source_id": "source",
                "revision_id": "r1",
                "summary": {},
                "characters": [],
                "sections": [],
                "dialogue_provenance": {
                    "status": "subtitle_asr",
                    "subtitle_path": "inputs/source.srt",
                    "asr_path": "inputs/transcript.txt",
                },
            }
        ),
        encoding="utf-8",
    )
    data = build_review_data(revision, source, "r1", shots, evidence, tmp_path)
    dialogue = [cue for cue in data["cues"] if cue["type"] == "dialogue"]
    assert dialogue[0]["text"] == "字幕原句"
    assert dialogue[0]["asrText"] == "ASR另一句"
    assert dialogue[0]["conflicts"]
    assert str(tmp_path / "inputs/source.srt") in data["inputFiles"]
