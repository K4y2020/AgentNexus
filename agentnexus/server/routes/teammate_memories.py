"""Teammate memory CRUD route (``/v1/teammates/{agent_id}/memories``).

Memories are the durable, per-bot half of a teammate identity: they persist in
the AgentNexus database rather than in any one CLI process, so a bot can move
between shifts/workstations without losing what it knows. The route deliberately
rejects native CLI wrapper agents (``claude-native-ui`` etc.) — a wrapper is an
execution tool, not a bot with a memory.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response, status

from agentnexus.native_coding_agents import (
    is_execution_harness_agent_name,
)
from agentnexus.server.auth import AuthProvider
from agentnexus.server.routes._auth_helpers import require_user as _require_user
from agentnexus.server.schemas import (
    AgentMemoryObject,
    CreateAgentMemoryRequest,
    UpdateAgentMemoryRequest,
)
from agentnexus.stores import AgentStore
from agentnexus.stores.agent_memory_store import AgentMemoryStore


def create_teammate_memories_router(
    agent_store: AgentStore,
    memory_store: AgentMemoryStore,
    *,
    auth_provider: AuthProvider | None = None,
) -> APIRouter:
    """Build the CRUD router for one teammate's memories."""
    router = APIRouter()

    async def _require_teammate(agent_id: str) -> None:
        agent = await asyncio.to_thread(agent_store.get, agent_id)
        if agent is None or is_execution_harness_agent_name(agent.name):
            raise HTTPException(status_code=404, detail="Teammate not found")

    def _object(memory: Any) -> AgentMemoryObject:
        return AgentMemoryObject(
            id=memory.id,
            agent_id=memory.agent_id,
            content=memory.content,
            source=memory.source,
            created_at=memory.created_at,
            updated_at=memory.updated_at,
        )

    @router.get("/teammates/{agent_id}/memories")
    async def list_memories(agent_id: str, request: Request) -> dict[str, list[AgentMemoryObject]]:
        _require_user(request, auth_provider)
        await _require_teammate(agent_id)
        rows = await asyncio.to_thread(memory_store.list, agent_id)
        return {"memories": [_object(r) for r in rows]}

    @router.post(
        "/teammates/{agent_id}/memories",
        status_code=status.HTTP_201_CREATED,
    )
    async def create_memory(
        agent_id: str,
        body: CreateAgentMemoryRequest,
        request: Request,
    ) -> dict[str, AgentMemoryObject]:
        _require_user(request, auth_provider)
        await _require_teammate(agent_id)
        created = await asyncio.to_thread(
            memory_store.create,
            uuid.uuid4().hex,
            agent_id,
            body.content,
        )
        return {"memory": _object(created)}

    @router.patch("/teammates/{agent_id}/memories/{memory_id}")
    async def update_memory(
        agent_id: str,
        memory_id: str,
        body: UpdateAgentMemoryRequest,
        request: Request,
    ) -> dict[str, AgentMemoryObject]:
        _require_user(request, auth_provider)
        await _require_teammate(agent_id)
        updated = await asyncio.to_thread(
            memory_store.update,
            agent_id,
            memory_id,
            body.content,
        )
        if updated is None:
            raise HTTPException(status_code=404, detail="Memory not found")
        return {"memory": _object(updated)}

    @router.delete(
        "/teammates/{agent_id}/memories/{memory_id}",
        status_code=status.HTTP_204_NO_CONTENT,
    )
    async def delete_memory(
        agent_id: str,
        memory_id: str,
        request: Request,
    ) -> Response:
        _require_user(request, auth_provider)
        await _require_teammate(agent_id)
        deleted = await asyncio.to_thread(memory_store.delete, agent_id, memory_id)
        if not deleted:
            raise HTTPException(status_code=404, detail="Memory not found")
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    return router
