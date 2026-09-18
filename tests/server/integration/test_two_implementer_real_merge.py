"""Real-git acceptance for parallel implementers and user-confirmed merge.

Two implementer sessions work in distinct real git worktrees off one source
repository. Each session commits a separate file. The control-plane API then
requires a write lease, creates a persisted merge preview, refuses a stale
fencing token, and only executes the preview after the explicit confirmation
POST. Both branches land in ``main`` and the files are present in the real
commit history.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from agentnexus.coordination.store import CoordinationStore
from agentnexus.host.git_worktree import create_worktree
from agentnexus.runtime.agent_cache import AgentCache
from agentnexus.server.app import create_app
from agentnexus.stores.agent_store.sqlalchemy_store import SqlAlchemyAgentStore
from agentnexus.stores.artifact_store.local import LocalArtifactStore
from agentnexus.stores.conversation_store.sqlalchemy_store import (
    SqlAlchemyConversationStore,
)
from agentnexus.stores.file_store.sqlalchemy_store import SqlAlchemyFileStore

pytestmark = pytest.mark.asyncio

_HOST_ID = "2b8753b34a61b09af35a01136d40fadf"
_GIT_ENV = {
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@t",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@t",
}


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=repo,
        env={**os.environ, **_GIT_ENV},
        check=True,
        capture_output=True,
        text=True,
    )


def _show(repo: Path, path: str) -> str:
    res = subprocess.run(
        ["git", "-C", str(repo), "show", f"main:{path}"],
        check=True,
        capture_output=True,
        text=True,
    )
    return res.stdout


def _build_app(db_uri: str, tmp_path: Path) -> FastAPI:
    artifact_store = LocalArtifactStore(str(tmp_path / "merge-artifacts"))
    return create_app(
        agent_store=SqlAlchemyAgentStore(db_uri),
        file_store=SqlAlchemyFileStore(db_uri),
        conversation_store=SqlAlchemyConversationStore(db_uri),
        artifact_store=artifact_store,
        agent_cache=AgentCache(
            artifact_store=artifact_store,
            cache_dir=tmp_path / "merge-cache",
        ),
        coordination_store=CoordinationStore(db_uri),
    )


def _init_repo(tmp_path: Path) -> Path:
    repo = (tmp_path / "managed" / "repo").resolve()
    repo.mkdir(parents=True)
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    (repo / "README.md").write_text("base\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "base")
    return repo


def _seed_worktree_sessions(
    app: FastAPI,
    repo: Path,
    first_path: Path,
    second_path: Path,
) -> tuple[object, object, object]:
    store = app.state.conversation_store
    root = store.create_conversation(
        title="two-implementer-merge",
        host_id=_HOST_ID,
        workspace=str(repo),
    )
    first = store.create_conversation(
        parent_conversation_id=root.id,
        kind="sub_agent",
        title="implementer:one-merge",
        host_id=_HOST_ID,
        workspace=str(first_path),
        git_branch="implementer/one",
    )
    second = store.create_conversation(
        parent_conversation_id=root.id,
        kind="sub_agent",
        title="implementer:two-merge",
        host_id=_HOST_ID,
        workspace=str(second_path),
        git_branch="implementer/two",
    )
    return root, first, second


async def test_two_implementer_real_worktrees_merge_after_user_confirm(
    runtime_init: None,
    db_uri: str,
    tmp_path: Path,
) -> None:
    """Both real worktree branches land in main through the preview/execute API."""
    repo = _init_repo(tmp_path)
    first = create_worktree(repo_path=str(repo), branch_name="implementer/one")
    second = create_worktree(repo_path=str(repo), branch_name="implementer/two")
    first_path = Path(first.worktree_path)
    second_path = Path(second.worktree_path)

    (first_path / "one.txt").write_text("implementer one\n", encoding="utf-8")
    _git(first_path, "add", ".")
    _git(first_path, "commit", "-q", "-m", "implementer one change")
    (second_path / "two.txt").write_text("implementer two\n", encoding="utf-8")
    _git(second_path, "add", ".")
    _git(second_path, "commit", "-q", "-m", "implementer two change")

    app = _build_app(db_uri, tmp_path)
    root, _first, _second = _seed_worktree_sessions(app, repo, first_path, second_path)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        lease = await client.post(
            "/v1/coordination/workspaces/lease",
            json={
                "root_session_id": root.id,
                "holder_session_id": root.id,
                "workspace_path": str(repo),
                "mode": "write",
            },
        )
        assert lease.status_code == 200, lease.text
        fencing_token = lease.json()["lease"]["fencing_token"]

        for branch in ("implementer/one", "implementer/two"):
            preview = await client.post(
                "/v1/coordination/workspaces/merge-previews",
                json={
                    "root_session_id": root.id,
                    "holder_session_id": root.id,
                    "repo_path": str(repo),
                    "source_branch": branch,
                    "target_branch": "main",
                },
            )
            assert preview.status_code == 200, preview.text
            operation = preview.json()["operation"]
            assert operation["status"] == "preview"
            assert operation["preview"]["can_merge"] is True

            rejected = await client.post(
                f"/v1/coordination/workspaces/merge-previews/{operation['operation_id']}/execute",
                json={"fencing_token": fencing_token + 1},
            )
            assert rejected.status_code == 409
            assert "token mismatch" in rejected.json()["detail"]

            confirmed = await client.post(
                f"/v1/coordination/workspaces/merge-previews/{operation['operation_id']}/execute",
                json={"fencing_token": fencing_token},
            )
            assert confirmed.status_code == 200, confirmed.text
            assert confirmed.json()["operation"]["status"] == "merged"

    store = app.state.coordination_store
    assert store is not None
    operations = await asyncio.to_thread(store.list_merge_operations, root.id)
    assert len(operations) == 2
    assert all(operation.status == "merged" for operation in operations)
    assert _show(repo, "one.txt") == "implementer one\n"
    assert _show(repo, "two.txt") == "implementer two\n"

    main_log = subprocess.run(
        ["git", "-C", str(repo), "log", "--first-parent", "--oneline", "main"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert "implementer/one" in main_log
    assert "implementer/two" in main_log
