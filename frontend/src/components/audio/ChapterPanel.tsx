import { RefreshCw, RotateCcw, Square } from "lucide-react";
import { useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardTitle } from "@/components/ui/card";
import { Progress } from "@/components/ui/progress";
import { assetUrl, chapters, type Chapter, type Segment, type SegmentStatus } from "@/lib/api";
import { formatDuration } from "@/lib/format";
import { cn } from "@/lib/utils";

const STATUS: Record<SegmentStatus, { label: string; tone: "neutral" | "accent" | "success" | "warning" | "danger" }> = {
  pending: { label: "Not generated", tone: "neutral" },
  generating: { label: "Generating", tone: "accent" },
  failed: { label: "Failed", tone: "danger" },
  ready: { label: "Ready", tone: "success" },
  outdated: { label: "Changed since generated", tone: "warning" },
  needs_review: { label: "New version", tone: "warning" },
};
const ATTENTION = new Set<SegmentStatus>(["failed", "needs_review", "outdated", "pending"]);
const PAGE = 40;
const LANGUAGE_NAME = { en: "English", hi: "Hindi" } as const;

function LanguageChoice({ seg, perSentence, busy, onPick }: {
  seg: Segment;
  perSentence: boolean;
  busy: boolean;
  onPick: (language: "en" | "hi" | null) => void;
}) {
  const id = `language-${seg.id}`;
  const automatic = !perSentence
    ? "Automatic"
    : seg.reads_as === "mixed"
      ? "Automatic (each part by its script)"
      : `Automatic (${LANGUAGE_NAME[seg.reads_as]})`;
  return (
    <span className="inline-flex items-center gap-1.5">
      <label htmlFor={id} className="text-xs text-muted">Read as</label>
      <select id={id} disabled={busy} value={seg.language_override ?? ""}
        onChange={(e) => onPick((e.target.value || null) as "en" | "hi" | null)}
        className="min-h-9 rounded-lg border border-border bg-bg px-2 text-xs">
        <option value="">{automatic}</option>
        <option value="en">English</option>
        <option value="hi">Hindi</option>
      </select>
    </span>
  );
}

function SegmentRow({ seg, perSentence, onChange, onError }: {
  seg: Segment;
  perSentence: boolean;
  onChange: (c: Chapter) => void;
  onError: (m: string) => void;
}) {
  const [busy, setBusy] = useState(false);
  const latest = seg.takes[seg.takes.length - 1];
  const act = async (fn: () => Promise<Chapter>) => {
    setBusy(true);
    try {
      onChange(await fn());
    } catch (e) {
      onError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  const s = STATUS[seg.status];
  return (
    <li className="py-3">
      <div className="flex items-start gap-3">
        <span className="w-8 shrink-0 pt-0.5 text-right text-xs text-muted tabular-nums">{seg.position + 1}</span>
        <div className="min-w-0 flex-1">
          <p className="text-sm leading-relaxed">{seg.text}</p>
          <div className="mt-2 flex flex-wrap items-center gap-2">
            <Badge tone={s.tone}>{s.label}</Badge>
            {seg.status === "failed" && latest?.error && <span className="text-xs text-danger">{latest.error}</span>}
            {seg.current?.asset_id && seg.status !== "needs_review" && (
              <audio controls preload="none" src={assetUrl(seg.current.asset_id)} className="h-9 max-w-full" aria-label={`Sentence ${seg.position + 1}`} />
            )}
            {seg.status !== "generating" && (
              <Button size="sm" variant="ghost" disabled={busy} onClick={() => act(() => chapters.regenerate(seg.id))}>
                <RotateCcw aria-hidden /> Regenerate
              </Button>
            )}
            <LanguageChoice seg={seg} perSentence={perSentence} busy={busy || seg.status === "generating"}
              onPick={(language) => act(() => chapters.setLanguage(seg.id, language))} />
          </div>
          {seg.language_override && !perSentence && (
            <p className="mt-1 text-xs text-muted">
              Saved as {LANGUAGE_NAME[seg.language_override]}. This voice reads everything in one language,
              so your choice is used only with voices that speak both English and Hindi.
            </p>
          )}
          {seg.status === "needs_review" && seg.current && latest?.asset_id && (
            <div className="mt-2 grid gap-2 rounded-lg border border-warning/40 p-2 sm:grid-cols-2">
              <div>
                <p className="mb-1 text-xs text-muted">Current</p>
                <audio controls preload="none" src={assetUrl(seg.current.asset_id!)} className="h-9 w-full" aria-label="Current version" />
                <Button size="sm" variant="ghost" className="mt-1" disabled={busy} onClick={() => act(() => chapters.useTake(seg.id, seg.current!.id))}>
                  Keep current
                </Button>
              </div>
              <div>
                <p className="mb-1 text-xs text-muted">New</p>
                <audio controls preload="none" src={assetUrl(latest.asset_id)} className="h-9 w-full" aria-label="New version" />
                <Button size="sm" className="mt-1" disabled={busy} onClick={() => act(() => chapters.useTake(seg.id, latest.id))}>
                  Use new
                </Button>
              </div>
            </div>
          )}
        </div>
      </div>
    </li>
  );
}

export function ChapterPanel({ chapter, onChange }: { chapter: Chapter; onChange: (c: Chapter) => void }) {
  const [filter, setFilter] = useState<"all" | "attention">("all");
  const [shown, setShown] = useState(PAGE);
  const [error, setError] = useState<string>();
  const counts = chapter.summary.counts;
  const total = chapter.summary.segments;
  const ready = (counts.ready ?? 0) + (counts.outdated ?? 0) + (counts.needs_review ?? 0);
  const generating = counts.generating ?? 0;
  const segments = chapter.segments.filter((s) => filter === "all" || ATTENTION.has(s.status));

  const act = async (fn: () => Promise<Chapter>) => {
    setError(undefined);
    try {
      onChange(await fn());
    } catch (e) {
      setError((e as Error).message);
    }
  };

  if (total === 0) return null;
  return (
    <Card>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <CardTitle>Narration</CardTitle>
        <span className="text-sm text-muted tabular-nums">
          {ready} of {total} sentences · {formatDuration(chapter.summary.duration_s)}
        </span>
      </div>
      <Progress className="mt-3" value={total ? ready / total : 0} label="Sentences with audio" />
      <div className="mt-3 flex flex-wrap gap-2">
        {generating > 0 && (
          <Button size="sm" variant="secondary" onClick={() => act(() => chapters.cancel(chapter.id))}>
            <Square aria-hidden /> Stop ({generating} left)
          </Button>
        )}
        {(counts.failed ?? 0) > 0 && (
          <Button size="sm" variant="secondary" onClick={() => act(() => chapters.generate(chapter.id, "failed"))}>
            <RefreshCw aria-hidden /> Retry {counts.failed} failed
          </Button>
        )}
        {(counts.outdated ?? 0) > 0 && generating === 0 && (
          <Button size="sm" variant="secondary" onClick={() => act(() => chapters.generate(chapter.id, "needed"))}>
            Update {counts.outdated} changed
          </Button>
        )}
      </div>
      {(counts.needs_review ?? 0) > 0 && (
        <p className="mt-2 text-xs text-warning">{counts.needs_review} sentences have a new version to compare.</p>
      )}
      {error && <p role="alert" className="mt-2 text-sm text-danger">{error}</p>}

      <div role="tablist" aria-label="Show sentences" className="mt-4 inline-flex rounded-lg border border-border bg-surface-2 p-1">
        {(["all", "attention"] as const).map((f) => (
          <button key={f} role="tab" aria-selected={filter === f} onClick={() => { setFilter(f); setShown(PAGE); }}
            className={cn("min-h-9 rounded-md px-3 text-sm", filter === f ? "bg-surface text-fg shadow-sm" : "text-muted")}>
            {f === "all" ? "All" : "Needs attention"}
          </button>
        ))}
      </div>
      <ul className="mt-2 divide-y divide-border">
        {segments.slice(0, shown).map((s) => (
          <SegmentRow key={s.id} seg={s} perSentence={chapter.language_per_sentence} onChange={onChange} onError={setError} />
        ))}
      </ul>
      {segments.length === 0 && <p className="py-4 text-sm text-muted">Nothing needs attention.</p>}
      {segments.length > shown && (
        <Button variant="ghost" className="mt-2 w-full" onClick={() => setShown((n) => n + PAGE)}>
          Show more ({segments.length - shown} left)
        </Button>
      )}
    </Card>
  );
}
