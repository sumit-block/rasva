import { useEffect, useState } from "react";
import { api, type EngineInfo } from "@/lib/api";

/**
 * Low-memory mode for image engines (setting `image.low_memory`). Engines that
 * support it use less memory, more slowly; others ignore it, and we say so.
 */
export function LowMemoryToggle({ engine }: { engine: EngineInfo }) {
  const [on, setOn] = useState<boolean>();
  const [error, setError] = useState<string>();
  const supported = engine.features.includes("low_memory");

  useEffect(() => {
    api.settings().then((s) => setOn(Boolean(s["image.low_memory"])), (e: Error) => setError(e.message));
  }, []);

  const change = async (value: boolean) => {
    setOn(value);
    setError(undefined);
    try {
      setOn(Boolean((await api.updateSettings({ "image.low_memory": value }))["image.low_memory"]));
    } catch (e) {
      setOn(!value);
      setError((e as Error).message);
    }
  };

  return (
    <div className="mt-4 border-t border-border pt-3">
      <label className="flex min-h-11 items-start gap-2 text-sm">
        <input type="checkbox" className="mt-1" checked={on ?? false} disabled={on === undefined}
          onChange={(e) => void change(e.target.checked)} />
        <span>
          <span className="font-medium">Low-memory mode</span>
          <span className="block text-xs text-muted">
            {supported
              ? "Uses less memory so other apps stay responsive. Images take longer. Applies to new images and retries."
              : `${engine.display_name} doesn't have a low-memory mode, so this changes nothing for it yet.`}
          </span>
        </span>
      </label>
      {error && <p role="alert" className="text-xs text-danger">{error}</p>}
    </div>
  );
}
