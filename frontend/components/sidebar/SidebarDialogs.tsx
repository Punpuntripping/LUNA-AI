"use client";

import { UsageLimitsDialog } from "@/components/Settings/UsageLimitsDialog";
import { ConversationSettingsDialog } from "@/components/Settings/ConversationSettingsDialog";
import { useUsageDialogStore } from "@/stores/usage-dialog-store";
import { useConversationSettingsDialogStore } from "@/stores/conversation-settings-dialog-store";

/**
 * The two settings dialogs that must be openable from OUTSIDE the sidebar
 * («حدود الاستخدام» and «إعدادات المحادثة» — both are targets of edu lesson
 * action buttons).
 *
 * ⚠ WHY THEY ARE NOT MOUNTED IN `SidebarFooter` WITH THE OTHERS.
 * Below `md` the sidebar body renders inside a Radix `Sheet`, and a Sheet
 * UNMOUNTS its children when closed. A dialog mounted next to the settings
 * popover is therefore simply absent on a phone with the drawer shut, so
 * `open()` from anywhere else in the app does nothing at all.
 *
 * ⚠ WHY HERE AND NOT IN THE PAGE SHELLS.
 * Mounting them in `ChatLayoutClient` fixes the chat routes and silently leaves
 * `/templates`, `/blogs` and `/library/mine` broken — those render the same
 * `Sidebar` (with the same settings rows) through `SidebarPageShell`. Hanging
 * the dialogs off `Sidebar` itself means they travel with the menu that opens
 * them, and any future shell gets them for free.
 *
 * Rendered OUTSIDE the `Sheet`/`aside` in both of `Sidebar`'s branches, and only
 * one of those branches is ever mounted — so this is exactly one instance of
 * each dialog at any viewport width. Do not mount either dialog anywhere else.
 *
 * The remaining settings dialogs (تفعيل برمز، إعدادات الحساب، سجل المدفوعات)
 * deliberately stay on local state inside `SidebarFooter`: nothing outside the
 * sidebar opens them, so they have no reason to pay for a store.
 */
export function SidebarDialogs() {
  const usageOpen = useUsageDialogStore((s) => s.isOpen);
  const setUsageOpen = useUsageDialogStore((s) => s.setOpen);
  const conversationOpen = useConversationSettingsDialogStore((s) => s.isOpen);
  const setConversationOpen = useConversationSettingsDialogStore(
    (s) => s.setOpen,
  );

  return (
    <>
      <UsageLimitsDialog open={usageOpen} onOpenChange={setUsageOpen} />
      <ConversationSettingsDialog
        open={conversationOpen}
        onOpenChange={setConversationOpen}
      />
    </>
  );
}
