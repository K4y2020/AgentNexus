"""Persistent Bot roster with a compatibility ``/teammates`` alias."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel

from agentnexus.bots import bot_owner_id, ensure_bot_for_agent
from agentnexus.entities import Bot, BotComputerBinding
from agentnexus.errors import ErrorCode, AgentNexusError
from agentnexus.model_override import validate_model_override
from agentnexus.native_coding_agents import is_execution_harness_agent_name
from agentnexus.runtime.agent_cache import AgentCache
from agentnexus.server.auth import RESERVED_USER_LOCAL, AuthProvider
from agentnexus.server.routes._auth_helpers import (
    get_session_owner_id,
)
from agentnexus.server.routes._auth_helpers import (
    require_user as _require_user,
)
from agentnexus.server.routes.builtin_agents import _to_agent_object
from agentnexus.server.schemas import (
    BotObject,
    TeammateObject,
    TeammateRoutineObject,
    UpdateBotRequest,
)
from agentnexus.stores import AgentStore, BotStore, ConversationStore, PermissionStore
from agentnexus.stores.scheduled_task_store import ScheduledTaskStore


class BindProjectRequest(BaseModel):
    project_id: str
    checkout_root: str
    default_branch: str = "main"


def _bot_object(bot: Bot, binding: BotComputerBinding) -> BotObject:
    return BotObject(
        id=bot.id,
        agent_id=bot.agent_id,
        name=bot.name,
        description=bot.description,
        status=bot.status,
        default_model=bot.default_model,
        behavior_mode=bot.behavior_mode,
        home_path=binding.home_path,
        host_id=binding.host_id,
        created_at=bot.created_at,
        updated_at=bot.updated_at,
    )


def _legacy_session_purpose(
    conversation: Any,
    *,
    primary_id: str | None,
    a2a_id: str | None,
) -> tuple[str, str | None]:
    if conversation.parent_conversation_id is not None:
        return "subagent", None
    if conversation.id == a2a_id:
        return "a2a", "a2a"
    if conversation.id == primary_id:
        return "primary", "primary"
    return "topic", None


async def _backfill_local_bot_sessions(
    conversation_store: ConversationStore,
    bot: Bot,
    *,
    permission_store: PermissionStore | None,
) -> None:
    """Idempotently attach legacy sessions owned by the Bot's owner."""
    try:
        page = await asyncio.to_thread(
            conversation_store.list_conversations,
            agent_id=bot.agent_id,
            limit=1000,
            include_archived=True,
        )
        conversations = list(page.data)
        if bot.owner_id != RESERVED_USER_LOCAL:
            conversations = [
                conv
                for conv in conversations
                if await asyncio.to_thread(
                    get_session_owner_id,
                    conv.id,
                    permission_store,
                )
                == bot.owner_id
            ]
        if not conversations:
            return
        existing_primary = await asyncio.to_thread(
            conversation_store.get_bot_singleton_session, bot.id, "primary"
        )
        existing_a2a = await asyncio.to_thread(
            conversation_store.get_bot_singleton_session, bot.id, "a2a"
        )
        a2a_id = (
            existing_a2a.id
            if existing_a2a
            else next(
                (
                    conv.id
                    for conv in conversations
                    if conv.labels.get("agentnexus.teammate.channel") == "a2a"
                ),
                None,
            )
        )
        primary_id = (
            existing_primary.id
            if existing_primary
            else next(
                (
                    conv.id
                    for conv in conversations
                    if conv.parent_conversation_id is None
                    and conv.id != a2a_id
                    and conv.labels.get("agentnexus.teammate.primary") == "true"
                ),
                None,
            )
        )
        if primary_id is None:
            primary_id = next(
                (
                    conv.id
                    for conv in conversations
                    if conv.parent_conversation_id is None and conv.id != a2a_id
                ),
                None,
            )
        for conv in conversations:
            if conv.bot_id is not None:
                continue
            purpose, slot = _legacy_session_purpose(
                conv,
                primary_id=primary_id,
                a2a_id=a2a_id,
            )
            await asyncio.to_thread(
                conversation_store.set_bot_ownership,
                conv.id,
                bot_id=bot.id,
                purpose=purpose,
                singleton_slot=slot,
            )
    except (NotImplementedError, AttributeError):
        return


def create_teammates_router(
    agent_store: AgentStore,
    scheduled_task_store: ScheduledTaskStore | None,
    agent_cache: AgentCache,
    conversation_store: ConversationStore | None = None,
    bot_store: BotStore | None = None,
    permission_store: PermissionStore | None = None,
    *,
    auth_provider: AuthProvider | None = None,
) -> APIRouter:
    """Build the Bot roster and its legacy Teammates alias."""
    router = APIRouter()
    resolved_bot_store = bot_store
    if resolved_bot_store is None and isinstance(
        getattr(agent_store, "storage_location", None), str
    ):
        from agentnexus.stores.bot_store.sqlalchemy_store import SqlAlchemyBotStore

        resolved_bot_store = SqlAlchemyBotStore(agent_store.storage_location)

    async def _list(request: Request) -> list[TeammateObject]:
        user_id = _require_user(request, auth_provider)
        owner_id = bot_owner_id(user_id)
        routine_owner_id = None if owner_id == RESERVED_USER_LOCAL else owner_id

        page = await asyncio.to_thread(agent_store.list, limit=1000)
        eligible: dict[str, tuple[Any, Any]] = {}
        for agent in page.data:
            if is_execution_harness_agent_name(agent.name):
                continue
            agent_obj = _to_agent_object(agent, agent_cache)
            if agent_obj.harness and is_execution_harness_agent_name(agent_obj.harness):
                continue
            eligible[agent.id] = (agent, agent_obj)

        if resolved_bot_store is None:
            return []
        for agent, _ in eligible.values():
            await asyncio.to_thread(
                ensure_bot_for_agent,
                resolved_bot_store,
                agent,
                owner_id=owner_id,
            )
        bots = await asyncio.to_thread(resolved_bot_store.list, owner_id=owner_id)

        if conversation_store is not None:
            for bot in bots:
                await _backfill_local_bot_sessions(
                    conversation_store,
                    bot,
                    permission_store=permission_store,
                )

        tasks = (
            await asyncio.to_thread(
                scheduled_task_store.list,
                owner_user_id=routine_owner_id,
            )
            if scheduled_task_store is not None
            else []
        )
        latest_status = (
            await asyncio.to_thread(
                scheduled_task_store.list_latest_run_status_for_tasks,
                [task.id for task in tasks],
            )
            if scheduled_task_store is not None and tasks
            else {}
        )
        scheduler = getattr(request.app.state, "scheduled_task_scheduler", None)
        routines_by_agent: dict[str, list[TeammateRoutineObject]] = {}
        for task in tasks:
            routines_by_agent.setdefault(task.agent_id, []).append(
                TeammateRoutineObject(
                    id=task.id,
                    name=task.name,
                    state=task.state,
                    rrule=task.rrule,
                    timezone=task.timezone,
                    last_run_at=task.last_run_at,
                    last_run_status=latest_status.get(task.id),
                    last_run_conversation_id=task.last_run_conversation_id,
                    next_run_at=(
                        str(scheduler.next_run_at(task.id))
                        if scheduler is not None and task.state == "active"
                        else None
                    ),
                )
            )

        result: list[TeammateObject] = []
        for bot in bots:
            pair = eligible.get(bot.agent_id)
            binding = await asyncio.to_thread(resolved_bot_store.get_binding, bot.id)
            if pair is None or binding is None:
                continue
            _, agent_obj = pair
            routines = routines_by_agent.get(bot.agent_id, [])
            active = [routine for routine in routines if routine.last_run_at is not None]
            latest = max(active, key=lambda routine: routine.last_run_at or 0) if active else None
            primary = None
            if conversation_store is not None:
                try:
                    primary = await asyncio.to_thread(
                        conversation_store.get_bot_singleton_session,
                        bot.id,
                        "primary",
                    )
                except (NotImplementedError, AttributeError):
                    primary = None
            result.append(
                TeammateObject(
                    bot=_bot_object(bot, binding),
                    agent=agent_obj,
                    routines=routines,
                    routine_count=len(routines),
                    last_activity_at=latest.last_run_at if latest else None,
                    last_activity_status=latest.last_run_status if latest else None,
                    last_activity_conversation_id=(
                        latest.last_run_conversation_id if latest else None
                    ),
                    primary_conversation_id=primary.id if primary else None,
                )
            )
        return result

    @router.get("/bots")
    async def list_bots(request: Request) -> dict[str, Any]:
        bots = await _list(request)
        return {"bots": [bot.model_dump(mode="json") for bot in bots]}

    @router.get("/teammates")
    async def list_teammates(request: Request) -> dict[str, Any]:
        bots = await _list(request)
        return {"teammates": [bot.model_dump(mode="json") for bot in bots]}

    @router.patch("/bots/{bot_id}")
    async def update_bot(bot_id: str, body: UpdateBotRequest, request: Request) -> dict[str, Any]:
        if resolved_bot_store is None:
            raise AgentNexusError("Bot store is not configured", code=ErrorCode.INTERNAL_ERROR)
        owner_id = bot_owner_id(_require_user(request, auth_provider))
        current = await asyncio.to_thread(resolved_bot_store.get, bot_id, owner_id=owner_id)
        if current is None:
            raise AgentNexusError("Bot not found", code=ErrorCode.NOT_FOUND)
        fields = body.model_fields_set
        default_model = current.default_model
        if "default_model" in fields:
            default_model = body.default_model
            if default_model is not None:
                try:
                    default_model = validate_model_override(default_model)
                except ValueError as exc:
                    raise AgentNexusError(str(exc), code=ErrorCode.INVALID_INPUT) from exc
        updated = await asyncio.to_thread(
            resolved_bot_store.update_settings,
            bot_id,
            owner_id=owner_id,
            name=(body.name.strip() if body.name is not None else current.name),
            description=(body.description if "description" in fields else current.description),
            status=body.status or current.status,
            default_model=default_model,
            behavior_mode=body.behavior_mode or current.behavior_mode,
        )
        if updated is None:
            raise AgentNexusError("Bot not found", code=ErrorCode.NOT_FOUND)

        binding = await asyncio.to_thread(resolved_bot_store.get_binding, bot_id)
        if binding is None:
            agent = await asyncio.to_thread(agent_store.get, current.agent_id)
            if agent is None:
                raise AgentNexusError("Bot agent not found", code=ErrorCode.NOT_FOUND)
            _, binding = await asyncio.to_thread(
                ensure_bot_for_agent,
                resolved_bot_store,
                agent,
                owner_id=owner_id,
            )
        if body.home_path is not None:
            home = Path(body.home_path).expanduser()
            if not home.is_absolute():
                raise AgentNexusError(
                    "Bot Home must be an absolute path",
                    code=ErrorCode.INVALID_INPUT,
                )
            home = home.resolve()
            (home / "scratch").mkdir(parents=True, exist_ok=True)
            binding = (
                await asyncio.to_thread(
                    resolved_bot_store.update_binding,
                    bot_id,
                    host_id=binding.host_id,
                    home_path=str(home),
                )
                or binding
            )

        if updated.status == "archived" and scheduled_task_store is not None:
            routine_owner_id = None if owner_id == RESERVED_USER_LOCAL else owner_id
            tasks = await asyncio.to_thread(
                scheduled_task_store.list,
                owner_user_id=routine_owner_id,
            )
            scheduler = getattr(request.app.state, "scheduled_task_scheduler", None)
            for task in tasks:
                if task.agent_id != updated.agent_id or task.state != "active":
                    continue
                paused = await asyncio.to_thread(
                    scheduled_task_store.update,
                    task.id,
                    state="paused",
                )
                if scheduler is not None and paused is not None:
                    scheduler.update(paused)
        return {"bot": _bot_object(updated, binding).model_dump(mode="json")}

    @router.get("/bots/{bot_id}/projects")
    async def list_bot_projects(bot_id: str, request: Request) -> dict[str, Any]:
        if resolved_bot_store is None:
            raise AgentNexusError("Bot store is not configured", code=ErrorCode.INTERNAL_ERROR)
        owner_id = bot_owner_id(_require_user(request, auth_provider))
        bot = await asyncio.to_thread(resolved_bot_store.get, bot_id, owner_id=owner_id)
        if bot is None:
            raise AgentNexusError("Bot not found", code=ErrorCode.NOT_FOUND)
        bindings = await asyncio.to_thread(resolved_bot_store.list_project_bindings, bot_id)
        return {
            "projects": [
                {
                    "id": b.id,
                    "bot_id": b.bot_id,
                    "project_id": b.project_id,
                    "checkout_root": b.checkout_root,
                    "default_branch": b.default_branch,
                    "created_at": b.created_at,
                    "updated_at": b.updated_at,
                }
                for b in bindings
            ]
        }

    @router.post("/bots/{bot_id}/projects")
    async def bind_bot_project(
        bot_id: str,
        body: BindProjectRequest,
        request: Request,
    ) -> dict[str, Any]:
        if resolved_bot_store is None:
            raise AgentNexusError("Bot store is not configured", code=ErrorCode.INTERNAL_ERROR)
        owner_id = bot_owner_id(_require_user(request, auth_provider))
        bot = await asyncio.to_thread(resolved_bot_store.get, bot_id, owner_id=owner_id)
        if bot is None:
            raise AgentNexusError("Bot not found", code=ErrorCode.NOT_FOUND)

        checkout = Path(body.checkout_root).expanduser()
        if not checkout.is_absolute():
            raise AgentNexusError(
                "checkout_root must be an absolute path",
                code=ErrorCode.INVALID_INPUT,
            )

        binding = await asyncio.to_thread(
            resolved_bot_store.bind_project,
            bot_id=bot_id,
            project_id=body.project_id,
            checkout_root=str(checkout.resolve()),
            default_branch=body.default_branch or "main",
        )
        return {
            "binding": {
                "id": binding.id,
                "bot_id": binding.bot_id,
                "project_id": binding.project_id,
                "checkout_root": binding.checkout_root,
                "default_branch": binding.default_branch,
                "created_at": binding.created_at,
                "updated_at": binding.updated_at,
            }
        }

    @router.delete("/bots/{bot_id}/projects/{project_id}")
    async def unbind_bot_project(
        bot_id: str,
        project_id: str,
        request: Request,
    ) -> dict[str, Any]:
        if resolved_bot_store is None:
            raise AgentNexusError("Bot store is not configured", code=ErrorCode.INTERNAL_ERROR)
        owner_id = bot_owner_id(_require_user(request, auth_provider))
        bot = await asyncio.to_thread(resolved_bot_store.get, bot_id, owner_id=owner_id)
        if bot is None:
            raise AgentNexusError("Bot not found", code=ErrorCode.NOT_FOUND)
        unbound = await asyncio.to_thread(
            resolved_bot_store.unbind_project,
            bot_id=bot_id,
            project_id=project_id,
        )
        return {"unbound": unbound}

    @router.get("/computers/local")
    async def get_local_computer(_request: Request) -> dict[str, Any]:
        from agentnexus.computers.local_host import LocalHostComputerProvider

        provider = LocalHostComputerProvider(resolved_bot_store) if resolved_bot_store else None
        caps = provider.capabilities() if provider else None
        return {
            "computer": {
                "id": "local_host",
                "name": "Local Workstation",
                "provider_kind": "local_host",
                "state": "running",
                "capabilities": {
                    "supports_git_worktrees": caps.supports_git_worktrees if caps else True,
                    "supports_leases": caps.supports_leases if caps else True,
                    "supports_containers": False,
                    "supports_gui": False,
                },
            }
        }

    return router
