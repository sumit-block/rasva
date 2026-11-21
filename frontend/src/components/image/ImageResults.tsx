import { Columns2, Download, RotateCcw, Shuffle, Star } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { JobRow } from "@/components/JobRow";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Notice } from "@/components/ui/notice";
import { useJobs } from "@/hooks/useJobs";
import { assetUrl, TERMINAL_STATES, type Job } from "@/lib/api";
import { timeAgo } from "@/lib/format";
import { cn } from "@/lib/utils";
import { imageApi, type ImageGroup } from "./api";
import { CompareView, type CompareSide } from "./CompareView";
import { ExportPanel } from "./ExportPanel";

const usable = (j: Job) => j.state === "completed" && j.outputs.length > 0;
const output = (j: Job) => j.outputs[0] ?? "";

/** Pose (I2) and outfit (I3) jobs keep Rasva's own instruction in `prompt`, not the user's words. */
const builtInstruction = (j: Job) => typeof j.inputs.pose_source === "string" || (j.inputs.workflow as { name?: string } | undefined)?.name === "outfit";

function titleOf(j: Job): string {
  if (j.task === "image.pose") return "Pose figure";
  if (typeof j.inputs.pose_source === "string") {
    const extra = String(j.inputs.user_prompt ?? "").trim();
    return extra ? `New pose: ${extra}` : "New pose";
  }
  const wf = j.inputs.workflow as { name?: string; garment_source?: string } | undefined;
  if (wf?.name === "outfit") return wf.garment_source === "photo" ? "Change outfit (from a garment photo)" : "Change outfit (described)";
  return String(j.inputs.prompt ?? "Image");
}

/**
 * Image results grouped as versions (PRD phase I4). Running jobs show as job
 * rows with progress; finished ones join their image's version strip.
 */
export function ImageResults({ projectId, jobs: given }: { projectId: string; jobs: Job[] }) {
  // The page may pass only one task's jobs; follow every image task (generate, edit, …).
  const { jobs: liveAll } = useJobs();
  const jobs = useMemo(() => {
    const byId = new Map(given.map((j) => [j.id, j]));
    for (const j of liveAll) if (j.project_id === projectId && j.task.startsWith("image.")) byId.set(j.id, j);
    return [...byId.values()].sort((a, b) => b.created_at.localeCompare(a.created_at));
  }, [given, liveAll, projectId]);
  const [groups, setGroups] = useState<ImageGroup[]>([]);
  const [error, setError] = useState<string>();
  // Refetch whenever a job of this project appears or changes state.
  const signature = jobs.map((j) => `${j.id}:${j.state}`).join(",");
  useEffect(() => {
    let alive = true;
    const t = setTimeout(() => {
      imageApi.groups(projectId).then(
        (gs) => alive && (setGroups(gs), setError(undefined)),
        (e: Error) => alive && setError(e.message),
      );
    }, 250);
    return () => {
      alive = false;
      clearTimeout(t);
    };
  }, [projectId, signature]);

  const live = useMemo(() => new Map(jobs.map((j) => [j.id, j])), [jobs]);
  const grouped = useMemo(() => new Set(groups.flatMap((g) => g.versions.map((v) => v.id))), [groups]);
  const running = jobs.filter((j) => !TERMINAL_STATES.has(j.state) || !grouped.has(j.id));

  if (jobs.length === 0 && groups.length === 0) {
    return <p className="text-sm text-muted">Your images appear here. Generating one takes a while on the first run while the engine loads.</p>;
  }
  return (
    <>
      {error && <Notice tone="error" className="mb-3">{error}</Notice>}
      {running.length > 0 && <ul className="mb-3 grid gap-3 md:grid-cols-2">{running.map((j) => <JobRow key={j.id} job={j} />)}</ul>}
      <ul className="grid gap-3 xl:grid-cols-2">
        {groups.map((g) => (
          <ImageGroupCard key={g.id} group={{ ...g, versions: g.versions.map((v) => live.get(v.id) ?? v) }}
            onChange={(ng) => setGroups((gs) => gs.map((x) => (x.id === ng.id ? ng : x)))} />
        ))}
      </ul>
    </>
  );
}

function ImageGroupCard({ group, onChange }: { group: ImageGroup; onChange: (g: ImageGroup) => void }) {
  const { upsert } = useJobs();
  const versions = group.versions;
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const selected = versions.find((v) => v.id === selectedId) ?? versions.find((v) => v.id === group.current_job_id) ?? versions[versions.length - 1];
  const [panel, setPanel] = useState<"none" | "compare" | "export">("none");
  const [editing, setEditing] = useState(false);
  const [prompt, setPrompt] = useState(String(selected?.inputs.prompt ?? ""));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();
  if (!selected) return null;

  const run = async (fn: () => Promise<unknown>) => {
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
  const again = (seed: "new" | "same") =>
    run(async () => {
      const edited = editing && prompt.trim() && prompt !== selected.inputs.prompt ? { prompt } : {};
      upsert(await imageApi.again(selected.id, { seed, ...edited }));
      setEditing(false);
    });
  const favourite = group.favourite_job_id === selected.id;
  const toggleFavourite = () => run(async () => onChange(await imageApi.setFavourite(group.id, favourite ? null : selected.id)));
  const index = versions.indexOf(selected) + 1;
  const asset = usable(selected) ? output(selected) : undefined;

  return (
    <li className="rounded-[var(--radius-card)] border border-border bg-surface p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="min-w-0 truncate text-sm font-medium" title={titleOf(selected)}>
          {titleOf(selected)}
        </h3>
        <span className="text-xs text-muted">Version {index} of {versions.length}</span>
      </div>

      <div className="mt-3">
        {asset ? (
          <img src={assetUrl(asset)} alt={`Version ${index}, seed ${String(selected.inputs.seed ?? "")}`}
            className="aspect-square w-full max-w-md rounded-lg border border-border bg-surface-2 object-contain" />
        ) : (
          <div className="flex aspect-square w-full max-w-md items-center justify-center rounded-lg border border-dashed border-border p-4 text-center text-sm text-muted">
            {selected.state === "completed"
              ? "This version's picture was removed in cleanup."
              : !TERMINAL_STATES.has(selected.state)
                ? selected.state === "queued" ? "Waiting its turn…" : (selected.stage_detail ?? "Making this version…")
                : selected.error_message ?? "This version didn't finish."}
          </div>
        )}
      </div>

      <p className="mt-2 text-xs text-muted">
        {[
          selected.inputs.seed !== undefined && `Seed ${String(selected.inputs.seed)}`,
          selected.inputs.steps !== undefined && `${String(selected.inputs.steps)} steps`,
          timeAgo(selected.created_at),
        ].filter(Boolean).join(" · ")}
        {selected.inputs.low_memory === true && " · low-memory mode"}
        {favourite && " · your favourite"}
        {!favourite && group.current_job_id === selected.id && " · current (newest)"}
      </p>
      {typeof selected.metrics.improved_instruction === "string" && (
        <details className="mt-2 text-xs">
          <summary className="cursor-pointer text-muted">Show improved instruction</summary>
          <p className="mt-1 whitespace-pre-wrap rounded-md bg-surface-2 p-2">{selected.metrics.improved_instruction}</p>
        </details>
      )}

      {versions.length > 1 && (
        <div role="radiogroup" aria-label="Versions" className="mt-3 flex gap-2 overflow-x-auto pb-1">
          {versions.map((v, i) => (
            <button key={v.id} type="button" role="radio" aria-checked={v.id === selected.id}
              aria-label={`Version ${i + 1}${v.id === group.favourite_job_id ? ", favourite" : ""}${usable(v) ? "" : `, ${v.state}`}`}
              onClick={() => setSelectedId(v.id)}
              className={cn("relative size-14 shrink-0 overflow-hidden rounded-md border-2 bg-surface-2",
                v.id === selected.id ? "border-accent" : "border-transparent")}>
              {usable(v) ? <img src={assetUrl(output(v))} alt="" loading="lazy" className="size-full object-cover" />
                : <span className="text-[10px] text-muted">{v.state === "completed" ? "removed" : v.state}</span>}
              {v.id === group.favourite_job_id && <Star aria-hidden className="absolute right-0.5 top-0.5 size-3 fill-warning text-warning" />}
            </button>
          ))}
        </div>
      )}

      <div className="mt-3 flex flex-wrap gap-2">
        <Button size="sm" variant="secondary" disabled={busy || !asset} aria-pressed={favourite} onClick={() => void toggleFavourite()}>
          <Star aria-hidden className={cn(favourite && "fill-current")} /> {favourite ? "Favourite" : "Make favourite"}
        </Button>
        <Button size="sm" variant="secondary" disabled={busy} onClick={() => void again("new")}>
          <Shuffle aria-hidden /> Make a variation
        </Button>
        <Button size="sm" variant="secondary" disabled={busy} onClick={() => void again("same")}>
          <RotateCcw aria-hidden /> Try again, same seed
        </Button>
        {!builtInstruction(selected) && (
          <Button size="sm" variant="ghost" aria-expanded={editing} onClick={() => { setPrompt(String(selected.inputs.prompt ?? "")); setEditing((e) => !e); }}>
            Edit description
          </Button>
        )}
        <Button size="sm" variant="ghost" aria-expanded={panel === "compare"} disabled={!asset}
          onClick={() => setPanel((p) => (p === "compare" ? "none" : "compare"))}>
          <Columns2 aria-hidden /> Compare
        </Button>
        <Button size="sm" variant="ghost" aria-expanded={panel === "export"} disabled={!asset}
          onClick={() => setPanel((p) => (p === "export" ? "none" : "export"))}>
          <Download aria-hidden /> Download
        </Button>
      </div>

      {editing && (
        <div className="mt-3">
          <label htmlFor={`edit-${group.id}`} className="text-sm font-medium">New description for the next version</label>
          <textarea id={`edit-${group.id}`} value={prompt} onChange={(e) => setPrompt(e.target.value)} rows={3} maxLength={1000}
            className="mt-1 w-full resize-y rounded-lg border border-border bg-bg p-2 text-sm" />
          <p className="text-xs text-muted">Then choose "Make a variation" or "Try again, same seed". The same seed keeps the layout closer.</p>
        </div>
      )}
      {error && <Notice tone="error" className="mt-3">{error}</Notice>}
      {panel === "compare" && asset && <ComparePanel group={group} selected={selected} index={index} />}
      {panel === "export" && asset && <div className="mt-3"><ExportPanel assetId={asset} seed={selected.inputs.seed} /></div>}
      {selected.state !== "completed" && TERMINAL_STATES.has(selected.state) && selected.state !== "canceled" && (
        <Badge tone="warning" className="mt-3">Didn't finish: "Try again, same seed" reruns it exactly</Badge>
      )}
    </li>
  );
}

function ComparePanel({ group, selected, index }: { group: ImageGroup; selected: Job; index: number }) {
  // Edits compare with their source photo; any version can compare with another.
  const refs = (selected.inputs.references as { asset_id: string; role: string }[] | undefined) ?? [];
  const others = group.versions.filter((v) => v.id !== selected.id && usable(v));
  const options: { key: string; side: CompareSide }[] = [
    ...refs.map((r) => ({ key: `ref:${r.asset_id}`, side: { src: assetUrl(r.asset_id), label: `Your ${r.role} photo` } })),
    ...others.map((v) => ({ key: v.id, side: { src: assetUrl(output(v)), label: `Version ${group.versions.indexOf(v) + 1}` } })),
  ];
  const [key, setKey] = useState(options[0]?.key);
  const other = options.find((o) => o.key === key) ?? options[0];
  if (!other) return <Notice className="mt-3">Make another version to compare with.</Notice>;
  return (
    <div className="mt-3">
      {options.length > 1 && (
        <label className="mb-2 block text-sm">
          <span className="mr-2 font-medium">Compare with</span>
          <select value={other.key} onChange={(e) => setKey(e.target.value)} className="min-h-11 rounded-lg border border-border bg-surface px-3 text-sm">
            {options.map((o) => <option key={o.key} value={o.key}>{o.side.label}</option>)}
          </select>
        </label>
      )}
      <CompareView before={other.side} after={{ src: assetUrl(output(selected)), label: `Version ${index}` }} />
    </div>
  );
}
