"""Delivery-state probing for effect-unknown reconciliation.

The plan forbids blind replay of an unacknowledged side effect: before a
delivery that crashed mid-flight is retried, the control plane must first
find out what actually happened. This module turns "what we can honestly
determine" into a three-valued verdict so recovery can replay only what is
provably safe and escalate everything else.

The default probe asks the runner whether the recipient session exists and
whether it is mid-turn. That is a real signal rather than a guess, but it is
deliberately narrow: a session that exists and is idle proves nothing about
a request that went missing minutes ago, so it reports UNKNOWN and the
delivery is escalated instead of replayed.

Deliberately out of scope here, and still open against the plan: inspecting
git HEAD/dirty state to see whether a worker already produced changes, and
replaying a vendor transcript to confirm injection. Both need workspace and
environment binding the coordination layer does not own yet.
"""

from __future__ import annotations

import logging
from enum import Enum

_logger = logging.getLogger(__name__)


class ProbeVerdict(str, Enum):
    """What a probe could establish about a vanished delivery."""

    #: Positive evidence the injection never landed, so replaying is safe.
    NOT_INJECTED = "not_injected"
    #: Positive evidence the injection landed; replaying would duplicate it.
    INJECTED = "injected"
    #: Evidence is missing or ambiguous — escalate, never replay.
    UNKNOWN = "unknown"


async def probe_delivery_state(
    recipient_session_id: str,
    *,
    timeout_s: float = 5.0,
) -> ProbeVerdict:
    """Ask the runner what it knows about a recipient session.

    The verdict is conservative by construction:

    * runner unbound or offline → UNKNOWN: the runner we would ask is not the
      one that may have received the request, so it can testify to nothing.
    * runner online but the session is unknown to it → NOT_INJECTED: the
      runner never initialised the session, so an injection could not have
      reached an agent. (A restarted runner would have failed earlier with
      "runner offline", so a 404 here is a real answer, not a gap.)
    * session busy with a turn → INJECTED: something is running, and a second
      injection would duplicate work or interrupt it.
    * session idle → UNKNOWN: says nothing about a request sent minutes ago.
    """
    try:
        from agentnexus.server.routes._sessions.common import get_server_runner_router

        router = get_server_runner_router()
        if router is None:
            return ProbeVerdict.UNKNOWN
        routed = router.client_for_session_resources(recipient_session_id)
    except Exception:  # noqa: BLE001
        _logger.debug("delivery probe could not route to a runner for %s", recipient_session_id)
        return ProbeVerdict.UNKNOWN

    try:
        resp = await routed.client.get(f"/v1/sessions/{recipient_session_id}", timeout=timeout_s)
    except Exception:  # noqa: BLE001
        _logger.debug("delivery probe request failed for %s", recipient_session_id)
        return ProbeVerdict.UNKNOWN

    if resp.status_code == 404:
        return ProbeVerdict.NOT_INJECTED
    if not 200 <= resp.status_code < 300:
        return ProbeVerdict.UNKNOWN
    try:
        status = (resp.json() or {}).get("status")
    except Exception:  # noqa: BLE001
        return ProbeVerdict.UNKNOWN
    if status == "running":
        return ProbeVerdict.INJECTED
    return ProbeVerdict.UNKNOWN
