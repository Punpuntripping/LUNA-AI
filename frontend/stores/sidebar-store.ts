import { create } from "zustand";

// قوالبي/مدوناتي/مكتبتي are no longer sidebar tabs — their rows navigate
// straight to the /mine full pages. Only the chats/cases panel swap remains.
export type SidebarTab = "conversations" | "cases";

interface SidebarState {
  isOpen: boolean;
  activeTab: SidebarTab;
  expandedCases: Set<string>;
  selectedConversationId: string | null;
  selectedCaseId: string | null;
  isCreateCaseDialogOpen: boolean;
  isCreateTemplateDialogOpen: boolean;
  isImportBlogDialogOpen: boolean;

  toggle: () => void;
  setOpen: (open: boolean) => void;
  setActiveTab: (tab: SidebarTab) => void;
  toggleCaseExpanded: (caseId: string) => void;
  setSelectedConversation: (id: string | null) => void;
  setSelectedCase: (id: string | null) => void;
  setCreateCaseDialogOpen: (open: boolean) => void;
  setCreateTemplateDialogOpen: (open: boolean) => void;
  setImportBlogDialogOpen: (open: boolean) => void;
}

/**
 * The rail starts open on a desktop viewport and closed below `md`.
 *
 * Read ONCE, at store creation, from `matchMedia` rather than in an effect:
 * an effect-based close runs after the first paint, which is exactly the
 * 288px drawer that used to flash open on every phone load
 * (mobile_compatibility §1.8). Server-side there is no viewport to ask, so it
 * falls back to the desktop answer — `Sidebar` renders the rail `hidden`
 * below `md` and pins its expanded/collapsed classes there, so the server
 * markup and the client's first render still agree byte-for-byte.
 */
function initialSidebarOpen(): boolean {
  if (typeof window === "undefined") return true;
  return window.matchMedia("(min-width: 768px)").matches;
}

export const useSidebarStore = create<SidebarState>((set) => ({
  isOpen: initialSidebarOpen(),
  activeTab: "conversations",
  expandedCases: new Set<string>(),
  selectedConversationId: null,
  selectedCaseId: null,
  isCreateCaseDialogOpen: false,
  isCreateTemplateDialogOpen: false,
  isImportBlogDialogOpen: false,

  toggle: () => set((s) => ({ isOpen: !s.isOpen })),
  setOpen: (isOpen) => set({ isOpen }),
  setActiveTab: (activeTab) => set({ activeTab }),
  toggleCaseExpanded: (caseId) =>
    set((s) => {
      const next = new Set(s.expandedCases);
      if (next.has(caseId)) next.delete(caseId);
      else next.add(caseId);
      return { expandedCases: next };
    }),
  setSelectedConversation: (id) => set({ selectedConversationId: id }),
  setSelectedCase: (id) => set({ selectedCaseId: id }),
  setCreateCaseDialogOpen: (isCreateCaseDialogOpen) => set({ isCreateCaseDialogOpen }),
  setCreateTemplateDialogOpen: (isCreateTemplateDialogOpen) =>
    set({ isCreateTemplateDialogOpen }),
  setImportBlogDialogOpen: (isImportBlogDialogOpen) =>
    set({ isImportBlogDialogOpen }),
}));
