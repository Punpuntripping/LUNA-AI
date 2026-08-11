"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useParams } from "next/navigation";
import { useAuthStore } from "@/stores/auth-store";
import { useChatStore } from "@/stores/chat-store";
import { useOnboardingStore } from "@/stores/onboarding-store";
import { usePreferencesStore } from "@/stores/preferences-store";
import { useTourStore } from "@/stores/tour-store";
import { useIsMobile } from "@/hooks/use-media-query";
import { useIsDemoConversation } from "@/hooks/use-demo-conversation";
import {
  clickTourAnchor,
  dismissSourceDialog,
  queryTourAnchor,
  useTourAnchor,
  useTourDomBeats,
} from "@/hooks/use-tour-anchor";
import { TourCard } from "./TourCard";
import { TourSpotlight } from "./TourSpotlight";
import {
  TOUR_MISSING_ANCHOR_MS,
  TOUR_STALL_MS,
  TOUR_STEPS,
  TOUR_STEP_COUNT,
  type TourAnchorId,
  type TourCondition,
} from "./tour-content";

// ---------------------------------------------------------------------------
// Soft bridge to `preferences-store`
//
// The `tour_workspace_seen` flag (plan §4.3) lives on a store this file does
// not own, so it is written structurally rather than through a typed selector,
// which would hard-couple the two files.
//
// It is WRITTEN ONLY — nothing reads it back. The tour re-runs on every entry
// to «محادثة تجريبية» (see `useTourAutoStart`), so there is no seen-gate left to
// read. The write is kept because it records whether a user ever completed the
// tour, and because restoring the once-per-account behaviour is then a
// one-condition change rather than a re-plumb.
// ---------------------------------------------------------------------------

/** Candidate action names, in preference order — whichever exists is called. */
const TOUR_SEEN_ACTIONS = ["markTourWorkspaceSeen", "markTourSeen"] as const;

function markTourSeen(): void {
  const state = usePreferencesStore.getState() as unknown as Record<string, unknown>;
  for (const name of TOUR_SEEN_ACTIONS) {
    const action = state[name];
    if (typeof action === "function") {
      void (action as () => unknown)();
      return;
    }
  }
}

// ---------------------------------------------------------------------------

const NO_ANCHORS: readonly TourAnchorId[] = [];

/** How many times the auto-start retries while waiting for Act 1's anchor. */
const AUTO_START_ATTEMPTS = 14;
const AUTO_START_INTERVAL_MS = 700;

interface BeatSnapshot {
  openItemId: string | null;
  focusedReferenceN: number | null;
  paneOpen: boolean;
  sourceDialogOpen: boolean;
  crossrefsExpanded: boolean;
}

function isSatisfied(condition: TourCondition, snapshot: BeatSnapshot): boolean {
  if (condition.kind === "store") {
    switch (condition.beat) {
      case "wi-open":
        return snapshot.openItemId !== null;
      case "wi-closed":
        return snapshot.openItemId === null;
      case "reference-3":
        return snapshot.focusedReferenceN === 3;
      case "pane-closed":
        return !snapshot.paneOpen;
    }
  }
  switch (condition.beat) {
    case "source-dialog-open":
      return snapshot.sourceDialogOpen;
    case "crossrefs-expanded":
      return snapshot.crossrefsExpanded;
  }
}

/**
 * Entry trigger: the tour runs EVERY time «محادثة تجريبية» is opened, not once
 * per account (owner decision 2026-08-11). That conversation exists only to be
 * toured, so arriving there IS the request to see it — and a user who wants to
 * re-watch shouldn't have to hunt through the settings popover for it.
 *
 * Fires when: authenticated · preferences hydrated · «اتعرف على ريحان» is NOT on
 * screen (never both at once) · this is the demo conversation · and Act 1's
 * anchor has actually rendered. The last two are separate tests on purpose: the
 * first says the script matches this screen, the second says the screen has
 * finished painting it.
 *
 * RE-ARMING IS KEYED ON THE CONVERSATION, NOT ON A BOOLEAN. `startedForRef`
 * holds the conversation the tour was last auto-started for, so:
 *   - skip/finish while STAYING on the demo → stays closed (the ref still
 *     matches, so no effect re-run can reopen it). Dismissal must mean dismissed.
 *   - navigate away and come back → the ref no longer matches, so it runs again.
 * A plain boolean could only do one of those two.
 *
 * `tour_workspace_seen` is deliberately NOT consulted here any more. It is still
 * written on finish/skip — it costs nothing, records whether a user has ever
 * completed the tour, and keeps this decision one line from being reverted.
 * Any other entry point (the sidebar settings item) just calls
 * `useTourStore.getState().open()`.
 */
function useTourAutoStart(isOpen: boolean, conversationId?: string): void {
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated);
  const isHydrated = usePreferencesStore((s) => s.isHydrated);
  const onboardingOpen = useOnboardingStore((s) => s.isOpen);
  const isDemo = useIsDemoConversation(conversationId);
  const startedForRef = useRef<string | null>(null);

  useEffect(() => {
    // Left the demo (or landed on an ordinary chat): forget the last run so
    // coming back re-arms. Done here rather than in a cleanup so a re-render
    // that merely toggles `isOpen` cannot clear it.
    if (!isDemo || !conversationId) {
      startedForRef.current = null;
      return;
    }
    if (isOpen || startedForRef.current === conversationId) return;
    if (!isAuthenticated || !isHydrated || onboardingOpen) return;

    const tryStart = (): boolean => {
      if (startedForRef.current === conversationId) return true;
      if (queryTourAnchor("artifact-chip") === null) return false;
      startedForRef.current = conversationId;
      useTourStore.getState().open();
      return true;
    };

    if (tryStart()) return;

    let attempts = 0;
    const intervalId = window.setInterval(() => {
      attempts += 1;
      if (tryStart() || attempts >= AUTO_START_ATTEMPTS) {
        window.clearInterval(intervalId);
      }
    }, AUTO_START_INTERVAL_MS);
    return () => window.clearInterval(intervalId);
  }, [isOpen, isAuthenticated, isHydrated, onboardingOpen, isDemo, conversationId]);
}

/**
 * «جولة المخرجات» — the coach-mark tour over the real UI (plan §5).
 *
 * Self-gating and prop-less: mount it once beside `<OnboardingDialog />` in
 * `ChatLayoutClient` and it renders nothing until `tour-store` opens it.
 *
 * Layering: the whole tour portals to `document.body` at `z-[80]` — above the
 * mobile workspace overlay (`z-[60]`) and above portalled Radix layers
 * (`z-[70]`), because Act 3 has to point at things inside the «عرض المصدر»
 * dialog. The root is `pointer-events-none` and only `TourCard` re-enables
 * pointer events, so the app underneath stays fully usable and the tour can
 * never swallow the click it is asking for.
 */
export function TourOverlay() {
  const isOpen = useTourStore((s) => s.isOpen);
  const params = useParams();
  const rawId = params?.id;
  useTourAutoStart(isOpen, typeof rawId === "string" ? rawId : undefined);
  if (!isOpen) return null;
  return <TourRunner />;
}

/**
 * Interop alias only — `TourOverlay` (named) is the canonical export and
 * matches every other component in this codebase. Kept so a `import
 * TourOverlay from "@/components/tour/TourOverlay"` in the mounting file also
 * resolves.
 */
export default TourOverlay;

/**
 * The engine. Mounted only while the tour is open, so none of its observers,
 * rAF loops or listeners exist for the 99.9% of the session that is not a
 * tour.
 */
function TourRunner() {
  const [mounted, setMounted] = useState(false);
  useEffect(() => setMounted(true), []);

  const params = useParams();
  const rawId = params?.id;
  const conversationId = typeof rawId === "string" ? rawId : undefined;

  const stepIndex = useTourStore((s) => s.stepIndex);
  const step = stepIndex < TOUR_STEP_COUNT ? TOUR_STEPS[stepIndex] : undefined;
  const isMobile = useIsMobile();
  const rootRef = useRef<HTMLDivElement | null>(null);

  // --- the four store beats (§5.3) -----------------------------------------
  // Read through selectors rather than DOM listeners: the click we care about
  // re-renders the very node that would carry the listener.
  const openItemId = useChatStore((s) =>
    conversationId
      ? s.workspaceByConversation[conversationId]?.openItemId ?? null
      : null,
  );
  const focusedReferenceN = useChatStore((s) =>
    conversationId
      ? s.workspaceByConversation[conversationId]?.focusedReferenceN ?? null
      : null,
  );
  const paneOpen = useChatStore((s) =>
    conversationId
      ? s.workspaceByConversation[conversationId]?.isOpen ?? false
      : false,
  );

  const finish = useCallback(() => {
    useTourStore.getState().close();
    markTourSeen();
  }, []);

  // Deterministic start state. Step 2 asks the user to open the WI; if the
  // pane were already open, that step's beat would be satisfied on arrival and
  // the tour would fast-forward past its own instruction. Runs once, on open.
  const didResetRef = useRef(false);
  useEffect(() => {
    if (didResetRef.current || !conversationId) return;
    didResetRef.current = true;
    useChatStore.getState().closeWorkspace(conversationId);
  }, [conversationId]);

  // Below `md` the chat is fully covered by the workspace overlay (§7.1), so
  // Act 1's anchors still measure to real — but invisible — rects. Refuse to
  // measure them; the card falls back to a centred panel.
  const anchorsUsable = step
    ? !(isMobile && paneOpen && step.stage === "chat")
    : false;
  const { rect, found, settled } = useTourAnchor(step?.anchors ?? NO_ANCHORS, {
    active: anchorsUsable,
  });

  const needsDomBeats =
    step?.advanceWhen?.some((condition) => condition.kind === "dom") ?? false;
  const beats = useTourDomBeats(needsDomBeats);

  // Step side effect: Act 3 ends inside the reveal dialog, and step 10's card
  // lives in the reference list UNDERNEATH it.
  useEffect(() => {
    if (step?.onEnter === "close-source-dialog") dismissSourceDialog();
  }, [step]);

  // --- advance --------------------------------------------------------------
  const satisfied =
    step?.advanceWhen?.some((condition) =>
      isSatisfied(condition, {
        openItemId,
        focusedReferenceN,
        paneOpen,
        sourceDialogOpen: beats.sourceDialogOpen,
        crossrefsExpanded: beats.crossrefsExpanded,
      }),
    ) ?? false;

  useEffect(() => {
    if (!step || !satisfied) return;
    useTourStore.getState().next(stepIndex);
  }, [step, satisfied, stepIndex]);

  // Nothing left to show (the script grew shorter, or the last «التالي» ran).
  useEffect(() => {
    if (!step) finish();
  }, [step, finish]);

  // --- stall guard (§5.3) ---------------------------------------------------
  const isClickStep = (step?.advanceWhen?.length ?? 0) > 0;
  const [showNext, setShowNext] = useState(!isClickStep);

  useEffect(() => {
    if (!step) return;
    const clickStep = (step.advanceWhen?.length ?? 0) > 0;
    setShowNext(!clickStep);
    if (!clickStep) return;
    const timerId = window.setTimeout(() => setShowNext(true), TOUR_STALL_MS);
    return () => window.clearTimeout(timerId);
  }, [step]);

  // Shorter fuse when there is nothing on screen to click at all.
  useEffect(() => {
    if (!step || showNext || found) return;
    const timerId = window.setTimeout(
      () => setShowNext(true),
      TOUR_MISSING_ANCHOR_MS,
    );
    return () => window.clearTimeout(timerId);
  }, [step, showNext, found]);

  const handleNext = useCallback(() => {
    if (!step) return;
    // The stall button does the thing it was waiting for, rather than just
    // skipping past it — otherwise the next step points at UI that never opened.
    if (step.fallbackClick && (step.advanceWhen?.length ?? 0) > 0) {
      clickTourAnchor(step.fallbackClick);
    }
    if (stepIndex + 1 >= TOUR_STEP_COUNT) {
      finish();
      return;
    }
    useTourStore.getState().next(stepIndex);
  }, [step, stepIndex, finish]);

  // Escape closes the tour — but only when no Radix dialog is open, since
  // Escape belongs to that dialog first. `isTrusted` keeps the synthetic
  // Escape `dismissSourceDialog` may dispatch from closing the tour with it.
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== "Escape" || !event.isTrusted) return;
      if (
        document.querySelector(
          "[role='dialog'][data-state='open']:not([data-tour-card])",
        )
      ) {
        return;
      }
      finish();
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [finish]);

  // Radix's modal dialog marks every sibling of its content `aria-hidden`,
  // which would silence the card for screen-reader users during Act 3. Strip
  // it off our own root (attribute-only, no subtree — the spotlight's own
  // aria-hidden is intentional).
  useEffect(() => {
    const node = rootRef.current;
    if (!node) return;
    const strip = () => {
      if (node.hasAttribute("aria-hidden")) node.removeAttribute("aria-hidden");
    };
    strip();
    const observer = new MutationObserver(strip);
    observer.observe(node, {
      attributes: true,
      attributeFilter: ["aria-hidden"],
    });
    return () => observer.disconnect();
  }, []);

  if (!mounted || !step) return null;

  return createPortal(
    <div
      ref={rootRef}
      data-tour-root=""
      // pointer-events-none is the whole safety story: the app keeps every one
      // of its own clicks, and only TourCard opts back in.
      className="pointer-events-none fixed inset-0 z-[80]"
    >
      {rect && <TourSpotlight rect={rect} />}
      <TourCard
        step={step}
        stepNumber={stepIndex + 1}
        totalSteps={TOUR_STEP_COUNT}
        rect={rect}
        settled={settled}
        isMobile={isMobile}
        isLast={stepIndex === TOUR_STEP_COUNT - 1}
        showNext={showNext}
        onNext={handleNext}
        onSkip={finish}
      />
    </div>,
    document.body,
  );
}
