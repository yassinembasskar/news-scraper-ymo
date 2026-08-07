import { useState } from "react";
import { AlertTriangle, Sparkles } from "lucide-react";
import { Card, CardBody } from "@/components/ui/Card";
import { Button } from "@/components/ui/Button";
import { CalendarGrid } from "@/components/reports/CalendarGrid";
import { GenerationBanner } from "@/components/reports/GenerationBanner";
import { InsightCard } from "@/components/reports/InsightCard";
import { useDailyReport, useReportActions, useReportCalendar, useReportGenerateStatus } from "@/hooks/useReports";

const today = new Date();

export default function Reports() {
  const [year, setYear] = useState(today.getFullYear());
  const [month, setMonth] = useState(today.getMonth() + 1);
  const [selectedDate, setSelectedDate] = useState<string | null>(null);

  const { data: calendar } = useReportCalendar(year, month);
  const { data: generateStatus } = useReportGenerateStatus();
  const { data: report } = useDailyReport(selectedDate ?? undefined);
  const { generate, stop } = useReportActions(selectedDate ?? "", year, month);

  const marked = Object.fromEntries((calendar?.days ?? []).map((d) => [d.report_date, d]));
  const minScore = calendar?.min_relativity_score ?? 60;
  const isGeneratingThisDay = generateStatus?.in_progress && generateStatus.report_date === selectedDate;
  const isGeneratingOtherDay = generateStatus?.in_progress && generateStatus.report_date !== selectedDate;

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
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold text-ink">Daily Insight Reports</h1>
        <p className="text-sm text-ink-muted">
          🔴 N = relevant insights found · ✅ = generated, none relevant · ⚠️ = generation had a problem · · N =
          articles scored ≥ {minScore}% awaiting a report
        </p>
      </div>

      <GenerationBanner onStop={() => stop.mutate()} stopping={stop.isPending} />

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
                <p className="text-sm text-ink-muted">Pick a day on the calendar to see or generate its report.</p>
              </CardBody>
            </Card>
          ) : isGeneratingThisDay ? (
            <Card>
              <CardBody>
                <p className="text-sm text-ink-muted">This day's report is being generated right now — see the progress above.</p>
              </CardBody>
            </Card>
          ) : !report?.generated ? (
            <Card>
              <CardBody className="space-y-3">
                <p className="text-sm text-ink-muted">
                  {(marked[selectedDate]?.candidate_count ?? 0)} article(s) scored ≥ {minScore}% relativity on this
                  day. No report has been generated for this day yet.
                </p>
                {isGeneratingOtherDay ? (
                  <p className="text-xs text-status-warning">
                    Another day's report is currently generating — only one runs at a time.
                  </p>
                ) : (
                  <Button onClick={() => generate.mutate()} loading={generate.isPending}>
                    <Sparkles className="size-4" />
                    Generate report
                  </Button>
                )}
              </CardBody>
            </Card>
          ) : (
            <>
              <Card>
                <CardBody className="flex items-center justify-between">
                  <div>
                    <p className="text-sm font-semibold text-ink">Report — {selectedDate}</p>
                    <p className="text-xs text-ink-muted">
                      Generated {(report.generated_at ?? "").replace("T", " ").slice(0, 19)} ·{" "}
                      {report.articles_processed} article(s) processed · {report.relevant_count} relevant
                    </p>
                  </div>
                  <Button variant="secondary" size="sm" onClick={() => generate.mutate()} loading={generate.isPending}>
                    Regenerate
                  </Button>
                </CardBody>
              </Card>

              {report.run_status !== "success" && (
                <Card className="border-status-critical/40">
                  <CardBody className="space-y-2">
                    <div className="flex items-center gap-2 text-status-critical">
                      <AlertTriangle className="size-4 shrink-0" />
                      <p className="text-sm font-medium">
                        {report.run_status === "stopped"
                          ? "Generation was stopped before it finished."
                          : report.run_status === "partial"
                            ? "Some articles failed while generating this report — the insights below may be incomplete."
                            : "This report failed to generate — no article could be assessed."}
                      </p>
                    </div>
                    {report.errors.length > 0 && (
                      <ul className="list-disc space-y-1 pl-6 text-xs text-ink-secondary">
                        {report.errors.slice(0, 5).map((err, i) => (
                          <li key={i}>{err}</li>
                        ))}
                        {report.errors.length > 5 && <li>+ {report.errors.length - 5} more</li>}
                      </ul>
                    )}
                  </CardBody>
                </Card>
              )}

              {report.insights.length === 0 ? (
                <Card>
                  <CardBody>
                    <p className="text-sm text-ink-muted">
                      {report.run_status === "success"
                        ? "There are no relevant insights for this day."
                        : "No insights were produced — see the problem above."}
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
