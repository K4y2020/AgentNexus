import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { MemoryRouter } from "react-router-dom";
import {
  A2ACollaborationCard,
  formatIntentLabel,
  parseTeammateOutput,
} from "./A2ACollaborationCard";

afterEach(cleanup);

describe("parseTeammateOutput", () => {
  it("parses valid JSON response output", () => {
    const json = JSON.stringify({
      status: "completed",
      target_teammate: "polly",
      target_session_id: "sess-123",
      session_url: "/c/sess-123",
      response: "All gates pass.",
    });
    const parsed = parseTeammateOutput(json);
    expect(parsed).toEqual({
      status: "completed",
      target_teammate: "polly",
      target_session_id: "sess-123",
      session_url: "/c/sess-123",
      response: "All gates pass.",
      message: undefined,
      error: undefined,
      intent: undefined,
      coordination_message_id: undefined,
      attachment_count: undefined,
      target_file_ids: undefined,
      effective_model: undefined,
      model_source: undefined,
    });
  });

  it("unwraps MCP content array from claude-sdk harness", () => {
    const raw = JSON.stringify([
      {
        type: "text",
        text: JSON.stringify({
          status: "completed",
          target_teammate: "debby",
          response: "Synthesized verdict.",
        }),
      },
    ]);
    const parsed = parseTeammateOutput(raw);
    expect(parsed?.status).toBe("completed");
    expect(parsed?.target_teammate).toBe("debby");
    expect(parsed?.response).toBe("Synthesized verdict.");
  });

  it("returns plain text wrapped in response when JSON parsing fails", () => {
    const parsed = parseTeammateOutput("Raw plain text report");
    expect(parsed?.response).toBe("Raw plain text report");
  });

  it("returns null for null or empty input", () => {
    expect(parseTeammateOutput(null)).toBeNull();
    expect(parseTeammateOutput("")).toBeNull();
  });
});

describe("formatIntentLabel", () => {
  it("maps known intents to clean Chinese labels", () => {
    expect(formatIntentLabel("review.request")).toBe("代码评审");
    expect(formatIntentLabel("task.request")).toBe("任务派发");
    expect(formatIntentLabel("question")).toBe("问答协作");
    expect(formatIntentLabel("status.inquiry")).toBe("状态查询");
  });

  it("falls back to raw intent or default", () => {
    expect(formatIntentLabel("custom.intent")).toBe("custom.intent");
    expect(formatIntentLabel(undefined)).toBe("协同请求");
  });
});

describe("A2ACollaborationCard UI", () => {
  it("renders running state with task prompt and loading indicator", () => {
    render(
      <MemoryRouter>
        <A2ACollaborationCard
          arguments={{
            teammate: "polly",
            task: "Verify Phase 0+1 implementation gates",
            intent: "review.request",
          }}
          output={null}
          state="input-available"
        />
      </MemoryRouter>,
    );

    expect(screen.getByTestId("a2a-collaboration-card")).toBeDefined();
    expect(screen.getByText("Polly")).toBeDefined();
    expect(screen.getByText("代码评审")).toBeDefined();
    expect(screen.getByText("协同中...")).toBeDefined();
    expect(screen.getByText("Verify Phase 0+1 implementation gates")).toBeDefined();
    expect(screen.getByText(/正在等待 Polly 执行并回传结论/)).toBeDefined();
  });

  it("renders completed state with teammate output report and session link", () => {
    const output = JSON.stringify({
      status: "completed",
      target_teammate: "polly",
      target_session_id: "636e2fbf710347deb3c772430ac1e180",
      session_url: "/c/636e2fbf710347deb3c772430ac1e180",
      response: "## Phase 0+1 Gate 判定翻转: G3 PASS, G4 PASS",
    });

    render(
      <MemoryRouter>
        <A2ACollaborationCard
          arguments={{
            teammate: "polly",
            task: "Run the full audit",
            intent: "review.request",
          }}
          output={output}
          state="output-available"
          duration={19.2}
        />
      </MemoryRouter>,
    );

    expect(screen.getByText("已就绪")).toBeDefined();
    expect(screen.getByText("19s")).toBeDefined();
    expect(screen.getByText("对端回传成果 (Teammate Output)")).toBeDefined();
    expect(screen.getByText("打开 Polly 独立工作台")).toBeDefined();
  });

  it("renders error state when error occurs", () => {
    const output = JSON.stringify({
      error: "No online host is available to run teammate 'polly'.",
    });

    render(
      <MemoryRouter>
        <A2ACollaborationCard
          arguments={{
            teammate: "polly",
            task: "Test error handling",
          }}
          output={output}
          state="output-error"
        />
      </MemoryRouter>,
    );

    expect(screen.getByText("异常")).toBeDefined();
    expect(screen.getByText("No online host is available to run teammate 'polly'.")).toBeDefined();
  });
});
