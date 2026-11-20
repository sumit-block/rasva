import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { API, api, TERMINAL_STATES, type Job } from "@/lib/api";

interface JobsContextValue {
  jobs: Job[];
  connected: boolean;
  active: Job | undefined;
  refresh: () => Promise<void>;
  upsert: (job: Job) => void;
}

const JobsContext = createContext<JobsContextValue | null>(null);

/** Live job list: initial fetch + Server-Sent Events, refetching on reconnect. */
export function JobsProvider({ children }: { children: ReactNode }) {
  const [jobs, setJobs] = useState<Job[]>([]);
  const [connected, setConnected] = useState(false);

  const upsert = useCallback((job: Job) => {
    setJobs((prev) => {
      const i = prev.findIndex((j) => j.id === job.id);
      if (i === -1) return [job, ...prev];
      const next = prev.slice();
      next[i] = job;
      return next;
    });
  }, []);

  const refresh = useCallback(async () => {
    try {
      setJobs(await api.jobs());
    } catch {
      /* surfaced through `connected` */
    }
  }, []);

  useEffect(() => {
    const es = new EventSource(`${API}/jobs/stream`);
    es.onopen = () => {
      setConnected(true);
      void refresh();
    };
    es.onerror = () => setConnected(false);
    es.addEventListener("job", (e) => {
      const data = JSON.parse((e as MessageEvent).data) as { job: Job };
      upsert(data.job);
    });
    return () => es.close();
  }, [refresh, upsert]);

  const value = useMemo(
    () => ({
      jobs,
      connected,
      active: jobs.find((j) => !TERMINAL_STATES.has(j.state) && j.state !== "queued"),
      refresh,
      upsert,
    }),
    [jobs, connected, refresh, upsert],
  );
  return <JobsContext.Provider value={value}>{children}</JobsContext.Provider>;
}

export function useJobs() {
  const ctx = useContext(JobsContext);
  if (!ctx) throw new Error("useJobs must be used inside JobsProvider");
  return ctx;
}
