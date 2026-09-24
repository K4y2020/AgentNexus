"""Structural test for the Debby two-headed brainstorming bundle (examples/debby).

Debby never answers from a single model: every question is fanned out to BOTH a
Claude sub-agent and a GPT sub-agent — two plain (non-coding) responders on the
claude-sdk and codex harnesses — and the ``debate`` skill has them
critique each other before converging. Pure spec-load — no LLM, no credentials —
modeled on ``test_example_polly.py``.

What breaks if this fails:
- the two heads collapse onto one vendor (no cross-model contrast — Debby's whole
  point), or a head is dropped entirely,
- a head silently switches harness (e.g. the GPT head ends up on claude-sdk),
- the ``debate`` skill is dropped or renamed (the critique loop regresses),
- filesystem tools leak onto the parent instead of staying on the two heads.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agentnexus.spec import load
from agentnexus.spec.types import AgentSpec

# tests/e2e/agentnexus/test_example_debby.py -> repo root is 3 parents up.
_DEBBY_BUNDLE = Path(__file__).resolve().parents[3] / "examples" / "debby"


@pytest.fixture(scope="module")
def debby_spec() -> AgentSpec:
    """Load and validate the debby bundle once for the module."""
    return load(_DEBBY_BUNDLE)


def test_debby_is_two_headed_cross_vendor(debby_spec: AgentSpec) -> None:
    """
    Debby has exactly two heads — ``claude`` on claude-sdk and ``gpt`` on
    codex — so every answer contrasts two distinct vendors.

    A missing/renamed head, or both heads landing on the same harness, removes
    the cross-model contrast that is Debby's entire reason to exist.
    """
    assert debby_spec.name == "debby"
    fam = {a.name: a.executor.config.get("harness") for a in debby_spec.sub_agents}
    assert sorted(debby_spec.tools.agents) == ["claude", "gpt"]
    assert fam["claude"] == "claude-sdk"
    assert fam["gpt"] == "codex"
    # Two distinct vendors → the heads always disagree across providers.
    assert len(set(fam.values())) == 2


def test_debby_heads_are_unpinned(debby_spec: AgentSpec) -> None:
    """
    Neither head pins a model: each inherits whatever Claude / OpenAI provider
    the user configured (Anthropic key, subscription, gateway, or Databricks).

    Un-pinning is load-bearing for OSS — a Databricks-specific model id would
    404 on a plain Anthropic / OpenAI key. Re-introducing a pin re-couples a
    head to one provider, so fail here if a model reappears.
    """
    by_name = {a.name: a for a in debby_spec.sub_agents}
    for name in ("claude", "gpt"):
        assert by_name[name].executor.model is None, name
        assert by_name[name].executor.profile is None, name


def test_debby_debate_skill_present(debby_spec: AgentSpec) -> None:
    """The ``debate`` skill is discovered from skills/debate/SKILL.md."""
    assert sorted(s.name for s in debby_spec.skills) == ["debate"]


def test_debby_parent_is_orchestration_only(debby_spec: AgentSpec) -> None:
    """
    Debby's parent has no direct filesystem tools; both responder heads do.

    This prevents the parent from replacing mandatory two-head fan-out with
    direct shell exploration while preserving reference-file access where the
    substantive analysis actually runs.
    """
    assert debby_spec.os_env is None
    by_name = {a.name: a for a in debby_spec.sub_agents}
    for name in ("claude", "gpt"):
        assert by_name[name].os_env is not None, name
        assert by_name[name].os_env.type == "caller_process", name


def test_debby_requires_parallel_first_action(debby_spec: AgentSpec) -> None:
    """The parent dispatches both heads before text or unrelated tools."""
    config = (_DEBBY_BUNDLE / "config.yaml").read_text(encoding="utf-8")
    compact = " ".join(config.split())

    assert "FIRST response must contain exactly two `sys_session_send` calls" in compact
    assert "one for `claude`, one for `gpt`" in compact
    assert "no other tool call" in compact
    assert "Never search unrelated workspaces or sessions" in compact
    assert debby_spec.guardrails is not None
    spawn_bounds = next(p for p in debby_spec.guardrails.policies if p.name == "spawn_bounds")
    assert spawn_bounds.function.arguments["max_dispatches_per_turn"] == 2
