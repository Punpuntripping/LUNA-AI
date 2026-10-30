import { StyleSheet, View } from "react-native";

import { WI_PREVIEW_COPY, wiPreviewHiddenRefsLine } from "@contracts";

import { AppText } from "@/components/AppText";
import { Button } from "@/components/Button";
import { Icon } from "@/components/Icon";
import { withAlpha } from "@/theme/alpha";
import { useTheme } from "@/theme/ThemeProvider";

// WI preview lock (.claude/plans/wi_preview_lock.md §7) — the app half of the
// web's WiPreviewWall. The server decides and truncates; a viewer renders
// `wiPreviewVisibleContent(item)`, lays <WiPreviewFade /> over its last block,
// puts <WiPreviewWall /> under it and <WiPreviewHiddenRefs /> at the end of
// «المراجع». Every word is the contract's (`WI_PREVIEW_COPY`, both apps say the
// same) and nothing here links anywhere — never to the web (store rules).

/** How far the canvas fades in over the end of the visible text. */
const FADE_HEIGHT = 72;

/**
 * The canvas fading in over the last lines of the visible part. Render it as
 * the LAST child of the view holding the final block of text: absolute, never
 * taller than that view, never takes a touch, and invisible to screen readers
 * (the text under it stays readable to them).
 */
export function WiPreviewFade() {
  const { colours } = useTheme();
  return (
    <View
      pointerEvents="none"
      accessible={false}
      importantForAccessibility="no"
      testID="wi-preview-fade"
      style={[
        styles.fade,
        { experimental_backgroundImage: `linear-gradient(to bottom, ${withAlpha(colours.canvas, 0)}, ${colours.canvas})` },
      ]}
    />
  );
}

export interface WiPreviewWallProps {
  /**
   * The upgrade button (`WI_PREVIEW_COPY.cta`) renders only when this is
   * passed. No caller passes it yet: Phase 4b wires it to the in-app purchase
   * paywall behind `useIapEntryVisible()` (src/purchases/entry.ts). Until
   * then the wall is text only, like every other limit in the app.
   */
  onUpgrade?: () => void;
}

/** The wall under the visible part — web gate panel look (`rounded-2xl border
 *  border-primary/20 bg-gradient-to-b from-primary/5 to-card p-5 text-center`). */
export function WiPreviewWall({ onUpgrade }: WiPreviewWallProps) {
  const { colours, radius } = useTheme();
  return (
    <View
      testID="wi-preview-wall"
      style={[
        styles.card,
        {
          borderColor: withAlpha(colours.primary, 0.2),
          borderRadius: radius["2xl"],
          backgroundColor: colours.surface1,
          experimental_backgroundImage: `linear-gradient(to bottom, ${withAlpha(colours.primary, 0.05)}, ${colours.surface1})`,
        },
      ]}
    >
      <Icon name="lock" size={18} colour={colours.primary} style={styles.icon} />
      <AppText variant="base" weight={700} align="center" accessibilityRole="header">
        {WI_PREVIEW_COPY.title}
      </AppText>
      <AppText variant="sm" tone="muted" align="center">
        {WI_PREVIEW_COPY.body}
      </AppText>
      {onUpgrade ? (
        <View style={styles.action}>
          <Button label={WI_PREVIEW_COPY.cta} onPress={onUpgrade} testID="wi-preview-upgrade" />
        </View>
      ) : null}
    </View>
  );
}

/** «وN مصادر أخرى في الجزء المقفل» at the end of «المراجع»; nothing for 0. */
export function WiPreviewHiddenRefs({ count }: { count: number }) {
  const { colours } = useTheme();
  const line = wiPreviewHiddenRefsLine(count);
  if (!line) return null;
  return (
    <View style={styles.hiddenRefs}>
      <Icon name="lock" size={13} colour={colours.textMuted} />
      <AppText variant="sm" tone="muted" style={styles.fill} testID="wi-preview-hidden-refs">
        {line}
      </AppText>
    </View>
  );
}

const styles = StyleSheet.create({
  fade: { position: "absolute", start: 0, end: 0, bottom: 0, height: FADE_HEIGHT, maxHeight: "100%" },
  // Labels take the card width and centre by textAlign: a shrink-wrapped Naskh
  // label loses its last glyph at large font scales on Android (QA F3).
  card: { gap: 8, padding: 20, borderWidth: 1 },
  icon: { alignSelf: "center" },
  action: { marginTop: 8 },
  hiddenRefs: { flexDirection: "row", alignItems: "center", gap: 6, paddingTop: 4, paddingBottom: 12 },
  // The row's width, not shrink-wrapped (same Android Naskh clipping as above).
  fill: { flex: 1 },
});
