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
  type ExperimentTask,
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
  objects: "Objects",
  scenes: "Scenes",
  styles: "Styles",
  text: "Words in the picture",
  people: "People and hands",
};

function categoryLabel(category: string, image: boolean): string {
  if (image && category === "custom") return "Your descriptions";
  return CATEGORY_LABEL[category] ?? category;
}

const DIMENSION_LABEL: Record<string, string> = {
  pronunciation: "Pronunciation",
  naturalness: "Naturalness",
  intelligibility: "Clarity",
  pacing: "Pacing",
  identity: "Identity",
  proportions: "Proportions",
  pose: "Pose",
  garment_similarity: "Garment",
  photorealism: "Photorealism",
  anatomical_artifacts: "Free of defects",
};

const DIMENSION_HINT: Record<string, string> = {
  anatomical_artifacts: "Anatomical artifacts: 5 = clean, 1 = severe (extra fingers, broken limbs)",
  photorealism: "How photographic it looks. Leave blank for drawings and paintings",
};

// PRD §20.2 dimensions that compare against a reference photo (I1 and later).
// Text-to-image has none, so they are folded away and usually left blank.
const REFERENCE_DIMENSIONS = new Set(["identity", "pose", "garment_similarity"]);

const OPEN = new Set(["queued", "preparing", "loading_model", "processing", "finalizing", "downloading", "draft"]);

function candidateLabel(key: string): string {
  const [engine, rest] = key.split(":");
  return rest ? `${engine} · ${rest.replaceAll("voice_id=", "").replaceAll("preset=", "")}` : (engine ?? key);
}

// ------------------------------------------------------------------ list + create

function NewExperiment({ onCreated }: { onCreated: (id: string) => void }) {
  const [task, setTask] = useState<ExperimentTask>("audio.narrate");
  return (
    <div className="space-y-3">
      <div role="radiogroup" aria-label="What to compare" className="flex flex-wrap gap-2">
        {([["audio.narrate", "Narration"], ["image.generate", "Images"]] as const).map(([t, label]) => (
          <label key={t} className={cn("flex min-h-11 cursor-pointer items-center gap-2 rounded-lg border px-3 text-sm", task === t ? "border-accent bg-accent/10" : "border-border")}>
            <input type="radio" name="task" checked={task === t} onChange={() => setTask(t)} />
            {label}
          </label>
        ))}
      </div>
      {task === "audio.narrate" ? <NewNarrationExperiment onCreated={onCreated} /> : <NewImageExperiment onCreated={onCreated} />}
    </div>
  );
}

function NewNarrationExperiment({ onCreated }: { onCreated: (id: string) => void }) {
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
  const narrationBenchmarks = benchmarks.data?.filter((b) => b.task === "audio.narrate");
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
            {narrationBenchmarks?.map((b) => (
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

type ImageCandidate = { id: string; label: string; engine_id: string; settings: Record<string, unknown> };

function NewImageExperiment({ onCreated }: { onCreated: (id: string) => void }) {
  const benchmarks = useAsync(experiments.benchmarks);
  const models = useAsync(api.models);
  const [name, setName] = useState("Image settings comparison");
  const [source, setSource] = useState<string>("image-v1");
  const [prompts, setPrompts] = useState("");
  const [chosen, setChosen] = useState<ImageCandidate[]>([]);
  const [steps, setSteps] = useState(25);
  const [guidance, setGuidance] = useState(7.5);
  const [error, setError] = useState<string>();
  const [busy, setBusy] = useState(false);

  const engines = (models.data ?? []).filter((m) => m.tasks.includes("image.generate") && isUsable(m));
  const presetOptions: ImageCandidate[] = engines.flatMap((m) =>
    m.presets.map((p) => ({
      id: `${m.id}|preset=${p.id}`,
      label: `${m.display_name} · ${p.label}`,
      engine_id: m.id,
      settings: { preset: p.id },
    })),
  );
  const toggle = (o: ImageCandidate) =>
    setChosen((c) => (c.some((x) => x.id === o.id) ? c.filter((x) => x.id !== o.id) : c.length < 8 ? [...c, o] : c));
  const [engineId, setEngineId] = useState<string>();
  const customEngine = engines.find((m) => m.id === engineId) ?? engines[0];
  const addCustom = () => {
    if (!customEngine) return;
    const o = {
      id: `${customEngine.id}|guidance=${guidance},steps=${steps}`,
      label: `${customEngine.display_name} · ${steps} steps, guidance ${guidance}`,
      engine_id: customEngine.id,
      settings: { steps, guidance },
    };
    if (!chosen.some((x) => x.id === o.id)) toggle(o);
  };

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(undefined);
    try {
      const exp = await experiments.create({
        name,
        task: "image.generate",
        ...(source === "custom" ? { prompts: prompts.split("\n").filter((t) => t.trim()) } : { benchmark: source }),
        candidates: chosen.map((o) => ({ engine_id: o.engine_id, settings: o.settings })),
      });
      onCreated(exp.id);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };

  if (models.data && engines.length === 0) {
    return <Notice>Install an image engine first. Experiments compare engines and settings that are on this Mac.</Notice>;
  }

  const imageBenchmarks = benchmarks.data?.filter((b) => b.task === "image.generate");
  const bench = imageBenchmarks?.find((b) => b.id === source);
  const cases = source === "custom" ? prompts.split("\n").filter((t) => t.trim()).length : (bench?.cases ?? 0);
  const runs = cases * chosen.length;
  const choice = (on: boolean) =>
    cn("min-h-11 rounded-lg border px-3 text-sm", on ? "border-accent bg-accent/10 text-fg" : "border-border text-muted");

  return (
    <Card>
      <form onSubmit={submit} className="space-y-4">
        <CardTitle>New image experiment</CardTitle>
        <label className="block text-sm">
          <span className="mb-1 block font-medium">Name</span>
          <input value={name} onChange={(e) => setName(e.target.value)} maxLength={200} required
            className="min-h-11 w-full rounded-lg border border-border bg-bg px-3" />
        </label>
        <fieldset>
          <legend className="mb-1 text-sm font-medium">Descriptions</legend>
          <div className="flex flex-wrap gap-2">
            {imageBenchmarks?.map((b) => (
              <label key={b.id} className={cn("flex min-h-11 cursor-pointer items-center gap-2 rounded-lg border px-3 text-sm", source === b.id ? "border-accent bg-accent/10" : "border-border")}>
                <input type="radio" name="image-source" checked={source === b.id} onChange={() => setSource(b.id)} />
                {b.name} ({b.cases})
              </label>
            ))}
            <label className={cn("flex min-h-11 cursor-pointer items-center gap-2 rounded-lg border px-3 text-sm", source === "custom" ? "border-accent bg-accent/10" : "border-border")}>
              <input type="radio" name="image-source" checked={source === "custom"} onChange={() => setSource("custom")} />
              My own descriptions
            </label>
          </div>
          {bench && source !== "custom" && <p className="mt-2 text-xs text-muted">{bench.description}</p>}
          {source === "custom" && (
            <>
              <textarea value={prompts} onChange={(e) => setPrompts(e.target.value)} rows={5}
                placeholder="One description per line"
                className="mt-2 w-full rounded-lg border border-border bg-bg p-3 text-sm" />
              <p className="mt-1 text-xs text-muted">Each description gets one seed, shared by every candidate, so they start from the same noise.</p>
            </>
          )}
        </fieldset>
        <fieldset>
          <legend className="mb-1 text-sm font-medium">Compare (up to 8)</legend>
          <div className="flex flex-wrap gap-2">
            {presetOptions.map((o) => {
              const on = chosen.some((x) => x.id === o.id);
              return <button key={o.id} type="button" aria-pressed={on} onClick={() => toggle(o)} className={choice(on)}>{o.label}</button>;
            })}
            {chosen.filter((o) => !presetOptions.some((p) => p.id === o.id)).map((o) => (
              <button key={o.id} type="button" aria-pressed onClick={() => toggle(o)} className={choice(true)} title="Remove">{o.label}</button>
            ))}
          </div>
          <div className="mt-3 flex flex-wrap items-end gap-2 text-sm">
            {engines.length > 1 && (
              <label>
                <span className="mb-1 block text-xs text-muted">Engine</span>
                <select value={customEngine?.id} onChange={(e) => setEngineId(e.target.value)} className="min-h-11 rounded-lg border border-border bg-bg px-2">
                  {engines.map((m) => <option key={m.id} value={m.id}>{m.display_name}</option>)}
                </select>
              </label>
            )}
            <label>
              <span className="mb-1 block text-xs text-muted">Steps</span>
              <input type="number" min={10} max={50} value={steps} onChange={(e) => setSteps(Number(e.target.value))}
                className="min-h-11 w-20 rounded-lg border border-border bg-bg px-2 tabular-nums" />
            </label>
            <label>
              <span className="mb-1 block text-xs text-muted">Guidance</span>
              <input type="number" min={1} max={15} step={0.5} value={guidance} onChange={(e) => setGuidance(Number(e.target.value))}
                className="min-h-11 w-20 rounded-lg border border-border bg-bg px-2 tabular-nums" />
            </label>
            <Button type="button" variant="secondary" onClick={addCustom} disabled={chosen.length >= 8}>Add these settings</Button>
          </div>
        </fieldset>
        {error && <Notice tone="error">{error}</Notice>}
        <div className="flex items-center gap-3">
          <Button type="submit" disabled={busy || chosen.length === 0 || runs === 0}>
            Start {runs > 0 ? `(${runs} images)` : ""}
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
        description="Compare engines, voices and image settings on the same inputs, measure them on this Mac, and rate the results."
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
  const image = exp.task === "image.generate";
  const prd = image ? "PRD §20.2" : "PRD §20.1";
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[46rem] text-sm">
        <thead>
          <tr className="text-left text-xs text-muted">
            <th className="py-2 pr-3 font-medium">Candidate</th>
            <th className="py-2 pr-3 font-medium">Done</th>
            <th className="py-2 pr-3 font-medium">Quality</th>
            {image ? (
              <th className="py-2 pr-3 font-medium" title="Median seconds to make one image; lower is faster">Time per image</th>
            ) : (
              <th className="py-2 pr-3 font-medium" title="Processing time ÷ audio length; lower is faster">Speed (RTF)</th>
            )}
            <th className="py-2 pr-3 font-medium" title={image ? "Process memory plus GPU memory where measured" : undefined}>Peak memory</th>
            <th className="py-2 pr-3 font-medium">Reliability</th>
            <th className="py-2 pr-3 font-medium" title={`${prd} experiment weights`}>Score</th>
            <th className="py-2 pr-3 font-medium" title={`${prd} final-selection weights`}>Final</th>
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
              <td className="py-2 pr-3 tabular-nums">
                {image ? fmt(s.median_generation_s, (n) => `${n.toFixed(1)} s`) : fmt(s.median_rtf, (n) => n.toFixed(3))}
              </td>
              <td className="py-2 pr-3 tabular-nums">
                {fmt(s.peak_memory_bytes, formatBytes)}
                {image && s.peak_gpu_bytes != null && <span className="block text-xs text-muted">GPU {formatBytes(s.peak_gpu_bytes)}</span>}
              </td>
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
        {image ? "Scores need ratings: there is no ranking without looking." : "Scores need ratings: there is no ranking without listening."} A failed gate (licence, memory, reliability) rules a
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

function ImageCell({ run, dims, onScore }: { run: ExperimentRun | undefined; dims: string[]; onScore: (runId: string, dim: string, v: number | null) => void }) {
  if (!run) return <td className="p-2 align-top" />;
  const main = dims.filter((d) => !REFERENCE_DIMENSIONS.has(d));
  const reference = dims.filter((d) => REFERENCE_DIMENSIONS.has(d));
  const time = run.metrics.generation_s;
  return (
    <td className="w-60 min-w-60 p-2 align-top">
      {run.status === "completed" && run.output_asset_id ? (
        <>
          <a href={assetUrl(run.output_asset_id)} target="_blank" rel="noreferrer" title="Open full size">
            <img src={assetUrl(run.output_asset_id)} alt={`Result from ${candidateLabel(run.candidate_key)}`} loading="lazy"
              className="aspect-square w-full rounded-md border border-border bg-surface object-cover" />
          </a>
          {typeof time === "number" && <p className="mt-1 text-xs text-muted tabular-nums">{time.toFixed(1)} s</p>}
          <div className="mt-2 space-y-1">
            {main.map((d) => (
              <div key={d} title={DIMENSION_HINT[d]}>
                <Rating label={DIMENSION_LABEL[d] ?? d} value={run.scores[d]} onChange={(v) => onScore(run.id, d, v)} />
              </div>
            ))}
          </div>
          {reference.length > 0 && (
            <details className="mt-1" open={reference.some((d) => run.scores[d] != null)}>
              <summary className="cursor-pointer text-xs text-muted">Reference checks (need a reference photo)</summary>
              <div className="mt-1 space-y-1">
                {reference.map((d) => (
                  <Rating key={d} label={DIMENSION_LABEL[d] ?? d} value={run.scores[d]} onChange={(v) => onScore(run.id, d, v)} />
                ))}
              </div>
            </details>
          )}
        </>
      ) : (
        <div className="flex aspect-square w-full items-center justify-center rounded-md border border-dashed border-border p-3 text-center text-xs text-muted">
          {run.status === "skipped" && <span>Skipped: {run.skip_reason}</span>}
          {run.status && OPEN.has(run.status) && <Progress className="w-full" value={run.progress} label="Waiting or generating" />}
          {(run.status === "failed" || run.status === "interrupted") && <span className="text-danger">{run.error ?? "Failed"}</span>}
          {run.status === "canceled" && <span>Canceled</span>}
        </div>
      )}
    </td>
  );
}

/** Image runs as a grid: one row per description, one column per candidate. */
function ImageGrid({ exp, cases, onScore }: { exp: Experiment; cases: [string, ExperimentRun[]][]; onScore: (runId: string, dim: string, v: number | null) => void }) {
  const keys = exp.candidates.map((c) => c.key);
  return (
    <Card className="overflow-x-auto p-0">
      <table className="w-full border-collapse text-sm">
        <thead>
          <tr className="text-left text-xs text-muted">
            <th scope="col" className="sticky left-0 min-w-48 bg-surface p-2 font-medium">Description</th>
            {keys.map((k) => <th key={k} scope="col" className="p-2 font-medium">{candidateLabel(k)}</th>)}
          </tr>
        </thead>
        <tbody className="divide-y divide-border">
          {cases.map(([caseId, runs]) => {
            // A skipped custom run has no job, so no text or seed: use one that has them.
            const first = runs.find((r) => r.text) ?? runs[0];
            return (
              <tr key={caseId}>
                <th scope="row" className="sticky left-0 w-48 min-w-48 bg-surface p-2 text-left align-top font-normal">
                  <p className="leading-relaxed">{first?.text}</p>
                  <div className="mt-1 flex flex-wrap gap-1">
                    <Badge>{categoryLabel(first?.category ?? "", true)}</Badge>
                    {first?.seed != null && <Badge tone="neutral">seed {first.seed}</Badge>}
                  </div>
                  {first?.focus && <p className="mt-1 text-xs text-muted">Look for: {first.focus}</p>}
                </th>
                {keys.map((k) => (
                  <ImageCell key={k} run={runs.find((r) => r.candidate_key === k)} dims={exp.review_dimensions} onScore={onScore} />
                ))}
              </tr>
            );
          })}
        </tbody>
      </table>
    </Card>
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
    // Images: a run is rated once it has any score (not every PRD dimension applies).
    const unrated = (r: ExperimentRun) =>
      r.status === "completed" &&
      (data?.task === "image.generate"
        ? Object.keys(r.scores).length === 0
        : Object.keys(r.scores).length < (data?.review_dimensions.length ?? 0));
    return [...groups.entries()].filter(([, runs]) => !unratedOnly || runs.some(unrated));
  }, [data, category, unratedOnly]);

  if (exp.error) return <Notice tone="error">{exp.error.message}</Notice>;
  if (!data) return <p className="text-sm text-muted">Loading…</p>;
  const categories = [...new Set(data.runs.map((r) => r.category))];
  const image = data.task === "image.generate";
  const onScore = (runId: string, dim: string, v: number | null) => act(() => experiments.score(id, runId, { [dim]: v }));

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
            <input value={decision} onChange={(e) => setDecision(e.target.value)} placeholder={image ? "e.g. Use Balanced as the default image preset" : "e.g. Keep Kokoro Heart as the English default"}
              className="min-h-11 flex-1 rounded-lg border border-border bg-bg px-3 text-sm" />
            <Button type="submit" variant="secondary" disabled={!decision.trim()}>Record decision</Button>
          </form>
        )}
      </Card>

      <div className="mb-3 flex flex-wrap items-center gap-2">
        <h2 className="mr-auto text-lg font-semibold">{image ? "Look and rate" : "Listen and rate"}</h2>
        <select aria-label="Category" value={category} onChange={(e) => setCategory(e.target.value)} className="min-h-11 rounded-lg border border-border bg-surface px-3 text-sm">
          <option value="all">All categories</option>
          {categories.map((c) => <option key={c} value={c}>{categoryLabel(c, image)}</option>)}
        </select>
        <label className="flex min-h-11 items-center gap-2 text-sm">
          <input type="checkbox" checked={unratedOnly} onChange={(e) => setUnratedOnly(e.target.checked)} /> Unrated only
        </label>
      </div>
      {image ? (
        <ImageGrid exp={data} cases={cases} onScore={onScore} />
      ) : (
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
      )}
    </>
  );
}

export function ExperimentsPage() {
  const { id } = useParams();
  return id ? <ExperimentDetail id={id} /> : <ExperimentList />;
}
