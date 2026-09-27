import { cn } from "@/lib/utils";
import type { DailyInsightItem } from "@/api/types";

const EFFECT_STRIPE: Record<DailyInsightItem["effect"], string> = {
  positive: "bg-status-good",
  negative: "bg-status-critical",
  neutral: "bg-ink-muted",
};

const EFFECT_CHIP: Record<DailyInsightItem["effect"], string> = {
  positive: "bg-status-good/10 text-status-good",
  negative: "bg-status-critical/10 text-status-critical",
  neutral: "bg-surface-raised text-ink-muted",
};

export function InsightCard({ insight }: { insight: DailyInsightItem }) {
  return (
    <div className="grid grid-cols-[3px_1fr] gap-3 rounded-[3px] border border-border bg-surface p-3.5">
      <div className={cn("rounded-full", EFFECT_STRIPE[insight.effect])} />
      <div>
        <p className="text-[13.5px] font-medium text-ink">{insight.title}</p>
        <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
          <span className={cn("rounded-full px-2 py-0.5 font-mono text-[10px] font-medium uppercase", EFFECT_CHIP[insight.effect])}>
            {insight.effect}
          </span>
          <span className="rounded-full bg-surface-raised px-2 py-0.5 font-mono text-[10px] text-ink-secondary">
            {insight.concerned_param}
          </span>
          <span className="tabular font-mono text-[10px] text-ink-muted">confidence {insight.confidence}%</span>
        </div>
        {insight.recommended_action && (
          <p className="mt-1.5 text-[12.5px] leading-relaxed text-ink-secondary">{insight.recommended_action}</p>
        )}
        <p className="mt-2 text-[10.5px] text-ink-muted">
          story: <span className="text-ink-secondary">{insight.representative_title}</span>
          {insight.chunk_indices.length > 0 && (
            <> · <span className="font-mono">{insight.chunk_indices.length}</span> supporting chunk(s)</>
          )}
        </p>
      </div>
    </div>
  );
}
