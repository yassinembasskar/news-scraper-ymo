// Hooks for the day-by-day cluster browser (DayClusterGrid + ClusterPanel)
// — separate from usePipeline.ts's stage-status hooks, since this is a
// browsing view (poll slowly, no in-progress/ETA concerns) rather than a
// live job-progress view.

import { useQuery } from "@tanstack/react-query";
import * as api from "@/api/client";

const POLL_MS = 20000;

export function useDaysOverview() {
  return useQuery({
    queryKey: ["days-overview"],
    queryFn: api.getDaysOverview,
    refetchInterval: POLL_MS,
  });
}

// Only fetches once a day is actually selected — there's no point loading
// every day's clusters up front when the grid only ever shows one day's
// list at a time.
export function useClustersForDay(date: string | null) {
  return useQuery({
    queryKey: ["clusters", date],
    queryFn: () => api.getClusters(date ?? undefined),
    enabled: date !== null,
    refetchInterval: POLL_MS,
  });
}
