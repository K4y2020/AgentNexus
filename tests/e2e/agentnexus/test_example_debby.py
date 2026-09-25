"""Structural test for the Debby two-headed brainstorming bundle (examples/debby).

Debby answers and edits files herself by default and has two advisory heads: a
Claude sub-agent and a GPT sub-agent — plain responders on the claude-sdk and
codex harnesses. When the user asks for two perspectives she fans the question
out to BOTH heads, and the ``debate`` skill has them critique each other before
converging. Pure spec-load — no LLM, no credentials — modeled on
``test_example_polly.py``.

What breaks if this fails:
- the two heads collapse onto one vendor (no cross-model contrast — Debby's whole
  point), or a head is dropped entirely,
- a head silently switches harness (e.g. the GPT head ends up on claude-sdk),
- a bundled skill (``debate``, ``ui-ux-pro-max``, ``video-shotcraft``) is dropped
  or renamed,
- the parent loses its direct filesystem access or the blast-radius guard on it,
- the two-head fan-out stops being parallel and bounded.
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
    # Pinning the brain's harness would route the gpt head onto Claude too.
    assert debby_spec.executor.config.get("smart_routing_harness") == "auto"


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


def test_debby_skills_present(debby_spec: AgentSpec) -> None:
    """The bundled skills are discovered from skills/<name>/SKILL.md."""
    assert sorted(s.name for s in debby_spec.skills) == [
        "debate",
        "ui-ux-pro-max",
        "video-shotcraft",
    ]


def test_debby_parent_acts_directly_under_blast_radius(debby_spec: AgentSpec) -> None:
    """
    Debby's parent works in the caller's workspace, guarded by ``blast_radius``;
    both responder heads keep their own filesystem access.

    Routine edits and shell work run on the parent without delegating, so its
    ``os_env`` is load-bearing and ``blast_radius`` is the guard on it.
    """
    assert debby_spec.os_env is not None
    assert debby_spec.os_env.type == "caller_process"
    assert debby_spec.guardrails is not None
    blast_radius = next(p for p in debby_spec.guardrails.policies if p.name == "blast_radius")
    assert blast_radius.function.path == "agentnexus.inner.nessie.policies.blast_radius"
    by_name = {a.name: a for a in debby_spec.sub_agents}
    for name in ("claude", "gpt"):
        assert by_name[name].os_env is not None, name
        assert by_name[name].os_env.type == "caller_process", name


def test_debby_fans_out_to_both_heads_in_parallel(debby_spec: AgentSpec) -> None:
    """Two-head fan-out is opt-in, dispatched in one turn, and capped at two sends."""
    config = (_DEBBY_BUNDLE / "config.yaml").read_text(encoding="utf-8")
    compact = " ".join(config.split())

    assert "Multi-perspective Brainstorming & Debate (ONLY when requested)" in compact
    assert (
        "Dispatch to `claude` and `gpt` in parallel via `sys_session_send` "
        "in the same turn, then END YOUR TURN." in compact
    )
    assert "Never search unrelated workspaces or sessions" in compact
    assert debby_spec.guardrails is not None
    spawn_bounds = next(p for p in debby_spec.guardrails.policies if p.name == "spawn_bounds")
    assert spawn_bounds.function.arguments["max_dispatches_per_turn"] == 2
    assert spawn_bounds.function.arguments["dispatch_tools"] == ["sys_session_send"]
