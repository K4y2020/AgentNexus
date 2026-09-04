"""Small shared helpers for persistent Bot identity and Local Host homes."""

from __future__ import annotations

from pathlib import Path

from omnigent.entities import Agent, Bot, BotComputerBinding
from omnigent.server.auth import RESERVED_USER_LOCAL
from omnigent.stores.bot_store import BotStore


def bot_owner_id(user_id: str | None) -> str:
    """Normalize single-user mode to the same durable owner used by hosts."""
    return user_id or RESERVED_USER_LOCAL


def default_bot_home(bot_id: str) -> Path:
    return Path.home() / ".omnigent" / "bots" / bot_id


def ensure_bot_home(
    bot_store: BotStore,
    bot: Bot,
    *,
    host_id: str | None = None,
) -> BotComputerBinding:
    """Create the fixed Bot Home and return its durable local binding."""
    home = default_bot_home(bot.id)
    binding = bot_store.ensure_binding(
        bot_id=bot.id,
        host_id=host_id,
        home_path=str(home),
    )
    Path(binding.home_path, "scratch").mkdir(parents=True, exist_ok=True)
    return binding


def ensure_bot_for_agent(
    bot_store: BotStore,
    agent: Agent,
    *,
    owner_id: str,
    host_id: str | None = None,
) -> tuple[Bot, BotComputerBinding]:
    bot = bot_store.ensure_for_agent(
        owner_id=owner_id,
        agent_id=agent.id,
        name=agent.name,
        description=agent.description,
    )
    return bot, ensure_bot_home(bot_store, bot, host_id=host_id)


def bot_scratch_path(binding: BotComputerBinding) -> str:
    return str(Path(binding.home_path) / "scratch")
