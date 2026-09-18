"""Built-in tool: send a message or shot contract to Seedance V3 Agent."""

from __future__ import annotations

from typing import Any

from agentnexus.tools.base import Tool, ToolContext


class SeedanceAgentMessageTool(Tool):
    """
    Send a message, shot contract, or canvas instruction to Seedance V3 Agent.

    Dispatched runner-side against the bound Seedance Project and Agent Session
    for the current Topic.
    """

    @classmethod
    def name(cls) -> str:
        return "seedance_agent_message"

    @classmethod
    def description(cls) -> str:
        return (
            "Disabled legacy delegation endpoint: it always rejects without contacting V3. "
            "Use seedance_read_canvas for read-only QA, seedance_edit_canvas initialize "
            "for binding, and validated direct generation for production."
        )

    def get_schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": "seedance_agent_message",
                "description": self.description(),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "task": {
                            "type": "string",
                            "description": (
                                "The task prompt or instruction to send to Seedance Agent, "
                                "e.g. '把已确认的镜头合同写入 Seedance 画布并准备生成'."
                            ),
                        },
                        "shot_ids": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": (
                                "Optional list of Cine shot IDs (e.g. ['S01-03']) to bind or process."
                            ),
                        },
                        "generation_allowed": {
                            "type": "boolean",
                            "description": (
                                "Whether image/video generation submission is allowed. "
                                "Defaults to false. When false, only cards and plans can be created."
                            ),
                        },
                        "model": {
                            "type": "string",
                            "description": (
                                "Optional model override for Seedance Agent Session "
                                "(e.g. 'gemini-3.7-flash-high' or 'deepseek-v4-flash')."
                            ),
                        },
                        "wait": {
                            "type": "boolean",
                            "description": (
                                "Optional short wait for Seedance Agent response. Defaults to true."
                            ),
                        },
                        "timeout_seconds": {
                            "type": "integer",
                            "description": (
                                "Maximum seconds to wait for initial response (10-300). Defaults to 60."
                            ),
                        },
                    },
                    "required": ["task"],
                },
            },
        }

    def invoke(self, arguments: str, ctx: ToolContext) -> str:
        return "seedance_agent_message is dispatched runner-side"
