import { useQuery } from "@tanstack/react-query";
import * as api from "@/api/client";

// Run-history is written once per completed run, not live progress, so a
// slow poll is enough to pick up a run that just finished.
const POLL_MS = 20000;

export function useLogs(limit = 100) {
  return useQuery({
    queryKey: ["logs", limit],
    queryFn: () => api.getLogs(limit),
    refetchInterval: POLL_MS,
  });
}
