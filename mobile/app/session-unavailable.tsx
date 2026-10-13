import { useState } from "react";
import { StyleSheet, View } from "react-native";

import { useSession } from "@/auth/session-store";
import { AppText } from "@/components/AppText";
import { Button } from "@/components/Button";
import { Screen } from "@/components/Screen";

// Cold start found a saved session but could not reach the server (offline,
// 503). The session is KEPT — retrying usually just works; signing out is
// offered for a phone that will stay offline.
export default function SessionUnavailableScreen() {
  const message = useSession((s) => s.unavailableMessage);
  const bootstrap = useSession((s) => s.bootstrap);
  const logout = useSession((s) => s.logout);
  const [retrying, setRetrying] = useState(false);

  return (
    <Screen centred scroll={false}>
      <View style={styles.hero}>
        <AppText variant="h2" weight={700} align="center">
          تعذّر الاتصال
        </AppText>
        <AppText variant="base" tone="secondary" align="center">
          {message ?? "تعذّر الاتصال بالخادم — تحقق من اتصالك"}
        </AppText>
      </View>
      <Button
        label="إعادة المحاولة"
        loading={retrying}
        onPress={async () => {
          setRetrying(true);
          await bootstrap();
          setRetrying(false);
        }}
        testID="unavailable-retry"
      />
      <Button label="تسجيل الخروج" variant="ghost" onPress={() => logout()} testID="unavailable-logout" />
    </Screen>
  );
}

const styles = StyleSheet.create({
  hero: { gap: 10, marginBottom: 16 },
});
