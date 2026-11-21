/** Phase I3 "Change outfit": a person photo plus a garment photo or a description,
 * and the result shown next to the original. */
import { ImagePlus, Shirt, Upload } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardDescription, CardTitle } from "@/components/ui/card";
import { Notice } from "@/components/ui/notice";
import { Progress } from "@/components/ui/progress";
import { useAsync } from "@/hooks/useAsync";
import { useJobs } from "@/hooks/useJobs";
import { api, assetUrl, isUsable, projectAssets, TERMINAL_STATES, type Asset, type Job } from "@/lib/api";
import { STATE_LABEL } from "@/lib/format";
import { usePasteTarget } from "@/lib/paste";
import { cn } from "@/lib/utils";
import { PasteButton } from "./PasteButton";
import {
  changeOutfit,
  GARMENT_TYPES,
  MAX_DESCRIPTION,
  outfitWorkflow,
  uploadPhoto,
  type GarmentType,
  type ReferenceRole,
} from "./outfitApi";

const EDIT_TASK = "image.edit";
const IMAGE_TYPES = ["image/png", "image/jpeg", "image/webp"];

function PhotoPicker({
  label,
  role,
  photos,
  value,
  exclude,
  onChange,
  onUpload,
  onError,
  busy,
}: {
  label: string;
  role: ReferenceRole;
  photos: Asset[];
  value: string | null;
  exclude?: string | null;
  onChange: (id: string) => void;
  onUpload: (file: File, role: ReferenceRole) => void;
  onError: (message: string) => void;
  busy: boolean;
}) {
  const input = useRef<HTMLInputElement>(null);
  const paste = usePasteTarget<HTMLFieldSetElement>((f) => onUpload(f, role), busy);
  const shown = photos.filter((p) => p.id !== exclude);
  return (
    <fieldset {...paste} className="mt-3">
      <legend className="text-sm font-medium">{label}</legend>
      <div role="radiogroup" aria-label={label} className="mt-2 flex flex-wrap gap-2">
        {shown.map((p) => (
          <button key={p.id} role="radio" aria-checked={value === p.id} onClick={() => onChange(p.id)}
            className={cn("rounded-lg border-2 p-0.5", value === p.id ? "border-accent" : "border-transparent")}>
            <img src={assetUrl(p.id)} alt={p.original_name} loading="lazy" className="size-20 rounded-md object-cover" />
          </button>
        ))}
        <Button variant="secondary" className="size-20 flex-col gap-1 text-xs" disabled={busy}
          onClick={() => input.current?.click()}>
          <Upload aria-hidden /> Add photo
        </Button>
        <PasteButton className="size-20 flex-col gap-1 text-xs" onFile={(f) => onUpload(f, role)} onError={onError} disabled={busy} />
        <input ref={input} type="file" accept={IMAGE_TYPES.join(",")} className="sr-only" tabIndex={-1}
          aria-label={`Add a ${role} photo`}
          onChange={(e) => {
            const file = e.target.files?.[0];
            if (file) onUpload(file, role);
            e.target.value = "";
          }} />
      </div>
    </fieldset>
  );
}

function Comparison({ job }: { job: Job }) {
  const w = outfitWorkflow(job);
  if (!w) return null;
  const terminal = TERMINAL_STATES.has(job.state);
  const source = w.garment_source === "text" ? "From a description" : w.garment_source === "photo" ? "From a garment photo" : "From a photo and a description";
  return (
    <li className="rounded-[var(--radius-card)] border border-border bg-surface p-4">
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <span className="font-medium">Outfit change</span>
        <Badge tone={job.state === "completed" ? "success" : job.state === "failed" ? "danger" : terminal ? "neutral" : "accent"}>
          {STATE_LABEL[job.state]}
        </Badge>
        <span className="text-xs text-muted">{source} · seed {String(job.inputs.seed ?? "")}</span>
      </div>
      <div className="mt-3 grid grid-cols-2 gap-3">
        <figure>
          <img src={assetUrl(w.person_asset_id)} alt="Original photo" loading="lazy"
            className="aspect-square w-full rounded-lg border border-border object-cover" />
          <figcaption className="mt-1 text-xs text-muted">Original</figcaption>
        </figure>
        <figure>
          {job.state === "completed" && job.outputs[0] ? (
            <a href={assetUrl(job.outputs[0])} target="_blank" rel="noreferrer">
              <img src={assetUrl(job.outputs[0])} alt="Photo with the new outfit" loading="lazy"
                className="aspect-square w-full rounded-lg border border-border object-cover" />
            </a>
          ) : (
            <div className="flex aspect-square w-full items-center justify-center rounded-lg border border-dashed border-border p-3 text-center text-xs text-muted">
              {terminal ? job.error_message ?? "No result." : (
                <div className="w-full">
                  <Progress value={job.state === "processing" ? job.progress : null} label="Outfit change progress" />
                  <p className="mt-2">{job.stage_detail ?? STATE_LABEL[job.state]}</p>
                </div>
              )}
            </div>
          )}
          <figcaption className="mt-1 text-xs text-muted">New outfit</figcaption>
        </figure>
      </div>
    </li>
  );
}

export function OutfitPanel({ projectId, ensureProject }: { projectId: string | null; ensureProject: () => Promise<string> }) {
  const models = useAsync(api.models);
  const { jobs, upsert } = useJobs();
  const engines = useMemo(() => (models.data ?? []).filter((m) => m.tasks.includes(EDIT_TASK)), [models.data]);
  // Prefer a model the outfit workflow was tested on (FLUX.2 Klein), as the server does.
  const engine = engines.find((m) => m.features.includes("outfit") && isUsable(m)) ?? engines.find(isUsable);

  const [photos, setPhotos] = useState<Asset[]>([]);
  const [person, setPerson] = useState<string | null>(null);
  const [garment, setGarment] = useState<string | null>(null);
  const [mode, setMode] = useState<"photo" | "text">("photo");
  const [description, setDescription] = useState("");
  const [garmentType, setGarmentType] = useState<GarmentType>("auto");
  const [keepBackground, setKeepBackground] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();
  const [history, setHistory] = useState<Job[]>([]);

  // Switching projects clears the choices; the first project, created by an
  // upload, keeps the photo just added.
  const previous = useRef(projectId);
  useEffect(() => {
    if (previous.current !== null && previous.current !== projectId) {
      setPerson(null);
      setGarment(null);
    }
    previous.current = projectId;
    if (!projectId) return setPhotos([]), setHistory([]);
    let alive = true;
    projectAssets(projectId, "reference").then(
      (all) => alive && setPhotos(all.filter((a) => IMAGE_TYPES.includes(a.media_type))),
      () => undefined,
    );
    api.projectJobs(projectId).then((js) => alive && setHistory(js), () => undefined);
    return () => {
      alive = false;
    };
  }, [projectId]);

  const results = useMemo(() => {
    const byId = new Map(history.map((j) => [j.id, j]));
    for (const j of jobs) if (j.project_id === projectId) byId.set(j.id, j);
    return [...byId.values()].filter((j) => outfitWorkflow(j)).sort((a, b) => b.created_at.localeCompare(a.created_at));
  }, [history, jobs, projectId]);

  const upload = async (file: File, role: ReferenceRole) => {
    setBusy(true);
    setError(undefined);
    try {
      const pid = projectId ?? (await ensureProject());
      const asset = await uploadPhoto(pid, file, role);
      setPhotos((prev) => [asset, ...prev]);
      (role === "person" ? setPerson : setGarment)(asset.id);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const text = description.trim();
  const ready = person && (mode === "photo" ? garment : text.length >= 3) && description.length <= MAX_DESCRIPTION;

  const preset = engine?.presets[1] ?? engine?.presets[0];

  const generate = async () => {
    if (!projectId || !person) return;
    setBusy(true);
    setError(undefined);
    try {
      upsert(await changeOutfit(projectId, {
        person_asset_id: person,
        ...(mode === "photo" ? { garment_asset_id: garment ?? undefined } : {}),
        ...(text ? { garment_description: text } : {}),
        garment_type: garmentType,
        keep_background: keepBackground,
        ...(preset ? { preset: preset.id } : {}),
      }));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <section aria-labelledby="outfit-title" className="mt-6">
      <Card>
        <CardTitle id="outfit-title" className="flex items-center gap-2"><Shirt className="size-4" aria-hidden /> Change outfit</CardTitle>
        <CardDescription>
          Put a garment from one photo onto the person in another, or describe the garment in words. The face, body and
          pose are meant to stay the same; check each result.
        </CardDescription>

        {models.error && <Notice tone="error" className="mt-3">{models.error.message}</Notice>}
        {models.data && !engine && (
          <Notice className="mt-3">
            Changing outfits needs an image editing engine, and none is installed yet.
            {engines.length > 0 && " Install one from Models."}
          </Notice>
        )}

        <PhotoPicker label="Person" role="person" photos={photos} value={person} exclude={garment}
          onChange={setPerson} onUpload={(f, r) => void upload(f, r)} onError={setError} busy={busy} />

        <fieldset className="mt-4">
          <legend className="text-sm font-medium">Garment</legend>
          <div role="radiogroup" aria-label="Garment source" className="mt-2 inline-flex rounded-lg border border-border p-0.5">
            {(["photo", "text"] as const).map((m) => (
              <button key={m} role="radio" aria-checked={mode === m} onClick={() => setMode(m)}
                className={cn("min-h-9 rounded-md px-3 text-sm", mode === m ? "bg-accent/15 font-medium" : "text-muted")}>
                {m === "photo" ? "From a photo" : "Describe it"}
              </button>
            ))}
          </div>
          {mode === "photo" && (
            <PhotoPicker label="Garment photo" role="garment" photos={photos} value={garment} exclude={person}
              onChange={setGarment} onUpload={(f, r) => void upload(f, r)} onError={setError} busy={busy} />
          )}
          <label htmlFor="garment-text" className="mt-3 block text-sm">
            {mode === "photo" ? "Extra details (optional)" : "Describe the garment"}
          </label>
          <textarea id="garment-text" rows={2} value={description} onChange={(e) => setDescription(e.target.value)}
            placeholder={mode === "photo" ? "Full sleeves, in deep green" : "A maroon silk saree with a gold zari border"}
            className="mt-1 w-full resize-y rounded-lg border border-border bg-bg p-3 text-sm text-fg placeholder:text-muted" />
          <div className="flex justify-end text-xs text-muted">
            <span className={cn(description.length > MAX_DESCRIPTION && "text-danger")}>{description.length} / {MAX_DESCRIPTION}</span>
          </div>
        </fieldset>

        <label htmlFor="garment-type" className="mt-3 block text-sm font-medium">What to change</label>
        <select id="garment-type" value={garmentType} onChange={(e) => setGarmentType(e.target.value as GarmentType)}
          className="mt-1 min-h-11 w-full max-w-sm rounded-lg border border-border bg-surface px-3 text-sm">
          {GARMENT_TYPES.map((t) => <option key={t.id} value={t.id}>{t.label}</option>)}
        </select>
        <p className="mt-1 text-xs text-muted">{GARMENT_TYPES.find((t) => t.id === garmentType)?.hint}</p>

        <label className="mt-3 flex min-h-11 items-center gap-2 text-sm">
          <input type="checkbox" checked={keepBackground} onChange={(e) => setKeepBackground(e.target.checked)} className="size-4 accent-[var(--accent)]" />
          Keep the background as it is
        </label>

        {error && <Notice tone="error" className="mt-3">{error}</Notice>}
        <div className="mt-4">
          <Button disabled={!engine || !ready || busy || !projectId} onClick={() => void generate()}>
            <ImagePlus aria-hidden /> Change outfit
          </Button>
          <p className="mt-2 text-xs text-muted">
            Use photos only of people who agreed to it. Results are labelled as AI-generated in their details.
          </p>
        </div>
      </Card>

      {results.length > 0 && <ul aria-label="Outfit results" className="mt-3 grid gap-3 md:grid-cols-2">{results.map((j) => <Comparison key={j.id} job={j} />)}</ul>}
    </section>
  );
}
