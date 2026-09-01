import {
  BrainCircuitIcon,
  CheckCircle2Icon,
  CpuIcon,
  FolderGit2Icon,
  GitBranchIcon,
  HardDriveIcon,
  ShieldCheckIcon,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { shortModelName } from "@/components/CostRoutingControl";
import { useSession } from "@/hooks/useSession";
import { claudePermissionModeLabel } from "@/lib/claudePermissionMode";
import { useChatStore } from "@/store/chatStore";

export interface AgentInspectorProps {
  conversationId: string;
}

export function AgentInspector({ conversationId }: AgentInspectorProps) {
  const { session } = useSession(conversationId);
  const claudePermissionMode = useChatStore((s) => s.claudePermissionMode);

  if (!session) {
    return (
      <div className="p-4 text-xs text-muted-foreground">
        Loading agent inspection details…
      </div>
    );
  }

  const requestedModel = session.modelOverride || session.llmModel || "Default";
  const resolvedModel = session.llmModel || requestedModel;
  const upstreamModel = session.labels?.["omnigent.upstream_model"] || resolvedModel;
  const harness = session.harness || "in-process";
  const role = session.labels?.["omnigent.role"] || "general-purpose";
  const branch = session.gitBranch || "main";
  const permissionMode = claudePermissionMode || session.labels?.["omnigent.claude_native.permission_mode"] || "default";

  return (
    <div className="flex flex-col gap-3 p-3 text-xs" data-testid="agent-inspector">
      {/* 1. Model Fact Chain (3-Tier) */}
      <Card size="sm">
        <CardHeader className="pb-2">
          <CardTitle className="flex items-center gap-1.5 text-xs font-semibold text-foreground">
            <BrainCircuitIcon className="size-3.5 text-primary" />
            Model Fact Chain (3-Tier)
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
            <Badge variant="default" className="font-mono text-[11px] bg-emerald-600 hover:bg-emerald-600">
              {shortModelName(upstreamModel)}
            </Badge>
          </div>
        </CardContent>
      </Card>

      {/* 2. Runtime & Role Binding */}
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
              {claudePermissionModeLabel(permissionMode)}
            </Badge>
          </div>
        </CardContent>
      </Card>

      {/* 3. Workspace & Git Status */}
      <Card size="sm">
        <CardHeader className="pb-2">
          <CardTitle className="flex items-center gap-1.5 text-xs font-semibold text-foreground">
            <FolderGit2Icon className="size-3.5 text-primary" />
            Workspace & Git Lease
          </CardTitle>
        </CardHeader>
        <CardContent className="flex flex-col gap-2 pt-0">
          <div className="flex items-center justify-between">
            <span className="text-muted-foreground">Git Branch:</span>
            <div className="flex items-center gap-1 font-mono text-foreground">
              <GitBranchIcon className="size-3 text-muted-foreground" />
              <span>{branch}</span>
            </div>
          </div>
          <div className="flex items-center justify-between">
            <span className="text-muted-foreground">Lease Status:</span>
            <div className="flex items-center gap-1 text-emerald-500 font-medium">
              <CheckCircle2Icon className="size-3" />
              <span>Active Lease</span>
            </div>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
