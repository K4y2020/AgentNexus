"""Built-in tool: read full Seedance V3 canvas content (read-only)."""

from __future__ import annotations

from typing import Any

from agentnexus.tools.base import Tool, ToolContext


class SeedanceReadCanvasTool(Tool):
    """
    Read the full contents of the Seedance V3 canvas for the current Topic/project.

    Read-only tool: reads cards, storyboard nodes, video/image prompts, scripts,
    edges, and generation statuses directly from the project snapshot.
    Does NOT create agent sessions, call models, or trigger generation.
    """

    @classmethod
    def name(cls) -> str:
        return "seedance_read_canvas"

    @classmethod
    def description(cls) -> str:
        return (
            "Read-only inspection of the Seedance V3 canvas for the current Topic/project. "
            "Retrieves full storyboard outlines, video/image prompts, script text, "
            "node connections (edges), and generation statuses without calling LLMs or triggering generation."
        )

    def get_schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": "seedance_read_canvas",
                "description": (
                    "Read-only inspection of the Seedance V3 canvas for the current Topic/project. "
                    "Retrieves full storyboard outlines, video/image prompts, script text, "
                    "node connections (edges), and generation statuses without calling LLMs or triggering generation."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "action": {
                            "type": "string", "enum": ["canvas", "models", "job"],
                            "description": "Default canvas. models reads the generation catalog (not chat models); job reads a returned job_id. Never search source code or guess API endpoints.",
                        },
                        "job_id": {"type": "string", "description": "Exact job ID returned by submit_generation, for action=job."},
                        "project_id": {
                            "type": "string",
                            "description": (
                                "Optional Seedance project ID (e.g. 'proj_xxx'). "
                                "Defaults to the project currently bound to this Topic."
                            ),
                        },
                        "node_types": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": (
                                "Optional filter by node type, e.g. ['storyboard', 'video_prompt', 'image_prompt', 'script']."
                            ),
                        },
                        "shot_ids": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": (
                                "Optional filter by shot IDs, e.g. ['S01', 'S02']."
                            ),
                        },
                        "include_edges": {
                            "type": "boolean",
                            "description": (
                                "Whether to include node connection edges. Defaults to true."
                            ),
                        },
                        "detail_level": {
                            "type": "string",
                            "enum": ["full", "summary"],
                            "description": (
                                "Detail level for card data. 'full' returns complete prompt texts and data; "
                                "'summary' returns compact metadata. Defaults to 'full'."
                            ),
                        },
                    },
                },
            },
        }

    def invoke(self, arguments: str, ctx: ToolContext) -> str:
        return "seedance_read_canvas is dispatched runner-side"
