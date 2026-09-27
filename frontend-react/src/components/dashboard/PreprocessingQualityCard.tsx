// Quality signals for the four LLM/embedding-driven preprocessing phases
// (condensing, entities, contextualization, insights) — meant to be
// watched *while* a large backlog run is in progress, not just checked
// once at the end, so a badly-mistuned threshold or a broken phase
// surfaces early. Each phase shows "processed" (how many clusters have
// actually reached it) alongside its rate/average, since a rate over 0
// processed clusters is meaningless, not zero — an empty dashed dial,
// not a 0% one.

import type { ReactNode } from "react";
import { Card, CardBody, CardHeader } from "@/components/ui/Card";
import { usePreprocessingQuality } from "@/hooks/usePipeline";
import { cn } from "@/lib/utils";

function Dial({ pct, display }: { pct: number | null; display: string }) {
  return (
    <div
      className={cn("relative size-13 shrink-0 rounded-full", pct == null && "opacity-70")}
      style={
        pct == null
          ? { background: "repeating-conic-gradient(var(--color-border) 0deg 8deg, transparent 8deg 16deg)" }
          : { background: `conic-gradient(var(--color-brand) calc(${pct} * 1%), var(--color-border) 0)` }
      }
    >
      <div className="absolute inset-[5px] flex items-center justify-center rounded-full bg-surface">
        <span className={cn("tabular font-mono text-[11.5px] font-semibold", pct == null ? "text-ink-muted" : "text-ink")}>
          {display}
        </span>
      </div>
    </div>
  );
}

function Meter({
  label,
  processed,
  pct,
  display,
  children,
}: {
  label: string;
  processed: number;
  pct: number | null;
  display: string;
  children: ReactNode;
}) {
  return (
    <div className="flex items-center gap-3 rounded-[3px] bg-surface-raised p-2.5">
      <Dial pct={pct} display={display} />
      <div className="min-w-0">
        <p className="text-[10.5px] font-semibold uppercase tracking-wide text-ink-muted">
          {label} <span className="font-normal normal-case">· {processed} processed</span>
        </p>
        <p className="mt-1 truncate text-[11px] text-ink-secondary">{children}</p>
      </div>
    </div>
  );
}

export function PreprocessingQualityCard() {
  const { data } = usePreprocessingQuality();

  return (
    <Card>
      <CardHeader
        title="Preprocessing quality"
        subtitle="Live signals to watch while the backlog runs — catch a mistuned threshold early"
      />
      <CardBody className="grid grid-cols-1 gap-2.5 sm:grid-cols-2">
        <Meter
          label="Condensing"
          processed={data?.condensing.clusters_processed ?? 0}
          pct={null}
          display={data?.condensing.avg_chunks_per_cluster != null ? String(data.condensing.avg_chunks_per_cluster) : "—"}
        >
          {data?.condensing.clusters_processed
            ? <>avg <b className="tabular font-mono text-ink">{data.condensing.avg_titles_per_cluster}</b> titles/cl · <b className="tabular font-mono text-ink">{data.condensing.zero_chunk_clusters}</b> zero-chunk</>
            : "awaiting phase 3"}
        </Meter>

        <Meter
          label="Entities"
          processed={data?.entities.clusters_processed ?? 0}
          pct={null}
          display={data?.entities.avg_entities_per_cluster != null ? String(data.entities.avg_entities_per_cluster) : "—"}
        >
          {data?.entities.clusters_processed
            ? <><b className="tabular font-mono text-ink">{data.entities.total_canonical_entities}</b> canonical entities total</>
            : "awaiting phase 4"}
        </Meter>

        <Meter
          label="Contextualization"
          processed={data?.contextualization.clusters_processed ?? 0}
          pct={data?.contextualization.hit_rate_pct ?? null}
          display={data?.contextualization.hit_rate_pct != null ? `${data.contextualization.hit_rate_pct}%` : "—"}
        >
          {data?.contextualization.clusters_processed
            ? <>hit rate · avg top score <b className="tabular font-mono text-ink">{data.contextualization.avg_top_score ?? "—"}</b></>
            : "awaiting phase 5"}
        </Meter>

        <Meter
          label="Insights"
          processed={data?.insights.clusters_processed ?? 0}
          pct={data?.insights.yield_rate_pct ?? null}
          display={data?.insights.yield_rate_pct != null ? `${data.insights.yield_rate_pct}%` : "—"}
        >
          {data?.insights.clusters_processed
            ? <><b className="tabular font-mono text-ink">{data.insights.total_insights}</b> insights · avg confidence <b className="tabular font-mono text-ink">{data.insights.avg_confidence ?? "—"}%</b></>
            : "awaiting phase 6"}
        </Meter>
      </CardBody>
    </Card>
  );
}
