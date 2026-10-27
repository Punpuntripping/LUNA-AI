import type { PreferencesBlob, ProfessionAnswer, ProfessionGroupKey, User } from "@contracts";

import { PROFESSION_GROUPS } from "./content";

// «وش أقرب وصف لك؟» — the rules of the web OnboardingDialog + StepProfession,
// pure (no native imports: unit-tested under node).

/** What the user has picked so far (web `ProfessionSelection`). `group: null`
 *  = untouched (an exit then records «declined» on a never-asked account).
 *  `label` only ever holds a value for مختص / فرد — chip text or «أخرى» text. */
export interface ProfessionSelection {
  group: ProfessionAnswer | null;
  label: string | null;
}

/** The step's state: the selection + whether «أخرى» was tapped (it stays open
 *  while typing even when the text is momentarily empty). */
export interface ProfessionStepState {
  selection: ProfessionSelection;
  otherOpen: boolean;
}

export type StoredProfession = Pick<User, "profession_group" | "profession_label">;

const ANSWERS: readonly ProfessionAnswer[] = ["legal", "entrepreneur", "specialist", "individual", "declined"];

export function isProfessionAnswer(value: unknown): value is ProfessionAnswer {
  return typeof value === "string" && (ANSWERS as readonly string[]).includes(value);
}

/** Stored users.profession_* → the starting selection, so a reopen («تغيير
 *  المهنة») doubles as "change my answer". "unknown" (degraded read) and null
 *  (never asked) both start untouched. */
export function initialStep(user: StoredProfession | null | undefined): ProfessionStepState {
  const group = user?.profession_group;
  if (isProfessionAnswer(group)) {
    return { selection: { group, label: user?.profession_label ?? null }, otherOpen: false };
  }
  return { selection: { group: null, label: null }, otherOpen: false };
}

// ---------------------------------------------------------------------------
// When it opens (web OnboardingDialog auto-open effect)
// ---------------------------------------------------------------------------

export type PrefsStatus = "pending" | "error" | "success";

export interface PrefsGate {
  /** The preferences read has settled (web `isHydrated`). */
  ready: boolean;
  /** Web `professionReaskDone`. Fail-closed: true until a SUCCESSFUL read says
   *  otherwise, and true after a failed one — never re-ask off a failed read. */
  reaskDone: boolean;
}

export function prefsGate(status: PrefsStatus, prefs: PreferencesBlob | undefined): PrefsGate {
  if (status === "pending") return { ready: false, reaskDone: true };
  if (status === "error") return { ready: true, reaskDone: true };
  return { ready: true, reaskDone: prefs?.profession_reask_done === true };
}

export interface AutoOpenInput {
  prefsReady: boolean;
  /** `user.profession_group` — undefined/"unknown" never prompt. */
  professionGroup: string | null | undefined;
  reaskDone: boolean;
  /** The question was answered or dismissed earlier in this session. */
  resolvedThisSession: boolean;
}

/**
 * Opens by itself when `profession_group` is EXACTLY null (never asked), or
 * ONCE more for a stored «declined» while `profession_reask_done` is not set —
 * only after the preferences read settled (web). Never twice in one session:
 * the web's optimistic user object keeps it shut after an exit even when the
 * save failed, and a later /auth/me must not undo that here.
 */
export function shouldAutoOpen(i: AutoOpenInput): boolean {
  if (!i.prefsReady || i.resolvedThisSession) return false;
  return i.professionGroup === null || (i.professionGroup === "declined" && !i.reaskDone);
}

/**
 * Popups that present themselves («رسائل ريحان») stand down while this is
 * true: the question is on screen, or it may still open once the preferences
 * read settles (web: the first-run dialogs claim the screen first).
 */
export function professionHold(i: AutoOpenInput & { isOpen: boolean }): boolean {
  if (i.isOpen) return true;
  if (i.resolvedThisSession) return false;
  if (!i.prefsReady) return i.professionGroup === null || i.professionGroup === "declined";
  return shouldAutoOpen(i);
}

// ---------------------------------------------------------------------------
// Every exit saves (web OnboardingDialog resolveProfession / finish)
// ---------------------------------------------------------------------------

export interface ProfessionSave {
  group: ProfessionAnswer;
  label: string | null;
}

/** The answer to persist on exit, or null when nothing changed. A wholly
 *  untouched step on a never-asked account records «declined». */
export function professionToSave(
  selection: ProfessionSelection,
  user: StoredProfession | null | undefined,
): ProfessionSave | null {
  const stored = user?.profession_group;
  const storedLabel = user?.profession_label ?? null;
  if (selection.group) {
    const label = selection.label?.trim() || null;
    if (selection.group !== stored || label !== storedLabel) return { group: selection.group, label };
    return null;
  }
  if (stored === null) return { group: "declined", label: null };
  return null;
}

export interface FinishDeps {
  selection: ProfessionSelection;
  user: StoredProfession | null | undefined;
  /** Optimistic local update + PATCH /auth/profession; true only if saved. */
  save: (group: ProfessionAnswer, label: string | null) => Promise<boolean>;
  /** PATCH /preferences {profession_reask_done: true} (once). */
  markReaskDone: () => Promise<void>;
  /** Closes the sheet and stops the auto-open for this session. */
  close: () => void;
}

/**
 * Every exit — «حفظ», the backdrop, Android back. The ORDER is the web's: the
 * save and the re-ask flag both update local state synchronously, so by the
 * time the auto-open re-evaluates it already sees the new answer. The re-ask
 * is closed on EVERY exit, so a new user who declines is not asked again.
 * Returns the save in flight, or null when nothing needed saving.
 */
export function finishProfession(d: FinishDeps): Promise<boolean> | null {
  const toSave = professionToSave(d.selection, d.user);
  const saving = toSave ? d.save(toSave.group, toSave.label) : null;
  void d.markReaskDone();
  d.close();
  return saving;
}

// ---------------------------------------------------------------------------
// The step's taps (web StepProfession)
// ---------------------------------------------------------------------------

/** The chips under the grid — only مختص and فرد have them. */
export function chipsFor(group: ProfessionSelection["group"]): readonly string[] | undefined {
  return PROFESSION_GROUPS.find((g) => g.key === group)?.options;
}

/** «أخرى» is active when tapped, or when the stored label is free text. */
export function isOtherActive(state: ProfessionStepState): boolean {
  const chips = chipsFor(state.selection.group);
  const { label } = state.selection;
  return chips != null && (state.otherOpen || (label != null && !chips.includes(label)));
}

export function pickGroup(state: ProfessionStepState, key: ProfessionGroupKey): ProfessionStepState {
  if (state.selection.group === key) return state;
  return { selection: { group: key, label: null }, otherOpen: false };
}

/** «أفضل عدم الإجابة» — a recorded answer, not a skip link. */
export function pickDecline(): ProfessionStepState {
  return { selection: { group: "declined", label: null }, otherOpen: false };
}

/** Tapping the selected chip clears it back to "group only". */
export function pickChip(state: ProfessionStepState, chip: string): ProfessionStepState {
  const label = state.selection.label === chip ? null : chip;
  return { selection: { group: state.selection.group, label }, otherOpen: false };
}

export function pickOther(state: ProfessionStepState): ProfessionStepState {
  return { selection: { group: state.selection.group, label: null }, otherOpen: true };
}

export function typeOther(state: ProfessionStepState, text: string): ProfessionStepState {
  return { selection: { group: state.selection.group, label: text || null }, otherOpen: state.otherOpen };
}
