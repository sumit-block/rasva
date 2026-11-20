// I2 (same person, new pose): reference photos with roles and pose jobs.
// Kept beside the components instead of lib/api.ts so image branches merge cleanly;
// I1 owns the shared reference contract and may replace these helpers.
import { API, ApiError, api, projectAssets, type Asset, type Job } from "@/lib/api";

export type RefRole = "person" | "garment" | "pose" | "background";
export type ImageAsset = Asset & { metadata: Record<string, unknown> };

export const POSE_TASK = "image.pose";
export const EDIT_TASK = "image.edit";

export interface BuiltInPose {
  id: string;
  label: string;
  framing: string;
  file: string;
  width: number;
  height: number;
}

// Mirrors rasva.engines.pose.edit.IDENTITY (labels and descriptions).
export const IDENTITY = [
  { id: "keep_face", label: "Keep the face closely", description: "Stays closest to the person in your photo; the pose may be looser." },
  { id: "balanced", label: "Balanced", description: "A good mix of likeness and pose." },
  { id: "follow_pose", label: "Follow the pose closely", description: "Matches the pose most exactly; the likeness may drift a little." },
] as const;
export type IdentityId = (typeof IDENTITY)[number]["id"];

export const FRAMING_LABEL: Record<string, string> = {
  full_body: "Full body",
  upper_body: "Waist up",
  portrait: "Head and shoulders",
};

async function errorFrom(res: Response): Promise<ApiError> {
  try {
    const body = await res.json();
    return new ApiError(res.status, body?.error?.code ?? "http_error", body?.error?.message ?? `Request failed (${res.status}).`);
  } catch {
    return new ApiError(res.status, "http_error", `Request failed (${res.status}).`);
  }
}

export async function uploadReference(projectId: string, file: Blob, name: string, role: RefRole): Promise<ImageAsset> {
  const form = new FormData();
  form.append("file", file, name);
  form.append("kind", "reference");
  form.append("role", role);
  let res: Response;
  try {
    res = await fetch(`${API}/projects/${projectId}/assets`, { method: "POST", body: form });
  } catch {
    throw new ApiError(0, "unreachable", "Can't reach Rasva on your Mac. Is it running and awake?");
  }
  if (!res.ok) throw await errorFrom(res);
  return (await res.json()) as ImageAsset;
}

export const imageAssets = async (projectId: string) =>
  (await projectAssets(projectId)).filter((a) => a.media_type.startsWith("image/")) as ImageAsset[];

export const roleOf = (a: ImageAsset) => (typeof a.metadata?.role === "string" ? (a.metadata.role as string) : undefined);
export const isPoseFigure = (a: ImageAsset) => a.metadata?.kind === "pose";

export async function builtInPoses(): Promise<BuiltInPose[]> {
  const res = await fetch("/poses/poses.json");
  if (!res.ok) throw new Error("The built-in poses could not be loaded.");
  return (await res.json()) as BuiltInPose[];
}

/** Upload a built-in figure into the project once; later picks reuse it. */
export async function builtInPoseAsset(projectId: string, pose: BuiltInPose, existing: ImageAsset[]): Promise<ImageAsset> {
  const name = `pose-${pose.id}.png`;
  const found = existing.find((a) => a.original_name === name && roleOf(a) === "pose");
  if (found) return found;
  const res = await fetch(`/poses/${pose.file}`);
  if (!res.ok) throw new Error("That pose figure could not be loaded.");
  return uploadReference(projectId, await res.blob(), name, "pose");
}

export const extractPose = (projectId: string, engineId: string, assetId: string) =>
  api.createJob({ project_id: projectId, task: POSE_TASK, engine_id: engineId, inputs: { references: [{ asset_id: assetId, role: "pose" }] } });

export function changePose(
  projectId: string,
  engineId: string,
  opts: { personId: string; poseId: string; poseSource: "figure" | "photo"; identity: IdentityId; prompt: string; seed?: number },
): Promise<Job> {
  return api.createJob({
    project_id: projectId,
    task: EDIT_TASK,
    engine_id: engineId,
    inputs: {
      references: [
        { asset_id: opts.personId, role: "person" },
        { asset_id: opts.poseId, role: "pose" },
      ],
      identity: opts.identity,
      pose_source: opts.poseSource,
      ...(opts.prompt.trim() ? { prompt: opts.prompt } : {}),
      ...(opts.seed !== undefined ? { seed: opts.seed } : {}),
    },
  });
}

export const referencesOf = (job: Job) =>
  (Array.isArray(job.inputs.references) ? job.inputs.references : []) as { asset_id?: string; role?: string }[];
export const isPoseChange = (job: Job) => job.task === EDIT_TASK && referencesOf(job).some((r) => r.role === "pose");
