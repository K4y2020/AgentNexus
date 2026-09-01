import { describe, expect, it } from "vitest";
import { structuredErrorFields } from "./blocks";

describe("structuredErrorFields", () => {
  it("maps the plan's suggested_action onto remediation and keeps layer metadata", () => {
    const out = structuredErrorFields({
      suggested_action: "retry the model change",
      layer: "provider",
      correlation_id: "req_123",
      diagnostic_refs: ["art_log_01"],
      retryable: true,
    });
    expect(out.remediation).toBe("retry the model change");
    expect(out.layer).toBe("provider");
    expect(out.correlationId).toBe("req_123");
    expect(out.diagnosticRefs).toEqual(["art_log_01"]);
    expect(out.retryable).toBe(true);
  });

  it("prefers remediation and camel-case fields when both are present", () => {
    const out = structuredErrorFields({
      remediation: "legacy remediation",
      suggested_action: "plan suggested action",
      suggestedAction: "camel action",
      correlation_id: "req_a",
      correlationId: "req_b",
    });
    expect(out.remediation).toBe("legacy remediation");
    expect(out.suggestedAction).toBe("camel action");
    expect(out.correlationId).toBe("req_b");
  });
});
