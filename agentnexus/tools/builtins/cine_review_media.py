"""Independent audiovisual quality review for generated Cine media."""

from agentnexus.tools.base import Tool, ToolContext


class CineReviewMediaTool(Tool):
    @classmethod
    def name(cls):
        return "cine_review_media"

    @classmethod
    def description(cls):
        return (
            "Independently inspect the actual generated video and soundtrack against the "
            "production's quality requirements. Localize story, performance and rhythm defects "
            "and return an authenticated pass/revise/review receipt. Never accepts a caller's "
            "verdict. Uses the configured audiovisual model and bounded review allowance."
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
                        "production_dir": {
                            "type": "string",
                            "description": "Current production directory inside this Topic.",
                        }
                    },
                    "required": ["production_dir"],
                    "additionalProperties": False,
                },
            },
        }

    def invoke(self, arguments: str, ctx: ToolContext) -> str:
        return "cine_review_media is dispatched runner-side"
