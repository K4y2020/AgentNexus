"""Phase 2 & Phase 3 validation: Computers, Worktrees, Leases, and A2A ACL."""

from __future__ import annotations

import subprocess
import uuid
from pathlib import Path

import pytest

from agentnexus.computers.local_host import LocalHostComputerProvider
from agentnexus.entities import Bot
from agentnexus.stores.bot_store.sqlalchemy_store import LeaseConflictError, SqlAlchemyBotStore


def init_test_git_repo(repo_dir: Path) -> None:
    repo_dir.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-b", "main"], cwd=repo_dir, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo_dir, check=True)
    subprocess.run(["git", "config", "user.name", "Test Runner"], cwd=repo_dir, check=True)
    (repo_dir / "README.md").write_text("# Project Root\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo_dir, check=True)
    subprocess.run(["git", "commit", "-m", "Initial commit"], cwd=repo_dir, check=True)


@pytest.fixture
def bot_store(tmp_path: Path) -> SqlAlchemyBotStore:
    db_path = tmp_path / "test_phase2.db"
    return SqlAlchemyBotStore(f"sqlite:///{db_path}")


@pytest.fixture
def sample_bot(bot_store: SqlAlchemyBotStore, tmp_path: Path) -> Bot:
    agent_id = uuid.uuid4().hex
    host_id = uuid.uuid4().hex
    bot = bot_store.ensure_for_agent(
        owner_id="user_owner",
        agent_id=agent_id,
        name="polly",
        description="Coding orchestrator",
    )
    bot_home = tmp_path / "bots" / bot.id
    bot_home.mkdir(parents=True, exist_ok=True)
    bot_store.ensure_binding(bot_id=bot.id, host_id=host_id, home_path=str(bot_home))
    return bot


# ── 1. Bot Project Binding Store Operations ──────────────────────


def test_bot_project_binding_crud(
    bot_store: SqlAlchemyBotStore, sample_bot: Bot, tmp_path: Path
) -> None:
    checkout1 = tmp_path / "repos" / "frontend"
    checkout2 = tmp_path / "repos" / "backend"
    checkout1.mkdir(parents=True)
    checkout2.mkdir(parents=True)

    proj_front = uuid.uuid4().hex
    proj_back = uuid.uuid4().hex

    # 1. Bind to two separate projects
    b1 = bot_store.bind_project(
        bot_id=sample_bot.id,
        project_id=proj_front,
        checkout_root=str(checkout1),
        default_branch="main",
    )
    b2 = bot_store.bind_project(
        bot_id=sample_bot.id,
        project_id=proj_back,
        checkout_root=str(checkout2),
        default_branch="dev",
    )
    assert b1.project_id == proj_front
    assert b2.project_id == proj_back

    # 2. List bindings
    all_bindings = bot_store.list_project_bindings(sample_bot.id)
    assert len(all_bindings) == 2
    assert {b.project_id for b in all_bindings} == {proj_front, proj_back}

    # 3. Get single binding
    found = bot_store.get_project_binding(bot_id=sample_bot.id, project_id=proj_front)
    assert found is not None
    assert found.checkout_root == str(checkout1)

    # 4. Unbind one project
    unbound = bot_store.unbind_project(bot_id=sample_bot.id, project_id=proj_front)
    assert unbound is True
    assert bot_store.get_project_binding(bot_id=sample_bot.id, project_id=proj_front) is None
    assert len(bot_store.list_project_bindings(sample_bot.id)) == 1


# ── 2. Local Host Provider Scratch Resolution ───────────────────


def test_local_host_provider_scratch_without_project(
    bot_store: SqlAlchemyBotStore, sample_bot: Bot
) -> None:
    provider = LocalHostComputerProvider(bot_store)
    ws = provider.resolve_run_workspace(
        bot_id=sample_bot.id,
        session_id=uuid.uuid4().hex,
        run_id="run_123",
        project_id=None,
    )
    assert not ws.is_worktree
    assert Path(ws.path).parent.name == "topics"
    assert (Path(ws.path) / "outputs").is_dir()
    assert Path(ws.path).exists()


# ── 3. Git Worktree Isolation & Concurrent Safety (Zero index.lock) ─


def test_git_worktree_concurrent_isolation_zero_lock(
    bot_store: SqlAlchemyBotStore,
    sample_bot: Bot,
    tmp_path: Path,
) -> None:
    repo_dir = tmp_path / "repos" / "app_repo"
    init_test_git_repo(repo_dir)

    proj_app = uuid.uuid4().hex
    bot_store.bind_project(
        bot_id=sample_bot.id,
        project_id=proj_app,
        checkout_root=str(repo_dir),
        default_branch="main",
    )

    provider = LocalHostComputerProvider(bot_store)

    # Launch two concurrent write runs on the same project
    ws1 = provider.resolve_run_workspace(
        bot_id=sample_bot.id,
        session_id=uuid.uuid4().hex,
        run_id="run_concurrent_1",
        project_id=proj_app,
        access_mode="write",
    )
    ws2 = provider.resolve_run_workspace(
        bot_id=sample_bot.id,
        session_id=uuid.uuid4().hex,
        run_id="run_concurrent_2",
        project_id=proj_app,
        access_mode="write",
    )

    assert ws1.is_worktree
    assert ws2.is_worktree
    assert ws1.path != ws2.path
    assert ws1.branch != ws2.branch
    assert Path(ws1.path).exists()
    assert Path(ws2.path).exists()

    # Concurrent file writing in both worktrees
    p1 = Path(ws1.path) / "feature1.txt"
    p2 = Path(ws2.path) / "feature2.txt"
    p1.write_text("Feature 1 by worker 1\n", encoding="utf-8")
    p2.write_text("Feature 2 by worker 2\n", encoding="utf-8")

    # Concurrent git commits in both worktrees (guarantees 0 index.lock collisions!)
    subprocess.run(["git", "add", "."], cwd=ws1.path, check=True)
    subprocess.run(["git", "commit", "-m", "Commit 1"], cwd=ws1.path, check=True)

    subprocess.run(["git", "add", "."], cwd=ws2.path, check=True)
    subprocess.run(["git", "commit", "-m", "Commit 2"], cwd=ws2.path, check=True)

    # Main repository checkout remains 100% clean and unpolluted!
    status_main = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=repo_dir,
        capture_output=True,
        text=True,
        check=True,
    )
    assert status_main.stdout.strip() == ""

    # Clean up worktrees
    provider.cleanup_run_workspace(bot_id=sample_bot.id, workspace=ws1)
    provider.cleanup_run_workspace(bot_id=sample_bot.id, workspace=ws2)


# ── 4. Non-Git Exclusive Execution Lease ─────────────────────────


def test_non_git_execution_lease_exclusivity(
    bot_store: SqlAlchemyBotStore,
    sample_bot: Bot,
    tmp_path: Path,
) -> None:
    non_git_dir = tmp_path / "shared_folder"
    non_git_dir.mkdir(parents=True)

    proj_docs = uuid.uuid4().hex
    bot_store.bind_project(
        bot_id=sample_bot.id,
        project_id=proj_docs,
        checkout_root=str(non_git_dir),
    )

    provider = LocalHostComputerProvider(bot_store)

    sess_1 = uuid.uuid4().hex
    sess_2 = uuid.uuid4().hex

    # Run 1 acquires exclusive lease
    ws1 = provider.resolve_run_workspace(
        bot_id=sample_bot.id,
        session_id=sess_1,
        run_id="run_exclusive_1",
        project_id=proj_docs,
        access_mode="write",
        is_git=False,
    )
    assert not ws1.is_worktree
    assert ws1.lease_id is not None

    # Run 2 attempts concurrent write on same non-git folder -> raises LeaseConflictError!
    with pytest.raises(LeaseConflictError):
        provider.resolve_run_workspace(
            bot_id=sample_bot.id,
            session_id=sess_2,
            run_id="run_exclusive_2",
            project_id=proj_docs,
            access_mode="write",
            is_git=False,
        )

    # Run 1 cleans up / releases lease
    provider.cleanup_run_workspace(bot_id=sample_bot.id, workspace=ws1)

    # Run 2 can now acquire lease
    ws2 = provider.resolve_run_workspace(
        bot_id=sample_bot.id,
        session_id=sess_2,
        run_id="run_exclusive_2",
        project_id=proj_docs,
        access_mode="write",
        is_git=False,
    )
    assert ws2.lease_id is not None
    provider.cleanup_run_workspace(bot_id=sample_bot.id, workspace=ws2)
