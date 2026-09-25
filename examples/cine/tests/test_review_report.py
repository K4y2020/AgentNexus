import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "skills/film-analysis"))

from pipeline.render import refresh_report, render_report
from pipeline.review_data import build_review_data
from pipeline.schemas import EvidenceRecord, PtsInterval, SourceMediaRecord, SourceShot

# The identity source_fixture's SourceMediaRecord records (size + head/tail hash).
FINGERPRINT = {"size_bytes": 0, "sha256_head": "a" * 64, "sha256_tail": "b" * 64}


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def source_fixture(tmp_path):
    source = SourceMediaRecord(
        source_id="source",
        path=str(tmp_path / "inputs/source.mp4"),
        size_bytes=0,
        mtime_ns=0,
        sha256_head="a" * 64,
        sha256_tail="b" * 64,
        start_pts=100,
        duration_pts=600,
        time_base_num=1,
        time_base_den=10,
    )
    rev = tmp_path / "projects/film/revisions/r1"
    interval = PtsInterval(in_pts=100, out_pts=400, time_base_num=1, time_base_den=10)
    shots = [SourceShot(source_id="source", revision_id="r1", shot_id="shot1", interval=interval)]
    image = rev / "evidence/first.jpg"
    image.parent.mkdir(parents=True)
    image.write_bytes(b"test")
    evidence = [
        EvidenceRecord(
            source_id="source",
            revision_id="r1",
            evidence_id="ev1",
            kind="story_sample",
            relative_path="evidence/first.jpg",
            source_interval=interval,
        )
    ]
    write(
        rev / "story_plan.json",
        {
            "source_id": "source",
            "revision_id": "r1",
            "batches": [
                {"batch_id": "B001", "interval": interval.model_dump(), "evidence_ids": ["ev1"]}
            ],
        },
    )
    draft = {
        "source_id": "source",
        "revision_id": "r1",
        "summary": {"premise": "A story"},
        "sections": [{"batch_id": "B001", "events": ["A visible event"]}],
        "dialogue_provenance": {"status": "asr", "source_path": "inputs/transcript.txt"},
    }
    write(rev.parent.parent / "story/r1.json", draft)
    write(
        tmp_path / "inputs/transcript.json",
        {
            "kind": "qualified_asr_transcript",
            "source_media": "source.mp4",
            "source_fingerprint": FINGERPRINT,
            "segments": [{"start": 2, "end": 4, "text": "Hello"}],
        },
    )
    return rev, source, shots, evidence, draft


def test_independent_batch_shot_asr_identity_and_nonzero_pts(tmp_path):
    rev, source, shots, evidence, _ = source_fixture(tmp_path)
    data = build_review_data(rev, source, "r1", shots, evidence, tmp_path)
    assert not data["warnings"]
    by_type = {cue["type"]: cue for cue in data["cues"]}
    assert by_type["shot"]["sourceId"] == "shot1"
    assert by_type["story"]["sourceId"] == "B001"
    assert by_type["shot"]["start"] == by_type["story"]["start"] == 0
    assert by_type["shot"]["end"] == 30
    assert by_type["dialogue"]["start"] == 2
    assert by_type["dialogue"]["speaker"] == "说话人未标注"
    assert by_type["story"]["imageIds"] == ["ev1"]
    assert data["images"][0]["url"] == "../evidence/first.jpg"


def test_feedback_edits_refresh_from_native_data_without_promoting_status(tmp_path):
    rev, source, shots, evidence, draft = source_fixture(tmp_path)
    draft["sections"][0]["events"] = ["Corrected source event"]
    write(rev.parent.parent / "story/r1.json", draft)
    write(
        rev.parent.parent / "reviews/r1.json",
        [
            {
                "source_id": "source",
                "revision_id": "r1",
                "shot_id": "shot1",
                "observations": ["Corrected observation"],
                "status": "disputed",
            }
        ],
    )
    write(
        tmp_path / "inputs/transcript.json",
        {
            "kind": "qualified_asr_transcript",
            "source_media": "source.mp4",
            "source_fingerprint": FINGERPRINT,
            "segments": [{"start": 2, "end": 4, "text": "Hello", "speaker": "Mother"}],
        },
    )
    data = build_review_data(rev, source, "r1", shots, evidence, tmp_path)
    cues = {cue["type"]: cue for cue in data["cues"]}
    assert cues["story"]["text"] == "Corrected source event"
    assert cues["shot"]["text"] == "Corrected observation"
    assert cues["dialogue"]["speaker"] == "Mother"
    assert all("待核验" in cue["status"] for cue in cues.values())
    assert shots[0].observations == []


def test_stale_story_and_escaping_transcript_do_not_leak(tmp_path):
    rev, source, shots, evidence, draft = source_fixture(tmp_path)
    draft["revision_id"] = "old"
    write(rev.parent.parent / "story/r1.json", draft)
    data = build_review_data(rev, source, "r1", shots, evidence, tmp_path)
    assert data["summary"] == {}
    assert {cue["type"] for cue in data["cues"]} == {"shot"}
    assert data["warnings"]
    draft["revision_id"] = "r1"
    draft["dialogue_provenance"]["source_path"] = "../../other-topic.txt"
    write(rev.parent.parent / "story/r1.json", draft)
    data = build_review_data(rev, source, "r1", shots, evidence, tmp_path)
    assert not any(cue["type"] == "dialogue" for cue in data["cues"])
    assert data["warnings"]


def test_missing_screenshots_and_invalid_asr_do_not_fabricate_tracks(tmp_path):
    rev, source, shots, evidence, _ = source_fixture(tmp_path)
    (rev / "evidence/first.jpg").unlink()
    write(
        tmp_path / "inputs/transcript.json",
        {
            "kind": "qualified_asr_transcript",
            "source_media": "source.mp4",
            "source_fingerprint": {**FINGERPRINT, "sha256_tail": "c" * 64},
            "segments": [{"start": 2, "end": 4, "text": "wrong film"}],
        },
    )
    data = build_review_data(rev, source, "r1", shots, evidence, tmp_path)
    assert data["images"] == []
    assert len(data["warnings"]) == 2
    assert not any(cue["type"] in {"action", "dialogue"} for cue in data["cues"])


def test_standalone_html_escapes_source_text_and_keeps_js_executable(tmp_path):
    rev, source, shots, evidence, draft = source_fixture(tmp_path)
    attack = '</script><script>alert("unsafe")</script>'
    draft["summary"]["premise"] = attack
    draft["sections"][0]["events"] = [attack]
    write(rev.parent.parent / "story/r1.json", draft)
    path = render_report(rev, source, "r1", [], shots, evidence, None, workspace=tmp_path)
    html = path.read_text(encoding="utf-8")
    assert attack not in html
    assert "&lt;/script&gt;" in html
    payload = re.search(
        r'<script id="review-data" type="application/json">(.*?)</script>', html, re.S
    )
    data = json.loads(payload.group(1))
    assert data["summary"]["premise"] == attack
    assert "=>" in html and "requestAnimationFrame" in html
    assert "未执行" in html
    assert str(source.path) not in html
    assert "https://" not in html


def test_asr_tail_clips_display_without_changing_source_or_dropping_good_rows(tmp_path):
    rev, source, shots, evidence, _ = source_fixture(tmp_path)
    path = tmp_path / "inputs/transcript.json"
    write(
        path,
        {
            "kind": "qualified_asr_transcript",
            "source_media": "source.mp4",
            "source_fingerprint": FINGERPRINT,
            "segments": [
                {"start": 2, "end": 4, "text": "Hello"},
                {"start": 59, "end": 61, "text": "End"},
                {"start": 65, "end": 67, "text": "Outside"},
            ],
        },
    )
    before = path.read_bytes()
    data = build_review_data(rev, source, "r1", shots, evidence, tmp_path)
    dialogue = [cue for cue in data["cues"] if cue["type"] == "dialogue"]
    assert len(dialogue) == 2
    assert dialogue[1]["end"] == 60
    assert dialogue[1]["rawInterval"] == [59, 61]
    assert dialogue[1]["timingNote"]
    assert len(data["warnings"]) == 2
    assert path.read_bytes() == before


def test_refresh_uses_committed_revision_and_preserves_ledgers(tmp_path):
    rev, source, shots, evidence, _ = source_fixture(tmp_path)
    project = rev.parent.parent
    write(project / "project.json", {"current_revision": "r1"})
    write(project / "source.json", source.model_dump())
    write(rev / "cut_candidates.json", [])
    write(rev / "source_shots.json", [shot.model_dump() for shot in shots])
    write(rev / "evidence_index.json", [ev.model_dump() for ev in evidence])
    originals = {path: path.read_bytes() for path in project.rglob("*.json")}
    report = refresh_report(project, tmp_path)
    assert report == rev / "report/report.html"
    assert "A visible event" in report.read_text(encoding="utf-8")
    assert all(path.read_bytes() == before for path, before in originals.items())
    receipt = refresh_report(project, tmp_path, with_receipt=True)
    assert receipt["status"] == "report_refreshed"
    assert receipt["report_path"] == str(report)
    assert receipt["counts"] == {
        "story_batches": 1,
        "source_shots": 1,
        "dialogue_segments": 1,
        "evidence_images": 1,
    }
    assert receipt["report_bytes"] == report.stat().st_size
    assert receipt["media"]["status"] == "missing"
    assert receipt["media"]["playback_verified"] is False
    assert str(tmp_path / "inputs/transcript.json") in receipt["input_files"]
    assert "inputFiles" not in report.read_text(encoding="utf-8")


def test_refresh_cross_drive_receipt_requires_selection(tmp_path, monkeypatch):
    from pipeline import render

    rev, source, shots, evidence, _ = source_fixture(tmp_path)
    original = render.relative_media_url

    def cross_drive(path, report_dir):
        if Path(path) == Path(source.path):
            raise ValueError("different drives")
        return original(path, report_dir)

    monkeypatch.setattr(render, "relative_media_url", cross_drive)
    receipt = render_report(
        rev, source, "r1", [], shots, evidence, None, workspace=tmp_path, with_receipt=True
    )
    assert receipt["media"] == {"status": "select_local_file", "playback_verified": False}


def test_refresh_cli_missing_input_returns_actionable_receipt(tmp_path, monkeypatch, capsys):
    import pytest
    from pipeline import entry

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "media_project.py",
            "--refresh-report",
            "--output",
            str(tmp_path),
            "--workspace",
            str(tmp_path),
        ],
    )
    with pytest.raises(SystemExit) as exc:
        entry.main()
    assert exc.value.code == 2
    receipt = json.loads(capsys.readouterr().out)
    assert receipt["error_code"] == "REPORT_INPUT_MISSING"
    assert "project.json" in receipt["error"]
    assert receipt["next_action"]


def test_transcript_without_a_fingerprint_is_not_this_films_dialogue(tmp_path):
    """source.mp4 is every upload's name; the basename alone proves nothing."""
    rev, source, shots, evidence, _ = source_fixture(tmp_path)
    write(
        tmp_path / "inputs/transcript.json",
        {
            "kind": "qualified_asr_transcript",
            "source_media": "source.mp4",
            "segments": [{"start": 2, "end": 4, "text": "legacy"}],
        },
    )
    data = build_review_data(rev, source, "r1", shots, evidence, tmp_path)
    assert not any(cue["type"] == "dialogue" for cue in data["cues"])
    assert data["warnings"]


def test_matching_fingerprint_is_accepted_whatever_the_file_name(tmp_path):
    rev, source, shots, evidence, _ = source_fixture(tmp_path)
    write(
        tmp_path / "inputs/transcript.json",
        {
            "kind": "qualified_asr_transcript",
            "source_media": "renamed-upload.mkv",
            "source_fingerprint": FINGERPRINT,
            "segments": [{"start": 2, "end": 4, "text": "Hello"}],
        },
    )
    data = build_review_data(rev, source, "r1", shots, evidence, tmp_path)
    assert [cue["text"] for cue in data["cues"] if cue["type"] == "dialogue"] == ["Hello"]


def test_machine_asr_srt_is_not_discovered_as_a_subtitle(tmp_path):
    """Undeclared discovery must not show another video's ASR as subtitles."""
    rev, source, shots, evidence, draft = source_fixture(tmp_path)
    draft["dialogue_provenance"] = {"status": "unverified", "source_path": None}
    write(rev.parent.parent / "story/r1.json", draft)
    machine = "1\n00:00:02,000 --> 00:00:04,000\nwrong film\n"
    (tmp_path / "inputs/source-transcript.srt").write_text(machine, encoding="utf-8")
    (tmp_path / "inputs/source.srt").write_text(machine, encoding="utf-8")  # generated mirror
    data = build_review_data(rev, source, "r1", shots, evidence, tmp_path)
    assert not any(cue["type"] == "dialogue" for cue in data["cues"])

    # A subtitle someone supplied as inputs/source.srt is still found.
    (tmp_path / "inputs/source.srt").write_text(
        "1\n00:00:02,000 --> 00:00:04,000\nsupplied line\n", encoding="utf-8"
    )
    data = build_review_data(rev, source, "r1", shots, evidence, tmp_path)
    assert [cue["text"] for cue in data["cues"] if cue["type"] == "dialogue"] == ["supplied line"]
