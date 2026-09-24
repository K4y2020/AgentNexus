import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { authenticatedFetch } from "@/lib/identity";

export interface GatewayItem {
  id: string;
  name: string;
  kind: string;
  family: "anthropic" | "openai" | "other";
  base_url: string;
  has_api_key: boolean;
  api_key_masked: string;
  is_default: boolean;
  default_model: string;
  wire_api?: string;
}

export interface GatewayPayload {
  id: string;
  kind?: string;
  family: "anthropic" | "openai";
  base_url: string;
  api_key?: string;
  is_default?: boolean;
  default_model?: string;
  wire_api?: string;
}

export interface GatewayTestPayload {
  base_url: string;
  family: string;
  api_key?: string;
}

export interface GatewayTestResult {
  status: "ok" | "error";
  latency_ms: number;
  models_count?: number;
  models?: string[];
  message: string;
  error?: string;
}

export function useGateways() {
  return useQuery<GatewayItem[]>({
    queryKey: ["gateways"],
    queryFn: async () => {
      const res = await authenticatedFetch("/v1/gateways");
      if (!res.ok) {
        throw new Error(`Failed to load gateways: HTTP ${res.status}`);
      }
      const data = await res.json();
      return (data.gateways || []) as GatewayItem[];
    },
  });
}

export function useSaveGateway() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (payload: GatewayPayload) => {
      const res = await authenticatedFetch("/v1/gateways", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || `Failed to save gateway: HTTP ${res.status}`);
      }
      return res.json();
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["gateways"] });
      queryClient.invalidateQueries({ queryKey: ["host-model-options"] });
    },
  });
}

export function useDeleteGateway() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (gatewayId: string) => {
      const res = await authenticatedFetch(`/v1/gateways/${encodeURIComponent(gatewayId)}`, {
        method: "DELETE",
      });
      if (!res.ok) {
        throw new Error(`Failed to delete gateway: HTTP ${res.status}`);
      }
      return res.json();
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["gateways"] });
      queryClient.invalidateQueries({ queryKey: ["host-model-options"] });
    },
  });
}

export function useSetDefaultGateway() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (gatewayId: string) => {
      const res = await authenticatedFetch(
        `/v1/gateways/${encodeURIComponent(gatewayId)}/set-default`,
        {
          method: "POST",
        },
      );
      if (!res.ok) {
        throw new Error(`Failed to set default gateway: HTTP ${res.status}`);
      }
      return res.json();
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["gateways"] });
      queryClient.invalidateQueries({ queryKey: ["host-model-options"] });
    },
  });
}

export async function testGatewayConnection(
  payload: GatewayTestPayload,
): Promise<GatewayTestResult> {
  const res = await authenticatedFetch("/v1/gateways/test", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    return {
      status: "error",
      latency_ms: 0,
      message: err.detail || `HTTP ${res.status}`,
      error: err.detail,
    };
  }
  return res.json();
}
