"""A final message may cite only identifiers some tool output or user message holds."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from agentnexus.entities import PagedList
from agentnexus.policies import resolve_function_policy
from agentnexus.policies.builtins.grounding import cited_evidence
from agentnexus.policies.types import EvaluationContext
from agentnexus.runtime.policies.engine import PolicyEngine, trajectory_entry
from agentnexus.spec.types import (
    FunctionPolicySpec,
    FunctionRef,
    Phase,
    PhaseSelector,
    PolicyAction,
)

NODE = "node_25d73d4151534131a362472f05a27e2f"
JOB = "job_d75067a3ca194b06a7caf3a18123c8c8"


def _response(text: str, trajectory: list[dict[str, Any]] | None) -> dict[str, Any]:
    event: dict[str, Any] = {"type": "response", "target": None, "data": text}
    if trajectory is not None:
        event["trajectory"] = trajectory
    return event


def _turn(*items: dict[str, Any]) -> list[dict[str, Any]]:
    return [{"type": "message", "role": "user", "text": "submit E01-02"}, *items]


def _call() -> dict[str, Any]:
    return {"type": "function_call", "name": "seedance_edit_canvas", "arguments": "{}"}


def _output(text: str) -> dict[str, Any]:
    return {"type": "function_call_output", "output": text}


def test_a_report_quoting_tool_output_passes() -> None:
    policy = cited_evidence()
    trajectory = _turn(_call(), _output(f'{{"job": "{JOB}", "node": "{NODE}"}}'))

    assert policy(_response(f"任务 {JOB} 已提交到 {NODE}。", trajectory))["result"] == "ALLOW"


def test_an_invented_receipt_is_refused_and_named() -> None:
    policy = cited_evidence()
    invented = "node_b352aa7bc56f4d858cf25e71ddb565fa"
    text = f"校验失败：CINE_CONTINUES_FROM_SOURCE_UNRESOLVED，节点 {invented}。"

    verdict = policy(_response(text, _turn()))

    assert verdict["result"] == "DENY"
    assert "CINE_CONTINUES_FROM_SOURCE_UNRESOLVED" in verdict["reason"]
    assert invented in verdict["reason"]
    assert "no tool calls" in verdict["reason"]


def test_the_agent_cannot_vouch_for_itself() -> None:
    """An identifier only the agent has said before is still unsourced."""
    policy = cited_evidence()
    trajectory = _turn({"type": "message", "role": "assistant", "text": f"done: {JOB}"})
    trajectory.append({"type": "message", "role": "user", "text": "which job?"})

    assert policy(_response(f"It was {JOB}.", trajectory))["result"] == "DENY"


def test_an_identifier_the_user_gave_is_a_source() -> None:
    policy = cited_evidence()
    trajectory = [
        {"type": "message", "role": "user", "text": f"why did MISSING_IMAGE_REFERENCE hit {NODE}?"}
    ]

    text = f"MISSING_IMAGE_REFERENCE on {NODE}: the prompt skips picture 6."
    assert policy(_response(text, trajectory))["result"] == "ALLOW"


def test_a_reply_with_no_identifiers_is_not_judged() -> None:
    assert cited_evidence()(_response("好的，我这就去做。", _turn()))["result"] == "ALLOW"


def test_without_a_trajectory_or_off_phase_it_abstains() -> None:
    policy = cited_evidence()
    assert policy(_response(f"done: {JOB}", None))["result"] == "ALLOW"
    assert (
        policy({"type": "tool_call", "data": {"name": "x", "arguments": {}}})["result"] == "ALLOW"
    )


def test_ignored_identifiers_and_custom_message() -> None:
    policy = cited_evidence(
        ignore=["CINE_GENERATION_AUTHORIZATION_REQUIRED"],
        message="没有出处：{cited}。{turn}",
        no_calls_note="本回合没有调用工具。",
    )
    assert (
        policy(_response("需要 CINE_GENERATION_AUTHORIZATION_REQUIRED 授权。", _turn()))["result"]
        == "ALLOW"
    )
    verdict = policy(_response(f"已完成 {JOB}", _turn(_call(), _output("ok"))))
    assert verdict["reason"] == f"没有出处：{JOB}。"


# ── The engine hands the RESPONSE phase its conversation ─────────────


@dataclass
class _Item:
    type: str
    data: Any


@dataclass
class _Message:
    role: str
    content: list[dict[str, Any]]


@dataclass
class _Output:
    output: str


@dataclass
class _Call:
    name: str
    arguments: str


@dataclass
class _Store:
    items: list[_Item]
    calls: list[dict[str, Any]] = field(default_factory=list)

    def list_items(self, conversation_id: str, **kwargs: Any) -> PagedList[Any]:
        self.calls.append({"conversation_id": conversation_id, **kwargs})
        ordered = list(reversed(self.items)) if kwargs.get("order") == "desc" else self.items
        return PagedList(data=ordered[: kwargs.get("limit", 100)], has_more=False)


def _engine(store: _Store, on: list[PhaseSelector] | None) -> PolicyEngine:
    spec = FunctionPolicySpec(
        name="grounded_report",
        on=on,
        function=FunctionRef(
            path="agentnexus.policies.builtins.grounding.cited_evidence", arguments={}
        ),
    )
    return PolicyEngine(
        policies=[resolve_function_policy(spec)],
        label_defs={},
        ask_timeout=30,
        conversation_id="conv_grounding",
        initial_labels={},
        conversation_store=store,  # type: ignore[arg-type]
    )


def _history() -> list[_Item]:
    return [
        _Item("message", _Message("user", [{"type": "input_text", "text": "submit E01-02"}])),
        _Item("function_call", _Call("seedance_edit_canvas", "{}")),
        _Item("function_call_output", _Output(f'{{"job": "{JOB}"}}')),
    ]


@pytest.mark.asyncio
async def test_the_engine_passes_the_conversation_to_a_response_policy() -> None:
    store = _Store(_history())
    engine = _engine(store, [PhaseSelector(phase=Phase.RESPONSE)])

    grounded = await engine.evaluate(
        EvaluationContext(phase=Phase.RESPONSE, content=f"提交了 {JOB}")
    )
    invented = await engine.evaluate(
        EvaluationContext(phase=Phase.RESPONSE, content="提交了 job_0123456789abcdef0123")
    )

    assert grounded.action == PolicyAction.ALLOW
    assert invented.action == PolicyAction.DENY
    assert store.calls[0]["order"] == "desc"


@pytest.mark.asyncio
async def test_no_fetch_without_a_policy_on_the_response_phase() -> None:
    store = _Store(_history())
    engine = _engine(store, [PhaseSelector(phase=Phase.TOOL_CALL)])

    await engine.evaluate(EvaluationContext(phase=Phase.RESPONSE, content=f"提交了 {JOB}"))
    await engine.evaluate(
        EvaluationContext(
            phase=Phase.TOOL_CALL, content={"name": "x", "arguments": {}}, tool_name="x"
        )
    )

    assert store.calls == []


@pytest.mark.asyncio
async def test_no_fetch_for_a_response_policy_that_does_not_ask() -> None:
    store = _Store(_history())
    spec = FunctionPolicySpec(
        name="plain",
        on=None,
        function=FunctionRef(
            path="agentnexus.policies.builtins.code_guard.packaged_code_only", arguments={}
        ),
    )
    engine = PolicyEngine(
        policies=[resolve_function_policy(spec)],
        label_defs={},
        ask_timeout=30,
        conversation_id="conv_plain",
        initial_labels={},
        conversation_store=store,  # type: ignore[arg-type]
    )

    await engine.evaluate(EvaluationContext(phase=Phase.RESPONSE, content="ok"))

    assert store.calls == []


def test_trajectory_entries_are_plain() -> None:
    message, call, output = _history()
    assert trajectory_entry(message) == {
        "type": "message",
        "role": "user",
        "text": "submit E01-02",
    }
    assert trajectory_entry(call) == {
        "type": "function_call",
        "name": "seedance_edit_canvas",
        "arguments": "{}",
    }
    assert trajectory_entry(output) == {
        "type": "function_call_output",
        "output": f'{{"job": "{JOB}"}}',
    }


def test_a_turn_without_calls_cannot_reuse_earlier_tool_output() -> None:
    """Real ids from an earlier turn are what a made-up "synced" report is built from."""
    policy = cited_evidence()
    earlier = _turn(_call(), _output(f'{{"node": "{NODE}"}}'))
    report = f"已重新同步，{NODE} 已更新。"

    idle = [*earlier, {"type": "message", "role": "user", "text": "再同步一次"}]
    worked = [*idle, _call(), _output('{"status": "synced"}')]

    assert policy(_response(report, idle))["result"] == "DENY"
    assert policy(_response(report, worked))["result"] == "ALLOW"
