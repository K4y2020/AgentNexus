import { useState } from "react";
import {
  AlertCircleIcon,
  CheckCircle2Icon,
  CheckIcon,
  CopyIcon,
  Edit2Icon,
  GlobeIcon,
  KeyRoundIcon,
  LayersIcon,
  Loader2Icon,
  PlusIcon,
  RadioIcon,
  RefreshCwIcon,
  ServerIcon,
  Trash2Icon,
  ZapIcon,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import {
  testGatewayConnection,
  useDeleteGateway,
  useGateways,
  useSaveGateway,
  useSetDefaultGateway,
  type GatewayItem,
  type GatewayPayload,
  type GatewayTestResult,
} from "@/hooks/useGateways";

export function GatewaysSection() {
  const { data: gateways = [], isLoading, refetch } = useGateways();
  const saveGateway = useSaveGateway();
  const deleteGateway = useDeleteGateway();
  const setDefaultGateway = useSetDefaultGateway();

  const [dialogOpen, setDialogOpen] = useState(false);
  const [editingGateway, setEditingGateway] = useState<GatewayItem | null>(null);

  // Form states
  const [formId, setFormId] = useState("");
  const [formFamily, setFormFamily] = useState<"anthropic" | "openai">("anthropic");
  const [formBaseUrl, setFormBaseUrl] = useState("");
  const [formApiKey, setFormApiKey] = useState("");
  const [formDefaultModel, setFormDefaultModel] = useState("");
  const [formIsDefault, setFormIsDefault] = useState(false);
  const [formWireApi, setFormWireApi] = useState<"responses" | "chat">("responses");

  // In-dialog test state
  const [dialogTesting, setDialogTesting] = useState(false);
  const [dialogTestResult, setDialogTestResult] = useState<GatewayTestResult | null>(null);

  // Per-item test states
  const [testingId, setTestingId] = useState<string | null>(null);
  const [testResults, setTestResults] = useState<Record<string, GatewayTestResult>>({});
  const [copiedId, setCopiedId] = useState<string | null>(null);

  function openCreateDialog(preset?: Partial<GatewayPayload>) {
    setEditingGateway(null);
    setFormId(preset?.id || "");
    setFormFamily(preset?.family || "anthropic");
    setFormBaseUrl(preset?.base_url || "");
    setFormApiKey(preset?.api_key || "");
    setFormDefaultModel(preset?.default_model || "");
    setFormIsDefault(preset?.is_default ?? false);
    setFormWireApi((preset?.wire_api as "responses" | "chat") || "responses");
    setDialogTestResult(null);
    setDialogOpen(true);
  }

  function openEditDialog(gw: GatewayItem) {
    setEditingGateway(gw);
    setFormId(gw.id);
    setFormFamily(gw.family === "openai" ? "openai" : "anthropic");
    setFormBaseUrl(gw.base_url);
    setFormApiKey("");
    setFormDefaultModel(gw.default_model || "");
    setFormIsDefault(gw.is_default);
    setFormWireApi((gw.wire_api as "responses" | "chat") || "responses");
    setDialogTestResult(null);
    setDialogOpen(true);
  }

  async function handleTestInDialog() {
    if (!formBaseUrl) return;
    setDialogTesting(true);
    setDialogTestResult(null);
    try {
      const res = await testGatewayConnection({
        base_url: formBaseUrl,
        family: formFamily,
        api_key: formApiKey || undefined,
      });
      setDialogTestResult(res);
      if (res.status === "ok" && res.models && res.models.length > 0 && !formDefaultModel) {
        const preferred =
          formFamily === "anthropic"
            ? res.models.find((m) => m.includes("claude-sonnet") || m.includes("claude")) || res.models[0]
            : res.models.find((m) => m.includes("gpt-5") || m.includes("gpt")) || res.models[0];
        setFormDefaultModel(preferred);
      }
    } catch (e: any) {
      setDialogTestResult({
        status: "error",
        latency_ms: 0,
        message: e?.message || "Test failed",
      });
    } finally {
      setDialogTesting(false);
    }
  }

  async function handleSave() {
    if (!formId || !formBaseUrl) return;
    await saveGateway.mutateAsync({
      id: formId,
      family: formFamily,
      base_url: formBaseUrl,
      api_key: formApiKey || undefined,
      is_default: formIsDefault,
      default_model: formDefaultModel || undefined,
      wire_api: formFamily === "openai" ? formWireApi : undefined,
    });
    setDialogOpen(false);
  }

  async function handleTestItem(gw: GatewayItem) {
    setTestingId(gw.id);
    try {
      const res = await testGatewayConnection({
        base_url: gw.base_url,
        family: gw.family,
      });
      setTestResults((prev) => ({ ...prev, [gw.id]: res }));
    } catch (e: any) {
      setTestResults((prev) => ({
        ...prev,
        [gw.id]: {
          status: "error",
          latency_ms: 0,
          message: e?.message || "Test failed",
        },
      }));
    } finally {
      setTestingId(null);
    }
  }

  function handleCopy(text: string, id: string) {
    navigator.clipboard.writeText(text);
    setCopiedId(id);
    setTimeout(() => setCopiedId(null), 2000);
  }

  return (
    <div className="flex flex-col gap-6">
      {/* Header */}
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div>
          <div className="flex items-center gap-2">
            <h1 className="text-2xl font-semibold">Model Gateways</h1>
            <Badge variant="secondary" className="text-xs font-normal">
              {gateways.length} configured
            </Badge>
          </div>
          <p className="mt-1 text-ui text-muted-foreground">
            Manage local and LAN model gateways for Claude SDK, Codex, and OpenAI agents. Connect directly to CLI Proxy API without CC-Switch.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button
            variant="outline"
            size="sm"
            onClick={() => void refetch()}
            className="gap-1.5 text-xs h-8"
          >
            <RefreshCwIcon className="h-3.5 w-3.5" />
            Refresh
          </Button>
          <Button
            size="sm"
            onClick={() => openCreateDialog()}
            className="gap-1.5 text-xs h-8"
          >
            <PlusIcon className="h-3.5 w-3.5" />
            Add Gateway
          </Button>
        </div>
      </div>

      {/* Quick Presets */}
      <div className="rounded-xl border border-border bg-card p-4">
        <div className="flex items-center gap-2 mb-3">
          <ZapIcon className="h-4 w-4 text-amber-500" />
          <h2 className="text-sm font-semibold">Quick Presets (一键快速预设)</h2>
        </div>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
          {/* Preset 1: CLI Proxy API Anthropic */}
          <div className="rounded-lg border border-border/80 bg-background/60 p-3 flex flex-col justify-between gap-2.5 hover:border-border transition-colors">
            <div className="flex items-start justify-between gap-2">
              <div>
                <div className="flex items-center gap-1.5 font-medium text-xs">
                  <ServerIcon className="h-3.5 w-3.5 text-blue-500" />
                  CLI Proxy API (Direct Anthropic)
                </div>
                <div className="text-xs text-muted-foreground mt-0.5 font-mono">
                  http://192.168.1.250:8317
                </div>
              </div>
              <Badge variant="outline" className="text-[10px] uppercase">Anthropic</Badge>
            </div>
            <p className="text-[11px] text-muted-foreground">
              直连局域网 CLI Proxy API，为 Claude SDK 提供原生 Messages 接口，无需启动本地 CC-Switch。
            </p>
            <Button
              size="sm"
              variant="secondary"
              className="h-7 text-xs w-full gap-1.5"
              onClick={() =>
                openCreateDialog({
                  id: "cpa-anthropic",
                  family: "anthropic",
                  base_url: "http://192.168.1.250:8317",
                  api_key: "56ff37d904ed90b4",
                  default_model: "claude-sonnet-4-6",
                  is_default: true,
                })
              }
            >
              <PlusIcon className="h-3 w-3" />
              Use This Preset (直连 CPA)
            </Button>
          </div>

          {/* Preset 2: CC-Switch Local */}
          <div className="rounded-lg border border-border/80 bg-background/60 p-3 flex flex-col justify-between gap-2.5 hover:border-border transition-colors">
            <div className="flex items-start justify-between gap-2">
              <div>
                <div className="flex items-center gap-1.5 font-medium text-xs">
                  <RadioIcon className="h-3.5 w-3.5 text-purple-500" />
                  CC-Switch (Local Relay)
                </div>
                <div className="text-xs text-muted-foreground mt-0.5 font-mono">
                  http://127.0.0.1:15721
                </div>
              </div>
              <Badge variant="outline" className="text-[10px] uppercase">Relay</Badge>
            </div>
            <p className="text-[11px] text-muted-foreground">
              本地运行的 CC-Switch 客户端中转，自动映射 claude-haiku-4-5 等自定义别名。
            </p>
            <Button
              size="sm"
              variant="outline"
              className="h-7 text-xs w-full gap-1.5"
              onClick={() =>
                openCreateDialog({
                  id: "claude-local-gateway",
                  family: "anthropic",
                  base_url: "http://127.0.0.1:15721",
                  api_key: "PROXY_MANAGED",
                  default_model: "claude-sonnet-4-6",
                  is_default: true,
                })
              }
            >
              <PlusIcon className="h-3 w-3" />
              Configure CC-Switch
            </Button>
          </div>
        </div>
      </div>

      {/* Gateway Cards List */}
      <div className="flex flex-col gap-3">
        <h2 className="text-sm font-semibold">Configured Gateways (已配置网关)</h2>
        {isLoading ? (
          <div className="flex items-center justify-center p-12 text-muted-foreground">
            <Loader2Icon className="h-5 w-5 animate-spin mr-2" />
            Loading gateways...
          </div>
        ) : gateways.length === 0 ? (
          <div className="rounded-xl border border-dashed border-border p-8 text-center text-sm text-muted-foreground">
            No gateways configured. Use a preset above or click &quot;Add Gateway&quot; to configure one.
          </div>
        ) : (
          <div className="grid grid-cols-1 gap-3">
            {gateways.map((gw) => {
              const testResult = testResults[gw.id];
              const isTesting = testingId === gw.id;

              return (
                <div
                  key={gw.id}
                  className="rounded-xl border border-border bg-card p-4 flex flex-col gap-3 transition-shadow hover:shadow-sm"
                >
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <div className="flex items-center gap-2.5">
                      <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-muted text-foreground">
                        {gw.family === "anthropic" ? (
                          <ServerIcon className="h-4 w-4 text-blue-500" />
                        ) : gw.family === "openai" ? (
                          <LayersIcon className="h-4 w-4 text-emerald-500" />
                        ) : (
                          <GlobeIcon className="h-4 w-4 text-amber-500" />
                        )}
                      </div>
                      <div>
                        <div className="flex items-center gap-2">
                          <span className="font-semibold text-sm">{gw.id}</span>
                          {gw.is_default && (
                            <Badge className="bg-primary/15 text-primary hover:bg-primary/20 text-[10px] font-normal border-primary/20">
                              Default ({gw.family})
                            </Badge>
                          )}
                          <Badge variant="outline" className="text-[10px] capitalize">
                            {gw.kind}
                          </Badge>
                        </div>
                        <div className="flex items-center gap-1.5 text-xs text-muted-foreground mt-0.5">
                          <span className="font-mono">{gw.base_url || "System Subscription"}</span>
                          {gw.base_url && (
                            <button
                              type="button"
                              onClick={() => handleCopy(gw.base_url, gw.id)}
                              className="text-muted-foreground hover:text-foreground transition-colors"
                              title="Copy URL"
                            >
                              {copiedId === gw.id ? (
                                <CheckIcon className="h-3 w-3 text-emerald-500" />
                              ) : (
                                <CopyIcon className="h-3 w-3" />
                              )}
                            </button>
                          )}
                        </div>
                      </div>
                    </div>

                    {/* Actions */}
                    <div className="flex items-center gap-2">
                      {gw.base_url && (
                        <Button
                          variant="outline"
                          size="sm"
                          disabled={isTesting}
                          onClick={() => void handleTestItem(gw)}
                          className="h-7 text-xs gap-1"
                        >
                          {isTesting ? (
                            <Loader2Icon className="h-3 w-3 animate-spin" />
                          ) : (
                            <RefreshCwIcon className="h-3 w-3" />
                          )}
                          Test
                        </Button>
                      )}

                      {!gw.is_default && gw.family !== "other" && (
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() => void setDefaultGateway.mutateAsync(gw.id)}
                          className="h-7 text-xs"
                        >
                          Set Default
                        </Button>
                      )}

                      <Button
                        variant="ghost"
                        size="icon"
                        onClick={() => openEditDialog(gw)}
                        className="h-7 w-7 text-muted-foreground hover:text-foreground"
                        title="Edit gateway"
                      >
                        <Edit2Icon className="h-3.5 w-3.5" />
                      </Button>

                      <Button
                        variant="ghost"
                        size="icon"
                        onClick={() => {
                          if (window.confirm(`Delete gateway ${gw.id}?`)) {
                            void deleteGateway.mutateAsync(gw.id);
                          }
                        }}
                        className="h-7 w-7 text-muted-foreground hover:text-destructive"
                        title="Delete gateway"
                      >
                        <Trash2Icon className="h-3.5 w-3.5" />
                      </Button>
                    </div>
                  </div>

                  {/* Metadata Row */}
                  <div className="flex flex-wrap items-center gap-4 text-xs text-muted-foreground border-t border-border/50 pt-2.5">
                    <div className="flex items-center gap-1.5">
                      <KeyRoundIcon className="h-3 w-3" />
                      <span>
                        Key:{" "}
                        <span className="font-mono text-foreground">
                          {gw.has_api_key ? gw.api_key_masked : "None"}
                        </span>
                      </span>
                    </div>

                    {gw.default_model && (
                      <div className="flex items-center gap-1.5">
                        <span>Default Model:</span>
                        <span className="font-mono text-foreground font-medium">
                          {gw.default_model}
                        </span>
                      </div>
                    )}

                    {gw.wire_api && (
                      <div className="flex items-center gap-1.5">
                        <span>Wire API:</span>
                        <span className="font-mono text-foreground">{gw.wire_api}</span>
                      </div>
                    )}

                    {/* Test result display */}
                    {testResult && (
                      <div
                        className={`flex items-center gap-1.5 ml-auto text-xs ${
                          testResult.status === "ok" ? "text-emerald-500" : "text-destructive"
                        }`}
                      >
                        {testResult.status === "ok" ? (
                          <CheckCircle2Icon className="h-3.5 w-3.5" />
                        ) : (
                          <AlertCircleIcon className="h-3.5 w-3.5" />
                        )}
                        <span>{testResult.message}</span>
                      </div>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>

      {/* Add / Edit Gateway Dialog */}
      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent className="sm:max-w-[480px]">
          <DialogHeader>
            <DialogTitle>
              {editingGateway ? `Edit Gateway (${editingGateway.id})` : "Add Model Gateway"}
            </DialogTitle>
            <DialogDescription className="text-xs">
              Configure connection parameters for an Anthropic or OpenAI compatible gateway endpoint.
            </DialogDescription>
          </DialogHeader>

          <div className="flex flex-col gap-4 py-2">
            {/* ID */}
            <div className="flex flex-col gap-1.5">
              <label className="text-xs font-medium">Gateway Identifier (ID)</label>
              <Input
                placeholder="e.g. cpa-anthropic, my-gateway"
                value={formId}
                onChange={(e) => setFormId(e.target.value)}
                disabled={!!editingGateway}
                className="h-8 text-xs font-mono"
              />
            </div>

            {/* Protocol Family */}
            <div className="grid grid-cols-2 gap-3">
              <div className="flex flex-col gap-1.5">
                <label className="text-xs font-medium">Protocol Family</label>
                <Select
                  value={formFamily}
                  onValueChange={(val: "anthropic" | "openai") => setFormFamily(val)}
                >
                  <SelectTrigger className="h-8 text-xs">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="anthropic" className="text-xs">
                      Anthropic (Claude SDK)
                    </SelectItem>
                    <SelectItem value="openai" className="text-xs">
                      OpenAI (Codex / Agents)
                    </SelectItem>
                  </SelectContent>
                </Select>
              </div>

              {formFamily === "openai" && (
                <div className="flex flex-col gap-1.5">
                  <label className="text-xs font-medium">Wire API</label>
                  <Select
                    value={formWireApi}
                    onValueChange={(val: "responses" | "chat") => setFormWireApi(val)}
                  >
                    <SelectTrigger className="h-8 text-xs">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="responses" className="text-xs">
                        Responses (Codex native)
                      </SelectItem>
                      <SelectItem value="chat" className="text-xs">
                        Chat Completions (/v1/chat)
                      </SelectItem>
                    </SelectContent>
                  </Select>
                </div>
              )}
            </div>

            {/* Base URL */}
            <div className="flex flex-col gap-1.5">
              <label className="text-xs font-medium">Base URL</label>
              <Input
                placeholder="http://192.168.1.250:8317"
                value={formBaseUrl}
                onChange={(e) => setFormBaseUrl(e.target.value)}
                className="h-8 text-xs font-mono"
              />
              <span className="text-[11px] text-muted-foreground">
                Do not include trailing slash. For CPA Anthropic use port 8317 directly.
              </span>
            </div>

            {/* API Key */}
            <div className="flex flex-col gap-1.5">
              <label className="text-xs font-medium">
                API Key / Auth Token {editingGateway ? "(leave blank to keep existing)" : ""}
              </label>
              <Input
                type="password"
                placeholder={editingGateway?.has_api_key ? "••••••••" : "56ff37d904ed90b4 or env:VAR"}
                value={formApiKey}
                onChange={(e) => setFormApiKey(e.target.value)}
                className="h-8 text-xs font-mono"
              />
            </div>

            {/* Default Model */}
            <div className="flex flex-col gap-1.5">
              <label className="text-xs font-medium">Default Model (Optional)</label>
              <Input
                placeholder={formFamily === "anthropic" ? "claude-sonnet-4-6" : "gpt-5.6-sol"}
                value={formDefaultModel}
                onChange={(e) => setFormDefaultModel(e.target.value)}
                className="h-8 text-xs font-mono"
              />
            </div>

            {/* Default Switch */}
            <div className="flex items-center justify-between rounded-lg border border-border p-3">
              <div className="flex flex-col gap-0.5">
                <span className="text-xs font-medium">Set as Default Gateway</span>
                <span className="text-[11px] text-muted-foreground">
                  Use this gateway as the primary handler for {formFamily} model execution.
                </span>
              </div>
              <Switch
                checked={formIsDefault}
                onCheckedChange={setFormIsDefault}
              />
            </div>

            {/* Test Connection Button & Result */}
            <div className="rounded-lg border border-border/70 bg-muted/30 p-3 flex flex-col gap-2">
              <div className="flex items-center justify-between">
                <span className="text-xs font-medium text-muted-foreground">Endpoint Verification</span>
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  disabled={!formBaseUrl || dialogTesting}
                  onClick={() => void handleTestInDialog()}
                  className="h-7 text-xs gap-1.5"
                >
                  {dialogTesting ? (
                    <Loader2Icon className="h-3 w-3 animate-spin" />
                  ) : (
                    <RefreshCwIcon className="h-3 w-3" />
                  )}
                  Test Connection
                </Button>
              </div>

              {dialogTestResult && (
                <div
                  className={`text-xs flex items-center gap-1.5 ${
                    dialogTestResult.status === "ok" ? "text-emerald-500" : "text-destructive"
                  }`}
                >
                  {dialogTestResult.status === "ok" ? (
                    <CheckCircle2Icon className="h-3.5 w-3.5 flex-shrink-0" />
                  ) : (
                    <AlertCircleIcon className="h-3.5 w-3.5 flex-shrink-0" />
                  )}
                  <span className="truncate">{dialogTestResult.message}</span>
                </div>
              )}
            </div>
          </div>

          <DialogFooter className="gap-2 sm:gap-0">
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={() => setDialogOpen(false)}
              className="text-xs h-8"
            >
              Cancel
            </Button>
            <Button
              type="button"
              size="sm"
              disabled={!formId || !formBaseUrl || saveGateway.isPending}
              onClick={() => void handleSave()}
              className="text-xs h-8 gap-1.5"
            >
              {saveGateway.isPending && <Loader2Icon className="h-3 w-3 animate-spin" />}
              Save Gateway
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
