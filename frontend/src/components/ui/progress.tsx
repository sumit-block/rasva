import { cn } from "@/lib/utils";

/** Accessible progress bar. `value` null = indeterminate (stage known, fraction unknown). */
export function Progress({
  value,
  label,
  className,
  tone = "accent",
}: {
  value: number | null;
  label: string;
  className?: string;
  tone?: "accent" | "warning" | "danger";
}) {
  const pct = value == null ? null : Math.round(Math.min(Math.max(value, 0), 1) * 100);
  const bar = { accent: "bg-accent", warning: "bg-warning", danger: "bg-danger" }[tone];
  return (
    <div
      role="progressbar"
      aria-label={label}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={pct ?? undefined}
      className={cn("h-2 w-full overflow-hidden rounded-full bg-surface-2", className)}
    >
      <div
        className={cn("h-full rounded-full transition-[width] duration-300", bar, pct == null && "w-1/3 animate-pulse")}
        style={pct == null ? undefined : { width: `${pct}%` }}
      />
    </div>
  );
}
