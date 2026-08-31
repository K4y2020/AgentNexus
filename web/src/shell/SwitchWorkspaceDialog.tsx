import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { AlertTriangleIcon, CheckIcon, GitBranchIcon, HardDriveIcon } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { useHostWorktrees, type HostWorktree } from "@/hooks/useHostWorktrees";
import { launchRunner, updateSession } from "@/lib/sessionsApi";
import { useChatStore } from "@/store/chatStore";
import { cn } from "@/lib/utils";
import { normalizeWorkspacePath } from "./NewChatDialog";

/** Rebind an existing session to another worktree on the same host. */
export function SwitchWorkspaceDialog({
  open,
  onOpenChange,
  sessionId,
  hostId,
  workspace,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  sessionId: string;
  hostId: string;
  workspace: string;
}) {
  const queryClient = useQueryClient();
  const markRunnerLaunched = useChatStore((s) => s.markRunnerLaunched);
  const { data: worktrees, isLoading, error: worktreesError } = useHostWorktrees(hostId, workspace);
  const [switchingPath, setSwitchingPath] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  // Step one succeeded but launch failed. The original path must then be a
  // selectable recovery target rather than staying disabled as "Current".
  const [stranded, setStranded] = useState(false);
  const current = normalizeWorkspacePath(workspace);

  function handleOpenChange(next: boolean): void {
    if (!next) {
      setSwitchingPath(null);
      setError(null);
      setStranded(false);
    }
    onOpenChange(next);
  }

  async function switchTo(target: HostWorktree): Promise<void> {
    const targetPath = normalizeWorkspacePath(target.path) ?? target.path;
    const isCurrent = targetPath === current;
    if ((isCurrent && !stranded) || switchingPath !== null) return;

    setSwitchingPath(target.path);
    setError(null);
    let released = false;
    try {
      // Empty runner id is the server's release sentinel. Preserve the model
      // override because this move stays on the same host/provider catalog.
      await updateSession(sessionId, { runnerId: "", silent: true });
      released = true;
      setStranded(true);
      await launchRunner(
        hostId,
        sessionId,
        target.path,
        target.branch ? { branchName: target.branch, existingWorktree: true } : undefined,
      );
      // The session id stays the same, so every workspace query key would
      // otherwise retain data from the old physical tree. Cancel and remove
      // those rows before the environment refetch exposes the new root; this
      // prevents old expanded directories from issuing 404s in the new tree.
      const workspaceQueryKeys = [
        ["workspace-environment", sessionId],
        ["workspace-changed-files", sessionId],
        ["workspace-all-files", sessionId],
        ["workspace-dir", sessionId],
        ["workspace-dir-listing", sessionId],
        ["file-content", sessionId],
        ["file-diff", sessionId],
      ] as const;
      await Promise.all(
        workspaceQueryKeys.map((queryKey) => queryClient.cancelQueries({ queryKey })),
      );
      for (const queryKey of workspaceQueryKeys) {
        queryClient.removeQueries({ queryKey });
      }
      markRunnerLaunched();
      handleOpenChange(false);
      void queryClient.invalidateQueries({ queryKey: ["session", sessionId] });
      void queryClient.invalidateQueries({ queryKey: ["conversations"] });
      void queryClient.invalidateQueries({ queryKey: ["workspace-environment", sessionId] });
      void queryClient.invalidateQueries({ queryKey: ["workspace-changed-files", sessionId] });
      void queryClient.invalidateQueries({ queryKey: ["workspace-all-files", sessionId] });
    } catch (e) {
      setError(e instanceof Error ? e.message : "Couldn't switch worktrees. Try again.");
      if (released) {
        void queryClient.invalidateQueries({ queryKey: ["session", sessionId] });
        void queryClient.invalidateQueries({ queryKey: ["conversations"] });
      }
    } finally {
      setSwitchingPath(null);
    }
  }

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent data-testid="switch-workspace-dialog" className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>Switch worktree</DialogTitle>
          <DialogDescription>
            Continue this same conversation in another working tree on this machine.
          </DialogDescription>
        </DialogHeader>

        <div className="flex max-h-[min(24rem,60vh)] flex-col gap-1 overflow-y-auto py-1">
          {isLoading ? (
            <p className="px-2 py-3 text-sm text-muted-foreground">Loading worktrees…</p>
          ) : worktreesError ? (
            <p className="px-2 py-3 text-sm text-destructive">
              Couldn't list this repository's worktrees.
            </p>
          ) : (worktrees ?? []).length === 0 ? (
            <p className="px-2 py-3 text-sm text-muted-foreground">
              No git worktrees were found for this directory.
            </p>
          ) : (
            (worktrees ?? []).map((target) => {
              const targetPath = normalizeWorkspacePath(target.path) ?? target.path;
              const isCurrent = targetPath === current;
              return (
                <Button
                  key={target.path}
                  type="button"
                  variant="ghost"
                  disabled={(isCurrent && !stranded) || switchingPath !== null}
                  loading={switchingPath === target.path}
                  onClick={() => void switchTo(target)}
                  data-testid={`switch-workspace-option-${target.is_main ? "main" : (target.branch ?? "detached")}`}
                  className={cn(
                    "h-auto min-h-14 w-full justify-start gap-3 px-2.5 py-2 text-left",
                    target.is_main && "border border-warning/30 bg-warning/5",
                  )}
                >
                  <span className="flex size-8 shrink-0 items-center justify-center rounded-md bg-muted text-muted-foreground">
                    {target.is_main ? (
                      <HardDriveIcon className="size-4" />
                    ) : (
                      <GitBranchIcon className="size-4" />
                    )}
                  </span>
                  <span className="flex min-w-0 flex-1 flex-col gap-0.5">
                    <span className="flex items-center gap-2 text-sm font-medium text-foreground">
                      <span className="truncate">{target.branch ?? "Detached HEAD"}</span>
                      {target.is_main && (
                        <span className="shrink-0 rounded bg-warning/15 px-1.5 py-0.5 text-xs text-warning">
                          Main working tree
                        </span>
                      )}
                    </span>
                    <span
                      className="truncate font-mono text-xs text-muted-foreground"
                      title={target.path}
                    >
                      {target.path}
                    </span>
                    {target.is_main && (
                      <span className="text-xs text-warning">
                        Uses local files directly, including uncommitted changes.
                      </span>
                    )}
                  </span>
                  {isCurrent && !stranded && (
                    <span className="flex shrink-0 items-center gap-1 text-xs text-muted-foreground">
                      <CheckIcon className="size-3.5" /> Current
                    </span>
                  )}
                </Button>
              );
            })
          )}
        </div>

        <p className="flex items-start gap-1.5 text-xs text-warning">
          <AlertTriangleIcon className="mt-0.5 size-3.5 shrink-0" />
          <span>
            The current Runner will restart. Running turns must finish first, and writes in the main
            working tree modify your local files directly.
          </span>
        </p>

        {error !== null && (
          <p className="text-sm text-destructive" data-testid="switch-workspace-error">
            {stranded
              ? `The old Runner was released, but the new worktree did not start: ${error}`
              : error}
          </p>
        )}
      </DialogContent>
    </Dialog>
  );
}
