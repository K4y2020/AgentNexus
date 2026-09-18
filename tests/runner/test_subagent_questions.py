import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest

from agentnexus.runner import app
from agentnexus.runner.tool_dispatch import _execute_subagent_tool
from agentnexus.runtime.subagent_block_notifier import _format_block_notice
from agentnexus.runtime.subagent_questions import ordinary_question, validate_answers


def question_event(phase="codex_request_user_input"):
    return {
        "type": "response.elicitation_request",
        "elicitation_id": "question-1",
        "params": {
            "phase": phase,
            "ask_user_question": {"questions": [{"id": "path", "question": "Which workspace?"}]},
        },
    }


@pytest.mark.parametrize(
    "case",
    [
        "answer",
        "message",
        "approval",
        "secret",
        "stale",
        "other_parent",
        "descendant",
        "failure",
    ],
)
@pytest.mark.asyncio
async def test_question_continues_original_turn_without_spawning(case):
    event = question_event("tool_call" if case == "approval" else "codex_request_user_input")
    if case == "secret":
        event["params"]["ask_user_question"]["questions"][0]["isSecret"] = True
    if case == "descendant":
        event["params"]["target_session_id"] = "grandchild"
    posts = []

    def handle(request):
        if request.method == "GET" and request.url.path == "/v1/sessions/child":
            return httpx.Response(
                200,
                json={
                    "parent_session_id": "other" if case == "other_parent" else "parent",
                    "title": "codex:fix-modal",
                    "busy": True,
                    "pending_elicitations": [] if case == "stale" else [event],
                },
            )
        if request.method == "POST":
            posts.append((request.url.path, json.loads(request.content)))
            return httpx.Response(503 if case == "failure" else 200, json={})
        return httpx.Response(404, json={})

    args = {"task_id": "child", "args": "Project path from the original task"}
    if case != "message":
        args.update(question_id="question-1", answers={"path": "U:/AI/Gamehack/ExportedProject"})
    async with httpx.AsyncClient(
        base_url="http://server", transport=httpx.MockTransport(handle)
    ) as client:
        try:
            result = json.loads(
                await _execute_subagent_tool(
                    args,
                    server_client=client,
                    conversation_id="parent",
                    session_inbox=asyncio.Queue(),
                )
            )
        finally:
            app._session_inboxes_ref.pop("parent", None)
    if case in ("answer", "failure"):
        assert posts == [
            (
                "/v1/sessions/child/elicitations/question-1/resolve",
                {
                    "action": "accept",
                    "content": args["answers"],
                },
            )
        ]
        if case == "answer":
            assert result["status"] == "answer_submitted"
            assert result["task_success_verified"] is False
        else:
            assert result["error"] == "question_answer_failed"
    else:
        assert not posts
        assert (
            result["error"]
            == {
                "message": "child_needs_input",
                "approval": "invalid_question_answer",
                "secret": "invalid_question_answer",
                "stale": "question_not_pending",
                "other_parent": "session_out_of_tree",
                "descendant": "question_not_pending",
            }[case]
        )


@pytest.mark.parametrize("answers", [{}, {"path": ""}, {"path": "x", "allow_all_edits": True}])
def test_reject_incomplete_or_policy_answers(answers):
    with pytest.raises(ValueError):
        validate_answers(question_event(), answers)


def test_question_wake_has_exact_child_and_question_ids():
    notice = json.loads(
        _format_block_notice(SimpleNamespace(id="child", title="codex:fix"), question_event())
    )
    assert notice["type"] == "subagent_needs_input"
    assert notice["task_id"] == "child"
    assert notice["question_id"] == "question-1"
    assert "Do not guess" in notice["instruction"]


def test_claude_permission_is_not_a_question_without_exact_tool():
    event = question_event("pre_tool_use")
    event["params"].update(policy_name="claude_native_permission", content_preview="Bash(...)")
    assert ordinary_question(event) is None
    event["params"]["content_preview"] = "AskUserQuestion(...)"
    assert ordinary_question(event) is not None


@pytest.mark.asyncio
async def test_question_wakes_parent_once_without_approval_grace(monkeypatch):
    from agentnexus.runtime import subagent_block_notifier as module

    async def forbidden_sleep(_seconds):
        raise AssertionError("Ordinary questions must not wait for approval escalation")

    monkeypatch.setattr(module, "_escalation_sleep", forbidden_sleep)
    child = SimpleNamespace(id="child", title="codex:fix", parent_conversation_id="parent")
    store = SimpleNamespace(get_conversation=lambda _: child)
    calls = []
    woke = asyncio.Event()
    resolved = asyncio.Event()

    async def deliver(parent_id, _child, notice):
        calls.append((parent_id, notice))
        (woke if len(calls) == 1 else resolved).set()
        return True

    notifier = module.SubagentBlockNotifier(store, deliver, asyncio.get_running_loop())
    try:
        notifier.observe("child", question_event())
        notifier.observe("child", question_event())
        await asyncio.wait_for(woke.wait(), timeout=2)
        assert len(calls) == 1
        assert calls[0][0] == "parent"
        notifier.observe(
            "child",
            {
                "type": "response.elicitation_resolved",
                "elicitation_id": "question-1",
                "action": "accept",
            },
        )
        await asyncio.wait_for(resolved.wait(), timeout=2)
        assert "does not mean the task succeeded" in calls[1][1]
    finally:
        notifier.close()
