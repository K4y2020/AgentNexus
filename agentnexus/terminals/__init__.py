"""Per-AP-process tmux terminal subsystem.

Hosts the :class:`TerminalRegistry`, the conversation-scoped registry
of live ``inner.terminal.TerminalInstance`` objects backing the
``sys_terminal_*`` tool family.

See ``designs/AGENTNEXUS_TERMINAL_BRIDGE.md`` for the design and the
:class:`TerminalInstance` documentation in
:mod:`omnigent.inner.terminal` for the underlying tmux machinery.
"""

from agentnexus.terminals.registry import TerminalListEntry, TerminalRegistry

__all__ = [
    "TerminalListEntry",
    "TerminalRegistry",
]
