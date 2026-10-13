import { router } from "expo-router";
import { useState } from "react";
import { StyleSheet, View } from "react-native";

import { useSession } from "@/auth/session-store";
import { AppText } from "@/components/AppText";
import { Button } from "@/components/Button";
import { Screen } from "@/components/Screen";
import { UpdateBanner } from "@/components/UpdateBanner";

// Phase 1 placeholder home — the chat lands here in Phase 2.
export default function HomeScreen() {
  const callName = useSession((s) => s.user?.call_name?.trim() || null);
  const logout = useSession((s) => s.logout);
  const [leaving, setLeaving] = useState(false);

  return (
    <Screen centred>
      <UpdateBanner />
      <View style={styles.hero}>
        <AppText variant="h1" weight={700} align="center" testID="home-greeting">
          {callName ? `مرحباً ${callName}` : "مرحباً بك"}
        </AppText>
        <AppText variant="base" tone="muted" align="center">
          المحادثات قادمة قريباً في التطبيق
        </AppText>
      </View>
      <Button label="الإعدادات" variant="secondary" onPress={() => router.push("/settings")} testID="home-settings" />
      <Button
        label="تسجيل الخروج"
        variant="ghost"
        loading={leaving}
        onPress={async () => {
          setLeaving(true);
          await logout();
        }}
        testID="home-logout"
      />
    </Screen>
  );
}

const styles = StyleSheet.create({
  hero: { gap: 8, marginBottom: 16 },
});
