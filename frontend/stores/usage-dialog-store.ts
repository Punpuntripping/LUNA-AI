import { create } from "zustand";

/**
 * Open/close state for «حدود الاستخدام» (`UsageLimitsDialog`).
 *
 * It used to be local `useState` inside `SidebarFooter`, which mounted the
 * dialog as a sibling of the settings popover. That broke the moment a second
 * entry point needed it: below `md` the sidebar body renders inside a Radix
 * `Sheet`, and a Sheet UNMOUNTS its children when closed — so on a phone with
 * the drawer shut the dialog was not in the tree at all and nothing outside the
 * sidebar could open it.
 *
 * The dialog is therefore mounted once in `ChatLayoutClient` (always rendered)
 * and driven from here. Call sites — the settings row, an edu lesson's action
 * button — only ever call `open()`.
 */
interface UsageDialogState {
  isOpen: boolean;
  open: () => void;
  close: () => void;
  setOpen: (open: boolean) => void;
}

export const useUsageDialogStore = create<UsageDialogState>((set) => ({
  isOpen: false,
  open: () => set({ isOpen: true }),
  close: () => set({ isOpen: false }),
  setOpen: (open) => set({ isOpen: open }),
}));
