"""Refuse a final message that cites identifiers no tool ever returned.

A model can answer a multi-step request with a report of work it never did:
no tool calls, and receipts, node ids and error codes that look real. The
identifiers are what give such a report away: a job id, a node id, an error
code or a content hash is copied from a tool's output, never composed. So each
one the final message cites must appear earlier in the conversation, in a tool
output or a user message. The agent's own earlier messages do not count: a
fabrication must not vouch for the next one. A turn that made no tool calls may
cite only what the user wrote: reusing real ids from earlier turns is how a
report of work that never happened looks genuine.

Wording is not checked — only identifiers, which are either in the record or
not. A message that cites none passes.

Configure on the ``response`` phase::

    policies:
      grounded_report:
        type: function
        on: [response]
        function:
          path: agentnexus.policies.builtins.grounding.cited_evidence
          arguments: {}
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable

from agentnexus.policies.schema import PolicyEvent, PolicyResponse

_ALLOW: PolicyResponse = {"result": "ALLOW"}

DEFAULT_PATTERNS = (
    r"\b[a-z]{2,8}_[0-9a-f]{12,}\b",  # prefixed ids: job_…, node_…, ast_…, proj_…
    r"\b[A-Z][A-Z0-9]+(?:_[A-Z0-9]+){2,}\b",  # error codes: CINE_PROMPT_MISMATCH
    r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b",  # uuids
    r"\b[0-9a-f]{40,64}\b",  # sha1 / sha256 digests
)
DEFAULT_MESSAGE = (
    "This reply cites {cited} that no tool returned and no user message contains. "
    "{turn}Do the work with tools and report only what they returned."
)
MAX_LISTED = 8


def _sources(trajectory: list[dict[str, object]], *, tools: bool = True) -> str:
    """Tool outputs and user messages: what a report may quote from."""
    parts = []
    for item in trajectory:
        if item.get("type") == "function_call_output" and tools:
            parts.append(str(item.get("output") or ""))
        elif item.get("type") == "message" and item.get("role") != "assistant":
            parts.append(str(item.get("text") or ""))
    return "\n".join(parts)


def _calls_this_turn(trajectory: list[dict[str, object]]) -> int:
    """Tool calls since the last user message."""
    calls = 0
    for item in reversed(trajectory):
        if item.get("type") == "message" and item.get("role") == "user":
            break
        calls += item.get("type") == "function_call"
    return calls


def cited_evidence(
    patterns: Iterable[str] = DEFAULT_PATTERNS,
    ignore: Iterable[str] = (),
    message: str = DEFAULT_MESSAGE,
    no_calls_note: str = "This turn made no tool calls. ",
) -> Callable[[PolicyEvent], PolicyResponse]:
    """Build the policy.

    :param patterns: Regexes for identifiers a report can only have copied.
    :param ignore: Identifiers that may be cited without a source, e.g. ones
        the agent's own instructions name.
    :param message: Denial text; ``{cited}`` lists the unsourced identifiers,
        ``{turn}`` is ``no_calls_note`` when the turn made no tool calls.
    :param no_calls_note: Inserted into ``message`` for a turn with no calls.
    """
    compiled = [re.compile(pattern) for pattern in patterns]
    ignored = frozenset(ignore)

    def evaluate(event: PolicyEvent) -> PolicyResponse:
        trajectory = event.get("trajectory")
        if event.get("type") != "response" or not isinstance(trajectory, list):
            return _ALLOW
        text = str(event.get("data") or "")
        cited = sorted({m.group(0) for rx in compiled for m in rx.finditer(text)} - ignored)
        if not cited:
            return _ALLOW
        called = _calls_this_turn(trajectory) > 0
        # A turn with no calls reports no new work: earlier tool output is what a
        # made-up "done" report would reuse, so only the user's words count.
        sources = _sources(trajectory, tools=called)
        unsourced = [token for token in cited if token not in sources]
        if not unsourced:
            return _ALLOW
        listed = ", ".join(unsourced[:MAX_LISTED])
        if len(unsourced) > MAX_LISTED:
            listed += f" (+{len(unsourced) - MAX_LISTED})"
        turn = "" if called else no_calls_note
        return {"result": "DENY", "reason": message.format(cited=listed, turn=turn)}

    evaluate.needs_trajectory = True  # type: ignore[attr-defined]
    return evaluate
