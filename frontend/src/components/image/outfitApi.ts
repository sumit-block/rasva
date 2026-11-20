/** Calls for the I3 "Change outfit" flow. Kept apart from lib/api.ts so the
 * image phase branches merge cleanly. */
import { API, type Asset, type Job } from "@/lib/api";

export type GarmentType = "auto" | "top" | "bottom" | "full";
export type ReferenceRole = "person" | "garment";

export const GARMENT_TYPES: { id: GarmentType; label: string; hint: string }[] = [
  { id: "auto", label: "Whatever the garment is", hint: "Let the engine decide what to replace." },
  { id: "top", label: "Top only", hint: "Shirt, kurta, blouse. Keeps the trousers or skirt." },
  { id: "bottom", label: "Bottom only", hint: "Trousers, skirt, dhoti. Keeps the top." },
  { id: "full", label: "Whole outfit", hint: "Saree, lehenga, dress, suit." },
];

export const MAX_DESCRIPTION = 300;

export interface OutfitRequest {
  person_asset_id: string;
  garment_asset_id?: string;
  garment_description?: string;
  garment_type: GarmentType;
  keep_background: boolean;
  preset?: string;
  seed?: number;
}

export interface OutfitWorkflow {
  name: "outfit";
  garment_source: "photo" | "text" | "photo_and_text";
  garment_type: GarmentType;
  person_asset_id: string;
  garment_asset_id: string | null;
}

async function call<T>(path: string, init: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${API}${path}`, init);
  } catch {
    throw new Error("Can't reach Rasva on your Mac. Is it running and awake?");
  }
  const body = await res.json().catch(() => null);
  if (!res.ok) throw new Error(body?.error?.message ?? `Request failed (${res.status}).`);
  return body as T;
}

export const changeOutfit = (projectId: string, body: OutfitRequest) =>
  call<Job>(`/projects/${projectId}/outfit`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });

/** Upload a photo as a reference. The role is stored with it once I1's upload
 * accepts it; until then the server ignores the field. */
export function uploadPhoto(projectId: string, file: File, role: ReferenceRole) {
  const form = new FormData();
  form.append("file", file);
  form.append("kind", "reference");
  form.append("role", role);
  return call<Asset>(`/projects/${projectId}/assets`, { method: "POST", body: form });
}

export const outfitWorkflow = (job: Job): OutfitWorkflow | null => {
  const w = job.inputs.workflow as OutfitWorkflow | undefined;
  return job.task === "image.edit" && w?.name === "outfit" ? w : null;
};
