"""The film-analysis MCP server: tool surface and path resolution."""

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
SERVER = ROOT / "tools" / "mcp" / "film_analysis_server.py"


@pytest.fixture(autouse=True)
def _no_vision_model(monkeypatch):
    """Reading line performance calls a vision model; tests that need it stub it themselves."""
    from pipeline import line_performance, shot_notes

    monkeypatch.setattr(
        shot_notes,
        "read_shot_notes",
        lambda _media, _project, **_k: {
            "status": "read",
            "model": "test",
            "revision_id": "r1",
            "locations": [],
            "errors": [],
        },
    )

    monkeypatch.setattr(
        line_performance,
        "read_line_performance",
        lambda _media, rows, **_k: {"status": "read", "lines": len(rows), "read": len(rows)},
    )


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
    assert names == {
        "film_analyze",
        "film_refresh_report",
        "film_transcribe",
        "film_status",
        "film_review",
    }


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


def test_status_reports_current_location_table_and_rejects_stale_one(tmp_path):
    project = tmp_path / "projects" / "film"
    _indexed(project)
    path = project / "revisions/r1/shot_notes.json"
    notes = {
        "schema_version": 1,
        "source_id": "src",
        "revision_id": "r1",
        "model": "vision",
        "status": "partial",
        "locations": [{"id": "L1"}],
        "errors": ["batch failed"],
    }
    _write(path, notes)
    result = _load().film_status(str(project))
    assert result["shot_notes"] == {
        "status": "partial",
        "locations": 1,
        "model": "vision",
        "people": 0,
        "errors": ["batch failed"],
    }
    _write(path, {**notes, "revision_id": "old"})
    assert _load().film_status(str(project))["shot_notes"]["status"] == "missing"


def test_location_request_failure_does_not_block_transcription(monkeypatch, tmp_path):
    from pipeline import shot_notes

    def fail(*_a, **_k):
        raise OSError("vision request failed")

    monkeypatch.setattr(shot_notes, "read_shot_notes", fail)
    result, calls = _analyze(monkeypatch, tmp_path, _video(tmp_path))
    # Transcription still runs, but an analysis with unread shots is not complete.
    assert calls and result["status"] == "notes_incomplete"
    assert "unfinished shots" in result["next"]
    assert result["shot_notes"]["status"] == "unavailable"
    assert "vision request failed" in result["shot_notes"]["errors"][0]


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

        send(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "test", "version": "1"},
                },
            }
        )
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
        assert len(listed["result"]["tools"]) == 5
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

    def index_media(_video, _output, *, workspace, resume, **_scope):
        captured["workspace"] = workspace
        captured["resume"] = resume
        return {"analysis_status": "indexed_unreviewed"}

    monkeypatch.setattr(entry, "index_media", index_media)
    monkeypatch.setattr(
        module, "_transcribe", lambda _video, workspace, _plan=None: {"workspace": str(workspace)}
    )
    result = module.film_analyze(str(video), str(output))

    assert captured["workspace"] == tmp_path
    assert captured["resume"] is False  # a new output directory is created
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

    def fake_transcribe(target, _workspace, _plan=None):
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

    def fake_transcribe(target, _workspace, _plan=None):
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


def test_server_launch_pins_its_own_dependencies():
    """The server must not depend on the host python or on an unpinned mcp major.

    The host python has no faster-whisper, scenedetect's opencv extra is gone,
    and mcp 2.x removed FastMCP; each of these kept the tool from starting.
    """
    import yaml

    config = yaml.safe_load(
        (Path(__file__).parents[1] / "tools/mcp/film-analysis.yaml").read_text(encoding="utf-8")
    )
    assert config["command"] == "uv"
    requirements = config["args"][config["args"].index("--with") :]
    assert "mcp>=1.2,<2" in requirements
    for needed in ("faster-whisper", "opencv-python", "rapidocr-onnxruntime", "pyyaml"):
        assert needed in requirements
    # rapidocr needs opencv-python; the headless build would install a second cv2.
    assert "opencv-python-headless" not in requirements
    assert config["args"][-1] == "tools/mcp/film_analysis_server.py"


def test_server_receives_the_gpu_switches_by_name_only():
    """The switches the pipeline reads reach the server by name; the YAML holds no values.

    Without them a host's CINE_GPU=off never reached the tool. A non-empty `env`
    would hand the server the runner's whole environment, and any value here
    would be one machine's path.
    """
    import yaml
    from pipeline import gpu_runtime

    config = yaml.safe_load(
        (Path(__file__).parents[1] / "tools/mcp/film-analysis.yaml").read_text(encoding="utf-8")
    )
    assert "env" not in config
    assert set(config["env_passthrough"]) == {
        gpu_runtime.MODE_VAR,
        gpu_runtime.HOME_VAR,
        "HF_HOME",
        "TORCH_HOME",
    }


def _stub_captions(monkeypatch, rows, status="read"):
    from pipeline import burned_subtitles

    monkeypatch.setattr(
        burned_subtitles,
        "read_burned_subtitles",
        lambda *_a, **_k: {"status": status, "engine": "rapidocr", "rows": rows},
    )


def test_a_transcript_made_before_captions_were_read_gains_them_without_asr(monkeypatch, tmp_path):
    video = _video(tmp_path)
    _transcript(tmp_path, video, marker=True, rows=_rows(True))
    _stub_captions(monkeypatch, [{"start": 5.0, "end": 6.0, "text": "少东家回来了"}])
    _stub_attribution(monkeypatch)
    module = _load()

    result = module._transcription_for(video, tmp_path)

    assert result["status"] == "transcribed"
    assert result["asr"] == "reused"
    assert result["captions"]["added"] == 1
    saved = json.loads((tmp_path / "inputs/source-transcript.json").read_text(encoding="utf-8"))
    assert [r["text"] for r in saved["segments"]] == ["开船咯", "少东家回来了"]
    assert saved["burned_subtitles"]["status"] == "read"
    assert module._attribution_complete(saved)


def test_captions_are_read_once_and_then_the_transcript_is_reused(monkeypatch, tmp_path):
    video = _video(tmp_path)
    _transcript(tmp_path, video, marker=True, rows=_rows(True))
    _stub_captions(monkeypatch, [])
    module = _load()

    assert module._transcription_for(video, tmp_path)["status"] == "reused"

    from pipeline import burned_subtitles

    def not_again(*_a, **_k):
        raise AssertionError("captions were already read for this transcript")

    monkeypatch.setattr(burned_subtitles, "read_burned_subtitles", not_again)
    assert module._transcription_for(video, tmp_path)["status"] == "reused"


def test_a_silent_film_with_captions_is_no_longer_dialogue_free(monkeypatch, tmp_path):
    video = _video(tmp_path)
    _transcript(tmp_path, video, marker=False, rows=[])
    _stub_captions(monkeypatch, [{"start": 1.0, "end": 2.0, "text": "抓紧绳子"}])
    _stub_attribution(monkeypatch)

    result = _load()._transcription_for(video, tmp_path)

    assert result["status"] == "transcribed"
    saved = json.loads((tmp_path / "inputs/source-transcript.json").read_text(encoding="utf-8"))
    assert [r["text"] for r in saved["segments"]] == ["抓紧绳子"]


def test_an_unavailable_caption_reader_leaves_the_transcript_and_tries_again_later(
    monkeypatch, tmp_path
):
    video = _video(tmp_path)
    _transcript(tmp_path, video, marker=True, rows=_rows(True))
    _stub_captions(monkeypatch, [], status="unavailable")

    result = _load()._transcription_for(video, tmp_path)

    assert result["status"] == "reused"
    saved = json.loads((tmp_path / "inputs/source-transcript.json").read_text(encoding="utf-8"))
    assert "burned_subtitles" not in saved


# ---------------------------------------------------------------------------
# An opening teaser: footage cut in from later is not attributed and not compared
# ---------------------------------------------------------------------------

FOUND = {"status": "found", "method": "frame_reuse", "end_seconds": 4.75, "clips": []}


def _recording_attribution(monkeypatch):
    """Attribution that notes which rows it was asked about."""
    from pipeline import attribute_speakers as attribution

    seen = []

    def fake_attribute(rows, _cast, **_kwargs):
        seen.append([r["text"] for r in rows])
        return {
            "model": "jev-test",
            "attributions": [
                {
                    "speaker_name": "船夫",
                    "delivery": "spoken",
                    "confidence": 0.9,
                    "needs_review": False,
                    "review_reasons": [],
                }
                for _ in rows
            ],
        }

    monkeypatch.setattr(attribution, "attribute_speakers", fake_attribute)
    monkeypatch.setattr(attribution, "derived_cast", lambda _rows, **_kw: {"characters": []})
    return seen


def _teaser_rows(*, marked=True):
    rows = [
        {"start": 0.1, "end": 4.7, "text": "天亮见。船不等人！"},
        {"start": 30.0, "end": 32.0, "text": "开船咯"},
        {"start": 40.0, "end": 42.0, "text": "坐稳了"},
    ]
    if marked:
        rows[0]["teaser"] = True
    return rows


def _attributed(rows):
    """Rows as an earlier run left them: every line already attributed."""
    return [
        {**row, "speaker": "船夫", "delivery": "spoken", "attribution_confidence": 0.9}
        for row in rows
    ]


def test_teaser_rows_are_left_out_of_attribution_and_labelled(monkeypatch, tmp_path):
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    payload = {"segments": _teaser_rows()}
    seen = _recording_attribution(monkeypatch)
    module = _load()

    outcome = module._attribute(payload, inputs)

    assert seen == [["开船咯", "坐稳了"]]
    assert outcome["segments"] == 3 and outcome["teaser_rows"] == 1
    saved = json.loads((inputs / "source-transcript.json").read_text(encoding="utf-8"))
    teaser = saved["segments"][0]
    assert (teaser["speaker"], teaser["delivery"], teaser["attribution_confidence"]) == (
        module.TEASER_SPEAKER,
        "unclear",
        0.0,
    )
    assert "needs_review" not in teaser
    assert module._attribution_complete(saved)
    text = (inputs / "source-transcript.txt").read_text(encoding="utf-8")
    assert "[预告片段] 天亮见。船不等人！" in text


def test_an_older_transcript_gets_its_teaser_marked_without_asr(monkeypatch, tmp_path):
    from pipeline import opening_teaser

    video = _video(tmp_path)
    _transcript(tmp_path, video, marker=True, rows=_attributed(_teaser_rows(marked=False)))
    _stub_captions(monkeypatch, [])
    seen = _recording_attribution(monkeypatch)
    monkeypatch.setattr(opening_teaser, "detect_opening_teaser", lambda _media: FOUND)
    module = _load()

    result = module._transcription_for(video, tmp_path)

    assert result["status"] == "transcribed" and result["asr"] == "reused"
    assert result["opening_teaser"]["rows_marked"] == 1
    assert seen == [["开船咯", "坐稳了"]]
    saved = json.loads((tmp_path / "inputs/source-transcript.json").read_text(encoding="utf-8"))
    assert saved["segments"][0]["teaser"] is True and saved["opening_teaser"]["status"] == "found"

    def scanned_again(_media):
        raise AssertionError("the opening was already scanned for this transcript")

    monkeypatch.setattr(opening_teaser, "detect_opening_teaser", scanned_again)
    assert module._transcription_for(video, tmp_path)["status"] == "reused"


def test_a_scan_that_finds_no_teaser_is_recorded_and_attribution_is_not_redone(
    monkeypatch, tmp_path
):
    from pipeline import attribute_speakers as attribution
    from pipeline import opening_teaser

    video = _video(tmp_path)
    _transcript(tmp_path, video, marker=True, rows=_rows(True))
    _stub_captions(monkeypatch, [])
    monkeypatch.setattr(
        opening_teaser,
        "detect_opening_teaser",
        lambda _media: {"status": "none", "method": "frame_reuse"},
    )

    def no_jev(*_a, **_k):
        raise AssertionError("nothing changed, so nothing is attributed again")

    monkeypatch.setattr(attribution, "attribute_speakers", no_jev)
    module = _load()

    assert module._transcription_for(video, tmp_path)["status"] == "reused"

    saved = json.loads((tmp_path / "inputs/source-transcript.json").read_text(encoding="utf-8"))
    assert saved["opening_teaser"]["status"] == "none"
    assert "teaser" not in saved["segments"][0]


def test_indexing_has_already_scanned_the_opening_so_the_plan_answers(monkeypatch, tmp_path):
    from pipeline import opening_teaser

    def refuse(_media):
        raise AssertionError("the story plan already holds the scan")

    monkeypatch.setattr(opening_teaser, "detect_opening_teaser", refuse)
    plan = tmp_path / "story_plan.json"
    plan.write_text(json.dumps({"opening_teaser": FOUND}), encoding="utf-8")

    assert _load()._opening_teaser(tmp_path / "video.mp4", str(plan)) == FOUND


def test_a_plan_without_a_usable_scan_falls_back_to_scanning(monkeypatch, tmp_path):
    from pipeline import opening_teaser

    monkeypatch.setattr(opening_teaser, "detect_opening_teaser", lambda _media: {"status": "none"})
    plan = tmp_path / "story_plan.json"
    plan.write_text(
        json.dumps({"opening_teaser": {"status": "unavailable", "error": "x"}}), encoding="utf-8"
    )
    module = _load()

    assert module._opening_teaser(tmp_path / "v.mp4", str(plan)) == {"status": "none"}
    assert module._opening_teaser(tmp_path / "v.mp4", None) == {"status": "none"}


# ---------------------------------------------------------------------------
# Line performance: the emotion each spoken line's frames show
# ---------------------------------------------------------------------------

CRYING = {"emotion": "委屈痛哭", "visible": "满脸泪水", "who": "包头巾的妇人", "at": [0.3, 0.75]}


def _stub_performance(monkeypatch, status="read"):
    from pipeline import line_performance

    calls = []

    def fake(_media, rows, **_k):
        calls.append(len(rows))
        if status == "read":
            for row in rows:
                row["performance"] = dict(CRYING)
        return {"status": status, "lines": len(rows), "read": len(rows) if status == "read" else 0}

    monkeypatch.setattr(line_performance, "read_line_performance", fake)
    return calls


def test_an_older_transcript_gains_line_performance_without_redoing_attribution(
    monkeypatch, tmp_path
):
    video = _video(tmp_path)
    _transcript(tmp_path, video, marker=True, rows=_rows(True))
    _stub_captions(monkeypatch, [])
    calls = _stub_performance(monkeypatch)
    from pipeline import attribute_speakers as attribution

    def no_jev(*_a, **_k):
        raise AssertionError("performance does not change who speaks")

    monkeypatch.setattr(attribution, "attribute_speakers", no_jev)

    result = _load()._transcription_for(video, tmp_path)

    assert result["status"] == "reused"
    assert result["line_performance"]["status"] == "read"
    saved = json.loads((tmp_path / "inputs/source-transcript.json").read_text(encoding="utf-8"))
    assert saved["segments"][0]["performance"]["emotion"] == "委屈痛哭"
    text = (tmp_path / "inputs/source-transcript.txt").read_text(encoding="utf-8")
    assert "开船咯  〔画面：包头巾的妇人，委屈痛哭：满脸泪水〕" in text
    assert _load()._transcription_for(video, tmp_path)["status"] == "reused"
    assert calls == [1]


def test_an_unavailable_vision_model_is_recorded_and_tried_again(monkeypatch, tmp_path):
    video = _video(tmp_path)
    _transcript(tmp_path, video, marker=True, rows=_rows(True))
    _stub_captions(monkeypatch, [])
    calls = _stub_performance(monkeypatch, status="unavailable")
    module = _load()

    assert (
        module._transcription_for(video, tmp_path)["line_performance"]["status"] == "unavailable"
    )
    module._transcription_for(video, tmp_path)
    assert calls == [1, 1]


def test_v2_performance_summary_reopens_after_model_or_dialogue_change(monkeypatch):
    from pipeline import transcribe

    monkeypatch.setattr(transcribe, "gateway_settings", lambda: ("base", "key", "vision"))
    module = _load()
    payload = {
        "visual_people": {"schema_version": 2, "people": [], "shots": []},
        "segments": [{"text": "请坐", "start": 0, "end": 1}],
    }
    payload["line_performance"] = {
        "status": "read",
        "model": "vision",
        "input_sha256": module._performance_inputs(payload),
    }
    assert not module._performance_pending(payload)
    payload["segments"].append({"text": "好的", "start": 1, "end": 2})
    assert module._performance_pending(payload)
    payload["line_performance"]["input_sha256"] = module._performance_inputs(payload)
    monkeypatch.setattr(transcribe, "gateway_settings", lambda: ("base", "key", "other"))
    assert module._performance_pending(payload)


def test_transcribe_loads_the_matching_visual_people_table_before_asr(monkeypatch, tmp_path):
    from pipeline import shot_notes, source_identity

    video = _video(tmp_path)
    project = tmp_path / "projects/film"
    plan = project / "revisions/r1/story_plan.json"
    _write(project / "source.json", source_identity.media_fingerprint(video))
    _write(project / "project.json", {"current_revision": "r1"})
    _write(plan, {})
    calls = []
    monkeypatch.setattr(
        shot_notes, "read_shot_notes", lambda *_a, **_k: calls.append("people") or {}
    )
    module = _load()
    monkeypatch.setattr(
        module, "_transcribe", lambda _v, _w, p: calls.append(p) or {"status": "transcribed"}
    )
    assert module.film_transcribe(str(video), str(tmp_path))["status"] == "transcribed"
    assert calls == ["people", str(plan)]


@pytest.mark.parametrize("with_project", [False, True])
def test_transcribe_reuses_existing_transcript_and_force_is_explicit(
    monkeypatch, tmp_path, with_project
):
    from pipeline import source_identity

    video = _video(tmp_path)
    _transcript(tmp_path, video, marker=True, rows=_rows(True))
    _stub_captions(monkeypatch, [])
    plan = tmp_path / "projects/film/revisions/r1/story_plan.json"
    if with_project:
        _write(plan.parent.parent.parent / "source.json", source_identity.media_fingerprint(video))
        _write(plan.parent.parent.parent / "project.json", {"current_revision": "r1"})
        _write(plan, {})
        _write(plan.with_name("shot_notes.json"), {})
    module = _load()
    calls = []
    monkeypatch.setattr(
        module, "_transcribe", lambda *_a: calls.append(True) or {"status": "transcribed"}
    )
    assert module.film_transcribe(str(video), str(tmp_path))["asr"] == "reused"
    assert module.film_transcribe(str(video), str(tmp_path))["asr"] == "reused"
    assert not calls
    assert module.film_transcribe(str(video), str(tmp_path), force=True)["status"] == "transcribed"
    assert len(calls) == 1


def test_refresh_splits_using_saved_word_times_before_reading_speakers(monkeypatch, tmp_path):
    video = _video(tmp_path)
    row = {
        "text": "设备好了吗？现在好了。",
        "start": 1,
        "end": 4,
        "timing_words": [
            {"word": "设备好了吗", "start": 1, "end": 2},
            {"word": "现在好了", "start": 2.1, "end": 4},
        ],
    }
    payload = {
        "segments": [row],
        "burned_subtitles": {"status": "read"},
        "opening_teaser": {"status": "none_found"},
        "speaker_attribution": "jev",
    }
    module = _load()
    monkeypatch.setattr(module, "_teaser_pending", lambda _p: False)
    seen = []

    def performance(_video, value, **_kwargs):
        seen.extend(value["segments"])
        return {"status": "read"}

    monkeypatch.setattr(module, "_read_performance", performance)
    result = module._refresh_transcript(video, payload, video.parent, None)
    assert result["changed"] and "speaker_attribution" not in payload
    assert [r["text"] for r in seen] == ["设备好了吗？", "现在好了。"]
    assert [(r["start"], r["end"]) for r in seen] == [(1, 2), (2.1, 4)]


def test_context_receipts_are_retained_without_repeating_raw_tool_output(monkeypatch, tmp_path):
    from pipeline import line_judgment

    reply = {"raw_response": "raw model receipt"}
    monkeypatch.setattr(
        line_judgment,
        "read_line_judgments",
        lambda *_a, **_k: {"status": "read", "lines": 1, "batches": [reply], "results": [{}]},
    )
    payload = {"segments": [], "line_judgment": {"batches": [{"raw_response": "previous"}]}}
    result = _load()._read_judgments(tmp_path / "source.mp4", payload)
    assert result == {"status": "read", "lines": 1}
    assert payload["line_judgment"]["batches"] == [{"raw_response": "previous"}, reply]


def test_unread_context_batch_is_retried_with_cached_a_without_asr(monkeypatch, tmp_path):
    from pipeline import line_performance, transcribe

    notes = {
        "schema_version": 3,
        "people": [{"id": "P1", "look": "短发售票员"}],
        "shots": [{"start": 0, "end": 3, "people": ["P1"], "place": "L1", "crowd": False}],
    }
    row = {"start": 1, "end": 2, "text": "票已经取好。"}
    row["performance"] = {
        "schema_version": 2,
        "model": "vision",
        "speaker": "offscreen",
        "emotion": "平静",
        "input_sha256": line_performance.performance_inputs(notes, row),
    }
    module = _load()
    monkeypatch.setattr(transcribe, "gateway_settings", lambda: ("url", "key", "vision"))
    payload = {
        "segments": [row],
        "visual_people": notes,
        "line_performance": {"status": "read", "model": "vision"},
    }
    payload["line_performance"]["input_sha256"] = module._performance_inputs(payload)
    monkeypatch.setattr(module, "_captions_pending", lambda _p: False)
    monkeypatch.setattr(module, "_teaser_pending", lambda _p: False)
    monkeypatch.setattr(
        module, "_read_performance", lambda *_a, **_k: pytest.fail("A was current")
    )
    calls = []
    monkeypatch.setattr(
        module, "_read_judgments", lambda *_a, **_k: calls.append(1) or {"status": "partial"}
    )
    assert module._refresh_transcript(tmp_path / "source.mp4", payload, tmp_path / "inputs", None)[
        "changed"
    ]
    assert module._refresh_transcript(tmp_path / "source.mp4", payload, tmp_path / "inputs", None)[
        "changed"
    ]
    assert calls == [1, 1] and row["performance"]["speaker"] == "offscreen"


# film_analyze scope and film_refresh_report: the MCP tools are the whole entry


def test_analyze_forwards_profile_and_scope_to_the_indexer(monkeypatch, tmp_path):
    module = _load()
    from pipeline import entry

    seen = {}

    def index(*_args, **kwargs):
        seen.update(kwargs)
        return {"analysis_status": "blocked", "reason": "stop here"}

    monkeypatch.setattr(entry, "index_media", index)
    video = tmp_path / "source.mp4"
    video.write_bytes(b"video")
    module.film_analyze(
        str(video), str(tmp_path / "projects/film"), profile="forensic", start=5, end=35
    )

    assert (seen["profile"], seen["start"], seen["end"]) == ("forensic", 5, 35)


def test_analyze_rejects_an_unknown_profile_without_indexing(monkeypatch, tmp_path):
    module = _load()
    from pipeline import entry

    monkeypatch.setattr(entry, "index_media", lambda *_a, **_k: pytest.fail("indexed"))
    video = tmp_path / "source.mp4"
    video.write_bytes(b"video")
    result = module.film_analyze(str(video), str(tmp_path / "projects/film"), profile="fast")

    assert result["status"] == "index_blocked"
    assert result["result"]["reason"] == "invalid_profile"


def test_refresh_report_on_a_missing_project_returns_an_actionable_receipt(tmp_path):
    receipt = _load().film_refresh_report(str(tmp_path / "projects/film"))

    assert receipt["status"] == "report_refresh_failed"
    assert receipt["error_code"] == "REPORT_INPUT_MISSING"
    assert receipt["next_action"]


def test_refresh_report_returns_what_the_cli_prints(monkeypatch, tmp_path, capsys):
    from pipeline import entry

    project = tmp_path / "projects/film"
    monkeypatch.setattr(
        sys,
        "argv",
        ["entry", "--refresh-report", "--output", str(project), "--workspace", str(tmp_path)],
    )
    with pytest.raises(SystemExit):
        entry.main()

    printed = json.loads(capsys.readouterr().out)
    assert printed == _load().film_refresh_report(str(project), str(tmp_path))


def test_media_project_warns_that_it_is_deprecated(tmp_path):
    script = ROOT / "skills" / "film-analysis" / "media_project.py"
    done = subprocess.run(
        [sys.executable, str(script), "--refresh-report", "--output", str(tmp_path / "nope")],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
    )

    assert "removed in 0.13.0" in done.stderr
    assert json.loads(done.stdout)["status"] == "report_refresh_failed"
