"use client";

import { useState } from "react";
import { cn } from "@/lib/utils";
import {
  PROFESSION_GROUPS,
  STEP_PROFESSION,
  type ProfessionGroupKey,
} from "../onboarding-content";

/** What the user has picked so far. `group: null` = untouched (a dismissal
 *  then records «declined»). `label` only ever holds a value for groups that
 *  have finer options (مختص / فرد) — chip text or free-typed «أخرى» text. */
export interface ProfessionSelection {
  group: ProfessionGroupKey | "declined" | null;
  label: string | null;
}

interface StepProfessionProps {
  value: ProfessionSelection;
  onChange: (value: ProfessionSelection) => void;
}

/**
 * «وش أقرب وصف لك؟» — 2×2 profession card grid (قانوني top-right in RTL,
 * then رائد أعمال، مختص، فرد) + a quieter full-width «أفضل عدم الإجابة» row.
 * Picking مختص or فرد reveals an optional chip strip (with a free-text
 * «أخرى») BELOW the grid — never inside the card, so the grid never reflows
 * under the cursor. Selection state lives in OnboardingDialog; saving happens
 * there on finish/dismiss.
 */
export function StepProfession({ value, onChange }: StepProfessionProps) {
  // «أخرى» stays open while typing even when the text momentarily matches
  // nothing/empty — closed again whenever a chip or another group is picked.
  const [otherOpen, setOtherOpen] = useState(false);

  const activeGroup = PROFESSION_GROUPS.find((g) => g.key === value.group);
  const chips = activeGroup?.options;
  const isOther =
    chips != null &&
    (otherOpen || (value.label != null && !chips.includes(value.label)));

  const pickGroup = (key: ProfessionGroupKey) => {
    if (value.group === key) return;
    setOtherOpen(false);
    onChange({ group: key, label: null });
  };

  const pickChip = (chip: string) => {
    setOtherOpen(false);
    // Tapping the selected chip clears it back to "group only".
    onChange({ group: value.group, label: value.label === chip ? null : chip });
  };

  return (
    <div className="space-y-4">
      <div>
        <h3 className="text-base font-semibold">{STEP_PROFESSION.heading}</h3>
        <p className="mt-1 text-sm leading-6 text-muted-foreground">
          {STEP_PROFESSION.intro}
        </p>
      </div>

      {/* 2×2 cards — RTL grid, so the first entry (قانوني) lands top-right */}
      <div className="grid grid-cols-2 gap-3">
        {PROFESSION_GROUPS.map((group) => {
          const Icon = group.icon;
          const selected = value.group === group.key;
          return (
            <button
              key={group.key}
              type="button"
              aria-pressed={selected}
              onClick={() => pickGroup(group.key)}
              className={cn(
                "flex flex-col items-start gap-2 rounded-xl border p-4 text-start transition-colors",
                selected
                  ? "border-primary/60 bg-primary/5 ring-1 ring-primary/60"
                  : "border-border bg-muted/30 hover:border-primary/30 hover:bg-muted/50",
              )}
            >
              <span
                className={cn(
                  "flex h-9 w-9 items-center justify-center rounded-lg transition-colors",
                  selected
                    ? "bg-primary/15 text-primary"
                    : "bg-muted text-muted-foreground",
                )}
              >
                <Icon className="h-5 w-5" />
              </span>
              <span className="text-sm font-semibold">{group.label}</span>
              <span className="text-[11px] leading-4 text-muted-foreground">
                {group.hint}
              </span>
            </button>
          );
        })}
      </div>

      {/* quieter full-width decline row — a recorded answer, not a skip link */}
      <button
        type="button"
        aria-pressed={value.group === "declined"}
        onClick={() => {
          setOtherOpen(false);
          onChange({ group: "declined", label: null });
        }}
        className={cn(
          "w-full rounded-xl border border-dashed px-4 py-2.5 text-sm transition-colors",
          value.group === "declined"
            ? "border-primary/60 bg-primary/5 text-primary"
            : "border-border text-muted-foreground hover:border-primary/30 hover:text-foreground",
        )}
      >
        {STEP_PROFESSION.declineLabel}
      </button>

      {/* finer segment — only مختص and فرد have options; always optional */}
      {chips && (
        <div className="space-y-2">
          <p className="text-xs text-muted-foreground">
            {STEP_PROFESSION.optionsHint}
          </p>
          <div className="flex flex-wrap gap-2">
            {chips.map((chip) => (
              <button
                key={chip}
                type="button"
                aria-pressed={!isOther && value.label === chip}
                onClick={() => pickChip(chip)}
                className={cn(
                  "rounded-full border px-3 py-1.5 text-sm transition-colors",
                  !isOther && value.label === chip
                    ? "border-primary/50 bg-primary/10 text-primary"
                    : "border-border bg-muted/30 text-muted-foreground hover:border-primary/30 hover:text-foreground",
                )}
              >
                {chip}
              </button>
            ))}
            <button
              type="button"
              aria-pressed={isOther}
              onClick={() => {
                setOtherOpen(true);
                onChange({ group: value.group, label: null });
              }}
              className={cn(
                "rounded-full border px-3 py-1.5 text-sm transition-colors",
                isOther
                  ? "border-primary/50 bg-primary/10 text-primary"
                  : "border-border bg-muted/30 text-muted-foreground hover:border-primary/30 hover:text-foreground",
              )}
            >
              {STEP_PROFESSION.otherChip}
            </button>
          </div>
          {isOther && (
            <input
              type="text"
              autoFocus
              maxLength={120}
              value={value.label ?? ""}
              onChange={(e) =>
                onChange({ group: value.group, label: e.target.value || null })
              }
              placeholder={STEP_PROFESSION.otherPlaceholder}
              className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm outline-none transition-colors placeholder:text-muted-foreground focus:border-primary/50 focus:ring-1 focus:ring-primary/30"
            />
          )}
        </div>
      )}
    </div>
  );
}
