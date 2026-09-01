import { useCallback, useEffect, useState } from "react";
import {
  CheckCircle2Icon,
  GitBranchIcon,
  NetworkIcon,
  PlayIcon,
  XCircleIcon,
} from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { authenticatedFetch } from "@/lib/identity";

export interface WorkflowTaskDTO {
  task_id: string;
  run_id: string;
  title: string;
  status: string;
  assignee_session_id: string | null;
  assignee_role: string | null;
  dependencies: string[];
  artifacts: unknown[];
}

export interface WorkflowRunDTO {
  run_id: string;
  title: string;
  template: string;
  status: string;
  budget: Record<string, unknown>;
  metadata: Record<string, unknown>;
  created_at: number;
  updated_at: number;
}

interface WorkflowDetailDTO {
  run: WorkflowRunDTO;
  tasks: WorkflowTaskDTO[];
}

interface DagTaskSpecDTO {
  name: string;
  title: string;
  assignee_session_id: string;
  assignee_role: string;
  prompt: string;
  intent?: string;
  acceptance_criteria?: string[];
  dependencies?: string[];
}

const REPORTABLE_STATUSES = new Set(["assigned", "running", "waiting_peer", "waiting_review"]);

export function WorkflowPanel({
  rootSessionId,
  actorSessionId,
  childSessions,
  onUpdated,
}: {
  rootSessionId: string;
  actorSessionId: string;
  childSessions: { id: string; title: string | null }[];
  onUpdated?: () => void;
}) {
  const [detail, setDetail] = useState<WorkflowDetailDTO | null>(null);
  const [postingTask, setPostingTask] = useState<string | null>(null);
  const [prompt, setPrompt] = useState("");
  const [selectedIds, setSelectedIds] = useState<{
    planner?: string;
    implementer?: string;
    reviewer?: string;
  }>({});
  const [starting, setStarting] = useState(false);
  const [dagOpen, setDagOpen] = useState(false);
  const [dagJson, setDagJson] = useState("");
  const [startingDag, setStartingDag] = useState(false);

  useEffect(() => {
    if (childSessions.length < 3) return;
    setSelectedIds((prev) => ({
      planner: prev.planner ?? childSessions[0]?.id,
      implementer: prev.implementer ?? childSessions[1]?.id,
      reviewer: prev.reviewer ?? childSessions[2]?.id,
    }));
  }, [childSessions]);

  const fetchWorkflow = useCallback(async () => {
    try {
      const listRes = await authenticatedFetch(
        `/v1/coordination/runs?root_session_id=${encodeURIComponent(rootSessionId)}`
      );
      if (!listRes.ok) return;
      const listData = (await listRes.json()) as { runs?: WorkflowRunDTO[] };
      const active = (listData.runs || []).find(
        (run) =>
          !["succeeded", "failed", "cancelled", "needs_attention"].includes(run.status)
      ) ?? listData.runs?.[0];
      if (!active) {
        setDetail(null);
        return;
      }
      const detailRes = await authenticatedFetch(
        `/v1/coordination/runs/${encodeURIComponent(active.run_id)}`
      );
      if (!detailRes.ok) return;
      setDetail((await detailRes.json()) as WorkflowDetailDTO);
    } catch (error) {
      console.warn("Failed to fetch coordination workflow:", error);
    }
  }, [rootSessionId]);

  useEffect(() => {
    void fetchWorkflow();
    const interval = window.setInterval(fetchWorkflow, 3000);
    return () => window.clearInterval(interval);
  }, [fetchWorkflow]);

  const reportTask = async (
    task: WorkflowTaskDTO,
    outcome: "succeeded" | "failed",
  ) => {
    if (!detail || postingTask) return;
    setPostingTask(task.task_id);
    try {
      const body: Record<string, unknown> = {
        actor_session_id: actorSessionId,
        outcome,
      };
      if (outcome === "succeeded" && task.assignee_role === "reviewer") {
        body.review_decision = "approved";
      }
      const res = await authenticatedFetch(
        `/v1/coordination/workflows/${encodeURIComponent(detail.run.run_id)}/tasks/${encodeURIComponent(task.task_id)}/report`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        }
      );
      if (!res.ok) {
        console.warn("Workflow task report refused:", res.status, await res.text());
      }
      await fetchWorkflow();
      onUpdated?.();
    } finally {
      setPostingTask(null);
    }
  };

  const startWorkflow = async () => {
    if (
      starting ||
      !prompt.trim() ||
      !selectedIds.planner ||
      !selectedIds.implementer ||
      !selectedIds.reviewer
    ) {
      return;
    }
    setStarting(true);
    try {
      const res = await authenticatedFetch(
        "/v1/coordination/workflows/plan-implement-review",
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            title: "Plan -> Implement -> Review Workflow",
            root_session_id: rootSessionId,
            planner_session_id: selectedIds.planner,
            implementer_session_id: selectedIds.implementer,
            reviewer_session_id: selectedIds.reviewer,
            user_prompt: prompt.trim(),
            workspace_path: ".",
          }),
        }
      );
      if (!res.ok) {
        console.warn("Workflow start refused:", res.status, await res.text());
      } else {
        setPrompt("");
      }
      await fetchWorkflow();
      onUpdated?.();
    } finally {
      setStarting(false);
    }
  };

  const startDagWorkflow = async () => {
    if (startingDag || !dagJson.trim()) return;
    let tasks: DagTaskSpecDTO[];
    try {
      tasks = JSON.parse(dagJson) as DagTaskSpecDTO[];
    } catch {
      console.warn("Custom DAG JSON is invalid");
      return;
    }
    setStartingDag(true);
    try {
      const res = await authenticatedFetch("/v1/coordination/workflows/template", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          title: "Custom DAG Workflow",
          root_session_id: rootSessionId,
          tasks,
        }),
      });
      if (!res.ok) {
        console.warn("Custom DAG start refused:", res.status, await res.text());
      } else {
        setDagJson("");
      }
      await fetchWorkflow();
      onUpdated?.();
    } finally {
      setStartingDag(false);
    }
  };

  if (!detail) {
    return (
      <Card size="sm" className="border-border" data-testid="workflow-start-panel">
        <CardHeader className="p-2 pb-1 flex flex-row items-center justify-between">
          <div className="flex items-center gap-1.5 font-semibold text-foreground">
            <PlayIcon className="size-3.5 text-primary" />
            <span>New Workflow</span>
          </div>
        </CardHeader>
        <CardContent className="p-2 pt-1 space-y-2">
          <Input
            value={prompt}
            onChange={(event) => setPrompt(event.target.value)}
            placeholder="Workflow prompt"
            className="h-7 text-xs"
          />
          <div className="grid grid-cols-1 gap-1.5">
            {(["planner", "implementer", "reviewer"] as const).map((role) => (
              <div key={role} className="flex items-center gap-1.5">
                <Badge
                  variant="secondary"
                  className="w-20 justify-center text-[10px] font-mono capitalize"
                >
                  {role}
                </Badge>
                <Select
                  value={selectedIds[role] ?? ""}
                  onValueChange={(value) =>
                    setSelectedIds((prev) => ({ ...prev, [role]: value }))
                  }
                >
                  <SelectTrigger size="sm" className="w-full h-7 text-[11px]">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {childSessions.map((child) => (
                      <SelectItem key={child.id} value={child.id}>
                        {child.title ?? child.id}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
            ))}
          </div>
          <Button
            type="button"
            size="sm"
            className="w-full h-7"
            loading={starting}
            disabled={!prompt.trim() || !selectedIds.planner || !selectedIds.implementer || !selectedIds.reviewer}
            onClick={() => void startWorkflow()}
          >
            <PlayIcon className="size-3.5" />
            Start
          </Button>
          <Button
            type="button"
            variant="outline"
            size="sm"
            className="w-full h-7"
            onClick={() => setDagOpen((open) => !open)}
          >
            <NetworkIcon className="size-3.5" />
            Custom DAG
          </Button>
          {dagOpen && (
            <div className="space-y-2">
              <textarea
                value={dagJson}
                onChange={(event) => setDagJson(event.target.value)}
                placeholder={JSON.stringify(
                  [
                    {
                      name: "analyze",
                      title: "Analyze",
                      assignee_session_id: childSessions[0]?.id ?? "",
                      assignee_role: "analyst",
                      prompt: "Analyze the request.",
                    },
                    {
                      name: "implement",
                      title: "Implement",
                      assignee_session_id: childSessions[1]?.id ?? "",
                      assignee_role: "implementer",
                      prompt: "Implement the findings.",
                      dependencies: ["analyze"],
                    },
                  ],
                  null,
                  2
                )}
                className="h-48 w-full resize-y rounded border border-border bg-background p-2 font-mono text-[10px] text-foreground focus:outline-none"
              />
              <Button
                type="button"
                size="sm"
                className="w-full h-7"
                loading={startingDag}
                disabled={!dagJson.trim()}
                onClick={() => void startDagWorkflow()}
              >
                <NetworkIcon className="size-3.5" />
                Start DAG
              </Button>
            </div>
          )}
        </CardContent>
      </Card>
    );
  }

  return (
    <Card size="sm" className="border-border" data-testid="workflow-panel">
      <CardHeader className="p-2 pb-1 flex flex-row items-center justify-between">
        <div className="flex items-center gap-1.5 font-semibold text-foreground">
          <GitBranchIcon className="size-3.5 text-primary" />
          <span>{detail.run.title}</span>
        </div>
        <Badge variant="outline" className="text-[10px] font-mono capitalize">
          {detail.run.status}
        </Badge>
      </CardHeader>
      <CardContent className="p-2 pt-1 space-y-1.5">
        {detail.tasks.map((task) => {
          const isActor = task.assignee_session_id === actorSessionId;
          const reportable = isActor && REPORTABLE_STATUSES.has(task.status);
          return (
            <div
              key={task.task_id}
              className="flex items-center justify-between gap-2 py-1 border-b border-border/50 last:border-b-0"
            >
              <div className="flex min-w-0 items-center gap-1.5">
                <Badge variant="secondary" className="text-[10px] font-mono capitalize">
                  {task.assignee_role || "agent"}
                </Badge>
                <span className="truncate text-foreground">{task.title}</span>
                <Badge variant="outline" className="text-[9px] font-mono">
                  {task.status}
                </Badge>
              </div>
              {reportable && (
                <div className="flex shrink-0 items-center gap-1">
                  <Button
                    type="button"
                    variant="ghost"
                    size="icon-xs"
                    aria-label="Report succeeded"
                    disabled={postingTask === task.task_id}
                    onClick={() => void reportTask(task, "succeeded")}
                  >
                    <CheckCircle2Icon className="size-3.5 text-emerald-600 dark:text-emerald-500" />
                  </Button>
                  <Button
                    type="button"
                    variant="ghost"
                    size="icon-xs"
                    aria-label="Report failed"
                    disabled={postingTask === task.task_id}
                    onClick={() => void reportTask(task, "failed")}
                  >
                    <XCircleIcon className="size-3.5 text-destructive" />
                  </Button>
                </div>
              )}
            </div>
          );
        })}
      </CardContent>
    </Card>
  );
}
