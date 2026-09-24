import {
  ArrowRightIcon,
  BotIcon,
  ChevronRightIcon,
  ExternalLinkIcon,
  FileTextIcon,
  Loader2Icon,
  MessageSquareShareIcon,
  SendIcon,
} from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { Card, CardContent, CardHeader } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { useChildSessions } from "@/hooks/useChildSessions";
import { authenticatedFetch } from "@/lib/identity";
import { Link } from "@/lib/routing";
import { cn } from "@/lib/utils";
import { WorkflowPanel } from "@/shell/WorkflowPanel";

export interface AgentMessageDTO {
  message_id: string;
  sender_session_id: string;
  sender_role: string;
  recipient_session_id: string;
  recipient_role: string | null;
  kind: string;
  intent: string;
  payload: Record<string, unknown>;
  artifacts: unknown[];
  message_state: string;
  consumption_state?: string;
  created_at: number;
}

function A2AMessageRow({ message }: { message: AgentMessageDTO }) {
  const [expanded, setExpanded] = useState(false);
  const [replyText, setReplyText] = useState<string | null>(null);
  const [loadingReply, setLoadingReply] = useState(false);

  useEffect(() => {
    if (!expanded || replyText !== null || !message.recipient_session_id) return;

    let cancelled = false;
    setLoadingReply(true);
    authenticatedFetch(
      `/v1/sessions/${encodeURIComponent(message.recipient_session_id)}/items?order=desc&limit=5`,
    )
      .then((res) => (res.ok ? res.json() : null))
      .then((data) => {
        if (cancelled) return;
        if (data?.data && Array.isArray(data.data)) {
          for (const item of data.data) {
            if (item.role === "assistant" && Array.isArray(item.content)) {
              let text = "";
              for (const c of item.content) {
                if (c.type === "output_text" && c.text) text += c.text;
              }
              if (text) {
                setReplyText(text);
                return;
              }
            }
          }
        }
        setReplyText("");
      })
      .catch(() => {
        if (!cancelled) setReplyText("");
      })
      .finally(() => {
        if (!cancelled) setLoadingReply(false);
      });

    return () => {
      cancelled = true;
    };
  }, [expanded, message.recipient_session_id, replyText]);

  const taskText = String(
    message.payload?.prompt || message.payload?.instruction || JSON.stringify(message.payload),
  );

  return (
    <Card
      size="sm"
      className={cn(
        "border-border transition-colors cursor-pointer hover:border-primary/40",
        expanded && "border-primary/60 bg-muted/10 shadow-xs",
      )}
      onClick={() => setExpanded((v) => !v)}
      data-testid={`a2a-message-${message.message_id}`}
    >
      <CardHeader className="p-2 pb-1.5 flex flex-row items-center justify-between select-none">
        <div className="flex items-center gap-1.5 min-w-0">
          <ChevronRightIcon
            className={cn(
              "size-3.5 text-muted-foreground transition-transform duration-150 shrink-0",
              expanded && "rotate-90",
            )}
          />
          <Badge variant="secondary" className="text-[10px] font-mono capitalize px-1.5 py-0">
            {message.sender_role || "Agent"}
          </Badge>
          <ArrowRightIcon className="size-3 text-muted-foreground shrink-0" />
          <Badge variant="outline" className="text-[10px] font-mono capitalize px-1.5 py-0">
            {message.recipient_role || "Peer"}
          </Badge>
        </div>
        <div className="flex items-center gap-1.5 shrink-0">
          <Badge
            variant="default"
            className="text-[9px] bg-primary/15 text-primary border-primary/30"
          >
            {message.intent}
          </Badge>
          <span className="text-[10px] text-muted-foreground font-mono">
            {new Date(message.created_at * 1000).toLocaleTimeString()}
          </span>
        </div>
      </CardHeader>

      <CardContent className="p-2 pt-0.5 space-y-2">
        {!expanded ? (
          <p className="text-muted-foreground text-xs font-sans line-clamp-2 pl-5">{taskText}</p>
        ) : (
          <div className="space-y-2.5 pl-5 pt-1 text-xs" onClick={(e) => e.stopPropagation()}>
            <div className="space-y-1">
              <span className="text-[10px] font-semibold text-muted-foreground uppercase tracking-wider">
                任务提示 / 指令 (Task Prompt)
              </span>
              <div className="rounded bg-muted/40 p-2 text-xs font-mono whitespace-pre-wrap select-text border border-border/40">
                {taskText}
              </div>
            </div>

            <div className="space-y-1">
              <div className="flex items-center justify-between">
                <span className="text-[10px] font-semibold text-muted-foreground uppercase tracking-wider">
                  对端执行汇报 / 结论 (Teammate Output)
                </span>
                {message.recipient_session_id && (
                  <Link
                    to={`/c/${message.recipient_session_id}`}
                    className="text-[11px] text-primary hover:underline flex items-center gap-0.5"
                    title="在主面板打开该会话"
                  >
                    查看完整会话现场 <ExternalLinkIcon className="size-3" />
                  </Link>
                )}
              </div>

              {loadingReply ? (
                <div className="flex items-center gap-1.5 py-2 text-muted-foreground text-xs">
                  <Loader2Icon className="size-3 animate-spin" />
                  <span>正在获取对端最新产出...</span>
                </div>
              ) : replyText ? (
                <div className="rounded border border-border bg-card p-2.5 text-xs whitespace-pre-wrap select-text max-h-60 overflow-y-auto font-sans leading-relaxed">
                  {replyText}
                </div>
              ) : (
                <div className="rounded border border-dashed border-border/60 p-2 text-[11px] text-muted-foreground/70 italic">
                  对端尚未产生文本结论或此消息为单向通知。
                </div>
              )}
            </div>

            {Array.isArray(message.artifacts) && message.artifacts.length > 0 && (
              <div className="space-y-1">
                <span className="text-[10px] font-semibold text-muted-foreground uppercase tracking-wider">
                  携带附件 ({message.artifacts.length})
                </span>
                <div className="flex flex-wrap gap-1">
                  {message.artifacts.map((a: any, idx) => (
                    <span
                      key={idx}
                      className="inline-flex items-center gap-1 rounded bg-muted/60 px-1.5 py-0.5 text-[10px] font-mono text-foreground"
                    >
                      <FileTextIcon className="size-2.5 text-muted-foreground" />
                      {a.filename || a.file_id || `file_${idx + 1}`}
                    </span>
                  ))}
                </div>
              </div>
            )}

            <div className="flex items-center justify-between pt-1 border-t border-border/50 text-[10px] text-muted-foreground font-mono">
              <span>
                状态: {message.message_state} / 消费: {message.consumption_state ?? "unconsumed"}
              </span>
              <span className="truncate max-w-40" title={message.message_id}>
                ID: {message.message_id}
              </span>
            </div>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

export function CommunicationPanel({
  conversationId,
  rootSessionId,
}: {
  conversationId: string;
  rootSessionId: string;
}) {
  const [messages, setMessages] = useState<AgentMessageDTO[]>([]);
  const [draftText, setDraftText] = useState("");
  const { children } = useChildSessions(conversationId);

  const fetchMessages = useCallback(async () => {
    try {
      const res = await authenticatedFetch(
        `/v1/coordination/messages?root_session_id=${encodeURIComponent(rootSessionId)}`,
      );
      if (res.ok) {
        const data = await res.json();
        setMessages(data.messages || []);
      }
    } catch (e) {
      console.warn("Failed to fetch coordination messages:", e);
    }
  }, [rootSessionId]);

  useEffect(() => {
    void fetchMessages();
    const interval = window.setInterval(fetchMessages, 3000);
    return () => window.clearInterval(interval);
  }, [fetchMessages]);

  const handleSendMessage = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!draftText.trim()) return;

    const targetRecipient = children[0]?.id ?? conversationId;

    try {
      await authenticatedFetch("/v1/coordination/messages", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          root_session_id: conversationId,
          sender_session_id: conversationId,
          sender_role: "user_orchestrator",
          recipient_session_id: targetRecipient,
          intent: "task.request",
          payload: { prompt: draftText.trim() },
        }),
      });
      setDraftText("");
      void fetchMessages();
    } catch (err) {
      console.error("Failed to send peer message:", err);
    }
  };

  const [timelineOpen, setTimelineOpen] = useState(false);

  return (
    <div className="flex flex-col h-full text-xs" data-testid="communication-panel">
      <div className="flex-1 overflow-y-auto p-3 space-y-3">
        <WorkflowPanel
          rootSessionId={rootSessionId}
          actorSessionId={conversationId}
          childSessions={children}
          onUpdated={() => void fetchMessages()}
        />

        {/* Collapsible A2A Audit Timeline */}
        <Collapsible
          open={timelineOpen}
          onOpenChange={setTimelineOpen}
          className="rounded-lg border border-border/80 bg-card overflow-hidden transition-all shadow-xs"
        >
          <CollapsibleTrigger asChild>
            <button
              type="button"
              className="flex w-full items-center justify-between p-2.5 bg-muted/20 hover:bg-muted/40 transition-colors text-left select-none cursor-pointer"
            >
              <div className="flex items-center gap-1.5 font-medium text-foreground">
                <ChevronRightIcon
                  className={cn(
                    "size-3.5 text-muted-foreground transition-transform duration-150 shrink-0",
                    timelineOpen && "rotate-90",
                  )}
                />
                <MessageSquareShareIcon className="size-3.5 text-primary" />
                <span>A2A 底层通信流水 (审计)</span>
              </div>
              <div className="flex items-center gap-1.5">
                <Badge variant="outline" className="font-mono text-[10px]">
                  {messages.length}
                </Badge>
              </div>
            </button>
          </CollapsibleTrigger>

          <CollapsibleContent className="p-2.5 pt-2 space-y-2.5 border-t border-border/60 bg-muted/5">
            <p className="text-[11px] text-muted-foreground leading-relaxed">
              A2A
              协同已在聊天主窗口以小窗就地呈现。此处为底层协调总线原始报文流水，供对账与排障审计使用。
            </p>

            {messages.length === 0 ? (
              <div className="flex flex-col items-center justify-center p-6 text-center text-muted-foreground">
                <BotIcon className="size-6 opacity-40 mb-1.5" />
                <p className="text-xs">暂无底层 A2A 消息记录。</p>
              </div>
            ) : (
              <div className="space-y-2">
                {messages.map((m) => (
                  <A2AMessageRow key={m.message_id} message={m} />
                ))}
              </div>
            )}

            <form
              onSubmit={handleSendMessage}
              className="pt-2 flex gap-1.5 border-t border-border/40"
            >
              <Input
                placeholder="发送手动调度指令..."
                value={draftText}
                onChange={(e) => setDraftText(e.target.value)}
                className="text-xs h-7"
              />
              <Button type="submit" size="sm" className="h-7 px-2.5 shrink-0">
                <SendIcon className="size-3" />
              </Button>
            </form>
          </CollapsibleContent>
        </Collapsible>
      </div>
    </div>
  );
}
