// TanStack Query hooks for one teammate's durable memories. Mutations invalidate
// only that bot's memory key so unrelated roster rows stay untouched.

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  createTeammateMemory,
  deleteTeammateMemory,
  listTeammateMemories,
  updateTeammateMemory,
  type TeammateMemory,
} from "@/lib/teammatesApi";

export const TEAMMATE_MEMORIES_KEY = ["teammate-memories"] as const;

function memoriesKey(agentId: string) {
  return [...TEAMMATE_MEMORIES_KEY, agentId] as const;
}

export function useTeammateMemories(agentId: string | null) {
  return useQuery<TeammateMemory[]>({
    queryKey: memoriesKey(agentId ?? ""),
    queryFn: () => listTeammateMemories(agentId!),
    enabled: agentId !== null,
    staleTime: 10_000,
  });
}

export function useCreateTeammateMemory(agentId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (content: string) => createTeammateMemory(agentId, content),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: memoriesKey(agentId) });
    },
  });
}

export function useUpdateTeammateMemory(agentId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ memoryId, content }: { memoryId: string; content: string }) =>
      updateTeammateMemory(agentId, memoryId, content),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: memoriesKey(agentId) });
    },
  });
}

export function useDeleteTeammateMemory(agentId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (memoryId: string) => deleteTeammateMemory(agentId, memoryId),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: memoriesKey(agentId) });
    },
  });
}
