import { memo, useEffect, useState } from "react";
import { Animated, StyleSheet, View } from "react-native";

import type { AgentFamily } from "@contracts";

import { useDeepSearchProgress, useRunningAgent } from "@/chat/stream-store";
import { AppText } from "@/components/AppText";
import { useTheme } from "@/theme/ThemeProvider";

import { DeepSearchProgress } from "./DeepSearchProgress";

// «ريحان يفكّر» — the row shown while an answer is on its way (port of the
// web's ThinkingRow + TypingIndicator, MessageList.tsx / TypingIndicator.tsx).
// A live deep_search gets the step tracker; everything else (and every
// placeholder after a resume — progress is not replayed, plan X6) the
// rotating phrase.

const RAYHAN = "ريحان";
const ROTATION_MS = 7000;
const DEFAULT_PHRASES = ["يفكّر", "يتفكّر", "يحلّل"] as const;
const AGENT_PHRASES: Partial<Record<AgentFamily, readonly string[]>> = {
  deep_search: ["يبحث بعمق", "يحلّل المصادر", "يراجع الأنظمة"],
  memory: ["يستحضر السياق", "يتذكّر"],
  writing: ["يصيغ الإجابة", "يكتب"],
};

export const Thinking = memo(function Thinking({ conversationId }: { conversationId: string }) {
  const family = useRunningAgent(conversationId);
  const progress = useDeepSearchProgress(conversationId);
  if (family === "deep_search" && progress) return <DeepSearchProgress conversationId={conversationId} />;
  // key: a new family restarts the phrase rotation from its first phrase.
  return <TypingIndicator key={family ?? "default"} family={family} />;
});

export function TypingIndicator({ family }: { family: AgentFamily | null }) {
  const { colours, radius } = useTheme();
  const phrases = (family && AGENT_PHRASES[family]) || DEFAULT_PHRASES;
  const [index, setIndex] = useState(0);
  const [pulse] = useState(() => new Animated.Value(0.4));

  useEffect(() => {
    if (phrases.length < 2) return;
    const id = setInterval(() => setIndex((i) => (i + 1) % phrases.length), ROTATION_MS);
    return () => clearInterval(id);
  }, [phrases]);

  useEffect(() => {
    const loop = Animated.loop(
      Animated.sequence([
        Animated.timing(pulse, { toValue: 1, duration: 700, useNativeDriver: true }),
        Animated.timing(pulse, { toValue: 0.4, duration: 700, useNativeDriver: true }),
      ]),
    );
    loop.start();
    return () => loop.stop();
  }, [pulse]);

  const label = `${RAYHAN} ${phrases[index % phrases.length]}`;
  return (
    <View
      accessibilityRole="progressbar"
      accessibilityLabel={`${RAYHAN} يعمل على ردّك`}
      style={[styles.box, { backgroundColor: colours.surface1, borderColor: colours.border, borderRadius: radius.xl }]}
      testID="chat-thinking"
    >
      <Animated.View style={[styles.dot, { backgroundColor: colours.primary, opacity: pulse }]} />
      <AppText variant="sm" tone="muted">
        {label}
      </AppText>
    </View>
  );
}

const styles = StyleSheet.create({
  box: {
    alignSelf: "flex-start",
    flexDirection: "row",
    alignItems: "center",
    gap: 8,
    paddingHorizontal: 14,
    paddingVertical: 10,
    borderWidth: 1,
  },
  dot: { width: 8, height: 8, borderRadius: 4 },
});
