// I2 (same person, new pose): pose figures and pose jobs, on I1's reference
// contract (uploadAsset with a role, references resolved by the jobs API).
import { api, projectAssets, uploadAsset, type Asset, type Job, type PhotoRole } from "@/lib/api";

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

export const uploadReference = async (projectId: string, file: Blob, name: string, role: PhotoRole) => {
  const f = file instanceof File ? file : new File([file], name, { type: file.type || "image/png" });
  const a = await uploadAsset(projectId, f, "reference", role);
  return { ...a, metadata: a.metadata ?? {} } as ImageAsset;
};

export const imageAssets = async (projectId: string) =>
  (await projectAssets(projectId))
    .filter((a) => a.media_type.startsWith("image/"))
    .map((a) => ({ ...a, metadata: a.metadata ?? {} })) as ImageAsset[];

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
