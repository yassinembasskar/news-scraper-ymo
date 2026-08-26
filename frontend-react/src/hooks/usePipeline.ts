// Hooks for the four background pipeline stages (extract/embed/score/
// report-generation) plus the overview/pipeline stat cards. Every stage
// status query polls itself — fast while a run is in progress, slow
// otherwise, so a run started from another tab/session is still picked up.

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import * as api from "@/api/client";
import type { StageStatusResponse } from "@/api/types";

const ACTIVE_POLL_MS = 1500;
const IDLE_POLL_MS = 20000;

function pollInterval(data: { in_progress: boolean } | undefined) {
  return data?.in_progress ? ACTIVE_POLL_MS : IDLE_POLL_MS;
}

export function useOverview() {
  return useQuery({
    queryKey: ["overview"],
    queryFn: api.getOverview,
    refetchInterval: IDLE_POLL_MS,
  });
}

// Pass `fast: true` while any stage is running so the breakdown counts and
// outer progress ring keep pace with the per-run bar instead of lagging up
// to 20s behind it — see useAnyStageRunning(), which the dashboard uses to
// derive this without an extra request (it reads the same cached
// stage-status queries the per-stage cards already poll).
export function usePipelineStats(fast = false) {
  return useQuery({
    queryKey: ["pipeline-stats"],
    queryFn: api.getPipelineStats,
    refetchInterval: fast ? ACTIVE_POLL_MS : IDLE_POLL_MS,
  });
}

// ── Generic stage (extraction / embedding / scoring) ────────────────────

export type StageName = "titleGate" | "extraction" | "embedding" | "scoring";

const STAGE_API: Record<
  StageName,
  { status: () => Promise<StageStatusResponse>; trigger: () => Promise<unknown>; stop: () => Promise<unknown> }
> = {
  titleGate: { status: api.getTitleGateStatus, trigger: api.triggerTitleGate, stop: api.stopTitleGate },
  extraction: { status: api.getExtractionStatus, trigger: api.triggerExtraction, stop: api.stopExtraction },
  embedding: { status: api.getEmbeddingStatus, trigger: api.triggerEmbedding, stop: api.stopEmbedding },
  scoring: { status: api.getScoringStatus, trigger: api.triggerScoring, stop: api.stopScoring },
};

export function useStageStatus(stage: StageName) {
  return useQuery({
    queryKey: ["stage-status", stage],
    queryFn: STAGE_API[stage].status,
    refetchInterval: (query) => pollInterval(query.state.data),
  });
}

// The four per-stage cards each already poll their own stage-status query;
// this just reads those same cached results (no extra request) to answer
// "is anything running right now", so the dashboard can speed up the
// pipeline-stats poll to match while a run is active.
export function useAnyStageRunning() {
  const titleGate = useStageStatus("titleGate");
  const extraction = useStageStatus("extraction");
  const embedding = useStageStatus("embedding");
  const scoring = useStageStatus("scoring");
  return Boolean(
    titleGate.data?.in_progress ||
      extraction.data?.in_progress ||
      embedding.data?.in_progress ||
      scoring.data?.in_progress,
  );
}

export function useStageActions(stage: StageName) {
  const queryClient = useQueryClient();
  const statusKey = ["stage-status", stage];
  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: statusKey });
    queryClient.invalidateQueries({ queryKey: ["pipeline-stats"] });
    queryClient.invalidateQueries({ queryKey: ["articles"] });
  };

  // Flip the button to "Stop" the instant Run is clicked rather than
  // waiting on a round trip — the next poll (as fast as ACTIVE_POLL_MS)
  // reconciles with the real status, and onError rolls back if the
  // request itself failed outright (e.g. the cross-stage guard rejected
  // it). Stop doesn't need this: in_progress correctly stays true until
  // the job actually winds down, and the button already shows immediate
  // feedback via its own `loading` (mutation isPending) state.
  const rollback = (context: { previous?: StageStatusResponse } | undefined) => {
    if (context?.previous) queryClient.setQueryData(statusKey, context.previous);
  };

  const trigger = useMutation({
    mutationFn: STAGE_API[stage].trigger,
    onMutate: async () => {
      await queryClient.cancelQueries({ queryKey: statusKey });
      const previous = queryClient.getQueryData<StageStatusResponse>(statusKey);
      if (previous) queryClient.setQueryData<StageStatusResponse>(statusKey, { ...previous, in_progress: true });
      return { previous };
    },
    onError: (_err, _vars, context) => rollback(context),
    onSuccess: invalidate,
  });
  const stop = useMutation({
    mutationFn: STAGE_API[stage].stop,
    onSuccess: invalidate,
  });

  return { trigger, stop };
}

// ── Update / scrape (synchronous, no polling needed) ────────────────────

export function useTriggerUpdate() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: api.triggerUpdate,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["overview"] });
      queryClient.invalidateQueries({ queryKey: ["pipeline-stats"] });
      queryClient.invalidateQueries({ queryKey: ["articles"] });
    },
  });
}
