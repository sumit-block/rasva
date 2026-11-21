import { useState } from "react";
import { CleanupCard } from "@/components/CleanupCard";
import { DevicesCard } from "@/components/DevicesCard";
import { PageHeader } from "@/components/layout/PageHeader";
import { Button } from "@/components/ui/button";
import { Card, CardDescription, CardTitle } from "@/components/ui/card";
import { Notice } from "@/components/ui/notice";
import { Progress } from "@/components/ui/progress";
import { useAsync } from "@/hooks/useAsync";
import { usePrefs } from "@/hooks/usePrefs";
import { api } from "@/lib/api";
import { CATEGORY_LABEL, formatBytes } from "@/lib/format";
import { cn } from "@/lib/utils";

const GB = 1024 ** 3;

function Segmented<T extends string>({ value, options, onChange, label }: { value: T; options: { value: T; label: string }[]; onChange: (v: T) => void; label: string }) {
  return (
    <div role="radiogroup" aria-label={label} className="inline-flex rounded-lg border border-border bg-surface-2 p-1">
      {options.map((o) => (
        <button
          key={o.value}
          role="radio"
          aria-checked={value === o.value}
          onClick={() => onChange(o.value)}
          className={cn(
            "min-h-9 rounded-md px-3 text-sm transition-colors",
            value === o.value ? "bg-surface text-fg shadow-sm" : "text-muted hover:text-fg",
          )}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}

export function SettingsPage() {
  const { prefs, setMode, setTheme } = usePrefs();
  const storage = useAsync(() => api.storage(true));
  const [budgetGb, setBudgetGb] = useState<string>("");
  const [message, setMessage] = useState<{ tone: "info" | "error"; text: string }>();

  const s = storage.data;

  const saveBudget = async () => {
    const gb = Number(budgetGb);
    if (!Number.isFinite(gb) || gb <= 0) return;
    try {
      await api.updateSettings({ "storage.budget_bytes": Math.round(gb * GB) });
      setMessage({ tone: "info", text: `Storage budget set to ${gb} GB.` });
      setBudgetGb("");
      await storage.reload();
    } catch (e) {
      setMessage({ tone: "error", text: (e as Error).message });
    }
  };

  return (
    <>
      <PageHeader title="Settings" />
      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <CardTitle>Interface</CardTitle>
          <CardDescription>Advanced mode shows engine choice, seeds, diagnostics and experiments.</CardDescription>
          <div className="mt-4 flex flex-col gap-4">
            <Segmented
              label="Interface mode"
              value={prefs.mode}
              onChange={(m) => void setMode(m)}
              options={[
                { value: "simple", label: "Simple" },
                { value: "advanced", label: "Advanced" },
              ]}
            />
            <Segmented
              label="Theme"
              value={prefs.theme}
              onChange={setTheme}
              options={[
                { value: "dark", label: "Dark" },
                { value: "light", label: "Light" },
                { value: "system", label: "Match system" },
              ]}
            />
          </div>
        </Card>

        <DevicesCard />

        <Card className="lg:col-span-2">
          <CardTitle>Storage</CardTitle>
          {s ? (
            <>
              <CardDescription>
                {formatBytes(s.used_bytes)} of {formatBytes(s.budget_bytes)} used. Rasva also keeps {formatBytes(s.os_reserve_bytes)} free
                on the disk for macOS ({formatBytes(s.free_disk_bytes)} free now).
              </CardDescription>
              <Progress className="mt-3" value={s.ratio} label="Storage used" tone={s.level === "ok" ? "accent" : s.level === "warning" ? "warning" : "danger"} />
              <table className="mt-4 w-full text-sm">
                <thead>
                  <tr className="text-left text-xs text-muted">
                    <th className="py-2 font-medium">Category</th>
                    <th className="py-2 text-right font-medium">Used</th>
                    {prefs.mode === "advanced" && <th className="py-2 text-right font-medium">Planned</th>}
                  </tr>
                </thead>
                <tbody className="divide-y divide-border">
                  {Object.entries(s.categories).map(([key, bytes]) => (
                    <tr key={key}>
                      <td className="py-2">{CATEGORY_LABEL[key] ?? key}</td>
                      <td className="py-2 text-right tabular-nums">{formatBytes(bytes)}</td>
                      {prefs.mode === "advanced" && (
                        <td className="py-2 text-right text-muted tabular-nums">
                          {s.planning_bands_gb[key] != null ? `${s.planning_bands_gb[key]} GB` : "–"}
                        </td>
                      )}
                    </tr>
                  ))}
                </tbody>
              </table>
              {prefs.client === "local" && <div className="mt-4 flex flex-wrap items-end gap-2">
                <label className="text-sm">
                  <span className="mb-1 block font-medium">Storage budget (GB)</span>
                  <input
                    inputMode="numeric"
                    value={budgetGb}
                    onChange={(e) => setBudgetGb(e.target.value)}
                    placeholder={String(Math.round(s.budget_bytes / GB))}
                    className="min-h-11 w-32 rounded-lg border border-border bg-bg px-3"
                  />
                </label>
                <Button variant="secondary" onClick={saveBudget} disabled={!budgetGb}>
                  Save budget
                </Button>
              </div>}
              {message && <Notice tone={message.tone} className="mt-3">{message.text}</Notice>}
            </>
          ) : (
            <CardDescription>{storage.error?.message ?? "Measuring…"}</CardDescription>
          )}
        </Card>
        {prefs.client === "local" && <MemoryCard />}
        {prefs.client === "local" && <CleanupCard onCleaned={() => void storage.reload()} />}
      </div>
    </>
  );
}

function MemoryCard() {
  const settings = useAsync(() => api.settings());
  const [marginGb, setMarginGb] = useState("");
  const [message, setMessage] = useState<{ tone: "info" | "error"; text: string }>();
  const current = settings.data?.["memory.safety_margin_bytes"];

  const save = async () => {
    const gb = Number(marginGb);
    if (marginGb.trim() === "" || !Number.isFinite(gb) || gb < 0) return;
    try {
      await api.updateSettings({ "memory.safety_margin_bytes": Math.round(gb * GB) });
      setMessage({ tone: "info", text: `Memory safety margin set to ${gb} GB.` });
      setMarginGb("");
      await settings.reload();
    } catch (e) {
      setMessage({ tone: "error", text: (e as Error).message });
    }
  };

  return (
    <Card>
      <CardTitle>Memory</CardTitle>
      <CardDescription>
        An engine starts only when your Mac has enough free memory for it plus this margin. Otherwise its job waits and starts
        on its own when memory frees up. A smaller margin starts jobs sooner but risks a slow Mac.
      </CardDescription>
      <div className="mt-4 flex flex-wrap items-end gap-2">
        <label className="text-sm">
          <span className="mb-1 block font-medium">Safety margin (GB, 0–8)</span>
          <input
            inputMode="decimal"
            value={marginGb}
            onChange={(e) => setMarginGb(e.target.value)}
            placeholder={current != null ? String(+(current / GB).toFixed(1)) : ""}
            className="min-h-11 w-32 rounded-lg border border-border bg-bg px-3"
          />
        </label>
        <Button variant="secondary" onClick={save} disabled={!marginGb}>
          Save margin
        </Button>
      </div>
      {message && <Notice tone={message.tone} className="mt-3">{message.text}</Notice>}
    </Card>
  );
}
