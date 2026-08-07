import { useQuery } from "@tanstack/react-query";
import * as api from "@/api/client";

export type ChartRange = "day" | "week" | "month";

export function useDayChart(isoDate: string) {
  return useQuery({
    queryKey: ["chart", "day", isoDate],
    queryFn: () => api.getDayChart(isoDate),
  });
}

export function useWeekChart(isoYear: number, isoWeek: number) {
  return useQuery({
    queryKey: ["chart", "week", isoYear, isoWeek],
    queryFn: () => api.getWeekChart(isoYear, isoWeek),
  });
}

export function useMonthChart(year: number, month: number) {
  return useQuery({
    queryKey: ["chart", "month", year, month],
    queryFn: () => api.getMonthChart(year, month),
  });
}
