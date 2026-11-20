import { Accessibility, ImagePlus, PersonStanding, Sparkles, Upload } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { JobRow } from "@/components/JobRow";
import { Button } from "@/components/ui/button";
import { Card, CardDescription, CardTitle } from "@/components/ui/card";
import { Notice } from "@/components/ui/notice";
import { useJobs } from "@/hooks/useJobs";
import { usePrefs } from "@/hooks/usePrefs";
import { api, assetUrl, isUsable, TERMINAL_STATES, type EngineInfo, type Job } from "@/lib/api";
import { cn } from "@/lib/utils";
import { CompareView } from "./CompareView";
import {
  builtInPoseAsset, builtInPoses, changePose, EDIT_TASK, extractPose, FRAMING_LABEL, IDENTITY, imageAssets,
  isPoseChange, isPoseFigure, POSE_TASK, referencesOf, roleOf, uploadReference,
  type BuiltInPose, type IdentityId, type ImageAsset,
} from "./poseApi";

const MAX_EXTRA = 500;
const MAX_SEED = 2 ** 32 - 1;

type PoseChoice =
  | { kind: "builtin"; pose: BuiltInPose }
  | { kind: "figure"; asset: ImageAsset }; // extracted by the pose finder

function Thumb({ src, alt, selected, onClick, children }: { src: string; alt: string; selected: boolean; onClick: () => void; children?: ReactNode }) {
  return (
    <button onClick={onClick} aria-pressed={selected}
      className={cn("overflow-hidden rounded-lg border-2 bg-bg text-left", selected ? "border-accent" : "border-border")}>
      <img src={src} alt={alt} loading="lazy" className="aspect-[2/3] w-full object-contain" />
      {children && <span className="block px-2 py-1 text-xs text-muted">{children}</span>}
    </button>
  );
}

function FilePick({ label, onFile, disabled }: { label: string; onFile: (f: File) => void; disabled?: boolean }) {
  const ref = useRef<HTMLInputElement>(null);
  return (
    <>
      <input ref={ref} type="file" accept="image/png,image/jpeg,image/webp" className="sr-only" tabIndex={-1}
        onChange={(e) => { const f = e.target.files?.[0]; if (f) onFile(f); e.target.value = ""; }} />
      <Button variant="secondary" disabled={disabled} onClick={() => ref.current?.click()}>
        <Upload aria-hidden /> {label}
      </Button>
    </>
  );
}

/** Phase I2: keep the same person, change the pose. */
export function PoseStudio({ projectId, ensureProject, models }: {
  projectId: string | null;
  ensureProject: () => Promise<string>;
  models: EngineInfo[];
}) {
  const { prefs } = usePrefs();
  const advanced = prefs.mode === "advanced";
  const { jobs, upsert } = useJobs();
  const poseEngine = models.find((m) => m.tasks.includes(POSE_TASK) && isUsable(m));
  const editEngine = models.find((m) => m.tasks.includes(EDIT_TASK) && m.features.includes("pose") && isUsable(m));

  const [assets, setAssets] = useState<ImageAsset[]>([]);
  const [builtins, setBuiltins] = useState<BuiltInPose[]>([]);
  const [personId, setPersonId] = useState<string>();
  const [pose, setPose] = useState<PoseChoice>();
  const [poseSource, setPoseSource] = useState<"figure" | "photo">("figure");
  const [identity, setIdentity] = useState<IdentityId>("balanced");
  const [extra, setExtra] = useState("");
  const [seed, setSeed] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();
  const [extractJobId, setExtractJobId] = useState<string>();

  const reload = useCallback(async () => {
    if (!projectId) return setAssets([]);
    try {
      setAssets(await imageAssets(projectId));
    } catch (e) {
      setError((e as Error).message);
    }
  }, [projectId]);
  useEffect(() => void reload(), [reload]);
  useEffect(() => {
    builtInPoses().then(setBuiltins, (e: Error) => setError(e.message));
  }, []);

  const people = assets.filter((a) => a.kind === "reference" && roleOf(a) === "person");
  const figures = assets.filter(isPoseFigure);
  const byId = useMemo(() => new Map(assets.map((a) => [a.id, a])), [assets]);

  // Pose-finder jobs for this project. The latest one is shown while it runs, or
  // after it fails so its message and Retry stay visible. When any of them
  // completes (a Retry included) the figures are reloaded; the one we started
  // is selected.
  const poseJobs = jobs.filter((j) => j.project_id === projectId && j.task === POSE_TASK);
  const extractJob = poseJobs.find((j) => j.id === extractJobId) ?? poseJobs.find((j) => j.retry_of === extractJobId);
  const extracting = !!extractJob && !TERMINAL_STATES.has(extractJob.state);
  const doneKey = poseJobs.filter((j) => j.state === "completed").map((j) => j.id).join(",");
  useEffect(() => {
    if (!doneKey || !projectId) return;
    let alive = true;
    void imageAssets(projectId).then((fresh) => {
      if (!alive) return;
      setAssets(fresh);
      const mine = poseJobs.find((j) => j.state === "completed" && (j.id === extractJobId || j.retry_of === extractJobId));
      const asset = mine && fresh.find((a) => a.id === mine.outputs[0]);
      if (asset) {
        setPose({ kind: "figure", asset });
        setExtractJobId(undefined);
      }
    }, () => undefined);
    return () => {
      alive = false;
    };
  }, [doneKey, projectId]);

  const run = async (fn: () => Promise<void>) => {
    setBusy(true);
    setError(undefined);
    try {
      await fn();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const addPerson = (file: File) => run(async () => {
    const pid = await ensureProject();
    const a = await uploadReference(pid, file, file.name, "person");
    setAssets((prev) => [a, ...prev]);
    setPersonId(a.id);
  });

  const addPosePhoto = (file: File) => run(async () => {
    if (!poseEngine) throw new Error("Install the pose finder from Models to read a pose from a photo.");
    const pid = await ensureProject();
    const photo = await uploadReference(pid, file, file.name, "pose");
    setAssets((prev) => [photo, ...prev]);
    const job = await extractPose(pid, poseEngine.id, photo.id);
    upsert(job);
    setExtractJobId(job.id);
  });

  const seedValue = seed.trim() === "" ? undefined : Number(seed);
  const badSeed = seedValue !== undefined && !(Number.isInteger(seedValue) && seedValue >= 0 && seedValue <= MAX_SEED);
  const ready = !!editEngine && !!personId && !!pose && !badSeed && extra.length <= MAX_EXTRA && !busy;

  const generate = () => run(async () => {
    if (!editEngine || !personId || !pose) return;
    const pid = await ensureProject();
    let poseId: string;
    let source: "figure" | "photo" = "figure";
    if (pose.kind === "builtin") {
      const a = await builtInPoseAsset(pid, pose.pose, assets);
      if (!byId.has(a.id)) setAssets((prev) => [a, ...prev]);
      poseId = a.id;
    } else if (poseSource === "photo" && Array.isArray(pose.asset.metadata.source_asset_ids)) {
      poseId = String(pose.asset.metadata.source_asset_ids[0]); // the original pose photo
      source = "photo";
    } else {
      poseId = pose.asset.id;
    }
    upsert(await changePose(pid, editEngine.id, { personId, poseId, poseSource: source, identity, prompt: extra, seed: advanced ? seedValue : undefined }));
  });

  // This project's pose changes, newest first (live updates win).
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
    const all = new Map(history.map((j) => [j.id, j]));
    for (const j of jobs) if (j.project_id === projectId) all.set(j.id, j);
    return [...all.values()].filter(isPoseChange).sort((a, b) => b.created_at.localeCompare(a.created_at));
  }, [history, jobs, projectId]);

  const figureMeta = pose?.kind === "figure" ? pose.asset.metadata : undefined;
  const preview = pose?.kind === "builtin" ? `/poses/${pose.pose.file}` : pose?.kind === "figure" ? assetUrl(pose.asset.id) : undefined;

  return (
    <section aria-labelledby="pose-title" className="mt-8">
      <h2 id="pose-title" className="flex items-center gap-2 text-lg font-semibold tracking-tight">
        <PersonStanding className="size-5 text-accent" aria-hidden /> Same person, new pose
      </h2>
      <p className="mt-1 text-sm text-muted">
        Choose a photo of a person and a pose. The new picture keeps their face, skin tone, hair and clothes, in the new pose.
        Use photos you have permission to use.
      </p>

      {!editEngine && (
        <Notice className="mt-3">
          Pose changes need the photo editing engine (FLUX.2 Klein). Install it from Models when it is available.
          You can already choose photos and poses here.
        </Notice>
      )}

      <div className="mt-4 grid gap-4 lg:grid-cols-3">
        <Card>
          <CardTitle>1. The person</CardTitle>
          <CardDescription>A clear photo where the face is visible. Full-body photos keep the clothes best.</CardDescription>
          <div className="mt-3"><FilePick label="Upload a photo" onFile={(f) => void addPerson(f)} disabled={busy} /></div>
          {people.length > 0 && (
            <div className="mt-3 grid grid-cols-3 gap-2">
              {people.map((a) => (
                <Thumb key={a.id} src={assetUrl(a.id)} alt={a.original_name} selected={personId === a.id} onClick={() => setPersonId(a.id)} />
              ))}
            </div>
          )}
        </Card>

        <Card>
          <CardTitle>2. The pose</CardTitle>
          <CardDescription>Pick a figure, or upload a photo of someone in the pose you want.</CardDescription>
          <div className="mt-3 flex flex-wrap gap-2">
            <FilePick label="Pose from a photo" onFile={(f) => void addPosePhoto(f)} disabled={busy || extracting} />
          </div>
          {!poseEngine && <p className="mt-2 text-xs text-muted">Reading a pose from a photo needs the pose finder (install it from Models). The figures below work without it.</p>}
          {extractJob && extractJob.state !== "completed" && extractJob.state !== "canceled" && (
            <ul className="mt-3"><JobRow job={extractJob} /></ul>
          )}
          <p className="mt-4 text-xs font-medium text-muted">Built-in figures</p>
          <div className="mt-2 grid grid-cols-4 gap-2">
            {builtins.map((b) => (
              <Thumb key={b.id} src={`/poses/${b.file}`} alt={b.label}
                selected={pose?.kind === "builtin" && pose.pose.id === b.id} onClick={() => setPose({ kind: "builtin", pose: b })} />
            ))}
          </div>
          {figures.length > 0 && (
            <>
              <p className="mt-4 text-xs font-medium text-muted">Read from your photos</p>
              <div className="mt-2 grid grid-cols-4 gap-2">
                {figures.map((a) => (
                  <Thumb key={a.id} src={assetUrl(a.id)} alt="Pose read from a photo"
                    selected={pose?.kind === "figure" && pose.asset.id === a.id} onClick={() => setPose({ kind: "figure", asset: a })} />
                ))}
              </div>
            </>
          )}
        </Card>

        <Card>
          <CardTitle>3. Check and generate</CardTitle>
          {preview ? (
            <figure className="mt-3">
              <img src={preview} alt="Chosen pose" className="mx-auto max-h-64 rounded-lg border border-border bg-black object-contain" />
              <figcaption className="mt-1 text-xs text-muted">
                {pose?.kind === "builtin" ? pose.pose.label : "Pose read from your photo"}
                {figureMeta?.framing ? ` · ${FRAMING_LABEL[String(figureMeta.framing)] ?? String(figureMeta.framing)}` : ""}
              </figcaption>
              {typeof figureMeta?.people_found === "number" && figureMeta.people_found > 1 && (
                <p className="mt-1 text-xs text-muted">{String(figureMeta.people_found)} people were in the photo; the largest one was used.</p>
              )}
            </figure>
          ) : (
            <p className="mt-3 flex items-center gap-2 text-sm text-muted"><Accessibility className="size-4" aria-hidden /> Choose a pose to see it here.</p>
          )}

          <div role="radiogroup" aria-label="Likeness or pose" className="mt-4 grid gap-2">
            {IDENTITY.map((o) => (
              <button key={o.id} role="radio" aria-checked={identity === o.id} onClick={() => setIdentity(o.id)}
                className={cn("min-h-11 rounded-lg border px-3 py-2 text-left", identity === o.id ? "border-accent bg-accent/10" : "border-border")}>
                <span className="block text-sm font-medium">{o.label}</span>
                <span className="block text-xs text-muted">{o.description}</span>
              </button>
            ))}
          </div>

          <label htmlFor="pose-extra" className="mt-4 block text-sm font-medium">Anything else? <span className="font-normal text-muted">(optional)</span></label>
          <input id="pose-extra" value={extra} onChange={(e) => setExtra(e.target.value)} placeholder="Outdoors in soft evening light"
            className="mt-1 min-h-11 w-full rounded-lg border border-border bg-bg px-3 text-sm" />
          {extra.length > MAX_EXTRA && <p className="mt-1 text-xs text-danger">Keep this under {MAX_EXTRA} characters.</p>}

          {advanced && (
            <>
              <label htmlFor="pose-seed" className="mt-4 block text-sm font-medium">Seed</label>
              <input id="pose-seed" inputMode="numeric" value={seed} onChange={(e) => setSeed(e.target.value)} placeholder="Random"
                className="mt-1 min-h-11 w-48 rounded-lg border border-border bg-bg px-3 text-sm tabular-nums" />
              {badSeed && <p className="mt-1 text-xs text-danger">The seed must be a whole number from 0 to {MAX_SEED.toLocaleString()}.</p>}
              {pose?.kind === "figure" && (
                <label className="mt-4 flex items-start gap-2 text-sm">
                  <input type="checkbox" className="mt-1" checked={poseSource === "photo"} onChange={(e) => setPoseSource(e.target.checked ? "photo" : "figure")} />
                  <span>Send the pose photo instead of the stick figure<span className="block text-xs text-muted">Try this if the figure's pose isn't followed. Only the pose is meant to carry over.</span></span>
                </label>
              )}
            </>
          )}

          {error && <Notice tone="error" className="mt-3">{error}</Notice>}
          <div className="mt-4">
            <Button disabled={!ready} onClick={() => void generate()}>
              <Sparkles aria-hidden /> Change the pose
            </Button>
          </div>
        </Card>
      </div>

      <section aria-label="Pose results" className="mt-4">
        {results.length === 0 ? (
          <p className="flex items-center gap-2 text-sm text-muted"><ImagePlus className="size-4" aria-hidden /> New poses appear here, next to the original photo.</p>
        ) : (
          <ul className="grid gap-3 md:grid-cols-2">
            {results.map((j) => {
              const person = referencesOf(j).find((r) => r.role === "person")?.asset_id;
              return (
                <li key={j.id} className="list-none">
                  <ul><JobRow job={j} /></ul>
                  {j.state === "completed" && person && j.outputs[0] && <CompareView beforeId={person} afterId={j.outputs[0]} />}
                </li>
              );
            })}
          </ul>
        )}
      </section>
    </section>
  );
}
