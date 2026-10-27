import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { ScrollView, StyleSheet, useWindowDimensions, View } from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";

import { useSession } from "@/auth/session-store";
import { Sheet } from "@/components/ActionSheet";
import { Button } from "@/components/Button";
import { useKeyboardHeight } from "@/hooks/use-keyboard-height";
import { modalCoordinator } from "@/lib/modal-coordinator";
import { STEP_PROFESSION } from "@/profession/content";
import { finishProfession, initialStep, isOtherActive, type ProfessionStepState } from "@/profession/logic";
import { markProfessionReaskDone } from "@/profession/reask";
import { useProfessionPrompt } from "@/profession/store";
import { useProfessionGate } from "@/profession/use-profession-gate";

import { StepProfession } from "./StepProfession";

/** Sheet chrome above the content: top padding 8 + grabber 4 + its margin 8. */
const SHEET_TOP = 20;
/** Breathing room under the status bar when the keyboard is up. */
const TOP_GAP = 8;

/**
 * «وش أقرب وصف لك؟» — the web OnboardingDialog as the app's bottom sheet,
 * mounted once in the signed-in layout (never on the deletion-grace screen).
 *
 * - Opens by itself when `profession_group` is exactly null, or once more for a
 *   stored «declined» while `profession_reask_done` is unset — after the
 *   preferences read settled (`src/profession/logic.ts`). «تغيير المهنة» in
 *   settings opens it too, with the stored answer pre-selected.
 * - Presents only once no other modal / sheet is up or settling, and holds
 *   «رسائل ريحان» back while it is wanted.
 * - EVERY exit (حفظ, backdrop, Android back) saves: the picked answer, or
 *   «declined» for an untouched never-asked account; and closes the re-ask.
 *   No offer popup follows (web IntroOfferDialog — nothing is sold in the app).
 */
export function ProfessionPromptHost() {
  const qc = useQueryClient();
  const { autoOpen } = useProfessionGate();
  const isOpen = useProfessionPrompt((s) => s.isOpen);
  const openCount = useProfessionPrompt((s) => s.openCount);
  const [step, setStep] = useState<ProfessionStepState>(() => initialStep(null));
  // The open (openCount) that is on screen; it stays set while the sheet slides
  // away so the content does not vanish mid-animation.
  const [presentedFor, setPresentedFor] = useState(0);

  useEffect(() => {
    if (autoOpen) useProfessionPrompt.getState().open();
  }, [autoOpen]);

  useEffect(() => {
    if (!isOpen) return undefined;
    return modalCoordinator.whenClear(() => {
      // The stored answer is pre-selected so a reopen doubles as "change my answer".
      setStep(initialStep(useSession.getState().user));
      setPresentedFor(openCount);
    });
  }, [isOpen, openCount]);

  const visible = isOpen && presentedFor === openCount;

  const finish = () => {
    // One exit per open (a second tap may land before the re-render).
    if (!useProfessionPrompt.getState().isOpen) return;
    finishProfession({
      selection: step.selection,
      user: useSession.getState().user,
      save: (group, label) => useSession.getState().saveProfession(group, label),
      markReaskDone: () => markProfessionReaskDone(qc),
      close: () => useProfessionPrompt.getState().resolve(),
    });
  };

  // Height: at most 85% of the screen (web max-h-[85dvh]) and never under the
  // status bar once the keyboard is up; the step scrolls, «حفظ» stays pinned.
  const { height } = useWindowDimensions();
  const insets = useSafeAreaInsets();
  const keyboard = useKeyboardHeight();
  const [footerHeight, setFooterHeight] = useState(64);
  const panelMax = Math.min(height * 0.85, height - insets.top - TOP_GAP - keyboard);
  const scrollMax = Math.max(120, panelMax - SHEET_TOP - (insets.bottom + 12) - footerHeight);

  // While «أخرى» has focus, keep its field (the last thing in the step) in
  // view as the keyboard shrinks the scroll area.
  const scrollRef = useRef<ScrollView>(null);
  const followInput = useRef(false);
  const scrollToInput = () => {
    if (followInput.current) scrollRef.current?.scrollToEnd({ animated: true });
  };

  return (
    <Sheet visible={visible} onClose={finish} testID="profession-sheet">
      <ScrollView
        ref={scrollRef}
        style={{ maxHeight: scrollMax }}
        contentContainerStyle={styles.body}
        keyboardShouldPersistTaps="handled"
        showsVerticalScrollIndicator={false}
        onLayout={scrollToInput}
        onContentSizeChange={scrollToInput}
      >
        <StepProfession
          state={step}
          onChange={(next) => {
            // The field can unmount without a blur (another card / chip tapped).
            if (!isOtherActive(next)) followInput.current = false;
            setStep(next);
          }}
          onOtherFocus={() => {
            followInput.current = true;
            scrollToInput();
          }}
          onOtherBlur={() => {
            followInput.current = false;
          }}
        />
      </ScrollView>
      <View style={styles.footer} onLayout={(e) => setFooterHeight(e.nativeEvent.layout.height)}>
        <Button label={STEP_PROFESSION.saveLabel} onPress={finish} testID="profession-save" />
      </View>
    </Sheet>
  );
}

const styles = StyleSheet.create({
  body: { paddingHorizontal: 12, paddingTop: 4, paddingBottom: 4 },
  footer: { paddingHorizontal: 12, paddingTop: 12 },
});
