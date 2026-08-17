import { create } from "zustand";
import { preferencesApi } from "@/lib/api";
import type { UserPreferencesData } from "@/types";
import {
  EDU_SYLLABUS,
  type EduLesson,
  type EduLessonId,
} from "@/components/edu/edu-syllabus";
import { useOnboardingStore } from "@/stores/onboarding-store";
import { useTourStore } from "@/stores/tour-store";

/**
 * «سلسلة تعلّم ريحان» — the engine. Design: `.claude/plans/edu_series.md`.
 *
 * One lesson every `EDU_CADENCE` user messages, in syllabus order, once each.
 * Call sites are dumb: `use-chat.ts` fires `bumpTurn()` on `done` and nothing
 * else. Every gate lives here.
 */

/**
 * Messages per lesson. ONE constant — retuning it retimes every future lesson,
 * and because the milestone gate compares against how many lessons the user has
 * actually SEEN (not a stored schedule), changing it never re-teaches or skips a
 * lesson for someone already mid-course.
 */
export const EDU_CADENCE = 4;

/** At most one lesson per browser session… */
const SESSION_CAP = 1;
/** …and at most one per day. */
const DAY_MS = 24 * 60 * 60 * 1000;
/**
 * Shown-and-ignored this many times ⇒ auto-mark seen. A lesson the user keeps
 * walking past has been answered; continuing to offer it is nagging.
 */
const MAX_IMPRESSIONS = 3;

const IMPRESSIONS_KEY = "rayhan.edu.impressions";

export type EduDismissReason = "got_it" | "action" | "learn_more" | "close";

// ---------------------------------------------------------------------------
// Impression counts — client-only, and deliberately NOT in preferences.
// They exist to bound a nag, not to be authoritative; a user on a second device
// starting from zero impressions is harmless, whereas a PATCH per render is not.
// ---------------------------------------------------------------------------

function readImpressions(): Record<string, number> {
  if (typeof window === "undefined") return {};
  try {
    const raw = window.localStorage.getItem(IMPRESSIONS_KEY);
    if (!raw) return {};
    const parsed: unknown = JSON.parse(raw);
    return parsed && typeof parsed === "object"
      ? (parsed as Record<string, number>)
      : {};
  } catch {
    // Private mode / quota / corrupt JSON — an absent counter just means the
    // impression cap is looser, never that a lesson breaks.
    return {};
  }
}

function writeImpressions(counts: Record<string, number>): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(IMPRESSIONS_KEY, JSON.stringify(counts));
  } catch {
    // Swallow — see above.
  }
}

// ---------------------------------------------------------------------------

/** How many syllabus lessons this user has already been taught. Counts only
 *  ids still IN the syllabus, so a stale `edu_*` key left behind by a removed
 *  lesson cannot inflate the count and stall the series. */
function countSeen(seen: Record<string, boolean>): number {
  return EDU_SYLLABUS.filter((lesson) => seen[lesson.id]).length;
}

/**
 * The next lesson owed: the FIRST entry in syllabus order that has not been
 * seen. Deliberately a search rather than a stored index — an index would
 * mis-target the moment anyone inserts a lesson mid-syllabus, silently skipping
 * it for every existing user.
 */
function findNext(seen: Record<string, boolean>): EduLesson | null {
  return EDU_SYLLABUS.find((lesson) => !seen[lesson.id]) ?? null;
}

/** Any open Radix dialog/sheet, including the full-viewport mobile workspace
 *  overlay (which sets `role="dialog"`). A teaching card must never land on
 *  top of a surface the user deliberately opened. */
function aModalIsOpen(): boolean {
  if (typeof document === "undefined") return false;
  return document.querySelector('[role="dialog"]') !== null;
}

interface EduState {
  /** False until preferences have been read once. Nothing fires before this. */
  hydrated: boolean;
  /** Lifetime user messages sent (mirrors `edu_turns`). */
  turns: number;
  seen: Record<string, boolean>;
  lastShownAt: number | null;
  activeLesson: EduLessonId | null;
  shownThisSession: EduLessonId[];

  /**
   * Seed from the `/preferences` payload. Pass `null` for a FAILED read —
   * that fails CLOSED (everything marked seen, series silent) exactly like
   * `onboardingSeen`. Teaching off a failed hydrate would re-nag users who
   * finished the whole syllabus months ago.
   */
  hydrateFrom: (prefs: UserPreferencesData | null) => void;
  /** One completed answer = one turn. Call from the `done` SSE event. */
  bumpTurn: () => void;
  /** Run the gate chain and deliver if everything passes. */
  maybeDeliver: () => void;
  /**
   * Open a lesson on demand from a manual entry point (the settings menu).
   *
   * Bypasses the cadence, session and day gates — the user explicitly asked for
   * this one, and making them wait for a milestone to read a card they just
   * clicked would be absurd. Still honours the single-slot rule, and still
   * refuses while a modal owns the screen (the card sits at z-40, under every
   * dialog, so showing it there would render it invisible).
   *
   * Dismissing it marks it seen like any other dismissal — so a lesson read
   * from the menu is not taught again by the series.
   */
  showLesson: (id: EduLessonId) => void;
  dismiss: (id: EduLessonId, reason: EduDismissReason) => void;
  reset: () => void;
}

const INITIAL = {
  hydrated: false,
  turns: 0,
  seen: {} as Record<string, boolean>,
  lastShownAt: null as number | null,
  activeLesson: null as EduLessonId | null,
  shownThisSession: [] as EduLessonId[],
};

/** Fire-and-forget preference write. No await, no rollback — the contract every
 *  other dismissal flag uses. A lost write costs one repeated lesson, which is
 *  strictly better than blocking the UI on a PATCH. */
function persist(patch: UserPreferencesData): void {
  void preferencesApi.update(patch).catch(() => {
    /* swallow — see above */
  });
}

export const useEduStore = create<EduState>((set, get) => ({
  ...INITIAL,

  reset: () => set({ ...INITIAL }),

  hydrateFrom: (prefs) => {
    if (!prefs) {
      // Fail closed: every lesson marked seen ⇒ `findNext` returns null ⇒ the
      // series is silent for this session.
      const allSeen: Record<string, boolean> = {};
      for (const lesson of EDU_SYLLABUS) allSeen[lesson.id] = true;
      set({ ...INITIAL, hydrated: true, seen: allSeen });
      return;
    }

    const seen: Record<string, boolean> = {};
    for (const lesson of EDU_SYLLABUS) {
      seen[lesson.id] = prefs[`edu_${lesson.id}`] === true;
    }

    const rawTurns = prefs.edu_turns;
    const turns =
      typeof rawTurns === "number" && Number.isFinite(rawTurns)
        ? Math.max(0, Math.floor(rawTurns))
        : 0;

    const rawLast = prefs.edu_last_shown_at;
    let lastShownAt: number | null = null;
    if (typeof rawLast === "string") {
      const parsed = Date.parse(rawLast);
      if (!Number.isNaN(parsed)) lastShownAt = parsed;
    }

    set({ hydrated: true, turns, seen, lastShownAt });
  },

  bumpTurn: () => {
    const { hydrated, turns } = get();
    // Not hydrated ⇒ we do not know the real baseline, and writing `1` would
    // clobber a stored count with a wrong one. Skipping is the safe direction:
    // the lesson arrives one turn later, nothing is lost.
    if (!hydrated) return;

    const next = turns + 1;
    set({ turns: next });
    persist({ edu_turns: next });
    get().maybeDeliver();
  },

  maybeDeliver: () => {
    const state = get();

    // 1 — hydrated
    if (!state.hydrated) return;

    // 2 — something left to teach
    const lesson = findNext(state.seen);
    if (!lesson) return;

    // 3 — THE MILESTONE RULE. Note `>` against the seen count, NOT
    //     `turns % EDU_CADENCE === 0`. An exact-modulo test would silently
    //     delete a chapter every time gates 4–8 blocked one; this formulation
    //     means a lesson owed at turn 4 is still owed at turn 5, 6, 20 —
    //     whenever the user is next eligible. The syllabus waits.
    if (Math.floor(state.turns / EDU_CADENCE) <= countSeen(state.seen)) return;

    // 4 — one at a time
    if (state.activeLesson !== null) return;

    // 5 — never stack on the onboarding dialog or «جولة المخرجات»
    if (useOnboardingStore.getState().isOpen) return;
    if (useTourStore.getState().isOpen) return;

    // 6 — nor on any other open dialog/sheet/overlay
    if (aModalIsOpen()) return;

    // 7 — one lesson per session
    if (state.shownThisSession.length >= SESSION_CAP) return;

    // 8 — one lesson per day
    if (state.lastShownAt !== null && Date.now() - state.lastShownAt < DAY_MS) {
      return;
    }

    // 9 — impression cap. Three ignored shows ⇒ answer it on the user's behalf.
    //     Marking it seen also advances `countSeen`, so the lesson after it
    //     waits for the NEXT milestone rather than arriving immediately — a
    //     user who ignored three showings has not earned a burst.
    const impressions = readImpressions();
    if ((impressions[lesson.id] ?? 0) >= MAX_IMPRESSIONS) {
      set((s) => ({ seen: { ...s.seen, [lesson.id]: true } }));
      persist({ [`edu_${lesson.id}`]: true });
      return;
    }

    const show = () => {
      // Re-check the volatile gates at fire time — `delayMs` means the world
      // may have changed since the chain passed (a dialog opened, another
      // lesson won the slot).
      const now = get();
      if (now.activeLesson !== null) return;
      if (now.shownThisSession.length >= SESSION_CAP) return;
      if (useOnboardingStore.getState().isOpen) return;
      if (useTourStore.getState().isOpen) return;
      if (aModalIsOpen()) return;

      const counts = readImpressions();
      counts[lesson.id] = (counts[lesson.id] ?? 0) + 1;
      writeImpressions(counts);

      set((s) => ({
        activeLesson: lesson.id,
        shownThisSession: [...s.shownThisSession, lesson.id],
      }));
    };

    if (lesson.delayMs && typeof window !== "undefined") {
      window.setTimeout(show, lesson.delayMs);
    } else {
      show();
    }
  },

  showLesson: (id) => {
    if (!EDU_SYLLABUS.some((lesson) => lesson.id === id)) return;
    // NO `aModalIsOpen()` CHECK HERE — deliberately, and it is not an oversight.
    //
    // Radix `PopoverContent` renders with `role="dialog"`, so the settings
    // popover this is invoked FROM trips that test. Worse, the caller's
    // `closeMenus()` is a React state update, so the popover is still in the
    // DOM when this runs synchronously right after — the check saw the menu
    // that summoned the lesson and refused every single time. Verified in the
    // browser 2026-08-16: popover closed, card never appeared.
    //
    // The gate exists to stop the AUTOMATIC series from firing a z-40 card
    // underneath a surface the user opened. A manual click is the opposite
    // case: it IS the user's intent, and the menu it came from is already
    // closing itself.
    set({ activeLesson: id });
  },

  dismiss: (id, _reason) => {
    // EVERY reason marks the lesson seen — «فهمت», the action button, the
    // learn-more link, the X. A dismissal is a dismissal; re-offering a lesson
    // because the user left via the "wrong" exit is nagging.
    //
    // A bare RENDER does not mark seen (a reload mid-card may show it again),
    // which is what the impression cap above is there to bound.
    const now = Date.now();
    set((s) => ({
      seen: { ...s.seen, [id]: true },
      activeLesson: null,
      lastShownAt: now,
    }));
    // BOTH keys in ONE patch, both flat — merge-safe by construction.
    persist({
      [`edu_${id}`]: true,
      edu_last_shown_at: new Date(now).toISOString(),
    });
  },
}));
