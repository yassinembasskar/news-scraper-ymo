import { NavLink } from "react-router-dom";
import { FileText, LayoutDashboard, Moon, Newspaper, Sun } from "lucide-react";
import { cn } from "@/lib/utils";
import { useTheme } from "@/lib/theme";

const NAV_ITEMS = [
  { to: "/", label: "Dashboard", icon: LayoutDashboard, end: true },
  { to: "/reports", label: "Reports", icon: FileText, end: false },
];

export function Sidebar() {
  const { theme, toggleTheme } = useTheme();

  return (
    <aside className="flex h-screen w-60 shrink-0 flex-col border-r border-border bg-surface">
      <div className="flex items-center gap-2 px-5 py-5">
        <div className="flex size-8 items-center justify-center rounded-lg bg-brand text-brand-ink">
          <Newspaper className="size-4.5" />
        </div>
        <div>
          <p className="text-sm font-semibold leading-tight text-ink">Yazaki News</p>
          <p className="text-[11px] leading-tight text-ink-muted">Monitor</p>
        </div>
      </div>

      <nav className="flex flex-1 flex-col gap-1 px-3">
        {NAV_ITEMS.map(({ to, label, icon: Icon, end }) => (
          <NavLink
            key={to}
            to={to}
            end={end}
            className={({ isActive }) =>
              cn(
                "flex items-center gap-2.5 rounded-lg px-3 py-2 text-sm font-medium transition-colors",
                isActive
                  ? "bg-brand text-brand-ink"
                  : "text-ink-secondary hover:bg-surface-raised hover:text-ink",
              )
            }
          >
            <Icon className="size-4" />
            {label}
          </NavLink>
        ))}
      </nav>

      <div className="border-t border-border p-3">
        <button
          onClick={toggleTheme}
          className="flex w-full items-center gap-2.5 rounded-lg px-3 py-2 text-sm font-medium text-ink-secondary transition-colors hover:bg-surface-raised hover:text-ink"
        >
          {theme === "dark" ? <Sun className="size-4" /> : <Moon className="size-4" />}
          {theme === "dark" ? "Light mode" : "Dark mode"}
        </button>
      </div>
    </aside>
  );
}
