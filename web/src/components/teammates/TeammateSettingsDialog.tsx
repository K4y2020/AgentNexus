// Unified Bot Settings Dialog: centralized profile, model & engine, memories, routines, and skills.

import { useEffect, useState } from "react";
import {
  BotIcon,
  BrainIcon,
  CheckIcon,
  ClockIcon,
  CpuIcon,
  ListChecksIcon,
  Loader2Icon,
  PencilIcon,
  PlayIcon,
  PlusIcon,
  SaveIcon,
  SparklesIcon,
  TerminalIcon,
  Trash2Icon,
  TriangleAlertIcon,
  WrenchIcon,
  XIcon,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Textarea } from "@/components/ui/textarea";
import { useHosts, useHostModelOptions } from "@/hooks/useHosts";
import { useQueryClient } from "@tanstack/react-query";
import { useSession } from "@/hooks/useSession";
import { useChatStore } from "@/store/chatStore";
import { updateSession } from "@/lib/sessionsApi";
import {
  useCreateTeammateMemory,
  useDeleteTeammateMemory,
  useTeammateMemories,
  useUpdateTeammateMemory,
} from "@/hooks/useTeammateMemories";
import { updateBot, type Teammate } from "@/lib/teammatesApi";

export type TeammateSettingsTab = "profile" | "model" | "memories" | "routines" | "skills";

interface TeammateSettingsDialogProps {
  teammate: Teammate | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  initialTab?: TeammateSettingsTab;
  hostId?: string | null;
  onAddRoutine?: (agentId: string) => void;
  onRunRoutine?: (routineId: string) => void;
  runPendingId?: string;
}

export function TeammateSettingsDialog({
  teammate,
  open,
  onOpenChange,
  initialTab = "profile",
  hostId = null,
  onAddRoutine,
  onRunRoutine,
  runPendingId,
}: TeammateSettingsDialogProps) {
  const [tab, setTab] = useState<string>(initialTab);
  const agent = teammate?.agent ?? null;
  const agentId = agent?.id ?? "";
  const queryClient = useQueryClient();

  // Memories hooks
  const {
    data: memories,
    isLoading: memoriesLoading,
    isError: memoriesError,
  } = useTeammateMemories(agentId || null);
  const createMemoryMutation = useCreateTeammateMemory(agentId);
  const updateMemoryMutation = useUpdateTeammateMemory(agentId);
  const deleteMemoryMutation = useDeleteTeammateMemory(agentId);

  const [memoryDraft, setMemoryDraft] = useState("");
  const [editingMemoryId, setEditingMemoryId] = useState<string | null>(null);
  const [memoryError, setMemoryError] = useState<string | null>(null);

  const conversationId = useChatStore((s) => s.conversationId);
  const targetConvId = conversationId ?? teammate?.primaryConversationId ?? null;
  const { session } = useSession(targetConvId);
  const { data: hosts } = useHosts();
  const effectiveHostId =
    hostId ?? session?.hostId ?? hosts?.find((h) => h.status === "online")?.host_id ?? null;

  // Debby subagent model options
  const isDebby = agent?.name.toLowerCase() === "debby";
  const isPolly = agent?.name.toLowerCase() === "polly";

  const { data: claudePartnerOptions = [] } = useHostModelOptions(
    effectiveHostId,
    "claude-sdk",
    open && (isDebby || isPolly) && Boolean(effectiveHostId),
  );
  const { data: gptPartnerOptions = [] } = useHostModelOptions(
    effectiveHostId,
    "codex",
    open && (isDebby || isPolly) && Boolean(effectiveHostId),
  );
  const { data: codebuddyOptions = [] } = useHostModelOptions(
    effectiveHostId,
    "codebuddy",
    open && isPolly && Boolean(effectiveHostId),
  );

  // Model options hook
  const { data: modelOptions = [] } = useHostModelOptions(
    effectiveHostId,
    agent?.harness ?? "claude-sdk",
    open && Boolean(effectiveHostId),
  );

  const sessionClaudeModel = session?.labels?.["subagent.model.claude"] || null;
  const sessionGptModel = session?.labels?.["subagent.model.gpt"] || null;

  const [claudePartnerModel, setClaudePartnerModel] = useState<string>("default");
  const [gptPartnerModel, setGptPartnerModel] = useState<string>("default");
  const [debbySaved, setDebbySaved] = useState(false);

  // Polly subagent models
  const sessionPollyClaudeModel = session?.labels?.["subagent.model.claude_code"] || null;
  const sessionPollyCodexModel = session?.labels?.["subagent.model.codex"] || null;
  const sessionPollyCodebuddyModel = session?.labels?.["subagent.model.codebuddy"] || null;

  const [pollyClaudeModel, setPollyClaudeModel] = useState<string>("default");
  const [pollyCodexModel, setPollyCodexModel] = useState<string>("default");
  const [pollyCodebuddyModel, setPollyCodebuddyModel] = useState<string>("default");
  const [pollySubagentsSaved, setPollySubagentsSaved] = useState(false);

  // Bot primary model state
  const [selectedBotModel, setSelectedBotModel] = useState<string>("default");
  const [modelSaved, setModelSaved] = useState(false);

  useEffect(() => {
    if (open) {
      const current =
        teammate?.bot.defaultModel || session?.modelOverride || (session?.llmModel ?? "default");
      setSelectedBotModel(current || "default");
      setModelSaved(false);
    }
  }, [open, teammate?.bot.defaultModel, session?.modelOverride, session?.llmModel]);

  async function handleSaveBotModel() {
    const targetModel = selectedBotModel === "default" ? null : selectedBotModel;
    try {
      if (teammate) {
        await updateBot(teammate.bot.id, { defaultModel: targetModel });
        await queryClient.invalidateQueries({ queryKey: ["teammates"] });
      }
      await useChatStore.getState().setModel(targetModel);
      if (targetConvId) {
        await queryClient.invalidateQueries({ queryKey: ["session", targetConvId] });
      }
    } catch {
      // non-fatal
    }
    setModelSaved(true);
    setTimeout(() => setModelSaved(false), 2500);
  }

  // Polly subagent routing state
  const subagentRoutingOverride = useChatStore((s) => s.subagentRoutingOverride);
  const [pollyRoutingOn, setPollyRoutingOn] = useState<boolean>(
    session?.labels?.["agentnexus.routing.subagents"] === "on" || subagentRoutingOverride === "on",
  );
  const [pollySaved, setPollySaved] = useState(false);

  // Behavior Pack / Lean mode
  const sessionBehaviorMode = session?.labels?.["agentnexus.behavior_mode"] || "off";
  const [behaviorMode, setBehaviorMode] = useState<string>("off");
  const [behaviorSaved, setBehaviorSaved] = useState(false);

  async function handleSaveBehaviorMode(val: string) {
    setBehaviorMode(val);
    if (teammate) {
      await updateBot(teammate.bot.id, {
        behaviorMode: val as "off" | "advisory" | "lean" | "strict",
      });
      await queryClient.invalidateQueries({ queryKey: ["teammates"] });
    }
    if (targetConvId) {
      try {
        await updateSession(targetConvId, {
          labels: { "agentnexus.behavior_mode": val },
          silent: true,
        });
        await queryClient.invalidateQueries({ queryKey: ["session", targetConvId] });
      } catch {
        // non-fatal
      }
    }
    setBehaviorSaved(true);
    setTimeout(() => setBehaviorSaved(false), 2500);
  }

  const currentWorkspace = teammate?.bot.homePath ?? "";
  const [draftWorkspace, setDraftWorkspace] = useState(currentWorkspace);
  const [workspaceSaved, setWorkspaceSaved] = useState(false);

  async function handleSaveWorkspace() {
    const ws = draftWorkspace.trim();
    if (!ws) return;
    if (teammate) {
      await updateBot(teammate.bot.id, { homePath: ws });
      await queryClient.invalidateQueries({ queryKey: ["teammates"] });
    }
    setWorkspaceSaved(true);
    setTimeout(() => setWorkspaceSaved(false), 2500);
  }

  useEffect(() => {
    if (open) {
      if (isDebby) {
        const savedClaude =
          sessionClaudeModel ||
          (typeof localStorage !== "undefined"
            ? localStorage.getItem("agentnexus.debby.claude_partner_model")
            : null) ||
          "default";
        const savedGpt =
          sessionGptModel ||
          (typeof localStorage !== "undefined"
            ? localStorage.getItem("agentnexus.debby.gpt_partner_model")
            : null) ||
          "default";
        setClaudePartnerModel(savedClaude || "default");
        setGptPartnerModel(savedGpt || "default");
        setDebbySaved(false);
      }
      if (isPolly) {
        const isRouting =
          session?.labels?.["agentnexus.routing.subagents"] === "on" ||
          subagentRoutingOverride === "on";
        setPollyRoutingOn(isRouting);
        setPollySaved(false);

        const savedClaude =
          sessionPollyClaudeModel ||
          (typeof localStorage !== "undefined"
            ? localStorage.getItem("agentnexus.polly.claude_model")
            : null) ||
          "default";
        const savedCodex =
          sessionPollyCodexModel ||
          (typeof localStorage !== "undefined"
            ? localStorage.getItem("agentnexus.polly.codex_model")
            : null) ||
          "default";
        const savedCodebuddy =
          sessionPollyCodebuddyModel ||
          (typeof localStorage !== "undefined"
            ? localStorage.getItem("agentnexus.polly.codebuddy_model")
            : null) ||
          "default";
        setPollyClaudeModel(savedClaude || "default");
        setPollyCodexModel(savedCodex || "default");
        setPollyCodebuddyModel(savedCodebuddy || "default");
        setPollySubagentsSaved(false);
      }
      const savedBehavior = teammate?.bot.behaviorMode || sessionBehaviorMode || "off";
      setBehaviorMode(savedBehavior);
      setBehaviorSaved(false);
      const ws = teammate?.bot.homePath ?? "";
      setDraftWorkspace(ws);
      setWorkspaceSaved(false);
    }
  }, [
    open,
    isDebby,
    isPolly,
    sessionClaudeModel,
    sessionGptModel,
    sessionPollyClaudeModel,
    sessionPollyCodexModel,
    sessionPollyCodebuddyModel,
    sessionBehaviorMode,
    session?.labels,
    subagentRoutingOverride,
    teammate?.bot.behaviorMode,
    teammate?.bot.homePath,
  ]);

  async function handleSaveDebbyPartners() {
    if (typeof localStorage !== "undefined") {
      localStorage.setItem("agentnexus.debby.claude_partner_model", claudePartnerModel);
      localStorage.setItem("agentnexus.debby.gpt_partner_model", gptPartnerModel);
    }
    if (targetConvId) {
      try {
        await updateSession(targetConvId, {
          labels: {
            "subagent.model.claude": claudePartnerModel === "default" ? "" : claudePartnerModel,
            "subagent.model.gpt": gptPartnerModel === "default" ? "" : gptPartnerModel,
          },
          silent: true,
        });
        await queryClient.invalidateQueries({ queryKey: ["session", targetConvId] });
      } catch {
        // non-fatal
      }
    }
    setDebbySaved(true);
    setTimeout(() => setDebbySaved(false), 2500);
  }

  async function handleSavePollyRouting(checked: boolean) {
    setPollyRoutingOn(checked);
    const store = useChatStore.getState();
    await store.setSubagentRouting(checked ? "on" : "off");
    if (targetConvId) {
      try {
        await updateSession(targetConvId, {
          labels: {
            "agentnexus.routing.subagents": checked ? "on" : "off",
          },
          silent: true,
        });
      } catch {
        // non-fatal
      }
    }
    setPollySaved(true);
    setTimeout(() => setPollySaved(false), 2500);
  }

  async function handleSavePollySubagents() {
    if (typeof localStorage !== "undefined") {
      localStorage.setItem("agentnexus.polly.claude_model", pollyClaudeModel);
      localStorage.setItem("agentnexus.polly.codex_model", pollyCodexModel);
      localStorage.setItem("agentnexus.polly.codebuddy_model", pollyCodebuddyModel);
    }
    if (targetConvId) {
      try {
        await updateSession(targetConvId, {
          labels: {
            "subagent.model.claude_code": pollyClaudeModel === "default" ? "" : pollyClaudeModel,
            "subagent.model.codex": pollyCodexModel === "default" ? "" : pollyCodexModel,
            "subagent.model.codebuddy":
              pollyCodebuddyModel === "default" ? "" : pollyCodebuddyModel,
          },
          silent: true,
        });
        await queryClient.invalidateQueries({ queryKey: ["session", targetConvId] });
      } catch {
        // non-fatal
      }
    }
    setPollySubagentsSaved(true);
    setTimeout(() => setPollySubagentsSaved(false), 2500);
  }

  useEffect(() => {
    if (open) {
      setTab(initialTab);
      setMemoryDraft("");
      setEditingMemoryId(null);
      setMemoryError(null);
    }
  }, [open, initialTab, agentId]);

  function startEditMemory(memoryId: string, content: string) {
    setEditingMemoryId(memoryId);
    setMemoryDraft(content);
    setMemoryError(null);
  }

  function cancelEditMemory() {
    setEditingMemoryId(null);
    setMemoryDraft("");
    setMemoryError(null);
  }

  async function handleSaveMemory() {
    const content = memoryDraft.trim();
    if (!content || agentId === "") return;
    setMemoryError(null);
    try {
      if (editingMemoryId === null) {
        await createMemoryMutation.mutateAsync(content);
      } else {
        await updateMemoryMutation.mutateAsync({ memoryId: editingMemoryId, content });
      }
      cancelEditMemory();
    } catch {
      setMemoryError("Couldn't save that memory.");
    }
  }

  if (!teammate || !agent) return null;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="flex max-h-[88vh] w-full flex-col sm:max-w-2xl p-0 gap-0 overflow-hidden">
        <DialogHeader className="px-6 pt-5 pb-4 border-b border-border/60">
          <div className="flex items-center justify-between gap-3">
            <div className="flex items-center gap-3">
              <div className="relative flex size-10 shrink-0 items-center justify-center rounded-lg bg-primary/10 text-primary font-semibold">
                <BotIcon className="size-5" />
                <span
                  className="absolute -bottom-0.5 -right-0.5 size-2.5 rounded-full border-2 border-background bg-emerald-500"
                  title="Ready"
                />
              </div>
              <div className="flex flex-col">
                <DialogTitle className="text-lg font-semibold flex items-center gap-2">
                  {agent.name}
                  {agent.harness && (
                    <span className="rounded bg-muted px-2 py-0.5 text-xs font-mono font-medium text-muted-foreground">
                      {agent.harness}
                    </span>
                  )}
                </DialogTitle>
                <p className="text-xs text-muted-foreground mt-0.5 line-clamp-1">
                  {agent.description ?? "Persistent teammate bot"}
                </p>
              </div>
            </div>
          </div>
        </DialogHeader>

        <Tabs value={tab} onValueChange={setTab} className="flex flex-1 flex-col overflow-hidden">
          <div className="px-6 pt-3 pb-2 border-b border-border/40 bg-muted/20">
            <TabsList variant="line" className="gap-2">
              <TabsTrigger value="profile" className="gap-1.5 py-1.5 text-xs">
                <BotIcon className="size-3.5" />
                Profile
              </TabsTrigger>
              <TabsTrigger value="model" className="gap-1.5 py-1.5 text-xs">
                <CpuIcon className="size-3.5" />
                Model & Engine
              </TabsTrigger>
              <TabsTrigger
                value="memories"
                className="gap-1.5 py-1.5 text-xs"
                data-testid="settings-tab-memories"
              >
                <BrainIcon className="size-3.5" />
                Memories
                {(memories ?? []).length > 0 && (
                  <span className="ml-1 rounded-full bg-primary/15 px-1.5 py-0.2 text-[10px] font-semibold text-primary">
                    {memories?.length}
                  </span>
                )}
              </TabsTrigger>
              <TabsTrigger
                value="routines"
                className="gap-1.5 py-1.5 text-xs"
                data-testid="settings-tab-routines"
              >
                <ClockIcon className="size-3.5" />
                Routines
                {teammate.routineCount > 0 && (
                  <span className="ml-1 rounded-full bg-muted px-1.5 py-0.2 text-[10px] font-semibold">
                    {teammate.routineCount}
                  </span>
                )}
              </TabsTrigger>
              <TabsTrigger value="skills" className="gap-1.5 py-1.5 text-xs">
                <WrenchIcon className="size-3.5" />
                Tools & Skills
              </TabsTrigger>
            </TabsList>
          </div>

          <div className="flex-1 overflow-y-auto px-6 py-4">
            {/* 1. Profile Tab */}
            <TabsContent value="profile" className="space-y-4 m-0">
              <div className="space-y-1">
                <h3 className="text-sm font-medium">About this Teammate</h3>
                <p className="text-xs text-muted-foreground">
                  Persistent identity and role definition in the AgentNexus workspace.
                </p>
              </div>

              <div className="rounded-lg border border-border/60 bg-muted/10 p-3.5 space-y-3">
                <div className="grid grid-cols-2 gap-3 text-xs">
                  <div>
                    <span className="text-muted-foreground block">Bot Identifier:</span>
                    <span className="font-mono text-foreground">{agent.name}</span>
                  </div>
                  <div>
                    <span className="text-muted-foreground block">Durable ID:</span>
                    <span className="font-mono text-foreground truncate block" title={agent.id}>
                      {agent.id}
                    </span>
                  </div>
                  <div>
                    <span className="text-muted-foreground block">Runtime Engine:</span>
                    <span className="font-medium text-foreground">
                      {agent.harness ?? "default"}
                    </span>
                  </div>
                  <div>
                    <span className="text-muted-foreground block">Builtin Status:</span>
                    <span className="text-foreground">
                      {agent.builtin ? "System template" : "Custom bot"}
                    </span>
                  </div>
                </div>

                <div className="border-t border-border/40 pt-2.5 space-y-1.5">
                  <div className="flex items-center justify-between text-xs">
                    <span className="text-muted-foreground font-medium">Bot Home:</span>
                    {workspaceSaved && (
                      <span className="text-[11px] text-emerald-500 font-medium flex items-center gap-1">
                        <CheckIcon className="size-3" />
                        Saved
                      </span>
                    )}
                  </div>
                  <div className="flex items-center gap-2">
                    <input
                      type="text"
                      value={draftWorkspace}
                      onChange={(e) => {
                        setDraftWorkspace(e.target.value);
                        setWorkspaceSaved(false);
                      }}
                      placeholder="Absolute Bot Home path"
                      className="flex-1 rounded border border-border/60 bg-background px-2.5 py-1 text-xs font-mono text-foreground focus-visible:outline-none focus-visible:border-ring"
                    />
                    <Button
                      size="sm"
                      variant="outline"
                      onClick={() => void handleSaveWorkspace()}
                      disabled={!draftWorkspace.trim()}
                      className="h-7 text-xs shrink-0"
                    >
                      <SaveIcon className="size-3 mr-1" />
                      Save Bot Home
                    </Button>
                  </div>
                </div>

                {agent.description && (
                  <div className="border-t border-border/40 pt-2.5">
                    <span className="text-xs text-muted-foreground block mb-1">
                      Role Description:
                    </span>
                    <p className="text-xs leading-relaxed text-foreground bg-background rounded p-2 border border-border/40">
                      {agent.description}
                    </p>
                  </div>
                )}
              </div>
            </TabsContent>

            {/* 2. Model & Engine Tab */}
            <TabsContent value="model" className="space-y-4 m-0">
              <div className="space-y-1">
                <h3 className="text-sm font-medium">Model & Execution Base</h3>
                <p className="text-xs text-muted-foreground">
                  Harness execution runtime and inference options for this bot.
                </p>
              </div>

              <div className="rounded-lg border border-border/60 bg-muted/10 p-3.5 space-y-3">
                <div className="flex items-center justify-between text-xs pb-2 border-b border-border/40">
                  <span className="font-medium text-foreground">Harness Engine:</span>
                  <span className="font-mono bg-background px-2 py-0.5 rounded border border-border/50 text-foreground">
                    {agent.harness ?? "standard"}
                  </span>
                </div>

                {isDebby ? (
                  <div className="space-y-3">
                    <div className="space-y-1.5 text-xs">
                      <p className="font-medium text-foreground">Two-Headed Debate Architecture:</p>
                      <p className="text-muted-foreground text-xs leading-relaxed">
                        Debby runs two parallel sub-agents (Claude partner & GPT partner) that
                        critique and synthesize perspectives before responding.
                      </p>
                    </div>

                    <div className="space-y-3 rounded-lg border border-border/60 bg-background p-3.5">
                      <div className="flex items-center justify-between">
                        <span className="font-semibold text-xs text-foreground flex items-center gap-1.5">
                          <CpuIcon className="size-3.5 text-primary" />
                          Sub-Agent Partners Configuration (子 Agent 辩手配置)
                        </span>
                        {debbySaved && (
                          <span className="text-[11px] text-emerald-500 font-medium flex items-center gap-1">
                            <CheckIcon className="size-3" />
                            Saved
                          </span>
                        )}
                      </div>
                      <p className="text-[11px] text-muted-foreground leading-relaxed">
                        配置辩论所调用的两个子 Agent
                        具体大模型。选择后将自动应用至当前及后续对话中：
                      </p>

                      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 pt-1">
                        {/* Claude Partner Model */}
                        <div className="space-y-1.5">
                          <label className="text-xs font-medium text-foreground block">
                            Claude Partner Sub-Agent:
                          </label>
                          <Select
                            value={claudePartnerModel}
                            onValueChange={(val) => {
                              setClaudePartnerModel(val);
                              setDebbySaved(false);
                            }}
                          >
                            <SelectTrigger
                              className="w-full text-xs h-8"
                              data-testid="select-claude-partner"
                            >
                              <SelectValue placeholder="Provider Default" />
                            </SelectTrigger>
                            <SelectContent position="popper">
                              <SelectItem value="default" className="text-xs">
                                Provider Default
                              </SelectItem>
                              {claudePartnerModel &&
                                claudePartnerModel !== "default" &&
                                !claudePartnerOptions.some(
                                  (opt) => opt.id === claudePartnerModel,
                                ) && (
                                  <SelectItem
                                    value={claudePartnerModel}
                                    className="text-xs font-mono"
                                  >
                                    {claudePartnerModel} (Current)
                                  </SelectItem>
                                )}
                              {claudePartnerOptions.map((opt) => (
                                <SelectItem
                                  key={opt.id}
                                  value={opt.id}
                                  className="text-xs font-mono"
                                >
                                  {opt.displayName ?? opt.id}
                                </SelectItem>
                              ))}
                            </SelectContent>
                          </Select>
                          <span className="text-[10px] text-muted-foreground block">
                            辩方 A：Claude 批判与审视子 Agent
                          </span>
                        </div>

                        {/* GPT Partner Model */}
                        <div className="space-y-1.5">
                          <label className="text-xs font-medium text-foreground block">
                            GPT / Codex Partner Sub-Agent:
                          </label>
                          <Select
                            value={gptPartnerModel}
                            onValueChange={(val) => {
                              setGptPartnerModel(val);
                              setDebbySaved(false);
                            }}
                          >
                            <SelectTrigger
                              className="w-full text-xs h-8"
                              data-testid="select-gpt-partner"
                            >
                              <SelectValue placeholder="Provider Default" />
                            </SelectTrigger>
                            <SelectContent position="popper">
                              <SelectItem value="default" className="text-xs">
                                Provider Default
                              </SelectItem>
                              {gptPartnerModel &&
                                gptPartnerModel !== "default" &&
                                !gptPartnerOptions.some((opt) => opt.id === gptPartnerModel) && (
                                  <SelectItem value={gptPartnerModel} className="text-xs font-mono">
                                    {gptPartnerModel} (Current)
                                  </SelectItem>
                                )}
                              {gptPartnerOptions.map((opt) => (
                                <SelectItem
                                  key={opt.id}
                                  value={opt.id}
                                  className="text-xs font-mono"
                                >
                                  {opt.displayName ?? opt.id}
                                </SelectItem>
                              ))}
                            </SelectContent>
                          </Select>
                          <span className="text-[10px] text-muted-foreground block">
                            辩方 B：GPT/Codex 对立视角与对辩子 Agent
                          </span>
                        </div>
                      </div>

                      <div className="flex justify-end pt-1">
                        <Button
                          size="sm"
                          onClick={() => void handleSaveDebbyPartners()}
                          data-testid="save-debby-partners"
                          className="h-7 text-xs"
                        >
                          <SaveIcon className="size-3 mr-1" />
                          Save Sub-Agent Models
                        </Button>
                      </div>
                    </div>

                    {/* Primary LLM Model for Debby */}
                    <div className="space-y-1.5 pt-2 border-t border-border/40">
                      <div className="flex items-center justify-between">
                        <label className="text-xs font-medium text-foreground block">
                          Primary Orchestrator Model (主编排模型):
                        </label>
                        {modelSaved && (
                          <span className="text-[11px] text-emerald-500 font-medium flex items-center gap-1">
                            <CheckIcon className="size-3" />
                            Applied
                          </span>
                        )}
                      </div>
                      <div className="flex items-center gap-2">
                        <Select
                          value={selectedBotModel}
                          onValueChange={(val) => {
                            setSelectedBotModel(val);
                            setModelSaved(false);
                          }}
                        >
                          <SelectTrigger
                            className="w-full text-xs h-8"
                            data-testid="select-debby-model"
                          >
                            <SelectValue placeholder="Provider Default" />
                          </SelectTrigger>
                          <SelectContent position="popper">
                            <SelectItem value="default" className="text-xs">
                              Provider Default
                            </SelectItem>
                            {modelOptions.map((opt) => (
                              <SelectItem key={opt.id} value={opt.id} className="text-xs font-mono">
                                {opt.displayName ?? opt.id}
                              </SelectItem>
                            ))}
                          </SelectContent>
                        </Select>
                        <Button
                          size="sm"
                          onClick={() => void handleSaveBotModel()}
                          data-testid="save-debby-model"
                          className="h-8 text-xs shrink-0"
                        >
                          <SaveIcon className="size-3 mr-1" />
                          Set Model
                        </Button>
                      </div>
                    </div>
                  </div>
                ) : isPolly ? (
                  <div className="space-y-3">
                    <div className="space-y-1.5 text-xs">
                      <p className="font-medium text-foreground">Multi-Agent Orchestrator:</p>
                      <p className="text-muted-foreground text-xs leading-relaxed">
                        Polly breaks high-level goals into sub-agent worktrees and coordinates
                        planning, implementation, and cross-verification.
                      </p>
                    </div>

                    <div className="space-y-3 rounded-lg border border-border/60 bg-background p-3.5">
                      <div className="flex items-center justify-between">
                        <span className="font-semibold text-xs text-foreground flex items-center gap-1.5">
                          <CpuIcon className="size-3.5 text-primary" />
                          Sub-Agent Orchestration (子 Agent 协同阵列)
                        </span>
                        {pollySaved && (
                          <span className="text-[11px] text-emerald-500 font-medium flex items-center gap-1">
                            <CheckIcon className="size-3" />
                            Saved
                          </span>
                        )}
                      </div>

                      <div className="flex items-center justify-between gap-3 rounded border border-border/40 p-2.5 bg-muted/20">
                        <div className="space-y-0.5">
                          <span className="text-xs font-medium text-foreground block">
                            Sub-Agent Smart Routing (智能分流)
                          </span>
                          <span className="text-[10px] text-muted-foreground block">
                            自动为每个子 Agent 任务动态分配性价比最佳的模型
                          </span>
                        </div>
                        <Switch
                          checked={pollyRoutingOn}
                          onCheckedChange={(checked) => void handleSavePollyRouting(checked)}
                          data-testid="switch-polly-routing"
                        />
                      </div>

                      {/* Sub-Agent Model Customization */}
                      <div className="space-y-3 pt-2 border-t border-border/40">
                        <div className="flex items-center justify-between">
                          <span className="text-xs font-semibold text-foreground flex items-center gap-1.5">
                            <CpuIcon className="size-3.5 text-primary" />
                            Sub-Agent Model Customization (子 Agent 模型独立配置)
                          </span>
                          {pollySubagentsSaved && (
                            <span className="text-[11px] text-emerald-500 font-medium flex items-center gap-1">
                              <CheckIcon className="size-3" />
                              Saved
                            </span>
                          )}
                        </div>
                        <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
                          {/* Claude Code Worker Model */}
                          <div className="space-y-1.5">
                            <label className="text-[11px] font-medium text-foreground block">
                              Claude Code (Anthropic):
                            </label>
                            <Select
                              value={pollyClaudeModel}
                              onValueChange={(val) => {
                                setPollyClaudeModel(val);
                                setPollySubagentsSaved(false);
                              }}
                            >
                              <SelectTrigger
                                className="w-full text-xs h-8"
                                data-testid="select-polly-claude"
                              >
                                <SelectValue placeholder="Default (claude-sonnet-4-6)" />
                              </SelectTrigger>
                              <SelectContent position="popper">
                                <SelectItem value="default" className="text-xs">
                                  Default (claude-sonnet-4-6)
                                </SelectItem>
                                {pollyClaudeModel &&
                                  pollyClaudeModel !== "default" &&
                                  !claudePartnerOptions.some(
                                    (opt) => opt.id === pollyClaudeModel,
                                  ) && (
                                    <SelectItem
                                      value={pollyClaudeModel}
                                      className="text-xs font-mono"
                                    >
                                      {pollyClaudeModel} (Current)
                                    </SelectItem>
                                  )}
                                {claudePartnerOptions.map((opt) => (
                                  <SelectItem
                                    key={opt.id}
                                    value={opt.id}
                                    className="text-xs font-mono"
                                  >
                                    {opt.displayName ?? opt.id}
                                  </SelectItem>
                                ))}
                              </SelectContent>
                            </Select>
                          </div>

                          {/* Codex Worker Model */}
                          <div className="space-y-1.5">
                            <label className="text-[11px] font-medium text-foreground block">
                              Codex (OpenAI):
                            </label>
                            <Select
                              value={pollyCodexModel}
                              onValueChange={(val) => {
                                setPollyCodexModel(val);
                                setPollySubagentsSaved(false);
                              }}
                            >
                              <SelectTrigger
                                className="w-full text-xs h-8"
                                data-testid="select-polly-codex"
                              >
                                <SelectValue placeholder="Default (gpt-5.4)" />
                              </SelectTrigger>
                              <SelectContent position="popper">
                                <SelectItem value="default" className="text-xs">
                                  Default (gpt-5.4)
                                </SelectItem>
                                {pollyCodexModel &&
                                  pollyCodexModel !== "default" &&
                                  !gptPartnerOptions.some((opt) => opt.id === pollyCodexModel) && (
                                    <SelectItem
                                      value={pollyCodexModel}
                                      className="text-xs font-mono"
                                    >
                                      {pollyCodexModel} (Current)
                                    </SelectItem>
                                  )}
                                {gptPartnerOptions.map((opt) => (
                                  <SelectItem
                                    key={opt.id}
                                    value={opt.id}
                                    className="text-xs font-mono"
                                  >
                                    {opt.displayName ?? opt.id}
                                  </SelectItem>
                                ))}
                              </SelectContent>
                            </Select>
                          </div>

                          {/* CodeBuddy Worker Model */}
                          <div className="space-y-1.5">
                            <label className="text-[11px] font-medium text-foreground block">
                              CodeBuddy (Tencent):
                            </label>
                            <Select
                              value={pollyCodebuddyModel}
                              onValueChange={(val) => {
                                setPollyCodebuddyModel(val);
                                setPollySubagentsSaved(false);
                              }}
                            >
                              <SelectTrigger
                                className="w-full text-xs h-8"
                                data-testid="select-polly-codebuddy"
                              >
                                <SelectValue placeholder="Default (hy4-preview)" />
                              </SelectTrigger>
                              <SelectContent position="popper">
                                <SelectItem value="default" className="text-xs">
                                  Default (hy4-preview)
                                </SelectItem>
                                {codebuddyOptions.map((opt) => (
                                  <SelectItem
                                    key={opt.id}
                                    value={opt.id}
                                    className="text-xs font-mono"
                                  >
                                    {opt.displayName ?? opt.id}
                                  </SelectItem>
                                ))}
                              </SelectContent>
                            </Select>
                          </div>
                        </div>

                        <div className="flex justify-end pt-1">
                          <Button
                            size="sm"
                            onClick={() => void handleSavePollySubagents()}
                            data-testid="save-polly-subagents"
                            className="h-7 text-xs"
                          >
                            <SaveIcon className="size-3 mr-1" />
                            Save Sub-Agent Models
                          </Button>
                        </div>
                      </div>

                      <div className="space-y-1.5 pt-1">
                        <span className="text-[11px] font-medium text-foreground block">
                          Active Sub-Agent Roles:
                        </span>
                        <div className="grid grid-cols-1 sm:grid-cols-3 gap-2">
                          <div className="rounded bg-muted/30 p-2 border border-border/40 text-[11px]">
                            <span className="font-medium text-foreground block">Investigate</span>
                            <span className="text-[10px] text-muted-foreground">
                              只读代码诊断与探查
                            </span>
                          </div>
                          <div className="rounded bg-muted/30 p-2 border border-border/40 text-[11px]">
                            <span className="font-medium text-foreground block">Implement</span>
                            <span className="text-[10px] text-muted-foreground">
                              工作区分支代码改动
                            </span>
                          </div>
                          <div className="rounded bg-muted/30 p-2 border border-border/40 text-[11px]">
                            <span className="font-medium text-foreground block">Cross-Review</span>
                            <span className="text-[10px] text-muted-foreground">
                              跨模型多维度独立审计
                            </span>
                          </div>
                        </div>
                      </div>

                      {/* Primary LLM Model for Polly */}
                      <div className="space-y-1.5 pt-2 border-t border-border/40">
                        <div className="flex items-center justify-between">
                          <label className="text-xs font-medium text-foreground block">
                            Primary Orchestrator Model (编排主模型):
                          </label>
                          {modelSaved && (
                            <span className="text-[11px] text-emerald-500 font-medium flex items-center gap-1">
                              <CheckIcon className="size-3" />
                              Applied
                            </span>
                          )}
                        </div>
                        <div className="flex items-center gap-2">
                          <Select
                            value={selectedBotModel}
                            onValueChange={(val) => {
                              setSelectedBotModel(val);
                              setModelSaved(false);
                            }}
                          >
                            <SelectTrigger
                              className="w-full text-xs h-8"
                              data-testid="select-polly-model"
                            >
                              <SelectValue placeholder="Provider Default" />
                            </SelectTrigger>
                            <SelectContent position="popper">
                              <SelectItem value="default" className="text-xs">
                                Provider Default
                              </SelectItem>
                              {modelOptions.map((opt) => (
                                <SelectItem
                                  key={opt.id}
                                  value={opt.id}
                                  className="text-xs font-mono"
                                >
                                  {opt.displayName ?? opt.id}
                                </SelectItem>
                              ))}
                            </SelectContent>
                          </Select>
                          <Button
                            size="sm"
                            onClick={() => void handleSaveBotModel()}
                            data-testid="save-polly-model"
                            className="h-8 text-xs shrink-0"
                          >
                            <SaveIcon className="size-3 mr-1" />
                            Set Model
                          </Button>
                        </div>
                      </div>
                    </div>
                  </div>
                ) : (
                  <div className="space-y-3 rounded-lg border border-border/60 bg-background p-3.5">
                    <div className="flex items-center justify-between">
                      <label className="text-xs font-medium text-foreground block">
                        Primary Bot Model:
                      </label>
                      {modelSaved && (
                        <span className="text-[11px] text-emerald-500 font-medium flex items-center gap-1">
                          <CheckIcon className="size-3" />
                          Applied
                        </span>
                      )}
                    </div>
                    <div className="flex items-center gap-2">
                      <Select
                        value={selectedBotModel}
                        onValueChange={(val) => {
                          setSelectedBotModel(val);
                          setModelSaved(false);
                        }}
                      >
                        <SelectTrigger className="w-full text-xs h-8">
                          <SelectValue placeholder="Provider Default" />
                        </SelectTrigger>
                        <SelectContent position="popper">
                          <SelectItem value="default" className="text-xs">
                            Provider Default
                          </SelectItem>
                          {selectedBotModel &&
                            selectedBotModel !== "default" &&
                            !modelOptions.some((opt) => opt.id === selectedBotModel) && (
                              <SelectItem value={selectedBotModel} className="text-xs font-mono">
                                {selectedBotModel} (Current)
                              </SelectItem>
                            )}
                          {modelOptions.map((opt) => (
                            <SelectItem key={opt.id} value={opt.id} className="text-xs font-mono">
                              {opt.displayName ?? opt.id}
                            </SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                      <Button
                        size="sm"
                        onClick={() => void handleSaveBotModel()}
                        className="h-8 text-xs shrink-0"
                      >
                        <SaveIcon className="size-3 mr-1" />
                        Set Model
                      </Button>
                    </div>
                  </div>
                )}

                {/* Behavior Pack (Lean Engineering) */}
                <div className="space-y-3 rounded-lg border border-border/60 bg-background p-3.5">
                  <div className="flex items-center justify-between">
                    <span className="font-semibold text-xs text-foreground flex items-center gap-1.5">
                      <ListChecksIcon className="size-3.5 text-primary" />
                      Behavior Pack (Lean Engineering)
                    </span>
                    {behaviorSaved && (
                      <span className="text-[11px] text-emerald-500 font-medium flex items-center gap-1">
                        <CheckIcon className="size-3" />
                        Applied
                      </span>
                    )}
                  </div>
                  <p className="text-[11px] text-muted-foreground leading-relaxed">
                    控制该 Bot 的工程自律阶梯。Lean / Strict 模式可大幅精简代码
                    diff，避免投机性抽象与过度重构：
                  </p>

                  <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 pt-1">
                    <div className="space-y-1">
                      <label className="text-xs font-medium text-foreground block">
                        Behavior Mode:
                      </label>
                      <Select
                        value={behaviorMode}
                        onValueChange={(val) => void handleSaveBehaviorMode(val)}
                      >
                        <SelectTrigger
                          className="w-full text-xs h-8"
                          data-testid="select-behavior-mode"
                        >
                          <SelectValue placeholder="Off (默认原生)" />
                        </SelectTrigger>
                        <SelectContent position="popper">
                          <SelectItem value="off" className="text-xs">
                            Off (关闭干预)
                          </SelectItem>
                          <SelectItem value="advisory" className="text-xs">
                            Advisory (建议精简方案)
                          </SelectItem>
                          <SelectItem value="lean" className="text-xs">
                            Lean (精益工程)
                          </SelectItem>
                          <SelectItem value="strict" className="text-xs">
                            Strict (严格限制代码扩散)
                          </SelectItem>
                        </SelectContent>
                      </Select>
                    </div>

                    <div className="rounded border border-border/40 p-2 bg-muted/20 text-[10px] text-muted-foreground flex flex-col justify-center">
                      <span className="font-semibold text-foreground mb-0.5">
                        {behaviorMode === "lean"
                          ? "Lean: 最小正确实施阶梯，优先复用现有库，杜绝过度设计。"
                          : behaviorMode === "strict"
                            ? "Strict: 严控重构与文件修改范围，只做经授权的最小变更。"
                            : behaviorMode === "advisory"
                              ? "Advisory: 保持方案完整性，并在完成后提示更轻量的替代路径。"
                              : "Off: 遵循模型原生自由发挥，不附加自律指令。"}
                      </span>
                      <span>已联动至会话每轮 Prompt Composer 动态生效。</span>
                    </div>
                  </div>
                </div>

                {modelOptions.length > 0 && (
                  <div className="border-t border-border/40 pt-2.5 space-y-1.5">
                    <span className="text-xs font-medium text-foreground block">
                      Available Host Models:
                    </span>
                    <div className="flex flex-wrap gap-1.5">
                      {modelOptions.slice(0, 8).map((opt) => (
                        <span
                          key={opt.id}
                          className="text-[11px] font-mono px-2 py-0.5 rounded bg-background border border-border/50 text-foreground"
                        >
                          {opt.displayName ?? opt.id}
                        </span>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            </TabsContent>

            {/* 3. Memories Tab */}
            <TabsContent value="memories" className="space-y-4 m-0">
              <div className="flex items-center justify-between">
                <div className="space-y-0.5">
                  <h3 className="text-sm font-medium">Long-Term Memory (Supermemory)</h3>
                  <p className="text-xs text-muted-foreground">
                    Persistent preferences and project facts automatically loaded into this bot's
                    context.
                  </p>
                </div>
              </div>

              {memoryError && <p className="text-xs text-destructive">{memoryError}</p>}

              <div className="space-y-2.5 max-h-[360px] overflow-y-auto pr-1">
                {memoriesError ? (
                  <div className="flex items-center gap-2 rounded-lg border border-destructive/30 bg-destructive/5 px-3 py-2 text-xs">
                    <TriangleAlertIcon className="size-4 shrink-0 text-destructive" />
                    <span>Couldn't load memories.</span>
                  </div>
                ) : memoriesLoading ? (
                  <div className="flex items-center justify-center gap-2 py-8 text-xs text-muted-foreground">
                    <Loader2Icon className="size-4 animate-spin" />
                    Loading memories…
                  </div>
                ) : (memories ?? []).length === 0 ? (
                  <div className="rounded-lg border border-dashed border-border/60 py-8 text-center text-xs text-muted-foreground">
                    No memories recorded yet. Add memories below to guide this bot across
                    conversations.
                  </div>
                ) : (
                  (memories ?? []).map((memory) => (
                    <div
                      key={memory.id}
                      data-testid={`memory-${memory.id}`}
                      className="rounded-lg border border-border/60 bg-background p-3 transition-colors hover:border-border"
                    >
                      {editingMemoryId === memory.id ? (
                        <Textarea
                          value={memoryDraft}
                          onChange={(event) => setMemoryDraft(event.currentTarget.value)}
                          maxLength={20_000}
                          className="min-h-20 text-xs"
                          data-testid="memory-draft"
                        />
                      ) : (
                        <p className="text-xs leading-relaxed whitespace-pre-wrap text-foreground">
                          {memory.content}
                        </p>
                      )}
                      <div className="mt-2 flex items-center justify-between gap-2 border-t border-border/30 pt-2 text-[11px] text-muted-foreground">
                        <span>{new Date(memory.createdAt * 1000).toLocaleString()}</span>
                        <div className="flex items-center gap-1">
                          {editingMemoryId === memory.id ? (
                            <>
                              <Button variant="ghost" size="sm" onClick={cancelEditMemory}>
                                <XIcon className="size-3" />
                                Cancel
                              </Button>
                              <Button
                                variant="outline"
                                size="sm"
                                onClick={() => void handleSaveMemory()}
                                disabled={
                                  updateMemoryMutation.isPending || memoryDraft.trim() === ""
                                }
                              >
                                <SaveIcon className="size-3" />
                                Save
                              </Button>
                            </>
                          ) : (
                            <>
                              <Button
                                variant="ghost"
                                size="icon-xs"
                                aria-label={`Edit memory ${memory.id}`}
                                title="Edit"
                                onClick={() => startEditMemory(memory.id, memory.content)}
                                data-testid={`edit-memory-${memory.id}`}
                              >
                                <PencilIcon className="size-3" />
                              </Button>
                              <Button
                                variant="ghost"
                                size="icon-xs"
                                aria-label={`Delete memory ${memory.id}`}
                                title="Delete"
                                disabled={deleteMemoryMutation.isPending}
                                onClick={() => deleteMemoryMutation.mutate(memory.id)}
                                data-testid={`delete-memory-${memory.id}`}
                              >
                                <Trash2Icon className="size-3" />
                              </Button>
                            </>
                          )}
                        </div>
                      </div>
                    </div>
                  ))
                )}
              </div>

              {editingMemoryId === null && (
                <div className="space-y-2 border-t border-border/60 pt-3">
                  <Textarea
                    value={memoryDraft}
                    onChange={(event) => setMemoryDraft(event.currentTarget.value)}
                    placeholder="Add a working preference or key project context for this bot..."
                    maxLength={20_000}
                    className="min-h-18 text-xs"
                    data-testid="memory-composer"
                  />
                  <div className="flex justify-end">
                    <Button
                      size="sm"
                      onClick={() => void handleSaveMemory()}
                      disabled={createMemoryMutation.isPending || memoryDraft.trim() === ""}
                      data-testid="save-new-memory"
                    >
                      <SaveIcon className="size-3.5" />
                      Save Memory
                    </Button>
                  </div>
                </div>
              )}
            </TabsContent>

            {/* 4. Routines Tab */}
            <TabsContent value="routines" className="space-y-4 m-0">
              <div className="flex items-center justify-between">
                <div className="space-y-0.5">
                  <h3 className="text-sm font-medium">Scheduled Routines</h3>
                  <p className="text-xs text-muted-foreground">
                    Automated background jobs assigned to this teammate.
                  </p>
                </div>
                {onAddRoutine && (
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={() => {
                      onOpenChange(false);
                      onAddRoutine(agent.id);
                    }}
                    data-testid={`add-routine-${agent.name}`}
                  >
                    <PlusIcon className="size-3.5" />
                    New Routine
                  </Button>
                )}
              </div>

              <div className="space-y-2 max-h-[380px] overflow-y-auto">
                {teammate.routines.length === 0 ? (
                  <div className="rounded-lg border border-dashed border-border/60 py-8 text-center text-xs text-muted-foreground">
                    No recurring routines configured for this bot.
                  </div>
                ) : (
                  teammate.routines.map((routine) => (
                    <div
                      key={routine.id}
                      className="rounded-lg border border-border/60 bg-background p-3 flex items-center justify-between gap-3 text-xs"
                    >
                      <div className="min-w-0 flex-1 space-y-1">
                        <div className="flex items-center gap-2">
                          <span className="font-medium text-foreground">{routine.name}</span>
                          <span
                            className={`px-1.5 py-0.2 rounded text-[10px] font-mono ${
                              routine.state === "active"
                                ? "bg-emerald-500/10 text-emerald-600"
                                : "bg-muted text-muted-foreground"
                            }`}
                          >
                            {routine.state}
                          </span>
                        </div>
                        <p className="font-mono text-[11px] text-muted-foreground">
                          {routine.rrule}
                        </p>
                        {routine.lastRunAt && (
                          <p className="text-[11px] text-muted-foreground">
                            Last run: {new Date(routine.lastRunAt * 1000).toLocaleString()} (
                            <span className="font-medium text-foreground">
                              {routine.lastRunStatus ?? "unknown"}
                            </span>
                            )
                          </p>
                        )}
                      </div>

                      {onRunRoutine && (
                        <Button
                          size="sm"
                          variant="ghost"
                          disabled={runPendingId === routine.id}
                          onClick={() => onRunRoutine(routine.id)}
                          data-testid={`run-routine-${routine.id}`}
                          title="Run this routine right now"
                        >
                          {runPendingId === routine.id ? (
                            <Loader2Icon className="size-3.5 animate-spin" />
                          ) : (
                            <PlayIcon className="size-3.5" />
                          )}
                          Run
                        </Button>
                      )}
                    </div>
                  ))
                )}
              </div>
            </TabsContent>

            {/* 5. Tools & Skills Tab */}
            <TabsContent value="skills" className="space-y-4 m-0">
              <div className="space-y-1">
                <h3 className="text-sm font-medium">Tools & Skills Catalog</h3>
                <p className="text-xs text-muted-foreground">
                  Capabilities and execution tools available to this teammate during turns.
                </p>
              </div>

              <div className="space-y-3">
                <div className="rounded-lg border border-border/60 bg-muted/10 p-3 space-y-2">
                  <span className="text-xs font-medium text-foreground flex items-center gap-1.5">
                    <SparklesIcon className="size-3.5 text-primary" />
                    Bound Skills ({agent.skills.length})
                  </span>
                  {agent.skills.length === 0 ? (
                    <p className="text-xs text-muted-foreground">No custom skills declared.</p>
                  ) : (
                    <div className="grid grid-cols-1 gap-1.5 pt-1">
                      {agent.skills.map((skill) => (
                        <div
                          key={skill.name}
                          className="rounded bg-background p-2 border border-border/40 text-xs"
                        >
                          <span className="font-mono font-medium text-foreground">
                            {skill.name}
                          </span>
                          <p className="text-[11px] text-muted-foreground mt-0.5">
                            {skill.description}
                          </p>
                        </div>
                      ))}
                    </div>
                  )}
                </div>

                <div className="rounded-lg border border-border/60 bg-muted/10 p-3 space-y-2">
                  <span className="text-xs font-medium text-foreground flex items-center gap-1.5">
                    <TerminalIcon className="size-3.5" />
                    Terminal Support
                  </span>
                  <div className="flex flex-wrap gap-1.5">
                    {agent.terminals.length === 0 ? (
                      <span className="text-xs text-muted-foreground">None (SDK pure-chat)</span>
                    ) : (
                      agent.terminals.map((term) => (
                        <span
                          key={term}
                          className="text-[11px] font-mono bg-background px-2 py-0.5 rounded border border-border/40 text-foreground"
                        >
                          {term}
                        </span>
                      ))
                    )}
                  </div>
                </div>
              </div>
            </TabsContent>
          </div>
        </Tabs>
      </DialogContent>
    </Dialog>
  );
}
