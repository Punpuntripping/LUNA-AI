import { StyleSheet, View } from "react-native";

import { AppText } from "./AppText";

/** Brand mark + screen title for the signed-out screens. */
export function AuthHeader({ title, subtitle }: { title: string; subtitle?: string }) {
  return (
    <View style={styles.wrap}>
      <AppText variant="h1" weight={700} tone="link" align="center">
        ريحان
      </AppText>
      <AppText variant="h2" weight={600} align="center">
        {title}
      </AppText>
      {subtitle ? (
        <AppText variant="sm" tone="muted" align="center">
          {subtitle}
        </AppText>
      ) : null}
    </View>
  );
}

const styles = StyleSheet.create({
  wrap: { gap: 4, marginBottom: 8 },
});
