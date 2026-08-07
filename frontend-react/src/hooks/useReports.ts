import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import * as api from "@/api/client";
import type { GenerateStatusResponse } from "@/api/types";

const ACTIVE_POLL_MS = 1500;
const IDLE_POLL_MS = 20000;

export function useReportCalendar(year: number, month: number) {
  return useQuery({
    queryKey: ["report-calendar", year, month],
    queryFn: () => api.getReportCalendar(year, month),
  });
}

export function useDailyReport(reportDate: string | undefined) {
  return useQuery({
    queryKey: ["daily-report", reportDate],
    queryFn: () => api.getDailyReport(reportDate as string),
    enabled: !!reportDate,
  });
}

export function useReportGenerateStatus() {
  return useQuery({
    queryKey: ["report-generate-status"],
    queryFn: api.getReportGenerateStatus,
    refetchInterval: (query) => (query.state.data?.in_progress ? ACTIVE_POLL_MS : IDLE_POLL_MS),
  });
}

export function useReportActions(reportDate: string, year: number, month: number) {
  const queryClient = useQueryClient();
  const statusKey = ["report-generate-status"];
  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: statusKey });
    queryClient.invalidateQueries({ queryKey: ["report-calendar", year, month] });
    queryClient.invalidateQueries({ queryKey: ["daily-report", reportDate] });
  };

  // Same reasoning as the pipeline stage cards (useStageActions in
  // usePipeline.ts): flip the banner on the instant Generate is clicked
  // instead of waiting on a poll — Reports.tsx's isGeneratingThisDay check
  // reads this same cached status, so this also swaps the "Generate"
  // button out immediately, not just the banner.
  const generate = useMutation({
    mutationFn: () => api.generateDailyReport(reportDate),
    onMutate: async () => {
      await queryClient.cancelQueries({ queryKey: statusKey });
      const previous = queryClient.getQueryData<GenerateStatusResponse>(statusKey);
      queryClient.setQueryData<GenerateStatusResponse>(statusKey, {
        in_progress: true,
        report_date: reportDate,
        total: previous?.report_date === reportDate ? previous.total : 0,
        processed: 0,
        estimated_minutes_remaining: null,
      });
      return { previous };
    },
    onError: (_err, _vars, context) => {
      if (context?.previous) queryClient.setQueryData(statusKey, context.previous);
    },
    onSuccess: invalidate,
  });
  const stop = useMutation({
    mutationFn: api.stopReportGeneration,
    onSuccess: invalidate,
  });

  return { generate, stop };
}
