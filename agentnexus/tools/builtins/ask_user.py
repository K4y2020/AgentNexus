"""Unified, structured questions using the existing chat elicitation surface."""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from agentnexus.tools.base import Tool

QUESTION_INSTRUCTIONS = (
    "When a user decision or missing information is required to proceed, call sys_ask_user "
    "to show an interactive question; do not substitute numbered Markdown choices. "
    "Use available verified context before asking. Wait for an answered result; "
    "declined, cancelled, unanswered or invalid_answer does not authorize any option. "
    "Continue the original task after the answer. Permission approvals remain separate."
)


def question_instructions(tool_schemas: list[dict[str, Any]]) -> tuple[str, ...]:
    return (
        (QUESTION_INSTRUCTIONS,)
        if any(schema.get("function", {}).get("name") == "sys_ask_user" for schema in tool_schemas)
        else ()
    )


class QuestionOption(BaseModel):
    model_config = ConfigDict(extra="forbid")
    label: str = Field(min_length=1, max_length=100)
    description: str = Field(default="", max_length=500)


class UserQuestion(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1, max_length=100)
    question: str = Field(min_length=1, max_length=2000)
    options: list[QuestionOption] = Field(default_factory=list, max_length=6)
    multi_select: bool = False
    sensitive: bool = False


class AskUserRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    questions: list[UserQuestion] = Field(min_length=1, max_length=3)
    a2a_request_id: str | None = None

    @model_validator(mode="after")
    def distinct_questions(self):
        ids = [q.id.strip() for q in self.questions]
        texts = [q.question.strip() for q in self.questions]
        if not all(ids) or not all(texts) or len(set(ids + texts)) != len(ids + texts):
            raise ValueError("Question IDs and texts must be non-empty and unambiguous")
        for q in self.questions:
            labels = [option.label.strip() for option in q.options]
            if not all(labels) or len(set(labels)) != len(labels):
                raise ValueError("Option labels must be non-empty and unique")
        return self

    def card_payload(self) -> dict[str, Any]:
        return {
            "questions": [
                {
                    "id": q.id,
                    "question": q.question,
                    "options": [o.model_dump() for o in q.options],
                    "multiSelect": q.multi_select,
                    "isSecret": q.sensitive,
                }
                for q in self.questions
            ]
        }


class SysAskUserTool(Tool):
    @classmethod
    def name(cls) -> str:
        return "sys_ask_user"

    @classmethod
    def description(cls) -> str:
        return (
            "Ask for a user decision or missing information using the interactive chat card. "
            "Use this instead of writing numbered choices in a final message when an answer "
            "is needed to continue. Waits for answers in the current session; never creates "
            "a new topic. Empty options allow free text. This is NOT a permission approval: "
            "do not use it to bypass tool policies. In A2A work include the originating "
            "a2a_request_id so the question is shown in the sender's chat."
        )

    def get_schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name(),
                "description": self.description(),
                "parameters": AskUserRequest.model_json_schema(),
            },
        }
