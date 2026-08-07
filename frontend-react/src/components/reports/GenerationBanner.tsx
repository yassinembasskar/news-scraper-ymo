import { Square } from "lucide-react";
import { Card, CardBody } from "@/components/ui/Card";
import { ProgressBar } from "@/components/ui/ProgressBar";
import { Button } from "@/components/ui/Button";
import { useReportGenerateStatus } from "@/hooks/useReports";
import { formatDuration } from "@/lib/format";

export function GenerationBanner({ onStop, stopping }: { onStop: () => void; stopping: boolean }) {
  const { data: status } = useReportGenerateStatus();

  if (!status?.in_progress) return null;

  const fraction = status.total > 0 ? status.processed / status.total : 0;

  return (
    <Card className="border-brand/30">
      <CardBody className="space-y-2">
        <div className="flex items-center justify-between">
          <p className="text-sm text-ink">
            Generating report for <strong>{status.report_date ?? "?"}</strong>: {status.processed}/{status.total}{" "}
            articles
            {status.estimated_minutes_remaining != null &&
              ` — about ${formatDuration(status.estimated_minutes_remaining)} left`}
          </p>
          <Button size="sm" variant="danger" onClick={onStop} loading={stopping}>
            <Square className="size-3.5" />
            Stop
          </Button>
        </div>
        <ProgressBar value={fraction} />
      </CardBody>
    </Card>
  );
}
