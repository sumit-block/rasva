import { ImageIcon, Plus, Sparkles } from "lucide-react";
import { useMemo, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { JobRow } from "@/components/JobRow";
import { PageHeader } from "@/components/layout/PageHeader";
import { Button } from "@/components/ui/button";
import { Card, CardDescription, CardTitle } from "@/components/ui/card";
import { EmptyState, Notice } from "@/components/ui/notice";
import { useAsync } from "@/hooks/useAsync";
import { useJobs } from "@/hooks/useJobs";
import { usePrefs } from "@/hooks/usePrefs";
import { api, isUsable, type EngineInfo, type Project } from "@/lib/api";
import { cn } from "@/lib/utils";

const TASK = "image.generate";
const MAX_CHARS = 1000;
const MAX_SEED = 2 ** 32 - 1;

function Studio({ engine, projects, reloadProjects }: { engine: EngineInfo; projects: Project[]; reloadProjects: () => Promise<void> }) {
  const { prefs } = usePrefs();
  const { jobs, upsert } = useJobs();
  const [params, setParams] = useSearchParams();
  const projectId = params.get("project") ?? projects[0]?.id ?? null;
  const [prompt, setPrompt] = useState("");
  const [negative, setNegative] = useState("");
  const [preset, setPreset] = useState(engine.presets[1]?.id ?? engine.presets[0]?.id ?? "");
  const [steps, setSteps] = useState(25);
  const [guidance, setGuidance] = useState(7.5);
  const [seed, setSeed] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string>();
  const advanced = prefs.mode === "advanced";

  const seedValue = seed.trim() === "" ? undefined : Number(seed);
  const badSeed = seedValue !== undefined && !(Number.isInteger(seedValue) && seedValue >= 0 && seedValue <= MAX_SEED);
  const tooLong = prompt.length > MAX_CHARS || negative.length > MAX_CHARS;
  const blocked = !prompt.trim() || tooLong || badSeed || submitting;

  const createProject = async () => {
    const p = await api.createProject({ name: `Images ${new Date().toLocaleDateString()}`, module: "image" });
    await reloadProjects();
    return p;
  };

  const generate = async () => {
    setSubmitting(true);
    setError(undefined);
    try {
      const pid = projectId ?? (await createProject()).id;
      if (!projectId) setParams({ project: pid });
      upsert(await api.createJob({
        project_id: pid, task: TASK, engine_id: engine.id,
        inputs: {
          prompt,
          ...(advanced && negative.trim() ? { negative_prompt: negative } : {}),
          ...(advanced && seedValue !== undefined ? { seed: seedValue } : {}),
          ...(preset ? { preset } : { steps, guidance }),
        },
      }));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSubmitting(false);
    }
  };

  const results = useMemo(
    () => jobs.filter((j) => j.task === TASK && j.project_id === projectId),
    [jobs, projectId],
  );

  return (
    <>
      <PageHeader
        title="Image Studio"
        description="Describe a picture and generate it on your Mac. This first engine makes 512 × 512 drafts."
        actions={
          <div className="flex flex-wrap items-center gap-2">
            <label className="sr-only" htmlFor="image-project">Project</label>
            <select id="image-project" value={projectId ?? ""} onChange={(e) => setParams({ project: e.target.value })}
              className="min-h-11 max-w-60 rounded-lg border border-border bg-surface px-3 text-sm">
              {projects.length === 0 && <option value="">New project</option>}
              {projects.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
            </select>
            <Button variant="secondary" disabled={submitting}
              onClick={() => void createProject().then((p) => setParams({ project: p.id }), (e: Error) => setError(e.message))}>
              <Plus aria-hidden /> New
            </Button>
          </div>
        }
      />

      <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_22rem]">
        <Card>
          <CardTitle>Describe the picture</CardTitle>
          <label htmlFor="prompt" className="sr-only">Description</label>
          <textarea id="prompt" value={prompt} onChange={(e) => setPrompt(e.target.value)} rows={4}
            placeholder="A lighthouse on a rocky coast at dusk, warm light in the window, painted in watercolour"
            className="mt-3 w-full resize-y rounded-lg border border-border bg-bg p-3 text-base leading-relaxed text-fg placeholder:text-muted" />
          <div className="mt-1 flex justify-between text-xs text-muted">
            <span>Only about the first 60 words are used.</span>
            <span className={cn(prompt.length > MAX_CHARS && "text-danger")}>{prompt.length} / {MAX_CHARS}</span>
          </div>
          {advanced && (
            <>
              <label htmlFor="negative" className="mt-4 block text-sm font-medium">Avoid</label>
              <input id="negative" value={negative} onChange={(e) => setNegative(e.target.value)}
                placeholder="blurry, extra fingers, text"
                className="mt-1 min-h-11 w-full rounded-lg border border-border bg-bg px-3 text-sm" />
              <label htmlFor="seed" className="mt-4 block text-sm font-medium">Seed</label>
              <input id="seed" inputMode="numeric" value={seed} onChange={(e) => setSeed(e.target.value)}
                placeholder="Random"
                className="mt-1 min-h-11 w-48 rounded-lg border border-border bg-bg px-3 text-sm tabular-nums" />
              <p className="mt-1 text-xs text-muted">The same seed and description give the same picture. Each result shows its seed.</p>
              {badSeed && <Notice tone="error" className="mt-2">The seed must be a whole number from 0 to {MAX_SEED.toLocaleString()}.</Notice>}
            </>
          )}
          {error && <Notice tone="error" className="mt-3">{error}</Notice>}
          <div className="mt-4">
            <Button disabled={blocked} onClick={() => void generate()}>
              <Sparkles aria-hidden /> Generate image
            </Button>
          </div>
        </Card>

        <Card>
          <CardTitle>Quality</CardTitle>
          <CardDescription>{engine.display_name}: {engine.summary}</CardDescription>
          <div role="radiogroup" aria-label="Quality" className="mt-4 grid gap-2">
            {[...engine.presets, ...(advanced ? [{ id: "", label: "Custom", description: "Use the settings below." }] : [])].map((p) => (
              <button key={p.id || "custom"} role="radio" aria-checked={preset === p.id} onClick={() => setPreset(p.id)}
                className={cn("min-h-11 rounded-lg border px-3 py-2 text-left", preset === p.id ? "border-accent bg-accent/10" : "border-border")}>
                <span className="block text-sm font-medium">{p.label}</span>
                <span className="block text-xs text-muted">{p.description}</span>
              </button>
            ))}
          </div>
          {advanced && !preset && (
            <>
              <label className="mt-4 block text-sm">
                <span className="mb-1 flex justify-between font-medium">Steps <span className="text-muted tabular-nums">{steps}</span></span>
                <input type="range" min={10} max={50} step={1} value={steps} onChange={(e) => setSteps(Number(e.target.value))} className="w-full accent-[var(--accent)]" />
              </label>
              <label className="mt-4 block text-sm">
                <span className="mb-1 flex justify-between font-medium">Follow the description <span className="text-muted tabular-nums">{guidance.toFixed(1)}</span></span>
                <input type="range" min={1} max={15} step={0.5} value={guidance} onChange={(e) => setGuidance(Number(e.target.value))} className="w-full accent-[var(--accent)]" />
              </label>
            </>
          )}
          <p className="mt-4 text-xs text-muted">
            Images are labelled as AI-generated in their details. This engine runs without a content filter, so what you
            create is your responsibility under its licence.
          </p>
        </Card>
      </div>

      <section aria-label="Results" className="mt-4">
        {results.length === 0 ? (
          <p className="text-sm text-muted">Your images appear here. Generating one takes a while on the first run while the engine loads.</p>
        ) : (
          <ul className="grid gap-3 md:grid-cols-2">{results.map((j) => <JobRow key={j.id} job={j} />)}</ul>
        )}
      </section>
    </>
  );
}

export function ImageStudioPage() {
  const navigate = useNavigate();
  const models = useAsync(api.models);
  const projects = useAsync(() => api.projects({ module: "image" }));

  const engine = useMemo(() => (models.data ?? []).find((m) => m.tasks.includes(TASK) && isUsable(m)), [models.data]);
  const candidate = models.data?.find((m) => m.tasks.includes(TASK));

  if (models.error || projects.error) return <Notice tone="error">{(models.error ?? projects.error)?.message}</Notice>;
  if (!models.data || !projects.data) return <p className="text-sm text-muted">Loading…</p>;
  if (!engine) {
    return (
      <>
        <PageHeader title="Image Studio" />
        <EmptyState icon={<ImageIcon />} title="Install an image engine to start"
          action={<Button onClick={() => navigate("/models")}>Go to Models</Button>}>
          {candidate && !candidate.compatibility.compatible
            ? candidate.compatibility.reasons.join(" ")
            : "Pictures need an image engine on this Mac. Stable Diffusion 1.5 is the small first one (about 2 GB)."}
        </EmptyState>
      </>
    );
  }
  return <Studio engine={engine} projects={projects.data} reloadProjects={projects.reload} />;
}
