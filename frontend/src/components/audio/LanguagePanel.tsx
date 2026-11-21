import { Languages, Undo2 } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { Button } from "@/components/ui/button";
import { languageTools, type LanguageAnalysis, type TransliterationSentence, type WordSuggestion } from "@/lib/api";
import { cn } from "@/lib/utils";

const LABEL = { hi: "Hindi", hinglish: "Roman Hinglish", en: "English", mixed: "Mixed", unknown: "Unclear" } as const;

function applyWords(text: string, words: WordSuggestion[], chosen: Set<number>): string {
  let out = "";
  let pos = 0;
  words.forEach((w, i) => {
    if (!chosen.has(i)) return;
    out += text.slice(pos, w.start) + w.suggestion;
    pos = w.end;
  });
  return out + text.slice(pos);
}

function SentenceReview({ s, onApply }: { s: TransliterationSentence; onApply: (from: string, to: string) => void }) {
  const [chosen, setChosen] = useState(() => new Set(s.words.flatMap((w, i) => (w.confident ? [i] : []))));
  const result = applyWords(s.original, s.words, chosen);
  return (
    <li className="py-3">
      <p className="text-sm text-muted">{s.original}</p>
      <div className="mt-2 flex flex-wrap gap-1">
        {s.words.map((w, i) => (
          <button key={`${w.start}-${w.original}`} aria-pressed={chosen.has(i)}
            onClick={() => setChosen((c) => { const n = new Set(c); if (n.has(i)) n.delete(i); else n.add(i); return n; })}
            title={w.source === "rule" ? "A guess: check the spelling" : w.source === "name" ? "Known name" : "Common word"}
            className={cn("min-h-9 rounded-md border px-2 text-sm",
              chosen.has(i) ? "border-accent bg-accent/10 text-fg" : "border-border text-muted",
              w.source === "rule" && "border-dashed")}>
            {w.original} → {w.suggestion}
          </button>
        ))}
      </div>
      <p className="mt-2 text-base">{result}</p>
      <Button size="sm" variant="secondary" className="mt-2" disabled={result === s.original} onClick={() => onApply(s.original, result)}>
        Use this
      </Button>
    </li>
  );
}

/** Language overview and Roman-to-Devanagari suggestions (PRD §10.1-10.2). Never changes text by itself. */
export function LanguagePanel({ script, voiceLanguage, perSentence = false, onChange }: {
  script: string; voiceLanguage: "hi" | "en" | undefined; perSentence?: boolean; onChange: (next: string) => void;
}) {
  const [analysis, setAnalysis] = useState<LanguageAnalysis>();
  const [review, setReview] = useState<TransliterationSentence[] | null>(null);
  const [undo, setUndo] = useState<string | null>(null);

  useEffect(() => {
    if (!script.trim()) {
      setAnalysis(undefined);
      return;
    }
    const t = window.setTimeout(() => {
      languageTools.analyze(script.slice(0, 200_000)).then(setAnalysis, () => setAnalysis(undefined));
    }, 700);
    return () => window.clearTimeout(t);
  }, [script]);

  const counts = analysis?.summary.counts ?? {};
  const hinglish = counts.hinglish ?? 0;
  const parts = useMemo(
    () => (Object.entries(counts) as [keyof typeof LABEL, number][]).filter(([, n]) => n > 0).map(([k, n]) => `${LABEL[k]} ${n}`),
    [counts],
  );

  const openReview = async () => {
    const r = await languageTools.transliterate(script);
    setReview(r.sentences);
  };
  const apply = (from: string, to: string) => {
    const at = script.indexOf(from);
    if (at < 0) return;
    setUndo(script);
    onChange(script.slice(0, at) + to + script.slice(at + from.length));
    setReview((rs) => rs?.filter((r) => r.original !== from) ?? null);
  };

  if (!analysis || parts.length === 0) return null;
  return (
    <div className="mt-3 rounded-lg border border-border bg-surface-2/50 p-3 text-sm">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="flex items-center gap-2">
          <Languages className="size-4 text-accent" aria-hidden /> {parts.join(" · ")}{" "}
          {analysis.sentences.length === 1 ? "sentence" : "sentences"}
        </span>
        {undo !== null && (
          <Button size="sm" variant="ghost" onClick={() => { onChange(undo); setUndo(null); }}><Undo2 aria-hidden /> Undo</Button>
        )}
      </div>
      {analysis.summary.uncertain > 0 && (
        <p className="mt-1 text-xs text-muted">
          {analysis.summary.uncertain} sentences are hard to tell apart;{" "}
          {perSentence
            ? "if one is read in the wrong language, set it with “Read as” in the sentence list below."
            : "pick the voice that fits them."}
        </p>
      )}
      {hinglish > 0 && voiceLanguage === "en" && (
        <p className="mt-1 text-xs text-muted">English voices read Roman Hinglish with English pronunciation.</p>
      )}
      {hinglish > 0 && review === null && (
        <Button size="sm" variant="secondary" className="mt-2" onClick={() => void openReview()}>
          Suggest Devanagari for {hinglish} Hinglish {hinglish === 1 ? "sentence" : "sentences"}
        </Button>
      )}
      {review !== null && (
        <div className="mt-2">
          <p className="text-xs text-muted">
            Tap words to include or skip them. Dashed ones are guesses: check the spelling. Your script only changes when
            you tap “Use this”.
          </p>
          {review.length === 0 ? <p className="mt-2 text-xs">No more suggestions.</p> : (
            <ul className="divide-y divide-border">{review.map((s) => <SentenceReview key={s.original} s={s} onApply={apply} />)}</ul>
          )}
          <Button size="sm" variant="ghost" onClick={() => setReview(null)}>Close</Button>
        </div>
      )}
    </div>
  );
}
