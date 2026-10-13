import * as SystemUI from "expo-system-ui";
import { createContext, useContext, useEffect, type ReactNode } from "react";
import { useColorScheme } from "react-native";

import { useThemePreference } from "@/stores/theme-preference";

import { darkTheme, lightTheme, type Theme } from "./theme";

const ThemeContext = createContext<Theme>(lightTheme);

export function ThemeProvider({ children }: { children: ReactNode }) {
  const system = useColorScheme();
  const preference = useThemePreference((s) => s.preference);
  const scheme = preference === "system" ? (system === "dark" ? "dark" : "light") : preference;
  const theme = scheme === "dark" ? darkTheme : lightTheme;

  useEffect(() => {
    // Root view background — no white flash behind screens in dark mode.
    SystemUI.setBackgroundColorAsync(theme.colours.canvas).catch(() => undefined);
  }, [theme]);

  return <ThemeContext.Provider value={theme}>{children}</ThemeContext.Provider>;
}

export function useTheme(): Theme {
  return useContext(ThemeContext);
}
