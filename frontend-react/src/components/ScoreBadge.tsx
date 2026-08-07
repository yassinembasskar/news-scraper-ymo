// Relativity score badge — thresholds match the original Streamlit app
// (>=80 high, >=60 medium, below critical). Status color is never the
// only signal: an icon + the numeric label always ship together, per the
// dataviz skill's status-palette rule (color-only status reads unreliable
// for CVD readers, and "warning" specifically is sub-3:1 contrast on the
// light surface by design).

import { AlertTriangle, CheckCircle2, XCircle } from "lucide-react";
import { cn } from "@/lib/utils";
import type { ScoredStatus } from "@/api/types";

export const RELATIVITY_HIGH_THRESHOLD = 80;
export const RELATIVITY_MEDIUM_THRESHOLD = 60;

export function ScoreBadge({
  score,
  scoredStatus,
  className,
}: {
  score: number | null;
  scoredStatus: ScoredStatus;
  className?: string;
}) {
  if (scoredStatus !== "Scored" || score === null) {
    return (
      <span className={cn("inline-flex items-center gap-1.5 text-xs font-medium text-ink-muted", className)}>
        {scoredStatus}
      </span>
    );
  }

  const tier =
    score >= RELATIVITY_HIGH_THRESHOLD ? "good" : score >= RELATIVITY_MEDIUM_THRESHOLD ? "warning" : "critical";

  const config = {
    good: { color: "text-status-good", bg: "bg-status-good/10", Icon: CheckCircle2 },
    warning: { color: "text-status-warning", bg: "bg-status-warning/15", Icon: AlertTriangle },
    critical: { color: "text-status-critical", bg: "bg-status-critical/10", Icon: XCircle },
  }[tier];

  const Icon = config.Icon;

  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-semibold",
        config.color,
        config.bg,
        className,
      )}
    >
      <Icon className="size-3.5" />
      {score}
    </span>
  );
}
