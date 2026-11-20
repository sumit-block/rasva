import { FlaskConical, Plus, Square } from "lucide-react";
import { useEffect, useMemo, useState, type FormEvent } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { PageHeader } from "@/components/layout/PageHeader";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardDescription, CardTitle } from "@/components/ui/card";
import { EmptyState, Notice } from "@/components/ui/notice";
import { Progress } from "@/components/ui/progress";
import { useAsync } from "@/hooks/useAsync";
import {
  api,
  assetUrl,
  experiments,
  isUsable,
  type CandidateSummary,
  type Experiment,
  type ExperimentRun,
} from "@/lib/api";
import { formatBytes, timeAgo } from "@/lib/format";
import { cn } from "@/lib/utils";

const CATEGORY_LABEL: Record<string, string> = {
  hinglish: "Roman Hinglish",
  hindi: "Hindi",
  english: "English",
  mixed: "Mixed script",
  names: "Names",
  custom: "Your sentences",
};

const DIMENSION_LABEL: Record<string, string> = {
  pronunciation: "Pronunciation",
  naturalness: "Naturalness",
  intelligibility: "Clarity",
  pacing: "Pacing",
};

const OPEN = new Set(["queued", "preparing", "loading_model", "processing", "finalizing", "downloading", "draft"]);

function candidateLabel(key: string): string {
  const [engine, rest] = key.split(":");
  return rest ? `${engine} · ${rest.replaceAll("voice_id=", "")}` : (engine ?? key);
}

// ------------------------------------------------------------------ list + create

function NewExperiment({ onCreated }: { onCreated: (id: string) => void }) {
  const benchmarks = useAsync(experiments.benchmarks);
  const models = useAsync(api.models);
  const [name, setName] = useState("Voice comparison");
  const [source, setSource] = useState<string>("audio-v1");
  const [texts, setTexts] = useState("");
  const [chosen, setChosen] = useState<string[]>([]);
  const [error, setError] = useState<string>();
  const [busy, setBusy] = useState(false);

  const options = (models.data ?? [])
    .filter((m) => m.tasks.includes("audio.narrate") && isUsable(m))
    .flatMap((m) => m.voices.map((v) => ({ engine: m, voice: v, id: `${m.id}|${v.id}` })));

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(undefined);
    try {
      const exp = await experiments.create({
        name,
        ...(source === "custom"
          ? { texts: texts.split("\n").filter((t) => t.trim()) }
          : { benchmark: source }),
        candidates: chosen.map((id) => {
          const [engine_id, voice_id] = id.split("|");
          return { engine_id: engine_id!, settings: { voice_id } };
        }),
      });
      onCreated(exp.id);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };

  if (models.data && options.length === 0) {
    return (
      <Notice>Install a voice engine first. Experiments compare engines and voices that are on this Mac.</Notice>
    );
  }

  const bench = benchmarks.data?.find((b) => b.id === source);
  const runs = (source === "custom" ? texts.split("\n").filter((t) => t.trim()).length : (bench?.cases ?? 0)) * chosen.length;

  return (
    <Card>
      <form onSubmit={submit} className="space-y-4">
        <CardTitle>New experiment</CardTitle>
        <label className="block text-sm">
          <span className="mb-1 block font-medium">Name</span>
          <input value={name} onChange={(e) => setName(e.target.value)} maxLength={200} required
            className="min-h-11 w-full rounded-lg border border-border bg-bg px-3" />
        </label>
        <fieldset>
          <legend className="mb-1 text-sm font-medium">Sentences</legend>
          <div className="flex flex-wrap gap-2">
            {benchmarks.data?.map((b) => (
              <label key={b.id} className={cn("flex min-h-11 cursor-pointer items-center gap-2 rounded-lg border px-3 text-sm", source === b.id ? "border-accent bg-accent/10" : "border-border")}>
                <input type="radio" name="source" checked={source === b.id} onChange={() => setSource(b.id)} />
                {b.name} ({b.cases})
              </label>
            ))}
            <label className={cn("flex min-h-11 cursor-pointer items-center gap-2 rounded-lg border px-3 text-sm", source === "custom" ? "border-accent bg-accent/10" : "border-border")}>
              <input type="radio" name="source" checked={source === "custom"} onChange={() => setSource("custom")} />
              My own sentences
            </label>
          </div>
          {bench && source !== "custom" && <p className="mt-2 text-xs text-muted">{bench.description}</p>}
          {source === "custom" && (
            <textarea value={texts} onChange={(e) => setTexts(e.target.value)} rows={5}
              placeholder="One sentence per line"
              className="mt-2 w-full rounded-lg border border-border bg-bg p-3 text-sm" />
          )}
        </fieldset>
        <fieldset>
          <legend className="mb-1 text-sm font-medium">Compare (up to 8)</legend>
          <div className="flex flex-wrap gap-2">
            {options.map((o) => {
              const on = chosen.includes(o.id);
              return (
                <button key={o.id} type="button" aria-pressed={on}
                  onClick={() => setChosen((c) => (on ? c.filter((x) => x !== o.id) : c.length < 8 ? [...c, o.id] : c))}
                  className={cn("min-h-11 rounded-lg border px-3 text-sm", on ? "border-accent bg-accent/10 text-fg" : "border-border text-muted")}>
                  {o.engine.display_name} · {o.voice.display_name} ({o.voice.language === "hi" ? "Hindi" : "English"})
                </button>
              );
            })}
          </div>
          <p className="mt-2 text-xs text-muted">
            Sentences a voice can't read (for example Devanagari with an English voice) are skipped, not failed.
          </p>
        </fieldset>
        {error && <Notice tone="error">{error}</Notice>}
        <div className="flex items-center gap-3">
          <Button type="submit" disabled={busy || chosen.length === 0 || runs === 0}>
            Start {runs > 0 ? `(${runs} generations)` : ""}
          </Button>
          <span className="text-xs text-muted">They run one at a time in the background.</span>
        </div>
      </form>
    </Card>
  );
}

function ExperimentList() {
  const navigate = useNavigate();
  const list = useAsync(experiments.list);
  const [creating, setCreating] = useState(false);
  return (
    <>
      <PageHeader
        title="Experiments"
        description="Compare engines and voices on the same sentences, measure them on this Mac, and rate what you hear."
        actions={!creating && <Button onClick={() => setCreating(true)}><Plus aria-hidden /> New experiment</Button>}
      />
      {creating && <div className="mb-6"><NewExperiment onCreated={(id) => navigate(`/experiments/${id}`)} /></div>}
      {list.error && <Notice tone="error">{list.error.message}</Notice>}
      {list.data?.length === 0 && !creating && (
        <EmptyState icon={<FlaskConical />} title="No experiments yet"
          action={<Button onClick={() => setCreating(true)}>New experiment</Button>}>
          Run the 60-sentence benchmark to see how each voice handles Hindi, English, Hinglish and names.
        </EmptyState>
      )}
      <ul className="space-y-3">
        {list.data?.map((e) => (
          <li key={e.id}>
            <button onClick={() => navigate(`/experiments/${e.id}`)} className="w-full text-left">
              <Card className="hover:border-accent/50">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <span className="font-medium">{e.name}</span>
                  <Badge tone={e.status === "decided" ? "success" : "accent"}>{e.status === "decided" ? "Decided" : "In progress"}</Badge>
                </div>
                <p className="mt-1 text-xs text-muted">
                  {e.candidates.length} candidates · {e.completed}/{e.runs} generated · {e.rated} rated · {timeAgo(e.created_at)}
                </p>
              </Card>
            </button>
          </li>
        ))}
      </ul>
    </>
  );
}

// ------------------------------------------------------------------ detail

function Rating({ value, onChange, label }: { value: number | undefined; onChange: (v: number | null) => void; label: string }) {
  return (
    <div className="flex items-center justify-between gap-2">
      <span className="text-xs text-muted">{label}</span>
      <div role="radiogroup" aria-label={label} className="flex gap-1">
        {[1, 2, 3, 4, 5].map((n) => (
          <button key={n} role="radio" aria-checked={value === n} onClick={() => onChange(value === n ? null : n)}
            className={cn("size-9 rounded-md border text-sm tabular-nums", value === n ? "border-accent bg-accent text-accent-fg" : "border-border text-muted hover:text-fg")}>
            {n}
          </button>
        ))}
      </div>
    </div>
  );
}

function SummaryTable({ exp, onUsability }: { exp: Experiment; onUsability: (key: string, v: number | null) => void }) {
  const fmt = (v: number | null, f: (n: number) => string) => (v == null ? "–" : f(v));
  const best = Math.max(...exp.summary.map((s) => (s.eligible !== false ? (s.score_final ?? -1) : -1)));
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[46rem] text-sm">
        <thead>
          <tr className="text-left text-xs text-muted">
            <th className="py-2 pr-3 font-medium">Candidate</th>
            <th className="py-2 pr-3 font-medium">Done</th>
            <th className="py-2 pr-3 font-medium">Quality</th>
            <th className="py-2 pr-3 font-medium" title="Processing time ÷ audio length; lower is faster">Speed (RTF)</th>
            <th className="py-2 pr-3 font-medium">Peak memory</th>
            <th className="py-2 pr-3 font-medium">Reliability</th>
            <th className="py-2 pr-3 font-medium" title="PRD §20.1 experiment weights">Score</th>
            <th className="py-2 pr-3 font-medium" title="PRD §20.1 final-selection weights">Final</th>
            <th className="py-2 pr-3 font-medium">Usability</th>
            <th className="py-2 font-medium">Gates</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-border">
          {exp.summary.map((s: CandidateSummary) => (
            <tr key={s.key} className={cn(s.score_final === best && best >= 0 && "bg-accent/5")}>
              <td className="py-2 pr-3 font-medium">{candidateLabel(s.key)}</td>
              <td className="py-2 pr-3 tabular-nums">{s.completed}/{s.total}{s.skipped ? ` (+${s.skipped} skipped)` : ""}</td>
              <td className="py-2 pr-3 tabular-nums">{fmt(s.quality, (n) => `${n.toFixed(2)} / 5`)}<span className="block text-xs text-muted">{s.rated} rated</span></td>
              <td className="py-2 pr-3 tabular-nums">{fmt(s.median_rtf, (n) => n.toFixed(3))}</td>
              <td className="py-2 pr-3 tabular-nums">{fmt(s.peak_memory_bytes, formatBytes)}</td>
              <td className="py-2 pr-3 tabular-nums">{fmt(s.reliability, (n) => `${Math.round(n * 100)}%`)}</td>
              <td className="py-2 pr-3 tabular-nums">{fmt(s.score_experiment, (n) => n.toFixed(1))}</td>
              <td className="py-2 pr-3 tabular-nums font-medium">{fmt(s.score_final, (n) => n.toFixed(1))}</td>
              <td className="py-2 pr-3">
                <select aria-label={`Usability for ${candidateLabel(s.key)}`} value={exp.usability[s.key] ?? ""}
                  onChange={(e) => onUsability(s.key, e.target.value ? Number(e.target.value) : null)}
                  className="min-h-9 rounded-md border border-border bg-bg px-2">
                  <option value="">–</option>
                  {[1, 2, 3, 4, 5].map((n) => <option key={n} value={n}>{n}</option>)}
                </select>
              </td>
              <td className="py-2">
                <div className="flex flex-wrap gap-1">
                  {s.gates.map((g) => (
                    <Badge key={g.id} title={g.message} tone={g.passed === true ? "success" : g.passed === false ? "danger" : "neutral"}>{g.id}</Badge>
                  ))}
                </div>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="mt-2 text-xs text-muted">
        Scores need ratings: there is no ranking without listening. A failed gate (licence, memory, reliability) rules a
        candidate out whatever it scores.
      </p>
    </div>
  );
}

function RunCard({ run, dims, onScore }: { run: ExperimentRun; dims: string[]; onScore: (runId: string, dim: string, v: number | null) => void }) {
  return (
    <div className="rounded-lg border border-border p-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="text-sm font-medium">{candidateLabel(run.candidate_key)}</span>
        {run.status === "completed" && typeof run.metrics.real_time_factor === "number" && (
          <span className="text-xs text-muted tabular-nums">RTF {run.metrics.real_time_factor.toFixed(3)}</span>
        )}
      </div>
      {run.status === "skipped" && <p className="mt-1 text-xs text-muted">Skipped: {run.skip_reason}</p>}
      {run.status && OPEN.has(run.status) && <Progress className="mt-2" value={null} label="Waiting or generating" />}
      {(run.status === "failed" || run.status === "interrupted") && <p className="mt-1 text-xs text-danger">{run.error ?? "Failed"}</p>}
      {run.status === "canceled" && <p className="mt-1 text-xs text-muted">Canceled</p>}
      {run.status === "completed" && run.output_asset_id && (
        <>
          <audio controls preload="none" src={assetUrl(run.output_asset_id)} className="mt-2 h-10 w-full" />
          <div className="mt-2 space-y-1">
            {dims.map((d) => (
              <Rating key={d} label={DIMENSION_LABEL[d] ?? d} value={run.scores[d]} onChange={(v) => onScore(run.id, d, v)} />
            ))}
          </div>
        </>
      )}
    </div>
  );
}

function ExperimentDetail({ id }: { id: string }) {
  const navigate = useNavigate();
  const exp = useAsync(() => experiments.get(id), [id]);
  const [category, setCategory] = useState("all");
  const [unratedOnly, setUnratedOnly] = useState(false);
  const [decision, setDecision] = useState("");
  const [error, setError] = useState<string>();

  const data = exp.data;
  const open = data?.runs.filter((r) => r.status && OPEN.has(r.status)).length ?? 0;
  const attempted = data?.runs.filter((r) => r.status !== "skipped").length ?? 0;

  // Refresh while generations are still running.
  useEffect(() => {
    if (!open) return;
    const t = window.setInterval(() => void exp.reload(), 3000);
    return () => window.clearInterval(t);
  }, [open, exp]);

  const act = async (fn: () => Promise<Experiment>) => {
    setError(undefined);
    try {
      exp.setData(await fn());
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const cases = useMemo(() => {
    const groups = new Map<string, ExperimentRun[]>();
    for (const r of data?.runs ?? []) {
      if (category !== "all" && r.category !== category) continue;
      groups.set(r.case_id, [...(groups.get(r.case_id) ?? []), r]);
    }
    return [...groups.entries()].filter(([, runs]) =>
      !unratedOnly || runs.some((r) => r.status === "completed" && Object.keys(r.scores).length < (data?.review_dimensions.length ?? 0)),
    );
  }, [data, category, unratedOnly]);

  if (exp.error) return <Notice tone="error">{exp.error.message}</Notice>;
  if (!data) return <p className="text-sm text-muted">Loading…</p>;
  const categories = [...new Set(data.runs.map((r) => r.category))];

  return (
    <>
      <PageHeader
        title={data.name}
        description={data.objective || undefined}
        actions={
          <>
            {open > 0 && <Button variant="secondary" onClick={() => act(() => experiments.cancel(id))}><Square aria-hidden /> Stop remaining</Button>}
            <Button variant="ghost" onClick={() => navigate("/experiments")}>All experiments</Button>
          </>
        }
      />
      {error && <Notice tone="error" className="mb-4">{error}</Notice>}
      {open > 0 && (
        <Card className="mb-4">
          <p className="text-sm">{attempted - open} of {attempted} generated</p>
          <Progress className="mt-2" value={(attempted - open) / Math.max(attempted, 1)} label="Experiment progress" />
        </Card>
      )}

      <Card className="mb-4">
        <CardTitle>Summary</CardTitle>
        <div className="mt-3">
          <SummaryTable exp={data} onUsability={(k, v) => act(() => experiments.usability(id, k, v))} />
        </div>
      </Card>

      <Card className="mb-6">
        <CardTitle>Decision</CardTitle>
        {data.decision ? (
          <CardDescription>{data.decision}</CardDescription>
        ) : (
          <form className="mt-3 flex flex-col gap-2 sm:flex-row" onSubmit={(e) => { e.preventDefault(); void act(() => experiments.decide(id, decision)); }}>
            <input value={decision} onChange={(e) => setDecision(e.target.value)} placeholder="e.g. Keep Kokoro Heart as the English default"
              className="min-h-11 flex-1 rounded-lg border border-border bg-bg px-3 text-sm" />
            <Button type="submit" variant="secondary" disabled={!decision.trim()}>Record decision</Button>
          </form>
        )}
      </Card>

      <div className="mb-3 flex flex-wrap items-center gap-2">
        <h2 className="mr-auto text-lg font-semibold">Listen and rate</h2>
        <select aria-label="Category" value={category} onChange={(e) => setCategory(e.target.value)} className="min-h-11 rounded-lg border border-border bg-surface px-3 text-sm">
          <option value="all">All categories</option>
          {categories.map((c) => <option key={c} value={c}>{CATEGORY_LABEL[c] ?? c}</option>)}
        </select>
        <label className="flex min-h-11 items-center gap-2 text-sm">
          <input type="checkbox" checked={unratedOnly} onChange={(e) => setUnratedOnly(e.target.checked)} /> Unrated only
        </label>
      </div>
      <ul className="space-y-3">
        {cases.map(([caseId, runs]) => (
          <li key={caseId}>
            <Card>
              <div className="flex flex-wrap items-start justify-between gap-2">
                <p className="text-base leading-relaxed">{runs[0]?.text}</p>
                <Badge>{CATEGORY_LABEL[runs[0]?.category ?? ""] ?? runs[0]?.category}</Badge>
              </div>
              {runs[0]?.focus && <p className="mt-1 text-xs text-muted">Listen for: {runs[0].focus}</p>}
              <div className="mt-3 grid gap-3 md:grid-cols-2">
                {runs.map((r) => (
                  <RunCard key={r.id} run={r} dims={data.review_dimensions}
                    onScore={(runId, dim, v) => act(() => experiments.score(id, runId, { [dim]: v }))} />
                ))}
              </div>
            </Card>
          </li>
        ))}
      </ul>
    </>
  );
}

export function ExperimentsPage() {
  const { id } = useParams();
  return id ? <ExperimentDetail id={id} /> : <ExperimentList />;
}
