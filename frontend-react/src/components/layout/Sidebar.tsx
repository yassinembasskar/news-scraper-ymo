import { NavLink } from "react-router-dom";
import { Moon, Sun } from "lucide-react";
import { cn } from "@/lib/utils";
import { useTheme } from "@/lib/theme";

// Daily Insight reports live inside the Dashboard now (see
// pages/Dashboard.tsx's ReportsSection) rather than their own nav item —
// merged in 2026-09-15 once the old per-article relativity scorer was
// retired and there was no more separate "Scoring" page slot to keep
// reporting apart from.
//
// Nav items are numbered like terminal pins on a connector housing
// (see the "harness trace" redesign, 2026-09-16) — a real, if playful,
// echo of Yazaki's own product (wiring harnesses) rather than a generic
// icon rail.
const NAV_ITEMS = [
  { to: "/", pin: "01", label: "Dashboard", end: true },
  { to: "/archived", pin: "02", label: "Archived", end: false },
  { to: "/logs", pin: "03", label: "Logs", end: false },
];

export function Sidebar() {
  const { theme, toggleTheme } = useTheme();

  return (
    <aside className="sticky top-0 flex h-screen w-42 shrink-0 self-start flex-col gap-5 overflow-y-auto border-r border-border px-3.5 py-4.5">
      <div className="flex items-center gap-2.5 px-1">
        <div className="relative flex size-6.5 shrink-0 items-center justify-center rounded-[2px] border-2 border-brand">
          <span className="absolute left-[3px] top-1/2 h-2 w-[3px] -translate-y-1/2 bg-brand" />
          <span className="absolute right-[3px] top-1/2 h-2 w-[3px] -translate-y-1/2 bg-brand" />
        </div>
        <div className="leading-tight">
          <p className="font-display text-sm font-extrabold uppercase tracking-wide text-ink">Yazaki</p>
          <p className="text-[8.5px] uppercase tracking-[0.14em] text-ink-muted">News Monitor</p>
        </div>
      </div>

      <nav className="flex flex-col gap-0.5">
        {NAV_ITEMS.map(({ to, pin, label, end }) => (
          <NavLink
            key={to}
            to={to}
            end={end}
            className={({ isActive }) =>
              cn(
                "flex items-baseline gap-2 rounded-[2px] px-2 py-1.5 text-[12.5px] transition-colors",
                isActive
                  ? "bg-surface-raised font-semibold text-ink"
                  : "text-ink-secondary hover:bg-surface-raised hover:text-ink",
              )
            }
          >
            {({ isActive }) => (
              <>
                <span className={cn("font-mono text-[10px]", isActive ? "text-brand" : "text-ink-muted")}>
                  {pin}
                </span>
                {label}
              </>
            )}
          </NavLink>
        ))}
      </nav>

      <div className="mt-auto border-t border-border pt-3">
        <button
          onClick={toggleTheme}
          className="flex w-full items-center justify-between gap-2 rounded-[2px] border border-border px-2.5 py-1.5 text-[11px] text-ink-secondary transition-colors hover:border-ink-muted hover:text-ink"
        >
          {theme === "dark" ? "Light mode" : "Dark mode"}
          {theme === "dark" ? <Sun className="size-3.5" /> : <Moon className="size-3.5" />}
        </button>
      </div>
    </aside>
  );
}
