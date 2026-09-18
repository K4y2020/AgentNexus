"""Tools package — Tool ABC, ToolManager, decorator, and built-in tools.

Public API (lazy):

- ``Tool``: Abstract base class for class-based tools.
- ``ToolManager``: Registry-based tool dispatch for workflows.
- ``ClientSideTool``: A tool presented to the LLM but executed by the caller.
- ``ClientSideToolSpec``: Configuration for a client-side tool.
- ``LocalPythonTool``: A tool backed by a local Python file in the agent image.
The ``tool`` decorator and ``ToolMetadata`` now live in
``omnigent_client.tools`` — import them from there.

Imports are lazy so that loading ``omnigent.tools`` does not pull
in heavy submodules (``manager`` transitively imports ``mcp``, which
clashes with the upstream ``mcp`` pip package when a custom tool
file is reloaded inside the subprocess runner). The Pythonic
``module.__getattr__`` hook resolves names on demand.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    # Type-checker view: real names with their actual types.
    from agentnexus.tools.base import Tool, ToolContext
    from agentnexus.tools.client_specified import (
        ClientSideTool,
        ClientSideToolSpec,
    )
    from agentnexus.tools.local import LocalPythonTool
    from agentnexus.tools.manager import ToolManager


# Map exported name → (submodule path, attribute name).
# Add an entry here when adding a new public re-export.
# NOTE: The `tool` decorator and `ToolMetadata` now live in the
# `omnigent_client.tools` package (see sdks/python-client/). Import
# them from there — they used to be re-exported here but the
# duplicate surface was removed in the SDK carve-out.
_LAZY_EXPORTS: dict[str, tuple[str, str]] = {
    "Tool": ("agentnexus.tools.base", "Tool"),
    "ToolContext": ("agentnexus.tools.base", "ToolContext"),
    "ClientSideTool": ("agentnexus.tools.client_specified", "ClientSideTool"),
    "ClientSideToolSpec": (
        "agentnexus.tools.client_specified",
        "ClientSideToolSpec",
    ),
    "LocalPythonTool": ("agentnexus.tools.local", "LocalPythonTool"),
    "ToolManager": ("agentnexus.tools.manager", "ToolManager"),
}


def __getattr__(name: str) -> Any:
    """
    Resolve public re-exports lazily on attribute access.

    Importing one symbol from this package no longer drags in
    every submodule. Subprocess-loaded tool files import the
    ``tool`` decorator from ``omnigent_client`` (not here),
    which avoids triggering the ``ToolManager`` → ``mcp`` import
    chain that conflicts with the upstream ``mcp`` pip package
    in subprocess environments — see ``list_builtin_tools.py``
    for context on the conflict.

    :param name: The attribute name being accessed.
    :returns: The resolved attribute from the appropriate
        submodule.
    :raises AttributeError: If ``name`` is not in
        :data:`_LAZY_EXPORTS`.
    """
    if name in _LAZY_EXPORTS:
        import importlib

        module_path, attr_name = _LAZY_EXPORTS[name]
        module = importlib.import_module(module_path)
        return getattr(module, attr_name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "ClientSideTool",
    "ClientSideToolSpec",
    "LocalPythonTool",
    "Tool",
    "ToolContext",
    "ToolManager",
]
