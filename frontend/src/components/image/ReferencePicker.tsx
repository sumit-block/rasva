import { ImagePlus } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { Notice } from "@/components/ui/notice";
import {
  assetUrl,
  MAX_REFERENCES,
  PHOTO_ROLES,
  projectAssets,
  setPhotoRole,
  uploadAsset,
  type Asset,
  type PhotoRole,
  type ReferenceInput,
} from "@/lib/api";
import { cn } from "@/lib/utils";

const ROLE_LABEL: Record<PhotoRole, string> = {
  person: "Person",
  garment: "Clothing",
  pose: "Pose",
  background: "Background",
};

const roleOf = (a: Asset): PhotoRole => {
  const r = a.metadata?.role;
  return PHOTO_ROLES.includes(r as PhotoRole) ? (r as PhotoRole) : "person";
};

/**
 * The project's reference photos: upload, give each a role, and pick up to
 * MAX_REFERENCES for an edit. Selection order is kept; the engine orders by role.
 */
export function ReferencePicker({ projectId, value, onChange, ensureProject }: {
  projectId: string | null;
  value: ReferenceInput[];
  onChange: (refs: ReferenceInput[]) => void;
  ensureProject: () => Promise<string>;
}) {
  const [photos, setPhotos] = useState<Asset[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();
  const input = useRef<HTMLInputElement>(null);

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

  const selected = new Map(value.map((r) => [r.asset_id, r]));

  const toggle = (a: Asset) => {
    if (selected.has(a.id)) return onChange(value.filter((r) => r.asset_id !== a.id));
    if (value.length >= MAX_REFERENCES) return setError(`Use at most ${MAX_REFERENCES} photos at once.`);
    setError(undefined);
    onChange([...value, { asset_id: a.id, role: roleOf(a) }]);
  };

  const changeRole = async (a: Asset, role: PhotoRole) => {
    try {
      const updated = await setPhotoRole(a.id, role);
      setPhotos((ps) => ps.map((p) => (p.id === a.id ? updated : p)));
      onChange(value.map((r) => (r.asset_id === a.id ? { ...r, role } : r)));
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const upload = async (files: FileList | null) => {
    if (!files?.length) return;
    setBusy(true);
    setError(undefined);
    try {
      const pid = projectId ?? (await ensureProject());
      const added: Asset[] = [];
      for (const f of Array.from(files)) added.push(await uploadAsset(pid, f, "reference", added.length || photos.length ? "garment" : "person"));
      setPhotos((ps) => [...ps, ...added]);
      const room = MAX_REFERENCES - value.length;
      onChange([...value, ...added.slice(0, Math.max(room, 0)).map((a) => ({ asset_id: a.id, role: roleOf(a) }))]);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
      if (input.current) input.current.value = "";
    }
  };

  return (
    <div>
      <div className="flex items-center justify-between">
        <p className="text-sm font-medium">Your photos <span className="font-normal text-muted">({value.length} of {MAX_REFERENCES} chosen)</span></p>
        <label className={cn("inline-flex min-h-11 cursor-pointer items-center gap-2 rounded-lg border border-border px-3 text-sm hover:bg-surface-2", busy && "opacity-60")}>
          <ImagePlus className="size-4" aria-hidden /> {busy ? "Adding…" : "Add photos"}
          <input ref={input} type="file" accept="image/png,image/jpeg,image/webp" multiple className="sr-only"
            disabled={busy} onChange={(e) => void upload(e.target.files)} />
        </label>
      </div>
      {photos.length === 0 ? (
        <p className="mt-2 text-sm text-muted">Add a photo of the person, and optionally a clothing, pose or background photo.</p>
      ) : (
        <ul className="mt-3 grid grid-cols-2 gap-2 sm:grid-cols-3">
          {photos.map((a) => {
            const chosen = selected.has(a.id);
            return (
              <li key={a.id} className={cn("rounded-lg border p-2", chosen ? "border-accent bg-accent/10" : "border-border")}>
                <button type="button" onClick={() => toggle(a)} aria-pressed={chosen} className="block w-full">
                  <img src={assetUrl(a.id)} alt={a.original_name} loading="lazy" className="aspect-square w-full rounded-md object-cover" />
                  <span className="mt-1 block truncate text-left text-xs text-muted">{chosen ? "Chosen · " : ""}{a.original_name}</span>
                </button>
                <label className="mt-1 block text-xs">
                  <span className="sr-only">Role of {a.original_name}</span>
                  <select value={roleOf(a)} onChange={(e) => void changeRole(a, e.target.value as PhotoRole)}
                    className="min-h-9 w-full rounded-md border border-border bg-bg px-2 text-xs">
                    {PHOTO_ROLES.map((r) => <option key={r} value={r}>{ROLE_LABEL[r]}</option>)}
                  </select>
                </label>
              </li>
            );
          })}
        </ul>
      )}
      {error && <Notice tone="error" className="mt-2">{error}</Notice>}
    </div>
  );
}
