import { create } from "zustand";
import { track } from "@/lib/analytics/client";
import { detectInstallPlatform, isStandalone } from "@/lib/install-app";
import {
  onAppInstalled,
  readInstallState,
  writeInstallState,
} from "@/components/install/install-device-state";
import { useChatStore } from "@/stores/chat-store";
import { useEduStore } from "@/stores/edu-store";
import { useOnboardingStore } from "@/stores/onboarding-store";
import { usePromoStore } from "@/stores/promo-store";
import { useTourStore } from "@/stores/tour-store";

/**
 * «ثبّت ريحان على شاشتك الرئيسية» — the chat nudge engine.
 * Design: `.claude/plans/install_app_nudge.md` §B.
 *
 * Same shape as `edu-store`: `use-chat.ts` calls `noteTurn()` on `done` and
 * nothing else; every gate lives here and the card is a dumb renderer. The
 * per-device localStorage record and the install measurement (`app_installed`,
 * `standalone_session`) live in `components/install/install-device-state.ts`,
 * which importing this store loads.
 *
 * ⚠ `edu-store` imports THIS module too (its gate 5b). The cycle is safe only
 * because neither side touches the other at module load — keep it that way.
 */

const DAY_MS = 24 * 60 * 60 * 1000;
/** ✕ (or «كيف؟» without a confirmed install) ⇒ quiet for two weeks. */
const SNOOZE_MS = 14 * DAY_MS;
/** Three ignored shows ⇒ stop forever on this device. */
const MAX_IMPRESSIONS = 3;
/** The value moment — never on first load, only after real answers. */
const MIN_TURNS = 2;
/** Let the finished answer land before a card slides in beside it. */
const SHOW_DELAY_MS = 1500;

/** Same probe as `edu-store`: any open Radix dialog/sheet or the mobile
 *  workspace overlay. A z-40 card must never land under one. */
function aModalIsOpen(): boolean {
  if (typeof document === "undefined") return false;
  return document.querySelector('[role="dialog"]') !== null;
}

/** The volatile gates — checked when the chain runs AND again at fire time. */
function competingSurfaceOpen(): boolean {
  if (useEduStore.getState().activeLesson !== null) return true;
  if (useOnboardingStore.getState().isOpen) return true;
  if (useTourStore.getState().isOpen) return true;
  if (usePromoStore.getState().isOpen) return true;
  if (useChatStore.getState().isStreaming) return true;
  return aModalIsOpen();
}

interface InstallNudgeState {
  /** Completed answers in THIS page session. Deliberately not persisted. */
  turns: number;
  shownThisSession: boolean;
  /** The card is on screen. `edu-store` stands down while this is true. */
  isOpen: boolean;

  /** One completed answer. Call from the `done` SSE event. */
  noteTurn: () => void;
  maybeShow: () => void;
  /** ✕ — snooze 14 days. */
  dismiss: () => void;
  /** «كيف؟» — close the card and snooze; `appinstalled` upgrades it to done. */
  how: () => void;
  /** A new stream started — hide, keeping the impression already counted. */
  hide: () => void;
}

export const useInstallNudgeStore = create<InstallNudgeState>((set, get) => ({
  turns: 0,
  shownThisSession: false,
  isOpen: false,

  noteTurn: () => {
    set((s) => ({ turns: s.turns + 1 }));
    get().maybeShow();
  },

  maybeShow: () => {
    const state = get();

    // 1–2 — a phone/tablet browser, not already the installed app
    if (detectInstallPlatform() === "other") return;
    if (isStandalone()) return;

    // 3–4 — device history: installed, capped, or snoozed
    const device = readInstallState();
    if (device.done) return;
    if (device.impressions >= MAX_IMPRESSIONS) return;
    if (device.snoozedUntil !== null && Date.now() < device.snoozedUntil) return;

    // 5 — the value moment
    if (state.turns < MIN_TURNS) return;

    // 6 — once per session
    if (state.shownThisSession || state.isOpen) return;

    // 7 — one teaching surface per session: a lesson shown (or showing) this
    //     session owns it, and so does anything the user has open.
    const edu = useEduStore.getState();
    if (edu.shownThisSession.length > 0) return;
    if (competingSurfaceOpen()) return;

    // 8 — the edu day rule, loosely: a lesson within 24h means this user was
    //     taught something recently enough.
    if (edu.lastShownAt !== null && Date.now() - edu.lastShownAt < DAY_MS) return;

    // Claim the session slot now so a second `done` inside the delay cannot
    // schedule a duplicate.
    set({ shownThisSession: true });

    const show = () => {
      // Re-check at fire time — a lesson, dialog or new stream may have won
      // the screen during the delay. A lost race forfeits this session.
      if (competingSurfaceOpen()) return;
      if (useEduStore.getState().shownThisSession.length > 0) return;
      writeInstallState({ impressions: readInstallState().impressions + 1 });
      set({ isOpen: true });
      track("install_nudge_shown", { platform: detectInstallPlatform() });
    };

    if (typeof window !== "undefined") {
      window.setTimeout(show, SHOW_DELAY_MS);
    }
  },

  dismiss: () => {
    writeInstallState({ snoozedUntil: Date.now() + SNOOZE_MS });
    set({ isOpen: false });
    track("install_nudge_dismissed");
  },

  how: () => {
    writeInstallState({ snoozedUntil: Date.now() + SNOOZE_MS });
    set({ isOpen: false });
    track("install_nudge_how", { platform: detectInstallPlatform() });
  },

  hide: () => {
    if (get().isOpen) set({ isOpen: false });
  },
}));

// Installed from anywhere (our button or Chrome's own menu) ⇒ drop the card.
onAppInstalled(() => useInstallNudgeStore.getState().hide());
