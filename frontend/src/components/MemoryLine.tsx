import { MemoryStick } from "lucide-react";
import { useEffect, useState } from "react";
import { api, type Status } from "@/lib/api";
import { formatBytes } from "@/lib/format";
import { cn } from "@/lib/utils";

const POLL_MS = 5000;

const LEVEL_TEXT: Record<Status["memory"]["level"], string> = {
  ok: "Memory: plenty free",
  tight: "Memory: getting tight",
  low: "Memory: low",
};

/** One small line above the job list: free memory and, if a job is held back, why. */
export function MemoryLine() {
  const [status, setStatus] = useState<Status>();

  useEffect(() => {
    let alive = true;
    const load = () =>
      api
        .status()
        .then((s) => alive && setStatus(s))
        .catch(() => undefined); // the jobs list already shows connection trouble
    void load();
    const timer = window.setInterval(load, POLL_MS);
    return () => {
      alive = false;
      window.clearInterval(timer);
    };
  }, []);

  if (!status?.memory) return null;
  const { memory } = status;
  const wait = status.orchestrator.memory_wait;
  return (
    <p className="mb-3 flex flex-wrap items-center gap-x-2 text-xs text-muted" aria-live="polite">
      <MemoryStick className="size-3.5" aria-hidden />
      <span className={cn(memory.level === "low" && "text-danger", memory.level === "tight" && "text-warning")}>
        {LEVEL_TEXT[memory.level]}
      </span>
      <span>
        · {formatBytes(memory.available_bytes)} of {formatBytes(memory.total_bytes)} available
      </span>
      {wait && <span>· next job needs about {formatBytes(wait.needed_bytes)}</span>}
    </p>
  );
}
