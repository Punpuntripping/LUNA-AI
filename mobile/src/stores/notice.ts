import { create } from "zustand";

/** One-shot success notice handed from a screen to the one it goes back to
 *  (e.g. change-password → settings «تم تغيير كلمة المرور»). The receiver
 *  shows it and clears it when it loses focus. */
interface NoticeState {
  notice: string | null;
  show: (message: string) => void;
  clear: () => void;
}

export const useNotice = create<NoticeState>((set) => ({
  notice: null,
  show: (message) => set({ notice: message }),
  clear: () => set({ notice: null }),
}));
