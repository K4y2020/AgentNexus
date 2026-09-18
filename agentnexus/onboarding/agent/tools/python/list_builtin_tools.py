"""List all built-in tools available in AgentNexus.

Returns the live registry of builtin tool names and their
descriptions, so the onboarding assistant always recommends
from the current set — not a stale hardcoded list.

Each tool class is imported individually from its own module to
avoid importing the ``omnigent.tools.builtins`` package (which
transitively pulls in modules that conflict with the ``mcp`` pip
package in subprocess environments).
"""

from agentnexus_client import tool

# Maps every builtin tool name to (module_path, class_name).
# This is the sole source of truth — when a new builtin is added,
# add it here. Each module is imported individually to avoid the
# transitive import chain from agentnexus.tools.builtins.__init__.
_TOOL_CLASSES: dict[str, tuple[str, str]] = {
    "download_file": ("agentnexus.tools.builtins.download_file", "DownloadFileTool"),
    "export_agent": ("agentnexus.tools.builtins.export_agent", "ExportAgentTool"),
    "list_files": ("agentnexus.tools.builtins.list_files", "ListFilesTool"),
    "search_conversations": (
        "agentnexus.tools.builtins.search_conversations",
        "SearchConversationsTool",
    ),
    "upload_file": ("agentnexus.tools.builtins.upload_file", "UploadFileTool"),
    "web_fetch": ("agentnexus.tools.builtins.web_fetch", "WebFetchTool"),
    "web_search": ("agentnexus.tools.builtins.web_search", "WebSearchTool"),
}


def _hindsight_available() -> bool:
    """Return True when the optional ``hindsight-client`` SDK is installed."""
    import importlib.util

    return importlib.util.find_spec("hindsight_client") is not None


def _nimble_available() -> bool:
    """Return True when the optional ``nimble-python`` SDK is installed."""
    import importlib.util

    return importlib.util.find_spec("nimble_python") is not None


# Both Nimble tools need a Nimble account and API key, so the `nimble` extra
# is the opt-in signal for the pair: advertised only when it is installed, so
# the assistant never recommends a tool the author cannot use. Runtime is
# unaffected: a spec may still enable either by name.
if _nimble_available():
    _TOOL_CLASSES.update(
        {
            "nimble_extract": (
                "agentnexus.tools.builtins.nimble_extract",
                "NimbleExtractTool",
            ),
            "nimble_research": (
                "agentnexus.tools.builtins.nimble_research",
                "NimbleResearchTool",
            ),
        }
    )


# Hindsight memory tools (optional ``hindsight`` extra). Advertised only when
# the SDK is installed, so the assistant never recommends unusable tools.
if _hindsight_available():
    _TOOL_CLASSES.update(
        {
            "hindsight_retain": ("agentnexus.tools.builtins.hindsight", "HindsightRetainTool"),
            "hindsight_recall": ("agentnexus.tools.builtins.hindsight", "HindsightRecallTool"),
            "hindsight_reflect": ("agentnexus.tools.builtins.hindsight", "HindsightReflectTool"),
        }
    )


@tool
def list_builtin_tools() -> str:
    """
    List all built-in tools available in AgentNexus.

    Returns tool names and descriptions. Call this before
    recommending tools for a new agent.
    """
    import importlib

    lines: list[str] = []
    for name in sorted(_TOOL_CLASSES):
        module_path, class_name = _TOOL_CLASSES[name]
        module = importlib.import_module(module_path)
        cls = getattr(module, class_name)
        lines.append(f"- {name}: {cls.description()}")

    return "\n".join(lines)
