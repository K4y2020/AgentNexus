"""Tests for Sessions API CRUD endpoints (list, get, delete, patch).

Exercises the core session management routes through the ``client``
fixture. Since the lifespan event (which seeds agents) does not run
in test fixtures, we seed a test agent and conversation directly via
the stores.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import httpx
import pytest
import pytest_asyncio

from agentnexus.db.utils import generate_agent_id
from agentnexus.entities import USER_SESSION_TITLE_MAX_CHARS
from agentnexus.server.routes import sessions as sessions_module
from agentnexus.stores.agent_store.sqlalchemy_store import SqlAlchemyAgentStore
from agentnexus.stores.bot_store.sqlalchemy_store import SqlAlchemyBotStore
from agentnexus.stores.conversation_store.sqlalchemy_store import (
    SqlAlchemyConversationStore,
)


@pytest_asyncio.fixture()
async def session_id(db_uri: str) -> str:
    """Seed a test agent and conversation, return the session ID."""
    agent_store = SqlAlchemyAgentStore(db_uri)
    conv_store = SqlAlchemyConversationStore(db_uri)
    agent_id = generate_agent_id()
    agent_store.create(agent_id, name="test-agent", bundle_location="test:///bundle")
    conv = conv_store.create_conversation(agent_id=agent_id)
    return conv.id


# ── GET /v1/sessions (list) ─────────────────────────────────────────


async def test_list_sessions_empty(client: httpx.AsyncClient) -> None:
    """Empty database returns an empty list."""
    resp = await client.get("/v1/sessions")
    assert resp.status_code == 200
    body = resp.json()
    assert body["data"] == []
    assert body["has_more"] is False


async def test_list_sessions_after_create(
    client: httpx.AsyncClient,
    session_id: str,
) -> None:
    """A created session appears in the list."""
    resp = await client.get("/v1/sessions")
    assert resp.status_code == 200
    body = resp.json()
    ids = [s["id"] for s in body["data"]]
    assert session_id in ids


async def test_bot_session_uses_durable_home_and_singleton_slot(
    client: httpx.AsyncClient,
    db_uri: str,
) -> None:
    agent_store = SqlAlchemyAgentStore(db_uri)
    bot_store = SqlAlchemyBotStore(db_uri)
    agent_id = generate_agent_id()
    agent = agent_store.create(
        agent_id,
        name="polly",
        bundle_location="test:///bundle",
    )
    bot = bot_store.ensure_for_agent(
        owner_id="local",
        agent_id=agent.id,
        name=agent.name,
        description=agent.description,
    )
    configured = bot_store.update_settings(
        bot.id,
        owner_id="local",
        name=bot.name,
        description=bot.description,
        status="active",
        default_model="gpt-5.6-luna",
        behavior_mode="lean",
    )
    assert configured is not None

    response = await client.post(
        "/v1/sessions",
        json={
            "agent_id": agent.id,
            "bot_id": bot.id,
            "purpose": "primary",
            "workspace": "U:/wrong/recent/project",
            "labels": {"agentnexus.teammate.primary": "true"},
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["bot_id"] == bot.id
    assert body["purpose"] == "primary"
    from pathlib import Path

    assert Path(body["workspace"]).name == body["id"]
    assert Path(body["workspace"]).parent.name == "topics"
    assert body["labels"]["agentnexus.workspace_layout"] == "topic-v1"
    assert body["model_override"] == "gpt-5.6-luna"
    assert body["labels"]["agentnexus.behavior_mode"] == "lean"

    topic_a2a = await client.post(
        "/v1/sessions",
        json={
            "agent_id": agent.id,
            "bot_id": bot.id,
            "purpose": "a2a",
            "labels": {
                "agentnexus.teammate.channel": "a2a",
                "agentnexus.teammate.channel_scope": "topic:topic_a",
                "agentnexus.teammate.channel_kind": "topic",
                "agentnexus.teammate.channel_source": "topic_a",
            },
        },
    )
    assert topic_a2a.status_code == 201, topic_a2a.text
    topic_b2a = await client.post(
        "/v1/sessions",
        json={
            "agent_id": agent.id,
            "bot_id": bot.id,
            "purpose": "a2a",
            "labels": {
                "agentnexus.teammate.channel": "a2a",
                "agentnexus.teammate.channel_scope": "topic:topic_b",
                "agentnexus.teammate.channel_kind": "topic",
                "agentnexus.teammate.channel_source": "topic_b",
            },
        },
    )
    assert topic_b2a.status_code == 201, topic_b2a.text
    workspaces = [body["workspace"], topic_a2a.json()["workspace"], topic_b2a.json()["workspace"]]
    assert len(set(workspaces)) == 3
    for response_body in (body, topic_a2a.json(), topic_b2a.json()):
        assert Path(response_body["workspace"]).name == response_body["id"]

    child = await client.post(
        "/v1/sessions",
        json={
            "agent_id": agent.id,
            "parent_session_id": body["id"],
            "title": "worker:one",
        },
    )
    assert child.status_code == 201, child.text
    assert child.json()["bot_id"] == bot.id
    assert child.json()["purpose"] == "subagent"

    duplicate = await client.post(
        "/v1/sessions",
        json={
            "agent_id": agent.id,
            "bot_id": bot.id,
            "purpose": "primary",
        },
    )
    assert duplicate.status_code == 409

    archived = await client.patch(
        f"/v1/sessions/{body['id']}",
        json={"archived": True},
    )
    assert archived.status_code == 409
    deleted = await client.delete(f"/v1/sessions/{body['id']}")
    assert deleted.status_code == 409


async def test_list_sessions_pagination(
    client: httpx.AsyncClient,
    db_uri: str,
) -> None:
    """Pagination with limit returns at most N sessions."""
    agent_store = SqlAlchemyAgentStore(db_uri)
    conv_store = SqlAlchemyConversationStore(db_uri)
    agent_id = generate_agent_id()
    agent_store.create(agent_id, name="pag-agent", bundle_location="test:///bundle")
    conv_store.create_conversation(agent_id=agent_id)
    conv_store.create_conversation(agent_id=agent_id)

    resp = await client.get("/v1/sessions?limit=1")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["data"]) == 1


# ── GET /v1/sessions/{id} (get snapshot) ────────────────────────────


async def test_get_session(
    client: httpx.AsyncClient,
    session_id: str,
) -> None:
    """Get a session by ID returns its snapshot."""
    resp = await client.get(f"/v1/sessions/{session_id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == session_id


async def test_get_session_not_found(client: httpx.AsyncClient) -> None:
    """Getting a nonexistent session returns 404."""
    resp = await client.get("/v1/sessions/4fe12335002377c209e501c3fe3bcffc")
    assert resp.status_code == 404


# ── DELETE /v1/sessions/{id} ────────────────────────────────────────


async def test_delete_session(
    client: httpx.AsyncClient,
    session_id: str,
) -> None:
    """Deleting a session returns 200 with deleted: true."""
    resp = await client.delete(f"/v1/sessions/{session_id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["deleted"] is True


async def test_delete_session_not_found(client: httpx.AsyncClient) -> None:
    """Deleting a nonexistent session returns 404."""
    resp = await client.delete("/v1/sessions/4fe12335002377c209e501c3fe3bcffc")
    assert resp.status_code == 404


async def test_delete_running_session_attempts_stop(
    client: httpx.AsyncClient,
    session_id: str,
) -> None:
    """Deleting a running session calls ``_stop_session_via_runner``."""
    mock_stop = AsyncMock(return_value=True)
    sessions_module._session_status_cache[session_id] = "running"
    try:
        with patch.object(sessions_module, "_stop_session_via_runner", mock_stop):
            resp = await client.delete(f"/v1/sessions/{session_id}")
        assert resp.status_code == 200
        assert resp.json()["deleted"] is True
        mock_stop.assert_awaited_once()
    finally:
        sessions_module._session_status_cache.pop(session_id, None)


async def test_delete_idle_session_with_background_tasks_attempts_stop(
    client: httpx.AsyncClient,
    session_id: str,
) -> None:
    """An idle session with live background shells is still stopped.

    Regression test: the sidebar rollup deliberately reads such a session
    as ``idle`` — the turn ended and it takes a new message immediately —
    so a stop gate keyed on that rollup alone would skip the runner and
    leave the shells running past the delete.
    """
    mock_stop = AsyncMock(return_value=True)
    sessions_module._session_status_cache[session_id] = "idle"
    sessions_module._session_background_task_count_cache[session_id] = 1
    try:
        with patch.object(sessions_module, "_stop_session_via_runner", mock_stop):
            resp = await client.delete(f"/v1/sessions/{session_id}")
        assert resp.status_code == 200
        assert resp.json()["deleted"] is True
        mock_stop.assert_awaited_once()
        assert mock_stop.await_args is not None
        assert mock_stop.await_args.args[0] == session_id
    finally:
        sessions_module._session_status_cache.pop(session_id, None)
        sessions_module._session_background_task_count_cache.pop(session_id, None)


async def test_delete_idle_parent_stops_running_child(
    client: httpx.AsyncClient,
    session_id: str,
    db_uri: str,
) -> None:
    """Deleting an idle parent with a running child stops the child.

    Regression test: ``_best_effort_stop`` previously used the child
    rollup only to decide whether to act, then always issued the stop
    against the parent's own session id. A parent that has already gone
    idle while its sub-agent child keeps running would get a no-op stop,
    then the recursive subtree delete would remove the child's row while
    its runner process kept running, orphaning it.
    """
    conv_store = SqlAlchemyConversationStore(db_uri)
    child = conv_store.create_conversation(
        kind="sub_agent",
        title="researcher:auth",
        parent_conversation_id=session_id,
    )

    mock_stop = AsyncMock(return_value=True)
    sessions_module._session_status_cache[child.id] = "running"
    try:
        with patch.object(sessions_module, "_stop_session_via_runner", mock_stop):
            resp = await client.delete(f"/v1/sessions/{session_id}")
        assert resp.status_code == 200
        assert resp.json()["deleted"] is True
        # The child must be the one stopped, not the (idle) parent.
        mock_stop.assert_awaited_once()
        assert mock_stop.await_args is not None
        assert mock_stop.await_args.args[0] == child.id
    finally:
        sessions_module._session_status_cache.pop(child.id, None)


async def test_delete_idle_parent_stops_running_grandchild(
    client: httpx.AsyncClient,
    session_id: str,
    db_uri: str,
) -> None:
    """Deleting an idle parent stops a running grandchild too.

    Regression test: ``_best_effort_stop`` used to walk only direct
    children, one level down. A sub-agent that itself spawns a sub-agent
    (parent -> child -> grandchild) with the child now idle but the
    grandchild still running was invisible to that one-level check, so
    the grandchild kept running unstopped -- the same bug as the direct-
    child case, just one generation deeper. ``delete_conversation``'s
    recursive subtree delete has no such depth limit, so the stop logic
    must match it.
    """
    conv_store = SqlAlchemyConversationStore(db_uri)
    child = conv_store.create_conversation(
        kind="sub_agent",
        title="researcher:auth",
        parent_conversation_id=session_id,
    )
    grandchild = conv_store.create_conversation(
        kind="sub_agent",
        title="researcher:citations",
        parent_conversation_id=child.id,
    )

    mock_stop = AsyncMock(return_value=True)
    sessions_module._session_status_cache[grandchild.id] = "running"
    try:
        with patch.object(sessions_module, "_stop_session_via_runner", mock_stop):
            resp = await client.delete(f"/v1/sessions/{session_id}")
        assert resp.status_code == 200
        assert resp.json()["deleted"] is True
        # The grandchild must be the one stopped, not the idle parent/child.
        mock_stop.assert_awaited_once()
        assert mock_stop.await_args is not None
        assert mock_stop.await_args.args[0] == grandchild.id
    finally:
        sessions_module._session_status_cache.pop(grandchild.id, None)


async def test_delete_proceeds_when_stop_fails(
    client: httpx.AsyncClient,
    session_id: str,
) -> None:
    """Delete succeeds even when the runner stop raises."""
    mock_stop = AsyncMock(side_effect=ConnectionError("runner gone"))
    sessions_module._session_status_cache[session_id] = "running"
    try:
        with patch.object(sessions_module, "_stop_session_via_runner", mock_stop):
            resp = await client.delete(f"/v1/sessions/{session_id}")
        assert resp.status_code == 200
        assert resp.json()["deleted"] is True
    finally:
        sessions_module._session_status_cache.pop(session_id, None)


async def test_delete_session_calls_full_runner_teardown(
    client: httpx.AsyncClient,
    session_id: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Server-side delete calls DELETE /v1/sessions/{id} on the runner.

    The old code called DELETE /v1/sessions/{id}/resources — the partial
    cleanup endpoint — which left session caches and the live comment relay
    alive after deletion. Full runner teardown must be invoked instead so
    nothing outlives the session.
    """
    deleted_paths: list[str] = []

    def _capture(request: httpx.Request) -> httpx.Response:
        if request.method == "DELETE":
            deleted_paths.append(request.url.path)
        return httpx.Response(200, json={"deleted": True})

    fake_runner = httpx.AsyncClient(
        transport=httpx.MockTransport(_capture),
        base_url="http://runner",
    )

    async def _get_runner(session_id: str) -> httpx.AsyncClient:
        return fake_runner

    monkeypatch.setattr(
        sessions_module,
        "_get_runner_client_for_resource_access",
        _get_runner,
    )
    try:
        resp = await client.delete(f"/v1/sessions/{session_id}")
        assert resp.status_code == 200
        assert resp.json()["deleted"] is True
    finally:
        await fake_runner.aclose()

    assert deleted_paths == [f"/v1/sessions/{session_id}"], (
        f"server-side delete should call full runner teardown, got: {deleted_paths}"
    )


# ── PATCH /v1/sessions/{id} ─────────────────────────────────────────


async def test_patch_session_title(
    client: httpx.AsyncClient,
    session_id: str,
) -> None:
    """Patching a session's title returns the updated session."""
    resp = await client.patch(
        f"/v1/sessions/{session_id}",
        json={"title": "New Title"},
        headers={"Content-Type": "application/json"},
    )
    assert resp.status_code == 200


async def test_patch_session_title_enforces_user_limit(
    client: httpx.AsyncClient,
    session_id: str,
) -> None:
    """Manual titles accept 200 characters and reject 201."""
    accepted = "x" * USER_SESSION_TITLE_MAX_CHARS
    resp = await client.patch(f"/v1/sessions/{session_id}", json={"title": accepted})
    assert resp.status_code == 200, resp.text
    assert resp.json()["title"] == accepted

    resp = await client.patch(f"/v1/sessions/{session_id}", json={"title": accepted + "x"})
    assert resp.status_code == 422, resp.text


async def test_patch_session_not_found(client: httpx.AsyncClient) -> None:
    """Patching a nonexistent session returns 404."""
    resp = await client.patch(
        "/v1/sessions/4fe12335002377c209e501c3fe3bcffc",
        json={"title": "New Title"},
        headers={"Content-Type": "application/json"},
    )
    assert resp.status_code == 404


# ── GET /v1/sessions/projects ────────────────────────────────────────


async def test_list_projects_empty(client: httpx.AsyncClient) -> None:
    """No project labels anywhere → empty project list."""
    resp = await client.get("/v1/sessions/projects")
    assert resp.status_code == 200
    assert resp.json() == []


async def test_list_projects_returns_names_sorted(
    client: httpx.AsyncClient,
    db_uri: str,
) -> None:
    """Projects surface as a sorted list of names."""
    conv_store = SqlAlchemyConversationStore(db_uri)
    a = conv_store.create_conversation()
    b = conv_store.create_conversation()
    conv_store.set_labels(a.id, {"omni_project": "Sprint 42"})
    conv_store.set_labels(b.id, {"omni_project": "Customer X"})

    resp = await client.get("/v1/sessions/projects")
    assert resp.status_code == 200
    # Label-only projects (no first-class row) list with id=None, sorted by name.
    assert resp.json() == [
        {"id": None, "name": "Customer X", "icon": None},
        {"id": None, "name": "Sprint 42", "icon": None},
    ]


# ── GET /v1/sessions?project= (filter) ───────────────────────────────


async def test_list_sessions_filtered_by_project(
    client: httpx.AsyncClient,
    db_uri: str,
) -> None:
    """``?project=X`` returns only sessions in that project."""
    agent_store = SqlAlchemyAgentStore(db_uri)
    conv_store = SqlAlchemyConversationStore(db_uri)
    # GET /v1/sessions filters has_agent_id=True, so bind the conversations to
    # a seeded agent — otherwise the list comes back empty.
    agent_id = generate_agent_id()
    agent_store.create(agent_id, name="project-agent", bundle_location="test:///bundle")
    filed = conv_store.create_conversation(agent_id=agent_id)
    conv_store.create_conversation(agent_id=agent_id)  # unfiled
    conv_store.set_labels(filed.id, {"omni_project": "X"})

    resp = await client.get("/v1/sessions?project=X")
    assert resp.status_code == 200
    ids = [s["id"] for s in resp.json()["data"]]
    assert ids == [filed.id]


async def test_list_sessions_empty_project_returns_unfiled(
    client: httpx.AsyncClient,
    db_uri: str,
) -> None:
    """``?project=`` (empty) returns only sessions with no project label."""
    agent_store = SqlAlchemyAgentStore(db_uri)
    conv_store = SqlAlchemyConversationStore(db_uri)
    agent_id = generate_agent_id()
    agent_store.create(agent_id, name="project-agent", bundle_location="test:///bundle")
    filed = conv_store.create_conversation(agent_id=agent_id)
    unfiled = conv_store.create_conversation(agent_id=agent_id)
    conv_store.set_labels(filed.id, {"omni_project": "X"})

    resp = await client.get("/v1/sessions?project=")
    assert resp.status_code == 200
    ids = [s["id"] for s in resp.json()["data"]]
    assert unfiled.id in ids
    assert filed.id not in ids


# ── PATCH /v1/sessions/{id} project label ────────────────────────────


async def test_patch_session_sets_project_label(
    client: httpx.AsyncClient,
    session_id: str,
    db_uri: str,
) -> None:
    """PATCH with ``labels: {project: X}`` upserts the project label."""
    resp = await client.patch(
        f"/v1/sessions/{session_id}",
        json={"labels": {"omni_project": "Sprint 42"}},
        headers={"Content-Type": "application/json"},
    )
    assert resp.status_code == 200

    conv_store = SqlAlchemyConversationStore(db_uri)
    conv = conv_store.get_conversation(session_id)
    assert conv is not None
    assert conv.labels.get("omni_project") == "Sprint 42"


async def test_patch_session_empty_project_removes_label(
    client: httpx.AsyncClient,
    session_id: str,
    db_uri: str,
) -> None:
    """PATCH with ``labels: {project: ""}`` removes the project label rather
    than persisting an empty value — so the session returns to Unfiled."""
    conv_store = SqlAlchemyConversationStore(db_uri)
    conv_store.set_labels(session_id, {"omni_project": "Sprint 42"})

    resp = await client.patch(
        f"/v1/sessions/{session_id}",
        json={"labels": {"omni_project": ""}},
        headers={"Content-Type": "application/json"},
    )
    assert resp.status_code == 200

    conv = conv_store.get_conversation(session_id)
    assert conv is not None
    assert "omni_project" not in conv.labels


# ── Pinned session label (omnigent.pinned) ───────────────────────────


async def test_patch_session_pins_and_unpins(
    client: httpx.AsyncClient,
    session_id: str,
    db_uri: str,
) -> None:
    """PATCH with the canonical ``labels: {"agentnexus.pinned": <pin-time>}`` pins
    the session for the CALLER: the server rewrites it to the per-user key
    ``omnigent.pinned.<user>`` in storage (so it doesn't pin for others), and an
    empty value deletes that per-user key (unpin)."""
    from agentnexus.stores.conversation_store import pinned_label_key

    conv_store = SqlAlchemyConversationStore(db_uri)
    # No auth header on this client ⇒ the single-user ``local`` identity.
    user_key = pinned_label_key(None)

    resp = await client.patch(
        f"/v1/sessions/{session_id}",
        json={"labels": {"agentnexus.pinned": "1721760000000"}},
        headers={"Content-Type": "application/json"},
    )
    assert resp.status_code == 200
    conv = conv_store.get_conversation(session_id)
    assert conv is not None
    # Stored under the per-user key, NOT the bare canonical key.
    assert conv.labels.get(user_key) == "1721760000000"
    assert "agentnexus.pinned" not in conv.labels
    # …but the response collapses it back to the canonical key for the caller.
    assert resp.json()["labels"].get("agentnexus.pinned") == "1721760000000"

    # Unpin: empty string clears the per-user key.
    resp = await client.patch(
        f"/v1/sessions/{session_id}",
        json={"labels": {"agentnexus.pinned": ""}},
        headers={"Content-Type": "application/json"},
    )
    assert resp.status_code == 200
    conv = conv_store.get_conversation(session_id)
    assert conv is not None
    assert user_key not in conv.labels


async def test_archiving_clears_the_callers_pin(
    client: httpx.AsyncClient,
    session_id: str,
    db_uri: str,
) -> None:
    """Archiving a session drops the caller's own pin: a pinned row shouldn't
    linger if the session is later unarchived. Only the requester's per-user key
    is cleared."""
    from agentnexus.stores.conversation_store import pinned_label_key

    conv_store = SqlAlchemyConversationStore(db_uri)
    user_key = pinned_label_key(None)

    # Pin, then archive.
    resp = await client.patch(
        f"/v1/sessions/{session_id}",
        json={"labels": {"agentnexus.pinned": "1721760000000"}},
        headers={"Content-Type": "application/json"},
    )
    assert resp.status_code == 200
    conv = conv_store.get_conversation(session_id)
    assert conv is not None
    assert conv.labels.get(user_key) == "1721760000000"

    resp = await client.patch(
        f"/v1/sessions/{session_id}",
        json={"archived": True},
        headers={"Content-Type": "application/json"},
    )
    assert resp.status_code == 200
    conv = conv_store.get_conversation(session_id)
    assert conv is not None
    assert conv.archived is True
    assert user_key not in conv.labels


async def test_archiving_wins_over_a_same_request_pin(
    client: httpx.AsyncClient,
    session_id: str,
    db_uri: str,
) -> None:
    """A single PATCH carrying both ``archived: true`` and a pin is
    contradictory; archive is authoritative. The pin-clear runs after the label
    upsert, so the session ends up archived and unpinned, not re-pinned."""
    from agentnexus.stores.conversation_store import pinned_label_key

    conv_store = SqlAlchemyConversationStore(db_uri)
    user_key = pinned_label_key(None)

    resp = await client.patch(
        f"/v1/sessions/{session_id}",
        json={"archived": True, "labels": {"agentnexus.pinned": "1721760000000"}},
        headers={"Content-Type": "application/json"},
    )
    assert resp.status_code == 200
    conv = conv_store.get_conversation(session_id)
    assert conv is not None
    assert conv.archived is True
    assert user_key not in conv.labels


async def test_patch_rejects_client_supplied_per_user_pin_key(
    client: httpx.AsyncClient,
    session_id: str,
    db_uri: str,
) -> None:
    """A client may only write the bare canonical ``omnigent.pinned`` key. A
    suffixed ``omnigent.pinned.<user>`` is server-derived — accepting one would
    let a caller pin/unpin a shared session for another user. It must be
    rejected, and nothing persisted."""
    conv_store = SqlAlchemyConversationStore(db_uri)

    for value in ("1721760000000", ""):
        resp = await client.patch(
            f"/v1/sessions/{session_id}",
            json={"labels": {"agentnexus.pinned.bob@example.com": value}},
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code == 400
    conv = conv_store.get_conversation(session_id)
    assert conv is not None
    assert "agentnexus.pinned.bob@example.com" not in conv.labels


async def test_patch_rejects_client_supplied_sandbox_labels(
    client: httpx.AsyncClient,
    session_id: str,
    db_uri: str,
) -> None:
    """The ``omnigent.sandbox.*`` namespace is server-internal — the server
    writes these labels (e.g. the repository a relaunch re-clones) and re-reads
    them to rebuild the runner's workspace. A client seed would forge that
    reconstruction state (e.g. redirect the relaunch clone), so every key under
    the prefix — the known ones and an unenumerated future key — must be rejected
    and nothing persisted."""
    conv_store = SqlAlchemyConversationStore(db_uri)

    for key in (
        "agentnexus.sandbox.agent",
        "agentnexus.sandbox.repo",
        "agentnexus.sandbox.future",
    ):
        resp = await client.patch(
            f"/v1/sessions/{session_id}",
            json={"labels": {key: "code-reviewer"}},
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code == 400
        conv = conv_store.get_conversation(session_id)
        assert conv is not None
        assert key not in conv.labels


async def test_list_sessions_pinned_filter(
    client: httpx.AsyncClient,
    db_uri: str,
) -> None:
    """``?pinned=true`` returns only sessions the CALLER pinned — matched by
    their per-user key. Another user's pin on a session does not surface."""
    from agentnexus.stores.conversation_store import pinned_label_key

    agent_store = SqlAlchemyAgentStore(db_uri)
    conv_store = SqlAlchemyConversationStore(db_uri)
    agent_id = generate_agent_id()
    agent_store.create(agent_id, name="pin-agent", bundle_location="test:///bundle")
    pinned = conv_store.create_conversation(agent_id=agent_id)
    plain = conv_store.create_conversation(agent_id=agent_id)
    other_user_pin = conv_store.create_conversation(agent_id=agent_id)
    # This client is unauthenticated ⇒ the ``local`` identity. Pin one session
    # under the caller's key and one under a different user's key.
    conv_store.set_labels(pinned.id, {pinned_label_key(None): "1721760000000"})
    conv_store.set_labels(other_user_pin.id, {pinned_label_key("someone-else"): "1721760000000"})

    resp = await client.get("/v1/sessions?pinned=true")
    assert resp.status_code == 200
    ids = [s["id"] for s in resp.json()["data"]]
    assert pinned.id in ids
    assert plain.id not in ids
    # A pin belonging to another user must not appear for the caller.
    assert other_user_pin.id not in ids


async def test_windows_native_codex_adapts_to_sdk(
    client: httpx.AsyncClient,
    db_uri: str,
) -> None:
    """On Windows, creating a session with codex-native-ui adapts to codex SDK."""
    from agentnexus._platform import IS_WINDOWS
    from agentnexus.db.utils import generate_agent_id
    from agentnexus.stores.agent_store.sqlalchemy_store import SqlAlchemyAgentStore
    if not IS_WINDOWS:
        pytest.skip("Windows adaptation test only runs on Windows")
    agent_store = SqlAlchemyAgentStore(db_uri)
    codex_agent_id = generate_agent_id()
    agent_store.create(codex_agent_id, name="codex-native-ui", bundle_location="test:///bundle")

    create_resp = await client.post("/v1/sessions", json={"agent_id": codex_agent_id})
    assert create_resp.status_code == 201
    created = create_resp.json()
    assert created["harness"] == "codex"
    assert created["labels"]["agentnexus.ui"] == "chat"
