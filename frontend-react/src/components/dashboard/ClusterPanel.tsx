// Day detail panel — opened by clicking any day in DayClusterGrid.
// Header shows that day's stats (articles/archived/passed/extraction
// success rate) regardless of how far the pipeline has gotten for it;
// the body lists its clusters once any exist, each clickable to open
// ClusterDetail (representative title, article count, extraction status
// — see repository.get_cluster_display_info).

import { X } from "lucide-react";
import { Card, CardHeader } from "@/components/ui/Card";
import { cn } from "@/lib/utils";
import { useClustersForDay, useDaysOverview } from "@/hooks/useClusters";
import type { Cluster, ClusterExtractionStatus } from "@/api/types";

const EXTRACTION_LABEL: Record<ClusterExtractionStatus, string> = {
  not_extracted: "Not extracted",
  extracting: "Extracting",
  extracted: "Extracted",
};

const EXTRACTION_STYLE: Record<ClusterExtractionStatus, string> = {
  not_extracted: "bg-surface-raised text-ink-muted",
  extracting: "bg-status-warning/15 text-status-warning",
  extracted: "bg-status-good/10 text-status-good",
};

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg bg-surface-raised px-3 py-2">
      <p className="text-[10px] uppercase tracking-wide text-ink-muted">{label}</p>
      <p className="mt-0.5 text-sm font-semibold text-ink">{value}</p>
    </div>
  );
}

export function ClusterPanel({
  date,
  onClose,
  onSelectCluster,
}: {
  date: string;
  onClose: () => void;
  onSelectCluster: (cluster: Cluster) => void;
}) {
  const { data: days } = useDaysOverview();
  const { data: clusters, isLoading } = useClustersForDay(date);
  const day = days?.find((d) => d.date === date);

  return (
    <Card>
      <CardHeader
        title={`${date}`}
        subtitle={!day ? "Loading…" : `${day.total} article(s)`}
        action={
          <button onClick={onClose} className="rounded-full p-1 text-ink-muted hover:bg-surface-raised hover:text-ink">
            <X className="size-4" />
          </button>
        }
      />
      {day && (
        <div className="grid grid-cols-2 gap-2 px-5 pt-4 sm:grid-cols-4">
          <Stat label="Articles" value={String(day.total)} />
          <Stat label="Archived / Passed" value={`${day.archived} / ${day.passed}`} />
          <Stat label="Clusters" value={day.is_clustered ? String(day.cluster_count) : "not yet"} />
          <Stat
            label="Extraction success"
            value={day.extraction_attempted > 0 ? `${Math.round((day.extraction_success_rate ?? 0) * 100)}%` : "n/a"}
          />
        </div>
      )}
      {day && !day.is_gate_complete && (
        <p className="px-5 pt-3 text-xs text-status-critical">
          Title gate hasn't finished for this day yet ({day.gated}/{day.total} gated).
        </p>
      )}
      {day && day.is_gate_complete && !day.is_clustered && (
        <p className="px-5 pt-3 text-xs text-ink-muted">This day hasn't been clustered yet.</p>
      )}

      <div className="mt-4 divide-y divide-border">
        {clusters?.map((cluster) => (
          <button
            key={cluster.cluster_id}
            onClick={() => onSelectCluster(cluster)}
            className="flex w-full items-center gap-4 px-5 py-3.5 text-left hover:bg-surface-raised"
          >
            <div className="min-w-0 flex-1">
              <p className="line-clamp-2 text-sm font-medium text-ink">{cluster.representative_title}</p>
              <p className="mt-1 text-xs text-ink-muted">
                {cluster.article_ids.length} article{cluster.article_ids.length === 1 ? "" : "s"}
              </p>
            </div>
            <span
              className={cn(
                "shrink-0 rounded-full px-2.5 py-1 text-[11px] font-medium whitespace-nowrap",
                EXTRACTION_STYLE[cluster.extraction_status],
              )}
            >
              {EXTRACTION_LABEL[cluster.extraction_status]}
            </span>
          </button>
        ))}
        {!isLoading && day?.is_clustered && (clusters?.length ?? 0) === 0 && (
          <p className="px-5 py-8 text-center text-sm text-ink-muted">No clusters for this day.</p>
        )}
      </div>
    </Card>
  );
}
