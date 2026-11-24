import { memo, useCallback, useEffect, useRef, useState } from "react";
import { Pressable, StyleSheet, View } from "react-native";

import {
  PLAN_CARD_AR,
  planButtonLabelAr,
  planCardView,
  planCheaperLineAr,
  planCostLineAr,
  planPickRefused,
  planPointsAr,
  planSearchLineAr,
  planSearches,
  planStatusLineAr,
  type ExtendedPlanCard,
  type ExtendedPlanOption,
  type PlanChoicePayload,
  type PlanOptionId,
} from "@contracts";

import { useIsConversationRunning } from "@/chat/stream-store";
import { AppText } from "@/components/AppText";
import { Icon } from "@/components/Icon";
import { useTheme } from "@/theme/ThemeProvider";

// «بحث موسّع» plan card (extended_search.md §4.4) — port of the web's
// PlanCard.tsx. Rendered inside the agent-question frame in place of the
// «السؤال» header, the row's text and the suggestions (AssistantBody).
//
// Unlike the next-step chips (which paste), a button here SENDS: the stored
// option runs exactly as priced. It locks at once (local pick), then the
// server's `outcome` on the refetched row drives what it shows. State rules +
// every Arabic line live in the shared contract (`@contracts` extended-plan).
// The parent keys it by `run_id`, so a recycled list cell never carries a
// pick over to another card.

/** Sends the button's choice as a normal message; resolves when that send ends. */
export type PlanChoiceHandler = (choice: PlanChoicePayload, label: string) => Promise<void> | void;

export interface PlanCardProps {
  card: ExtendedPlanCard;
  conversationId: string;
  /** The card row is the conversation's last message. */
  isLatest: boolean;
  /** Absent (the read-only demo conversation) → the buttons never go live. */
  onChoose?: PlanChoiceHandler;
}

export const PlanCard = memo(function PlanCard({ card, conversationId, isLatest, onChoose }: PlanCardProps) {
  const { colours, radius } = useTheme();
  const running = useIsConversationRunning(conversationId);
  const [pick, setPick] = useState<PlanOptionId | null>(null);
  const [sending, setSending] = useState(false);
  // Synchronous guard: two taps inside one frame both see stale state.
  const sendingRef = useRef(false);

  const ui = { isLatest, busy: running || sending, pick };
  const view = planCardView(card, ui);
  const live = view.state === "active" && onChoose !== undefined;
  const chosen = view.state === "chosen" ? view.option : null;

  // The send ended but the user's row did not survive (refused / dropped):
  // nothing ran, so the card forgets the pick and its buttons come back clean.
  const refused = planPickRefused(card, ui);
  useEffect(() => {
    if (refused) setPick(null);
  }, [refused]);

  const handleChoose = useCallback(
    async (option: ExtendedPlanOption) => {
      if (!onChoose || sendingRef.current) return;
      sendingRef.current = true;
      setPick(option.id);
      setSending(true);
      try {
        await onChoose({ run_id: card.run_id, option: option.id }, option.label);
      } finally {
        sendingRef.current = false;
        setSending(false);
      }
    },
    [onChoose, card.run_id],
  );

  const searches = planSearches(card);
  const cheaperLine = planCheaperLineAr(card);
  const statusLine = planStatusLineAr(card, view);

  return (
    <View style={styles.wrap} testID="plan-card">
      <View style={styles.head}>
        <Icon name="planList" size={15} colour={colours.primary} />
        <AppText variant="xs" weight={700} tone="link" accessibilityRole="header">
          {PLAN_CARD_AR.title}
        </AppText>
      </View>

      {searches.length > 0 ? (
        <View style={styles.list}>
          {searches.map((search, i) => (
            <View key={i} style={styles.line}>
              <AppText tone="muted">{`${i + 1}.`}</AppText>
              <AppText style={styles.shrink}>{planSearchLineAr(search)}</AppText>
            </View>
          ))}
        </View>
      ) : null}

      <AppText tone="muted">{PLAN_CARD_AR.merge}</AppText>

      <View style={styles.cost}>
        <AppText variant="xs" weight={500}>
          {planCostLineAr(card)}
        </AppText>
        {cheaperLine ? (
          <AppText variant="xs" tone="muted">
            {cheaperLine}
          </AppText>
        ) : null}
      </View>

      <View style={styles.buttons}>
        {card.options.map((option) => {
          const isChosen = chosen === option.id;
          const solid = option.id === "plan";
          const fg = solid ? colours.primaryFg : isChosen ? colours.primary : colours.textPrimary;
          return (
            <Pressable
              key={option.id}
              testID={`plan-option-${option.id}`}
              accessibilityRole="button"
              accessibilityLabel={`${option.label}، نحو ${planPointsAr(option.points)}`}
              accessibilityState={{ disabled: !live, selected: chosen !== null ? isChosen : undefined }}
              disabled={!live}
              onPress={() => void handleChoose(option)}
              style={({ pressed }) => [
                styles.button,
                {
                  borderRadius: radius.md,
                  backgroundColor: solid ? colours.primary : colours.canvas,
                  borderColor: solid || isChosen ? colours.primary : colours.border,
                  // The picked button stays at full strength; the rest fade.
                  opacity: !live && !isChosen ? 0.5 : pressed ? 0.85 : 1,
                },
              ]}
            >
              {isChosen ? <Icon name="check" size={14} colour={fg} /> : null}
              <AppText variant="xs" weight={500} numberOfLines={1} style={[styles.shrink, { color: fg }]}>
                {planButtonLabelAr(option)}
              </AppText>
            </Pressable>
          );
        })}
      </View>

      {statusLine ? (
        // Announce the lock / outcome, not the standing hint.
        <View style={styles.status} accessibilityLiveRegion={view.state === "active" ? "none" : "polite"}>
          {view.state === "chosen" ? <Icon name="check" size={13} colour={colours.primary} /> : null}
          {view.state === "insufficient" ? <Icon name="error" size={13} colour={colours.textMuted} /> : null}
          <AppText variant="xs" tone={view.state === "chosen" ? "link" : "muted"} style={styles.shrink}>
            {statusLine}
          </AppText>
        </View>
      ) : null}
    </View>
  );
});

const styles = StyleSheet.create({
  wrap: { gap: 8 },
  head: { flexDirection: "row", alignItems: "center", gap: 6 },
  list: { gap: 4 },
  line: { flexDirection: "row", alignItems: "flex-start", gap: 6 },
  shrink: { flexShrink: 1 },
  cost: { gap: 2 },
  buttons: { flexDirection: "row", flexWrap: "wrap", alignItems: "center", gap: 6, paddingTop: 2 },
  button: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: 6,
    minHeight: 36,
    paddingHorizontal: 12,
    borderWidth: 1,
    maxWidth: "100%",
  },
  status: { flexDirection: "row", alignItems: "center", gap: 4 },
});
