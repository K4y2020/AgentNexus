import pytest
from pydantic import ValidationError

from omnigent.runner.tool_dispatch import build_native_relay_tool_schemas
from omnigent.runtime.prompt import build_instructions, build_instructions_nullable
from omnigent.spec import AgentSpec
from omnigent.tools.builtins.ask_user import AskUserRequest, SysAskUserTool
from omnigent.tools.manager import ToolManager


def test_question_available_to_main_and_native_agents():
    spec = AgentSpec(spec_version=1)
    assert "sys_ask_user" in {s["function"]["name"] for s in ToolManager(spec).get_tool_schemas()}
    for value in (spec, None):
        assert "sys_ask_user" in {s["name"] for s in build_native_relay_tool_schemas(value)}


@pytest.mark.parametrize("builder", [build_instructions, build_instructions_nullable])
def test_question_rule_only_injected_when_callable(builder):
    spec = AgentSpec(spec_version=1)
    assert "sys_ask_user" not in (builder(spec, None, []) or "")
    assert "sys_ask_user" in builder(spec, None, [SysAskUserTool().get_schema()])


@pytest.mark.parametrize(
    "questions",
    [
        [],
        [{"id": "x", "question": " "}],
        [{"id": "x", "question": "One?"}, {"id": "x", "question": "Two?"}],
    ],
)
def test_invalid_question_contract_is_rejected(questions):
    with pytest.raises(ValidationError):
        AskUserRequest(questions=questions)


def test_free_text_and_sensitive_question_shape():
    body = AskUserRequest(questions=[{"id": "path", "question": "Which path?", "sensitive": True}])
    question = body.card_payload()["questions"][0]
    assert question["options"] == []
    assert question["isSecret"] is True
