import { useQuery } from "@tanstack/react-query";
import { listTeammates, type Teammate } from "@/lib/teammatesApi";

export const TEAMMATES_KEY = ["teammates"] as const;

export function useTeammates() {
  try {
    return useQuery<Teammate[]>({
      queryKey: TEAMMATES_KEY,
      queryFn: listTeammates,
      staleTime: 30_000,
      refetchInterval: 60_000,
    });
  } catch {
    return {
      data: [],
      isLoading: false,
      isError: false,
      refetch: async () => {},
    } as unknown as ReturnType<typeof useQuery<Teammate[]>>;
  }
}
