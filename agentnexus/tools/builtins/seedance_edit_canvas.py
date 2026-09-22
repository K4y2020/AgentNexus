"""Built-in tool: direct mutation of Seedance V3 canvas (nodes, edges, prompts, and generation)."""

from __future__ import annotations

from typing import Any

from agentnexus.tools.base import Tool, ToolContext


class SeedanceEditCanvasTool(Tool):
    """
    Directly mutate or update the Seedance V3 canvas for the current Topic.

    Allows creating, updating, deleting nodes/cards, connecting/disconnecting edges,
    or submitting generation jobs directly on the Seedance V3 canvas without going
    through the Seedance Agent middleman.
    """

    @classmethod
    def name(cls) -> str:
        return "seedance_edit_canvas"

    @classmethod
    def description(cls) -> str:
        return (
            "Directly create, update, connect, or delete cards and nodes on the Seedance V3 canvas, "
            "or submit authorized generation jobs, without delegating through the V3 Agent middleman. "
            "Scoped to the current Topic's bound Seedance project, with optimistic concurrency revision "
            "checks, safety guards, and automatic read-after-write verification."
        )

    def get_schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": "seedance_edit_canvas",
                "description": (
                    "Directly create, update, connect, or delete cards and nodes on the Seedance V3 canvas, "
                    "or submit authorized generation jobs, without delegating through the V3 Agent middleman. "
                    "Scoped to the current Topic's bound Seedance project, with optimistic concurrency revision "
                    "checks, safety guards, and automatic read-after-write verification."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "action": {
                            "type": "string",
                            "enum": [
                                "initialize",
                                "import_storyboard",
                                "export_image",
                                "validate_generation",
                                "create_node",
                                "update_node",
                                "delete_node",
                                "connect",
                                "disconnect",
                                "submit_generation",
                            ],
                            "description": "The canvas mutation action to perform.",
                        },
                        "project_id": {
                            "type": "string",
                            "description": (
                                "Optional Seedance project ID. Defaults to the project bound to the current Topic."
                            ),
                        },
                        "storyboard_file": {
                            "type": "string", "description": "For import_storyboard: native cine-storyboard JSON path inside the current Topic workspace.",
                        },
                        "job_id": {"type": "string", "description": "For export_image: exact succeeded V3 image or video job ID. Downloads its output into this Topic; no generation or filesystem search."},
                        "output_path": {"type": "string", "description": "For export_image: optional destination inside this Topic. Existing different files are not overwritten. Default outputs/v3-media/<hash>.<actual-format>."},
                        "output_index": {"type": "integer", "minimum": 0, "description": "For export_image: zero-based index in the job's outputRefs, default 0."},
                        "script_file": {
                            "type": "string", "description": "For import_storyboard: matching native cine-script JSON path in this Topic. No ad hoc conversion scripts.",
                        },
                        "episode_nodes": {
                            "type": "object", "additionalProperties": {"type": "string"},
                            "description": "Optional for import_storyboard: episode number to an existing storyboard node ID. When omitted, the importer deterministically reuses or creates Cine's summary and EPxx storyboard cards, then imports atomically.",
                        },
                        "node_id": {
                            "type": "string",
                            "description": (
                                "Target node ID (e.g. 'node_xxx'). Required for update_node, delete_node, "
                                "and submit_generation."
                            ),
                        },
                        "node_type": {
                            "type": "string",
                            "enum": [
                                "video_prompt",
                                "image_prompt",
                                "storyboard",
                                "script",
                                "text",
                                "image",
                                "audio",
                            ],
                            "description": "Portraits and turnaround sheets both use image_prompt. When a portrait exists, leave it unchanged and create a separate turnaround node, then connect portrait -> turnaround using references. Reuse an existing matching turnaround on retry. Defaults to video_prompt.",
                        },
                        "title": {
                            "type": "string",
                            "description": "Card title (e.g. 'S03 - Extreme Close-Up'). Used for create_node and update_node.",
                        },
                        "prompt": {
                            "type": "string",
                            "description": (
                                "Prompt text for video/image generation (convenience shortcut for data.prompt). "
                                "Used for create_node, update_node, and submit_generation."
                            ),
                        },
                        "brief": {
                            "type": "string",
                            "description": (
                                "Shot description, action summary, or visual brief (convenience shortcut for data.brief). "
                                "Used for create_node and update_node."
                            ),
                        },
                        "duration_seconds": {
                            "type": "integer",
                            "description": "Shot duration in seconds. Used for video_prompt create_node and update_node.",
                        },
                        "camera": {
                            "type": "object",
                            "description": (
                                "Camera parameters, e.g. {'angle': 'high-angle', 'motion': 'pan-down', 'speed': 'slow'}."
                            ),
                        },
                        "aspect_ratio": {
                            "type": "string",
                            "description": "Aspect ratio, e.g. '16:9', '9:16', '2.35:1', or '1:1'.",
                        },
                        "data": {
                            "type": "object",
                            "description": "Additional node data. For a turnaround based on an existing confirmed portrait, set turnaroundSourceNodeId to the portrait node ID; generation requires its resolved image reference. Do not copy activeOutputRef onto the new card.",
                        },
                        "patch": {
                            "type": "object",
                            "description": "Raw patch dictionary for update_node.",
                        },
                        "parent_id": {
                            "type": "string",
                            "description": "Optional parent node ID (e.g. group or parent card).",
                        },
                        "expected_revision": {
                            "type": "integer",
                            "description": (
                                "Optimistic concurrency revision check for update_node and delete_node. "
                                "If specified, operation will be rejected if the canvas node has been modified "
                                "by another actor (e.g. human user editing on the canvas UI)."
                            ),
                        },
                        "confirm": {
                            "type": "boolean",
                            "description": "Required confirmation flag for delete_node. Must be true to delete.",
                        },
                        "from_node_id": {
                            "type": "string",
                            "description": "Source node ID for connect action.",
                        },
                        "to_node_id": {
                            "type": "string",
                            "description": "Target node ID for connect action.",
                        },
                        "kind": {
                            "type": "string",
                            "enum": ["sequence", "references", "parent"],
                            "description": "Connection kind for connect action. Defaults to 'references'.",
                        },
                        "edge_id": {
                            "type": "string",
                            "description": "Edge ID to disconnect for disconnect action.",
                        },
                        "generation_kind": {
                            "type": "string",
                            "enum": ["image", "video"],
                            "description": "Kind for submit_generation and validate_generation. If omitted, cast/art and storyboard frame pointers imply image; otherwise video. Use image for character sheets.",
                        },
                        "generation_allowed": {
                            "type": "boolean",
                            "description": (
                                "Explicit authorization flag for submit_generation. Must be true to trigger "
                                "paid generation workers."
                            ),
                        },
                        "production_dir": {
                            "type": "string",
                            "description": "Artifact directory in this Topic. Canonical or one unique *-cast.json is accepted without copying. Cast images validate cast and source text only; no outline required. Storyboards still validate the full upstream chain.",
                        },
                        "source_text": {
                            "type": "string",
                            "description": "Source or approved treatment text inside this session workspace for native quote validation.",
                        },
                        "production_stage": {
                            "type": "string", "enum": ["cast", "art", "storyboard"],
                            "description": "Validated artifact owning the prompt. Video requires storyboard.",
                        },
                        "model": {
                            "type": "string",
                            "description": "Generation catalog value from seedance_read_canvas(action=models), not a chat model. Uses node.data.model if omitted; otherwise returns selection-required instead of guessing. Preserve the user's model choice.",
                        },
                        "production_pointer": {
                            "type": "string",
                            "description": "Exact prompt JSON pointer: cast /characters/0/image/sheet; art /scenes/0/image/sheet; storyboard image /episodes/0/segments/0/cuts/0/frame; video /episodes/0/segments/0/h3Prompt. Submitted prompt must match exactly. validate_generation runs the same gate without sending a V3 command.",
                        },
                    },
                    "required": ["action"],
                },
            },
        }

    def invoke(self, arguments: str, ctx: ToolContext) -> str:
        return "seedance_edit_canvas is dispatched runner-side"
