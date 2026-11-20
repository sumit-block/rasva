import {
  AudioLines,
  Boxes,
  FlaskConical,
  FolderOpen,
  Home,
  Image as ImageIcon,
  ListChecks,
  Loader2,
  Menu,
  Settings as SettingsIcon,
  WifiOff,
} from "lucide-react";
import { NavLink, Outlet } from "react-router-dom";
import { useJobs } from "@/hooks/useJobs";
import { STATE_LABEL } from "@/lib/format";
import { cn } from "@/lib/utils";
import { usePrefs } from "@/hooks/usePrefs";

export type NavItem = { to: string; label: string; icon: typeof Home; advanced?: boolean; mobile?: boolean };

export const NAV: NavItem[] = [
  { to: "/", label: "Home", icon: Home, mobile: true },
  { to: "/audio", label: "Audio Studio", icon: AudioLines, mobile: true },
  { to: "/image", label: "Image Studio", icon: ImageIcon },
  { to: "/library", label: "Library", icon: FolderOpen, mobile: true },
  { to: "/jobs", label: "Jobs", icon: ListChecks, mobile: true },
  { to: "/models", label: "Models", icon: Boxes },
  { to: "/experiments", label: "Experiments", icon: FlaskConical, advanced: true },
  { to: "/settings", label: "Settings", icon: SettingsIcon },
];

const MORE: NavItem = { to: "/more", label: "More", icon: Menu, mobile: true };

function JobStatusChip() {
  const { active, connected, jobs } = useJobs();
  const waiting = jobs.filter((j) => j.state === "queued").length;
  if (!connected) {
    return (
      <span role="status" className="inline-flex items-center gap-2 text-sm text-danger">
        <WifiOff className="size-4" aria-hidden /> Mac unreachable
      </span>
    );
  }
  if (!active) {
    return (
      <span role="status" className="text-sm text-muted">
        {waiting ? `${waiting} waiting` : "No active jobs"}
      </span>
    );
  }
  const pct = active.progress != null ? ` ${Math.round(active.progress * 100)}%` : "";
  return (
    <NavLink to="/jobs" role="status" className="inline-flex items-center gap-2 text-sm text-fg hover:text-accent">
      <Loader2 className="size-4 animate-spin text-accent" aria-hidden />
      {STATE_LABEL[active.state]}
      {pct}
      {waiting > 0 && <span className="text-muted">· {waiting} waiting</span>}
    </NavLink>
  );
}

export function AppShell() {
  const { prefs } = usePrefs();
  const items = NAV.filter((n) => !n.advanced || prefs.mode === "advanced");
  return (
    <div className="flex h-full">
      <a href="#main" className="sr-only focus:not-sr-only focus:absolute focus:z-50 focus:m-2 focus:rounded focus:bg-surface focus:p-2">
        Skip to content
      </a>
      {/* Desktop rail */}
      <nav aria-label="Main" className="hidden w-60 shrink-0 flex-col border-r border-border bg-surface md:flex">
        <div className="flex h-16 items-center gap-2 px-5">
          <img src="/favicon.svg" alt="" className="size-7" />
          <span className="text-lg font-semibold tracking-tight">Rasva Studio</span>
        </div>
        <ul className="flex flex-1 flex-col gap-1 px-3">
          {items.map(({ to, label, icon: Icon }) => (
            <li key={to}>
              <NavLink
                to={to}
                end={to === "/"}
                className={({ isActive }) =>
                  cn(
                    "flex min-h-11 items-center gap-3 rounded-lg px-3 text-sm transition-colors",
                    isActive ? "bg-accent/12 text-accent" : "text-muted hover:bg-surface-2 hover:text-fg",
                  )
                }
              >
                <Icon className="size-4" aria-hidden />
                {label}
              </NavLink>
            </li>
          ))}
        </ul>
        <p className="px-5 py-4 text-xs text-muted">Everything runs on this Mac.</p>
      </nav>

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex h-16 shrink-0 items-center justify-between gap-4 border-b border-border px-4 md:px-8">
          <span className="font-semibold md:hidden">Rasva Studio</span>
          <span className="hidden md:block" />
          <JobStatusChip />
        </header>
        <main id="main" className="flex-1 overflow-y-auto px-4 pt-6 pb-28 md:px-8 md:pb-10">
          <div className="mx-auto max-w-6xl">
            <Outlet />
          </div>
        </main>
      </div>

      {/* Mobile bottom navigation */}
      <nav aria-label="Main" className="fixed inset-x-0 bottom-0 z-40 border-t border-border bg-surface/95 pb-[env(safe-area-inset-bottom)] backdrop-blur md:hidden">
        <ul className="grid grid-cols-5">
          {[...items.filter((n) => n.mobile), MORE]
            .map(({ to, label, icon: Icon }) => (
              <li key={to}>
                <NavLink
                  to={to}
                  end={to === "/"}
                  className={({ isActive }) =>
                    cn(
                      "flex min-h-14 flex-col items-center justify-center gap-1 text-[11px]",
                      isActive ? "text-accent" : "text-muted",
                    )
                  }
                >
                  <Icon className="size-5" aria-hidden />
                  {label.replace(" Studio", "")}
                </NavLink>
              </li>
            ))}
        </ul>
      </nav>
    </div>
  );
}
