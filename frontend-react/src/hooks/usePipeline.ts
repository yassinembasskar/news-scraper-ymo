// Hooks for the background pipeline stages (title-gate/preprocessing/
// report-generation) plus the overview/pipeline stat cards. Every stage
// status query polls itself — fast while a run is in progress, slow
// otherwise, so a run started from another tab/session is still picked up.
// The old per-article embedding/scoring stages were retired 2026-09-15
// (see docs/completion-plan.md) in favor of cluster-level condensation/
// contextualization, tracked on the Preprocessing card instead.

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

// Meant to be watched live while a backlog run is in progress (see
// repository.get_preprocessing_quality_metrics's docstring) — polls
// faster whenever preprocessing is actually running, same reasoning as
// usePipelineStats(fast).
export function usePreprocessingQuality(fast = false) {
  return useQuery({
    queryKey: ["preprocessing-quality"],
    queryFn: api.getPreprocessingQuality,
    refetchInterval: fast ? ACTIVE_POLL_MS : IDLE_POLL_MS,
  });
}

// ── Generic stage (title-gate / preprocessing) ──────────────────────────

export type StageName = "titleGate" | "preprocessing";

const STAGE_API: Record<
  StageName,
  { status: () => Promise<StageStatusResponse>; trigger: () => Promise<unknown>; stop: () => Promise<unknown> }
> = {
  titleGate: { status: api.getTitleGateStatus, trigger: api.triggerTitleGate, stop: api.stopTitleGate },
  preprocessing: {
    status: api.getPreprocessingStatus,
    trigger: api.triggerPreprocessing,
    stop: api.stopPreprocessing,
  },
};

export function useStageStatus(stage: StageName) {
  return useQuery({
    queryKey: ["stage-status", stage],
    queryFn: STAGE_API[stage].status,
    refetchInterval: (query) => pollInterval(query.state.data),
  });
}

// Same query (and same cache entry, via the shared ["stage-status",
// "preprocessing"] key) as useStageStatus("preprocessing") — this one is
// just typed against the richer PreprocessingStatusResponse shape so
// components can read phase/phase_total/phase_processed/the *_done
// counters, which STAGE_API's generic StageStatusResponse type erases.
export function usePreprocessingStatus() {
  return useQuery({
    queryKey: ["stage-status", "preprocessing"],
    queryFn: api.getPreprocessingStatus,
    refetchInterval: (query) => pollInterval(query.state.data),
  });
}

// Same pattern as usePreprocessingStatus() above — shares the
// ["stage-status", "titleGate"] cache entry with useStageStatus("titleGate"),
// typed against the richer TitleGateStatusResponse shape for the
// days_gated/days_total/current_day fields.
export function useTitleGateStatus() {
  return useQuery({
    queryKey: ["stage-status", "titleGate"],
    queryFn: api.getTitleGateStatus,
    refetchInterval: (query) => pollInterval(query.state.data),
  });
}

// The per-stage cards each already poll their own stage-status query; this
// just reads those same cached results (no extra request) to answer "is
// anything running right now", so the dashboard can speed up the
// pipeline-stats poll to match while a run is active.
export function useAnyStageRunning() {
  const titleGate = useStageStatus("titleGate");
  const preprocessing = useStageStatus("preprocessing");
  return Boolean(titleGate.data?.in_progress || preprocessing.data?.in_progress);
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
