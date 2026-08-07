import { cn } from "@/lib/utils";

export function ProgressBar({
  value,
  className,
  trackClassName,
}: {
  value: number; // 0..1
  className?: string;
  trackClassName?: string;
}) {
  const percent = Math.round(Math.min(Math.max(value, 0), 1) * 100);
  return (
    <div className={cn("h-1.5 w-full overflow-hidden rounded-full bg-border", trackClassName)}>
      <div
        className={cn("h-full rounded-full bg-brand transition-[width] duration-500", className)}
        style={{ width: `${percent}%` }}
      />
    </div>
  );
}
