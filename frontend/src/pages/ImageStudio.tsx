import { ImageIcon, Pencil, Plus, Sparkles, Wand2 } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { ImageResults } from "@/components/image/ImageResults";
import { LowMemoryToggle } from "@/components/image/LowMemoryToggle";
import { OutfitPanel } from "@/components/image/OutfitPanel";
import { PoseStudio } from "@/components/image/PoseStudio";
import { ReferencePicker } from "@/components/image/ReferencePicker";
import { PageHeader } from "@/components/layout/PageHeader";
import { Button } from "@/components/ui/button";
import { Card, CardTitle } from "@/components/ui/card";
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

const defaultProjectName = () => `Images ${new Date().toLocaleDateString()}`;

/**
 * "New" and "Rename" for image projects: a small name box, with a suggested
 * name selected so typing replaces it. Enter saves, Esc cancels.
 */
function ProjectNameButtons({ current, disabled, onCreate, onRename }: {
  current: Project | null;
  disabled: boolean;
  onCreate: (name: string) => Promise<void>;
  onRename: (id: string, name: string) => Promise<void>;
}) {
  const [editing, setEditing] = useState<"new" | "rename" | null>(null);
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();

  const open = (kind: "new" | "rename") => {
    setEditing(kind);
    setError(undefined);
    setName(kind === "rename" && current ? current.name : defaultProjectName());
  };
  const save = async () => {
    const clean = name.trim();
    if (!clean) return setError("Give the project a name.");
    setBusy(true);
    try {
      if (editing === "rename" && current) await onRename(current.id, clean);
      else await onCreate(clean);
      setEditing(null);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  if (!editing) {
    return (
      <>
        {current && (
          <Button variant="ghost" disabled={disabled} onClick={() => open("rename")} aria-label={`Rename ${current.name}`}>
            <Pencil aria-hidden /> Rename
          </Button>
        )}
        <Button variant="secondary" disabled={disabled} onClick={() => open("new")}>
          <Plus aria-hidden /> New
        </Button>
      </>
    );
  }
  return (
    <form className="flex flex-wrap items-center gap-2" onSubmit={(e) => { e.preventDefault(); void save(); }}>
      <label className="sr-only" htmlFor="project-name">{editing === "new" ? "New project name" : "Project name"}</label>
      <input id="project-name" autoFocus maxLength={200} value={name} onChange={(e) => setName(e.target.value)}
        onFocus={(e) => e.currentTarget.select()}
        onKeyDown={(e) => { if (e.key === "Escape") setEditing(null); }}
        placeholder="Project name"
        className="min-h-11 w-56 rounded-lg border border-accent bg-bg px-3 text-sm" />
      <Button type="submit" disabled={busy || !name.trim()}>{editing === "new" ? "Create" : "Save"}</Button>
      <Button type="button" variant="ghost" onClick={() => setEditing(null)}>Cancel</Button>
      {error && <span className="w-full text-xs text-danger">{error}</span>}
    </form>
  );
}

function Studio({ engines, projects, reloadProjects, models }: { engines: EngineInfo[]; projects: Project[]; reloadProjects: () => Promise<void>; models: EngineInfo[] }) {
  const { prefs } = usePrefs();
  const { jobs, upsert } = useJobs();
  const [params, setParams] = useSearchParams();
  const projectId = params.get("project") ?? projects[0]?.id ?? null;
  const canEdit = engines.some((e) => e.tasks.includes(TASK.edit));
  const [mode, setMode] = useState<Mode>(canEdit && params.get("mode") === "edit" ? "edit" : "create");
  const forMode = useMemo(() => engines.filter((e) => e.tasks.includes(TASK[mode])), [engines, mode]);
  // The model is the owner's choice: none is picked until they choose one, and
  // the choice is remembered on the Mac per mode (settings image.engine.*).
  const [chosen, setChosen] = useState<Record<Mode, string>>({ create: "", edit: "" });
  useEffect(() => {
    api.settings().then(
      (s) => setChosen({ create: s["image.engine.generate"] ?? "", edit: s["image.engine.edit"] ?? "" }),
      () => undefined,
    );
  }, []);
  const engine = forMode.find((e) => e.id === chosen[mode]);
  const chooseEngine = (id: string) => {
    setChosen((c) => ({ ...c, [mode]: id }));
    api.updateSettings({ [mode === "edit" ? "image.engine.edit" : "image.engine.generate"]: id }).catch((e: Error) => setError(e.message));
  };
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
  const needsPhoto = mode === "edit" && !refs.some((r) => r.role === "person");
  const blocked = !engine || !prompt.trim() || tooLong || badSeed || needsPhoto || submitting;

  const createProject = async (name = defaultProjectName()) => {
    const p = await api.createProject({ name, module: "image" });
    await reloadProjects();
    return p;
  };
  const newProject = async (name?: string) => {
    const p = await createProject(name);
    setParams({ project: p.id, ...(mode === "edit" ? { mode } : {}) });
    return p.id;
  };
  // Pose and outfit panels call this before every job: reuse the open project.
  const ensureProject = async () => projectId ?? (await newProject());

  const switchMode = (m: Mode) => {
    setMode(m);
    setParams({ ...(projectId ? { project: projectId } : {}), ...(m === "edit" ? { mode: m } : {}) });
  };

  const generate = async () => {
    if (!engine) return;
    setSubmitting(true);
    setError(undefined);
    try {
      const pid = await ensureProject();
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
<ProjectNameButtons
              current={projects.find((p) => p.id === projectId) ?? null}
              disabled={submitting}
              onCreate={async (name) => void (await newProject(name))}
              onRename={async (id, name) => { await api.updateProject(id, { name }); await reloadProjects(); }} />
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
            {!engine && <span className="ml-3 text-xs text-muted">Choose a model.</span>}
            {engine && needsPhoto && <span className="ml-3 text-xs text-muted">Add the original photo.</span>}
          </div>
        </Card>

        <Card>
          <CardTitle>Model and quality</CardTitle>
          <div role="radiogroup" aria-labelledby="image-model-label" className="mt-3 grid gap-2">
            <p id="image-model-label" className="text-sm font-medium">
              Model {!engine && <span className="font-normal text-muted">· choose one to start; Rasva remembers it</span>}
            </p>
            {forMode.map((e) => (
              <button key={e.id} role="radio" aria-checked={engine?.id === e.id} onClick={() => chooseEngine(e.id)}
                className={cn("min-h-11 rounded-lg border px-3 py-2 text-left", engine?.id === e.id ? "border-accent bg-accent/10" : "border-border")}>
                <span className="block text-sm font-medium">{e.display_name}</span>
                <span className="block text-xs text-muted">{e.summary}</span>
              </button>
            ))}
          </div>
          {engine && engine.presets.length > 0 && <p className="mt-4 text-sm font-medium">Quality</p>}
          {engine && (
            <div role="radiogroup" aria-label="Quality" className="mt-2 grid gap-2">
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
          {engine && <LowMemoryToggle engine={engine} />}
          <p className="mt-4 text-xs text-muted">
            Images are labelled as AI-generated in their details. These engines run without a content filter, so what you
            create is your responsibility under their licences.
          </p>
        </Card>
      </div>

      <section aria-label="Results" className="mt-4">
        {projectId ? (
          <ImageResults projectId={projectId} jobs={results} />
        ) : (
          <p className="text-sm text-muted">Your images appear here. Generating one takes a while on the first run while the engine loads.</p>
        )}
      </section>

      {/* Phase I2: same person, new pose (components/image/PoseStudio.tsx). */}
      <PoseStudio projectId={projectId} models={models} ensureProject={ensureProject} />

      {/* Phase I3: change outfit (components/image/OutfitPanel.tsx). */}
      <OutfitPanel projectId={projectId} ensureProject={ensureProject} />
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
