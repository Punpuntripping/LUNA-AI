import { create } from "zustand";

export type SidebarTab = "conversations" | "cases" | "templates";

interface SidebarState {
  isOpen: boolean;
  activeTab: SidebarTab;
  expandedCases: Set<string>;
  selectedConversationId: string | null;
  selectedCaseId: string | null;
  isCreateCaseDialogOpen: boolean;
  isCreateTemplateDialogOpen: boolean;

  toggle: () => void;
  setOpen: (open: boolean) => void;
  setActiveTab: (tab: SidebarTab) => void;
  toggleCaseExpanded: (caseId: string) => void;
  setSelectedConversation: (id: string | null) => void;
  setSelectedCase: (id: string | null) => void;
  setCreateCaseDialogOpen: (open: boolean) => void;
  setCreateTemplateDialogOpen: (open: boolean) => void;
}

export const useSidebarStore = create<SidebarState>((set) => ({
  isOpen: true,
  activeTab: "conversations",
  expandedCases: new Set<string>(),
  selectedConversationId: null,
  selectedCaseId: null,
  isCreateCaseDialogOpen: false,
  isCreateTemplateDialogOpen: false,

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
}));
