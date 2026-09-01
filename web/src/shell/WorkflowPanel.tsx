import { useCallback, useEffect, useState } from "react";
import { CheckCircle2Icon, GitBranchIcon, XCircleIcon } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader } from "@/components/ui/card";
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

const REPORTABLE_STATUSES = new Set(["assigned", "running", "waiting_peer", "waiting_review"]);

export function WorkflowPanel({
  rootSessionId,
  actorSessionId,
  onUpdated,
}: {
  rootSessionId: string;
  actorSessionId: string;
  onUpdated?: () => void;
}) {
  const [detail, setDetail] = useState<WorkflowDetailDTO | null>(null);
  const [postingTask, setPostingTask] = useState<string | null>(null);

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

  if (!detail) return null;

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
