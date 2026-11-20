import { api, type Job } from "./api";

const CHECK_PROJECT = "Pipeline checks";

/** Runs the built-in diagnostic engine end to end. Reuses one project for all checks. */
export async function runPipelineCheck(): Promise<Job> {
  const existing = (await api.projects({ q: CHECK_PROJECT })).find((p) => p.name === CHECK_PROJECT);
  const project = existing ?? (await api.createProject({ name: CHECK_PROJECT, module: "audio" }));
  return api.createJob({ project_id: project.id, task: "diagnostic.tone", inputs: { duration_s: 2 } });
}
