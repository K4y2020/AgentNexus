"""Restore durable model selection on background turns without an explicit model."""

from typing import Any

import httpx


async def restore_turn_model(
    body: dict[str, Any],
    session_id: str,
    client: httpx.AsyncClient,
) -> dict[str, Any]:
    explicit = body.get("model_override")
    if explicit is not None:
        if not isinstance(explicit, str) or not explicit.strip():
            raise ValueError("Invalid explicit turn model")
        return body
    response = await client.get(f"/v1/sessions/{session_id}", timeout=10.0)
    response.raise_for_status()
    snapshot = response.json()
    if not isinstance(snapshot, dict) or "model_override" not in snapshot:
        raise ValueError("Cannot resolve saved model: session snapshot lacks model_override")
    model = snapshot["model_override"]
    parent_id = snapshot.get("parent_session_id")
    worker = snapshot.get("sub_agent_name")
    if isinstance(parent_id, str) and isinstance(worker, str) and worker:
        from omnigent.runner.tool_dispatch import _preferred_subagent_model

        preference = await _preferred_subagent_model(
            server_client=client,
            conversation_id=parent_id,
            sub_agent_name=worker,
        )
        if preference not in (None, ""):
            model = preference
    if model is None and not parent_id and snapshot.get("bot_id"):
        response = await client.get("/v1/bots", timeout=10.0)
        response.raise_for_status()
        roster = response.json()
        rows = roster.get("bots") if isinstance(roster, dict) else None
        if not isinstance(rows, list):
            raise ValueError("Cannot resolve saved Bot model: invalid roster")
        matches = [
            row["bot"]
            for row in rows
            if isinstance(row, dict)
            and isinstance(row.get("bot"), dict)
            and row["bot"].get("id") == snapshot["bot_id"]
        ]
        if len(matches) != 1 or "default_model" not in matches[0]:
            raise ValueError("Cannot resolve saved Bot model: owning Bot unavailable")
        model = matches[0]["default_model"]
    if model is not None and (not isinstance(model, str) or not model.strip()):
        raise ValueError("Cannot resolve saved model: invalid model_override")
    return {**body, "model_override": model}
