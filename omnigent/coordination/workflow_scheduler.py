"""Periodic recovery for coordination workflow dispatch.

The durable outbox owns delivery once a message is queued; this scheduler
owns the recovery gap *before* that happens: a running stage whose request
was lost during a crash, an explicit reassignment, or a torn transaction is
re-queued by the workflow engine. It never replays consumed work and never
touches paused/cancelled/succeeded runs.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging

from omnigent.coordination.workflow_engine import CoordinationWorkflowEngine

_logger = logging.getLogger(__name__)


class CoordinationWorkflowScheduler:
    """Background poller that lets the control plane recover missing dispatch."""

    def __init__(
        self,
        engine: CoordinationWorkflowEngine,
        *,
        interval_s: float = 1.0,
    ) -> None:
        self.engine = engine
        self.interval_s = interval_s
        self._running = False
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        """Start the recovery poll loop."""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._poll_loop())
        _logger.info("CoordinationWorkflowScheduler started")

    async def stop(self) -> None:
        """Stop the recovery poll loop."""
        self._running = False
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        _logger.info("CoordinationWorkflowScheduler stopped")

    async def _poll_loop(self) -> None:
        while self._running:
            try:
                healed = await self.engine.reconcile_missing_dispatches()
                if healed:
                    _logger.info("workflow recovery queued %s dispatch(es)", healed)
            except Exception:  
                _logger.exception("Error in workflow recovery loop")
            await asyncio.sleep(self.interval_s)

    async def sync_once(self) -> int:
        """Run one recovery pass; primarily used by tests."""
        return await self.engine.reconcile_missing_dispatches()
