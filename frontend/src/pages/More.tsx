import { ChevronRight } from "lucide-react";
import { Link } from "react-router-dom";
import { NAV } from "@/components/layout/AppShell";
import { PageHeader } from "@/components/layout/PageHeader";
import { usePrefs } from "@/hooks/usePrefs";

/** Phone-only menu for destinations that don't fit in the bottom bar. */
export function MorePage() {
  const { prefs } = usePrefs();
  const items = NAV.filter((n) => !n.mobile && (!n.advanced || prefs.mode === "advanced"));
  return (
    <>
      <PageHeader title="More" />
      <ul className="divide-y divide-border overflow-hidden rounded-[var(--radius-card)] border border-border bg-surface">
        {items.map(({ to, label, icon: Icon }) => (
          <li key={to}>
            <Link to={to} className="flex min-h-14 items-center gap-3 px-4 hover:bg-surface-2">
              <Icon className="size-5 text-muted" aria-hidden />
              <span className="flex-1">{label}</span>
              <ChevronRight className="size-4 text-muted" aria-hidden />
            </Link>
          </li>
        ))}
      </ul>
      {prefs.mode === "simple" && (
        <p className="mt-3 text-xs text-muted">Turn on Advanced mode in Settings to see Experiments.</p>
      )}
    </>
  );
}
