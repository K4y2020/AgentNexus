"""Owned static model tables for Smart Routing.

Most pre-launch picker listings use live harness probes. Native CLIs without
model discovery retain release-curated stand-ins here, with ownership and
discovery gaps recorded beside the router's rankings and arm menus.
"""

from __future__ import annotations

from dataclasses import dataclass

from agentnexus.onboarding.provider_config import SUBSCRIPTION_KIND


@dataclass(frozen=True)
class StaticModelFallback:
    """A release-curated model list with auditable ownership and provenance."""

    model_ids: tuple[str, ...]
    owner: str
    provenance: str
    discovery_gap: str


# These native CLIs do not expose a model discovery command to the host.
# Keep their picker stand-ins here with explicit ownership until a live probe
# is available; the host must never infer them from an unrelated provider.
_ANTIGRAVITY_PICKER = StaticModelFallback(
    model_ids=(
        "gemini-2.5-pro",
        "gemini-2.5-flash",
        "gemini-2.0-flash",
        "gemini-1.5-pro",
    ),
    owner="Antigravity native launch picker (agentnexus.host.connect)",
    provenance="release-curated Antigravity CLI model choices",
    discovery_gap="the Antigravity CLI has no host-side model listing command",
)
ANTIGRAVITY_PICKER_MODELS = _ANTIGRAVITY_PICKER.model_ids

_CODEBUDDY_PICKER = StaticModelFallback(
    model_ids=(
        "auto",
        "hy4-preview",
        "hy3-x",
        "hy3",
        "deepseek-v4-pro",
        "deepseek-v4-flash",
        "glm-5.3",
        "glm-5.3-flash",
        "glm-5.2",
        "glm-5.1",
        "glm-5v-turbo",
        "kimi-k3-2",
        "kimi-k2.7",
        "kimi-k2.6",
        "kimi-k2.5",
        "minimax-m3-pay",
    ),
    owner="Codebuddy native launch picker (agentnexus.host.connect)",
    provenance="release-curated Codebuddy CLI model choices",
    discovery_gap="the Codebuddy CLI has no host-side model listing command",
)
CODEBUDDY_PICKER_MODELS = _CODEBUDDY_PICKER.model_ids

# Stand-ins served only when the live listing comes back empty, so the picker
# still offers launchable choices.
_PI_PICKER = StaticModelFallback(
    model_ids=("auto", "claude-sonnet-5", "gpt-5.6-sol"),
    owner="Pi native launch picker (agentnexus.host.connect)",
    provenance="release-curated Pi model choices",
    discovery_gap="Pi lists models only after a provider is configured on the host",
)
PI_PICKER_MODELS = _PI_PICKER.model_ids

_CURSOR_PICKER = StaticModelFallback(
    model_ids=("auto-smart", "composer-2.5", "gpt-5.6-sol", "claude-sonnet-5"),
    owner="Cursor native launch picker (agentnexus.host.connect)",
    provenance="release-curated cursor-agent model choices",
    discovery_gap="`cursor-agent models` fails when the CLI is missing or signed out",
)
CURSOR_PICKER_MODELS = _CURSOR_PICKER.model_ids

# Alias defaults for an Anthropic gateway added from the web UI with at most one
# model: the tier aliases first, then Claude ids the proxy serves via other models.
_ANTHROPIC_GATEWAY_ALIAS_TARGETS = StaticModelFallback(
    model_ids=(
        "claude-sonnet-5",
        "claude-opus-5",
        "claude-haiku-4-5",
        "claude-fable-5",
        "claude-opus-4-6-thinking",
        "gpt-5.6-terra",
        "gpt-5.6-sol",
    ),
    owner="Anthropic gateway creation (agentnexus.server.routes.gateways)",
    provenance="release-curated alias targets for Anthropic-compatible proxy gateways",
    discovery_gap="a gateway's /v1/models listing does not say which id backs each alias",
)
_ANTHROPIC_GATEWAY_REMAPPED_IDS = StaticModelFallback(
    model_ids=("claude-opus-4-8", "claude-haiku-4-5", "claude-fable-5"),
    owner="Anthropic gateway creation (agentnexus.server.routes.gateways)",
    provenance="Claude Code model ids the proxy serves through other backing models",
    discovery_gap="a gateway's /v1/models listing does not say which id backs each alias",
)
#: ``(alias, target)`` pairs in the order the gateway form applies them.
ANTHROPIC_GATEWAY_DEFAULT_ALIASES: tuple[tuple[str, str], ...] = tuple(
    zip(
        ("sonnet", "opus", "haiku", "fable", *_ANTHROPIC_GATEWAY_REMAPPED_IDS.model_ids),
        _ANTHROPIC_GATEWAY_ALIAS_TARGETS.model_ids,
        strict=True,
    )
)


#: Curated preference ORDER for codex's current arms — a ranking hint only
#: (preferred first), consumed by the Databricks live-discovery ranker to
#: sort servable ids. It never invents picker rows: ids absent from the live
#: listing are simply not ranked by it.
_CODEX_ARM_PREFERENCE = StaticModelFallback(
    model_ids=("gpt-5.6-sol", "gpt-5.6-luna", "gpt-5.6-terra", "gpt-5.5"),
    owner="Databricks model discovery (agentnexus.databricks_model_discovery)",
    provenance="AgentNexus's release-curated Codex arm ordering",
    discovery_gap="a workspace listing ranks models by neither recency nor capability",
)

_STATIC_MODEL_FALLBACKS: dict[tuple[str, str], StaticModelFallback] = {
    (SUBSCRIPTION_KIND, "codex"): _CODEX_ARM_PREFERENCE,
}


def static_model_fallback(provider_kind: str, cli: str) -> StaticModelFallback | None:
    """Return the owned fallback table for a provider kind and CLI, if registered."""
    return _STATIC_MODEL_FALLBACKS.get((provider_kind, cli))


#: Codex's launch default when nothing else names a model. The bundled
#: OpenAI catalog's newest row is a bare family alias (``gpt-5.6``) that
#: codex rejects, so a codex launch defaults to a concrete variant from
#: codex's own catalog — dotted spelling, since the Databricks hyphenated
#: form 400s against codex's own backend.
_CODEX_LAUNCH_DEFAULT = StaticModelFallback(
    model_ids=("gpt-5.6-sol",),
    owner="Codex native launch (agentnexus.inner.codex_executor)",
    provenance="codex's own catalog slug for the cheapest current arm",
    discovery_gap=(
        "the launch default is resolved before any app-server probe can "
        "answer, and codex rejects the bundled catalog's newest row (a bare "
        "family alias)"
    ),
)

CODEX_DEFAULT_MODEL = _CODEX_LAUNCH_DEFAULT.model_ids[0]


# ── Smart Routing ───────────────────────────────────────────────────────────
#
# The router's static tables. A live per-session catalog wins wherever one is
# in reach (``omnigent.server.smart_routing.fetch_runner_models``) and a
# deployment's ``routing.*`` settings override each table wholesale; these are
# what a router that can reach neither falls back to.

_SMART_ROUTING_FALLBACKS: dict[str, StaticModelFallback] = {
    "claude_ladder": StaticModelFallback(
        model_ids=(
            "databricks-claude-haiku-4-5",
            "databricks-claude-sonnet-4-6",
            "databricks-claude-sonnet-5",
            "databricks-claude-opus-4-8",
        ),
        owner="Smart Routing (agentnexus.server.smart_routing)",
        provenance="AI Gateway Claude serving endpoints, cheapest → most powerful",
        discovery_gap=(
            "the router picks before a session's live model catalog is reachable, "
            "and a gateway listing ranks models by neither cost nor capability"
        ),
    ),
    "gpt_ladder": StaticModelFallback(
        model_ids=(
            "databricks-gpt-5-4-nano",
            "databricks-gpt-5-4-mini",
            "databricks-gpt-5-4",
            "databricks-gpt-5-5",
        ),
        owner="Smart Routing (agentnexus.server.smart_routing)",
        provenance="AI Gateway GPT serving endpoints, cheapest → most powerful",
        discovery_gap=(
            "the router picks before a session's live model catalog is reachable, "
            "and a gateway listing ranks models by neither cost nor capability"
        ),
    ),
    "pi_ladder": StaticModelFallback(
        model_ids=(
            "databricks-gpt-5-4-nano",
            "databricks-claude-haiku-4-5",
            "databricks-gpt-5-4-mini",
            "databricks-claude-sonnet-4-6",
            "databricks-claude-sonnet-5",
            "databricks-gpt-5-4",
            "databricks-gpt-5-5",
            "databricks-claude-opus-4-8",
        ),
        owner="Smart Routing (agentnexus.server.smart_routing)",
        provenance="the Claude and GPT ladders interleaved by cost, for multi-model pi",
        discovery_gap=(
            "the router picks before a session's live model catalog is reachable, "
            "and a gateway listing ranks models by neither cost nor capability"
        ),
    ),
    "current_generation_gpt": StaticModelFallback(
        model_ids=(
            "databricks-glm-5-2",
            "databricks-gpt-5-6-luna",
            "databricks-gpt-5-6-sol",
        ),
        owner="Smart Routing (agentnexus.server.smart_routing)",
        provenance="the external router's own current arms, offered so a pick keeps its endpoint",
        discovery_gap=(
            "the router picks before a session's live model catalog is reachable, "
            "and a gateway listing ranks models by neither cost nor capability"
        ),
    ),
    "task_v1_claude_arms": StaticModelFallback(
        model_ids=("claude-opus-4-8", "claude-sonnet-5"),
        owner="Smart Routing (agentnexus.server.smart_routing)",
        provenance="the task_v1 router's Claude arm menu, which it requires in full",
        discovery_gap="the router's arm menu is part of its request contract, not a catalog",
    ),
    "task_v1_codex_arms": StaticModelFallback(
        model_ids=("glm-5-2", "gpt-5-6-sol", "gpt-5-6-luna"),
        owner="Smart Routing (agentnexus.server.smart_routing)",
        provenance="the task_v1 router's codex arm menu, which it requires in full",
        discovery_gap="the router's arm menu is part of its request contract, not a catalog",
    ),
    "family_fallbacks": StaticModelFallback(
        model_ids=("claude-sonnet-5", "gpt-5-6-luna"),
        owner="Smart Routing (agentnexus.server.smart_routing)",
        provenance="one arm per family (claude, gpt), both frozen members of the task_v1 menus",
        discovery_gap="a workspace that serves no endpoint for the picked arm needs a pinned one",
    ),
    "pi_excluded": StaticModelFallback(
        model_ids=(
            "databricks-claude-haiku-4-5",
            "databricks-gpt-5-5",
            "databricks-gpt-5-5-pro",
            "databricks-gpt-5-6-luna",
            "databricks-gpt-5-6-terra",
            "databricks-gpt-5-6-sol",
        ),
        owner="Smart Routing (agentnexus.server.smart_routing)",
        provenance="probed: pi's own gateway 400s on each of these",
        discovery_gap="a gateway listing advertises these without pi's request-shape limits",
    ),
    "codex_catalog_clone_source": StaticModelFallback(
        model_ids=("gpt-5.6-luna",),
        owner="Codex extended catalog (agentnexus.inner.codex_executor)",
        provenance="codex's own bundled catalog slug for the cheapest current arm",
        discovery_gap="codex's bundled catalog carries no entry for a gateway-only arm to clone",
    ),
}

#: Claude serving endpoints the router ranks, cheapest → most powerful.
SMART_ROUTING_CLAUDE_LADDER = _SMART_ROUTING_FALLBACKS["claude_ladder"].model_ids

#: GPT serving endpoints the router ranks, cheapest → most powerful.
SMART_ROUTING_GPT_LADDER = _SMART_ROUTING_FALLBACKS["gpt_ladder"].model_ids

#: Both ladders interleaved by cost, for the multi-model pi harness.
SMART_ROUTING_PI_LADDER = _SMART_ROUTING_FALLBACKS["pi_ladder"].model_ids

#: The router's own current gpt-family arms (GLM included), offered as
#: candidates so a routed arm resolves to its own endpoint.
SMART_ROUTING_CURRENT_GENERATION_GPT = _SMART_ROUTING_FALLBACKS["current_generation_gpt"].model_ids

#: The ``task_v1`` router's Claude arm menu, most powerful first.
SMART_ROUTING_TASK_V1_CLAUDE_ARMS = _SMART_ROUTING_FALLBACKS["task_v1_claude_arms"].model_ids

#: The ``task_v1`` router's codex arm menu.
SMART_ROUTING_TASK_V1_CODEX_ARMS = _SMART_ROUTING_FALLBACKS["task_v1_codex_arms"].model_ids

#: One fixed fallback arm per family, ordered ``(claude, gpt)``.
SMART_ROUTING_FAMILY_FALLBACKS = _SMART_ROUTING_FALLBACKS["family_fallbacks"].model_ids

#: Models pi's own gateway rejects, so the router may not pick them under pi.
SMART_ROUTING_PI_EXCLUDED = _SMART_ROUTING_FALLBACKS["pi_excluded"].model_ids

#: The codex catalog entry a gateway-only arm is cloned from.
CODEX_CATALOG_CLONE_SOURCE_SLUG = _SMART_ROUTING_FALLBACKS["codex_catalog_clone_source"].model_ids[
    0
]
