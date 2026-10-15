import { create } from "zustand";

// Transient one-line confirmations («تم النسخ»). One at a time; a new toast
// replaces the current one. Rendered by <ToastHost/> in the signed-in layout.

const TOAST_MS = 1_800;

interface ToastState {
  message: string | null;
  seq: number;
  show: (message: string) => void;
  hide: () => void;
}

let timer: ReturnType<typeof setTimeout> | null = null;

export const useToast = create<ToastState>((set) => ({
  message: null,
  seq: 0,
  show: (message) => {
    if (timer) clearTimeout(timer);
    set((s) => ({ message, seq: s.seq + 1 }));
    timer = setTimeout(() => {
      timer = null;
      set({ message: null });
    }, TOAST_MS);
  },
  hide: () => {
    if (timer) clearTimeout(timer);
    timer = null;
    set({ message: null });
  },
}));

export function showToast(message: string): void {
  useToast.getState().show(message);
}
