import { memo, useCallback, useMemo, useState } from "react";
import { Pressable, StyleSheet, View } from "react-native";

import { parseNextSteps, parseTemplatesUsed, type Message } from "@contracts";

import { useDeepSearchSummary, useIsConversationRunning } from "@/chat/stream-store";
import { citationTarget } from "@/chat/use-workspace-lookup";
import { ActionSheet } from "@/components/ActionSheet";
import { AppText } from "@/components/AppText";
import { copyActions, CopyMenu } from "@/components/CopyMenu";
import { IconButton } from "@/components/IconButton";
import { Markdown } from "@/components/markdown/Markdown";
import { useTheme } from "@/theme/ThemeProvider";

import { AgentQuestion } from "./AgentQuestion";
import { useChatRow } from "./ChatContext";
import { DeepSearchSummaryChip } from "./DeepSearchProgress";
import { RetryButton } from "./FailedBubble";
import { NextStepChips } from "./NextStepChips";
import { TemplatesUsedLine } from "./TemplatesUsedLine";

// M4 — an assistant answer: unframed full-width markdown (port of the web's
// MessageBubble assistant branch). Settled extras: «القوالب المستخدمة»,
// next-step chips (latest answer only), نسخ + «إعادة التوليد». An
// `agent_question` turn renders as the framed «السؤال» callout instead.
// «افتح التحليل» / referenced-item chips arrive with the workspace (2b).

export interface AssistantBodyProps {
  message: Message;
  /** Live text while this row streams (null = settled). */
  streamingContent?: string | null;
  /** The newest row of the conversation (next-step chips). */
  isLatest?: boolean;
}

export const AssistantBody = memo(function AssistantBody({ message, streamingContent = null, isLatest = false }: AssistantBodyProps) {
  const { colours } = useTheme();
  const { conversationId, isDemo, artifactKinds, pasteIntoComposer, regenerate, openCitation } = useChatRow();
  const running = useIsConversationRunning(conversationId);
  const summary = useDeepSearchSummary(message.message_id);
  const [menu, setMenu] = useState(false);

  const streaming = streamingContent !== null;
  const content = streaming ? streamingContent : message.content;
  const settled = !streaming && !message.isOptimistic;
  const isQuestion = message.metadata?.kind === "agent_question";

  // Copy reads at tap time: the live stream while it arrives (Y2).
  const getText = useCallback(() => content ?? "", [content]);

  // K2 — `[n]` is pressable only when the message has an agent_search artifact.
  const target = isQuestion || streaming ? null : citationTarget(message, artifactKinds);
  const onCitationPress = useMemo(
    () => (target ? (n: number) => openCitation(target, n) : undefined),
    [target, openCitation],
  );

  const templates = useMemo(
    () => (settled && !isQuestion && !message.isFailed ? parseTemplatesUsed(message.metadata?.templates_used) : undefined),
    [settled, isQuestion, message.isFailed, message.metadata?.templates_used],
  );
  const nextSteps = useMemo(
    () => (isLatest && settled && !isQuestion && !message.isFailed && !isDemo ? parseNextSteps(message.metadata?.next_steps) : []),
    [isLatest, settled, isQuestion, message.isFailed, isDemo, message.metadata?.next_steps],
  );

  const body = <Markdown content={content ?? ""} streaming={streaming} variant="chat" onCitationPress={onCitationPress} />;

  return (
    <View style={[styles.wrap, isQuestion && styles.tight]} testID={streaming ? "assistant-message-streaming" : "assistant-message"}>
      {summary ? <DeepSearchSummaryChip summary={summary} /> : null}
      <AppText variant="xs" tone="muted" style={styles.caption}>
        ريحان
      </AppText>

      <Pressable onLongPress={() => setMenu(true)} delayLongPress={350} accessibilityHint="اضغط مطولاً لخيارات النسخ">
        {isQuestion ? (
          <AgentQuestion suggestions={message.metadata?.suggestions}>{body}</AgentQuestion>
        ) : message.isFailed ? (
          <View style={[styles.failedFrame, { borderColor: colours.destructive }]}>{body}</View>
        ) : (
          body
        )}
      </Pressable>

      {templates !== undefined ? <TemplatesUsedLine templates={templates} /> : null}
      {nextSteps.length > 0 ? <NextStepChips steps={nextSteps} onPaste={pasteIntoComposer} /> : null}

      {message.isFailed && !running && !isDemo ? (
        <View style={styles.failedRow}>
          <AppText variant="xs" tone="destructive">
            فشل إرسال الرسالة
          </AppText>
          <RetryButton onPress={() => regenerate(message.message_id)} />
        </View>
      ) : null}

      {settled && !message.isFailed ? (
        <View style={styles.actions}>
          <CopyMenu getText={getText} testID="assistant-message-copy" />
          {!isDemo && !running ? (
            <IconButton
              icon="regenerate"
              label="إعادة التوليد"
              size={18}
              colour={colours.textMuted}
              onPress={() => regenerate(message.message_id)}
              testID="assistant-message-regenerate"
            />
          ) : null}
        </View>
      ) : null}

      <ActionSheet visible={menu} onClose={() => setMenu(false)} actions={copyActions(getText)} testID="assistant-message-menu" />
    </View>
  );
});

const styles = StyleSheet.create({
  wrap: { alignSelf: "stretch", marginBottom: 28 },
  tight: { marginBottom: 12 },
  caption: { marginBottom: 4 },
  failedFrame: { borderWidth: 2, borderRadius: 16, paddingHorizontal: 14, paddingVertical: 12 },
  failedRow: { flexDirection: "row", alignItems: "center", gap: 8, marginTop: 8 },
  actions: { flexDirection: "row", alignItems: "center", gap: 2, marginTop: 4 },
});
