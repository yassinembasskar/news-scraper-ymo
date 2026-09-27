// The "full picture" of time spent in each phase, across every stage —
// a summary (total/average time per stage, and per preprocessing phase)
// plus the raw chronological run history underneath it. Computed
// client-side from the same GET /logs entries the list renders, rather
// than a separate backend aggregation — cheap at this scale (a few
// hundred run records at most) and keeps the summary always in sync
// with exactly what's listed below it.

import { useMemo, useState } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";
import { Card, CardBody, CardHeader } from "@/components/ui/Card";
import { cn } from "@/lib/utils";
import { formatDateTime } from "@/lib/format";
import { useLogs } from "@/hooks/useLogs";
import type { PipelineLogStage, RunLogEntry } from "@/api/types";

const STAGE_LABEL: Record<PipelineLogStage, string> = {
  scrape: "Scrape",
  title_gate: "Title gate",
  preprocessing: "Preprocessing",
};

const STAGE_ORDER: PipelineLogStage[] = ["scrape", "title_gate", "preprocessing"];

const PHASE_LABEL: Record<string, string> = {
  clustering: "Clustering",
  extraction: "Extraction",
  condensing: "Condensing",
  entities: "Entities",
  contextualization: "Contextualization",
  insights: "Insights",
};

const STATUS_STYLE: Record<string, string> = {
  success: "bg-status-good/10 text-status-good",
  partial: "bg-status-warning/15 text-status-warning",
  failed: "bg-status-critical/10 text-status-critical",
  stopped: "bg-surface-raised text-ink-muted",
  skipped: "bg-surface-raised text-ink-muted",
};

function formatSeconds(seconds: number | null): string {
  if (seconds == null) return "—";
  if (seconds < 60) return `${Math.round(seconds)}s`;
  const minutes = seconds / 60;
  if (minutes < 60) return `${minutes.toFixed(1)}min`;
  const hours = minutes / 60;
  return `${hours.toFixed(1)}h`;
}

function StatusPill({ status }: { status: string }) {
  return (
    <span
      className={cn(
        "rounded-full px-2 py-0.5 text-[11px] font-medium capitalize",
        STATUS_STYLE[status] ?? "bg-surface-raised text-ink-muted",
      )}
    >
      {status}
    </span>
  );
}

function buildSummary(entries: RunLogEntry[]) {
  const byStage = new Map<PipelineLogStage, { total: number; count: number }>();
  const phaseTotals = new Map<string, number>();

  for (const entry of entries) {
    const acc = byStage.get(entry.stage) ?? { total: 0, count: 0 };
    acc.total += entry.duration_seconds ?? 0;
    acc.count += 1;
    byStage.set(entry.stage, acc);

    if (entry.phase_durations) {
      for (const [phase, seconds] of Object.entries(entry.phase_durations)) {
        phaseTotals.set(phase, (phaseTotals.get(phase) ?? 0) + seconds);
      }
    }
  }

  return { byStage, phaseTotals };
}

function LogRow({ entry }: { entry: RunLogEntry }) {
  const [expanded, setExpanded] = useState(false);
  const hasPhases = entry.phase_durations && Object.keys(entry.phase_durations).length > 0;

  return (
    <div className="px-5 py-3">
      <button
        onClick={() => hasPhases && setExpanded((e) => !e)}
        className={cn("flex w-full items-center gap-3 text-left", hasPhases ? "cursor-pointer" : "cursor-default")}
      >
        {hasPhases ? (
          expanded ? (
            <ChevronDown className="size-3.5 shrink-0 text-ink-muted" />
          ) : (
            <ChevronRight className="size-3.5 shrink-0 text-ink-muted" />
          )
        ) : (
          <span className="w-3.5 shrink-0" />
        )}
        <span className="w-36 shrink-0 text-xs font-medium text-ink">{STAGE_LABEL[entry.stage]}</span>
        <StatusPill status={entry.status} />
        <span className="min-w-0 flex-1 truncate text-xs text-ink-secondary">{entry.message}</span>
        <span className="w-16 shrink-0 text-right text-xs font-medium text-ink">
          {formatSeconds(entry.duration_seconds)}
        </span>
        <span className="w-36 shrink-0 text-right text-[11px] text-ink-muted">
          {formatDateTime(entry.logged_at)}
        </span>
      </button>
      {expanded && hasPhases && (
        <div className="ml-6 mt-2 flex flex-wrap gap-2">
          {Object.entries(entry.phase_durations!).map(([phase, seconds]) => (
            <span key={phase} className="rounded-lg bg-surface-raised px-2.5 py-1 text-[11px] text-ink-secondary">
              {PHASE_LABEL[phase] ?? phase}: <span className="font-medium text-ink">{formatSeconds(seconds)}</span>
            </span>
          ))}
        </div>
      )}
    </div>
  );
}

export default function Logs() {
  const { data: entries, isLoading } = useLogs(200);
  const { byStage, phaseTotals } = useMemo(() => buildSummary(entries ?? []), [entries]);

  return (
    <div className="space-y-6">
      <div>
        <h1 className="font-display text-3xl font-bold uppercase tracking-wide text-ink">Logs</h1>
        <p className="text-sm text-ink-muted">Time spent per phase, across every stage of the pipeline.</p>
      </div>

      <Card>
        <CardHeader title="Time per stage" subtitle="Summed across the runs listed below" />
        <CardBody>
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
            {STAGE_ORDER.map((stage) => {
              const acc = byStage.get(stage);
              return (
                <div key={stage} className="rounded-lg bg-surface-raised px-3 py-2.5">
                  <p className="text-[10px] uppercase tracking-wide text-ink-muted">{STAGE_LABEL[stage]}</p>
                  <p className="mt-0.5 text-sm font-semibold text-ink">{formatSeconds(acc?.total ?? null)}</p>
                  <p className="text-[11px] text-ink-muted">{acc?.count ?? 0} run(s)</p>
                </div>
              );
            })}
          </div>
          {phaseTotals.size > 0 && (
            <div className="mt-4 border-t border-border pt-4">
              <p className="mb-2 text-xs font-semibold text-ink-muted">Preprocessing, by phase</p>
              <div className="flex flex-wrap gap-2">
                {Object.entries(PHASE_LABEL).map(([phase, label]) =>
                  phaseTotals.has(phase) ? (
                    <span key={phase} className="rounded-lg bg-surface-raised px-2.5 py-1.5 text-xs text-ink-secondary">
                      {label}: <span className="font-medium text-ink">{formatSeconds(phaseTotals.get(phase)!)}</span>
                    </span>
                  ) : null,
                )}
              </div>
            </div>
          )}
        </CardBody>
      </Card>

      <Card>
        <CardHeader title="Run history" subtitle={isLoading ? "Loading…" : `${entries?.length ?? 0} run(s)`} />
        <div className="mt-2 divide-y divide-border">
          {entries?.map((entry, i) => <LogRow key={`${entry.stage}-${entry.logged_at}-${i}`} entry={entry} />)}
          {!isLoading && (entries?.length ?? 0) === 0 && (
            <p className="px-5 py-8 text-center text-sm text-ink-muted">No runs logged yet.</p>
          )}
        </div>
      </Card>
    </div>
  );
}
