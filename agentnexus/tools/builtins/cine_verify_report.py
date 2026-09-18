"""Evidence-backed acceptance of Cine source reports."""

from agentnexus.tools.base import Tool, ToolContext


class CineVerifyReportTool(Tool):
    @classmethod
    def name(cls):
        return "cine_verify_report"

    @classmethod
    def description(cls):
        return (
            "For adaptation scope, check whole-film story sections and representative images, "
            "without requiring every source shot to be reviewed. For full/sample forensic scope, "
            "verify Cine visual report coverage from this session's canonical source revision "
            "and actual image tool receipts. Old imported model_reviewed labels are not proof. "
            "Full scope cannot pass on a short sample. "
            "Does not verify audio, motion or semantic accuracy."
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
                        "scope": {"type": "string", "enum": ["adaptation", "full", "sample"]},
                        "start_seconds": {
                            "type": "number",
                            "description": "Required only for sample scope.",
                        },
                        "end_seconds": {
                            "type": "number",
                            "description": "Required only for sample scope.",
                        },
                        "ledger_path": {
                            "type": "string",
                            "description": "Adaptation: story draft path, "
                            "default project/story/current-revision.json. "
                            "Forensic: optional report JSON array. Must stay in this Topic.",
                        },
                    },
                    "required": ["scope"],
                },
            },
        }

    def invoke(self, arguments: str, ctx: ToolContext) -> str:
        return "cine_verify_report is dispatched runner-side"
