import { useState } from "react";
import { StyleSheet, View } from "react-native";

import { errorMessage } from "@/api/errors";
import { useSession } from "@/auth/session-store";
import { AppText } from "@/components/AppText";
import { Button } from "@/components/Button";
import { ErrorBanner } from "@/components/ErrorBanner";
import { Screen } from "@/components/Screen";
import { formatDate } from "@/lib/format";

// Blocking screen for an account inside its 30-day deletion grace window
// (user.deletion_pending). Restore, or sign out — nothing else is reachable.
export default function AccountRestoreScreen() {
  const purgeAt = useSession((s) => s.user?.purge_at ?? null);
  const restoreAccount = useSession((s) => s.restoreAccount);
  const logout = useSession((s) => s.logout);
  const [busy, setBusy] = useState<"restore" | "logout" | null>(null);
  const [error, setError] = useState<string | null>(null);

  const purgeDate = purgeAt ? formatDate(purgeAt) : "";

  return (
    <Screen centred>
      <View style={styles.hero}>
        <AppText variant="h2" weight={700} align="center">
          حسابك قيد الحذف
        </AppText>
        <AppText variant="base" tone="secondary" align="center">
          {purgeDate
            ? `سيُحذف حسابك وجميع بياناته نهائياً في ${purgeDate}. يمكنك استعادته قبل هذا التاريخ.`
            : "سيُحذف حسابك وجميع بياناته نهائياً بعد انتهاء مهلة 30 يوماً. يمكنك استعادته قبل ذلك."}
        </AppText>
      </View>
      <ErrorBanner message={error} />
      <Button
        label="استعادة الحساب"
        loading={busy === "restore"}
        disabled={busy !== null}
        onPress={async () => {
          setError(null);
          setBusy("restore");
          try {
            await restoreAccount();
          } catch (e) {
            setError(errorMessage(e));
          } finally {
            setBusy(null);
          }
        }}
        testID="restore-account"
      />
      <Button
        label="تسجيل الخروج"
        variant="ghost"
        loading={busy === "logout"}
        disabled={busy !== null}
        onPress={async () => {
          setBusy("logout");
          await logout();
        }}
        testID="restore-logout"
      />
    </Screen>
  );
}

const styles = StyleSheet.create({
  hero: { gap: 10, marginBottom: 12 },
});
