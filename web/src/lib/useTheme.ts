"use client";

import { useCallback, useEffect, useState } from "react";
import { applyTheme, readStoredTheme, storeTheme, type ThemePreference } from "./theme";

export function useTheme(): {
  preference: ThemePreference;
  setPreference: (next: ThemePreference) => void;
} {
  const [preference, setPreferenceState] = useState<ThemePreference>("system");

  // Read the stored choice after mount so server and client markup agree.
  useEffect(() => {
    setPreferenceState(readStoredTheme());
  }, []);

  // In "system" mode, follow OS changes live.
  useEffect(() => {
    if (preference !== "system" || typeof window.matchMedia !== "function") return;
    const query = window.matchMedia("(prefers-color-scheme: dark)");
    const onChange = () => applyTheme("system");
    query.addEventListener("change", onChange);
    return () => query.removeEventListener("change", onChange);
  }, [preference]);

  const setPreference = useCallback((next: ThemePreference) => {
    setPreferenceState(next);
    storeTheme(next);
    applyTheme(next);
  }, []);

  return { preference, setPreference };
}
