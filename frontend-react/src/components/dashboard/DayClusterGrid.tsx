// Day-by-day cluster browser — replaces the old article-activity chart +
// article list. Every day with at least one article is clickable (not
// just clustered ones) and opens ClusterPanel below, which shows that
// day's stats (articles/clusters/extraction success rate) regardless of
// how far the pipeline has gotten. Cells stay white/neutral — status is
// conveyed by a colored border plus a thin two-segment bar (archived vs.
// passed-gate-or-clustered) at the bottom, not a solid color fill.

import { useMemo } from "react";
import { Card, CardHeader } from "@/components/ui/Card";
import { cn } from "@/lib/utils";
import { useDaysOverview } from "@/hooks/useClusters";
import type { DayOverview } from "@/api/types";

function addDays(iso: string, days: number): string {
  const d = new Date(`${iso}T00:00:00Z`);
  d.setUTCDate(d.getUTCDate() + days);
  return d.toISOString().slice(0, 10);
}

function monthLabel(iso: string): string {
  return new Date(`${iso}T00:00:00Z`).toLocaleString("en", { month: "short", year: "numeric", timeZone: "UTC" });
}

// Fills every day between the first and last day that actually has
// articles, so gaps in scraping/API coverage render as visibly empty
// cells instead of just being absent from the grid.
function fillGaps(days: DayOverview[]): DayOverview[] {
  if (days.length === 0) return [];
  const byDate = new Map(days.map((d) => [d.date, d]));
  const filled: DayOverview[] = [];
  let cursor = days[0].date;
  const last = days[days.length - 1].date;
  while (cursor <= last) {
    filled.push(
      byDate.get(cursor) ?? {
        date: cursor,
        total: 0,
        gated: 0,
        archived: 0,
        passed: 0,
        is_gate_complete: false,
        extracted_successful: 0,
        extraction_attempted: 0,
        extraction_success_rate: null,
        cluster_count: 0,
        is_clustered: false,
      },
    );
    cursor = addDays(cursor, 1);
  }
  return filled;
}

// Each day is a jack on a patch panel: a small square terminal, solid-
// bordered once wired (title-gate complete) or dashed while still open,
// with a thin inset bar in place of a punch-down wire showing the
// archived/passed split.
function DayCell({ day, onSelect, selected }: { day: DayOverview; onSelect: (date: string) => void; selected: boolean }) {
  const dayNumber = Number(day.date.slice(-2));

  if (day.total === 0) {
    return (
      <div
        title={day.date}
        className="flex aspect-square items-center justify-center rounded-[2px] border border-dashed border-border font-mono text-[10px] text-ink-muted opacity-50"
      >
        {dayNumber}
      </div>
    );
  }

  const greenValue = day.is_clustered ? day.cluster_count : day.passed;
  const borderColor = day.is_gate_complete ? "border-border" : "border-status-warning";

  return (
    <button
      type="button"
      onClick={() => onSelect(day.date)}
      title={`${day.date} — ${day.total} article(s)${day.is_gate_complete ? `, ${day.archived} archived, ${day.passed} passed gate` : ", title gate still running"}${day.is_clustered ? `, ${day.cluster_count} cluster(s)` : ""}`}
      className={cn(
        "relative flex aspect-square cursor-pointer flex-col items-center justify-center gap-1 rounded-[2px] border-[1.5px] bg-surface font-mono text-[10.5px] text-ink transition-colors hover:border-ink-muted",
        borderColor,
        selected && "outline outline-2 outline-offset-1 outline-brand",
      )}
    >
      <span>{dayNumber}</span>
      {day.is_gate_complete ? (
        <div className="absolute inset-x-1 bottom-1 flex h-[2.5px] overflow-hidden rounded-full bg-border">
          {day.archived > 0 && (
            <div className="bg-status-warning" style={{ flexGrow: Math.max(day.archived, 1), flexBasis: 0 }} />
          )}
          {greenValue > 0 && (
            <div className="bg-status-good" style={{ flexGrow: Math.max(greenValue, 1), flexBasis: 0 }} />
          )}
        </div>
      ) : (
        <div className="absolute inset-x-1 bottom-1 h-[2.5px] rounded-full bg-status-warning" />
      )}
    </button>
  );
}

export function DayClusterGrid({
  selectedDay,
  onSelectDay,
}: {
  selectedDay: string | null;
  onSelectDay: (date: string | null) => void;
}) {
  const { data, isLoading } = useDaysOverview();
  const days = useMemo(() => fillGaps(data ?? []), [data]);

  const months = useMemo(() => {
    const groups: { label: string; days: DayOverview[] }[] = [];
    for (const day of days) {
      const label = monthLabel(day.date);
      const current = groups[groups.length - 1];
      if (current && current.label === label) current.days.push(day);
      else groups.push({ label, days: [day] });
    }
    return groups;
  }, [days]);

  return (
    <Card>
      <CardHeader
        title="Day patch panel"
        subtitle={isLoading ? "Loading…" : `${days.length} day(s) — click a day for its stats and clusters`}
      />
      <div className="space-y-4 p-5 pt-4">
        {months.map((month) => (
          <div key={month.label}>
            <p className="mb-1.5 text-[10.5px] font-semibold uppercase tracking-[0.1em] text-ink-muted">{month.label}</p>
            <div className="grid grid-cols-[repeat(auto-fill,minmax(28px,1fr))] gap-1.5">
              {month.days.map((day) => (
                <DayCell key={day.date} day={day} onSelect={onSelectDay} selected={selectedDay === day.date} />
              ))}
            </div>
          </div>
        ))}
        {!isLoading && days.length === 0 && (
          <p className="py-8 text-center text-sm text-ink-muted">No articles scraped yet.</p>
        )}
      </div>
    </Card>
  );
}
