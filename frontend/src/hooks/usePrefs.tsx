import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";
import { api } from "@/lib/api";

type Theme = "dark" | "light" | "system";
interface Prefs {
  mode: "simple" | "advanced";
  theme: Theme;
}

const PrefsContext = createContext<{ prefs: Prefs; setMode: (m: Prefs["mode"]) => Promise<void>; setTheme: (t: Theme) => void } | null>(null);

function readTheme(): Theme {
  try {
    const t = localStorage.getItem("rasva.theme");
    return t === "light" || t === "system" ? t : "dark";
  } catch {
    return "dark";
  }
}

function applyTheme(theme: Theme) {
  const resolved =
    theme === "system" ? (window.matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark") : theme;
  document.documentElement.dataset.theme = resolved;
}

/** UI mode lives on the Mac (shared by paired devices); theme is per-device. */
export function PrefsProvider({ children }: { children: ReactNode }) {
  const [prefs, setPrefs] = useState<Prefs>({ mode: "simple", theme: readTheme() });

  useEffect(() => {
    api.settings().then((s) => setPrefs((p) => ({ ...p, mode: s["ui.mode"] })), () => undefined);
  }, []);

  useEffect(() => {
    applyTheme(prefs.theme);
    if (prefs.theme !== "system") return;
    const mq = window.matchMedia("(prefers-color-scheme: light)");
    const onChange = () => applyTheme("system");
    mq.addEventListener("change", onChange);
    return () => mq.removeEventListener("change", onChange);
  }, [prefs.theme]);

  const setMode = useCallback(async (mode: Prefs["mode"]) => {
    await api.updateSettings({ "ui.mode": mode });
    setPrefs((p) => ({ ...p, mode }));
  }, []);

  const setTheme = useCallback((theme: Theme) => {
    try {
      localStorage.setItem("rasva.theme", theme);
    } catch {
      /* storage unavailable: theme still applies for this session */
    }
    setPrefs((p) => ({ ...p, theme }));
  }, []);

  return <PrefsContext.Provider value={{ prefs, setMode, setTheme }}>{children}</PrefsContext.Provider>;
}

export function usePrefs() {
  const ctx = useContext(PrefsContext);
  if (!ctx) throw new Error("usePrefs must be used inside PrefsProvider");
  return ctx;
}
