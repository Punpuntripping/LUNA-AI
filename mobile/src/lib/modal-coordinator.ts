import { useEffect, useSyncExternalStore } from "react";

// Who else has a RN Modal up (or just took one down)? Android shows each RN
// Modal as its own Dialog window, and iOS refuses to present a modal while
// another is dismissing — a sheet that presents itself (the ArtifactSheet
// auto-open) waits here until the other modals are gone AND their dismiss
// animation has had time to finish.
//
// Full-screen sheets (the ArtifactSheet) are tracked APART from `open`: the
// actions inside them (export → share sheet) wait on `whenSettled`, which
// would never fire if the sheet counted itself. Only popups that present
// themselves unasked («رسائل ريحان») wait for them too — `whenClear`.

/** Long enough for a `slide` Modal dismissal on both platforms (~300ms). */
export const MODAL_SETTLE_MS = 400;
/** Re-check cadence while another modal is still open. */
const POLL_MS = 150;

interface CoordinatorState {
  open: number;
  lastDismissAt: number;
  sheets: number;
  lastSheetDismissAt: number;
}

export interface ModalCoordinator {
  /** A modal became visible. Returns its release (call once, on dismiss). */
  acquire: () => () => void;
  /** 0 when no modal is open and the last dismissal has settled; else ms to wait. */
  msUntilSettled: () => number;
  /** Runs `cb` once no other modal is open/dismissing. Returns a cancel. */
  whenSettled: (cb: () => void) => () => void;
  /** A full-screen sheet wants to be / is on screen. Returns its release. */
  acquireSheet: () => () => void;
  /** `whenSettled` that also waits for full-screen sheets (unasked popups). */
  whenClear: (cb: () => void) => () => void;
  /** Number of modals currently open. */
  openCount: () => number;
  /** Notified whenever `openCount` changes. Returns an unsubscribe. */
  subscribe: (listener: () => void) => () => void;
}

export function createModalCoordinator(
  now: () => number = Date.now,
  timers: { set: (fn: () => void, ms: number) => unknown; clear: (h: unknown) => void } = {
    set: (fn, ms) => setTimeout(fn, ms),
    clear: (h) => clearTimeout(h as ReturnType<typeof setTimeout>),
  },
): ModalCoordinator {
  const state: CoordinatorState = {
    open: 0,
    lastDismissAt: Number.NEGATIVE_INFINITY,
    sheets: 0,
    lastSheetDismissAt: Number.NEGATIVE_INFINITY,
  };
  const listeners = new Set<() => void>();
  const notify = () => listeners.forEach((l) => l());

  const msUntilSettled = () => {
    if (state.open > 0) return POLL_MS;
    return Math.max(0, state.lastDismissAt + MODAL_SETTLE_MS - now());
  };

  const msUntilClear = () => {
    if (state.sheets > 0) return POLL_MS;
    return Math.max(msUntilSettled(), state.lastSheetDismissAt + MODAL_SETTLE_MS - now());
  };

  const waitFor = (ms: () => number, cb: () => void) => {
    let handle: unknown = null;
    let cancelled = false;
    const check = () => {
      if (cancelled) return;
      const wait = ms();
      if (wait <= 0) {
        handle = null;
        cb();
        return;
      }
      handle = timers.set(check, wait);
    };
    check();
    return () => {
      cancelled = true;
      if (handle !== null) timers.clear(handle);
    };
  };

  return {
    acquire: () => {
      state.open += 1;
      notify();
      let released = false;
      return () => {
        if (released) return;
        released = true;
        state.open = Math.max(0, state.open - 1);
        state.lastDismissAt = now();
        notify();
      };
    },
    openCount: () => state.open,
    subscribe: (listener) => {
      listeners.add(listener);
      return () => {
        listeners.delete(listener);
      };
    },
    msUntilSettled,
    whenSettled: (cb) => waitFor(msUntilSettled, cb),
    acquireSheet: () => {
      state.sheets += 1;
      let released = false;
      return () => {
        if (released) return;
        released = true;
        state.sheets = Math.max(0, state.sheets - 1);
        state.lastSheetDismissAt = now();
      };
    },
    whenClear: (cb) => waitFor(msUntilClear, cb),
  };
}

export const modalCoordinator = createModalCoordinator();

/** Registers a Modal with the coordinator while `visible` (and stamps its
 *  dismissal when it hides or unmounts). */
export function useModalPresence(visible: boolean): void {
  useEffect(() => {
    if (!visible) return undefined;
    return modalCoordinator.acquire();
  }, [visible]);
}

/** Registers a full-screen sheet while it is wanted on screen (including the
 *  wait before it presents), so unasked popups never stack on it. */
export function useSheetPresence(active: boolean): void {
  useEffect(() => {
    if (!active) return undefined;
    return modalCoordinator.acquireSheet();
  }, [active]);
}

/** True while any registered Modal (shared Sheet, CopyMenu, …) is open. */
export function useAnyModalOpen(): boolean {
  return useSyncExternalStore(
    modalCoordinator.subscribe,
    () => modalCoordinator.openCount() > 0,
  );
}
