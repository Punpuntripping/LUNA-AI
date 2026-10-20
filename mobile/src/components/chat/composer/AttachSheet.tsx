import { ActionSheet, type SheetAction } from "@/components/ActionSheet";
import { modalCoordinator } from "@/lib/modal-coordinator";

// The composer's «+» sheet (web ComposerPlusMenu, aria «إضافة إلى الرسالة»).
// The web's single «مرفق» row (one file dialog) becomes the phone's three
// sources. `actions` = more rows after them (قالب / مدونة / مستوى التفصيل are
// wired in here by the screen — the web menu's other items).

export type AttachSource = "files" | "photos" | "camera";

export const ATTACH_SHEET_TITLE = "إضافة إلى الرسالة";

export interface AttachSheetProps {
  visible: boolean;
  onClose: () => void;
  /** Runs AFTER the sheet is gone (a picker can't present over a dismissing modal). */
  onPick: (source: AttachSource) => void;
  /** False when attachments are not possible here (the rows are hidden). */
  canAttach: boolean;
  /** Extra rows appended after the attachment sources. */
  actions?: SheetAction[];
}

export function AttachSheet({ visible, onClose, onPick, canAttach, actions = [] }: AttachSheetProps) {
  const pick = (source: AttachSource) => () => {
    modalCoordinator.whenSettled(() => onPick(source));
  };
  const sources: SheetAction[] = canAttach
    ? [
        { key: "files", label: "ملف", hint: "PDF أو صورة — حتى 50 ميجابايت", icon: "attachFile", onPress: pick("files") },
        { key: "photos", label: "الصور", hint: "اختر صوراً من مكتبة الصور", icon: "photos", onPress: pick("photos") },
        { key: "camera", label: "الكاميرا", hint: "صوّر مستنداً وأرفقه", icon: "camera", onPress: pick("camera") },
      ]
    : [];
  return (
    <ActionSheet
      visible={visible}
      onClose={onClose}
      title={ATTACH_SHEET_TITLE}
      actions={[...sources, ...actions]}
      testID="composer-attach-sheet"
    />
  );
}
