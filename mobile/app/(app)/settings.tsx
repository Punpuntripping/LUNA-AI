import { AR_DATE_LOCALE, AR_NUM_LOCALE } from "@contracts";
import { router, useFocusEffect } from "expo-router";
import { useCallback, useState } from "react";
import { Pressable, StyleSheet, View } from "react-native";

import { useSession } from "@/auth/session-store";
import { AppText } from "@/components/AppText";
import { Button } from "@/components/Button";
import { ErrorBanner } from "@/components/ErrorBanner";
import { LinkText } from "@/components/LinkText";
import { Screen } from "@/components/Screen";
import { DetailLevelControl } from "@/components/settings/DetailLevelControl";
import { PushNotificationsSection } from "@/components/settings/PushNotificationsSection";
import { APP_BUILD, APP_UPDATE_ID_LABEL, APP_VERSION } from "@/lib/app-info";
import { formatDate, formatNumber } from "@/lib/format";
import { useNotice } from "@/stores/notice";
import { useThemePreference, type ThemePreference } from "@/stores/theme-preference";
import { useTheme } from "@/theme/ThemeProvider";

const THEME_OPTIONS: { value: ThemePreference; label: string }[] = [
  { value: "light", label: "فاتح" },
  { value: "dark", label: "داكن" },
  { value: "system", label: "تلقائي" },
];

export default function SettingsScreen() {
  const { colours, radius } = useTheme();
  const preference = useThemePreference((s) => s.preference);
  const setPreference = useThemePreference((s) => s.setPreference);
  const email = useSession((s) => s.user?.email ?? "");
  const logout = useSession((s) => s.logout);
  const [leaving, setLeaving] = useState(false);
  const notice = useNotice((s) => s.notice);
  const clearNotice = useNotice((s) => s.clear);

  // A notice handed over by a sub-screen (e.g. «تم تغيير كلمة المرور») shows
  // until settings loses focus.
  useFocusEffect(useCallback(() => clearNotice, [clearNotice]));

  return (
    <Screen>
      <ErrorBanner message={notice} tone="info" />

      <View style={styles.section}>
        <AppText variant="sm" weight={600} tone="secondary">
          المظهر
        </AppText>
        <View
          accessibilityRole="radiogroup"
          style={[styles.segment, { backgroundColor: colours.surface2, borderRadius: radius.default }]}
        >
          {THEME_OPTIONS.map((opt) => {
            const selected = opt.value === preference;
            return (
              <Pressable
                key={opt.value}
                accessibilityRole="radio"
                accessibilityState={{ selected }}
                onPress={() => setPreference(opt.value)}
                style={[
                  styles.segmentItem,
                  { borderRadius: radius.md, backgroundColor: selected ? colours.surface1 : "transparent" },
                ]}
                testID={`theme-${opt.value}`}
              >
                <AppText variant="sm" weight={selected ? 600 : 400} align="center">
                  {opt.label}
                </AppText>
              </Pressable>
            );
          })}
        </View>
      </View>

      {/* Web «إعدادات المحادثة» (ConversationSettingsDialog) — مستوى التفصيل + الإشعارات. */}
      <View style={styles.section}>
        <AppText variant="sm" weight={600} tone="secondary">
          إعدادات المحادثة
        </AppText>
        <AppText variant="xs" tone="muted">
          خيارات تتحكم في طريقة إجابة ريحان في جميع محادثاتك.
        </AppText>
        <AppText variant="base" weight={600} style={styles.subheading}>
          مستوى التفصيل
        </AppText>
        <DetailLevelControl />
        <PushNotificationsSection />
      </View>

      <View style={styles.section}>
        <AppText variant="sm" weight={600} tone="secondary">
          الحساب
        </AppText>
        <AppText variant="base">{email}</AppText>
        <Button
          label="تغيير كلمة المرور"
          variant="secondary"
          onPress={() => router.push("/change-password")}
          testID="settings-change-password"
        />
      </View>

      <View style={styles.section}>
        <AppText variant="sm" weight={600} tone="secondary">
          عن التطبيق
        </AppText>
        <AppText variant="sm" tone="muted">{`الإصدار ${APP_VERSION} (البناء ${APP_BUILD})`}</AppText>
        <AppText variant="xs" tone="subtle" selectable>{`معرّف التحديث: ${APP_UPDATE_ID_LABEL}`}</AppText>
      </View>

      {__DEV__ ? <DigitsCheck /> : null}
      {__DEV__ ? (
        <LinkText label="Markdown spike" variant="xs" onPress={() => router.push("/dev-markdown")} testID="settings-dev-markdown" />
      ) : null}

      <Button
        label="تسجيل الخروج"
        variant="secondary"
        loading={leaving}
        onPress={async () => {
          setLeaving(true);
          await logout();
        }}
        testID="settings-logout"
      />
    </Screen>
  );
}

/** Dev-only probe (plan T6 🔎): does Hermes Intl honour `-u-nu-latn`? The
 *  «خام» lines bypass the toLatinDigits safety net on purpose. */
function DigitsCheck() {
  const now = new Date();
  const raw = new Intl.DateTimeFormat(AR_DATE_LOCALE, { day: "numeric", month: "long", year: "numeric", calendar: "gregory" }).format(now);
  const rawNum = new Intl.NumberFormat(AR_NUM_LOCALE).format(20182.5);
  return (
    <View style={styles.section}>
      <AppText variant="sm" weight={600} tone="secondary">
        فحص الأرقام (للمطوّر)
      </AppText>
      <AppText variant="sm">{`تاريخ: ${formatDate(now)} · رقم: ${formatNumber(20182.5)}`}</AppText>
      <AppText variant="sm" tone="muted">{`خام: ${raw} · ${rawNum}`}</AppText>
    </View>
  );
}

const styles = StyleSheet.create({
  section: { gap: 6 },
  subheading: { marginTop: 4 },
  segment: { flexDirection: "row", padding: 4, gap: 4 },
  segmentItem: { flex: 1, minHeight: 44, paddingVertical: 8, justifyContent: "center" },
});
