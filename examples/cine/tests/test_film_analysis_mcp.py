"""The film-analysis MCP server: tool surface and path resolution."""

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
SERVER = ROOT / "tools" / "mcp" / "film_analysis_server.py"


def _load():
    spec = importlib.util.spec_from_file_location("film_analysis_server", SERVER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_server_exposes_the_pipeline_as_fixed_tools():
    """The agent should pick a tool, not assemble a shell command.

    Transcription was described in prose, so each run improvised its own
    invocation — which is how a session passed a bare name list as the
    attribution context.
    """
    module = _load()
    names = {tool.name for tool in module.mcp._tool_manager.list_tools()}
    assert names == {"film_analyze", "film_transcribe", "film_status", "film_review"}


def test_status_finds_a_transcript_two_levels_up(tmp_path):
    """A project lives at <workspace>/projects/<name>, so inputs are two up."""
    project = tmp_path / "projects" / "ep01"
    project.mkdir(parents=True)
    (project / "project.json").write_text("{}", encoding="utf-8")
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    (inputs / "source-transcript.json").write_text(
        json.dumps({"segments": [{"text": "x", "needs_review": True}]}), encoding="utf-8"
    )
    module = _load()
    status = module.film_status(str(project))
    assert status["artifacts"]["transcript"] is True
    assert status["lines_needing_review"] == 1


def test_status_reports_missing_project_without_raising(tmp_path):
    module = _load()
    status = module.film_status(str(tmp_path / "nope"))
    assert status["status"] == "missing"


@pytest.mark.skipif(not SERVER.is_file(), reason="server not installed")
def test_server_starts_and_lists_tools_over_stdio():
    """A server that cannot start is worse than no server: the agent falls back."""
    proc = subprocess.Popen(
        [sys.executable, str(SERVER)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        encoding="utf-8",
    )
    try:
        def send(obj):
            proc.stdin.write(json.dumps(obj) + "\n")
            proc.stdin.flush()

        send({
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                       "clientInfo": {"name": "test", "version": "1"}},
        })
        send({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})
        send({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        import time

        time.sleep(6)
        proc.stdin.close()
        payloads = []
        for line in proc.stdout.read().splitlines():
            try:
                payloads.append(json.loads(line))
            except ValueError:
                continue
        listed = next((p for p in payloads if p.get("id") == 2), None)
        assert listed is not None, "tools/list got no response"
        assert len(listed["result"]["tools"]) == 4
    finally:
        proc.kill()


def test_analyze_infers_workspace_above_projects(monkeypatch, tmp_path):
    module = _load()
    from pipeline import entry

    video = tmp_path / "inputs" / "source.mp4"
    video.parent.mkdir()
    video.write_bytes(b"test")
    output = tmp_path / "projects" / "ep01"
    output.parent.mkdir()
    captured = {}

    def index_media(_video, _output, *, workspace, resume):
        captured["workspace"] = workspace
        captured["resume"] = resume
        return {"analysis_status": "indexed_unreviewed"}

    monkeypatch.setattr(entry, "index_media", index_media)
    monkeypatch.setattr(
        module, "_transcribe", lambda _video, workspace: {"workspace": str(workspace)}
    )
    result = module.film_analyze(str(video), str(output))

    assert captured["workspace"] == tmp_path
    assert captured["resume"] is False             # a new output directory is created
    assert result["transcription"]["workspace"] == str(tmp_path)


# ---------------------------------------------------------------------------
# film_status: the committed revision's own artifacts, never "ready"
# ---------------------------------------------------------------------------


def _write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def _indexed(project, revision="r1", *, draft=None):
    """A project indexed at ``revision``; ``draft`` defaults to the indexer skeleton."""
    _write(project / "project.json", {"current_revision": revision})
    _write(project / "source.json", {"source_id": "src"})
    rev = project / "revisions" / revision
    (rev / "report").mkdir(parents=True, exist_ok=True)
    (rev / "report" / "report.html").write_text("<html/>", encoding="utf-8")
    _write(rev / "evidence_index.json", [])
    _write(
        rev / "story_plan.json",
        {"source_id": "src", "revision_id": revision, "batches": [{"batch_id": "B001"}]},
    )
    _write(
        project / "story" / f"{revision}.json",
        draft
        or {
            "source_id": "src",
            "revision_id": revision,
            "characters": [],
            "summary": {"premise": "", "conflict": "", "turning_points": [], "ending": ""},
            "sections": [{"batch_id": "B001", "events": [], "connection": ""}],
        },
    )


def _filled(revision="r1"):
    return {
        "source_id": "src",
        "revision_id": revision,
        "characters": ["ferryman"],
        "summary": {
            "premise": "fog",
            "conflict": "crossing",
            "turning_points": ["midstream"],
            "ending": "landing",
        },
        "sections": [{"batch_id": "B001", "events": ["the boat leaves"], "connection": "opening"}],
    }


def test_status_does_not_call_the_indexer_skeleton_ready(tmp_path):
    project = tmp_path / "projects" / "film"
    _indexed(project)
    status = _load().film_status(str(project))
    assert status["status"] == "story_incomplete"
    assert status["revision_id"] == "r1"
    assert "summary_premise_missing" in status["story_gaps"]
    assert "cine_verify_report" in status["next"]


def test_status_for_a_filled_draft_defers_readiness_to_verify_report(tmp_path):
    project = tmp_path / "projects" / "film"
    _indexed(project, draft=_filled())
    status = _load().film_status(str(project))
    assert status["status"] == "story_drafted"
    assert status["story_gaps"] == []
    assert "cine_verify_report" in status["next"]


def test_status_ignores_artifacts_and_reviews_of_an_older_revision(tmp_path):
    project = tmp_path / "projects" / "film"
    _indexed(project, "r1", draft=_filled("r1"))
    _write(
        project / "film_review.json", {"revision_id": "r1", "verdict": "ready", "total_score": 95}
    )
    # The committed revision moved on; only r1 files exist.
    _write(project / "project.json", {"current_revision": "r2"})
    status = _load().film_status(str(project))
    assert status["status"] == "incomplete"
    assert status["artifacts"]["report"] is False
    assert status["artifacts"]["story"] is False
    assert "quality_verdict" not in status
    assert status["stale_quality_review_revision"] == "r1"

    # A review of the current revision is shown.
    _indexed(project, "r2", draft=_filled("r2"))
    _write(
        project / "revisions" / "r2" / "film_review.json",
        {"revision_id": "r2", "verdict": "usable_with_risks", "total_score": 80},
    )
    status = _load().film_status(str(project))
    assert status["status"] == "story_drafted"
    assert status["quality_verdict"] == "usable_with_risks"
    assert "stale_quality_review_revision" not in status


# ---------------------------------------------------------------------------
# film_analyze: honest outer status; transcripts reused only for this video
# ---------------------------------------------------------------------------


def _video(workspace, content=b"video A"):
    video = workspace / "inputs" / "source.mp4"
    video.parent.mkdir(parents=True, exist_ok=True)
    video.write_bytes(content)
    return video


def _rows(attributed, *, flagged=False):
    row = {"start": 0.0, "end": 1.0, "text": "开船咯"}
    if attributed:
        row.update(speaker="船夫", delivery="spoken", attribution_confidence=0.9)
    if flagged:
        row.update(needs_review=True, review_reasons=["speaker_low_confidence"])
    return [row]


def _transcript(workspace, video, *, marker, rows):
    from pipeline.source_identity import media_fingerprint

    payload = {
        "kind": "qualified_asr_transcript",
        "source_media": "source.mp4",
        "source_fingerprint": media_fingerprint(video),
        "segments": rows,
    }
    if marker:
        payload["speaker_attribution"] = "jev"
    _write(workspace / "inputs" / "source-transcript.json", payload)


def _analyze(monkeypatch, workspace, video, *, index="indexed_unreviewed", transcribe=None):
    module = _load()
    from pipeline import entry

    monkeypatch.setattr(
        entry,
        "index_media",
        lambda *_a, **_k: {"analysis_status": index, "next_step": "Read the plan."},
    )
    calls = []

    def fake_transcribe(target, _workspace):
        calls.append(target)
        return transcribe() if transcribe else {"status": "transcribed"}

    monkeypatch.setattr(module, "_transcribe", fake_transcribe)
    project = workspace / "projects" / "film"
    return module.film_analyze(str(video), str(project), str(workspace)), calls


def _raise_runtime_error():
    raise RuntimeError("whisper missing")


def test_analyze_reports_a_blocked_index_without_running_asr(monkeypatch, tmp_path):
    video = _video(tmp_path)
    result, calls = _analyze(monkeypatch, tmp_path, video, index="blocked")
    assert result["status"] == "index_blocked"
    assert result["transcription"] == {"status": "not_run", "reason": "index_blocked"}
    assert calls == []


@pytest.mark.parametrize(
    ("outcome", "expected"),
    [
        (lambda: {"status": "attribution_unavailable"}, "attribution_unavailable"),
        (_raise_runtime_error, "transcription_failed"),
    ],
)
def test_analyze_reports_an_incomplete_transcription(monkeypatch, tmp_path, outcome, expected):
    video = _video(tmp_path)
    result, _ = _analyze(monkeypatch, tmp_path, video, transcribe=outcome)
    assert result["status"] == "transcription_incomplete"
    assert result["transcription"]["status"] == expected


def test_analyze_reuses_only_a_complete_transcript_of_this_video(monkeypatch, tmp_path):
    video = _video(tmp_path)
    _transcript(tmp_path, video, marker=True, rows=_rows(True))
    result, calls = _analyze(monkeypatch, tmp_path, video)
    assert result["status"] == "indexed"
    assert result["transcription"]["status"] == "reused"
    assert calls == []


def test_analyze_retranscribes_when_the_workspace_video_changed(monkeypatch, tmp_path):
    """Same file name, different content: the old transcript belongs to another film."""
    video = _video(tmp_path, b"video A")
    _transcript(tmp_path, video, marker=True, rows=_rows(True))
    video.write_bytes(b"video B, a different film")
    result, calls = _analyze(monkeypatch, tmp_path, video)
    assert len(calls) == 1
    assert result["transcription"]["status"] == "transcribed"


def test_analyze_retranscribes_a_transcript_without_a_fingerprint(monkeypatch, tmp_path):
    video = _video(tmp_path)
    _write(
        tmp_path / "inputs" / "source-transcript.json",
        {"source_media": "source.mp4", "speaker_attribution": "jev", "segments": _rows(True)},
    )
    _result, calls = _analyze(monkeypatch, tmp_path, video)
    assert len(calls) == 1


def _stub_attribution(monkeypatch, *, needs_review=False):
    from pipeline import attribute_speakers as attribution
    from pipeline import transcribe as transcribe_module

    def fake_attribute(rows, _cast, **_kwargs):
        return {
            "model": "jev-test",
            "attributions": [
                {
                    "speaker_name": "船夫",
                    "delivery": "spoken",
                    "confidence": 0.93,
                    "needs_review": needs_review,
                    "review_reasons": ["speaker_low_confidence"] if needs_review else [],
                }
                for _ in rows
            ],
        }

    def no_asr(*_args, **_kwargs):
        raise AssertionError("an attribution-only retry must not rerun ASR")

    monkeypatch.setattr(attribution, "attribute_speakers", fake_attribute)
    monkeypatch.setattr(attribution, "derived_cast", lambda _rows, **_kw: {"characters": []})
    monkeypatch.setattr(transcribe_module, "transcribe", no_asr)


@pytest.mark.parametrize("marker", [False, True], ids=["raw_asr", "marker_without_row_fields"])
def test_incomplete_attribution_is_retried_without_asr(monkeypatch, tmp_path, marker):
    video = _video(tmp_path)
    _transcript(tmp_path, video, marker=marker, rows=_rows(False, flagged=True))
    inputs = tmp_path / "inputs"
    (inputs / "source-transcript.srt").write_text("old generated", encoding="utf-8")
    (inputs / "source.srt").write_text("old generated", encoding="utf-8")
    _stub_attribution(monkeypatch)
    module = _load()

    result = module._transcription_for(video, tmp_path)

    assert result["status"] == "transcribed"
    assert result["asr"] == "reused"
    assert result["attribution"] == "retried"
    saved = json.loads((inputs / "source-transcript.json").read_text(encoding="utf-8"))
    (row,) = saved["segments"]
    assert row["speaker"] == "船夫"
    assert row["attribution_confidence"] == 0.93
    # The confident new judgment clears the flag an earlier run left behind.
    assert "needs_review" not in row
    assert "review_reasons" not in row
    assert module._attribution_complete(saved)
    srt = (inputs / "source-transcript.srt").read_text(encoding="utf-8")
    assert "[船夫] 开船咯" in srt
    assert "[船夫] 开船咯" in (inputs / "source-transcript.txt").read_text(encoding="utf-8")
    # The mirror still held the previous generated SRT, so it follows.
    assert (inputs / "source.srt").read_text(encoding="utf-8") == srt


def test_attribution_keeps_a_flag_while_the_new_judgment_is_unsure(monkeypatch, tmp_path):
    video = _video(tmp_path)
    _transcript(tmp_path, video, marker=False, rows=_rows(False))
    _stub_attribution(monkeypatch, needs_review=True)
    result = _load()._transcription_for(video, tmp_path)
    saved = json.loads((tmp_path / "inputs/source-transcript.json").read_text(encoding="utf-8"))
    assert saved["segments"][0]["needs_review"] is True
    assert saved["segments"][0]["review_reasons"] == ["speaker_low_confidence"]
    assert result["needs_review"] == 1


def test_attribution_markers_alone_do_not_count_as_complete():
    module = _load()
    rows = [{"start": 0.0, "end": 1.0, "text": "x", "speaker": "a", "delivery": "spoken"}]
    assert not module._attribution_complete({"speaker_attribution": "jev", "segments": rows})
    rows[0]["attribution_confidence"] = float("nan")
    assert not module._attribution_complete({"speaker_attribution": "jev", "segments": rows})
    rows[0]["attribution_confidence"] = 0.8
    assert module._attribution_complete({"speaker_attribution": "jev", "segments": rows})


# ---------------------------------------------------------------------------
# Transcript formats: JSON, TXT and SRT agree; a supplied subtitle survives
# ---------------------------------------------------------------------------


def _formats(tmp_path, mirror_text):
    inputs = tmp_path / "inputs"
    inputs.mkdir(parents=True)
    (inputs / "source-transcript.srt").write_text("STALE SRT", encoding="utf-8")
    if mirror_text is not None:
        (inputs / "source.srt").write_text(mirror_text, encoding="utf-8")
    rows = [{"start": 0.0, "end": 1.0, "speaker": "Alice", "text": "New line"}]
    mirror = _load()._write_transcript_formats({"segments": []}, rows, inputs)
    return inputs, mirror


def test_attribution_rewrites_the_srt_and_its_generated_mirror(tmp_path):
    inputs, mirror = _formats(tmp_path, "STALE SRT")
    srt = (inputs / "source-transcript.srt").read_text(encoding="utf-8")
    assert "[Alice] New line" in srt
    assert (inputs / "source.srt").read_text(encoding="utf-8") == srt
    assert mirror == "updated"
    saved = json.loads((inputs / "source-transcript.json").read_text(encoding="utf-8"))
    assert saved["segments"][0]["speaker"] == "Alice"
    assert "[Alice] New line" in (inputs / "source-transcript.txt").read_text(encoding="utf-8")


def test_attribution_preserves_a_supplied_subtitle(tmp_path):
    inputs, mirror = _formats(tmp_path, "1\n00:00:00,000 --> 00:00:01,000\nTrusted line\n")
    assert "[Alice] New line" in (inputs / "source-transcript.srt").read_text(encoding="utf-8")
    assert "Trusted line" in (inputs / "source.srt").read_text(encoding="utf-8")
    assert mirror == "preserved"


def test_attribution_creates_the_mirror_when_absent(tmp_path):
    inputs, mirror = _formats(tmp_path, None)
    assert mirror == "created"
    assert (inputs / "source.srt").read_text(encoding="utf-8") == (
        inputs / "source-transcript.srt"
    ).read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# film_analyze retries: resume the same project, refuse anything else
# (real entry.index_media and project.create_project; only the ffmpeg-bound
# run_pipeline is replaced)
# ---------------------------------------------------------------------------


def _fake_pipeline(monkeypatch, passes=(True,)):
    from pipeline import entry
    from pipeline.source_identity import media_fingerprint

    results = iter(passes)
    runs = []

    def run_pipeline(video, projects_dir, *, project_id, **_kwargs):
        passed = next(results)
        runs.append(project_id)
        project = Path(projects_dir) / project_id
        revision = f"r{len(runs)}"
        _write(
            project / "source.json",
            {
                "source_id": "src",
                **media_fingerprint(video),
                "start_pts": 0,
                "duration_pts": 60,
                "time_base_num": 1,
                "time_base_den": 1,
            },
        )
        rev = project / "revisions" / revision
        _write(
            rev / "story_plan.json",
            {
                "source_id": "src",
                "revision_id": revision,
                "batches": [{"batch_id": "B001", "start_seconds": 0, "end_seconds": 60}],
            },
        )
        _write(rev / "validation.json", {"passed": passed})
        (rev / "report").mkdir(parents=True, exist_ok=True)
        (rev / "report" / "report.html").write_text("<html/>", encoding="utf-8")
        if passed:
            manifest = json.loads((project / "project.json").read_text(encoding="utf-8"))
            manifest["current_revision"] = revision
            _write(project / "project.json", manifest)
        return {
            "revision_id": revision,
            "source_id": "src",
            "validation_passed": passed,
            "report_path": str(rev / "report" / "report.html"),
            "story_plan_path": str(rev / "story_plan.json"),
        }

    monkeypatch.setattr(entry, "run_pipeline", run_pipeline)
    return runs


def _with_transcriptions(monkeypatch, *outcomes):
    module = _load()
    pending = iter(outcomes)
    calls = []

    def fake_transcribe(target, _workspace):
        calls.append(target)
        return next(pending)

    monkeypatch.setattr(module, "_transcribe", fake_transcribe)
    return module, calls


def test_retry_after_failed_transcription_resumes_the_same_project(monkeypatch, tmp_path):
    video = _video(tmp_path)
    project = tmp_path / "projects" / "film"
    runs = _fake_pipeline(monkeypatch)
    module, calls = _with_transcriptions(
        monkeypatch, {"status": "attribution_unavailable"}, {"status": "transcribed"}
    )

    first = module.film_analyze(str(video), str(project), str(tmp_path))
    assert first["status"] == "transcription_incomplete"
    assert "film_analyze again" in first["next"]

    # The advised retry: no FileExistsError, the committed revision is reused.
    second = module.film_analyze(str(video), str(project), str(tmp_path))
    assert second["status"] == "indexed"
    assert second["result"]["reused_revision"] is True
    assert runs == ["film"]
    assert len(calls) == 2


def test_retry_after_a_blocked_index_resumes_instead_of_recreating(monkeypatch, tmp_path):
    video = _video(tmp_path)
    project = tmp_path / "projects" / "film"
    runs = _fake_pipeline(monkeypatch, passes=(False, True))
    module, calls = _with_transcriptions(monkeypatch, {"status": "transcribed"})

    first = module.film_analyze(str(video), str(project), str(tmp_path))
    assert first["status"] == "index_blocked"
    assert first["transcription"] == {"status": "not_run", "reason": "index_blocked"}
    assert calls == []

    second = module.film_analyze(str(video), str(project), str(tmp_path))
    assert second["status"] == "indexed"
    assert runs == ["film", "film"]
    assert len(calls) == 1


def test_retry_with_a_different_video_is_refused_without_asr(monkeypatch, tmp_path):
    video = _video(tmp_path, b"film A")
    project = tmp_path / "projects" / "film"
    runs = _fake_pipeline(monkeypatch)
    module, calls = _with_transcriptions(monkeypatch, {"status": "transcribed"})
    assert module.film_analyze(str(video), str(project), str(tmp_path))["status"] == "indexed"
    manifest_before = (project / "project.json").read_bytes()

    video.write_bytes(b"film B, same file name")
    result = module.film_analyze(str(video), str(project), str(tmp_path))
    assert result["status"] == "index_blocked"
    assert result["result"]["reason"] == "source_changed"
    assert result["transcription"]["status"] == "not_run"
    assert runs == ["film"]
    assert len(calls) == 1
    assert (project / "project.json").read_bytes() == manifest_before


@pytest.mark.parametrize(
    "contents",
    [
        {"notes.txt": "my own files"},
        {"project.json": json.dumps({"project_id": "someone-else"})},
    ],
    ids=["no_project_json", "other_project"],
)
def test_a_foreign_output_directory_is_refused(monkeypatch, tmp_path, contents):
    video = _video(tmp_path)
    project = tmp_path / "projects" / "film"
    project.mkdir(parents=True)
    for name, text in contents.items():
        (project / name).write_text(text, encoding="utf-8")
    runs = _fake_pipeline(monkeypatch)
    module, calls = _with_transcriptions(monkeypatch)

    result = module.film_analyze(str(video), str(project), str(tmp_path))
    assert result["status"] == "index_blocked"
    assert result["result"]["reason"] == "output_not_a_cine_project"
    assert result["transcription"]["status"] == "not_run"
    assert runs == [] and calls == []
    assert sorted(p.name for p in project.iterdir()) == sorted(contents)
