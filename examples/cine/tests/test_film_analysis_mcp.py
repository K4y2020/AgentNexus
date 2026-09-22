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
