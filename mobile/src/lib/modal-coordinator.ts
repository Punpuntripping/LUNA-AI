import { useEffect, useSyncExternalStore } from "react";

// Who else has a RN Modal up (or just took one down)? Android shows each RN
// Modal as its own Dialog window, and iOS refuses to present a modal while
// another is dismissing — a sheet that presents itself (the ArtifactSheet
// auto-open) waits here until the other modals are gone AND their dismiss
// animation has had time to finish.

/** Long enough for a `slide` Modal dismissal on both platforms (~300ms). */
export const MODAL_SETTLE_MS = 400;
/** Re-check cadence while another modal is still open. */
const POLL_MS = 150;

interface CoordinatorState {
  open: number;
  lastDismissAt: number;
}

export interface ModalCoordinator {
  /** A modal became visible. Returns its release (call once, on dismiss). */
  acquire: () => () => void;
  /** 0 when no modal is open and the last dismissal has settled; else ms to wait. */
  msUntilSettled: () => number;
  /** Runs `cb` once no other modal is open/dismissing. Returns a cancel. */
  whenSettled: (cb: () => void) => () => void;
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
  const state: CoordinatorState = { open: 0, lastDismissAt: Number.NEGATIVE_INFINITY };
  const listeners = new Set<() => void>();
  const notify = () => listeners.forEach((l) => l());

  const msUntilSettled = () => {
    if (state.open > 0) return POLL_MS;
    return Math.max(0, state.lastDismissAt + MODAL_SETTLE_MS - now());
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
    whenSettled: (cb) => {
      let handle: unknown = null;
      let cancelled = false;
      const check = () => {
        if (cancelled) return;
        const wait = msUntilSettled();
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
    },
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

/** True while any registered Modal (shared Sheet, CopyMenu, …) is open. */
export function useAnyModalOpen(): boolean {
  return useSyncExternalStore(
    modalCoordinator.subscribe,
    () => modalCoordinator.openCount() > 0,
  );
}
