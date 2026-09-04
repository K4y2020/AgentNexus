"""SQLAlchemy-backed durable bot store."""

from __future__ import annotations

import uuid

from sqlalchemy import asc, select
from sqlalchemy.exc import IntegrityError

from omnigent.db.db_models import (
    SqlBot,
    SqlBotComputerBinding,
    current_workspace_id,
)
from omnigent.db.utils import get_or_create_engine, make_named_managed_session_maker, now_epoch
from omnigent.entities import Bot, BotComputerBinding
from omnigent.stores.bot_store import BotStore


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


def _stable_id(kind: str, *parts: object) -> str:
    value = ":".join(str(part) for part in parts)
    return uuid.uuid5(uuid.NAMESPACE_URL, f"omnigent:{kind}:{value}").hex


class SqlAlchemyBotStore(BotStore):
    def __init__(self, storage_location: str) -> None:
        super().__init__(storage_location)
        self._engine = get_or_create_engine(storage_location)
        self._session = make_named_managed_session_maker(
            self._engine,
            query_name_prefix="omnigent.bot_store",
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
