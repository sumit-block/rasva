import { ImagePlus, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Notice } from "@/components/ui/notice";
import {
  assetUrl,
  PHOTO_ROLES,
  projectAssets,
  uploadAsset,
  type Asset,
  type PhotoRole,
  type ReferenceInput,
} from "@/lib/api";
import { usePasteTarget } from "@/lib/paste";
import { cn } from "@/lib/utils";
import { PasteButton } from "./PasteButton";

/** One box per role, in the order the engine reads them. */
const BOXES: { role: PhotoRole; title: string; hint: string; required?: boolean }[] = [
  { role: "person", title: "Original photo", hint: "The photo to change.", required: true },
  { role: "garment", title: "Clothing", hint: "A garment to put on." },
  { role: "pose", title: "Pose", hint: "A photo or figure in the pose you want." },
  { role: "background", title: "Background", hint: "A place to put them in." },
];

const roleOf = (a: Asset): PhotoRole => {
  const r = a.metadata?.role;
  return PHOTO_ROLES.includes(r as PhotoRole) ? (r as PhotoRole) : "person";
};

/**
 * Reference photos for an edit: a separate box for the original photo and for
 * each optional clothing, pose and background photo. Each box takes an upload
 * or a pasted picture, or one of that box's earlier photos. One photo per box,
 * so at most four in all (the engine's limit).
 */
export function ReferencePicker({ projectId, value, onChange, ensureProject }: {
  projectId: string | null;
  value: ReferenceInput[];
  onChange: (refs: ReferenceInput[]) => void;
  ensureProject: () => Promise<string>;
}) {
  const [photos, setPhotos] = useState<Asset[]>([]);
  const [busy, setBusy] = useState<PhotoRole | null>(null);
  const [error, setError] = useState<string>();

  useEffect(() => {
    if (!projectId) return setPhotos([]);
    let alive = true;
    projectAssets(projectId, "reference").then(
      (list) => alive && setPhotos(list.filter((a) => a.media_type.startsWith("image/"))),
      (e: Error) => alive && setError(e.message),
    );
    return () => {
      alive = false;
    };
  }, [projectId]);

  const choose = (role: PhotoRole, assetId: string | null) => {
    setError(undefined);
    const rest = value.filter((r) => r.role !== role && r.asset_id !== assetId);
    onChange(assetId ? [...rest, { asset_id: assetId, role }] : rest);
  };

  const upload = async (role: PhotoRole, file: File) => {
    setBusy(role);
    setError(undefined);
    try {
      const pid = projectId ?? (await ensureProject());
      const added = await uploadAsset(pid, file, "reference", role);
      setPhotos((ps) => [added, ...ps]);
      choose(role, added.id);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(null);
    }
  };

  return (
    <div>
      <p className="text-sm font-medium">Your photos</p>
      <p className="text-xs text-muted">Upload or paste (⌘V while pointing at a box). Only the original photo is needed.</p>
      <div className="mt-2 grid grid-cols-[repeat(auto-fill,minmax(13rem,1fr))] gap-3">
        {BOXES.map((b) => (
          <RoleBox key={b.role} {...b} busy={busy === b.role} disabled={busy !== null}
            photos={photos.filter((a) => roleOf(a) === b.role)}
            chosen={value.find((r) => r.role === b.role)?.asset_id ?? null}
            onChoose={(id) => choose(b.role, id)} onFile={(f) => void upload(b.role, f)} onError={setError} />
        ))}
      </div>
      {error && <Notice tone="error" className="mt-2">{error}</Notice>}
    </div>
  );
}

function RoleBox({ role, title, hint, required, photos, chosen, busy, disabled, onChoose, onFile, onError }: {
  role: PhotoRole;
  title: string;
  hint: string;
  required?: boolean;
  photos: Asset[];
  chosen: string | null;
  busy: boolean;
  disabled: boolean;
  onChoose: (assetId: string | null) => void;
  onFile: (f: File) => void;
  onError: (m: string) => void;
}) {
  const input = useRef<HTMLInputElement>(null);
  const paste = usePasteTarget<HTMLElement>(onFile, disabled);
  const current = photos.find((a) => a.id === chosen);
  const others = photos.filter((a) => a.id !== chosen).slice(0, 6);

  return (
    <section {...paste} aria-label={title}
      className={cn("rounded-lg border p-3", chosen ? "border-accent bg-accent/5" : "border-dashed border-border", required && !chosen && "border-accent/60")}>
      <div className="flex items-center justify-between gap-2">
        <p className="text-sm font-medium">{title}</p>
        {required ? <Badge>Needed</Badge> : <span className="text-xs text-muted">Optional</span>}
      </div>
      <p className="text-xs text-muted">{hint}</p>

      {current ? (
        <div className="relative mt-2">
          <img src={assetUrl(current.id)} alt={`${title}: ${current.original_name}`}
            className="h-40 w-full rounded-md bg-surface-2 object-contain" />
          <Button size="sm" variant="secondary" className="absolute right-1 top-1" onClick={() => onChoose(null)}
            aria-label={`Remove the ${title.toLowerCase()}`}>
            <X aria-hidden /> Remove
          </Button>
        </div>
      ) : (
        <div className="mt-2 flex h-16 w-full items-center justify-center rounded-md bg-surface-2 text-xs text-muted">
          {busy ? "Adding…" : "No photo yet"}
        </div>
      )}

      <div className="mt-2 flex flex-wrap gap-2">
        <input ref={input} type="file" accept="image/png,image/jpeg,image/webp" className="sr-only" tabIndex={-1}
          aria-label={`Upload: ${title}`}
          onChange={(e) => { const f = e.target.files?.[0]; if (f) onFile(f); e.target.value = ""; }} />
        <Button size="sm" variant="secondary" disabled={disabled} onClick={() => input.current?.click()}>
          <ImagePlus aria-hidden /> {current ? "Replace" : "Upload"}
        </Button>
        <PasteButton onFile={onFile} onError={onError} disabled={disabled} className="h-9 px-3 text-sm" />
      </div>

      {others.length > 0 && (
        <div className="mt-2">
          <p className="text-xs text-muted">Or pick an earlier {role === "person" ? "photo" : title.toLowerCase() + " photo"}</p>
          <div className="mt-1 flex flex-wrap gap-1">
            {others.map((a) => (
              <button key={a.id} type="button" onClick={() => onChoose(a.id)} title={a.original_name}
                className="rounded-md border border-border p-0.5 hover:border-accent">
                <img src={assetUrl(a.id)} alt={a.original_name} loading="lazy" className="size-12 rounded object-cover" />
              </button>
            ))}
          </div>
        </div>
      )}
    </section>
  );
}
