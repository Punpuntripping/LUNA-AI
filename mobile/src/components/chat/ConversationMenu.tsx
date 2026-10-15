import { useState } from "react";

import type { ConversationSummary } from "@contracts";

import { errorMessage } from "@/api/errors";
import { useDeleteConversation, useRenameConversation, useStarConversation } from "@/chat/use-conversations";
import { Sheet, SheetActions, SheetPrompt, type SheetAction } from "@/components/ActionSheet";
import { showToast } from "@/stores/toast";

// L4 / M9 — the conversation's menu (list long-press and the chat header):
// تمييز / إلغاء التمييز · إعادة تسمية · حذف (confirmed). The shared demo
// conversation is read-only: its only action is «إخفاء» (DELETE on the demo
// hides it for this user only — conversation_service.delete_conversation).
// One Modal for the whole flow; the steps swap its content.

export const DEFAULT_TITLE = "محادثة جديدة";

export function conversationTitle(c: Pick<ConversationSummary, "title_ar"> | null | undefined): string {
  return c?.title_ar?.trim() || DEFAULT_TITLE;
}

type Step = "menu" | "rename" | "confirm";

export interface ConversationMenuProps {
  /** Open while non-null. */
  conversation: ConversationSummary | null;
  onClose: () => void;
  /** After a successful delete / hide (the chat screen leaves). */
  onDeleted?: () => void;
  testID?: string;
}

export function ConversationMenu({ conversation, onClose, onDeleted, testID = "conversation-menu" }: ConversationMenuProps) {
  const star = useStarConversation();
  const rename = useRenameConversation();
  const remove = useDeleteConversation();
  const [step, setStep] = useState<Step>("menu");

  const close = () => {
    setStep("menu");
    onClose();
  };

  const visible = conversation !== null;
  const id = conversation?.conversation_id ?? "";
  const demo = conversation?.is_demo === true;

  const menuActions: SheetAction[] = demo
    ? [{ key: "hide", label: "إخفاء", icon: "hide", destructive: true, onPress: () => setStep("confirm") }]
    : [
        {
          key: "star",
          label: conversation?.is_starred ? "إلغاء التمييز" : "تمييز",
          icon: "star",
          onPress: () => {
            const starred = !conversation?.is_starred;
            close();
            star.mutate({ id, starred }, { onError: (err) => showToast(errorMessage(err)) });
          },
        },
        { key: "rename", label: "إعادة تسمية", icon: "edit", onPress: () => setStep("rename") },
        { key: "delete", label: "حذف", icon: "trash", destructive: true, onPress: () => setStep("confirm") },
      ];

  const confirmDelete = () => {
    close();
    remove.mutate(id, {
      onSuccess: () => onDeleted?.(),
      onError: (err) => showToast(errorMessage(err)),
    });
  };

  return (
    <Sheet visible={visible} onClose={close} testID={testID}>
      {step === "menu" ? (
        <SheetActions
          onClose={close}
          title={conversationTitle(conversation)}
          actions={menuActions}
          keepOpenOnPress
          testID={testID}
        />
      ) : step === "rename" ? (
        <SheetPrompt
          onClose={close}
          title="إعادة تسمية المحادثة"
          initialValue={conversation?.title_ar ?? ""}
          confirmLabel="حفظ"
          onSubmit={(value) => rename.mutate({ id, title: value }, { onError: (err) => showToast(errorMessage(err)) })}
          testID={`${testID}-rename`}
        />
      ) : (
        <SheetActions
          onClose={close}
          title={demo ? "إخفاء المحادثة التجريبية؟" : "حذف المحادثة؟"}
          message={demo ? "ستختفي من قائمتك فقط." : "سيتم حذف المحادثة وما فيها من رسائل، ولا يمكن التراجع عن ذلك."}
          actions={[
            {
              key: "confirm",
              label: demo ? "إخفاء" : "حذف",
              icon: demo ? "hide" : "trash",
              destructive: true,
              onPress: confirmDelete,
            },
          ]}
          keepOpenOnPress
          testID={`${testID}-confirm`}
        />
      )}
    </Sheet>
  );
}
