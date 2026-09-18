"""Ordinary child questions, distinct from permission and policy approvals."""

from __future__ import annotations

from typing import Any


def ordinary_question(
    event: dict[str, Any],
    *,
    allow_sensitive: bool = False,
) -> dict[str, Any] | None:
    params = event.get("params")
    if not isinstance(params, dict):
        return None
    phase = params.get("phase")
    supported = phase in ("codex_request_user_input", "agy_ask_question", "user_question") or (
        phase == "pre_tool_use"
        and params.get("policy_name") == "claude_native_permission"
        and str(params.get("content_preview", "")).startswith("AskUserQuestion(")
    )
    if not supported:
        return None
    payload = params.get("ask_user_question")
    if not isinstance(payload, dict) or not isinstance(payload.get("questions"), list):
        return None
    questions = payload["questions"]
    if not questions or any(
        not isinstance(q, dict)
        or not isinstance(q.get("question"), str)
        or not q["question"].strip()
        or (q.get("isSecret") is True and not allow_sensitive)
        for q in questions
    ):
        return None
    return payload


def validate_answers(
    event: dict[str, Any],
    answers: object,
    *,
    allow_sensitive: bool = False,
) -> dict[str, Any]:
    question = ordinary_question(event, allow_sensitive=allow_sensitive)
    if question is None:
        raise ValueError("Human approval required; this is not a supported ordinary question")
    if not isinstance(answers, dict) or not answers:
        raise ValueError("answers must contain all requested question answers")
    remaining = set(answers)
    for entry in question["questions"]:
        keys = {entry["question"]}
        if isinstance(entry.get("id"), str) and entry["id"]:
            keys.add(entry["id"])
        selected = keys.intersection(remaining)
        if len(selected) != 1:
            raise ValueError(
                "Provide exactly one answer per question, keyed by id or question text"
            )
        key = selected.pop()
        remaining.remove(key)
        value = answers[key]
        values = value if isinstance(value, list) else [value]
        if not values or any(not isinstance(v, str) or not v.strip() for v in values):
            raise ValueError("Each answer must be a non-empty string or list of strings")
    if remaining:
        raise ValueError("Unexpected answer keys; permission flags are not accepted")
    return answers
