import {
  BrainCircuitIcon,
  CpuIcon,
  FolderGit2Icon,
  GitBranchIcon,
  ListChecksIcon,
} from "lucide-react";
import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { shortModelName } from "@/components/CostRoutingControl";
import { useSession } from "@/hooks/useSession";
import { authenticatedFetch } from "@/lib/identity";
import { claudePermissionModeFromSession, claudePermissionModeLabel } from "@/lib/claudePermissionMode";

export interface AgentInspectorProps {
  conversationId: string;
  rootSessionId: string;
}

interface BehaviorFactDTO {
  binding: {
    workflow_node?: string;
    requested_mode?: string;
    injection_channel?: string;
    resolved?: {
      binding?: {
        mode?: string;
        digest?: string;
        version?: string;
      };
    };
  } | null;
  session_mode?: {
    binding?: {
      mode?: string;
    };
  } | null;
  delivery_state: string;
  consumption_state: string;
  reason: string;
}

const BEHAVIOR_MODE_LABEL: Record<string, string> = {
  off: "Off",
  advisory: "Advisory",
  lean: "Lean",
  strict: "Strict",
};
const BEHAVIOR_MODE_LABEL_KEY = "omnigent.behavior_mode";

export function AgentInspector({
  conversationId,
  rootSessionId,
}: AgentInspectorProps) {
  const queryClient = useQueryClient();
  const [behaviorSaving, setBehaviorSaving] = useState(false);
  const [behaviorSaveError, setBehaviorSaveError] = useState<string | null>(null);
  const { session } = useSession(conversationId);
  const {
    data: behaviorFacts,
    isLoading: behaviorLoading,
    error: behaviorError,
  } = useQuery({
    queryKey: ["behaviorFacts", conversationId, rootSessionId],
    queryFn: async () => {
      const res = await authenticatedFetch(
        `/v1/coordination/behavior/${encodeURIComponent(conversationId)}?root_session_id=${encodeURIComponent(rootSessionId)}`
      );
      if (!res.ok) throw new Error(`behavior facts returned ${res.status}`);
      return (await res.json()) as BehaviorFactDTO;
    },
    enabled: Boolean(rootSessionId) && Boolean(conversationId),
    retry: false,
    staleTime: 30_000,
  });

  if (!session) {
    return (
      <div className="p-4 text-xs text-muted-foreground">
        Loading agent inspection details...
      </div>
    );
  }

  const requestedModel = session.modelOverride ?? "Unknown";
  const resolvedModel = session.llmModel ?? "Unknown";
  const upstreamModel = session.labels?.["omnigent.upstream_model"] ?? "Unknown";
  const harness = session.harness ?? "Unknown";
  const role = session.labels?.["omnigent.role"] ?? "Unknown";
  const branch = session.gitBranch ?? "Unknown";
  const permissionMode = claudePermissionModeFromSession(session);
  const binding = behaviorError ? null : behaviorFacts?.binding ?? null;
  const sessionModeMode =
    behaviorError ? null : (behaviorFacts?.session_mode?.binding?.mode ?? null);
  const resolvedBinding = binding?.resolved?.binding;
  const mode = resolvedBinding?.mode ?? binding?.requested_mode ?? "unknown";
  const injectionState = behaviorFacts?.delivery_state ?? "unknown";

  const handleSessionModeChange = async (value: string) => {
    setBehaviorSaving(true);
    setBehaviorSaveError(null);
    try {
      const res = await authenticatedFetch(
        `/v1/sessions/${encodeURIComponent(conversationId)}`,
        {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ labels: { [BEHAVIOR_MODE_LABEL_KEY]: value } }),
        },
      );
      if (!res.ok) {
        setBehaviorSaveError(`Failed to save session mode (${res.status})`);
      }
      await queryClient.invalidateQueries({
        queryKey: ["behaviorFacts", conversationId, rootSessionId],
      });
    } catch (err) {
      setBehaviorSaveError(
        err instanceof Error ? err.message : "Failed to save session mode",
      );
    } finally {
      setBehaviorSaving(false);
    }
  };

  return (
    <div className="flex flex-col gap-3 p-3 text-xs" data-testid="agent-inspector">
      <Card size="sm">
        <CardHeader className="pb-2">
          <CardTitle className="flex items-center gap-1.5 text-xs font-semibold text-foreground">
            <BrainCircuitIcon className="size-3.5 text-primary" />
            Model Fact Chain
          </CardTitle>
        </CardHeader>
        <CardContent className="flex flex-col gap-2 pt-0">
          <div className="flex items-center justify-between">
            <span className="text-muted-foreground">Requested Model:</span>
            <Badge variant="outline" className="font-mono text-[11px]">
              {shortModelName(requestedModel)}
            </Badge>
          </div>
          <div className="flex items-center justify-between">
            <span className="text-muted-foreground">Resolved Model:</span>
            <Badge variant="secondary" className="font-mono text-[11px]">
              {shortModelName(resolvedModel)}
            </Badge>
          </div>
          <div className="flex items-center justify-between">
            <span className="text-muted-foreground">Upstream Gateway:</span>
            <Badge variant="outline" className="font-mono text-[11px]">
              {shortModelName(upstreamModel)}
            </Badge>
          </div>
        </CardContent>
      </Card>

      <Card size="sm">
        <CardHeader className="pb-2">
          <CardTitle className="flex items-center gap-1.5 text-xs font-semibold text-foreground">
            <ListChecksIcon className="size-3.5 text-primary" />
            Behavior Pack
          </CardTitle>
        </CardHeader>
        <CardContent className="flex flex-col gap-2 pt-0">
          {behaviorError ? (
            <span className="text-muted-foreground">
              Behavior facts unavailable
            </span>
          ) : behaviorLoading ? (
            <span className="text-muted-foreground">Loading behavior facts...</span>
          ) : !binding ? (
            <>
              <div className="flex items-center justify-between">
                <span className="text-muted-foreground">Mode:</span>
                <Badge variant="outline" className="font-mono text-[11px]">
                  {sessionModeMode
                    ? (BEHAVIOR_MODE_LABEL[sessionModeMode] ?? sessionModeMode)
                    : "Not recorded"}
                </Badge>
              </div>
              {sessionModeMode ? (
                <span className="text-[10px] text-muted-foreground">
                  Requested for this session; not yet injected.
                </span>
              ) : (
                <span className="text-[10px] text-muted-foreground">
                  No workflow behavior binding has been addressed to this session.
                </span>
              )}
              <label htmlFor="session-behavior-mode" className="text-muted-foreground">
                Session mode
              </label>
              <select
                id="session-behavior-mode"
                data-testid="session-behavior-mode"
                value={sessionModeMode ?? "off"}
                disabled={behaviorSaving}
                onChange={(e) => void handleSessionModeChange(e.target.value)}
                className="h-7 w-full rounded-md border border-border bg-background px-2 font-mono text-[11px] text-foreground"
              >
                <option value="off">Off</option>
                <option value="advisory">Advisory</option>
                <option value="lean">Lean</option>
                <option value="strict">Strict</option>
              </select>
              {behaviorSaveError ? (
                <span className="text-[10px] text-destructive">{behaviorSaveError}</span>
              ) : null}
            </>
          ) : (
            <>
              <div className="flex items-center justify-between">
                <span className="text-muted-foreground">Mode:</span>
                <Badge
                  variant={mode === "strict" ? "default" : "outline"}
                  className="font-mono text-[11px]"
                >
                  {BEHAVIOR_MODE_LABEL[mode] ?? mode}
                </Badge>
              </div>
              <div className="flex items-center justify-between">
                <span className="text-muted-foreground">Pack:</span>
                <span className="font-mono text-foreground">
                  {resolvedBinding?.digest
                    ? `${resolvedBinding.digest.slice(0, 16)}…`
                    : "off"}
                </span>
              </div>
              <div className="flex items-center justify-between">
                <span className="text-muted-foreground">Channel:</span>
                <span className="font-mono text-foreground">
                  {binding.injection_channel ?? "none"}
                </span>
              </div>
              <div className="flex items-center justify-between">
                <span className="text-muted-foreground">Node:</span>
                <span className="font-mono text-foreground capitalize">
                  {binding.workflow_node ?? "stage"}
                </span>
              </div>
              <div
                className={`flex items-center justify-between ${
                  injectionState === "confirmed"
                    ? "text-success"
                    : "text-foreground"
                }`}
              >
                <span className="text-muted-foreground">Injection:</span>
                <span className="font-mono">
                  {injectionState === "confirmed"
                    ? "confirmed"
                    : injectionState === "queued"
                      ? "queued"
                      : injectionState === "failed"
                        ? "delivery failed"
                        : "unconfirmed/unknown"}
                </span>
              </div>
            </>
          )}
        </CardContent>
      </Card>

      <Card size="sm">
        <CardHeader className="pb-2">
          <CardTitle className="flex items-center gap-1.5 text-xs font-semibold text-foreground">
            <CpuIcon className="size-3.5 text-primary" />
            Agent & Harness Binding
          </CardTitle>
        </CardHeader>
        <CardContent className="flex flex-col gap-2 pt-0">
          <div className="flex items-center justify-between">
            <span className="text-muted-foreground">Role:</span>
            <span className="font-medium text-foreground capitalize">{role}</span>
          </div>
          <div className="flex items-center justify-between">
            <span className="text-muted-foreground">Harness Type:</span>
            <span className="font-mono text-foreground">{harness}</span>
          </div>
          <div className="flex items-center justify-between">
            <span className="text-muted-foreground">Permissions:</span>
            <Badge variant="outline" className="text-foreground">
              {permissionMode ? claudePermissionModeLabel(permissionMode) : "Unknown"}
            </Badge>
          </div>
        </CardContent>
      </Card>

      <Card size="sm">
        <CardHeader className="pb-2">
          <CardTitle className="flex items-center gap-1.5 text-xs font-semibold text-foreground">
            <FolderGit2Icon className="size-3.5 text-primary" />
            Workspace & Git
          </CardTitle>
        </CardHeader>
        <CardContent className="flex flex-col gap-2 pt-0">
          <div className="flex items-center justify-between">
            <span className="text-muted-foreground">Workspace:</span>
            <span className="font-mono text-foreground break-all text-right max-w-[65%]">
              {session.workspace ?? "Unknown"}
            </span>
          </div>
          <div className="flex items-center justify-between">
            <span className="text-muted-foreground">Git Branch:</span>
            <div className="flex items-center gap-1 font-mono text-foreground">
              <GitBranchIcon className="size-3 text-muted-foreground" />
              <span>{branch}</span>
            </div>
          </div>
          <div className="flex items-center justify-between">
            <span className="text-muted-foreground">Lease Status:</span>
            <span className="text-muted-foreground">No lease record</span>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
