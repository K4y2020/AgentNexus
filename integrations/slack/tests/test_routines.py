from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from agentnexus_slack.routines import RoutineCompletionPoller
from agentnexus_slack.store import SQLiteStore
from fakes import RecordingSlackClient


class FakeAgentNexus:
    def __init__(self, tasks: list[dict[str, Any]]) -> None:
        self.tasks = tasks
        self.latest = ("item_1", "Routine result summary.")

    async def list_scheduled_tasks(self) -> list[dict[str, Any]]:
        return self.tasks

    async def latest_assistant_message(self, session_id: str) -> tuple[str, str] | None:
        return self.latest


class FakePool:
    def __init__(self, client: FakeAgentNexus) -> None:
        self.client = client
        self.requested: list[str] = []

    async def get(self, server_url: str, user_id: str = "") -> FakeAgentNexus:
        self.requested.append(user_id)
        return self.client


async def _store(tmp_path: Path) -> SQLiteStore:
    store = SQLiteStore(tmp_path / "store.sqlite3")
    await store.initialize()
    await store.upsert_channel_binding(
        "T1",
        "C1",
        agent_id="ag_1",
        agent_name="Debby",
        workspace="/tmp/work",
        owner_user_id="U1",
    )
    return store


def _task(
    *, name: str = "Daily standup", status: str = "succeeded", run_at: float
) -> dict[str, Any]:
    return {
        "id": "task_1",
        "name": name,
        "agent_id": "ag_1",
        "last_run_status": status,
        "last_run_at": run_at,
        "last_run_conversation_id": "conv_1",
    }


async def test_poller_delivers_fresh_routine_to_bound_channel(tmp_path: Path) -> None:
    store = await _store(tmp_path)
    slack = RecordingSlackClient()
    omnigent = FakeAgentNexus([_task(run_at=time.time())])
    pool = FakePool(omnigent)
    poller = RoutineCompletionPoller(
        store=store,
        pool=pool,  # type: ignore[arg-type]
        server_url="http://omnigent.test",
        slack_client=slack,
        interval_seconds=60.0,
    )

    await poller.start()
    await poller.stop()

    assert len(slack.posts) == 1
    post = slack.posts[0]
    assert post["channel"] == "C1"
    assert "Daily standup" in post["text"]
    assert "Routine result summary." in post["text"]
    # Polling authenticated as the binding owner, not a blank identity.
    assert pool.requested == ["T1:U1"]


async def test_poller_dedupes_a_delivered_run(tmp_path: Path) -> None:
    store = await _store(tmp_path)
    slack = RecordingSlackClient()
    omnigent = FakeAgentNexus([_task(run_at=time.time())])
    pool = FakePool(omnigent)
    poller = RoutineCompletionPoller(
        store=store,
        pool=pool,  # type: ignore[arg-type]
        server_url="http://omnigent.test",
        slack_client=slack,
        interval_seconds=60.0,
    )

    await poller.start()
    await poller.deliver_once()
    await poller.stop()

    assert len(slack.posts) == 1


async def test_poller_marks_preexisting_runs_without_spamming(tmp_path: Path) -> None:
    store = await _store(tmp_path)
    slack = RecordingSlackClient()
    omnigent = FakeAgentNexus([_task(run_at=time.time() - 3600)])
    pool = FakePool(omnigent)
    poller = RoutineCompletionPoller(
        store=store,
        pool=pool,  # type: ignore[arg-type]
        server_url="http://omnigent.test",
        slack_client=slack,
        interval_seconds=60.0,
    )

    await poller.start()
    await poller.deliver_once()
    await poller.stop()

    assert slack.posts == []
