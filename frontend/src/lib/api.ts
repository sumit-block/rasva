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
  metadata?: Record<string, unknown>;
}

/** Roles a reference photo can play in an image edit (PRD §16). */
export type PhotoRole = "person" | "garment" | "pose" | "background";
export const PHOTO_ROLES: PhotoRole[] = ["person", "garment", "pose", "background"];
export const MAX_REFERENCES = 4;

/** One reference in an `image.edit` job's inputs; the server resolves it to a file. */
export interface ReferenceInput {
  asset_id: string;
  role?: PhotoRole;
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
  /** The engine's software list changed since install; model files are unaffected. */
  software_update_available: boolean;
  needs_license_confirmation: boolean;
  active_job: Job | null;
  compatibility: { compatible: boolean; reasons: string[] };
  voices: Voice[];
  license_notes: string[];
  default_voices: Record<string, string>;
  presets: { id: string; label: string; description: string }[];
}

export type AppSettings = {
  "storage.budget_bytes": number;
  "storage.os_reserve_bytes": number;
  "ui.mode": "simple" | "advanced";
  "onboarding.completed": boolean;
  "onboarding.step": number;
  "lan.enabled": boolean;
  "memory.safety_margin_bytes": number;
};

export interface MemoryWait {
  job_id: string;
  engine_id: string;
  needed_bytes: number;
  available_bytes: number;
}

export interface Status {
  orchestrator: {
    current_job: string | null;
    resident_engine: string | null;
    resident_engine_loaded: boolean;
    memory_wait: MemoryWait | null;
  };
  /** psutil's view: available = free + inactive memory on macOS. */
  memory: { total_bytes: number; available_bytes: number; level: "ok" | "tight" | "low" };
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
    if (res.status === 401 && code === "not_paired" && window.location.pathname !== "/pair") {
      // A device on home Wi-Fi that isn't paired (or was removed): go pair it.
      window.location.assign("/pair");
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
  projectJobs: (projectId: string, limit = 200) =>
    request<Job[]>(`/jobs?project_id=${encodeURIComponent(projectId)}&limit=${limit}`),
  createJob: (body: { project_id: string; task: string; engine_id?: string; inputs?: object }) =>
    request<Job>("/jobs", { method: "POST", body: json(body) }),
  cancelJob: (id: string) => request<Job>(`/jobs/${id}/cancel`, { method: "POST" }),
  retryJob: (id: string) => request<Job>(`/jobs/${id}/retry`, { method: "POST" }),
  asset: (id: string) => request<Asset>(`/assets/${id}`),
  models: () => request<EngineInfo[]>("/models"),
  installModel: (id: string, acceptLicense = false) =>
    request<Job>(`/models/${id}/install`, { method: "POST", body: json({ accept_license: acceptLicense }) }),
  updateModel: (id: string) => request<Job>(`/models/${id}/update`, { method: "POST" }),
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

export type ExperimentTask = "audio.narrate" | "image.generate";

export interface ExperimentRun {
  id: string;
  case_id: string;
  category: string;
  /** The sentence (narration) or the description (images). */
  text: string;
  seed: number | null;
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
  median_generation_s: number | null;
  median_load_s: number | null;
  peak_memory_bytes: number | null;
  peak_gpu_bytes: number | null;
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
  task: ExperimentTask;
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
    task?: ExperimentTask;
    benchmark?: string;
    texts?: string[];
    prompts?: string[];
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
  /** Leave out `language` for engines that choose entries per sentence. */
  check: (body: { text: string; language?: "hi" | "en"; project_id?: string }) =>
    request<{ normalized: string; applied: AppliedPronunciation[]; unapproved_matches: AppliedPronunciation[] }>(
      "/pronunciations/check",
      { method: "POST", body: json(body) },
    ),
  preview: (id: string, body: { engine_id: string; voice_id: string; example?: number }) =>
    request<Job>(`/pronunciations/${id}/preview`, { method: "POST", body: json(body) }),
};

export interface Take {
  id: string;
  job_id: string;
  state: JobState;
  asset_id: string | null;
  duration_s: number | null;
  error: string | null;
  progress: number | null;
  settings: Record<string, unknown>;
  text: string;
  reviewed: boolean;
  created_at: string;
}

export type SegmentStatus = "pending" | "generating" | "failed" | "ready" | "outdated" | "needs_review";

export interface Segment {
  id: string;
  position: number;
  paragraph: number;
  text: string;
  pause_after_s: number;
  current_take_id: string | null;
  current: Take | null;
  takes: Take[];
  status: SegmentStatus;
  /** The user's choice when detection is wrong; null = automatic. */
  language_override: "en" | "hi" | null;
  /** What an engine that speaks both reads it as ("mixed": its sentences differ). */
  reads_as: "en" | "hi" | "mixed";
}

export interface MixTrack {
  id: string;
  kind: "music" | "effect";
  asset_id: string;
  original_name: string;
  anchor_segment: number | null;
  offset_s: number;
  gain_db: number;
  fade_in_s: number;
  fade_out_s: number;
  loop: boolean;
  duck: boolean;
}

export interface ChapterSettings {
  engine_id: string;
  voice_id: string;
  voice_profile_id?: string | null;
  speed: number;
  use_dictionary: boolean;
  preset?: string | null;
}

export interface Chapter {
  id: string;
  project_id: string;
  title: string;
  script: string;
  settings: ChapterSettings;
  mix: { duck_db?: number };
  /** The engine reads each sentence in its own language (and honours the user's choice). */
  language_per_sentence: boolean;
  segments: Segment[];
  summary: { segments: number; counts: Partial<Record<SegmentStatus, number>>; duration_s: number; exportable: boolean };
  tracks: MixTrack[];
  exports: Job[];
  mp3_available: boolean;
  music_presets: string[];
  updated_at: string;
}

export interface ChapterListItem {
  id: string;
  title: string;
  position: number;
  segments: number;
}

export type TrackInput = Partial<Omit<MixTrack, "id" | "original_name">> & { preset?: string };

export const chapters = {
  list: (projectId: string) => request<ChapterListItem[]>(`/projects/${projectId}/chapters`),
  create: (projectId: string, body: { title: string; script: string; settings: Partial<ChapterSettings> }) =>
    request<Chapter>(`/projects/${projectId}/chapters`, { method: "POST", body: json(body) }),
  get: (id: string) => request<Chapter>(`/chapters/${id}`),
  update: (id: string, body: { title?: string; script?: string; settings?: Partial<ChapterSettings>; mix?: { duck_db: number } }) =>
    request<Chapter>(`/chapters/${id}`, { method: "PATCH", body: json(body) }),
  generate: (id: string, scope: "needed" | "failed" | "all" = "needed") =>
    request<Chapter>(`/chapters/${id}/generate`, { method: "POST", body: json({ scope }) }),
  cancel: (id: string) => request<Chapter>(`/chapters/${id}/cancel`, { method: "POST" }),
  regenerate: (segmentId: string) => request<Chapter>(`/segments/${segmentId}/regenerate`, { method: "POST" }),
  setLanguage: (segmentId: string, language: "en" | "hi" | null) =>
    request<Chapter>(`/segments/${segmentId}`, { method: "PATCH", body: json({ language }) }),
  useTake: (segmentId: string, takeId: string) =>
    request<Chapter>(`/segments/${segmentId}/takes/${takeId}/use`, { method: "POST" }),
  addTrack: (id: string, body: TrackInput) => request<Chapter>(`/chapters/${id}/tracks`, { method: "POST", body: json(body) }),
  updateTrack: (trackId: string, body: TrackInput) =>
    request<Chapter>(`/tracks/${trackId}`, { method: "PATCH", body: json(body) }),
  removeTrack: (trackId: string) => request<Chapter>(`/tracks/${trackId}`, { method: "DELETE" }),
  export: (id: string, body: { formats: ("wav" | "mp3")[]; stems: boolean }) =>
    request<Job>(`/chapters/${id}/export`, { method: "POST", body: json(body) }),
};

export const projectAssets = (projectId: string, kind?: "reference" | "import" | "output") =>
  request<Asset[]>(`/projects/${projectId}/assets${kind ? `?kind=${kind}` : ""}`);

export async function uploadAsset(projectId: string, file: File, kind: "reference" | "import", role?: PhotoRole): Promise<Asset> {
  const form = new FormData();
  form.append("file", file);
  form.append("kind", kind);
  if (role) form.append("role", role);
  return request<Asset>(`/projects/${projectId}/assets`, { method: "POST", body: form });
}

export const setPhotoRole = (assetId: string, role: PhotoRole) =>
  request<Asset>(`/assets/${assetId}`, { method: "PATCH", body: json({ role }) });

export interface LanStatus {
  enabled: boolean;
  running: boolean;
  error: string | null;
  port: number;
  setup_port: number;
  hostname: string;
  addresses: string[];
  urls: string[];
  ca_fingerprint: string | null;
  ca_name: string | null;
  pairing: { code: string; expires_at: number } | null;
}

export interface Device {
  id: string;
  name: string;
  user_agent: string;
  created_at: string;
  last_seen_at: string | null;
  last_ip: string | null;
}

export type ClientInfo = { kind: "local" | "device" | "unpaired"; device: { id: string; name: string } | null };

export const lan = {
  status: () => request<LanStatus>("/lan"),
  enable: () => request<LanStatus>("/lan/enable", { method: "POST" }),
  disable: () => request<LanStatus>("/lan/disable", { method: "POST" }),
  startPairing: () => request<LanStatus>("/devices/pairing", { method: "POST" }),
  stopPairing: () => request<LanStatus>("/devices/pairing", { method: "DELETE" }),
  devices: () => request<Device[]>("/devices"),
  revoke: async (id: string) => {
    const res = await fetch(`${API}/devices/${id}`, { method: "DELETE" });
    if (!res.ok) throw new ApiError(res.status, "revoke_failed", "Could not remove that device.");
  },
  me: () => request<ClientInfo>("/devices/me"),
  pair: (code: string, name: string) =>
    request<Device>("/devices/pair", { method: "POST", body: json({ code, name }) }),
};

export interface CleanupGroup {
  id: string;
  label: string;
  description: string;
  default: boolean;
  count: number;
  bytes: number;
}

export interface CleanupPlan {
  groups: CleanupGroup[];
  missing_files: number;
}

export const cleanup = {
  plan: () => request<CleanupPlan>("/storage/cleanup"),
  run: (groups: string[]) =>
    request<CleanupPlan & { freed_bytes: number }>("/storage/cleanup", { method: "POST", body: json({ groups }) }),
};

export interface Recommendation {
  engine_id: string;
  voice_id: string;
  reason: string;
  evidence: { quality: number; rated: number; experiment_id: string; experiment_name: string } | null;
}

export const recommendation = (language: "hi" | "en") =>
  request<Recommendation>(`/audio/recommendation?language=${language}`);

export type SentenceLanguage = "hi" | "hinglish" | "en" | "mixed" | "unknown";

export interface LanguageAnalysis {
  sentences: { index: number; text: string; language: SentenceLanguage; confidence: number }[];
  summary: { counts: Partial<Record<SentenceLanguage, number>>; dominant: SentenceLanguage; uncertain: number };
}

export interface WordSuggestion {
  original: string;
  suggestion: string;
  source: "name" | "word" | "rule";
  confident: boolean;
  start: number;
  end: number;
}

export interface TransliterationSentence {
  index: number;
  original: string;
  confidence: number;
  words: WordSuggestion[];
  suggested: string;
}

export const languageTools = {
  analyze: (text: string) => request<LanguageAnalysis>("/audio/analyze", { method: "POST", body: json({ text }) }),
  transliterate: (text: string) =>
    request<{ sentences: TransliterationSentence[] }>("/audio/transliterate", { method: "POST", body: json({ text }) }),
};

export interface QualityIssue {
  severity: "error" | "warning";
  code: string;
  message: string;
}

export interface ReferenceQuality {
  duration_s: number;
  speech_s?: number;
  snr_db?: number;
  speech_dbfs?: number;
  level: "good" | "warning" | "error";
  issues: QualityIssue[];
  transcript_issues?: QualityIssue[];
}

export interface VoiceProfile {
  id: string;
  name: string;
  description: string;
  language: "hi" | "en" | "any";
  reference_asset_id: string;
  transcript: string;
  quality: ReferenceQuality;
  consent: { basis: "self" | "permission"; speaker_name: string; statement: string; recorded_at: string; recorded_from: string };
  created_at: string;
}

export const voices = {
  list: () => request<VoiceProfile[]>("/voices"),
  statement: () => request<{ statement: string }>("/voices/consent-statement"),
  consentLog: () =>
    request<{ id: number; profile_name: string; event: "granted" | "deleted"; basis: string; speaker_name: string; at: string }[]>(
      "/voices/consent-log",
    ),
  uploadReference: (file: Blob, name: string) => {
    const form = new FormData();
    form.append("file", file, name);
    return request<{ asset: Asset; quality: ReferenceQuality }>("/voices/reference", { method: "POST", body: form });
  },
  discardReference: async (assetId: string) => {
    await fetch(`${API}/voices/reference/${assetId}`, { method: "DELETE" });
  },
  create: (body: {
    name: string; language: "hi" | "en" | "any"; reference_asset_id: string; transcript: string;
    transcript_confirmed: boolean; consent: { basis: "self" | "permission"; speaker_name: string; accepted: boolean };
  }) => request<VoiceProfile>("/voices", { method: "POST", body: json(body) }),
  remove: async (id: string) => {
    const res = await fetch(`${API}/voices/${id}`, { method: "DELETE" });
    if (!res.ok) {
      const body = await res.json().catch(() => null);
      throw new ApiError(res.status, body?.error?.code ?? "delete_failed", body?.error?.message ?? "Could not delete that voice.");
    }
  },
};

export const isUsable = (m: EngineInfo) => m.install_state === "installed" || m.install_state === "built_in";
