import { useState } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { Card, CardBody, CardHeader } from "@/components/ui/Card";
import { cn } from "@/lib/utils";
import { useDayChart, useMonthChart, useWeekChart } from "@/hooks/useCharts";
import type { ChartBucket } from "@/api/types";

type Range = "day" | "week" | "month";

function isoWeekOf(date: Date): { year: number; week: number } {
  const d = new Date(Date.UTC(date.getFullYear(), date.getMonth(), date.getDate()));
  const dayNum = d.getUTCDay() || 7;
  d.setUTCDate(d.getUTCDate() + 4 - dayNum);
  const yearStart = new Date(Date.UTC(d.getUTCFullYear(), 0, 1));
  const week = Math.ceil(((d.getTime() - yearStart.getTime()) / 86400000 + 1) / 7);
  return { year: d.getUTCFullYear(), week };
}

const today = new Date();
const todayIso = today.toISOString().slice(0, 10);
const { year: defaultIsoYear, week: defaultIsoWeek } = isoWeekOf(today);

export function ChartCard({ onSelectBucket }: { onSelectBucket: (bucket: ChartBucket | null) => void }) {
  const [range, setRange] = useState<Range>("day");
  const [day, setDay] = useState(todayIso);
  const [isoYear, setIsoYear] = useState(defaultIsoYear);
  const [isoWeek, setIsoWeek] = useState(defaultIsoWeek);
  const [year, setYear] = useState(today.getFullYear());
  const [month, setMonth] = useState(today.getMonth() + 1);
  const [selectedLabel, setSelectedLabel] = useState<string | null>(null);

  const dayQuery = useDayChart(day);
  const weekQuery = useWeekChart(isoYear, isoWeek);
  const monthQuery = useMonthChart(year, month);

  const chartData =
    range === "day" ? dayQuery.data : range === "week" ? weekQuery.data : monthQuery.data;

  const handleBarClick = (data: { payload?: ChartBucket }) => {
    const bucket = data.payload;
    if (!bucket) return;
    if (selectedLabel === bucket.label) {
      setSelectedLabel(null);
      onSelectBucket(null);
    } else {
      setSelectedLabel(bucket.label);
      onSelectBucket(bucket);
    }
  };

  return (
    <Card>
      <CardHeader
        title="Article activity"
        subtitle={chartData?.period_label}
        action={
          <div className="flex gap-1 rounded-lg bg-page p-1">
            {(["day", "week", "month"] as Range[]).map((r) => (
              <button
                key={r}
                onClick={() => setRange(r)}
                className={cn(
                  "rounded-md px-2.5 py-1 text-xs font-medium capitalize transition-colors",
                  range === r ? "bg-surface text-ink shadow-sm" : "text-ink-muted hover:text-ink-secondary",
                )}
              >
                {r}
              </button>
            ))}
          </div>
        }
      />
      <CardBody>
        <div className="mb-4 flex flex-wrap items-center gap-2">
          {range === "day" && (
            <input
              type="date"
              value={day}
              onChange={(e) => setDay(e.target.value)}
              className="rounded-lg border border-border bg-surface-raised px-2.5 py-1.5 text-sm text-ink"
            />
          )}
          {range === "week" && (
            <>
              <input
                type="number"
                value={isoYear}
                onChange={(e) => setIsoYear(Number(e.target.value))}
                className="w-24 rounded-lg border border-border bg-surface-raised px-2.5 py-1.5 text-sm text-ink"
              />
              <input
                type="number"
                min={1}
                max={53}
                value={isoWeek}
                onChange={(e) => setIsoWeek(Number(e.target.value))}
                className="w-20 rounded-lg border border-border bg-surface-raised px-2.5 py-1.5 text-sm text-ink"
              />
            </>
          )}
          {range === "month" && (
            <>
              <input
                type="number"
                value={year}
                onChange={(e) => setYear(Number(e.target.value))}
                className="w-24 rounded-lg border border-border bg-surface-raised px-2.5 py-1.5 text-sm text-ink"
              />
              <select
                value={month}
                onChange={(e) => setMonth(Number(e.target.value))}
                className="rounded-lg border border-border bg-surface-raised px-2.5 py-1.5 text-sm text-ink"
              >
                {Array.from({ length: 12 }, (_, i) => i + 1).map((m) => (
                  <option key={m} value={m}>
                    {new Date(2000, m - 1, 1).toLocaleString("en", { month: "long" })}
                  </option>
                ))}
              </select>
            </>
          )}
        </div>

        <ResponsiveContainer width="100%" height={280}>
          <BarChart data={chartData?.buckets ?? []} barGap={2}>
            <CartesianGrid strokeDasharray="3 3" stroke="var(--color-border)" vertical={false} />
            <XAxis dataKey="label" stroke="var(--color-ink-muted)" fontSize={12} tickLine={false} />
            <YAxis stroke="var(--color-ink-muted)" fontSize={12} tickLine={false} axisLine={false} width={32} />
            <Tooltip
              cursor={{ fill: "var(--color-border)", opacity: 0.4 }}
              contentStyle={{
                background: "var(--color-surface-raised)",
                border: "1px solid var(--color-border)",
                borderRadius: 8,
                fontSize: 12,
                color: "var(--color-ink)",
              }}
            />
            <Legend wrapperStyle={{ fontSize: 12, color: "var(--color-ink-secondary)" }} />
            <Bar
              dataKey="en"
              name="English"
              stackId="lang"
              fill="var(--color-series-en)"
              radius={[0, 0, 0, 0]}
              onClick={handleBarClick}
              cursor="pointer"
            />
            <Bar
              dataKey="fr"
              name="French"
              stackId="lang"
              fill="var(--color-series-fr)"
              onClick={handleBarClick}
              cursor="pointer"
            />
            <Bar
              dataKey="ar"
              name="Arabic"
              stackId="lang"
              fill="var(--color-series-ar)"
              radius={[4, 4, 0, 0]}
              onClick={handleBarClick}
              cursor="pointer"
            />
          </BarChart>
        </ResponsiveContainer>
        {selectedLabel && (
          <p className="mt-2 text-xs text-ink-muted">
            Filtering articles by <strong className="text-ink">{selectedLabel}</strong> — click the bar again to
            clear.
          </p>
        )}
      </CardBody>
    </Card>
  );
}
