import { useCallback, useEffect, useState } from "react";
import type { ViewerPrefs } from "../types";

const DEFAULT: ViewerPrefs = { persona: "casual", language: "en", favourite_team_id: null, followed_player_id: null };

/** Viewer prefs persisted per view (e.g. "studio", "split-left") in localStorage. */
export function usePrefs(key: string, initial: Partial<ViewerPrefs> = {}) {
  const storageKey = `fulcrum.prefs.${key}`;
  const [prefs, setPrefs] = useState<ViewerPrefs>(() => {
    try {
      const raw = localStorage.getItem(storageKey);
      if (raw) return { ...DEFAULT, ...initial, ...JSON.parse(raw) };
    } catch {
      /* storage unavailable */
    }
    return { ...DEFAULT, ...initial };
  });

  useEffect(() => {
    try {
      localStorage.setItem(storageKey, JSON.stringify(prefs));
    } catch {
      /* storage unavailable */
    }
  }, [storageKey, prefs]);

  const update = useCallback((patch: Partial<ViewerPrefs>) => setPrefs((p) => ({ ...p, ...patch })), []);
  return [prefs, update] as const;
}
