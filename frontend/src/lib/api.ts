/** Typed client for the local Rasva API. Same-origin only; no third-party calls. */

export const API = "/api/v1";

export type JobState =
  | "draft"
  | "queued"
  | "downloading"
  | "preparing"
  | "loading_model"
  | "processing"
  | "finalizing"
  | "completed"
  | "failed"
  | "canceled"
  | "interrupted";

export const TERMINAL_STATES: ReadonlySet<JobState> = new Set([
  "completed",
  "failed",
  "canceled",
  "interrupted",
]);

export interface Job {
  id: string;
  project_id: string | null;
  task: string;
  engine_id: string;
  state: JobState;
  inputs: Record<string, unknown>;
  outputs: string[];
  progress: number | null;
  stage_detail: string | null;
  error_code: string | null;
  error_message: string | null;
  metrics: Record<string, unknown>;
  cancel_requested: boolean;
  retry_of: string | null;
  created_at: string;
  updated_at: string;
  started_at: string | null;
  finished_at: string | null;
}

export interface Project {
  id: string;
  name: string;
  module: "audio" | "image";
  description: string;
  status: "active" | "archived";
  settings: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

export interface Asset {
  id: string;
  project_id: string;
  kind: "reference" | "import" | "output";
  media_type: string;
  original_name: string;
  size_bytes: number;
  created_at: string;
}

export interface Check {
  id: string;
  label: string;
  status: "pass" | "warn" | "fail";
  message: string;
}

export interface SystemCheck {
  ready: boolean;
  checks: Check[];
}

export interface Storage {
  budget_bytes: number;
  used_bytes: number;
  remaining_bytes: number;
  ratio: number;
  level: "ok" | "warning" | "critical" | "full";
  free_disk_bytes: number;
  os_reserve_bytes: number;
  categories: Record<string, number>;
  planning_bands_gb: Record<string, number>;
}

export interface Voice {
  id: string;
  display_name: string;
  language: "en" | "hi";
  accent: string | null;
  gender: "female" | "male" | null;
}

export interface EngineInfo {
  id: string;
  display_name: string;
  summary: string;
  version: string;
  modality: "audio" | "image" | "diagnostic";
  tasks: string[];
  languages: string[];
  features: string[];
  license: string;
  license_class: "permissive" | "open-weight" | "restricted" | "unknown";
  approx_download_bytes: number;
  approx_installed_bytes: number;
  download_bytes: number;
  required_bytes: number;
  installed_bytes: number;
  install_state: "built_in" | "not_installed" | "installing" | "installed" | "failed" | "removing";
  install_error: string | null;
  needs_license_confirmation: boolean;
  active_job: Job | null;
  compatibility: { compatible: boolean; reasons: string[] };
  voices: Voice[];
  license_notes: string[];
}

export type AppSettings = {
  "storage.budget_bytes": number;
  "storage.os_reserve_bytes": number;
  "ui.mode": "simple" | "advanced";
  "onboarding.completed": boolean;
  "lan.enabled": boolean;
};

export interface Status {
  orchestrator: { current_job: string | null; resident_engine: string | null };
  storage: Pick<Storage, "used_bytes" | "budget_bytes" | "remaining_bytes" | "level">;
  settings: AppSettings;
}

export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
  ) {
    super(message);
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${API}${path}`, {
      ...init,
      headers: {
        ...(init?.body && !(init.body instanceof FormData)
          ? { "Content-Type": "application/json" }
          : {}),
        ...init?.headers,
      },
    });
  } catch {
    throw new ApiError(0, "unreachable", "Can't reach Rasva on your Mac. Is it running and awake?");
  }
  if (!res.ok) {
    let code = "http_error";
    let message = `Request failed (${res.status}).`;
    try {
      const body = await res.json();
      code = body?.error?.code ?? code;
      message = body?.error?.message ?? message;
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(res.status, code, message);
  }
  return res.json() as Promise<T>;
}

const json = (body: unknown) => JSON.stringify(body);

export const api = {
  status: () => request<Status>("/system/status"),
  check: () => request<SystemCheck>("/system/check"),
  storage: (refresh = false) => request<Storage>(`/system/storage${refresh ? "?refresh=true" : ""}`),
  projects: (params: { q?: string; module?: string } = {}) => {
    const qs = new URLSearchParams(Object.entries(params).filter(([, v]) => v) as [string, string][]);
    return request<Project[]>(`/projects${qs.size ? `?${qs}` : ""}`);
  },
  createProject: (body: { name: string; module: "audio" | "image" }) =>
    request<Project>("/projects", { method: "POST", body: json(body) }),
  updateProject: (id: string, body: Partial<Pick<Project, "name" | "status" | "description" | "settings">>) =>
    request<Project>(`/projects/${id}`, { method: "PATCH", body: json(body) }),
  jobs: () => request<Job[]>("/jobs?limit=100"),
  createJob: (body: { project_id: string; task: string; engine_id?: string; inputs?: object }) =>
    request<Job>("/jobs", { method: "POST", body: json(body) }),
  cancelJob: (id: string) => request<Job>(`/jobs/${id}/cancel`, { method: "POST" }),
  retryJob: (id: string) => request<Job>(`/jobs/${id}/retry`, { method: "POST" }),
  asset: (id: string) => request<Asset>(`/assets/${id}`),
  models: () => request<EngineInfo[]>("/models"),
  installModel: (id: string, acceptLicense = false) =>
    request<Job>(`/models/${id}/install`, { method: "POST", body: json({ accept_license: acceptLicense }) }),
  removeModel: (id: string) => request<Job>(`/models/${id}`, { method: "DELETE" }),
  settings: () => request<AppSettings>("/settings"),
  updateSettings: (body: Partial<AppSettings>) =>
    request<AppSettings>("/settings", { method: "PATCH", body: json(body) }),
};

export const assetUrl = (id: string, download = false) =>
  `${API}/assets/${id}/content${download ? "?download=true" : ""}`;

export const isUsable = (m: EngineInfo) => m.install_state === "installed" || m.install_state === "built_in";
