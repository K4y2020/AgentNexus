import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { MemoryRouter } from "react-router-dom";
import { SeedanceCanvasContext } from "@/lib/seedanceCanvas";
import {
  A2ACollaborationCard,
  applyCoordinationSnapshot,
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
  it("promotes a dispatched receipt when the durable result arrives", () => {
    const promoted = applyCoordinationSnapshot(
      {
        status: "dispatched",
        target_teammate: "polly",
        coordination_message_id: "msg_123",
        message: "queued",
      },
      {
        message: { message_state: "active" },
        result: { payload: { outcome: "succeeded", summary: "已完成审查。" } },
        delivery_state: "confirmed",
      },
    );

    expect(promoted?.status).toBe("completed");
    expect(promoted?.response).toBe("已完成审查。");
  });

  it("turns a failed durable delivery into a failed card", () => {
    const promoted = applyCoordinationSnapshot(
      { status: "dispatched", coordination_message_id: "msg_456" },
      { message: { message_state: "expired" }, delivery_state: "failed" },
    );

    expect(promoted?.status).toBe("failed");
    expect(promoted?.error).toBe("A2A 请求未能完成");
  });

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

    expect(screen.getByText("已完成")).toBeDefined();
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

  it("does not present a failed result with report text as successful", () => {
    render(
      <MemoryRouter>
        <A2ACollaborationCard
          arguments={{ teammate: "polly", task: "Audit" }}
          output={JSON.stringify({ status: "failed", response: "The audit did not pass." })}
          state="output-available"
        />
      </MemoryRouter>,
    );
    expect(screen.getByText("异常")).toBeDefined();
    expect(screen.queryByText("已就绪")).toBeNull();
    expect(screen.getByText("失败详情")).toBeDefined();
  });
});
describe("Seedance V3 integration in A2ACollaborationCard", () => {
  it("parses Seedance project and job fields from output", () => {
    const raw = JSON.stringify({
      status: "completed",
      target_teammate: "seedance",
      seedance_project_id: "proj_abc123",
      seedance_agent_session_id: "sess_xyz789",
      seedance_canvas_url: "http://127.0.0.1:5173/?project=proj_abc123",
      seedance_job_ids: ["job_1", "job_2"],
      seedance_asset_ids: ["asset_1"],
      response: "Canvas updated with 3 shots.",
    });
    const parsed = parseTeammateOutput(raw);
    expect(parsed?.seedance_project_id).toBe("proj_abc123");
    expect(parsed?.seedance_agent_session_id).toBe("sess_xyz789");
    expect(parsed?.seedance_canvas_url).toBe("http://127.0.0.1:5173/?project=proj_abc123");
    expect(parsed?.seedance_job_ids).toEqual(["job_1", "job_2"]);
    expect(parsed?.seedance_asset_ids).toEqual(["asset_1"]);
  });

  it("applies Seedance fields from coordination snapshot payload", () => {
    const initial = parseTeammateOutput(
      JSON.stringify({ status: "dispatched", target_teammate: "seedance" }),
    );
    const updated = applyCoordinationSnapshot(initial, {
      result: {
        payload: {
          outcome: "succeeded",
          summary: "Created storyboard cards",
          seedance_project_id: "proj_999",
          seedance_canvas_url: "http://127.0.0.1:5173/?project=proj_999",
          seedance_job_ids: ["job_gen_1"],
        },
      },
    });
    expect(updated?.status).toBe("completed");
    expect(updated?.seedance_project_id).toBe("proj_999");
    expect(updated?.seedance_canvas_url).toBe("http://127.0.0.1:5173/?project=proj_999");
    expect(updated?.seedance_job_ids).toEqual(["job_gen_1"]);
  });

  it.each([true, false])("gates the Seedance button on Cine ownership (%s)", (enabled) => {
    const payload = JSON.stringify({
      status: "completed",
      target_teammate: "seedance",
      seedance_project_id: "proj_test_123",
      seedance_agent_session_id: "sess_test_456",
      seedance_canvas_url: "http://127.0.0.1:5173/?project=proj_test_123",
      seedance_job_ids: ["job_vid_01"],
      seedance_asset_ids: ["asset_png_01"],
      response: "Shots synced to Seedance.",
    });

    render(
      <MemoryRouter>
        <SeedanceCanvasContext.Provider value={enabled}>
        <A2ACollaborationCard
          arguments={{ teammate: "seedance", task: "Sync shot S01-03 to canvas" }}
          output={payload}
          state="output-available"
        />
        </SeedanceCanvasContext.Provider>
      </MemoryRouter>,
    );

    if (!enabled) {
      expect(screen.queryByTestId("open-seedance-canvas-link")).toBeNull();
      return;
    }
    const canvasLink = screen.getByTestId("open-seedance-canvas-link");
    expect(canvasLink).toBeInTheDocument();
    expect(canvasLink).toHaveAttribute("href", "http://127.0.0.1:5173/?project=proj_test_123");
    expect(screen.getByText("Seedance V3 画布")).toBeInTheDocument();
    expect(screen.getByText(/proj_test_123/)).toBeInTheDocument();
    expect(screen.getByText(/job_vid_01/)).toBeInTheDocument();
  });
});
