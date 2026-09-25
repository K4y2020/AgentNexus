"""Built-in tool: Cine camera prompt evidence retrieval."""

from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path
from typing import Any

from agentnexus.tools.base import Tool, ToolContext

logger = logging.getLogger(__name__)


class CineCameraEvidenceTool(Tool):
    """
    Search observed camera prompt evidence and action cinematography patterns from
    the Cine production corpus (Liblib / NeoWow observed prompts).
    """

    @classmethod
    def name(cls) -> str:
        return "cine_camera_evidence"

    @classmethod
    def description(cls) -> str:
        return (
            "Query real-world camera prompting evidence and cinematography patterns. "
            "Use to look up proven combinations of camera motion, shot framing, camera angle, "
            "focal length band, and combat action atoms. "
            "Returns condensed reference snippets as stylistic grammar reference. "
            "Never copy character/story details verbatim."
        )

    def get_schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name(),
                "description": self.description(),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": (
                                "Free-text search terms (e.g. '慢推', '环绕', '低机位')."
                            ),
                        },
                        "brief": {
                            "type": "string",
                            "description": (
                                "Natural language scene/shot description. Automatically parses "
                                "camera intent, motions, and action seeds."
                            ),
                        },
                        "motion": {
                            "type": "string",
                            "enum": [
                                "push-in",
                                "pull-out",
                                "pan-left",
                                "pan-right",
                                "tilt-up",
                                "tilt-down",
                                "orbit",
                                "tracking",
                                "static",
                                "handheld",
                                "dolly-zoom",
                                "crane",
                                "aerial",
                            ],
                            "description": "Camera motion filter.",
                        },
                        "shot_type": {
                            "type": "string",
                            "enum": [
                                "extreme-close-up",
                                "close-up",
                                "medium-close-up",
                                "medium-shot",
                                "full-shot",
                                "wide-shot",
                                "extreme-wide-shot",
                            ],
                            "description": "Shot framing filter.",
                        },
                        "camera_angle": {
                            "type": "string",
                            "enum": [
                                "eye-level",
                                "low-angle",
                                "high-angle",
                                "overhead",
                                "dutch-angle",
                            ],
                            "description": "Camera angle filter.",
                        },
                        "focal_band": {
                            "type": "string",
                            "enum": ["wide", "normal", "portrait", "telephoto"],
                            "description": "Focal length band.",
                        },
                        "limit": {
                            "type": "integer",
                            "description": "Max evidence snippets to return (1..10, default 5).",
                        },
                        "include_atoms": {
                            "type": "boolean",
                            "description": (
                                "Whether to return matched physical action atoms "
                                "(e.g. CQC, wuxia, chase)."
                            ),
                        },
                    },
                },
            },
        }

    def _resolve_cli_path(self) -> Path | None:
        # Relative to omnigent root
        root = Path(__file__).resolve().parent.parent.parent.parent
        cli_path = root / "examples" / "cine" / "camera-evidence" / "cli" / "query.mjs"
        if cli_path.is_file():
            return cli_path
        return None

    def invoke(self, arguments: str, ctx: ToolContext) -> str:
        try:
            params = json.loads(arguments) if isinstance(arguments, str) else arguments
        except Exception as e:
            return json.dumps({"error": f"Invalid arguments JSON: {e}"})

        cli_path = self._resolve_cli_path()
        if not cli_path:
            return json.dumps({"error": "Cine camera evidence CLI not found."})

        cmd = ["node", str(cli_path), "--format", "json"]

        if params.get("query"):
            cmd.extend(["--query", str(params["query"])])
        if params.get("brief"):
            cmd.extend(["--brief", str(params["brief"])])
        if params.get("motion"):
            cmd.extend(["--motion", str(params["motion"])])
        if params.get("shot_type"):
            cmd.extend(["--shot", str(params["shot_type"])])
        if params.get("camera_angle"):
            cmd.extend(["--angle", str(params["camera_angle"])])
        if params.get("focal_band"):
            cmd.extend(["--focal-band", str(params["focal_band"])])
        if params.get("limit"):
            cmd.extend(["--limit", str(params["limit"])])
        if params.get("include_atoms"):
            cmd.append("--atoms")

        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=30,
                encoding="utf-8",
                check=False,
            )
            if proc.returncode != 0:
                logger.warning("Cine camera evidence CLI failed: %s", proc.stderr)
                return json.dumps(
                    {
                        "error": f"Retrieval failed: {proc.stderr.strip()}",
                        "totalMatched": 0,
                        "snippets": [],
                    }
                )
            return proc.stdout.strip()
        except Exception as e:
            logger.exception("Error executing cine camera evidence CLI")
            return json.dumps({"error": str(e), "totalMatched": 0, "snippets": []})
