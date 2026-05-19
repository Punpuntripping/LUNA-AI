"use client";

import { useTheme } from "next-themes";
import { useEffect, useState } from "react";
import { Moon, Sun, Monitor, Leaf } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";

const THEME_CYCLE = ["light", "light-conservatory", "dark", "system"] as const;
type ThemeKey = (typeof THEME_CYCLE)[number];

const THEME_LABELS: Record<ThemeKey, string> = {
  light: "ورق العشب — فاتح",
  "light-conservatory": "البيت الزجاجي — فاتح بديل",
  dark: "النظام المُمَنهَج — داكن",
  system: "وضع النظام",
};

const THEME_ICONS: Record<ThemeKey, React.ComponentType<{ className?: string }>> = {
  light: Sun,
  "light-conservatory": Leaf,
  dark: Moon,
  system: Monitor,
};

export function ThemeToggle() {
  const { theme, setTheme } = useTheme();
  const [mounted, setMounted] = useState(false);

  useEffect(() => {
    setMounted(true);
  }, []);

  function cycleTheme() {
    const current = (theme as ThemeKey) ?? "system";
    const idx = THEME_CYCLE.indexOf(current);
    const nextIdx = (idx + 1) % THEME_CYCLE.length;
    setTheme(THEME_CYCLE[nextIdx]);
  }

  if (!mounted) {
    return (
      <Button variant="ghost" size="icon" disabled aria-label="تبديل المظهر">
        <Sun className="h-4 w-4" />
      </Button>
    );
  }

  const key = (theme as ThemeKey) ?? "system";
  const Icon = THEME_ICONS[key] ?? Monitor;
  const label = THEME_LABELS[key] ?? THEME_LABELS.system;

  return (
    <TooltipProvider>
      <Tooltip>
        <TooltipTrigger asChild>
          <Button
            variant="ghost"
            size="icon"
            onClick={cycleTheme}
            aria-label="تبديل المظهر"
          >
            <Icon className="h-4 w-4" />
          </Button>
        </TooltipTrigger>
        <TooltipContent side="bottom">
          <p>{label}</p>
        </TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}
