import { Play, Square } from "lucide-react";
import { Card, CardBody, CardHeader } from "@/components/ui/Card";
import { Button } from "@/components/ui/Button";
import { ProgressBar } from "@/components/ui/ProgressBar";
import { ProgressRing } from "@/components/ui/ProgressRing";
import type { StageName } from "@/hooks/usePipeline";
import { useStageActions, useStageStatus } from "@/hooks/usePipeline";
import { formatDuration } from "@/lib/format";

export function PipelineStageCard({
  stage,
  title,
  ringColor,
  completed,
  total,
  breakdown,
}: {
  stage: StageName;
  title: string;
  ringColor: string;
  /** Count of the "done" bucket (successful/embedded/scored) out of `total`. */
  completed: number;
  /** This stage's own candidate pool size (see backend stats.get_pipeline_stats()). */
  total: number;
  breakdown: { label: string; value: number }[];
}) {
  const { data: status } = useStageStatus(stage);
  const { trigger, stop } = useStageActions(stage);

  const percent = total > 0 ? (completed / total) * 100 : 0;
  const running = status?.in_progress ?? false;
  const runFraction = running && status && status.total > 0 ? status.processed / status.total : 0;

  return (
    <Card>
      <CardHeader
        title={title}
        subtitle={`${completed} / ${total}`}
        action={
          running ? (
            <Button size="sm" variant="danger" onClick={() => stop.mutate()} loading={stop.isPending}>
              <Square className="size-3.5" />
              Stop
            </Button>
          ) : (
            <Button size="sm" variant="secondary" onClick={() => trigger.mutate()} loading={trigger.isPending}>
              <Play className="size-3.5" />
              Run
            </Button>
          )
        }
      />
      <CardBody className="flex items-center gap-5">
        <ProgressRing value={percent} color={ringColor} size={80} strokeWidth={7} />
        <div className="flex-1 space-y-1.5">
          {breakdown.map(({ label, value }) => (
            <div key={label} className="flex items-center justify-between text-xs">
              <span className="text-ink-secondary">{label}</span>
              <span className="font-medium text-ink">{value}</span>
            </div>
          ))}
        </div>
      </CardBody>
      {running && status && (
        <div className="border-t border-border px-5 py-3">
          <div className="mb-1.5 flex items-center justify-between text-xs text-ink-secondary">
            <span>
              {status.processed}/{status.total} this run
            </span>
            {status.estimated_minutes_remaining != null && (
              <span>{formatDuration(status.estimated_minutes_remaining)} left</span>
            )}
          </div>
          <ProgressBar value={runFraction} />
        </div>
      )}
    </Card>
  );
}
