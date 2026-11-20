import { useId, useState } from "react";
import { cn } from "@/lib/utils";

export interface CompareSide {
  src: string;
  label: string;
}

/**
 * Before/after comparison (PRD phase I4): a wipe slider or side by side.
 * The slider is a native range input laid over the pictures, so arrow keys,
 * Page Up/Down, Home and End work and screen readers announce the position.
 */
export function CompareView({ before, after }: { before: CompareSide; after: CompareSide }) {
  const [mode, setMode] = useState<"slider" | "side">("slider");
  const [pos, setPos] = useState(50);
  const id = useId();

  return (
    <div>
      <div role="radiogroup" aria-label="Comparison layout" className="mb-2 inline-flex rounded-lg border border-border p-0.5">
        {(["slider", "side"] as const).map((m) => (
          <button key={m} type="button" role="radio" aria-checked={mode === m} onClick={() => setMode(m)}
            className={cn("min-h-9 rounded-md px-3 text-xs font-medium", mode === m ? "bg-surface-2 text-fg" : "text-muted")}>
            {m === "slider" ? "Slider" : "Side by side"}
          </button>
        ))}
      </div>

      {mode === "slider" ? (
        <figure className="m-0">
          <div className="relative w-full max-w-md overflow-hidden rounded-lg border border-border bg-surface-2 has-[:focus-visible]:outline-2 has-[:focus-visible]:outline-offset-2 has-[:focus-visible]:outline-accent">
            <img src={after.src} alt={after.label} className="block aspect-square w-full object-contain" />
            <img src={before.src} alt={before.label} aria-hidden
              className="absolute inset-0 block aspect-square size-full object-contain"
              style={{ clipPath: `inset(0 ${100 - pos}% 0 0)` }} />
            <div aria-hidden className="pointer-events-none absolute inset-y-0 w-0.5 bg-white shadow-[0_0_0_1px_rgba(0,0,0,0.4)]"
              style={{ left: `calc(${pos}% - 1px)` }} />
            <span aria-hidden className="pointer-events-none absolute left-2 top-2 rounded bg-black/60 px-1.5 py-0.5 text-xs text-white">{before.label}</span>
            <span aria-hidden className="pointer-events-none absolute right-2 top-2 rounded bg-black/60 px-1.5 py-0.5 text-xs text-white">{after.label}</span>
            <label htmlFor={id} className="sr-only">Show more of {before.label} or {after.label}</label>
            <input id={id} type="range" min={0} max={100} step={1} value={pos} onChange={(e) => setPos(Number(e.target.value))}
              aria-valuetext={`${pos}% ${before.label}, ${100 - pos}% ${after.label}`}
              className="absolute inset-0 size-full cursor-ew-resize opacity-0" />
          </div>
          <figcaption className="mt-1 text-xs text-muted">Drag across the picture, or focus it and use the arrow keys.</figcaption>
        </figure>
      ) : (
        <div className="grid max-w-2xl grid-cols-2 gap-2">
          {[before, after].map((s, i) => (
            <figure key={i} className="m-0 min-w-0">
              <img src={s.src} alt={s.label} className="block aspect-square w-full rounded-lg border border-border bg-surface-2 object-contain" />
              <figcaption className="mt-1 truncate text-xs text-muted">{s.label}</figcaption>
            </figure>
          ))}
        </div>
      )}
    </div>
  );
}
