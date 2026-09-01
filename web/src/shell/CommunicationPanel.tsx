import {
  ArrowRightIcon,
  BotIcon,
  CheckCircleIcon,
  ClockIcon,
  MessageSquareShareIcon,
  PlusIcon,
  SendIcon,
} from "lucide-react";
import { useEffect, useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { useChildSessions } from "@/hooks/useChildSessions";
import { authenticatedFetch } from "@/lib/identity";

export interface AgentMessageDTO {
  message_id: string;
  sender_session_id: string;
  sender_role: string;
  recipient_session_id: string;
  recipient_role: string | null;
  kind: string;
  intent: string;
  payload: Record<string, any>;
  artifacts: Array<Record<string, any>>;
  message_state: string;
  created_at: number;
}

export function CommunicationPanel({ conversationId }: { conversationId: string }) {
  const [messages, setMessages] = useState<AgentMessageDTO[]>([]);
  const [loading, setLoading] = useState(false);
  const [draftText, setDraftText] = useState("");
  const [selectedRecipient, setSelectedRecipient] = useState<string>("");
  const { children } = useChildSessions(conversationId);

  const fetchMessages = async () => {
    try {
      setLoading(true);
      const res = await authenticatedFetch(
        `/v1/coordination/messages?root_session_id=${encodeURIComponent(conversationId)}`
      );
      if (res.ok) {
        const data = await res.json();
        setMessages(data.messages || []);
      }
    } catch (e) {
      console.warn("Failed to fetch coordination messages:", e);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void fetchMessages();
    const interval = window.setInterval(fetchMessages, 3000);
    return () => window.clearInterval(interval);
  }, [conversationId]);

  const handleSendMessage = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!draftText.trim()) return;

    const targetRecipient = selectedRecipient || (children[0]?.sessionId ?? conversationId);

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

  return (
    <div className="flex flex-col h-full text-xs" data-testid="communication-panel">
      <div className="flex items-center justify-between border-b p-3">
        <div className="flex items-center gap-1.5 font-semibold text-foreground">
          <MessageSquareShareIcon className="size-4 text-primary" />
          <span>A2A Communication Timeline</span>
        </div>
        <Badge variant="outline" className="font-mono text-[10px]">
          {messages.length} messages
        </Badge>
      </div>

      {/* Messages Feed */}
      <div className="flex-1 overflow-y-auto p-3 space-y-3">
        {messages.length === 0 ? (
          <div className="flex flex-col items-center justify-center p-8 text-center text-muted-foreground">
            <BotIcon className="size-8 opacity-40 mb-2" />
            <p>No peer messages sent between agents yet.</p>
            <p className="text-[11px] mt-1">
              Messages between Planner, Implementer, and Reviewer will appear here.
            </p>
          </div>
        ) : (
          messages.map((m) => (
            <Card key={m.message_id} size="sm" className="border-border">
              <CardHeader className="p-2 pb-1 flex flex-row items-center justify-between">
                <div className="flex items-center gap-1">
                  <Badge variant="secondary" className="text-[10px] font-mono capitalize">
                    {m.sender_role || "Agent"}
                  </Badge>
                  <ArrowRightIcon className="size-3 text-muted-foreground" />
                  <Badge variant="outline" className="text-[10px] font-mono capitalize">
                    {m.recipient_role || "Peer"}
                  </Badge>
                </div>
                <Badge variant="default" className="text-[9px] bg-primary/20 text-primary border-primary/30">
                  {m.intent}
                </Badge>
              </CardHeader>
              <CardContent className="p-2 pt-1">
                <p className="text-foreground text-xs whitespace-pre-wrap font-sans">
                  {m.payload?.prompt || m.payload?.instruction || JSON.stringify(m.payload)}
                </p>
                <div className="flex items-center justify-between mt-2 pt-1 border-t text-[10px] text-muted-foreground font-mono">
                  <span className="flex items-center gap-1">
                    <CheckCircleIcon className="size-2.5 text-emerald-500" />
                    <span>delivered</span>
                  </span>
                  <span>{new Date(m.created_at * 1000).toLocaleTimeString()}</span>
                </div>
              </CardContent>
            </Card>
          ))
        )}
      </div>

      {/* Manual A2A message composer */}
      <form onSubmit={handleSendMessage} className="border-t p-2 flex gap-1.5 bg-muted/20">
        <Input
          placeholder="Inject A2A coordination message…"
          value={draftText}
          onChange={(e) => setDraftText(e.target.value)}
          className="text-xs h-8"
        />
        <Button type="submit" size="sm" className="h-8 px-3">
          <SendIcon className="size-3" />
        </Button>
      </form>
    </div>
  );
}
