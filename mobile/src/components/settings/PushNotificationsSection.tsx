import { useFocusEffect } from "expo-router";
import { useCallback, useState } from "react";
import { AppState, Linking, StyleSheet, Switch, View } from "react-native";

import { useSession } from "@/auth/session-store";
import { AppText } from "@/components/AppText";
import { Button } from "@/components/Button";
import { Icon } from "@/components/Icon";
import { disablePush, enablePush, MSG_PUSH_UNSUPPORTED, PushError, readPushStatus, type PushStatus } from "@/push/registration";
import { useTheme } from "@/theme/ThemeProvider";

/**
 * «الإشعارات» — the secondary push opt-in (the primary is the chip on the
 * deep-search tracker); port of the web's PushNotificationsSection in
 * ConversationSettingsDialog. State is PER DEVICE (the OS owns the permission),
 * so it is re-read whenever settings shows and when the app comes back from
 * the device settings.
 */

const LABEL = "نبّهني عند جاهزية الإجابة";
const MSG_FALLBACK = "تعذّر تحديث الإشعارات.";

export function PushNotificationsSection() {
  const { colours, radius } = useTheme();
  const userId = useSession((s) => s.user?.user_id ?? null);
  const [status, setStatus] = useState<PushStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useFocusEffect(
    useCallback(() => {
      let cancelled = false;
      const load = () => {
        void readPushStatus(userId).then((s) => {
          if (!cancelled) setStatus(s);
        });
      };
      setError(null);
      load();
      const sub = AppState.addEventListener("change", (next) => {
        if (next === "active") load();
      });
      return () => {
        cancelled = true;
        sub.remove();
      };
    }, [userId]),
  );

  const handleToggle = async (next: boolean) => {
    if (!userId) return;
    setBusy(true);
    setError(null);
    try {
      if (next) await enablePush(userId);
      else await disablePush();
    } catch (err) {
      setError(err instanceof PushError ? err.message : MSG_FALLBACK);
    } finally {
      setStatus(await readPushStatus(userId));
      setBusy(false);
    }
  };

  const blocked = status?.permission === "blocked";

  return (
    <View style={styles.wrap} testID="push-settings">
      <AppText variant="base" weight={600}>
        الإشعارات
      </AppText>
      <AppText variant="sm" tone="muted">
        ننبّهك على هذا الجهاز حين تجهز إجابة البحث المعمّق، دون أن يتضمن التنبيه أي نص من سؤالك أو الإجابة.
      </AppText>

      {status && !status.supported ? (
        <AppText variant="xs" tone="muted" testID="push-unsupported-hint">
          {MSG_PUSH_UNSUPPORTED}
        </AppText>
      ) : null}

      {status?.supported ? (
        <>
          <View style={[styles.row, { borderColor: colours.border, backgroundColor: colours.surface2, borderRadius: radius.md }]}>
            <Icon name="bellRing" size={16} colour={colours.primary} />
            <AppText variant="sm" weight={500} style={styles.flex}>
              {LABEL}
            </AppText>
            <Switch
              value={status.registered && !blocked}
              onValueChange={(next) => void handleToggle(next)}
              disabled={busy || blocked}
              accessibilityLabel={LABEL}
              trackColor={{ false: colours.surface3, true: colours.primary }}
              thumbColor={colours.surface1}
              ios_backgroundColor={colours.surface3}
              testID="push-switch"
            />
          </View>
          {blocked ? (
            <>
              <AppText variant="xs" tone="muted" testID="push-denied-hint">
                الإشعارات محظورة لهذا التطبيق؛ لتفعيلها اسمح بها من إعدادات الجهاز ثم عد إلى هنا.
              </AppText>
              <Button
                label="فتح إعدادات الجهاز"
                variant="secondary"
                onPress={() => void Linking.openSettings().catch(() => undefined)}
                testID="push-open-settings"
              />
            </>
          ) : null}
        </>
      ) : null}

      {error ? (
        <AppText variant="sm" tone="destructive" accessibilityRole="alert" testID="push-error">
          {error}
        </AppText>
      ) : null}
    </View>
  );
}

const styles = StyleSheet.create({
  wrap: { gap: 8, marginTop: 4 },
  flex: { flex: 1 },
  row: { flexDirection: "row", alignItems: "center", gap: 8, minHeight: 56, paddingHorizontal: 12, paddingVertical: 8, borderWidth: 1 },
});
