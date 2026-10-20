import type { PendingTemplate } from "@contracts";
import { create } from "zustand";

import { useSession } from "@/auth/session-store";

import type { PendingBlogChip } from "./send-parts";

// The composer's «قالب» chip and «مدونة» chips, per composer — keyed like the
// drafts (conversation id, or NEW_CHAT_DRAFT on `c/new`). Session only, never
// persisted. Module-level so an import that resolves after the screen
// unmounted still lands on its chip.

interface ExtrasState {
  templates: Record<string, PendingTemplate>;
  blogs: Record<string, PendingBlogChip[]>;
  setTemplate: (key: string, template: PendingTemplate | null) => void;
  /** New-chat hand-off: the `c/new` template rides to the created conversation. */
  carryTemplate: (from: string, to: string) => void;
  addBlog: (key: string, chip: PendingBlogChip) => void;
  updateBlog: (key: string, chipId: string, patch: Partial<PendingBlogChip>) => void;
  removeBlogs: (key: string, chipIds: readonly string[]) => void;
  clear: () => void;
}

export const useComposerExtrasStore = create<ExtrasState>((set) => ({
  templates: {},
  blogs: {},
  setTemplate: (key, template) =>
    set((s) => {
      const templates = { ...s.templates };
      if (template) templates[key] = template;
      else delete templates[key];
      return { templates };
    }),
  carryTemplate: (from, to) =>
    set((s) => {
      const t = s.templates[from];
      if (!t || from === to) return s;
      const templates = { ...s.templates, [to]: t };
      delete templates[from];
      return { templates };
    }),
  addBlog: (key, chip) => set((s) => ({ blogs: { ...s.blogs, [key]: [...(s.blogs[key] ?? []), chip] } })),
  updateBlog: (key, chipId, patch) =>
    set((s) => {
      const list = s.blogs[key];
      if (!list?.some((b) => b.id === chipId)) return s;
      return { blogs: { ...s.blogs, [key]: list.map((b) => (b.id === chipId ? { ...b, ...patch } : b)) } };
    }),
  removeBlogs: (key, chipIds) =>
    set((s) => {
      const list = s.blogs[key];
      if (!list) return s;
      const drop = new Set(chipIds);
      const next = list.filter((b) => !drop.has(b.id));
      const blogs = { ...s.blogs };
      if (next.length) blogs[key] = next;
      else delete blogs[key];
      return { blogs };
    }),
  clear: () => set({ templates: {}, blogs: {} }),
}));

const NO_BLOGS: PendingBlogChip[] = [];

export function usePendingTemplate(key: string): PendingTemplate | null {
  return useComposerExtrasStore((s) => s.templates[key] ?? null);
}

export function usePendingBlogs(key: string): PendingBlogChip[] {
  return useComposerExtrasStore((s) => s.blogs[key] ?? NO_BLOGS);
}

// Logout (or a dead session): drop every chip with the rest of the session.
useSession.subscribe((state, prev) => {
  if (prev.user && !state.user) useComposerExtrasStore.getState().clear();
});
