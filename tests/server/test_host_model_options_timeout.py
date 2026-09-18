"""Timeout-budget invariants for host model-option discovery."""

from agentnexus import claude_native, model_catalog
from agentnexus.server.routes import hosts


def test_outer_model_options_timeout_contains_provider_and_probe_budgets() -> None:
    """The server must not cancel a valid sequential endpoint→CLI fallback."""
    nested_worst_case = model_catalog._HTTP_TIMEOUT_S + claude_native._CLAUDE_MODEL_PROBE_TIMEOUT_S

    assert nested_worst_case < hosts._MODEL_OPTIONS_TIMEOUT_S
