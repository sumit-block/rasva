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
  "onboarding.step": number;
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

export interface Benchmark {
  id: string;
  name: string;
  description: string;
  task: string;
  cases: number;
  categories: Record<string, number>;
}

export interface ExperimentCandidate {
  key: string;
  engine_id: string;
  settings: Record<string, unknown>;
}

export interface ExperimentRun {
  id: string;
  case_id: string;
  category: string;
  text: string;
  focus: string;
  candidate_key: string;
  engine_id: string;
  status: JobState | "skipped" | null;
  skip_reason: string | null;
  error: string | null;
  progress: number | null;
  metrics: Record<string, unknown>;
  output_asset_id: string | null;
  scores: Record<string, number>;
}

export interface Gate {
  id: string;
  passed: boolean | null;
  message: string;
}

export interface CandidateSummary {
  key: string;
  engine_id: string;
  total: number;
  completed: number;
  failed: number;
  pending: number;
  skipped: number;
  rated: number;
  quality: number | null;
  quality_by_dimension: Record<string, number>;
  median_rtf: number | null;
  median_load_s: number | null;
  peak_memory_bytes: number | null;
  installed_bytes: number;
  reliability: number | null;
  score_experiment: number | null;
  score_final: number | null;
  complete: boolean;
  gates: Gate[];
  eligible: boolean | null;
}

export interface ExperimentListItem {
  id: string;
  name: string;
  objective: string;
  benchmark: string | null;
  status: "active" | "decided" | "archived";
  decision: string | null;
  candidates: ExperimentCandidate[];
  runs: number;
  completed: number;
  rated: number;
  created_at: string;
}

export interface Experiment extends Omit<ExperimentListItem, "runs" | "completed" | "rated"> {
  usability: Record<string, number>;
  review_dimensions: string[];
  runs: ExperimentRun[];
  summary: CandidateSummary[];
}

export const experiments = {
  benchmarks: () => request<Benchmark[]>("/experiments/benchmarks"),
  list: () => request<ExperimentListItem[]>("/experiments"),
  get: (id: string) => request<Experiment>(`/experiments/${id}`),
  create: (body: {
    name: string;
    objective?: string;
    benchmark?: string;
    texts?: string[];
    candidates: { engine_id: string; settings: Record<string, unknown> }[];
  }) => request<Experiment>("/experiments", { method: "POST", body: json(body) }),
  score: (id: string, runId: string, scores: Record<string, number | null>) =>
    request<Experiment>(`/experiments/${id}/runs/${runId}/scores`, { method: "PUT", body: json({ scores }) }),
  usability: (id: string, candidate_key: string, score: number | null) =>
    request<Experiment>(`/experiments/${id}/usability`, { method: "PUT", body: json({ candidate_key, score }) }),
  decide: (id: string, decision: string) =>
    request<Experiment>(`/experiments/${id}/decision`, { method: "POST", body: json({ decision }) }),
  cancel: (id: string) => request<Experiment>(`/experiments/${id}/cancel`, { method: "POST" }),
};

export interface PronunciationEntry {
  id: string;
  project_id: string | null;
  written_forms: string[];
  spoken: string;
  phonetic_hint: string;
  language: "hi" | "en" | "any";
  whole_word: boolean;
  case_sensitive: boolean;
  approved: boolean;
  examples: string[];
  notes: string;
  updated_at: string;
}

export interface AppliedPronunciation {
  entry_id: string;
  written: string;
  spoken: string;
  start: number;
  end: number;
}

export type EntryInput = Partial<Omit<PronunciationEntry, "id" | "updated_at">>;

export const pronunciations = {
  list: (projectId?: string) =>
    request<PronunciationEntry[]>(`/pronunciations${projectId ? `?project_id=${encodeURIComponent(projectId)}` : ""}`),
  suggestions: () => request<{ written: string; hint: string }[]>("/pronunciations/suggestions"),
  create: (body: EntryInput) => request<PronunciationEntry>("/pronunciations", { method: "POST", body: json(body) }),
  update: (id: string, body: EntryInput) =>
    request<PronunciationEntry>(`/pronunciations/${id}`, { method: "PATCH", body: json(body) }),
  remove: async (id: string) => {
    const res = await fetch(`${API}/pronunciations/${id}`, { method: "DELETE" });
    if (!res.ok) throw new ApiError(res.status, "delete_failed", "Could not delete that pronunciation.");
  },
  check: (body: { text: string; language: "hi" | "en"; project_id?: string }) =>
    request<{ normalized: string; applied: AppliedPronunciation[]; unapproved_matches: AppliedPronunciation[] }>(
      "/pronunciations/check",
      { method: "POST", body: json(body) },
    ),
  preview: (id: string, body: { engine_id: string; voice_id: string; example?: number }) =>
    request<Job>(`/pronunciations/${id}/preview`, { method: "POST", body: json(body) }),
};

export const isUsable = (m: EngineInfo) => m.install_state === "installed" || m.install_state === "built_in";
