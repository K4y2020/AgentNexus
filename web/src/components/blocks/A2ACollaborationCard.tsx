// Structured card for an A2A teammate delegation (send_to_teammate tool call).
// Renders an inline sub-window in the message transcript, showing the
// cross-bot collaboration flow:
// - Header with caller -> target teammate, intent badge, status pill, and duration.
// - Task prompt dispatched to the teammate.
// - Live running state with animated progress shimmer.
// - Teammate response / report rendered as full markdown with expand/collapse.
// - Quick navigation link to the teammate's dedicated session workspace.

import {
  AlertCircleIcon,
  BotIcon,
  CheckCircle2Icon,
  ChevronDownIcon,
  ChevronRightIcon,
  ClockIcon,
  ExternalLinkIcon,
  FileTextIcon,
  Loader2Icon,
  MessagesSquareIcon,
  SendIcon,
} from "lucide-react";
import { useMemo, useState } from "react";
import { Link } from "@/lib/routing";
import { CodeBlock, CodeBlockHeader, CodeBlockTitle } from "@/components/ai-elements/code-block";
import { Shimmer } from "@/components/ai-elements/shimmer";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
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
        typeof obj.coordination_message_id === "string"
          ? obj.coordination_message_id
          : undefined,
      attachment_count:
        typeof obj.attachment_count === "number" ? obj.attachment_count : undefined,
      target_file_ids: Array.isArray(obj.target_file_ids)
        ? (obj.target_file_ids.filter((id) => typeof id === "string") as string[])
        : undefined,
      effective_model:
        typeof obj.effective_model === "string" ? obj.effective_model : undefined,
      model_source: typeof obj.model_source === "string" ? obj.model_source : undefined,
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
  const [outputExpanded, setOutputExpanded] = useState(true);
  const [rawOpen, setRawOpen] = useState(false);

  const rawTeammate = typeof args.teammate === "string" ? args.teammate : "";
  const taskPrompt = typeof args.task === "string" ? args.task : "";
  const intent = typeof args.intent === "string" ? args.intent : "task.request";
  const fileIds = Array.isArray(args.file_ids)
    ? (args.file_ids.filter((id) => typeof id === "string") as string[])
    : [];

  const parsed = useMemo(() => parseTeammateOutput(output), [output]);
  const teammateName = capitalizeName(parsed?.target_teammate || rawTeammate || "Teammate");

  const elapsedDuration = useElapsedDuration(
    state === "input-available" ? startedAt : null,
  );
  const displayDurationSeconds = duration ?? elapsedDuration;
  const formattedDuration =
    displayDurationSeconds !== undefined ? formatToolDuration(displayDurationSeconds) : null;

  const isRunning =
    state === "input-available" ||
    (!output && state !== "output-error" && state !== "cancelled" && state !== "no-output");

  const hasError = Boolean(parsed?.error) || state === "output-error" || state === "cancelled";
  const errorMessage =
    parsed?.error || (state === "cancelled" ? "协同任务已取消" : undefined);
  const isDispatchedOnly = parsed?.status === "dispatched" && !parsed.response;
  const isCompleted = Boolean(parsed?.response) || parsed?.status === "completed";

  const targetSessionUrl =
    parsed?.session_url ||
    (parsed?.target_session_id ? "/c/" + parsed.target_session_id : null);

  const isLongTask = taskPrompt.length > 180 || taskPrompt.includes("\n");

  const rawJson = useMemo(() => {
    return JSON.stringify({ arguments: args, output: parsed ?? output }, null, 2);
  }, [args, parsed, output]);

  return (
    <div
      className={cn(
        TOOL_SURFACE_WIDTH_CLASS,
        "my-2.5 overflow-hidden rounded-xl border border-border/80 bg-card text-card-foreground shadow-sm transition-all dark:border-border/60 dark:bg-card/75",
      )}
      data-testid="a2a-collaboration-card"
    >
      {/* 1. Header Bar */}
      <div className="flex items-center justify-between border-b border-border/60 bg-muted/25 px-3.5 py-2.5">
        <div className="flex items-center gap-2 min-w-0">
          <div className="flex size-7 items-center justify-center rounded-lg bg-primary/10 text-primary shrink-0">
            <MessagesSquareIcon className="size-4" />
          </div>
          <div className="flex items-center gap-1.5 min-w-0 text-xs sm:text-sm">
            <span className="font-semibold text-foreground tracking-tight">A2A 跨 Bot 协同</span>
            <span className="text-muted-foreground text-xs font-mono">➔</span>
            <span className="inline-flex items-center gap-1 font-semibold text-foreground rounded-md bg-muted/80 px-2 py-0.5 text-xs">
              <BotIcon className="size-3 text-primary" />
              {teammateName}
            </span>
            <span className="hidden sm:inline-flex text-[11px] rounded-full bg-secondary/80 text-secondary-foreground font-medium px-2 py-0.5 shrink-0">
              {formatIntentLabel(parsed?.intent || intent)}
            </span>
          </div>
        </div>

        <div className="flex items-center gap-2 shrink-0">
          {isRunning && (
            <span className="inline-flex items-center gap-1.5 rounded-full border border-amber-500/20 bg-amber-500/10 px-2.5 py-0.5 text-xs font-medium text-amber-600 dark:text-amber-400">
              <Loader2Icon className="size-3 animate-spin" />
              <span>协同中...</span>
            </span>
          )}
          {!isRunning && hasError && (
            <span className="inline-flex items-center gap-1.5 rounded-full border border-destructive/20 bg-destructive/10 px-2.5 py-0.5 text-xs font-medium text-destructive">
              <AlertCircleIcon className="size-3" />
              <span>异常</span>
            </span>
          )}
          {!isRunning && isCompleted && (
            <span className="inline-flex items-center gap-1.5 rounded-full border border-emerald-500/20 bg-emerald-500/10 px-2.5 py-0.5 text-xs font-medium text-emerald-600 dark:text-emerald-400">
              <CheckCircle2Icon className="size-3" />
              <span>已就绪</span>
            </span>
          )}
          {!isRunning && !hasError && !isCompleted && isDispatchedOnly && (
            <span className="inline-flex items-center gap-1.5 rounded-full border border-blue-500/20 bg-blue-500/10 px-2.5 py-0.5 text-xs font-medium text-blue-600 dark:text-blue-400">
              <ClockIcon className="size-3" />
              <span>已派发</span>
            </span>
          )}
          {formattedDuration && (
            <span className="text-xs tabular-nums text-muted-foreground opacity-75 font-mono">
              {formattedDuration}
            </span>
          )}
        </div>
      </div>

      {/* 2. Task Prompt Section */}
      <div className="border-b border-border/40 px-3.5 py-2.5">
        <div className="mb-1.5 flex items-center justify-between text-xs font-medium text-muted-foreground">
          <span className="flex items-center gap-1">
            <SendIcon className="size-3 text-muted-foreground" />
            <span>派发指令</span>
          </span>
          {isLongTask && (
            <button
              type="button"
              onClick={() => setTaskExpanded((prev) => !prev)}
              className="text-[11px] font-medium text-primary hover:underline cursor-pointer"
            >
              {taskExpanded ? "收起" : "展开全文"}
            </button>
          )}
        </div>
        <div
          className={cn(
            "rounded-lg bg-muted/40 p-2.5 text-xs font-mono leading-relaxed text-foreground/90 select-text whitespace-pre-wrap break-words border border-border/30",
            !taskExpanded && isLongTask && "max-h-20 overflow-hidden relative",
          )}
        >
          {taskPrompt || "(无任务描述)"}
          {!taskExpanded && isLongTask && (
            <div className="pointer-events-none absolute inset-x-0 bottom-0 h-8 rounded-b-lg bg-gradient-to-t from-muted/90 to-transparent" />
          )}
        </div>
        {fileIds.length > 0 && (
          <div className="mt-2 flex flex-wrap items-center gap-1.5 text-xs text-muted-foreground">
            <span className="text-[11px] font-medium">附件 ({fileIds.length}):</span>
            {fileIds.map((fid) => (
              <span
                key={fid}
                className="inline-flex items-center gap-1 rounded bg-muted/80 px-1.5 py-0.5 font-mono text-[11px]"
              >
                <FileTextIcon className="size-3 text-muted-foreground" />
                {fid.slice(0, 10)}…
              </span>
            ))}
          </div>
        )}
      </div>

      {/* 3. Live Running Indicator */}
      {isRunning && (
        <div className="flex flex-col gap-2.5 bg-muted/10 px-3.5 py-3.5 border-b border-border/40">
          <div className="flex items-center gap-2 text-xs text-muted-foreground">
            <Loader2Icon className="size-3.5 animate-spin text-amber-500 shrink-0" />
            <Shimmer className="text-xs font-medium text-foreground/80">
              {"正在等待 " + teammateName + " 执行并回传结论..."}
            </Shimmer>
          </div>
          <div className="h-1.5 overflow-hidden rounded-full bg-muted/60">
            <div className="h-full w-2/5 animate-pulse rounded-full bg-amber-500/60" />
          </div>
        </div>
      )}

      {/* 4. Error banner if any */}
      {errorMessage && (
        <div className="flex items-center gap-2 border-b border-border/40 bg-destructive/10 px-3.5 py-2.5 text-xs text-destructive">
          <AlertCircleIcon className="size-4 shrink-0" />
          <span>{errorMessage}</span>
        </div>
      )}

      {/* 5. Asynchronous Dispatched info */}
      {!isRunning && isDispatchedOnly && parsed?.message && (
        <div className="border-b border-border/40 bg-muted/15 px-3.5 py-2.5 text-xs leading-relaxed text-muted-foreground select-text">
          {parsed.message}
        </div>
      )}

      {/* 6. Teammate Response / Output Report */}
      {parsed?.response && (
        <div className="border-b border-border/40 px-3.5 py-2.5">
          <div className="mb-2 flex items-center justify-between text-xs font-medium text-muted-foreground">
            <span className="flex items-center gap-1.5 font-semibold text-emerald-600 dark:text-emerald-400">
              <CheckCircle2Icon className="size-3.5" />
              <span>对端回传成果 (Teammate Output)</span>
            </span>
            <button
              type="button"
              onClick={() => setOutputExpanded((prev) => !prev)}
              className="flex items-center gap-0.5 text-[11px] text-muted-foreground hover:text-foreground cursor-pointer"
            >
              <span>{outputExpanded ? "收起" : "展开"}</span>
              <ChevronDownIcon
                className={cn("size-3 transition-transform", !outputExpanded && "-rotate-90")}
              />
            </button>
          </div>
          {outputExpanded && (
            <div className="max-h-96 overflow-y-auto rounded-lg border border-border/50 bg-background/80 p-3 text-xs leading-relaxed text-foreground select-text shadow-inner">
              <FilePathAwareMessageResponse breaks>
                {parsed.response}
              </FilePathAwareMessageResponse>
            </div>
          )}
        </div>
      )}

      {/* 7. Footer Bar: Navigation and Raw Details */}
      <div className="flex items-center justify-between bg-muted/15 px-3.5 py-2 text-xs">
        <div className="flex items-center gap-2">
          {targetSessionUrl ? (
            <Link
              to={targetSessionUrl}
              className="inline-flex items-center gap-1.5 text-xs font-medium text-primary hover:underline"
            >
              <BotIcon className="size-3.5" />
              <span>打开 {teammateName} 独立工作台</span>
              <ExternalLinkIcon className="size-3" />
            </Link>
          ) : (
            <span className="text-[11px] text-muted-foreground font-mono">
              A2A Direct Channel
            </span>
          )}
        </div>

        <Collapsible open={rawOpen} onOpenChange={setRawOpen}>
          <CollapsibleTrigger asChild>
            <button
              type="button"
              className="inline-flex items-center gap-1 text-[11px] text-muted-foreground hover:text-foreground cursor-pointer"
            >
              <span>原始数据</span>
              <ChevronRightIcon
                className={cn("size-3 transition-transform", rawOpen && "rotate-90")}
              />
            </button>
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
    </div>
  );
}
