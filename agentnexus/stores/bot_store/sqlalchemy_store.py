"""SQLAlchemy-backed durable bot store."""

from __future__ import annotations

import time
import uuid

from sqlalchemy import asc, select
from sqlalchemy.exc import IntegrityError

from agentnexus.db.db_models import (
    SqlBot,
    SqlBotComputerBinding,
    SqlBotProjectBinding,
    SqlComputerExecutionLease,
    current_workspace_id,
)
from agentnexus.db.utils import get_or_create_engine, make_named_managed_session_maker, now_epoch
from agentnexus.entities import Bot, BotComputerBinding, BotProjectBinding, ComputerExecutionLease
from agentnexus.stores.bot_store import BotStore


def _bot_entity(row: SqlBot) -> Bot:
    return Bot(
        id=row.id,
        owner_id=row.owner_id,
        agent_id=row.agent_id,
        name=row.name,
        description=row.description,
        status=row.status,
        default_model=row.default_model,
        behavior_mode=row.behavior_mode,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _binding_entity(row: SqlBotComputerBinding) -> BotComputerBinding:
    return BotComputerBinding(
        id=row.id,
        bot_id=row.bot_id,
        host_id=row.host_id,
        home_path=row.home_path,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )

class LeaseConflictError(ValueError):
    """Raised when an exclusive execution lease cannot be acquired."""


def _project_binding_entity(row: SqlBotProjectBinding) -> BotProjectBinding:
    return BotProjectBinding(
        id=row.id,
        bot_id=row.bot_id,
        project_id=row.project_id,
        checkout_root=row.checkout_root,
        default_branch=row.default_branch,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _lease_entity(row: SqlComputerExecutionLease) -> ComputerExecutionLease:
    return ComputerExecutionLease(
        id=row.id,
        computer_id=row.computer_id,
        bot_id=row.bot_id,
        session_id=row.session_id,
        run_id=row.run_id,
        path=row.path,
        fence=row.fence,
        expires_at=row.expires_at,
        created_at=row.created_at,
    )



def _stable_id(kind: str, *parts: object) -> str:
    value = ":".join(str(part) for part in parts)
    return uuid.uuid5(uuid.NAMESPACE_URL, f"agentnexus:{kind}:{value}").hex


class SqlAlchemyBotStore(BotStore):
    def __init__(self, storage_location: str) -> None:
        super().__init__(storage_location)
        self._engine = get_or_create_engine(storage_location)
        self._session = make_named_managed_session_maker(
            self._engine,
            query_name_prefix="agentnexus.bot_store",
        )

    def ensure_for_agent(
        self,
        *,
        owner_id: str,
        agent_id: str,
        name: str,
        description: str | None,
    ) -> Bot:
        existing = self.get_by_agent(agent_id, owner_id=owner_id)
        if existing is not None:
            return existing
        bot_id = _stable_id("bot", current_workspace_id(), owner_id, agent_id)
        try:
            with self._session("ensure_bot_for_agent") as session:
                row = session.get(SqlBot, (current_workspace_id(), bot_id))
                if row is None:
                    row = SqlBot(
                        id=bot_id,
                        owner_id=owner_id,
                        agent_id=agent_id,
                        name=name,
                        description=description,
                        status="active",
                        default_model=None,
                        behavior_mode="off",
                        created_at=now_epoch(),
                        updated_at=None,
                    )
                    session.add(row)
                    session.flush()
                return _bot_entity(row)
        except IntegrityError:
            raced = self.get(bot_id, owner_id=owner_id)
            if raced is None:
                raise
            return raced

    def get(self, bot_id: str, *, owner_id: str | None = None) -> Bot | None:
        with self._session("select_bot") as session:
            row = session.get(SqlBot, (current_workspace_id(), bot_id))
            if row is None or (owner_id is not None and row.owner_id != owner_id):
                return None
            return _bot_entity(row)

    def get_by_agent(self, agent_id: str, *, owner_id: str) -> Bot | None:
        with self._session("select_bot_by_agent") as session:
            row = session.execute(
                select(SqlBot)
                .where(SqlBot.workspace_id == current_workspace_id())
                .where(SqlBot.owner_id == owner_id)
                .where(SqlBot.agent_id == agent_id)
                .where(SqlBot.status == "active")
                .order_by(asc(SqlBot.created_at), asc(SqlBot.id))
                .limit(1)
            ).scalar_one_or_none()
            return _bot_entity(row) if row is not None else None

    def list(self, *, owner_id: str, include_archived: bool = False) -> list[Bot]:
        with self._session("list_bots") as session:
            stmt = select(SqlBot).where(
                SqlBot.workspace_id == current_workspace_id(),
                SqlBot.owner_id == owner_id,
            )
            if not include_archived:
                stmt = stmt.where(SqlBot.status == "active")
            rows = session.execute(stmt.order_by(asc(SqlBot.created_at), asc(SqlBot.id))).scalars()
            return [_bot_entity(row) for row in rows]

    def update_settings(
        self,
        bot_id: str,
        *,
        owner_id: str,
        name: str,
        description: str | None,
        status: str,
        default_model: str | None,
        behavior_mode: str,
    ) -> Bot | None:
        with self._session("update_bot_settings") as session:
            row = session.get(SqlBot, (current_workspace_id(), bot_id))
            if row is None or row.owner_id != owner_id:
                return None
            row.name = name
            row.description = description
            row.status = status
            row.default_model = default_model
            row.behavior_mode = behavior_mode
            row.updated_at = now_epoch()
            session.flush()
            return _bot_entity(row)

    def ensure_binding(
        self,
        *,
        bot_id: str,
        host_id: str | None,
        home_path: str,
    ) -> BotComputerBinding:
        existing = self.get_binding(bot_id)
        if existing is not None:
            if host_id is not None and existing.host_id is None:
                return (
                    self.update_binding(
                        bot_id,
                        host_id=host_id,
                        home_path=existing.home_path,
                    )
                    or existing
                )
            return existing
        binding_id = _stable_id("bot-binding", current_workspace_id(), bot_id)
        try:
            with self._session("ensure_bot_binding") as session:
                row = SqlBotComputerBinding(
                    id=binding_id,
                    bot_id=bot_id,
                    host_id=host_id,
                    home_path=home_path,
                    created_at=now_epoch(),
                    updated_at=None,
                )
                session.add(row)
                session.flush()
                return _binding_entity(row)
        except IntegrityError:
            raced = self.get_binding(bot_id)
            if raced is None:
                raise
            return raced

    def get_binding(self, bot_id: str) -> BotComputerBinding | None:
        with self._session("select_bot_binding") as session:
            row = session.execute(
                select(SqlBotComputerBinding)
                .where(SqlBotComputerBinding.workspace_id == current_workspace_id())
                .where(SqlBotComputerBinding.bot_id == bot_id)
                .limit(1)
            ).scalar_one_or_none()
            return _binding_entity(row) if row is not None else None

    def update_binding(
        self,
        bot_id: str,
        *,
        host_id: str | None,
        home_path: str,
    ) -> BotComputerBinding | None:
        with self._session("update_bot_binding") as session:
            row = session.execute(
                select(SqlBotComputerBinding)
                .where(SqlBotComputerBinding.workspace_id == current_workspace_id())
                .where(SqlBotComputerBinding.bot_id == bot_id)
                .limit(1)
            ).scalar_one_or_none()
            if row is None:
                return None
            row.host_id = host_id
            row.home_path = home_path
            row.updated_at = now_epoch()
            session.flush()
            return _binding_entity(row)

    def bind_project(
        self,
        *,
        bot_id: str,
        project_id: str,
        checkout_root: str,
        default_branch: str = "main",
    ) -> BotProjectBinding:
        binding_id = _stable_id("bot_project", current_workspace_id(), bot_id, project_id)
        now = now_epoch()
        with self._session("bind_project") as session:
            stmt = select(SqlBotProjectBinding).where(
                SqlBotProjectBinding.workspace_id == current_workspace_id(),
                SqlBotProjectBinding.bot_id == bot_id,
                SqlBotProjectBinding.project_id == project_id,
            )
            existing = session.execute(stmt).scalar_one_or_none()
            if existing is not None:
                existing.checkout_root = checkout_root
                existing.default_branch = default_branch
                existing.updated_at = now
                session.flush()
                return _project_binding_entity(existing)

            row = SqlBotProjectBinding(
                id=binding_id,
                bot_id=bot_id,
                project_id=project_id,
                checkout_root=checkout_root,
                default_branch=default_branch,
                created_at=now,
                updated_at=None,
            )
            session.add(row)
            session.flush()
            return _project_binding_entity(row)

    def get_project_binding(self, *, bot_id: str, project_id: str) -> BotProjectBinding | None:
        with self._session("get_project_binding") as session:
            stmt = select(SqlBotProjectBinding).where(
                SqlBotProjectBinding.workspace_id == current_workspace_id(),
                SqlBotProjectBinding.bot_id == bot_id,
                SqlBotProjectBinding.project_id == project_id,
            )
            row = session.execute(stmt).scalar_one_or_none()
            return _project_binding_entity(row) if row is not None else None

    def list_project_bindings(self, bot_id: str) -> list[BotProjectBinding]:
        with self._session("list_project_bindings") as session:
            stmt = select(SqlBotProjectBinding).where(
                SqlBotProjectBinding.workspace_id == current_workspace_id(),
                SqlBotProjectBinding.bot_id == bot_id,
            ).order_by(asc(SqlBotProjectBinding.created_at))
            return [_project_binding_entity(r) for r in session.execute(stmt).scalars()]

    def unbind_project(self, *, bot_id: str, project_id: str) -> bool:
        with self._session("unbind_project") as session:
            stmt = select(SqlBotProjectBinding).where(
                SqlBotProjectBinding.workspace_id == current_workspace_id(),
                SqlBotProjectBinding.bot_id == bot_id,
                SqlBotProjectBinding.project_id == project_id,
            )
            row = session.execute(stmt).scalar_one_or_none()
            if row is not None:
                session.delete(row)
                return True
            return False

    def acquire_execution_lease(
        self,
        *,
        computer_id: str,
        bot_id: str,
        session_id: str,
        run_id: str,
        path: str,
        ttl_seconds: float = 120.0,
    ) -> ComputerExecutionLease:
        now = time.time()
        with self._session("acquire_execution_lease") as session:
            stmt = select(SqlComputerExecutionLease).where(
                SqlComputerExecutionLease.workspace_id == current_workspace_id(),
                SqlComputerExecutionLease.path == path,
            )
            existing = session.execute(stmt).scalar_one_or_none()
            if existing is not None:
                if existing.run_id == run_id:
                    existing.expires_at = now + ttl_seconds
                    session.flush()
                    return _lease_entity(existing)
                if existing.expires_at > now:
                    raise LeaseConflictError(
                        f"Path '{path}' is exclusively leased by run '{existing.run_id}' "
                        f"until {existing.expires_at:.1f}"
                    )
                fence = existing.fence + 1
                session.delete(existing)
                session.flush()
            else:
                fence = 1

            lease_id = _stable_id("lease", current_workspace_id(), run_id, path)
            row = SqlComputerExecutionLease(
                id=lease_id,
                computer_id=computer_id,
                bot_id=bot_id,
                session_id=session_id,
                run_id=run_id,
                path=path,
                fence=fence,
                expires_at=now + ttl_seconds,
                created_at=now,
            )
            session.add(row)
            session.flush()
            return _lease_entity(row)

    def release_execution_lease(self, run_id: str) -> bool:
        with self._session("release_execution_lease") as session:
            stmt = select(SqlComputerExecutionLease).where(
                SqlComputerExecutionLease.workspace_id == current_workspace_id(),
                SqlComputerExecutionLease.run_id == run_id,
            )
            row = session.execute(stmt).scalar_one_or_none()
            if row is not None:
                session.delete(row)
                session.flush()
                return True
            return False

    def get_active_lease(
        self, path: str, *, now: float | None = None
    ) -> ComputerExecutionLease | None:
        t = now if now is not None else time.time()
        with self._session("get_active_lease") as session:
            stmt = select(SqlComputerExecutionLease).where(
                SqlComputerExecutionLease.workspace_id == current_workspace_id(),
                SqlComputerExecutionLease.path == path,
                SqlComputerExecutionLease.expires_at > t,
            )
            row = session.execute(stmt).scalar_one_or_none()
            return _lease_entity(row) if row is not None else None
