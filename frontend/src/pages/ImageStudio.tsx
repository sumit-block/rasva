import { ImageIcon, Plus, Sparkles, Wand2 } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { JobRow } from "@/components/JobRow";
import { PoseStudio } from "@/components/image/PoseStudio";
import { ReferencePicker } from "@/components/image/ReferencePicker";
import { PageHeader } from "@/components/layout/PageHeader";
import { Button } from "@/components/ui/button";
import { Card, CardDescription, CardTitle } from "@/components/ui/card";
import { EmptyState, Notice } from "@/components/ui/notice";
import { useAsync } from "@/hooks/useAsync";
import { useJobs } from "@/hooks/useJobs";
import { usePrefs } from "@/hooks/usePrefs";
import { api, isUsable, type EngineInfo, type Job, type Project, type ReferenceInput } from "@/lib/api";
import { cn } from "@/lib/utils";

type Mode = "create" | "edit";
const TASK: Record<Mode, string> = { create: "image.generate", edit: "image.edit" };
const IMAGE_TASKS = new Set(Object.values(TASK));
const MAX_CHARS = 1000;
const MAX_SEED = 2 ** 32 - 1;

function Studio({ engines, projects, reloadProjects, models }: { engines: EngineInfo[]; projects: Project[]; reloadProjects: () => Promise<void>; models: EngineInfo[] }) {
  const { prefs } = usePrefs();
  const { jobs, upsert } = useJobs();
  const [params, setParams] = useSearchParams();
  const projectId = params.get("project") ?? projects[0]?.id ?? null;
  const canEdit = engines.some((e) => e.tasks.includes(TASK.edit));
  const [mode, setMode] = useState<Mode>(canEdit && params.get("mode") === "edit" ? "edit" : "create");
  const forMode = useMemo(() => engines.filter((e) => e.tasks.includes(TASK[mode])), [engines, mode]);
  const [engineId, setEngineId] = useState(forMode[0]?.id ?? "");
  const engine = forMode.find((e) => e.id === engineId) ?? forMode[0];
  const [prompt, setPrompt] = useState("");
  const [negative, setNegative] = useState("");
  const [preset, setPreset] = useState("");
  const [steps, setSteps] = useState(25);
  const [guidance, setGuidance] = useState(7.5);
  const [seed, setSeed] = useState("");
  const [refs, setRefs] = useState<ReferenceInput[]>([]);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string>();
  const advanced = prefs.mode === "advanced";
  // Only Stable Diffusion has a negative prompt and step/guidance knobs worth exposing.
  const knobs = engine?.features.includes("negative_prompt") ?? false;

  useEffect(() => {
    // A sensible quality preset for the chosen engine: the middle one.
    setPreset(engine?.presets[1]?.id ?? engine?.presets[0]?.id ?? "");
  }, [engine?.id]);

  const seedValue = seed.trim() === "" ? undefined : Number(seed);
  const badSeed = seedValue !== undefined && !(Number.isInteger(seedValue) && seedValue >= 0 && seedValue <= MAX_SEED);
  const tooLong = prompt.length > MAX_CHARS || negative.length > MAX_CHARS;
  const needsPhoto = mode === "edit" && refs.length === 0;
  const blocked = !engine || !prompt.trim() || tooLong || badSeed || needsPhoto || submitting;

  const createProject = async () => {
    const p = await api.createProject({ name: `Images ${new Date().toLocaleDateString()}`, module: "image" });
    await reloadProjects();
    return p;
  };
  const ensureProject = async () => {
    const p = await createProject();
    setParams({ project: p.id, ...(mode === "edit" ? { mode } : {}) });
    return p.id;
  };

  const switchMode = (m: Mode) => {
    setMode(m);
    const next = engines.filter((e) => e.tasks.includes(TASK[m]));
    if (!next.some((e) => e.id === engineId)) setEngineId(next[0]?.id ?? "");
    setParams({ ...(projectId ? { project: projectId } : {}), ...(m === "edit" ? { mode: m } : {}) });
  };

  const generate = async () => {
    if (!engine) return;
    setSubmitting(true);
    setError(undefined);
    try {
      const pid = projectId ?? (await ensureProject());
      upsert(await api.createJob({
        project_id: pid, task: TASK[mode], engine_id: engine.id,
        inputs: {
          prompt,
          ...(mode === "edit" ? { references: refs } : {}),
          ...(advanced && knobs && negative.trim() ? { negative_prompt: negative } : {}),
          ...(advanced && seedValue !== undefined ? { seed: seedValue } : {}),
          ...(preset ? { preset } : knobs ? { steps, guidance } : {}),
        },
      }));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSubmitting(false);
    }
  };

  // The live list holds only recent jobs of every kind; load this project's own
  // history so older images stay visible, and let live updates win.
  const [history, setHistory] = useState<Job[]>([]);
  useEffect(() => {
    if (!projectId) return setHistory([]);
    let alive = true;
    api.projectJobs(projectId).then((js) => alive && setHistory(js), () => undefined);
    return () => {
      alive = false;
    };
  }, [projectId]);
  const results = useMemo(() => {
    const byId = new Map(history.map((j) => [j.id, j]));
    for (const j of jobs) if (j.project_id === projectId) byId.set(j.id, j);
    return [...byId.values()]
      .filter((j) => IMAGE_TASKS.has(j.task))
      .sort((a, b) => b.created_at.localeCompare(a.created_at));
  }, [history, jobs, projectId]);

  return (
    <>
      <PageHeader
        title="Image Studio"
        description="Make a picture from a description, or change one of your photos. Everything runs on your Mac."
        actions={
          <div className="flex flex-wrap items-center gap-2">
            <label className="sr-only" htmlFor="image-project">Project</label>
            <select id="image-project" value={projectId ?? ""} onChange={(e) => setParams({ project: e.target.value, ...(mode === "edit" ? { mode } : {}) })}
              className="min-h-11 max-w-60 rounded-lg border border-border bg-surface px-3 text-sm">
              {projects.length === 0 && <option value="">New project</option>}
              {projects.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
            </select>
            <Button variant="secondary" disabled={submitting}
              onClick={() => void ensureProject().catch((e: Error) => setError(e.message))}>
              <Plus aria-hidden /> New
            </Button>
          </div>
        }
      />

      {canEdit && (
        <div role="tablist" aria-label="What to do" className="mb-4 inline-flex rounded-lg border border-border bg-surface-2 p-1">
          {([["create", "Create from text"], ["edit", "Edit a photo"]] as const).map(([m, label]) => (
            <button key={m} role="tab" aria-selected={mode === m} onClick={() => switchMode(m)}
              className={cn("min-h-10 rounded-md px-4 text-sm", mode === m ? "bg-surface text-fg shadow-sm" : "text-muted hover:text-fg")}>
              {label}
            </button>
          ))}
        </div>
      )}

      <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_22rem]">
        <Card>
          {mode === "edit" && (
            <div className="mb-4">
              <ReferencePicker projectId={projectId} value={refs} onChange={setRefs} ensureProject={ensureProject} />
            </div>
          )}
          <CardTitle>{mode === "edit" ? "Describe the change" : "Describe the picture"}</CardTitle>
          <label htmlFor="prompt" className="sr-only">Description</label>
          <textarea id="prompt" value={prompt} onChange={(e) => setPrompt(e.target.value)} rows={4}
            placeholder={mode === "edit"
              ? "Keep the same person and face. Dress them in the blue kurta from the clothing photo, standing in a garden."
              : "A lighthouse on a rocky coast at dusk, warm light in the window, painted in watercolour"}
            className="mt-3 w-full resize-y rounded-lg border border-border bg-bg p-3 text-base leading-relaxed text-fg placeholder:text-muted" />
          <div className="mt-1 flex justify-between text-xs text-muted">
            <span>{knobs ? "Only about the first 60 words are used." : mode === "edit" ? "Say what to keep and what to change." : ""}</span>
            <span className={cn(prompt.length > MAX_CHARS && "text-danger")}>{prompt.length} / {MAX_CHARS}</span>
          </div>
          {advanced && (
            <>
              {knobs && (
                <>
                  <label htmlFor="negative" className="mt-4 block text-sm font-medium">Avoid</label>
                  <input id="negative" value={negative} onChange={(e) => setNegative(e.target.value)}
                    placeholder="blurry, extra fingers, text"
                    className="mt-1 min-h-11 w-full rounded-lg border border-border bg-bg px-3 text-sm" />
                </>
              )}
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
              {mode === "edit" ? <Wand2 aria-hidden /> : <Sparkles aria-hidden />} {mode === "edit" ? "Edit photo" : "Generate image"}
            </Button>
            {needsPhoto && <span className="ml-3 text-xs text-muted">Choose at least one photo.</span>}
          </div>
        </Card>

        <Card>
          <CardTitle>Quality</CardTitle>
          {engine && <CardDescription>{engine.display_name}: {engine.summary}</CardDescription>}
          {advanced && forMode.length > 1 && (
            <label className="mt-4 block text-sm">
              <span className="mb-1 block font-medium">Engine</span>
              <select value={engine?.id ?? ""} onChange={(e) => setEngineId(e.target.value)} className="min-h-11 w-full rounded-lg border border-border bg-bg px-3">
                {forMode.map((e) => <option key={e.id} value={e.id}>{e.display_name}</option>)}
              </select>
            </label>
          )}
          {engine && (
            <div role="radiogroup" aria-label="Quality" className="mt-4 grid gap-2">
              {[...engine.presets, ...(advanced && knobs ? [{ id: "", label: "Custom", description: "Use the settings below." }] : [])].map((p) => (
                <button key={p.id || "custom"} role="radio" aria-checked={preset === p.id} onClick={() => setPreset(p.id)}
                  className={cn("min-h-11 rounded-lg border px-3 py-2 text-left", preset === p.id ? "border-accent bg-accent/10" : "border-border")}>
                  <span className="block text-sm font-medium">{p.label}</span>
                  <span className="block text-xs text-muted">{p.description}</span>
                </button>
              ))}
            </div>
          )}
          {advanced && knobs && !preset && (
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
            Images are labelled as AI-generated in their details. These engines run without a content filter, so what you
            create is your responsibility under their licences.
          </p>
        </Card>
      </div>

      <section aria-label="Results" className="mt-4">
        {results.length === 0 ? (
          <p className="text-sm text-muted">Your images appear here. The first one takes longer while the engine loads.</p>
        ) : (
          <ul className="grid gap-3 md:grid-cols-2">{results.map((j) => <JobRow key={j.id} job={j} />)}</ul>
        )}
      </section>

      {/* Phase I2: same person, new pose (components/image/PoseStudio.tsx). */}
      <PoseStudio projectId={projectId} models={models}
        ensureProject={async () => {
          if (projectId) return projectId;
          const p = await createProject();
          setParams({ project: p.id });
          return p.id;
        }} />
    </>
  );
}

export function ImageStudioPage() {
  const navigate = useNavigate();
  const models = useAsync(api.models);
  const projects = useAsync(() => api.projects({ module: "image" }));

  const engines = useMemo(
    () => (models.data ?? []).filter((m) => m.tasks.some((t) => IMAGE_TASKS.has(t)) && isUsable(m)),
    [models.data],
  );
  const candidate = models.data?.find((m) => m.tasks.some((t) => IMAGE_TASKS.has(t)));

  if (models.error || projects.error) return <Notice tone="error">{(models.error ?? projects.error)?.message}</Notice>;
  if (!models.data || !projects.data) return <p className="text-sm text-muted">Loading…</p>;
  if (engines.length === 0) {
    return (
      <>
        <PageHeader title="Image Studio" />
        <EmptyState icon={<ImageIcon />} title="Install an image engine to start"
          action={<Button onClick={() => navigate("/models")}>Go to Models</Button>}>
          {candidate && !candidate.compatibility.compatible
            ? candidate.compatibility.reasons.join(" ")
            : "Pictures need an image engine on this Mac. Stable Diffusion 1.5 is the small first one (about 2 GB)."}
        </EmptyState>
        {/* Phase I2: pose changes don't need the description engine. */}
        {models.data.some((m) => isUsable(m) && (m.tasks.includes("image.edit") || m.tasks.includes("image.pose"))) && (
          <PoseStudio projectId={projects.data[0]?.id ?? null} models={models.data}
            ensureProject={async () => {
              const existing = projects.data?.[0]?.id;
              if (existing) return existing;
              const p = await api.createProject({ name: `Images ${new Date().toLocaleDateString()}`, module: "image" });
              await projects.reload();
              return p.id;
            }} />
        )}
      </>
    );
  }
  return <Studio engines={engines} projects={projects.data} reloadProjects={projects.reload} models={models.data} />;
}
