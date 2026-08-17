import { create } from "zustand";

/**
 * Open/close state for «إعدادات المحادثة» (`ConversationSettingsDialog` —
 * مستوى التفصيل + وضع السرية).
 *
 * The exact twin of `usage-dialog-store`, and for the exact same reason: the
 * dialog used to be local `useState` inside `SidebarFooter`, which mounts it as
 * a sibling of the settings popover. Below `md` the sidebar body renders inside
 * a Radix `Sheet`, and a Sheet UNMOUNTS its children when closed — so on a phone
 * with the drawer shut the dialog was not in the tree at all and nothing outside
 * the sidebar (the وضع السرية edu lesson's action button) could open it.
 *
 * The dialog is therefore mounted once per app shell — `ChatLayoutClient` and
 * `SidebarPageShell`, the two surfaces that render a `Sidebar` — and driven from
 * here. Call sites only ever call `open()`.
 *
 * ⚠ Exactly ONE instance must be mounted at a time. Both shells are mutually
 * exclusive (different route layouts), so mounting in each is not a double
 * mount; adding a third mount inside a component that can coexist with either
 * would be.
 */
interface ConversationSettingsDialogState {
  isOpen: boolean;
  open: () => void;
  close: () => void;
  setOpen: (open: boolean) => void;
}

export const useConversationSettingsDialogStore =
  create<ConversationSettingsDialogState>((set) => ({
    isOpen: false,
    open: () => set({ isOpen: true }),
    close: () => set({ isOpen: false }),
    setOpen: (open) => set({ isOpen: open }),
  }));
