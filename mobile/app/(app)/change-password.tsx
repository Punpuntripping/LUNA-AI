import { router } from "expo-router";
import { useState } from "react";
import { StyleSheet, View } from "react-native";

import { forgotPassword } from "@/api/auth";
import { errorMessage } from "@/api/errors";
import { useSession } from "@/auth/session-store";
import { AppText } from "@/components/AppText";
import { Button } from "@/components/Button";
import { ErrorBanner } from "@/components/ErrorBanner";
import { MSG_CHECK_SPAM, PasswordCodeForm } from "@/components/PasswordCodeForm";
import { Screen } from "@/components/Screen";
import { useNotice } from "@/stores/notice";
import { useTheme } from "@/theme/ThemeProvider";

// Owner decision 2026-10-13: a password is changed ONLY with an emailed code,
// signed in or not. Same backend routes as «نسيت كلمة المرور»; both calls go
// out WITHOUT the bearer token, so a wrong code (401) never looks like an
// expired session to the api client.

export default function ChangePasswordScreen() {
  const email = useSession((s) => s.user?.email ?? "");
  const changePassword = useSession((s) => s.changePassword);
  const showNotice = useNotice((s) => s.show);

  const [sent, setSent] = useState(false);
  const [serverError, setServerError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const send = async () => {
    if (busy || !email) return;
    setServerError(null);
    setBusy(true);
    try {
      await forgotPassword(email);
      setSent(true);
    } catch (e) {
      setServerError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  };

  const emailBox = <EmailBox email={email} />;

  if (!sent) {
    return (
      <Screen>
        <AppText variant="sm" tone="muted">
          لتغيير كلمة المرور نرسل رمزاً من 6 أرقام إلى بريدك، ثم تختار كلمة مرور جديدة.
        </AppText>
        {emailBox}
        <ErrorBanner message={serverError} />
        <Button label="إرسال رمز إلى بريدك" onPress={send} loading={busy} disabled={!email} testID="change-send" />
        <AppText variant="xs" tone="muted" align="center">
          {MSG_CHECK_SPAM}
        </AppText>
      </Screen>
    );
  }

  return (
    <Screen>
      <PasswordCodeForm
        header={
          <>
            <AppText variant="sm" tone="muted">
              أدخل الرمز المكوّن من 6 أرقام المرسل إلى بريدك
            </AppText>
            {emailBox}
            <AppText variant="xs" tone="muted" align="center">
              {MSG_CHECK_SPAM}
            </AppText>
          </>
        }
        submitLabel="حفظ"
        onSubmit={async (code, password) => {
          await changePassword(code, password);
          showNotice("تم تغيير كلمة المرور");
          router.back();
        }}
        onResend={async () => {
          await forgotPassword(email);
        }}
        testIDPrefix="change"
      />
    </Screen>
  );
}

/** The signed-in address, read-only — the code can only go there. */
function EmailBox({ email }: { email: string }) {
  const { colours, radius } = useTheme();
  return (
    <View style={[styles.email, { backgroundColor: colours.surface2, borderRadius: radius.default }]}>
      <AppText variant="xs" tone="secondary">
        البريد الإلكتروني
      </AppText>
      <AppText variant="base" selectable testID="change-email">
        {email}
      </AppText>
    </View>
  );
}

const styles = StyleSheet.create({
  email: { paddingHorizontal: 12, paddingVertical: 10, gap: 2 },
});
