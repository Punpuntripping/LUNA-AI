import { create } from "zustand";

// Composer text per conversation (session only — never persisted: it can hold
// client confidential text). Keyed by conversation id; `c/new` uses NEW_CHAT.
// Lets a refusal restore the text even after `c/new` replaced itself with
// `c/{id}`, and keeps an unsent draft when the user steps back to the list.

export const NEW_CHAT_DRAFT = "__new__";

interface DraftsState {
  drafts: Record<string, string>;
  setDraft: (key: string, text: string) => void;
  /** Put text back only if the user has not typed something new since. */
  restoreDraft: (key: string, text: string) => void;
  clear: () => void;
}

export const useComposerDrafts = create<DraftsState>((set) => ({
  drafts: {},
  setDraft: (key, text) =>
    set((s) => {
      if ((s.drafts[key] ?? "") === text) return s;
      const drafts = { ...s.drafts };
      if (text) drafts[key] = text;
      else delete drafts[key];
      return { drafts };
    }),
  restoreDraft: (key, text) =>
    set((s) => ((s.drafts[key] ?? "").trim() ? s : { drafts: { ...s.drafts, [key]: text } })),
  clear: () => set({ drafts: {} }),
}));

export function useDraft(key: string): string {
  return useComposerDrafts((s) => s.drafts[key] ?? "");
}
