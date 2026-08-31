import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type * as SessionsApiModule from "@/lib/sessionsApi";

import { useChatStore } from "@/store/chatStore";
import { SwitchWorkspaceDialog } from "./SwitchWorkspaceDialog";

const { useHostWorktreesMock, launchRunnerMock, updateSessionMock } = vi.hoisted(() => ({
  useHostWorktreesMock: vi.fn(),
  launchRunnerMock: vi.fn(),
  updateSessionMock: vi.fn(),
}));

vi.mock("@/hooks/useHostWorktrees", () => ({
  useHostWorktrees: (...args: unknown[]) => useHostWorktreesMock(...args),
}));

vi.mock("@/lib/sessionsApi", async (importOriginal) => {
  const actual = await importOriginal<typeof SessionsApiModule>();
  return {
    ...actual,
    launchRunner: (...args: unknown[]) => launchRunnerMock(...args),
    updateSession: (...args: unknown[]) => updateSessionMock(...args),
  };
});

function renderDialog(onOpenChange = vi.fn()) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <SwitchWorkspaceDialog
        open
        onOpenChange={onOpenChange}
        sessionId="conv_1"
        hostId="host_1"
        workspace="U:/repo/.claude/worktrees/current"
      />
    </QueryClientProvider>,
  );
  return { onOpenChange, client };
}

describe("SwitchWorkspaceDialog", () => {
  beforeEach(() => {
    useHostWorktreesMock.mockReset().mockReturnValue({
      data: [
        { path: "U:/repo", branch: "main", is_main: true, detached: false },
        {
          path: "U:/repo/.claude/worktrees/current",
          branch: "claude/current",
          is_main: false,
          detached: false,
        },
      ],
      isLoading: false,
      error: null,
    });
    updateSessionMock.mockReset().mockResolvedValue({});
    launchRunnerMock.mockReset().mockResolvedValue({ runnerId: "runner_new" });
    useChatStore.setState({ markRunnerLaunched: vi.fn() });
  });

  it("marks the current worktree and explains that main includes local changes", () => {
    renderDialog();

    expect(screen.getByTestId("switch-workspace-option-claude/current")).toBeDisabled();
    expect(screen.getByText("Current")).toBeInTheDocument();
    expect(screen.getByText(/including uncommitted changes/i)).toBeInTheDocument();
  });

  it("releases and rebinds the same session to the main working tree", async () => {
    const { onOpenChange, client } = renderDialog();
    client.setQueryData(["workspace-dir", "conv_1", "verified", ""], { stale: true });
    client.setQueryData(["workspace-all-files", "conv_1", ""], { stale: true });

    fireEvent.click(screen.getByTestId("switch-workspace-option-main"));

    await waitFor(() =>
      expect(updateSessionMock).toHaveBeenCalledWith("conv_1", {
        runnerId: "",
        silent: true,
      }),
    );
    expect(launchRunnerMock).toHaveBeenCalledWith("host_1", "conv_1", "U:/repo", {
      branchName: "main",
      existingWorktree: true,
    });
    expect(client.getQueryData(["workspace-dir", "conv_1", "verified", ""])).toBeUndefined();
    expect(client.getQueryData(["workspace-all-files", "conv_1", ""])).toBeUndefined();
    expect(onOpenChange).toHaveBeenCalledWith(false);
  });

  it("keeps recovery choices visible when the new Runner fails", async () => {
    launchRunnerMock.mockRejectedValueOnce(new Error("launch failed"));
    renderDialog();

    fireEvent.click(screen.getByTestId("switch-workspace-option-main"));

    expect(await screen.findByTestId("switch-workspace-error")).toHaveTextContent(
      /old Runner was released.*launch failed/i,
    );
    // The former current path becomes clickable so the user can recover back
    // to it after the release succeeded.
    expect(screen.getByTestId("switch-workspace-option-claude/current")).not.toBeDisabled();
  });
});
