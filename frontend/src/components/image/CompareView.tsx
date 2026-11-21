import { useId, useState } from "react";
import { assetUrl } from "@/lib/api";
import { cn } from "@/lib/utils";

/** The original photo next to (or swiped over) the result. */
export function CompareView({ beforeId, afterId, beforeLabel = "Original", afterLabel = "New pose" }: {
  beforeId: string;
  afterId: string;
  beforeLabel?: string;
  afterLabel?: string;
}) {
  const [mode, setMode] = useState<"side" | "swipe">("side");
  const [split, setSplit] = useState(50);
  const sliderId = useId();

  return (
    <div className="mt-3">
      <div role="radiogroup" aria-label="Compare" className="mb-2 inline-flex rounded-lg border border-border p-0.5 text-xs">
        {(["side", "swipe"] as const).map((m) => (
          <button key={m} role="radio" aria-checked={mode === m} onClick={() => setMode(m)}
            className={cn("min-h-9 rounded-md px-3", mode === m ? "bg-surface-2 font-medium text-fg" : "text-muted")}>
            {m === "side" ? "Side by side" : "Swipe"}
          </button>
        ))}
      </div>
      {mode === "side" ? (
        <div className="grid grid-cols-2 gap-2">
          {([[beforeId, beforeLabel], [afterId, afterLabel]] as const).map(([id, label]) => (
            <figure key={label}>
              <img src={assetUrl(id)} alt={label} loading="lazy" className="w-full rounded-lg border border-border bg-bg object-contain" />
              <figcaption className="mt-1 text-xs text-muted">{label}</figcaption>
            </figure>
          ))}
        </div>
      ) : (
        <>
          <div className="relative overflow-hidden rounded-lg border border-border bg-bg">
            <img src={assetUrl(afterId)} alt={afterLabel} className="block w-full object-contain" />
            <img src={assetUrl(beforeId)} alt={beforeLabel} aria-hidden
              className="absolute inset-0 h-full w-full object-contain"
              style={{ clipPath: `inset(0 ${100 - split}% 0 0)` }} />
            <div className="pointer-events-none absolute inset-y-0 w-0.5 bg-accent" style={{ left: `${split}%` }} />
          </div>
          <label htmlFor={sliderId} className="mt-2 flex items-center gap-3 text-xs text-muted">
            {beforeLabel}
            <input id={sliderId} type="range" min={0} max={100} value={split} onChange={(e) => setSplit(Number(e.target.value))}
              className="flex-1 accent-[var(--accent)]" />
            {afterLabel}
          </label>
        </>
      )}
    </div>
  );
}
