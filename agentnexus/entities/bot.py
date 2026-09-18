"""Persistent teammate bot entities."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Bot:
    """A durable teammate identity backed by a versioned Agent definition."""

    id: str
    owner_id: str
    agent_id: str
    name: str
    description: str | None
    status: str
    default_model: str | None
    behavior_mode: str
    created_at: int
    updated_at: int | None = None


@dataclass
class BotComputerBinding:
    """The fixed local execution home assigned to one bot."""

    id: str
    bot_id: str
    host_id: str | None
    home_path: str
    created_at: int
    updated_at: int | None = None


@dataclass
class BotProjectBinding:
    """A bot's association to a project repository checkout."""

    id: str
    bot_id: str
    project_id: str
    checkout_root: str
    default_branch: str = "main"
    created_at: int = 0
    updated_at: int | None = None


@dataclass
class ComputerExecutionLease:
    """Exclusive write lease on a path for non-git or critical operations."""

    id: str
    computer_id: str
    bot_id: str
    session_id: str
    run_id: str
    path: str
    fence: int = 1
    expires_at: float = 0.0
    created_at: float = 0.0
