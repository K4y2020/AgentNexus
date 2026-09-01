import {
  BrainCircuitIcon,
  CpuIcon,
  FolderGit2Icon,
  GitBranchIcon,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { shortModelName } from "@/components/CostRoutingControl";
import { useSession } from "@/hooks/useSession";
import { claudePermissionModeFromSession, claudePermissionModeLabel } from "@/lib/claudePermissionMode";

export interface AgentInspectorProps {
  conversationId: string;
}

export function AgentInspector({ conversationId }: AgentInspectorProps) {
  const { session } = useSession(conversationId);

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
