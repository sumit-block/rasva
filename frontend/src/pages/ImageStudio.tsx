import { ImageIcon, ImagePlus, Sparkles, X } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { JobRow } from "@/components/JobRow";
import { PasteButton } from "@/components/image/PasteButton";
import { PageHeader } from "@/components/layout/PageHeader";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { EmptyState, Notice } from "@/components/ui/notice";
import { useAsync } from "@/hooks/useAsync";
import { useJobs } from "@/hooks/useJobs";
import {
  api,
  assetUrl,
  isUsable,
  uploadAsset,
  type Asset,
  type EngineInfo,
  type Job,
  type PhotoRole,
  type Project,
} from "@/lib/api";
import { usePasteTarget } from "@/lib/paste";
import { cn } from "@/lib/utils";

const TASK = { generate: "image.generate", edit: "image.edit" } as const;
const MAX_CHARS = 2000;
const MAX_PHOTOS = 4;
// Photos reach the model in the order attached; roles only carry that order.
const ORDER: PhotoRole[] = ["person", "garment", "pose", "background"];
const PROJECT_NAME = "Images";

/**
 * Image Studio, simple form (owner decision, ADR-0011): write what you want, attach
 * or paste up to four photos if you like, and generate. With photos it edits them;
 * without, it makes a new picture. The installed image model does the rest.
 */
function Studio({ engines, projects, reloadProjects }: { engines: EngineInfo[]; projects: Project[]; reloadProjects: () => Promise<void> }) {
  const { jobs, upsert } = useJobs();
  const [projectId, setProjectId] = useState<string | null>(projects[0]?.id ?? null);
  const [prompt, setPrompt] = useState("");
  const [photos, setPhotos] = useState<Asset[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();
  const fileInput = useRef<HTMLInputElement>(null);

  const task = photos.length > 0 ? TASK.edit : TASK.generate;
  const engine = engines.find((e) => e.tasks.includes(task));

  const ensureProject = async () => {
    if (projectId) return projectId;
    const p = await api.createProject({ name: PROJECT_NAME, module: "image" });
    await reloadProjects();
    setProjectId(p.id);
    return p.id;
  };

  const attach = async (files: File[]) => {
    const room = MAX_PHOTOS - photos.length;
    if (room <= 0) return setError(`Use at most ${MAX_PHOTOS} photos at once.`);
    setBusy(true);
    setError(undefined);
    try {
      const pid = await ensureProject();
      const added: Asset[] = [];
      for (const f of files.slice(0, room)) added.push(await uploadAsset(pid, f, "reference", ORDER[photos.length + added.length]));
      setPhotos((ps) => [...ps, ...added]);
      if (files.length > room) setError(`Only the first ${room} were added: at most ${MAX_PHOTOS} photos at once.`);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  // ⌘V anywhere on the page attaches a copied picture (text fields keep normal paste).
  const paste = usePasteTarget((f) => void attach([f]), busy);

  const generate = async () => {
    if (!engine || !prompt.trim() || prompt.length > MAX_CHARS) return;
    setBusy(true);
    setError(undefined);
    try {
      const pid = await ensureProject();
      upsert(await api.createJob({
        project_id: pid, task, engine_id: engine.id,
        inputs: {
          prompt,
          ...(photos.length ? { references: photos.map((a, i) => ({ asset_id: a.id, role: ORDER[i] })) } : {}),
        },
      }));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  // Results: this project's image jobs, newest first, with live updates.
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
      .filter((j) => j.task.startsWith("image."))
      .sort((a, b) => b.created_at.localeCompare(a.created_at));
  }, [history, jobs, projectId]);

  const tooLong = prompt.length > MAX_CHARS;
  const ready = !!engine && !!prompt.trim() && !tooLong && !busy;

  return (
    <>
      <PageHeader title="Image Studio" description="Write what you want. Attach or paste a photo to change it, or leave it empty for a new picture." />

      <div {...paste}
        onDragOver={(e) => e.preventDefault()}
        onDrop={(e) => {
          e.preventDefault();
          const files = [...e.dataTransfer.files].filter((f) => f.type.startsWith("image/"));
          if (files.length) void attach(files);
        }}>
        <Card>
          <label htmlFor="image-prompt" className="sr-only">What do you want?</label>
          <textarea id="image-prompt" value={prompt} onChange={(e) => setPrompt(e.target.value)} rows={4}
            onKeyDown={(e) => { if ((e.metaKey || e.ctrlKey) && e.key === "Enter") void generate(); }}
            placeholder={photos.length
              ? "Say what to change and what to keep, e.g. \"Put her in the green kurta from the second photo; keep her face and pose.\""
              : "Describe the picture, e.g. \"A lighthouse on a rocky coast at dusk, watercolour.\""}
            className="w-full resize-y rounded-lg border border-border bg-bg p-3 text-base leading-relaxed text-fg placeholder:text-muted" />

          {photos.length > 0 && (
            <ul className="mt-3 flex flex-wrap gap-3" aria-label="Attached photos">
              {photos.map((a, i) => (
                <li key={a.id} className="relative">
                  <img src={assetUrl(a.id)} alt={`Photo ${i + 1}: ${a.original_name}`} className="size-20 rounded-md border border-border object-cover" />
                  <span className="absolute left-1 top-1 rounded bg-bg/80 px-1 text-xs">{i + 1}</span>
                  <button type="button" onClick={() => setPhotos((ps) => ps.filter((p) => p.id !== a.id))}
                    aria-label={`Remove photo ${i + 1}`}
                    className="absolute -right-2 -top-2 grid size-7 place-items-center rounded-full border border-border bg-surface text-muted hover:text-fg">
                    <X className="size-4" aria-hidden />
                  </button>
                </li>
              ))}
            </ul>
          )}

          <div className="mt-3 flex flex-wrap items-center gap-2">
            <input ref={fileInput} type="file" accept="image/png,image/jpeg,image/webp" multiple className="sr-only" tabIndex={-1}
              aria-label="Attach photos"
              onChange={(e) => { const fs = [...(e.target.files ?? [])]; e.target.value = ""; if (fs.length) void attach(fs); }} />
            <Button variant="secondary" disabled={busy || photos.length >= MAX_PHOTOS} onClick={() => fileInput.current?.click()}>
              <ImagePlus aria-hidden /> Attach photo
            </Button>
            <PasteButton onFile={(f) => void attach([f])} onError={setError} disabled={busy || photos.length >= MAX_PHOTOS} />
            <span className={cn("text-xs text-muted", tooLong && "text-danger")}>{prompt.length} / {MAX_CHARS}</span>
            <Button className="ml-auto" disabled={!ready} onClick={() => void generate()}>
              <Sparkles aria-hidden /> {busy ? "Working…" : "Generate"}
            </Button>
          </div>
          {!engine && photos.length > 0 && <Notice className="mt-3">The installed image model cannot change photos.</Notice>}
          {error && <Notice tone="error" className="mt-3">{error}</Notice>}
          <p className="mt-3 text-xs text-muted">
            {engine ? `${engine.display_name} runs on this Mac and takes several minutes per picture. ` : ""}
            Pictures are labelled as AI-generated in their details. There is no content filter.
          </p>
        </Card>
      </div>

      <section aria-label="Results" className="mt-4">
        {results.length === 0 ? (
          <p className="text-sm text-muted">Your pictures appear here.</p>
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
  const engines = useMemo(
    () => (models.data ?? []).filter((m) => m.modality === "image" && m.tasks.includes(TASK.generate) && isUsable(m)),
    [models.data],
  );

  if (models.error || projects.error) return <Notice tone="error">{(models.error ?? projects.error)?.message}</Notice>;
  if (!models.data || !projects.data) return <p className="text-sm text-muted">Loading…</p>;
  if (engines.length === 0) {
    return (
      <>
        <PageHeader title="Image Studio" />
        <EmptyState icon={<ImageIcon />} title="Install an image model to start"
          action={<Button onClick={() => navigate("/models")}>Go to Models</Button>}>
          Pictures need an image model on this Mac. Install Qwen-Image 2.1 Turbo from Models.
        </EmptyState>
      </>
    );
  }
  return <Studio engines={engines} projects={projects.data} reloadProjects={projects.reload} />;
}
