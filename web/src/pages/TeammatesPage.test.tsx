// Teammates roster actions: opening the shared scheduled-task dialog pre-bound
// to a teammate's agent, and dispatching a routine's "run now" through the
// existing scheduled-tasks mutation hook.

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import * as hostHookModule from "@/hooks/useHosts";
import * as recentWorkspacesHooks from "@/hooks/useRecentWorkspaces";
import * as scheduledHooks from "@/hooks/useScheduledTasks";
import * as teammatesHooks from "@/hooks/useTeammates";
import { createSession } from "@/lib/sessionsApi";
import { TeammatesPage } from "./TeammatesPage";
import type { Teammate } from "@/lib/teammatesApi";

vi.mock("@/hooks/useScheduledTasks", () => ({
  useRunScheduledTaskNow: vi.fn(),
}));

vi.mock("@/hooks/useTeammates", () => ({
  useTeammates: vi.fn(),
}));

vi.mock("@/hooks/useHosts", () => ({
  useHosts: vi.fn(),
}));

vi.mock("@/hooks/useRecentWorkspaces", () => ({
  useRecentWorkspaces: vi.fn(),
}));

vi.mock("@/lib/sessionsApi", () => ({
  createSession: vi.fn(),
}));

vi.mock("@/components/scheduled/CreateScheduledTaskDialog", () => ({
  CreateScheduledTaskDialog: ({
    open,
    initialAgentId,
  }: {
    open: boolean;
    initialAgentId?: string;
  }) =>
    open ? <div data-testid="routine-dialog-open" data-agent-id={initialAgentId ?? ""} /> : null,
}));

vi.mock("@/components/teammates/TeammateMemoryDialog", () => ({
  TeammateMemoryDialog: ({ open, agent }: { open: boolean; agent: { id: string } | null }) =>
    open ? <div data-testid="memory-dialog-open" data-agent-id={agent?.id ?? ""} /> : null,
}));

vi.mock("@/components/teammates/TeammateSettingsDialog", () => ({
  TeammateSettingsDialog: ({
    open,
    teammate,
    initialTab,
  }: {
    open: boolean;
    teammate: { agent: { id: string } } | null;
    initialTab?: string;
  }) =>
    open ? (
      <div
        data-testid="settings-dialog-open"
        data-agent-id={teammate?.agent.id ?? ""}
        data-initial-tab={initialTab ?? "profile"}
      />
    ) : null,
}));

function teammate(overrides: Partial<Teammate> = {}): Teammate {
  return {
    bot: {
      id: "bot_polly",
      agentId: "ag_polly",
      name: "polly",
      description: "Coding orchestrator",
      status: "active",
      defaultModel: null,
      behaviorMode: "off",
      homePath: "C:/Users/Kay/.omnigent/bots/bot_polly",
      hostId: "host_1",
      createdAt: 1,
      updatedAt: null,
    },
    agent: {
      id: "ag_polly",
      name: "polly",
      version: 1,
      description: "Coding orchestrator",
      createdAt: 1,
      updatedAt: null,
      harness: "claude-sdk",
      builtin: true,
      skills: [],
      terminals: [],
    },
    routines: [],
    routineCount: 0,
    lastActivityAt: null,
    lastActivityStatus: null,
    lastActivityConversationId: null,
    primaryConversationId: null,
    ...overrides,
  };
}

const runNowMutate = vi.fn();
const refetch = vi.fn().mockResolvedValue(undefined);

function setTeammates(teammates: Teammate[]) {
  vi.mocked(teammatesHooks.useTeammates).mockReturnValue({
    data: teammates,
    isLoading: false,
    isError: false,
    refetch,
  } as unknown as ReturnType<typeof teammatesHooks.useTeammates>);
}

beforeEach(() => {
  runNowMutate.mockReset();
  refetch.mockReset();
  refetch.mockResolvedValue(undefined);
  vi.mocked(hostHookModule.useHosts).mockReset();
  vi.mocked(hostHookModule.useHosts).mockReturnValue({
    data: [{ host_id: "host_1", name: "local", owner: "local", status: "online" }],
  } as unknown as ReturnType<typeof hostHookModule.useHosts>);
  vi.mocked(recentWorkspacesHooks.useRecentWorkspaces).mockReset();
  vi.mocked(recentWorkspacesHooks.useRecentWorkspaces).mockReturnValue({
    recent: ["U:/AI/MultiAgent"],
    addRecent: vi.fn(),
  });
  vi.mocked(createSession).mockReset();
  vi.mocked(createSession).mockResolvedValue({
    id: "conv_bot",
  } as unknown as Awaited<ReturnType<typeof createSession>>);
  vi.mocked(scheduledHooks.useRunScheduledTaskNow).mockReturnValue({
    mutate: runNowMutate,
    isPending: false,
    variables: undefined,
  } as unknown as ReturnType<typeof scheduledHooks.useRunScheduledTaskNow>);
});

afterEach(() => cleanup());

function renderPage() {
  return render(
    <MemoryRouter>
      <TeammatesPage />
    </MemoryRouter>,
  );
}

describe("TeammatesPage actions", () => {
  it("opens the routine dialog pre-bound to the row's agent", () => {
    setTeammates([teammate()]);
    renderPage();

    fireEvent.click(screen.getByTestId("add-routine-polly"));

    expect(screen.getByTestId("routine-dialog-open")).toHaveAttribute("data-agent-id", "ag_polly");
  });

  it("opens the settings dialog for the row's bot", () => {
    setTeammates([teammate()]);
    renderPage();

    fireEvent.click(screen.getByTestId("settings-polly"));

    expect(screen.getByTestId("settings-dialog-open")).toHaveAttribute("data-agent-id", "ag_polly");
    expect(screen.getByTestId("settings-dialog-open")).toHaveAttribute("data-initial-tab", "profile");
  });

  it("opens the memory dialog for the row's bot", () => {
    setTeammates([teammate()]);
    renderPage();

    fireEvent.click(screen.getByTestId("memories-polly"));

    expect(screen.getByTestId("memory-dialog-open")).toHaveAttribute("data-agent-id", "ag_polly");
  });

  it("starts a chat bound to the row's bot", async () => {
    setTeammates([teammate()]);
    renderPage();

    fireEvent.click(screen.getByTestId("chat-polly"));

    await waitFor(() =>
      expect(createSession).toHaveBeenCalledWith("ag_polly", [], {
        hostId: "host_1",
        workspace: "C:/Users/Kay/.omnigent/bots/bot_polly/scratch",
        title: "polly",
        labels: { "omnigent.teammate.primary": "true" },
        botId: "bot_polly",
        purpose: "primary",
      }),
    );
  });

  it("uses the server-owned Bot Home when workspace history is empty", async () => {
    setTeammates([teammate()]);
    vi.mocked(recentWorkspacesHooks.useRecentWorkspaces).mockReturnValue({
      recent: [],
      addRecent: vi.fn(),
    });
    renderPage();

    fireEvent.click(screen.getByTestId("chat-polly"));

    await waitFor(() =>
      expect(createSession).toHaveBeenCalledWith("ag_polly", [], {
        hostId: "host_1",
        workspace: "C:/Users/Kay/.omnigent/bots/bot_polly/scratch",
        title: "polly",
        labels: { "omnigent.teammate.primary": "true" },
        botId: "bot_polly",
        purpose: "primary",
      }),
    );
  });

  it("navigates directly to primary conversation when one already exists without creating new session", async () => {
    setTeammates([teammate({ primaryConversationId: "conv_existing_primary" })]);
    renderPage();

    fireEvent.click(screen.getByTestId("chat-polly"));

    expect(createSession).not.toHaveBeenCalled();
  });

  it("starts a new topic when clicking New Topic button even if primary exists", async () => {
    setTeammates([teammate({ primaryConversationId: "conv_existing_primary" })]);
    renderPage();

    fireEvent.click(screen.getByTestId("new-topic-polly"));

    await waitFor(() =>
      expect(createSession).toHaveBeenCalledWith("ag_polly", [], {
        hostId: "host_1",
        workspace: "C:/Users/Kay/.omnigent/bots/bot_polly/scratch",
        title: "polly Topic",
        labels: {},
        botId: "bot_polly",
        purpose: "topic",
      }),
    );
  });

  it("runs a bound routine now through the scheduled-task mutation", () => {
    setTeammates([
      teammate({
        routines: [
          {
            id: "st_1",
            name: "Morning brief",
            state: "active",
            rrule: "FREQ=DAILY;BYHOUR=9;BYMINUTE=0",
            timezone: "UTC",
            lastRunAt: 1000,
            lastRunStatus: "succeeded",
            lastRunConversationId: null,
            nextRunAt: null,
          },
        ],
        routineCount: 1,
      }),
    ]);
    renderPage();

    fireEvent.click(screen.getByTestId("run-routine-st_1"));

    expect(runNowMutate).toHaveBeenCalledWith(
      "st_1",
      expect.objectContaining({ onSuccess: expect.any(Function) }),
    );
  });
});
