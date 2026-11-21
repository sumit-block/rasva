import { API, request, type Job } from "@/lib/api";

/** Image versions (PRD phase I4). See backend/rasva/services/images.py. */
export interface ImageGroup {
  id: string;
  project_id: string;
  task: string;
  favourite_job_id: string | null;
  current_job_id: string | null;
  created_at: string;
  updated_at: string;
  versions: Job[];
}

const json = (body: unknown) => JSON.stringify(body);

export const imageApi = {
  groups: (projectId: string) => request<ImageGroup[]>(`/projects/${encodeURIComponent(projectId)}/image-groups`),
  setFavourite: (groupId: string, jobId: string | null) =>
    request<ImageGroup>(`/image-groups/${groupId}`, { method: "PATCH", body: json({ favourite_job_id: jobId }) }),
  again: (jobId: string, body: { seed: "new" | "same"; prompt?: string; negative_prompt?: string }) =>
    request<Job>(`/jobs/${jobId}/again`, { method: "POST", body: json(body) }),
  provenance: (assetId: string) => request<{ details: Record<string, string> }>(`/assets/${assetId}/provenance`),
};

export const exportPngUrl = (assetId: string, details: boolean) =>
  `${API}/assets/${assetId}/export?details=${details ? "true" : "false"}`;
