import { useState } from "react";
import { AlertTriangle } from "lucide-react";
import { Card, CardBody } from "@/components/ui/Card";
import { StatCards } from "@/components/dashboard/StatCards";
import { UpdateButton } from "@/components/dashboard/UpdateButton";
import { HarnessTrace } from "@/components/dashboard/HarnessTrace";
import { PreprocessingQualityCard } from "@/components/dashboard/PreprocessingQualityCard";
import { DayClusterGrid } from "@/components/dashboard/DayClusterGrid";
import { ClusterPanel } from "@/components/dashboard/ClusterPanel";
import { ClusterDetail } from "@/components/dashboard/ClusterDetail";
import { CalendarGrid } from "@/components/reports/CalendarGrid";
import { InsightCard } from "@/components/reports/InsightCard";
import { useDailyReport, useReportCalendar } from "@/hooks/useReports";
import type { Cluster } from "@/api/types";

const today = new Date();

// Daily LLM Insight reports live here rather than on their own page —
// once the old per-article relativity scorer was retired (2026-09-15,
// replaced by cluster-level condensation/contextualization), there was
// no more standalone "Scoring" page slot for reporting to occupy
// separately; the report calendar + insights now sit in that same spot
// on the main dashboard instead. Read-only: insight generation is
// preprocessing's own phase 6 now (see HarnessTrace's progress for
// that), not a per-day "Generate" button here.
function ReportsSection() {
  const [year, setYear] = useState(today.getFullYear());
  const [month, setMonth] = useState(today.getMonth() + 1);
  const [selectedDate, setSelectedDate] = useState<string | null>(null);

  const { data: calendar } = useReportCalendar(year, month);
  const { data: report } = useDailyReport(selectedDate ?? undefined);

  const marked = Object.fromEntries((calendar?.days ?? []).map((d) => [d.report_date, d]));

  const shiftMonth = (delta: number) => {
    let m = month + delta;
    let y = year;
    if (m < 1) {
      m = 12;
      y -= 1;
    } else if (m > 12) {
      m = 1;
      y += 1;
    }
    setYear(y);
    setMonth(m);
    setSelectedDate(null);
  };

  return (
    <div className="space-y-4">
      <div>
        <h2 className="font-display text-xl font-bold uppercase tracking-wide text-ink">Insight Feed</h2>
        <p className="text-sm text-ink-muted">
          🔴 N = relevant insights found · ✅ = generated, none relevant · · N = clusters still queued for insight
          generation (part of Preprocessing, see above)
        </p>
      </div>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-[360px_1fr]">
        <Card>
          <CardBody>
            <CalendarGrid
              year={year}
              month={month}
              marked={marked}
              selectedDate={selectedDate}
              onSelectDate={setSelectedDate}
              onShiftMonth={shiftMonth}
            />
          </CardBody>
        </Card>

        <div className="space-y-4">
          {!selectedDate ? (
            <Card>
              <CardBody>
                <p className="text-sm text-ink-muted">Pick a day on the calendar to see its insights.</p>
              </CardBody>
            </Card>
          ) : !report || report.total_clusters === 0 ? (
            <Card>
              <CardBody>
                <p className="text-sm text-ink-muted">No clusters on this day yet.</p>
              </CardBody>
            </Card>
          ) : (
            <>
              <Card>
                <CardBody className="flex items-center justify-between">
                  <div>
                    <p className="text-sm font-semibold text-ink">Report — {selectedDate}</p>
                    <p className="text-xs text-ink-muted">
                      {report.insights_generated_clusters}/{report.total_clusters} cluster(s) processed ·{" "}
                      {report.insights.length} relevant insight(s)
                    </p>
                  </div>
                </CardBody>
              </Card>

              {report.pending_clusters > 0 && (
                <Card className="border-status-warning/40">
                  <CardBody className="flex items-center gap-2 text-status-warning">
                    <AlertTriangle className="size-4 shrink-0" />
                    <p className="text-sm">
                      {report.pending_clusters} cluster(s) haven't reached insight generation yet — this day's
                      report is still incomplete. Running Preprocessing again will pick them up.
                    </p>
                  </CardBody>
                </Card>
              )}

              {report.insights.length === 0 ? (
                <Card>
                  <CardBody>
                    <p className="text-sm text-ink-muted">
                      {report.insights_generated_clusters > 0
                        ? "No relevant insights found for this day's processed clusters."
                        : "No clusters on this day have been through insight generation yet."}
                    </p>
                  </CardBody>
                </Card>
              ) : (
                <div className="space-y-3">
                  {report.insights.map((insight, i) => (
                    <InsightCard key={i} insight={insight} />
                  ))}
                </div>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
}

export default function Dashboard() {
  const [selectedDay, setSelectedDay] = useState<string | null>(null);
  const [selectedCluster, setSelectedCluster] = useState<Cluster | null>(null);

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="font-display text-3xl font-bold uppercase tracking-wide text-ink">Dashboard</h1>
          <p className="text-sm text-ink-muted">Automotive-industry news, extraction, and preprocessing.</p>
        </div>
        <UpdateButton />
      </div>

      <StatCards />

      <HarnessTrace />

      <PreprocessingQualityCard />

      <DayClusterGrid
        selectedDay={selectedDay}
        onSelectDay={(date) => {
          setSelectedDay(date);
          setSelectedCluster(null);
        }}
      />

      {selectedDay && !selectedCluster && (
        <ClusterPanel
          date={selectedDay}
          onClose={() => setSelectedDay(null)}
          onSelectCluster={setSelectedCluster}
        />
      )}

      {selectedCluster && <ClusterDetail cluster={selectedCluster} onBack={() => setSelectedCluster(null)} />}

      <ReportsSection />
    </div>
  );
}
