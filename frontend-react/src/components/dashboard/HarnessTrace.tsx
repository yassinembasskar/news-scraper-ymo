// The title-gate and preprocessing jobs as one continuous signal path,
// styled as a wiring-harness continuity trace: each stage is a terminal
// pin, joined by a wire that reads solid once a stage has real completed
// work behind it, copper and flowing while it's the one currently being
// worked on, and open/grey where nothing has reached it yet. Replaces
// the earlier two-card grid (TitleGateStageCard + PreprocessingStageCard,
// 2026-09-16 redesign) — the two jobs are still independently
// triggerable (separate Run/Stop controls in the header), but they're
// one visual sequence, since a story only reaches insights by passing
// through the gate first.

import { Play, Square } from "lucide-react";
import { Card, CardBody, CardHeader } from "@/components/ui/Card";
import { Button } from "@/components/ui/Button";
import { ProgressBar } from "@/components/ui/ProgressBar";
import { useStageActions, useTitleGateStatus, usePreprocessingStatus } from "@/hooks/usePipeline";
import { formatDuration } from "@/lib/format";
import { cn } from "@/lib/utils";
import type { PreprocessingPhase, PreprocessingStatusResponse } from "@/api/types";

type NodeState = "done" | "live" | "pending";

const PHASES: {
  key: PreprocessingPhase;
  label: string;
  unit: string;
  doneKey: keyof PreprocessingStatusResponse;
}[] = [
  { key: "clustering", label: "Cluster", unit: "days", doneKey: "days_done" },
  { key: "extraction", label: "Extract", unit: "articles", doneKey: "articles_extracted_done" },
  { key: "condensing", label: "Condense", unit: "clusters", doneKey: "clusters_condensed_done" },
  { key: "entities", label: "Entities", unit: "clusters", doneKey: "clusters_entities_done" },
  { key: "contextualization", label: "Contextualize", unit: "clusters", doneKey: "clusters_contextualized_done" },
  { key: "insights", label: "Insights", unit: "clusters", doneKey: "clusters_insights_done" },
];

function Pin({ n, state }: { n: number; state: NodeState }) {
  return (
    <div
      className={cn(
        "relative flex size-7.5 shrink-0 items-center justify-center rounded-full border-[2.5px] font-mono text-[11px] font-semibold",
        state === "done" && "border-status-good bg-status-good/10 text-status-good",
        state === "live" && "border-brand bg-surface text-brand",
        state === "pending" && "border-border bg-surface text-ink-muted",
      )}
      style={state === "live" ? { boxShadow: "0 0 0 3px color-mix(in srgb, var(--color-brand) 20%, transparent)" } : undefined}
    >
      {n}
      {state === "live" && (
        <span className="harness-live-pin absolute -inset-[3px] animate-[harness-pulse_1.6s_ease-out_infinite] rounded-full border border-brand" />
      )}
    </div>
  );
}

function Wire({ state }: { state: NodeState }) {
  return (
    <div
      className={cn(
        "mt-3.75 h-[3px] flex-1",
        state === "pending" && "bg-border",
        state === "done" && "bg-status-good",
        state === "live" &&
          "harness-live-wire bg-[length:16px_100%] bg-[linear-gradient(90deg,var(--color-status-good)_50%,var(--color-brand)_50%)] animate-[harness-flow_1.4s_linear_infinite]",
      )}
    />
  );
}

function Node({
  n,
  label,
  state,
  metric,
  first,
}: {
  n: number;
  label: string;
  state: NodeState;
  metric: string;
  first?: boolean;
}) {
  return (
    <div className="flex flex-1 items-start">
      {!first && <Wire state={state} />}
      <div className="flex w-28 flex-col items-center px-1 text-center">
        <Pin n={n} state={state} />
        <p
          className={cn(
            "mt-2 text-[11.5px] font-semibold tracking-wide",
            state === "live" ? "text-brand" : state === "done" ? "text-ink" : "text-ink-muted",
          )}
        >
          {label}
        </p>
        <p className="mt-0.5 text-[10.5px] text-ink-muted">{metric}</p>
      </div>
    </div>
  );
}

export function HarnessTrace() {
  const { data: gate } = useTitleGateStatus();
  const { data: prep } = usePreprocessingStatus();
  const gateActions = useStageActions("titleGate");
  const prepActions = useStageActions("preprocessing");

  const gateRunning = gate?.in_progress ?? false;
  const prepRunning = prep?.in_progress ?? false;

  const gateState: NodeState = gateRunning ? "live" : (gate?.gated_total ?? 0) > 0 ? "done" : "pending";

  const activeLine =
    gateRunning && gate
      ? {
          label: "Title gate",
          text: `${gate.processed}/${gate.total} — day ${gate.current_day ?? "—"}`,
          fraction: gate.total > 0 ? gate.processed / gate.total : 0,
          eta: gate.estimated_minutes_remaining,
        }
      : prepRunning && prep
        ? {
            label: PHASES.find((p) => p.key === prep.phase)?.label ?? "Preprocessing",
            text: `${prep.phase_processed}/${prep.phase_total}`,
            fraction: prep.phase_total > 0 ? prep.phase_processed / prep.phase_total : 0,
            eta: prep.estimated_minutes_remaining,
          }
        : null;

  return (
    <Card>
      <CardHeader
        title="Signal path — title gate to insights"
        subtitle="Each pin is one stage a story passes through before it becomes an insight"
        action={
          <div className="flex items-center gap-4">
            <div className="flex items-center gap-1.5">
              <span className="text-[10px] uppercase tracking-wide text-ink-muted">Gate</span>
              {gateRunning ? (
                <Button size="sm" variant="danger" onClick={() => gateActions.stop.mutate()} loading={gateActions.stop.isPending}>
                  <Square className="size-3.5" />
                </Button>
              ) : (
                <Button size="sm" variant="secondary" onClick={() => gateActions.trigger.mutate()} loading={gateActions.trigger.isPending}>
                  <Play className="size-3.5" />
                </Button>
              )}
            </div>
            <div className="flex items-center gap-1.5">
              <span className="text-[10px] uppercase tracking-wide text-ink-muted">Preprocessing</span>
              {prepRunning ? (
                <Button size="sm" variant="danger" onClick={() => prepActions.stop.mutate()} loading={prepActions.stop.isPending}>
                  <Square className="size-3.5" />
                </Button>
              ) : (
                <Button size="sm" variant="secondary" onClick={() => prepActions.trigger.mutate()} loading={prepActions.trigger.isPending}>
                  <Play className="size-3.5" />
                </Button>
              )}
            </div>
          </div>
        }
      />
      <CardBody>
        <div className="flex items-start overflow-x-auto pb-1 pt-2">
          <Node n={1} label="Title Gate" state={gateState} metric={`${gate?.gated_total ?? 0}/${gate?.articles_total ?? 0} gated`} first />
          {PHASES.map((phase, i) => {
            const done = ((prep?.[phase.doneKey] as number | undefined) ?? 0) > 0;
            const state: NodeState = prepRunning && prep?.phase === phase.key ? "live" : done ? "done" : "pending";
            const doneCount = (prep?.[phase.doneKey] as number | undefined) ?? 0;
            return <Node key={phase.key} n={i + 2} label={phase.label} state={state} metric={`${doneCount} ${phase.unit}`} />;
          })}
        </div>

        <div className="mt-1 flex flex-wrap gap-4 border-t border-border pt-3 text-[10.5px] text-ink-muted">
          <span className="flex items-center gap-1.5">
            <span className="size-1.5 rounded-full bg-status-good" /> Continuity — done
          </span>
          <span className="flex items-center gap-1.5">
            <span className="size-1.5 rounded-full bg-brand" /> Live — in progress
          </span>
          <span className="flex items-center gap-1.5">
            <span className="size-1.5 rounded-full bg-border" /> Open — not yet reached
          </span>
        </div>

        {activeLine && (
          <div className="mt-3 space-y-1.5">
            <div className="flex items-center justify-between text-xs">
              <span className="font-medium text-ink">{activeLine.label}</span>
              <span className="tabular font-mono text-ink-secondary">
                {activeLine.text}
                {activeLine.eta != null && ` · ${formatDuration(activeLine.eta)} left`}
              </span>
            </div>
            <ProgressBar value={activeLine.fraction} />
          </div>
        )}
      </CardBody>
    </Card>
  );
}
