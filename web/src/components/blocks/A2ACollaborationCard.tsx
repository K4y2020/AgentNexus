// Structured card for an A2A teammate delegation (send_to_teammate tool call).
// Renders an inline sub-window in the message transcript, showing the
// cross-bot collaboration flow:
// - Header with caller -> target teammate, intent badge, status pill, and duration.
// - Task prompt dispatched to the teammate.
// - Live running state with animated progress shimmer.
// - Teammate response / report rendered as full markdown with expand/collapse.
// - Quick navigation link to the teammate's dedicated session workspace.

import { openSeedanceCanvas, SeedanceCanvasContext } from "@/lib/seedanceCanvas";
import {
  AlertCircleIcon,
  ArrowRightIcon,
  BotIcon,
  CheckCircle2Icon,
  ChevronDownIcon,
  ChevronRightIcon,
  ClockIcon,
  ExternalLinkIcon,
  PanelRightOpenIcon,
  FileTextIcon,
  FilmIcon,
  MessagesSquareIcon,
  SendIcon,
} from "lucide-react";
import { useContext, useEffect, useMemo, useState } from "react";
import { Link } from "@/lib/routing";
import { authenticatedFetch } from "@/lib/identity";
import { CodeBlock, CodeBlockHeader, CodeBlockTitle } from "@/components/ai-elements/code-block";
import { Shimmer } from "@/components/ai-elements/shimmer";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Spinner } from "@/components/ui/spinner";
import type { ToolState } from "@/lib/renderItems";
import { cn } from "@/lib/utils";
import { TOOL_SURFACE_WIDTH_CLASS } from "./toolSurface";
import { FilePathAwareMessageResponse } from "./ChatMarkdown";
import { formatToolDuration, useElapsedDuration } from "./ToolCard";

export interface ParsedTeammateOutput {
  status?: string;
  target_teammate?: string;
  target_session_id?: string;
  session_url?: string;
  response?: string;
  message?: string;
  error?: string;
  intent?: string;
  coordination_message_id?: string;
  attachment_count?: number;
  target_file_ids?: string[];
  effective_model?: string;
  model_source?: string;
  channel_kind?: "chat" | "topic";
  channel_scope?: string;
  channel_source_session_id?: string;
  seedance_project_id?: string;
  seedance_agent_session_id?: string;
  seedance_canvas_url?: string;
  seedance_job_ids?: string[];
  seedance_asset_ids?: string[];
  seedance_status?: string;
}

interface CoordinationSnapshot {
  message?: { message_state?: string };
  result?: {
    payload?: { summary?: string; prompt?: string; outcome?: string } & Pick<
      ParsedTeammateOutput,
      | "seedance_project_id"
      | "seedance_agent_session_id"
      | "seedance_canvas_url"
      | "seedance_job_ids"
      | "seedance_asset_ids"
      | "seedance_status"
    >;
  } | null;
  delivery_state?: string;
}

export function applyCoordinationSnapshot(
  parsed: ParsedTeammateOutput | null,
  snapshot: CoordinationSnapshot,
): ParsedTeammateOutput | null {
  if (!parsed) return parsed;
  const result = snapshot.result;
  const outcome = result?.payload?.outcome;
  if (result && (outcome === "succeeded" || outcome === "failed")) {
    const payload = result.payload as Record<string, unknown> | undefined;
    return {
      ...parsed,
      status: outcome === "succeeded" ? "completed" : "failed",
      response: result.payload?.summary || result.payload?.prompt || parsed.response,
      seedance_project_id:
        typeof payload?.seedance_project_id === "string"
          ? payload.seedance_project_id
          : parsed.seedance_project_id,
      seedance_agent_session_id:
        typeof payload?.seedance_agent_session_id === "string"
          ? payload.seedance_agent_session_id
          : parsed.seedance_agent_session_id,
      seedance_canvas_url:
        typeof payload?.seedance_canvas_url === "string"
          ? payload.seedance_canvas_url
          : parsed.seedance_canvas_url,
      seedance_job_ids: Array.isArray(payload?.seedance_job_ids)
        ? (payload.seedance_job_ids.filter((id) => typeof id === "string") as string[])
        : parsed.seedance_job_ids,
      seedance_asset_ids: Array.isArray(payload?.seedance_asset_ids)
        ? (payload.seedance_asset_ids.filter((id) => typeof id === "string") as string[])
        : parsed.seedance_asset_ids,
      seedance_status:
        typeof payload?.seedance_status === "string"
          ? payload.seedance_status
          : parsed.seedance_status,
    };
  }
  if (
    snapshot.delivery_state === "failed" ||
    snapshot.message?.message_state === "cancelled" ||
    snapshot.message?.message_state === "expired"
  ) {
    return { ...parsed, status: "failed", error: parsed.error || "A2A 请求未能完成" };
  }
  return parsed;
}

export function parseTeammateOutput(output: string | null): ParsedTeammateOutput | null {
  if (!output) return null;
  let parsed: unknown;
  try {
    parsed = JSON.parse(output);
  } catch {
    return { response: output };
  }

  if (Array.isArray(parsed)) {
    const textObj = parsed.find(
      (item): item is { type: string; text: string } =>
        typeof item === "object" &&
        item !== null &&
        (item as Record<string, unknown>).type === "text" &&
        typeof (item as Record<string, unknown>).text === "string",
    );
    if (textObj) {
      return parseTeammateOutput(textObj.text);
    }
  }

  if (typeof parsed === "object" && parsed !== null) {
    const obj = parsed as Record<string, unknown>;
    return {
      status: typeof obj.status === "string" ? obj.status : undefined,
      target_teammate: typeof obj.target_teammate === "string" ? obj.target_teammate : undefined,
      target_session_id:
        typeof obj.target_session_id === "string" ? obj.target_session_id : undefined,
      session_url: typeof obj.session_url === "string" ? obj.session_url : undefined,
      response: typeof obj.response === "string" ? obj.response : undefined,
      message: typeof obj.message === "string" ? obj.message : undefined,
      error: typeof obj.error === "string" ? obj.error : undefined,
      intent: typeof obj.intent === "string" ? obj.intent : undefined,
      coordination_message_id:
        typeof obj.coordination_message_id === "string" ? obj.coordination_message_id : undefined,
      attachment_count: typeof obj.attachment_count === "number" ? obj.attachment_count : undefined,
      target_file_ids: Array.isArray(obj.target_file_ids)
        ? (obj.target_file_ids.filter((id) => typeof id === "string") as string[])
        : undefined,
      effective_model: typeof obj.effective_model === "string" ? obj.effective_model : undefined,
      model_source: typeof obj.model_source === "string" ? obj.model_source : undefined,
      channel_kind:
        obj.channel_kind === "chat" || obj.channel_kind === "topic" ? obj.channel_kind : undefined,
      channel_scope: typeof obj.channel_scope === "string" ? obj.channel_scope : undefined,
      channel_source_session_id:
        typeof obj.channel_source_session_id === "string"
          ? obj.channel_source_session_id
          : undefined,
      seedance_project_id:
        typeof obj.seedance_project_id === "string" ? obj.seedance_project_id : undefined,
      seedance_agent_session_id:
        typeof obj.seedance_agent_session_id === "string"
          ? obj.seedance_agent_session_id
          : undefined,
      seedance_canvas_url:
        typeof obj.seedance_canvas_url === "string" ? obj.seedance_canvas_url : undefined,
      seedance_job_ids: Array.isArray(obj.seedance_job_ids)
        ? (obj.seedance_job_ids.filter((id) => typeof id === "string") as string[])
        : undefined,
      seedance_asset_ids: Array.isArray(obj.seedance_asset_ids)
        ? (obj.seedance_asset_ids.filter((id) => typeof id === "string") as string[])
        : undefined,
      seedance_status: typeof obj.seedance_status === "string" ? obj.seedance_status : undefined,
    };
  }

  return { response: String(parsed) };
}

export function formatIntentLabel(intent?: string): string {
  switch (intent) {
    case "review.request":
      return "代码评审";
    case "task.request":
      return "任务派发";
    case "question":
      return "问答协作";
    case "status.inquiry":
      return "状态查询";
    default:
      return intent || "协同请求";
  }
}

function capitalizeName(name: string): string {
  if (!name) return "";
  return name.charAt(0).toUpperCase() + name.slice(1);
}

export interface A2ACollaborationCardProps {
  /** Full send_to_teammate arguments dict */
  arguments: Record<string, unknown>;
  /** Output string from tool invocation; null while running / pending */
  output: string | null;
  /** Current tool state */
  state: ToolState;
  startedAt?: number | null;
  duration?: number;
}

export function A2ACollaborationCard({
  arguments: args,
  output,
  state,
  startedAt,
  duration,
}: A2ACollaborationCardProps) {
  const [taskExpanded, setTaskExpanded] = useState(false);
  const cineCanvasEnabled = useContext(SeedanceCanvasContext);
  const [outputExpanded, setOutputExpanded] = useState(true);
  const [rawOpen, setRawOpen] = useState(false);

  const rawTeammate = typeof args.teammate === "string" ? args.teammate : "";
  const taskPrompt = typeof args.task === "string" ? args.task : "";
  const intent = typeof args.intent === "string" ? args.intent : "task.request";
  const fileIds = Array.isArray(args.file_ids)
    ? (args.file_ids.filter((id) => typeof id === "string") as string[])
    : [];

  const parsed = useMemo(() => parseTeammateOutput(output), [output]);
  const [coordinationSnapshot, setCoordinationSnapshot] = useState<CoordinationSnapshot | null>(
    null,
  );
  const coordinationMessageId = parsed?.coordination_message_id;

  useEffect(() => {
    if (!coordinationMessageId || parsed?.status !== "dispatched") {
      setCoordinationSnapshot(null);
      return;
    }
    let cancelled = false;
    let timer: number | undefined;
    let delay = 2000;
    const check = async () => {
      try {
        const response = await authenticatedFetch(
          `/v1/coordination/messages/${encodeURIComponent(coordinationMessageId)}`,
        );
        if (!response.ok) throw new Error(`coordination status ${response.status}`);
        const snapshot = (await response.json()) as CoordinationSnapshot;
        if (cancelled) return;
        setCoordinationSnapshot(snapshot);
        const terminal = Boolean(
          snapshot.result ||
          snapshot.delivery_state === "failed" ||
          snapshot.message?.message_state === "cancelled" ||
          snapshot.message?.message_state === "expired",
        );
        if (!terminal) {
          delay = Math.min(delay * 1.5, 10000);
          timer = window.setTimeout(check, delay);
        }
      } catch {
        if (!cancelled) {
          delay = Math.min(delay * 1.5, 10000);
          timer = window.setTimeout(check, delay);
        }
      }
    };
    void check();
    return () => {
      cancelled = true;
      if (timer !== undefined) window.clearTimeout(timer);
    };
  }, [coordinationMessageId, parsed?.status]);

  const displayedParsed = useMemo(
    () => applyCoordinationSnapshot(parsed, coordinationSnapshot ?? {}),
    [parsed, coordinationSnapshot],
  );
  const teammateName = capitalizeName(
    displayedParsed?.target_teammate || rawTeammate || "Teammate",
  );

  const elapsedDuration = useElapsedDuration(state === "input-available" ? startedAt : null);
  const displayDurationSeconds = duration ?? elapsedDuration;
  const formattedDuration =
    displayDurationSeconds !== undefined ? formatToolDuration(displayDurationSeconds) : null;

  const isRunning =
    state === "input-available" ||
    (!output && state !== "output-error" && state !== "cancelled" && state !== "no-output");

  const hasError =
    Boolean(displayedParsed?.error) ||
    displayedParsed?.status === "failed" ||
    state === "output-error" ||
    state === "cancelled";
  const errorMessage =
    displayedParsed?.error || (state === "cancelled" ? "协同任务已取消" : undefined);
  const isDispatchedOnly = displayedParsed?.status === "dispatched" && !displayedParsed.response;
  const isCompleted = !hasError && displayedParsed?.status === "completed";

  const targetSessionUrl =
    displayedParsed?.session_url ||
    (displayedParsed?.target_session_id ? "/c/" + displayedParsed.target_session_id : null);

  const isLongTask = taskPrompt.length > 180 || taskPrompt.includes("\n");

  const rawJson = useMemo(() => {
    return JSON.stringify({ arguments: args, output: displayedParsed ?? output }, null, 2);
  }, [args, displayedParsed, output]);

  return (
    <Card
      className={cn(TOOL_SURFACE_WIDTH_CLASS, "my-3 gap-0 py-0")}
      data-testid="a2a-collaboration-card"
    >
      {/* 1. Header Bar */}
      <div className="flex flex-wrap items-center justify-between gap-2 border-b px-4 py-3">
        <div className="flex min-w-0 items-center gap-2">
          <MessagesSquareIcon className="size-4 shrink-0 text-muted-foreground" />
          <div className="flex min-w-0 flex-wrap items-center gap-2 text-sm">
            <span className="font-semibold">A2A 跨 Bot 协同</span>
            <ArrowRightIcon className="size-3 shrink-0 text-muted-foreground" />
            <Badge variant="secondary" className="max-w-full">
              <BotIcon />
              <span className="truncate">{teammateName}</span>
            </Badge>
            <Badge variant="outline" className="hidden sm:inline-flex">
              {formatIntentLabel(displayedParsed?.intent || intent)}
            </Badge>
            {displayedParsed?.channel_kind && (
              <Badge variant="outline" className="hidden sm:inline-flex">
                {displayedParsed.channel_kind === "topic" ? "当前 Topic" : "当前 Chat"}
              </Badge>
            )}
            {(displayedParsed?.seedance_project_id ||
              displayedParsed?.target_teammate?.toLowerCase() === "seedance") && (
              <Badge
                variant="outline"
                className="hidden sm:inline-flex border-violet-500/40 text-violet-600 dark:text-violet-400"
              >
                Seedance 画布联动
              </Badge>
            )}
          </div>
        </div>

        <div className="flex items-center gap-2 shrink-0">
          {isRunning && (
            <Badge variant="info">
              <Spinner aria-label="协同中" />
              <span>协同中...</span>
            </Badge>
          )}
          {!isRunning && hasError && (
            <Badge variant="destructive">
              <AlertCircleIcon />
              <span>异常</span>
            </Badge>
          )}
          {!isRunning && isCompleted && (
            <Badge variant="success">
              <CheckCircle2Icon />
              <span>已完成</span>
            </Badge>
          )}
          {!isRunning && !hasError && !isCompleted && isDispatchedOnly && (
            <Badge variant="info">
              <ClockIcon />
              <span>已派发</span>
            </Badge>
          )}
          {formattedDuration && (
            <span className="text-sm tabular-nums text-muted-foreground opacity-75 font-mono">
              {formattedDuration}
            </span>
          )}
        </div>
      </div>

      {/* 2. Task Prompt Section */}
      <div className="border-b border-border-weak px-4 py-3">
        <div className="mb-1.5 flex items-center justify-between text-sm font-medium text-muted-foreground">
          <span className="flex items-center gap-1">
            <SendIcon className="size-3 text-muted-foreground" />
            <span>派发指令</span>
          </span>
          {isLongTask && (
            <Button
              type="button"
              variant="ghost"
              size="xs"
              aria-expanded={taskExpanded}
              onClick={() => setTaskExpanded((prev) => !prev)}
            >
              {taskExpanded ? "收起" : "展开全文"}
            </Button>
          )}
        </div>
        <div
          className={cn(
            "text-sm leading-relaxed select-text whitespace-pre-wrap break-words",
            !taskExpanded && isLongTask && "max-h-20 overflow-hidden relative",
          )}
        >
          {taskPrompt || "(无任务描述)"}
          {!taskExpanded && isLongTask && (
            <div className="pointer-events-none absolute inset-x-0 bottom-0 h-8 rounded-b-lg bg-gradient-to-t from-muted/90 to-transparent" />
          )}
        </div>
        {fileIds.length > 0 && (
          <div className="mt-2 flex flex-wrap items-center gap-1.5 text-sm text-muted-foreground">
            <span className="text-sm font-medium">附件 ({fileIds.length}):</span>
            {fileIds.map((fid) => (
              <Badge key={fid} variant="secondary" className="font-mono">
                <FileTextIcon className="size-3 text-muted-foreground" />
                {fid.slice(0, 10)}…
              </Badge>
            ))}
          </div>
        )}
      </div>

      {/* 3. Live Running Indicator */}
      {isRunning && (
        <div className="flex flex-col gap-2.5 bg-muted/10 p-4 border-b border-border-weak">
          <div className="flex items-center gap-2 text-sm text-muted-foreground">
            <Spinner className="shrink-0 text-info" aria-label="等待回传" />
            <Shimmer className="text-sm font-medium text-foreground/80">
              {"正在等待 " + teammateName + " 执行并回传结论..."}
            </Shimmer>
          </div>
        </div>
      )}

      {/* 4. Error banner if any */}
      {errorMessage && (
        <div className="flex items-center gap-2 border-b border-border-weak bg-destructive/10 px-4 py-3 text-sm text-destructive">
          <AlertCircleIcon className="size-4 shrink-0" />
          <span>{errorMessage}</span>
        </div>
      )}

      {/* 5. Asynchronous Dispatched info */}
      {!isRunning && isDispatchedOnly && displayedParsed?.message && (
        <div className="border-b border-border-weak bg-muted/15 px-4 py-3 text-sm leading-relaxed text-muted-foreground select-text">
          {displayedParsed.message}
        </div>
      )}

      {/* 6. Teammate Response / Output Report */}
      {displayedParsed?.response && (
        <div className="border-b border-border-weak px-4 py-3">
          <div className="mb-2 flex flex-wrap items-center justify-between gap-2 text-sm font-medium text-muted-foreground">
            <span className="flex min-w-0 items-center gap-1.5 font-semibold text-foreground">
              {hasError ? (
                <AlertCircleIcon className="size-3.5 shrink-0 text-destructive" />
              ) : (
                <CheckCircle2Icon className="size-3.5 shrink-0 text-success" />
              )}
              <span>{hasError ? "失败详情" : "对端回传成果 (Teammate Output)"}</span>
            </span>
            <Button
              type="button"
              variant="ghost"
              size="xs"
              aria-expanded={outputExpanded}
              onClick={() => setOutputExpanded((prev) => !prev)}
            >
              <span>{outputExpanded ? "收起" : "展开"}</span>
              <ChevronDownIcon
                className={cn("size-3 transition-transform", !outputExpanded && "-rotate-90")}
              />
            </Button>
          </div>
          {outputExpanded && (
            <div className="max-h-96 overflow-y-auto text-ui leading-relaxed select-text break-words">
              <FilePathAwareMessageResponse breaks>
                {displayedParsed.response}
              </FilePathAwareMessageResponse>
            </div>
          )}
        </div>
      )}

      {/* 6.5 Seedance Canvas & Artifacts Section */}
      {(displayedParsed?.seedance_project_id || displayedParsed?.seedance_canvas_url) && (
        <div
          className="border-b border-border-weak bg-muted/10 px-4 py-3 text-sm space-y-2.5"
          data-testid="seedance-collaboration-section"
        >
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div className="flex flex-wrap items-center gap-2">
              <FilmIcon className="size-4 text-violet-500 shrink-0" />
              <span className="font-semibold text-foreground">Seedance V3 画布</span>
              {displayedParsed.seedance_project_id && (
                <Badge variant="outline" className="font-mono text-xs">
                  项目: {displayedParsed.seedance_project_id}
                </Badge>
              )}
              {displayedParsed.seedance_agent_session_id && (
                <Badge variant="outline" className="font-mono text-xs">
                  会话: {displayedParsed.seedance_agent_session_id.slice(0, 12)}…
                </Badge>
              )}
            </div>
            {cineCanvasEnabled && (
              <Button asChild variant="outline" size="xs">
                <a
                  href={
                    displayedParsed.seedance_canvas_url ||
                    `http://127.0.0.1:5173/?project=${encodeURIComponent(displayedParsed.seedance_project_id!)}`
                  }
                  target="_blank"
                  rel="noopener noreferrer"
                  className="inline-flex items-center gap-1.5"
                  data-testid="open-seedance-canvas-link"
                  onClick={openSeedanceCanvas}
                >
                  <span>打开 Seedance 画布</span>
                  <PanelRightOpenIcon className="size-3" />
                </a>
              </Button>
            )}
          </div>

          {displayedParsed.seedance_job_ids && displayedParsed.seedance_job_ids.length > 0 && (
            <div className="flex flex-wrap items-center gap-1.5 text-xs text-muted-foreground">
              <span className="font-medium">
                生成任务 ({displayedParsed.seedance_job_ids.length}):
              </span>
              {displayedParsed.seedance_job_ids.map((jid) => (
                <Badge key={jid} variant="secondary" className="font-mono text-[11px]">
                  {jid}
                </Badge>
              ))}
            </div>
          )}

          {displayedParsed.seedance_asset_ids && displayedParsed.seedance_asset_ids.length > 0 && (
            <div className="flex flex-wrap items-center gap-1.5 text-xs text-muted-foreground">
              <span className="font-medium">
                产物资产 ({displayedParsed.seedance_asset_ids.length}):
              </span>
              {displayedParsed.seedance_asset_ids.map((aid) => (
                <Badge key={aid} variant="outline" className="font-mono text-[11px]">
                  {aid}
                </Badge>
              ))}
            </div>
          )}
        </div>
      )}

      {/* 7. Footer Bar: Navigation and Raw Details */}
      <div className="flex flex-wrap items-center justify-between gap-2 px-4 py-2 text-sm">
        <div className="min-w-0">
          {targetSessionUrl ? (
            <Button asChild variant="link" size="sm" className="max-w-full">
              <Link to={targetSessionUrl}>
                <BotIcon className="size-3.5" />
                <span className="truncate">打开 {teammateName} 独立工作台</span>
                <ExternalLinkIcon className="size-3" />
              </Link>
            </Button>
          ) : (
            <span className="text-sm text-muted-foreground font-mono">A2A Direct Channel</span>
          )}
        </div>

        <Collapsible
          open={rawOpen}
          onOpenChange={setRawOpen}
          className="min-w-0 max-w-full data-[state=open]:w-full"
        >
          <CollapsibleTrigger asChild>
            <Button type="button" variant="ghost" size="xs">
              <span>原始数据</span>
              <ChevronRightIcon
                className={cn("size-3 transition-transform", rawOpen && "rotate-90")}
              />
            </Button>
          </CollapsibleTrigger>
          <CollapsibleContent className="mt-2 text-left">
            <div className="mt-1 space-y-2">
              <CodeBlock code={rawJson} language="json">
                <CodeBlockHeader>
                  <CodeBlockTitle>A2A Raw Payload</CodeBlockTitle>
                </CodeBlockHeader>
              </CodeBlock>
            </div>
          </CollapsibleContent>
        </Collapsible>
      </div>
    </Card>
  );
}
