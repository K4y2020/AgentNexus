"""Built-in tool: save a persistent teammate memory across sessions."""

from __future__ import annotations

from typing import Any

from agentnexus.tools.base import Tool, ToolContext


class SaveTeammateMemoryTool(Tool):
    """Save a persistent working preference, project convention, or fact for this teammate."""

    @classmethod
    def name(cls) -> str:
        return "save_teammate_memory"

    @classmethod
    def description(cls) -> str:
        return (
            "Save a persistent working preference, project rule, architectural convention, "
            "or user requirement for this teammate bot. The memory is stored durably in the "
            "database and automatically remembered across all future conversations."
        )

    def get_schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": "save_teammate_memory",
                "description": (
                    "Save a persistent working preference, project rule, or user requirement "
                    "for this teammate bot. The memory is stored durably in the database."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "content": {
                            "type": "string",
                            "description": (
                                "The concise statement or preference to remember across "
                                "sessions, e.g. '用户倾向极客风格', '页面位于 U:\\AI\\x.html'."
                            ),
                        },
                    },
                    "required": ["content"],
                },
            },
        }

    def invoke(self, arguments: str, ctx: ToolContext) -> str:
        return "save_teammate_memory is dispatched runner-side"
