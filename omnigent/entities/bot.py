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
