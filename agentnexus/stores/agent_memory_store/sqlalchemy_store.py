"""SQLAlchemy-backed agent-memory store."""

from __future__ import annotations

from sqlalchemy import delete, desc, select

from agentnexus.db.db_models import SqlAgentMemory, current_workspace_id
from agentnexus.db.utils import (
    get_or_create_engine,
    make_named_managed_session_maker,
    now_epoch,
)
from agentnexus.entities import AgentMemory
from agentnexus.stores.agent_memory_store import AgentMemoryStore


def _to_entity(row: SqlAgentMemory) -> AgentMemory:
    """Convert an ORM row to the domain entity."""
    return AgentMemory(
        id=row.id,
        agent_id=row.agent_id,
        content=row.content,
        source=row.source,
        workspace_id=row.workspace_id,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


class SqlAlchemyAgentMemoryStore(AgentMemoryStore):
    """Persist teammate memories in the AgentNexus database."""

    def __init__(self, storage_location: str) -> None:
        super().__init__(storage_location)
        self._engine = get_or_create_engine(storage_location)
        self._session = make_named_managed_session_maker(
            self._engine,
            query_name_prefix="agentnexus.agent_memory_store",
        )

    def create(
        self,
        memory_id: str,
        agent_id: str,
        content: str,
        *,
        source: str = "manual",
    ) -> AgentMemory:
        row = SqlAgentMemory(
            id=memory_id,
            agent_id=agent_id,
            content=content,
            source=source,
            created_at=now_epoch(),
            updated_at=None,
        )
        with self._session("insert_memory") as session:
            session.add(row)
            session.flush()
            return _to_entity(row)

    def list(self, agent_id: str, *, limit: int = 200) -> list[AgentMemory]:
        with self._session("list_memories") as session:
            stmt = (
                select(SqlAgentMemory)
                .where(
                    SqlAgentMemory.workspace_id == current_workspace_id(),
                    SqlAgentMemory.agent_id == agent_id,
                )
                .order_by(
                    desc(SqlAgentMemory.created_at),
                    desc(SqlAgentMemory.id),
                )
                .limit(limit)
            )
            rows = session.execute(stmt).scalars().all()
            return [_to_entity(r) for r in rows]

    def update(
        self,
        agent_id: str,
        memory_id: str,
        content: str,
    ) -> AgentMemory | None:
        with self._session("update_memory") as session:
            row = session.get(
                SqlAgentMemory,
                (current_workspace_id(), memory_id),
            )
            if row is None or row.agent_id != agent_id:
                return None
            row.content = content
            row.updated_at = now_epoch()
            return _to_entity(row)

    def delete(self, agent_id: str, memory_id: str) -> bool:
        with self._session("delete_memory") as session:
            result = session.execute(
                delete(SqlAgentMemory).where(
                    SqlAgentMemory.workspace_id == current_workspace_id(),
                    SqlAgentMemory.id == memory_id,
                    SqlAgentMemory.agent_id == agent_id,
                )
            )
            return (result.rowcount or 0) > 0
