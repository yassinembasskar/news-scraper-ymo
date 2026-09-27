import { Card } from "@/components/ui/Card";
import { useOverview } from "@/hooks/usePipeline";
import { formatRelativeOrDate } from "@/lib/format";

function GaugeTile({ label, value, unit }: { label: string; value: string | number; unit?: string }) {
  return (
    <Card className="px-4 py-3.5">
      <p className="text-[10.5px] font-semibold uppercase tracking-[0.1em] text-ink-muted">{label}</p>
      <p className="mt-1.5 flex items-baseline gap-1.5">
        <span className="tabular font-mono text-xl font-medium text-ink">{value}</span>
        {unit && <span className="text-[11px] text-ink-muted">{unit}</span>}
      </p>
    </Card>
  );
}

export function StatCards() {
  const { data: overview } = useOverview();

  return (
    <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5">
      <GaugeTile label="Articles today" value={overview?.today_total ?? "—"} unit="found" />
      <GaugeTile label="Top keyword today" value={overview?.today_top_keyword ?? "—"} />
      <GaugeTile label="Articles this month" value={overview?.month_total ?? "—"} unit="found" />
      <GaugeTile label="Top keyword this month" value={overview?.month_top_keyword ?? "—"} />
      <GaugeTile label="Last update run" value={formatRelativeOrDate(overview?.last_scraped)} />
    </div>
  );
}
