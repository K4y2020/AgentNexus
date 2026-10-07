"""TypeSafe JEV (System One) judgment tool for Cine agents.

Lets the Cine agent make fast, typed semantic decisions — shot usability,
framing/camera classification, dialogue quality gates, prompt conflict
detection — without a heavyweight LLM round-trip. State + typed questions go
to api.typesafe.ai/v1/systemone; answers come back typed with calibrated
probabilities and confidence.
"""

from agentnexus.tools.base import Tool, ToolContext


class CineJevJudgeTool(Tool):
    @classmethod
    def name(cls):
        return "cine_jev_judge"

    @classmethod
    def description(cls):
        return (
            "Ask the TypeSafe JEV System One model a batch of typed questions against a state. "
            "Returns calibrated decisions: choice + probability distribution + confidence, "
            "yes/no probabilities (noul), and rubric scores. Use for film-language judgments: "
            "script dialogue quality gates, storyboard-script alignment checks, and video prompt "
            "conflict detection. State is text or structured JSON only: JEV cannot see images, "
            "audio, or video, so do not use it as a visual evidence receipt. It judges only "
            "what the state contains: put the text under judgment in it (the shot prompts, "
            "ledger entries or lines themselves), not a summary, and keep your own verdict out "
            "of the criteria. Its answers are not a production check: a stage file in a "
            "production directory is checked by production.py check. Questions are "
            "evaluated in parallel "
            "(~150ms); adding questions does not add latency. NOT for generating prose — "
            "use the main model for writing."
        )

    def get_schema(self):
        return {
            "type": "function",
            "function": {
                "name": self.name(),
                "description": self.description(),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "state": {
                            "type": "object",
                            "description": "Structured context to judge: shot descriptions, "
                            "dialogue lines, prompt text, candidate lists, or review tables.",
                        },
                        "questions": {
                            "type": "object",
                            "description": "Map of question-id to question spec. Each spec: "
                            '{"type": "choice"|"noul"|"score", "instructions": "...", '
                            '"criteria": {...} for choice (option-id -> description) or '
                            "array of level descriptions for score. Ask independent questions "
                            "together — they run in parallel.",
                            "additionalProperties": {
                                "type": "object",
                                "properties": {
                                    "type": {
                                        "type": "string",
                                        "enum": ["choice", "noul", "score"],
                                    },
                                    "instructions": {"type": "string"},
                                    "criteria": {},
                                },
                                "required": ["type", "instructions"],
                            },
                        },
                        "model": {
                            "type": "string",
                            "description": "Optional model id, defaults to jev-latest.",
                        },
                    },
                    "required": ["state", "questions"],
                },
            },
        }

    def invoke(self, arguments: str, ctx: ToolContext) -> str:
        return "cine_jev_judge is dispatched runner-side"
