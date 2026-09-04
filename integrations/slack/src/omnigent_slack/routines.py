from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from typing import Any

from omnigent_slack.auth_manager import pack_user_key
from omnigent_slack.omnigent import OmnigentClientPool
from omnigent_slack.store import SQLiteStore

# Run states the scheduler treats as finished work. ``running``/``scheduled``
# are still pending and have nothing to report.
_TERMINAL_STATUSES = frozenset({"succeeded", "failed", "skipped", "incomplete"})

_TERMINAL_LABELS = {
    "succeeded": (":white_check_mark:", "completed"),
    "failed": (":x:", "failed"),
    "skipped": (":fast_forward:", "skipped"),
    "incomplete": (":warning:", "stopped incomplete"),
}


class RoutineCompletionPoller:
    """Poll scheduled tasks and post finished runs to their bound Slack channel.

    A channel binding makes an agent a resident teammate; this poller closes the
    loop so a routine fired for that agent also lands back in the channel. Runs
    are only fresh if they finished after the poller started, so a bot restart
    never spams a channel with every historical routine completion.
    """

    def __init__(
        self,
        *,
        store: SQLiteStore,
        pool: OmnigentClientPool,
        server_url: str,
        slack_client: Any,
        interval_seconds: float = 60.0,
    ) -> None:
        self._store = store
        self._pool = pool
        self._server_url = server_url
        self._slack_client = slack_client
        self._interval_seconds = interval_seconds
        self._started_at = 0.0
        self._task: asyncio.Task[None] | None = None
        self._logger = logging.getLogger(__name__)

    async def start(self) -> None:
        """Mark pre-existing runs as seen and begin the polling loop."""
        self._started_at = time.time()
        try:
            await self.deliver_once()
        except Exception:
            self._logger.exception("Initial routine-completion poll failed")
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._task is None:
            return
        task, self._task = self._task, None
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    async def _run(self) -> None:
        while True:
            await asyncio.sleep(self._interval_seconds)
            try:
                await self.deliver_once()
            except Exception:
                self._logger.exception("Routine-completion poll failed")

    async def deliver_once(self) -> int:
        """Deliver newly-finished routines; returns how many Slack posts were made."""
        bindings = await self._store.list_channel_bindings()
        delivered = 0
        for binding in bindings:
            user_key = pack_user_key(binding.team_id, binding.owner_user_id)
            try:
                omnigent = await self._pool.get(self._server_url, user_key)
                tasks = await omnigent.list_scheduled_tasks()
            except Exception as exc:
                self._logger.warning(
                    "Could not poll routines team=%s channel=%s agent=%s error=%s",
                    binding.team_id,
                    binding.channel_id,
                    binding.agent_id,
                    exc,
                )
                continue
            for task in tasks:
                if task.get("agent_id") != binding.agent_id:
                    continue
                status = str(task.get("last_run_status") or "")
                if status not in _TERMINAL_STATUSES:
                    continue
                run_at = _as_timestamp(task.get("last_run_at"))
                if run_at is None:
                    continue
                conversation_id = task.get("last_run_conversation_id")
                run_key = (
                    str(conversation_id)
                    if isinstance(conversation_id, str) and conversation_id
                    else f"no-conv:{run_at:.6f}"
                )
                if not await self._store.mark_run_delivered(
                    binding.team_id,
                    binding.channel_id,
                    str(task.get("id") or ""),
                    run_key,
                ):
                    continue
                is_fresh = run_at >= self._started_at
                if not is_fresh:
                    # Already-seen history: claimed, but never posted.
                    continue
                try:
                    await self._slack_client.chat_postMessage(
                        channel=binding.channel_id,
                        text=await self._completion_text(omnigent, task, status),
                    )
                except Exception as exc:
                    await self._store.unmark_run_delivered(
                        binding.team_id,
                        binding.channel_id,
                        str(task.get("id") or ""),
                        run_key,
                    )
                    self._logger.warning(
                        "Could not post routine completion channel=%s task=%s error=%s",
                        binding.channel_id,
                        task.get("id"),
                        exc,
                    )
                    continue
                delivered += 1
        return delivered

    async def _completion_text(
        self,
        omnigent: Any,
        task: dict[str, Any],
        status: str,
    ) -> str:
        icon, label = _TERMINAL_LABELS.get(status, (":white_check_mark:", status))
        task_name = str(task.get("name") or task.get("id") or "routine")
        text = f"{icon} *{task_name}* - routine finished ({label})."
        conversation_id = task.get("last_run_conversation_id")
        if not isinstance(conversation_id, str) or not conversation_id:
            return text
        try:
            latest = await omnigent.latest_assistant_message(conversation_id)
        except Exception:
            return text
        if latest is None:
            return text
        preview = " ".join(str(latest[1]).split())
        if preview:
            text += f"\n> {preview[:280]}"
        return text


def _as_timestamp(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str) and value:
        try:
            return float(value)
        except ValueError:
            return None
    return None
