/**
 * Teammates roster page (`/teammates`): the persistent-agent version of the
 * template registry. Each row aggregates a registered agent, its scheduled
 * routines, and the newest routine activity so a person can see who is staffed
 * and what they have been doing without leaving the page.
 */

import { useState } from "react";
import {
  BotIcon,
  BrainIcon,
  Loader2Icon,
  MessageSquareTextIcon,
  PlayIcon,
  PlusIcon,
  SettingsIcon,
  TriangleAlertIcon,
} from "lucide-react";
import { CreateScheduledTaskDialog } from "@/components/scheduled/CreateScheduledTaskDialog";
import { TeammateMemoryDialog } from "@/components/teammates/TeammateMemoryDialog";
import {
  TeammateSettingsDialog,
  type TeammateSettingsTab,
} from "@/components/teammates/TeammateSettingsDialog";
import { PageScroll } from "@/components/PageScroll";
import { Button } from "@/components/ui/button";
import { useHosts } from "@/hooks/useHosts";
import { useNow } from "@/hooks/useNow";
import { useRecentWorkspaces } from "@/hooks/useRecentWorkspaces";
import { useRunScheduledTaskNow } from "@/hooks/useScheduledTasks";
import { useTeammates } from "@/hooks/useTeammates";
import { readLastHostChoice } from "@/lib/hostPreferences";
import { useNavigate } from "@/lib/routing";
import { createSession } from "@/lib/sessionsApi";
import { relativeTime } from "@/lib/relativeTime";
import type { Teammate, TeammateAgent, TeammateRoutineRunStatus } from "@/lib/teammatesApi";
import { cn } from "@/lib/utils";

export function TeammatesPage() {
  const { data: teammates, isLoading, isError, refetch } = useTeammates();
  const runNowMutation = useRunScheduledTaskNow();
  const { data: hosts } = useHosts();
  const savedHostId = readLastHostChoice();
  const chatHost =
    hosts?.find((host) => host.host_id === savedHostId && host.status === "online") ??
    hosts?.find((host) => host.status === "online") ??
    null;
  const { addRecent } = useRecentWorkspaces(chatHost?.host_id ?? null);
  const now = useNow();
  const navigate = useNavigate();
  const [routineAgentId, setRoutineAgentId] = useState<string | null>(null);
  const [startingAgentId, setStartingAgentId] = useState<string | null>(null);
  const [chatError, setChatError] = useState<string | null>(null);
  const rows = teammates ?? [];
  const runPendingId =
    runNowMutation.isPending && runNowMutation.variables ? runNowMutation.variables : undefined;
  const [memoryAgent, setMemoryAgent] = useState<TeammateAgent | null>(null);
  const [settingsTeammate, setSettingsTeammate] = useState<Teammate | null>(null);
  const [settingsTab, setSettingsTab] = useState<TeammateSettingsTab>("profile");

  function handleOpenSettings(teammate: Teammate, tab: TeammateSettingsTab = "profile") {
    setSettingsTeammate(teammate);
    setSettingsTab(tab);
  }

  function handleRunNow(routineId: string) {
    runNowMutation.mutate(routineId, {
      onSuccess: () => void refetch(),
    });
  }

  function handleRoutineDialogChange(open: boolean) {
    if (!open) {
      setRoutineAgentId(null);
      void refetch();
    }
  }

  async function handleChat(teammate: Teammate, forceNew = false) {
    if (!forceNew && teammate.primaryConversationId) {
      navigate(`/c/${teammate.primaryConversationId}`);
      return;
    }
    const targetWorkspace = `${teammate.bot.homePath.replace(/[\\/]+$/, "")}/scratch`;
    const targetHost =
      hosts?.find((host) => host.host_id === teammate.bot.hostId && host.status === "online") ??
      chatHost;

    if (!targetHost) {
      setChatError("No host is online. Connect one with `agentnexus host`, then try again.");
      return;
    }
    setStartingAgentId(teammate.agent.id);
    setChatError(null);
    try {
      const session = await createSession(teammate.agent.id, [], {
        hostId: targetHost.host_id,
        workspace: targetWorkspace,
        title: forceNew ? `${teammate.bot.name} Topic` : teammate.bot.name,
        labels: forceNew ? {} : { "agentnexus.teammate.primary": "true" },
        botId: teammate.bot.id,
        purpose: forceNew ? "topic" : "primary",
      });
      addRecent(targetWorkspace);
      void refetch();
      navigate(`/c/${session.id}`);
    } catch {
      setChatError(`Couldn't start a conversation with ${teammate.agent.name}.`);
    } finally {
      setStartingAgentId(null);
    }
  }

  return (
    <PageScroll contentClassName="px-6">
      <div className="mb-6 flex items-start justify-between gap-4">
        <div className="flex flex-col gap-1">
          <h1 className="text-2xl font-semibold">Teammates</h1>
          <p className="text-ui text-muted-foreground">
            Registered agents, their routines, and recent activity.
          </p>
        </div>
      </div>

      {isError ? (
        <div
          role="alert"
          data-testid="teammates-load-error"
          className="flex items-center gap-2 rounded-lg border border-destructive/30 bg-destructive/5 px-3 py-2 text-ui"
        >
          <TriangleAlertIcon className="size-4 shrink-0 text-destructive" />
          <span className="flex-1">Couldn't load the roster.</span>
          <Button
            variant="outline"
            size="sm"
            onClick={() => void refetch()}
            componentId="teammates.retry"
          >
            Retry
          </Button>
        </div>
      ) : isLoading ? (
        <div className="flex items-center gap-2 py-12 text-ui text-muted-foreground">
          <Loader2Icon className="size-4 animate-spin" />
          Loading teammates…
        </div>
      ) : rows.length === 0 ? (
        <div className="flex flex-col items-center gap-2 py-12 text-center">
          <BotIcon className="size-8 text-muted-foreground/50" />
          <p className="text-ui font-medium">No teammates registered</p>
          <p className="max-w-sm text-sm text-muted-foreground">
            Agents added to this server appear here.
          </p>
        </div>
      ) : (
        <>
          {chatError && (
            <div
              role="alert"
              data-testid="teammates-chat-error"
              className="flex items-center gap-2 rounded-lg border border-destructive/30 bg-destructive/5 px-3 py-2 text-ui"
            >
              <TriangleAlertIcon className="size-4 shrink-0 text-destructive" />
              <span className="flex-1">{chatError}</span>
            </div>
          )}
          <div className="flex flex-col gap-2" data-testid="teammates-list">
            {rows.map((teammate) => (
              <TeammateRow
                key={teammate.agent.id}
                teammate={teammate}
                nowMs={now.getTime()}
                runPendingId={runPendingId}
                startingAgentId={startingAgentId}
                onAddRoutine={(agent) => setRoutineAgentId(agent.id)}
                onChat={handleChat}
                onOpenMemories={(agent) => setMemoryAgent(agent)}
                onOpenSettings={handleOpenSettings}
                onRunRoutine={handleRunNow}
              />
            ))}
          </div>
        </>
      )}

      <CreateScheduledTaskDialog
        open={routineAgentId !== null}
        onOpenChange={handleRoutineDialogChange}
        initialAgentId={routineAgentId ?? undefined}
      />
      {settingsTeammate && (
        <TeammateSettingsDialog
          teammate={settingsTeammate}
          open={settingsTeammate !== null}
          initialTab={settingsTab}
          hostId={chatHost?.host_id ?? null}
          onOpenChange={(open) => {
            if (!open) {
              setSettingsTeammate(null);
              void refetch();
            }
          }}
          onAddRoutine={(agentId) => {
            setRoutineAgentId(agentId);
          }}
          onRunRoutine={handleRunNow}
          runPendingId={runPendingId}
        />
      )}
      <TeammateMemoryDialog
        agent={memoryAgent}
        open={memoryAgent !== null}
        onOpenChange={(open) => {
          if (!open) setMemoryAgent(null);
        }}
      />
    </PageScroll>
  );
}

function TeammateRow({
  teammate,
  nowMs,
  runPendingId,
  startingAgentId,
  onAddRoutine,
  onChat,
  onOpenMemories,
  onOpenSettings,
  onRunRoutine,
}: {
  teammate: Teammate;
  nowMs: number;
  runPendingId: string | undefined;
  startingAgentId: string | null;
  onAddRoutine: (agent: TeammateAgent) => void;
  onChat: (teammate: Teammate, forceNew?: boolean) => void;
  onOpenMemories: (agent: TeammateAgent) => void;
  onOpenSettings: (teammate: Teammate, tab?: TeammateSettingsTab) => void;
  onRunRoutine: (routineId: string) => void;
}) {
  const { agent, routines } = teammate;
  const activityText =
    teammate.lastActivityAt == null
      ? "No activity yet"
      : `${relativeTime(teammate.lastActivityAt * 1000, nowMs)} ago`;

  return (
    <div
      data-testid={`teammate-${agent.name}`}
      className="rounded-lg border border-border/70 bg-card p-4"
    >
      <div className="flex items-start gap-3">
        <div className="flex size-9 shrink-0 items-center justify-center rounded-md bg-muted text-muted-foreground">
          <BotIcon className="size-5" />
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <h2 className="truncate text-ui font-semibold">{agent.name}</h2>
            {agent.harness && (
              <span className="rounded-md border border-border/70 px-1.5 py-0.5 font-mono text-10 uppercase tracking-normal text-muted-foreground">
                {agent.harness}
              </span>
            )}
            <span
              className={cn(
                "rounded-md px-1.5 py-0.5 text-10 font-medium",
                runStatusTone(teammate.lastActivityStatus),
              )}
            >
              {teammate.lastActivityStatus ?? "idle"}
            </span>
          </div>
          {agent.description && (
            <p className="mt-0.5 line-clamp-2 text-sm text-muted-foreground">{agent.description}</p>
          )}
          <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted-foreground">
            <span>{teammate.routineCount} routines</span>
            <span>{activityText}</span>
          </div>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <Button
            size="sm"
            disabled={startingAgentId === agent.id}
            onClick={() => onChat(teammate)}
            data-testid={`chat-${agent.name}`}
            componentId={`teammates.${agent.name}.chat`}
          >
            {startingAgentId === agent.id ? (
              <Loader2Icon className="size-3.5 animate-spin" />
            ) : (
              <MessageSquareTextIcon className="size-3.5" />
            )}
            Chat
          </Button>
          {teammate.primaryConversationId && (
            <Button
              variant="outline"
              size="sm"
              disabled={startingAgentId === agent.id}
              onClick={() => onChat(teammate, true)}
              data-testid={`new-topic-${agent.name}`}
              componentId={`teammates.${agent.name}.new_topic`}
              title="Start a separate conversation"
            >
              <PlusIcon className="size-3.5" />
              New Topic
            </Button>
          )}
          <Button
            variant="outline"
            size="sm"
            onClick={() => onOpenSettings(teammate, "profile")}
            data-testid={`settings-${agent.name}`}
            componentId={`teammates.${agent.name}.settings`}
            title="Configure teammate"
          >
            <SettingsIcon className="size-3.5" />
            Settings
          </Button>
          <Button
            variant="outline"
            size="sm"
            onClick={() => {
              onOpenMemories(agent);
              onOpenSettings(teammate, "memories");
            }}
            data-testid={`memories-${agent.name}`}
            componentId={`teammates.${agent.name}.memories`}
            title="View and edit memories"
          >
            <BrainIcon className="size-3.5" />
            Memories
          </Button>
          <Button
            variant="outline"
            size="sm"
            onClick={() => onAddRoutine(agent)}
            data-testid={`add-routine-${agent.name}`}
            componentId={`teammates.${agent.name}.add_routine`}
          >
            <PlusIcon className="size-3.5" />
            Routine
          </Button>
        </div>
      </div>

      {routines.length > 0 && (
        <div className="mt-3 border-t border-border/60 pt-3">
          <div className="flex flex-wrap gap-2">
            {routines.map((routine) => (
              <div
                key={routine.id}
                className="flex items-center gap-1.5 rounded-md border border-border/70 bg-background py-1 pr-1 pl-2 text-xs"
              >
                <span
                  aria-hidden
                  className={cn("size-1.5 rounded-full", runStatusDot(routine.lastRunStatus))}
                />
                <span className="font-medium">{routine.name}</span>
                <span className="text-muted-foreground">
                  {routine.lastRunStatus ? routine.lastRunStatus : routine.state}
                </span>
                <Button
                  variant="ghost"
                  size="icon"
                  className="size-6 text-muted-foreground hover:text-foreground"
                  aria-label={`Run ${routine.name} now`}
                  title="Run now"
                  disabled={runPendingId === routine.id}
                  onClick={() => onRunRoutine(routine.id)}
                  data-testid={`run-routine-${routine.id}`}
                  componentId={`teammates.routine.${routine.id}.run`}
                >
                  <PlayIcon className="size-3" />
                </Button>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function runStatusDot(status: TeammateRoutineRunStatus | null): string {
  if (status === "succeeded") return "bg-emerald-500";
  if (status === "failed" || status === "incomplete") return "bg-rose-500";
  if (status === "running") return "bg-amber-500";
  return "bg-muted-foreground/50";
}

function runStatusTone(status: TeammateRoutineRunStatus | null): string {
  if (status === "succeeded") return "bg-emerald-500/10 text-emerald-600";
  if (status === "failed" || status === "incomplete") return "bg-rose-500/10 text-rose-600";
  if (status === "running") return "bg-amber-500/10 text-amber-700";
  return "bg-muted text-muted-foreground";
}
