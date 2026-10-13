import { File, Paths } from "expo-file-system";
import { Appearance } from "react-native";
import { create } from "zustand";

// فاتح / داكن / تلقائي — the manual override of the system colour scheme.
// Not a secret, so a plain JSON file (read synchronously at startup, so the
// first frame already has the right scheme — no flash).

export type ThemePreference = "light" | "dark" | "system";

const PREFS_FILE = "rayhan.theme.json";

function readStored(): ThemePreference {
  try {
    const file = new File(Paths.document, PREFS_FILE);
    if (!file.exists) return "system";
    const parsed: unknown = JSON.parse(file.textSync());
    const value = parsed && typeof parsed === "object" ? (parsed as { theme?: unknown }).theme : null;
    return value === "light" || value === "dark" ? value : "system";
  } catch {
    return "system";
  }
}

function writeStored(pref: ThemePreference): void {
  try {
    const file = new File(Paths.document, PREFS_FILE);
    if (!file.exists) file.create();
    file.write(JSON.stringify({ theme: pref }));
  } catch {
    // Losing the preference is harmless — it falls back to the system.
  }
}

/** Applying through Appearance makes native controls (alerts, keyboard,
 *  date pickers) follow too, not only our own views. */
function apply(pref: ThemePreference): void {
  Appearance.setColorScheme(pref === "system" ? "unspecified" : pref);
}

interface ThemePreferenceState {
  preference: ThemePreference;
  setPreference: (pref: ThemePreference) => void;
}

const initial = readStored();
apply(initial);

export const useThemePreference = create<ThemePreferenceState>((set) => ({
  preference: initial,
  setPreference: (pref) => {
    apply(pref);
    writeStored(pref);
    set({ preference: pref });
  },
}));
