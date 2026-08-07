import { CalendarDays, Clock, Newspaper, Tag, TrendingUp } from "lucide-react";
import { Card } from "@/components/ui/Card";
import { useOverview } from "@/hooks/usePipeline";
import { formatRelativeOrDate } from "@/lib/format";

function StatCard({
  icon: Icon,
  label,
  value,
}: {
  icon: typeof Newspaper;
  label: string;
  value: string | number;
}) {
  return (
    <Card className="flex items-center gap-3.5 px-4 py-4">
      <div className="flex size-10 shrink-0 items-center justify-center rounded-xl bg-brand/10 text-brand">
        <Icon className="size-5" />
      </div>
      <div className="min-w-0">
        <p className="truncate text-xs text-ink-muted">{label}</p>
        <p className="truncate text-lg font-semibold text-ink">{value}</p>
      </div>
    </Card>
  );
}

export function StatCards() {
  const { data: overview } = useOverview();

  return (
    <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-5">
      <StatCard icon={Newspaper} label="Articles today" value={overview?.today_total ?? "—"} />
      <StatCard icon={Tag} label="Top keyword today" value={overview?.today_top_keyword ?? "—"} />
      <StatCard icon={TrendingUp} label="Articles this month" value={overview?.month_total ?? "—"} />
      <StatCard icon={CalendarDays} label="Top keyword this month" value={overview?.month_top_keyword ?? "—"} />
      <StatCard icon={Clock} label="Last updated" value={formatRelativeOrDate(overview?.last_scraped)} />
    </div>
  );
}
