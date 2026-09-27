import { ChevronLeft, ChevronRight } from "lucide-react";
import { cn } from "@/lib/utils";
import type { CalendarDay } from "@/api/types";

const WEEKDAY_HEADERS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

function daysInMonth(year: number, month: number): number {
  return new Date(year, month, 0).getDate();
}

/** Monday-first weekday index (0..6) for the 1st of the month. */
function firstWeekdayOffset(year: number, month: number): number {
  const jsDay = new Date(year, month - 1, 1).getDay(); // 0 = Sunday
  return (jsDay + 6) % 7;
}

export function CalendarGrid({
  year,
  month,
  marked,
  selectedDate,
  onSelectDate,
  onShiftMonth,
}: {
  year: number;
  month: number;
  marked: Record<string, CalendarDay>;
  selectedDate: string | null;
  onSelectDate: (date: string) => void;
  onShiftMonth: (delta: number) => void;
}) {
  const total = daysInMonth(year, month);
  const offset = firstWeekdayOffset(year, month);
  const cells: (number | null)[] = [...Array(offset).fill(null), ...Array.from({ length: total }, (_, i) => i + 1)];

  return (
    <div>
      <div className="mb-4 flex items-center justify-between">
        <button
          onClick={() => onShiftMonth(-1)}
          className="flex size-8 items-center justify-center rounded-[3px] text-ink-secondary hover:bg-surface-raised"
        >
          <ChevronLeft className="size-4" />
        </button>
        <h3 className="font-display text-sm font-bold uppercase tracking-wide text-ink">
          {new Date(year, month - 1, 1).toLocaleString("en", { month: "long", year: "numeric" })}
        </h3>
        <button
          onClick={() => onShiftMonth(1)}
          className="flex size-8 items-center justify-center rounded-[3px] text-ink-secondary hover:bg-surface-raised"
        >
          <ChevronRight className="size-4" />
        </button>
      </div>

      <div className="grid grid-cols-7 gap-1.5">
        {WEEKDAY_HEADERS.map((d) => (
          <div key={d} className="pb-1 text-center text-[10px] font-semibold uppercase tracking-wide text-ink-muted">
            {d}
          </div>
        ))}
        {cells.map((day, i) => {
          if (day === null) return <div key={`pad-${i}`} />;
          const dateStr = `${year.toString().padStart(4, "0")}-${month.toString().padStart(2, "0")}-${day
            .toString()
            .padStart(2, "0")}`;
          const summary = marked[dateStr];
          const isSelected = selectedDate === dateStr;

          let marker: string | null = null;
          if (summary && summary.total_clusters > 0) {
            const pending = summary.total_clusters - summary.insights_generated_clusters;
            if (pending > 0) {
              marker = `· ${pending}`;
            } else {
              marker = summary.relevant_count > 0 ? `🔴 ${summary.relevant_count}` : "✅";
            }
          }

          return (
            <button
              key={dateStr}
              onClick={() => onSelectDate(dateStr)}
              className={cn(
                "flex flex-col items-center gap-0.5 rounded-[3px] py-2 text-sm transition-colors",
                isSelected
                  ? "bg-brand text-brand-ink"
                  : "text-ink hover:bg-surface-raised",
              )}
            >
              <span className="tabular font-mono font-medium">{day}</span>
              <span className={cn("text-[10px] leading-none", isSelected ? "text-brand-ink/80" : "text-ink-muted")}>
                {marker ?? " "}
              </span>
            </button>
          );
        })}
      </div>
    </div>
  );
}
