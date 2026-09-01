import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AgentInspector } from "./AgentInspector";
import * as identity from "@/lib/identity";
import * as useSessionModule from "@/hooks/useSession";

vi.mock("@/lib/identity", () => ({
  authenticatedFetch: vi.fn(),
}));

vi.mock("@/hooks/useSession", async (importOriginal) => ({
  ...(await importOriginal<typeof useSessionModule>()),
  useSession: vi.fn(),
}));

function jsonResponse(body: unknown, { ok = true, status = 200 } = {}): Response {
  return {
    ok,
    status,
    json: async () => body,
  } as unknown as Response;
}

function mockSession() {
  vi.mocked(useSessionModule.useSession).mockReturnValue({
    session: {
      id: "conv_child",
      modelOverride: "gpt-5",
      llmModel: "gpt-5",
      labels: {
        "omnigent.role": "planner",
        "omnigent.upstream_model": "gpt-5",
      },
      harness: "codex",
      gitBranch: "main",
      workspace: "C:/repo",
    } as never,
    isLoading: false,
    error: null,
  });
}

function renderInspector() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <AgentInspector conversationId="conv_child" rootSessionId="conv_root" />
    </QueryClientProvider>,
  );
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("AgentInspector behavior facts", () => {
  beforeEach(() => {
    mockSession();
  });

  it("renders a confirmed lean binding without inventing facts", async () => {
    vi.mocked(identity.authenticatedFetch).mockResolvedValue(
      jsonResponse({
        session_id: "conv_child",
        binding: {
          workflow_node: "planner",
          requested_mode: "lean",
          injection_channel: "composed_per_turn",
          resolved: {
            binding: {
              mode: "lean",
              digest: "sha256:abcdef1234567890",
              version: "1.0.0",
            },
          },
        },
        delivery_state: "confirmed",
        consumption_state: "consumed",
        reason: "confirmed_injection",
      }),
    );

    renderInspector();
    expect(await screen.findByText("Lean")).toBeInTheDocument();
    expect(screen.getByText("composed_per_turn")).toBeInTheDocument();
    expect(screen.getAllByText("planner").length).toBeGreaterThanOrEqual(2);
    expect(screen.getByText("confirmed")).toBeInTheDocument();
  });

  it("shows queued explicitly instead of confirming a pending injection", async () => {
    vi.mocked(identity.authenticatedFetch).mockResolvedValue(
      jsonResponse({
        session_id: "conv_child",
        binding: {
          workflow_node: "implementer",
          requested_mode: "off",
          injection_channel: "none",
          resolved: { binding: { mode: "off" } },
        },
        delivery_state: "queued",
        consumption_state: "unconsumed",
        reason: "recorded_pending_delivery",
      }),
    );

    renderInspector();
    expect(await screen.findByText("Off")).toBeInTheDocument();
    expect(screen.getByText("queued")).toBeInTheDocument();
    expect(screen.queryByText("confirmed")).not.toBeInTheDocument();
  });

  it("renders no binding rather than a fake agent_default", async () => {
    vi.mocked(identity.authenticatedFetch).mockResolvedValue(
      jsonResponse({
        session_id: "conv_child",
        binding: null,
        delivery_state: "none",
        consumption_state: "none",
        reason: "no_workflow_behavior_binding",
      }),
    );

    renderInspector();
    expect(await screen.findByText("Not recorded")).toBeInTheDocument();
    expect(screen.queryByText("confirmed")).not.toBeInTheDocument();
  });

  it("shows a requested session mode as not yet injected", async () => {
    vi.mocked(identity.authenticatedFetch).mockResolvedValue(
      jsonResponse({
        session_id: "conv_child",
        binding: null,
        session_mode: { binding: { mode: "advisory" } },
        delivery_state: "none",
        consumption_state: "none",
        reason: "session_mode_not_yet_injected",
      }),
    );

    renderInspector();
    await waitFor(() => {
      const select = screen.getByTestId("session-behavior-mode") as HTMLSelectElement;
      expect(select.value).toBe("advisory");
    });
    expect(screen.getAllByText("Advisory").length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText(/not yet injected/i)).toBeInTheDocument();
    expect(screen.queryByText("Not recorded")).not.toBeInTheDocument();
  });

  it("persists a session behavior mode through the session labels API", async () => {
    vi.mocked(identity.authenticatedFetch).mockResolvedValue(jsonResponse({}));

    renderInspector();
    const select = (await screen.findByTestId("session-behavior-mode")) as HTMLSelectElement;
    fireEvent.change(select, { target: { value: "strict" } });

    await waitFor(() => {
      const patchCall = vi
        .mocked(identity.authenticatedFetch)
        .mock.calls.find(([url]) => String(url).includes("/v1/sessions/conv_child"));
      expect(patchCall).toBeDefined();
      expect(JSON.parse(String(patchCall![1]!.body))).toEqual({
        labels: { "omnigent.behavior_mode": "strict" },
      });
    });
  });

  it("does not pretend a fetch failure is a missing binding", async () => {
    vi.mocked(identity.authenticatedFetch).mockRejectedValue(new Error("offline"));

    renderInspector();
    expect(await screen.findByText("Behavior facts unavailable")).toBeInTheDocument();
    expect(screen.queryByText("Not recorded")).not.toBeInTheDocument();
  });
});
