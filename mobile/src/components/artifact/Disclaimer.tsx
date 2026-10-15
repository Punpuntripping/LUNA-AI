import { StyleSheet, View } from "react-native";

import { AGENT_OUTPUT_DISCLAIMER_AR } from "@contracts";

import { AppText } from "@/components/AppText";
import { useTheme } from "@/theme/ThemeProvider";

/** The legal notice under every agent output (plan 05 §D1) — same text and
 *  same body size as the web's AgentOutputDisclaimer, never shrunk. */
export function AgentOutputDisclaimer() {
  const { colours } = useTheme();
  return (
    <View style={[styles.box, { borderTopColor: colours.border }]} accessibilityRole="text">
      <AppText variant="sm" tone="muted">
        {AGENT_OUTPUT_DISCLAIMER_AR}
      </AppText>
    </View>
  );
}

const styles = StyleSheet.create({
  box: { borderTopWidth: StyleSheet.hairlineWidth, paddingTop: 12, marginTop: 8 },
});
