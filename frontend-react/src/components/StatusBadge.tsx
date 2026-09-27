// Generic pipeline-stage status badge (extraction, title-gate, etc.) —
// icon+label-never-color-alone, since the status enums share the same
// states in spirit: not started / pending / done / failed / skipped.

import { CheckCircle2, Circle, Clock, MinusCircle, XCircle } from "lucide-react";
import { cn } from "@/lib/utils";

type Tier = "done" | "failed" | "pending" | "skipped" | "notStarted";

const TIER_CONFIG: Record<Tier, { color: string; bg: string; Icon: typeof CheckCircle2 }> = {
  done: { color: "text-status-good", bg: "bg-status-good/10", Icon: CheckCircle2 },
  failed: { color: "text-status-critical", bg: "bg-status-critical/10", Icon: XCircle },
  pending: { color: "text-status-warning", bg: "bg-status-warning/15", Icon: Clock },
  skipped: { color: "text-ink-muted", bg: "bg-surface-raised", Icon: MinusCircle },
  notStarted: { color: "text-ink-muted", bg: "bg-surface-raised", Icon: Circle },
};

const DONE_VALUES = new Set(["Successful"]);

function tierFor(status: string): Tier {
  if (DONE_VALUES.has(status)) return "done";
  if (status === "Failed") return "failed";
  if (status === "Pending") return "pending";
  // A terminal, non-actionable state — an archived article was
  // deliberately never sent to extraction, so nothing more happens here.
  if (status === "Archived") return "skipped";
  return "notStarted"; // "No Extraction"
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
