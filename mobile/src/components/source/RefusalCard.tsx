import { StyleSheet, View } from "react-native";

import type { RefusalCardCopy } from "@contracts";

import { AppText } from "@/components/AppText";
import { Button } from "@/components/Button";
import { useTheme } from "@/theme/ThemeProvider";

export interface RefusalCardProps {
  copy: RefusalCardCopy;
  actionLabel?: string;
  onAction?: () => void;
  actionLoading?: boolean;
}

/** A refused or failed reveal — never blames the reader, never links to web
 *  pricing (the copy's own CTA fields are ignored on purpose). */
export function RefusalCard({ copy, actionLabel, onAction, actionLoading = false }: RefusalCardProps) {
  const { colours, radius } = useTheme();
  return (
    <View
      accessibilityRole="alert"
      testID="reference-source-refusal"
      style={[styles.card, { backgroundColor: colours.brandSoftBg, borderColor: colours.border, borderRadius: radius.default }]}
    >
      <AppText variant="base" weight={600} align="center" style={{ color: colours.brandSoftFg }}>
        {copy.title}
      </AppText>
      <AppText variant="sm" tone="secondary" align="center">
        {copy.body}
      </AppText>
      {actionLabel && onAction ? (
        <View style={styles.action}>
          <Button label={actionLabel} onPress={onAction} variant="secondary" loading={actionLoading} />
        </View>
      ) : null}
    </View>
  );
}

const styles = StyleSheet.create({
  card: { borderWidth: StyleSheet.hairlineWidth, padding: 16, gap: 8 },
  action: { marginTop: 4 },
});
