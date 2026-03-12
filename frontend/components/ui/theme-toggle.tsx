"use client";

import { useTheme } from "next-themes";
import { useEffect, useState } from "react";
import { Moon, Sun, Monitor } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";

const THEME_CYCLE = ["light", "dark", "system"] as const;

const THEME_LABELS: Record<string, string> = {
  light: "الوضع الفاتح",
  dark: "الوضع الداكن",
  system: "وضع النظام",
};

export function ThemeToggle() {
  const { theme, setTheme } = useTheme();
  const [mounted, setMounted] = useState(false);

  useEffect(() => {
    setMounted(true);
  }, []);

  function cycleTheme() {
    const currentIndex = THEME_CYCLE.indexOf(
      (theme as (typeof THEME_CYCLE)[number]) ?? "system"
    );
    const nextIndex = (currentIndex + 1) % THEME_CYCLE.length;
    setTheme(THEME_CYCLE[nextIndex]);
  }

  // Prevent hydration mismatch — render a placeholder until mounted
  if (!mounted) {
    return (
      <Button variant="ghost" size="icon" disabled aria-label="تبديل المظهر">
        <Sun className="h-4 w-4" />
      </Button>
    );
  }

  const label = THEME_LABELS[theme ?? "system"] ?? THEME_LABELS.system;

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
            {theme === "light" && <Sun className="h-4 w-4" />}
            {theme === "dark" && <Moon className="h-4 w-4" />}
            {theme === "system" && <Monitor className="h-4 w-4" />}
          </Button>
        </TooltipTrigger>
        <TooltipContent side="bottom">
          <p>{label}</p>
        </TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}
