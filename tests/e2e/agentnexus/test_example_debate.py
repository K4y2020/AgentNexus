"""Structural test for the debate moderator bundle (examples/debate).

``debate`` never argues a position itself: it relays each discussant's latest
message verbatim to the other — Claude Code on claude-sdk and Codex on codex —
until both emit a CONSENSUS verdict or the round cap is hit. Pure spec-load — no
LLM, no credentials — modeled on ``test_example_debby.py``.

What breaks if this fails:
- the discussants collapse onto one vendor, or one is dropped,
- a discussant pins a provider-specific model,
- the moderator gains spawn/timer tools or can fan out beyond the two discussants,
- the verdict markers drift between the moderator and a discussant, so the
  moderator can no longer detect the end of the debate.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agentnexus.spec import load
from agentnexus.spec.types import AgentSpec

# tests/e2e/agentnexus/test_example_debate.py -> repo root is 3 parents up.
_DEBATE_BUNDLE = Path(__file__).resolve().parents[3] / "examples" / "debate"
_DISCUSSANTS = ("claude_code", "codex")


@pytest.fixture(scope="module")
def debate_spec() -> AgentSpec:
    """Load and validate the debate bundle once for the module."""
    return load(_DEBATE_BUNDLE)


def test_debate_discussants_are_cross_vendor(debate_spec: AgentSpec) -> None:
    """The moderator relays between ``claude_code`` on claude-sdk and ``codex`` on codex."""
    assert debate_spec.name == "debate"
    assert debate_spec.executor.config.get("harness") == "claude-sdk"
    assert sorted(debate_spec.tools.agents) == list(_DISCUSSANTS)
    harness = {a.name: a.executor.config.get("harness") for a in debate_spec.sub_agents}
    assert harness == {"claude_code": "claude-sdk", "codex": "codex"}


def test_debate_discussants_are_unpinned(debate_spec: AgentSpec) -> None:
    """Neither discussant pins a model, so each runs on the user's configured provider."""
    assert {a.name for a in debate_spec.sub_agents} == set(_DISCUSSANTS)
    for agent in debate_spec.sub_agents:
        assert agent.executor.model is None, agent.name
        assert agent.executor.profile is None, agent.name


def test_debate_moderator_only_relays(debate_spec: AgentSpec) -> None:
    """
    The moderator can't spawn its own children or set timers, and
    ``spawn_bounds`` caps each turn at one send per discussant.
    """
    assert debate_spec.spawn is False
    assert debate_spec.timers is False
    assert debate_spec.guardrails is not None
    spawn_bounds = next(p for p in debate_spec.guardrails.policies if p.name == "spawn_bounds")
    assert spawn_bounds.function.arguments == {
        "max_dispatches_per_turn": 2,
        "dispatch_tools": ["sys_session_send"],
    }


def test_debate_verdict_markers_match_across_bundle() -> None:
    """
    Each discussant ends with the CONSENSUS / NO-CONSENSUS markers the moderator
    watches for; renaming a marker on one side breaks end detection.
    """
    moderator = " ".join((_DEBATE_BUNDLE / "config.yaml").read_text(encoding="utf-8").split())
    assert "(CONSENSUS: ... or NO-CONSENSUS: ...)" in moderator
    assert "OTHER side's full latest message VERBATIM" in moderator
    assert "do not exceed 6 rounds" in moderator
    for name in _DISCUSSANTS:
        prompt = (_DEBATE_BUNDLE / "agents" / name / "config.yaml").read_text(encoding="utf-8")
        assert "CONSENSUS: <the agreed position, one sentence>" in prompt, name
        assert (
            "NO-CONSENSUS: <the single sharpest remaining disagreement, one sentence>" in prompt
        ), name
