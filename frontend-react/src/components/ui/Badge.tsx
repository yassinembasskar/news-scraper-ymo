import type { HTMLAttributes } from "react";
import { cn } from "@/lib/utils";

export function Badge({ className, ...props }: HTMLAttributes<HTMLSpanElement>) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-full border border-border bg-surface-raised",
        "px-2.5 py-0.5 text-xs font-medium text-ink-secondary",
        className,
      )}
      {...props}
    />
  );
}
