import { Linking, StyleSheet, View } from "react-native";

import { AppText } from "@/components/AppText";
import { Button } from "@/components/Button";
import { Screen } from "@/components/Screen";
import { useUpdateGate } from "@/stores/update-gate";

// «حدّث التطبيق» — blocking, no dismiss. Shown when the installed native
// version is below /app/config's minimum, or on any 426 APP_UPDATE_REQUIRED.
export default function UpdateRequiredScreen() {
  const storeUrl = useUpdateGate((s) => s.storeUrl);

  return (
    <Screen centred scroll={false}>
      <View style={styles.hero}>
        <AppText variant="h1" weight={700} tone="link" align="center">
          ريحان
        </AppText>
        <AppText variant="h2" weight={700} align="center">
          حدّث التطبيق
        </AppText>
        <AppText variant="base" tone="secondary" align="center">
          يتوفر إصدار جديد من ريحان. يلزم تحديث التطبيق إلى أحدث إصدار للمتابعة.
        </AppText>
      </View>
      {storeUrl ? (
        <Button label="تحديث الآن" onPress={() => Linking.openURL(storeUrl).catch(() => undefined)} testID="update-store" />
      ) : (
        <AppText variant="sm" tone="muted" align="center">
          حدّث ريحان من متجر التطبيقات على جهازك.
        </AppText>
      )}
    </Screen>
  );
}

const styles = StyleSheet.create({
  hero: { gap: 10, marginBottom: 16 },
});
