import { Stack, useLocalSearchParams } from "expo-router";
import { StyleSheet, View } from "react-native";

import { AppText } from "@/components/AppText";
import { SourceReader } from "@/components/source/SourceReader";
import { useTheme } from "@/theme/ThemeProvider";

/** `/source/{itemId}/{n}` — citation `n` of an agent output (plan 05 §R2). */
export default function SourceScreen() {
  const { colours } = useTheme();
  const params = useLocalSearchParams<{ itemId: string; n: string }>();
  const itemId = typeof params.itemId === "string" ? params.itemId : "";
  const n = Number.parseInt(typeof params.n === "string" ? params.n : "", 10);
  const valid = itemId.length > 0 && Number.isSafeInteger(n) && n >= 1;

  return (
    <View style={[styles.root, { backgroundColor: colours.canvas }]}>
      <Stack.Screen options={{ title: valid ? `المرجع ${n}` : "المصدر" }} />
      {valid ? (
        <SourceReader itemId={itemId} n={n} />
      ) : (
        <View style={styles.centre}>
          <AppText variant="base" tone="muted" align="center">
            المرجع غير موجود
          </AppText>
        </View>
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, direction: "rtl" },
  centre: { flex: 1, alignItems: "center", justifyContent: "center", padding: 24 },
});
