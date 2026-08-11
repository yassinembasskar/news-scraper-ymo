// Generic pipeline-stage status badge (extraction/embedding/scoring) —
// same icon+label-never-color-alone rule as ScoreBadge.tsx, generalized
// across the three status enums since they share the same five states in
// spirit: not started / pending / done / failed / expired.

import { CheckCircle2, Circle, Clock, MinusCircle, XCircle } from "lucide-react";
import { cn } from "@/lib/utils";

type Tier = "done" | "failed" | "pending" | "expired" | "notStarted";

const TIER_CONFIG: Record<Tier, { color: string; bg: string; Icon: typeof CheckCircle2 }> = {
  done: { color: "text-status-good", bg: "bg-status-good/10", Icon: CheckCircle2 },
  failed: { color: "text-status-critical", bg: "bg-status-critical/10", Icon: XCircle },
  pending: { color: "text-status-warning", bg: "bg-status-warning/15", Icon: Clock },
  expired: { color: "text-ink-muted", bg: "bg-surface-raised", Icon: MinusCircle },
  notStarted: { color: "text-ink-muted", bg: "bg-surface-raised", Icon: Circle },
};

const DONE_VALUES = new Set(["Successful", "Embedded", "Scored"]);

function tierFor(status: string): Tier {
  if (DONE_VALUES.has(status)) return "done";
  if (status === "Failed") return "failed";
  if (status === "Pending") return "pending";
  if (status === "Expired") return "expired";
  return "notStarted"; // "No Extraction" | "Not Embedded" | "Not Scored"
}

export function StatusBadge({
  label,
  status,
  className,
}: {
  label: string;
  status: string;
  className?: string;
}) {
  const { color, bg, Icon } = TIER_CONFIG[tierFor(status)];
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[11px] font-medium whitespace-nowrap",
        color,
        bg,
        className,
      )}
    >
      <Icon className="size-3" />
      {label}: {status}
    </span>
  );
}
