"""Shared JEV transport, finite answer parsing and bounded question batches."""

from __future__ import annotations

import math
from types import SimpleNamespace
from typing import Any

try:
    from .jev_gates import STATE_TOKEN_BUDGET, _estimate_value_tokens
    from .jev_gates import _find_api_key as _find_api_key
    from .jev_transport import open_client as open_client
except ImportError:
    from jev_gates import STATE_TOKEN_BUDGET, _estimate_value_tokens
    from jev_gates import _find_api_key as _find_api_key
    from jev_transport import open_client as open_client

UNATTRIBUTED = "unclear"
MIN_MARGIN = 0.15


def _ask_batched(client: Any, state: Any, questions: dict[str, Any], model: str) -> Any:
    """Ask independent questions in as few requests as fit JEV's input limit.

    Every question repeats the roster in its criteria, so one request per
    episode overflows the limit. Questions in one request cannot see each
    other's answers, so splitting them changes only the request count.
    """
    room = max(STATE_TOKEN_BUDGET - _estimate_value_tokens(state), 1)
    batches: list[dict[str, Any]] = []
    current: dict[str, Any] = {}
    used = 0
    for name, question in questions.items():
        cost = _estimate_value_tokens(question.model_dump(exclude_none=True))
        if current and used + cost > room:
            batches.append(current)
            current, used = {}, 0
        current[name] = question
        used += cost
    if current:
        batches.append(current)
    answers: dict[str, Any] = {}
    response_model = model
    for batch in batches:
        response = client.system_one(state=state, questions=batch, model=model)
        answers.update(getattr(response, "answers", None) or {})
        response_model = getattr(response, "model", response_model)
    return SimpleNamespace(answers=answers, model=response_model)


def _unit_interval(value: Any) -> float | None:
    """``value`` as a probability, or None unless it is a finite number in [0, 1].

    NaN, infinity, booleans and strings are not finite probabilities.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) and 0.0 <= number <= 1.0 else None


def _choice(answer: Any, allowed: set[str]) -> tuple[str, float, dict[str, float], str | None]:
    """Read a Choice answer; an option outside ``allowed`` is not an answer.

    A confidence or probability that is not a finite number in [0, 1] makes the
    whole answer unresolved (``malformed_answer``) rather than being clamped.
    """
    if answer is None:
        return UNATTRIBUTED, 0.0, {}, "missing_answer"
    raw = str(getattr(answer, "choice", UNATTRIBUTED))
    reported_confidence = getattr(answer, "confidence", None)
    confidence = 0.0 if reported_confidence is None else _unit_interval(reported_confidence)
    reported = getattr(answer, "probabilities", None)
    malformed = confidence is None or not (reported is None or isinstance(reported, dict))
    probabilities: dict[str, float] = {}
    for option, value in reported.items() if isinstance(reported, dict) else ():
        probability = _unit_interval(value)
        if probability is None:
            malformed = True
        else:
            probabilities[str(option)] = probability
    if malformed:
        return UNATTRIBUTED, 0.0, probabilities, "malformed_answer"
    if raw not in allowed:
        return UNATTRIBUTED, 0.0, probabilities, f"unknown_option:{raw}"
    return raw, confidence, probabilities, None


def _ambiguous(choice: str, probabilities: dict[str, float]) -> bool:
    """True when the runner-up option is nearly as likely as the choice."""
    if choice not in probabilities:
        return False
    others = [p for k, p in probabilities.items() if k != choice and k != UNATTRIBUTED]
    return bool(others) and probabilities[choice] - max(others) < MIN_MARGIN
