import asyncio
import dataclasses
import importlib.util
import os
import sys
from pathlib import Path

from agentnexus.host.connect import _build_runner_env
from agentnexus.runner.identity import RUNNER_AUTH_SECRET_ENV_VARS
from agentnexus.runner.mcp_manager import RunnerMcpManager
from agentnexus.spec import load
from agentnexus.tools.manager import ToolManager

BUNDLE = Path(__file__).resolve().parents[1] / "examples" / "cine"
ENV_PROBE = (
    Path(__file__).resolve().parent / "tools" / "fixtures" / "env_probe_stdio_mcp_server.py"
)


def test_cine_bundle_loads():
    spec = load(BUNDLE)
    assert spec.name == "cine"
    assert [s.name for s in spec.skills] == [
        "cine-art",
        "cine-characters",
        "cine-outline",
        "cine-script",
        "cine-storyboard",
        "film-analysis",
    ]
    assert spec.executor.config["harness"] == "openai-agents"
    skill_paths = [
        BUNDLE / "skills" / name / "SKILL.md"
        for name in [
            "film-analysis",
            "cine-outline",
            "cine-script",
            "cine-storyboard",
            "cine-characters",
            "cine-art",
        ]
    ]
    for path in (BUNDLE / "config.yaml", *skill_paths):
        content = path.read_text(encoding="utf-8")
        assert "EVA" not in content
        assert "U:/movie" not in content


def test_cine_bundle_opts_into_its_tools():
    spec = load(BUNDLE)
    declared = {b.name for b in spec.tools.builtins}
    assert declared == {
        "seedance_read_canvas",
        "seedance_edit_canvas",
        "cine_verify_report",
        "cine_jev_judge",
    }
    advertised = {s["function"]["name"] for s in ToolManager(spec).get_tool_schemas()}
    assert declared <= advertised


def test_media_project_delegates_to_canonical_pipeline(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(BUNDLE / "skills/film-analysis"))
    spec = importlib.util.spec_from_file_location(
        "media_project", BUNDLE / "skills/film-analysis/media_project.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    calls = []

    def index(video, output):
        calls.append((video, output))
        return {"analysis_status": "indexed_unreviewed", "reviewed_shot_count": 0}

    monkeypatch.setattr(module, "index_media", index)
    video, output = tmp_path / "source.mp4", tmp_path / "film"
    assert module.initialize(video, output)["reviewed_shot_count"] == 0
    assert calls == [(video, output)]


def test_asset_anchor_rule_is_one_module_in_both_places():
    """The importer and the storyboard stage check must refuse the same storyboards.

    The pipeline runs in a Topic whose python has no agentnexus, so it carries
    its own copy; any edit has to land in both.
    """
    root = Path(__file__).resolve().parents[1]
    importer = root / "agentnexus" / "seedance" / "asset_anchors.py"
    pipeline = BUNDLE / "skills" / "film-analysis" / "pipeline" / "asset_anchors.py"
    assert importer.read_bytes() == pipeline.read_bytes()
    assert b"agentnexus" not in pipeline.read_bytes().split(b'"""')[-1]


def test_a_report_citing_an_invented_receipt_is_blocked():
    """Cine's final replies go through the grounding policy on the response phase."""
    from agentnexus.policies import resolve_function_policy

    spec = load(BUNDLE)
    policy = resolve_function_policy(
        next(p for p in spec.guardrails.policies if p.name == "grounded_report")
    )
    assert policy.needs_trajectory, "the engine hands it the conversation on the response phase"
    evaluate = policy._callable
    trajectory = [{"type": "message", "role": "user", "text": "校验 E01-05"}]
    verdict = evaluate(
        {
            "type": "response",
            "data": "失败：CINE_CONTINUES_FROM_SOURCE_UNRESOLVED",
            "trajectory": trajectory,
        }
    )
    assert verdict["result"] == "DENY"
    assert "本回合没有调用任何工具" in verdict["reason"]


def _runner_env(host_env):
    """The environment a host started with *host_env* gives a runner."""
    return _build_runner_env(
        host_env,
        server_url="http://127.0.0.1:6767",
        runner_id="runner_cine",
        binding_token="runner-binding-secret",
        workspace=str(BUNDLE),
        parent_pid=os.getpid(),
        initial_auth_token="runner-bearer-secret",
    )


def _film_server_view(monkeypatch, runner_env, names):
    """Read *names* inside the bundle's film-analysis server, spawned as a runner spawns it.

    The declaration is the bundle's own; only its command is swapped for an env
    probe, because the real server needs uv and the Whisper stack.
    """
    for name in list(os.environ):
        if name not in runner_env:
            monkeypatch.delenv(name)
    for name, value in runner_env.items():
        monkeypatch.setenv(name, value)
    spec = load(BUNDLE)
    film = next(s for s in spec.mcp_servers if s.name == "cine-film-analysis")
    probe = dataclasses.replace(film, command=sys.executable, args=[str(ENV_PROBE)])
    spec = dataclasses.replace(spec, mcp_servers=[probe])

    async def read():
        manager = RunnerMcpManager()
        try:
            await manager.schemas_for(spec)
            return {
                name: await manager.call_tool(spec, f"{probe.name}__read_env", {"name": name})
                for name in names
            }
        finally:
            await manager.shutdown()

    return asyncio.run(read())


def test_gpu_switches_set_on_the_host_reach_the_film_analysis_server(tmp_path, monkeypatch):
    """CINE_GPU=off on the host reaches the server, which used to run on the GPU anyway.

    Covers the host→runner filter, the bundle's MCP declaration and a live stdio
    child. Unset switches stay unset; the runner's auth tokens and a provider key
    it holds never reach the server.
    """
    monkeypatch.setenv("AGENTNEXUS_CONFIG_HOME", str(tmp_path))
    switches = {
        "CINE_GPU": "off",
        "CINE_GPU_ENV": str(tmp_path / "cine-gpu"),
        "HF_HOME": str(tmp_path / "hf"),
        "TORCH_HOME": str(tmp_path / "torch"),
    }
    secrets = [*sorted(RUNNER_AUTH_SECRET_ENV_VARS), "OPENAI_API_KEY"]
    host = {k: v for k, v in os.environ.items() if k not in switches}
    host["OPENAI_API_KEY"] = "sk-host-provider-key"

    runner = _runner_env({**host, **switches})
    assert all(runner.get(name) for name in secrets), "the runner holds these"
    assert _film_server_view(monkeypatch, runner, [*switches, *secrets]) == {
        **{name: f"set:{value}" for name, value in switches.items()},
        **dict.fromkeys(secrets, "<unset>"),
    }
    assert _film_server_view(monkeypatch, _runner_env(host), list(switches)) == dict.fromkeys(
        switches, "<unset>"
    )
