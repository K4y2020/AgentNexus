"""Built-in tool: send a message or dispatch a task to another teammate bot."""

from __future__ import annotations

from typing import Any

from agentnexus.tools.base import Tool, ToolContext


class SendToTeammateTool(Tool):
    """
    Send a message or dispatch a task to another teammate bot.

    Enables cross-bot A2A communication between persistent teammates (e.g.
    Debby dispatching an implementation goal to Polly, or Polly reporting
    a status/result back to Debby).
    """

    @classmethod
    def name(cls) -> str:
        return "send_to_teammate"

    @classmethod
    def description(cls) -> str:
        return (
            "Send a message or dispatch a task to another teammate bot in this "
            "workspace (e.g. 'polly' or 'debby'). The message is delivered through "
            "the A2A coordination bus and automatically wakes the "
            "target bot in its workspace to execute the task or respond."
        )

    def get_schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": "send_to_teammate",
                "description": (
                    "Send a message or dispatch a task to another teammate bot in "
                    "this workspace (e.g. 'polly' or 'debby'). The message is "
                    "delivered through the A2A coordination bus and automatically wakes the "
                    "target bot in its workspace to execute the task or respond."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "teammate": {
                            "type": "string",
                            "description": (
                                "Name of the target teammate bot to contact, "
                                "e.g. 'polly' or 'debby'."
                            ),
                        },
                        "task": {
                            "type": "string",
                            "description": (
                                "The task prompt, instruction, or question to send "
                                "to the teammate."
                            ),
                        },
                        "intent": {
                            "type": "string",
                            "enum": [
                                "task.request",
                                "question",
                                "review.request",
                                "status.inquiry",
                                "task.result",
                            ],
                            "description": (
                                "Optional communication intent. Defaults to 'task.request'."
                            ),
                        },
                        "file_ids": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": (
                                "Optional source-session file ids to copy into the target "
                                "bot's A2A session. When omitted, files attached to the "
                                "latest human message are forwarded automatically. Pass an "
                                "empty list to suppress automatic forwarding."
                            ),
                        },
                        "in_reply_to": {
                            "type": "string",
                            "description": (
                                "Original A2A request ID. Required for task.result and forwarding "
                                "an incoming task; never guess from the latest conversation."
                            ),
                        },
                        "wait": {
                            "type": "boolean",
                            "description": (
                                "Optional short wait for a durable result. Defaults to false. "
                                "Background results are returned to this conversation; "
                                "end the turn and do not poll inbox/history."
                            ),
                        },
                        "timeout_seconds": {
                            "type": "integer",
                            "description": (
                                "Maximum seconds to wait when wait=true (0-300). Defaults to 30; "
                                "timeout leaves the request pending for automatic result delivery."
                            ),
                        },
                    },
                    "required": ["teammate", "task"],
                },
            },
        }

    def invoke(self, arguments: str, ctx: ToolContext) -> str:
        return "send_to_teammate is dispatched runner-side"
