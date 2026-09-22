"""AgentNexus: A declarative agent authoring and runtime framework."""

# Some libraries we transitively depend on call ``hashlib.md5()``
# without ``usedforsecurity=False`` for non-security content hashes.
# On FIPS-enabled OpenSSL builds the bare md5 constructor raises
# ``ValueError: digital envelope routines: EVP_DigestInit_ex disabled
# for FIPS``, which crashes the entire framework boot. Patch md5 here,
# at the package import boundary, so every consumer — including
# subprocesses spawned via ``-m omnigent`` in e2e tests — picks up
# the fix before any dependency import touches it. The flag is the
# standard Python 3.9+ opt-out for non-security md5 calls and is a
# harmless no-op on non-FIPS hosts.
import hashlib as _fips_safe_hashlib

_fips_safe_orig_md5 = _fips_safe_hashlib.md5


def _fips_safe_md5(*args, **kwargs):  # type: ignore[no-untyped-def]
    kwargs.setdefault("usedforsecurity", False)
    return _fips_safe_orig_md5(*args, **kwargs)


_fips_safe_hashlib.md5 = _fips_safe_md5

# Mirror legacy env prefixes onto their new ``AGENTNEXUS_*`` names
# before any submodule below reads the environment, so the dual-read
# backward-compat fallback is in effect for the entire package.
from agentnexus._env_compat import mirror_legacy_env as _mirror_legacy_env  # noqa: E402

_mirror_legacy_env()

# The public names below re-export lazily (PEP 562). This package init is on
# the hot path of every ``python -m agentnexus.<hook>`` subprocess Claude Code
# spawns — once per streamed text chunk (the TUI blocks on the MessageDisplay
# hook), per statusline refresh, and per tool call — and eagerly importing the
# datamodel/executor graph here cost those spawns ~250 ms each. Names resolve
# on first attribute access and are cached in module globals; the import-graph
# guards live in tests/test_claude_native_message_display_hook.py and the
# wall-clock trend in the ``native_hook_spawn`` benchmark journey.
import importlib  # noqa: E402
from typing import TYPE_CHECKING, Any  # noqa: E402

if TYPE_CHECKING:
    from agentnexus.inner.claude_sdk_executor import ClaudeSDKExecutor as ClaudeSDKExecutor
    from agentnexus.inner.codex_executor import CodexExecutor as CodexExecutor
    from agentnexus.inner.databricks_executor import DatabricksExecutor as DatabricksExecutor
    from agentnexus.inner.datamodel import (
        AgentDef as AgentDef,
    )
    from agentnexus.inner.datamodel import (
        Connection as Connection,
    )
    from agentnexus.inner.datamodel import (
        Credentials as Credentials,
    )
    from agentnexus.inner.datamodel import (
        History as History,
    )
    from agentnexus.inner.datamodel import (
        Memory as Memory,
    )
    from agentnexus.inner.datamodel import (
        MemoryConfig as MemoryConfig,
    )
    from agentnexus.inner.datamodel import (
        Message as Message,
    )
    from agentnexus.inner.datamodel import (
        ParamDef as ParamDef,
    )
    from agentnexus.inner.datamodel import (
        SessionState as SessionState,
    )
    from agentnexus.inner.executor import (
        Executor as Executor,
    )
    from agentnexus.inner.executor import (
        ExecutorConfig as ExecutorConfig,
    )
    from agentnexus.inner.executor import (
        ExecutorError as ExecutorError,
    )
    from agentnexus.inner.executor import (
        ExecutorEvent as ExecutorEvent,
    )
    from agentnexus.inner.executor import (
        TextChunk as TextChunk,
    )
    from agentnexus.inner.executor import (
        ToolCallComplete as ToolCallComplete,
    )
    from agentnexus.inner.executor import (
        ToolCallRequest as ToolCallRequest,
    )
    from agentnexus.inner.executor import (
        TurnCancelled as TurnCancelled,
    )
    from agentnexus.inner.executor import (
        TurnComplete as TurnComplete,
    )
    from agentnexus.inner.loader import load_agent_def as load_agent_def
    from agentnexus.inner.open_responses_sdk import OpenResponsesExecutor as OpenResponsesExecutor
    from agentnexus.inner.openai_agents_sdk_executor import (
        OpenAIAgentsSDKExecutor as OpenAIAgentsSDKExecutor,
    )
    from agentnexus.inner.policies import (
        FunctionPolicy as FunctionPolicy,
    )
    from agentnexus.inner.policies import (
        Policy as Policy,
    )
    from agentnexus.inner.policies import (
        PolicyAction as PolicyAction,
    )
    from agentnexus.inner.policies import (
        PolicyResult as PolicyResult,
    )
    from agentnexus.inner.policies import (
        PromptPolicy as PromptPolicy,
    )
    from agentnexus.inner.tools import (
        AgentTool as AgentTool,
    )
    from agentnexus.inner.tools import (
        CancellableFunctionTool as CancellableFunctionTool,
    )
    from agentnexus.inner.tools import (
        FunctionTool as FunctionTool,
    )
    from agentnexus.inner.tools import (
        HandoffTool as HandoffTool,
    )
    from agentnexus.inner.tools import (
        InheritedTool as InheritedTool,
    )
    from agentnexus.inner.tools import (
        MCPTool as MCPTool,
    )
    from agentnexus.inner.tools import (
        SkillTool as SkillTool,
    )
    from agentnexus.inner.tools import (
        Tool as Tool,
    )
    from agentnexus.inner.tracing import (
        disable_tracing as disable_tracing,
    )
    from agentnexus.inner.tracing import (
        enable_tracing as enable_tracing,
    )
    from agentnexus.inner.tracing import (
        is_tracing_enabled as is_tracing_enabled,
    )

# Public name → defining module for the always-present re-exports.
_LAZY_EXPORTS = {
    "AgentDef": "agentnexus.inner.datamodel",
    "Connection": "agentnexus.inner.datamodel",
    "Credentials": "agentnexus.inner.datamodel",
    "History": "agentnexus.inner.datamodel",
    "Memory": "agentnexus.inner.datamodel",
    "MemoryConfig": "agentnexus.inner.datamodel",
    "Message": "agentnexus.inner.datamodel",
    "ParamDef": "agentnexus.inner.datamodel",
    "SessionState": "agentnexus.inner.datamodel",
    "Executor": "agentnexus.inner.executor",
    "ExecutorConfig": "agentnexus.inner.executor",
    "ExecutorError": "agentnexus.inner.executor",
    "ExecutorEvent": "agentnexus.inner.executor",
    "TextChunk": "agentnexus.inner.executor",
    "ToolCallComplete": "agentnexus.inner.executor",
    "ToolCallRequest": "agentnexus.inner.executor",
    "TurnCancelled": "agentnexus.inner.executor",
    "TurnComplete": "agentnexus.inner.executor",
    "FunctionPolicy": "agentnexus.inner.policies",
    "Policy": "agentnexus.inner.policies",
    "PolicyAction": "agentnexus.inner.policies",
    "PolicyResult": "agentnexus.inner.policies",
    "PromptPolicy": "agentnexus.inner.policies",
    "AgentTool": "agentnexus.inner.tools",
    "CancellableFunctionTool": "agentnexus.inner.tools",
    "FunctionTool": "agentnexus.inner.tools",
    "HandoffTool": "agentnexus.inner.tools",
    "InheritedTool": "agentnexus.inner.tools",
    "MCPTool": "agentnexus.inner.tools",
    "SkillTool": "agentnexus.inner.tools",
    "Tool": "agentnexus.inner.tools",
    "load_agent_def": "agentnexus.inner.loader",
    "disable_tracing": "agentnexus.inner.tracing",
    "enable_tracing": "agentnexus.inner.tracing",
    "is_tracing_enabled": "agentnexus.inner.tracing",
}

# Optional executors resolve to ``None`` when their extra's dependencies are
# absent, matching the former eager try/except imports. Databricks also
# tolerates ``OSError``: its SDK can raise one probing credentials at import.
_OPTIONAL_EXPORTS = {
    "DatabricksExecutor": ("agentnexus.inner.databricks_executor", (OSError, ImportError)),
    "ClaudeSDKExecutor": ("agentnexus.inner.claude_sdk_executor", (ImportError,)),
    "OpenResponsesExecutor": ("agentnexus.inner.open_responses_sdk", (ImportError,)),
    "OpenAIAgentsSDKExecutor": ("agentnexus.inner.openai_agents_sdk_executor", (ImportError,)),
    "CodexExecutor": ("agentnexus.inner.codex_executor", (ImportError,)),
}


def __getattr__(name: str) -> Any:
    """Resolve a lazy re-export (or submodule) on first attribute access."""
    target = _LAZY_EXPORTS.get(name)
    if target is not None:
        value = getattr(importlib.import_module(target), name)
        globals()[name] = value
        return value
    optional = _OPTIONAL_EXPORTS.get(name)
    if optional is not None:
        target, absent_exceptions = optional
        try:
            value = getattr(importlib.import_module(target), name)
        except absent_exceptions:
            value = None
        globals()[name] = value
        return value
    # The eager imports used to bind ``inner`` (and other submodules touched
    # by them) as package attributes; keep ``omnigent.<submodule>`` access
    # working for consumers that only ran ``import agentnexus``.
    try:
        return importlib.import_module(f"{__name__}.{name}")
    except ModuleNotFoundError as exc:
        if exc.name != f"{__name__}.{name}":
            raise
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from None


def __dir__() -> list[str]:
    """Include the lazy re-exports in ``dir(omnigent)``."""
    return sorted(set(globals()) | set(__all__))


__all__ = [
    "AgentDef",
    "AgentTool",
    "CancellableFunctionTool",
    "ClaudeSDKExecutor",
    "CodexExecutor",
    "Connection",
    "Credentials",
    "DatabricksExecutor",
    "Executor",
    "ExecutorConfig",
    "ExecutorError",
    "ExecutorEvent",
    "FunctionPolicy",
    "FunctionTool",
    "HandoffTool",
    "History",
    "InheritedTool",
    "MCPTool",
    "Memory",
    "MemoryConfig",
    "Message",
    "OpenAIAgentsSDKExecutor",
    "OpenResponsesExecutor",
    "ParamDef",
    "Policy",
    "PolicyAction",
    "PolicyResult",
    "PromptPolicy",
    "SessionState",
    "SkillTool",
    "TextChunk",
    "Tool",
    "ToolCallComplete",
    "ToolCallRequest",
    "TurnCancelled",
    "TurnComplete",
    "disable_tracing",
    "enable_tracing",
    "is_tracing_enabled",
    "load_agent_def",
]
