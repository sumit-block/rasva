import { Music, Trash2, Upload, Zap } from "lucide-react";
import { useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { Card, CardDescription, CardTitle } from "@/components/ui/card";
import { Notice } from "@/components/ui/notice";
import { useAsync } from "@/hooks/useAsync";
import { usePrefs } from "@/hooks/usePrefs";
import { chapters, projectAssets, uploadAsset, type Chapter, type MixTrack } from "@/lib/api";

const PRESETS = [
  { id: "quiet", label: "Quiet", hint: "Barely there" },
  { id: "balanced", label: "Balanced", hint: "Soft background" },
  { id: "present", label: "Present", hint: "Clearly audible" },
] as const;

const field = "min-h-11 rounded-lg border border-border bg-bg px-3 text-sm";

function TrackRow({ track, chapter, onChange, onError }: {
  track: MixTrack; chapter: Chapter; onChange: (c: Chapter) => void; onError: (m: string) => void;
}) {
  const { prefs } = usePrefs();
  const update = (body: Partial<MixTrack>) => chapters.updateTrack(track.id, body).then(onChange, (e: Error) => onError(e.message));
  return (
    <li className="py-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="flex min-w-0 items-center gap-2 text-sm">
          {track.kind === "music" ? <Music className="size-4 shrink-0 text-accent" aria-hidden /> : <Zap className="size-4 shrink-0 text-highlight" aria-hidden />}
          <span className="truncate">{track.original_name}</span>
          <span className="shrink-0 text-xs text-muted">
            {track.kind === "music"
              ? `${track.loop ? "looped" : "once"}${track.duck ? ", lowered under speech" : ""}`
              : `at sentence ${(track.anchor_segment ?? 0) + 1}`}
          </span>
        </span>
        <Button size="sm" variant="ghost" aria-label={`Remove ${track.original_name}`}
          onClick={() => chapters.removeTrack(track.id).then(onChange, (e: Error) => onError(e.message))}>
          <Trash2 aria-hidden />
        </Button>
      </div>
      {track.kind === "effect" && (
        <label className="mt-2 flex items-center gap-2 text-xs text-muted">
          Play at sentence
          <input type="number" min={1} max={chapter.segments.length} defaultValue={(track.anchor_segment ?? 0) + 1}
            onBlur={(e) => update({ anchor_segment: Math.max(0, Number(e.target.value) - 1) })}
            className="min-h-9 w-20 rounded-md border border-border bg-bg px-2 text-sm text-fg" />
        </label>
      )}
      {prefs.mode === "advanced" && (
        <div className="mt-2 grid grid-cols-2 gap-2 text-xs text-muted sm:grid-cols-4">
          {([["gain_db", "Volume (dB)", -60, 6, 1], ["fade_in_s", "Fade in (s)", 0, 60, 0.5], ["fade_out_s", "Fade out (s)", 0, 60, 0.5], ["offset_s", "Delay (s)", 0, 3600, 0.5]] as const).map(
            ([key, label, min, max, step]) => (
              <label key={key} className="flex flex-col gap-1">
                {label}
                <input type="number" min={min} max={max} step={step} defaultValue={track[key]}
                  onBlur={(e) => update({ [key]: Number(e.target.value) })}
                  className="min-h-9 rounded-md border border-border bg-bg px-2 text-sm text-fg" />
              </label>
            ),
          )}
          <label className="flex items-center gap-2"><input type="checkbox" defaultChecked={track.loop} onChange={(e) => update({ loop: e.target.checked })} /> Loop</label>
          <label className="flex items-center gap-2"><input type="checkbox" defaultChecked={track.duck} onChange={(e) => update({ duck: e.target.checked })} /> Lower under speech</label>
        </div>
      )}
    </li>
  );
}

export function MixPanel({ chapter, onChange }: { chapter: Chapter; onChange: (c: Chapter) => void }) {
  const { prefs } = usePrefs();
  const imports = useAsync(() => projectAssets(chapter.project_id, "import"), [chapter.project_id]);
  const [kind, setKind] = useState<"music" | "effect">("music");
  const [assetId, setAssetId] = useState("");
  const [preset, setPreset] = useState<string>("balanced");
  const [anchor, setAnchor] = useState(1);
  const [error, setError] = useState<string>();
  const [uploading, setUploading] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);
  const audioImports = (imports.data ?? []).filter((a) => a.media_type.startsWith("audio/"));

  const upload = async (file: File) => {
    setUploading(true);
    setError(undefined);
    try {
      const asset = await uploadAsset(chapter.project_id, file, "import");
      await imports.reload();
      setAssetId(asset.id);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setUploading(false);
    }
  };

  const add = async () => {
    setError(undefined);
    try {
      onChange(await chapters.addTrack(chapter.id, kind === "music"
        ? { kind, asset_id: assetId, preset }
        : { kind, asset_id: assetId, anchor_segment: Math.max(0, anchor - 1) }));
      setAssetId("");
    } catch (e) {
      setError((e as Error).message);
    }
  };

  return (
    <Card>
      <CardTitle>Music &amp; effects</CardTitle>
      <CardDescription>
        Add your own licensed music or sound effects. Music is lowered automatically while someone is speaking.
      </CardDescription>

      {chapter.tracks.length > 0 && (
        <ul className="mt-3 divide-y divide-border">
          {chapter.tracks.map((t) => <TrackRow key={t.id} track={t} chapter={chapter} onChange={onChange} onError={setError} />)}
        </ul>
      )}

      <div className="mt-4 space-y-3 rounded-lg border border-border p-3">
        <div role="tablist" aria-label="Add" className="inline-flex rounded-lg border border-border bg-surface-2 p-1">
          {(["music", "effect"] as const).map((k) => (
            <button key={k} role="tab" aria-selected={kind === k} onClick={() => setKind(k)}
              className={`min-h-9 rounded-md px-3 text-sm ${kind === k ? "bg-surface text-fg shadow-sm" : "text-muted"}`}>
              {k === "music" ? "Background music" : "Sound effect"}
            </button>
          ))}
        </div>
        <div className="flex flex-wrap gap-2">
          <label className="sr-only" htmlFor="mix-asset">Audio file</label>
          <select id="mix-asset" value={assetId} onChange={(e) => setAssetId(e.target.value)} className={`${field} min-w-0 flex-1`}>
            <option value="">{audioImports.length ? "Choose a file" : "Upload a file first"}</option>
            {audioImports.map((a) => <option key={a.id} value={a.id}>{a.original_name}</option>)}
          </select>
          <input ref={fileRef} type="file" accept=".wav,.mp3,.m4a,.ogg,.flac,audio/*" className="hidden"
            onChange={(e) => { const f = e.target.files?.[0]; if (f) void upload(f); e.target.value = ""; }} />
          <Button variant="secondary" disabled={uploading} onClick={() => fileRef.current?.click()}>
            <Upload aria-hidden /> {uploading ? "Uploading…" : "Upload"}
          </Button>
        </div>
        {kind === "music" ? (
          <div role="radiogroup" aria-label="Music level" className="grid grid-cols-3 gap-2">
            {PRESETS.map((p) => (
              <button key={p.id} role="radio" aria-checked={preset === p.id} onClick={() => setPreset(p.id)}
                className={`min-h-14 rounded-lg border px-2 text-left ${preset === p.id ? "border-accent bg-accent/10" : "border-border"}`}>
                <span className="block text-sm font-medium">{p.label}</span>
                <span className="block text-xs text-muted">{p.hint}</span>
              </button>
            ))}
          </div>
        ) : (
          <label className="flex items-center gap-2 text-sm">
            Play at sentence
            <input type="number" min={1} max={Math.max(1, chapter.segments.length)} value={anchor}
              onChange={(e) => setAnchor(Number(e.target.value))} className={`${field} w-24`} />
          </label>
        )}
        <Button disabled={!assetId} onClick={() => void add()}>Add {kind === "music" ? "music" : "effect"}</Button>
        <p className="text-xs text-muted">
          These presets set volume and fades. Rasva doesn't generate music; it mixes the files you provide.
        </p>
      </div>

      {prefs.mode === "advanced" && (
        <label className="mt-3 flex items-center gap-2 text-sm">
          Lower music under speech by
          <input type="number" min={-30} max={0} step={1} defaultValue={chapter.mix.duck_db ?? -12}
            onBlur={(e) => chapters.update(chapter.id, { mix: { duck_db: Number(e.target.value) } }).then(onChange, (err: Error) => setError(err.message))}
            className={`${field} w-20`} /> dB
        </label>
      )}
      {error && <Notice tone="error" className="mt-3">{error}</Notice>}
    </Card>
  );
}
