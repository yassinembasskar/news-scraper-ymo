import { useQuery } from "@tanstack/react-query";
import * as api from "@/api/client";

// Read-only — insight generation is preprocessing's own phase 6 now, not
// an on-demand per-day job, so there's nothing here to trigger or poll a
// progress bar for. usePreprocessingStatus (usePipeline.ts) already
// covers the "insights" phase's live progress.

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
