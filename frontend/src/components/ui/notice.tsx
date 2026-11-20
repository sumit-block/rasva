import { AlertTriangle, Info } from "lucide-react";
import type { ReactNode } from "react";
import { cn } from "@/lib/utils";

export function Notice({
  tone = "info",
  children,
  className,
}: {
  tone?: "info" | "error";
  children: ReactNode;
  className?: string;
}) {
  const Icon = tone === "error" ? AlertTriangle : Info;
  return (
    <div
      role={tone === "error" ? "alert" : "status"}
      className={cn(
        "flex items-start gap-3 rounded-lg border p-3 text-sm",
        tone === "error" ? "border-danger/40 bg-danger/10 text-fg" : "border-border bg-surface-2 text-muted",
        className,
      )}
    >
      <Icon className={cn("mt-0.5 size-4 shrink-0", tone === "error" ? "text-danger" : "text-accent")} aria-hidden />
      <div>{children}</div>
    </div>
  );
}

export function EmptyState({
  icon,
  title,
  children,
  action,
}: {
  icon: ReactNode;
  title: string;
  children: ReactNode;
  action?: ReactNode;
}) {
  return (
    <div className="flex flex-col items-center rounded-[var(--radius-card)] border border-dashed border-border px-6 py-12 text-center">
      <div className="mb-4 rounded-full bg-surface-2 p-3 text-accent [&_svg]:size-6">{icon}</div>
      <h2 className="text-base font-semibold">{title}</h2>
      <div className="mt-2 max-w-md text-sm text-muted">{children}</div>
      {action && <div className="mt-5">{action}</div>}
    </div>
  );
}
