import { router, useFocusEffect, useLocalSearchParams } from "expo-router";
import { useCallback, useState } from "react";
import { StyleSheet, View } from "react-native";

import { forgotPassword } from "@/api/auth";
import { errorMessage } from "@/api/errors";
import { AppText } from "@/components/AppText";
import { AuthHeader } from "@/components/AuthHeader";
import { Button } from "@/components/Button";
import { ErrorBanner } from "@/components/ErrorBanner";
import { LinkText } from "@/components/LinkText";
import { Screen } from "@/components/Screen";
import { TextField } from "@/components/TextField";
import { validateEmail } from "@/lib/validation";

const MSG_CHECK_SPAM = "تحقّق من مجلد البريد غير المرغوب فيه إن لم تجده";

export default function ForgotPasswordScreen() {
  const params = useLocalSearchParams<{ email?: string }>();

  const [email, setEmail] = useState(params.email ?? "");
  const [emailError, setEmailError] = useState<string | null>(null);
  const [serverError, setServerError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  // Back from reset-password: no stale errors; the typed email is kept.
  useFocusEffect(
    useCallback(
      () => () => {
        setEmailError(null);
        setServerError(null);
      },
      [],
    ),
  );

  const send = async () => {
    const err = validateEmail(email);
    setEmailError(err);
    setServerError(null);
    if (err) return;
    setBusy(true);
    try {
      const normalised = email.trim().toLowerCase();
      await forgotPassword(normalised);
      router.push({ pathname: "/reset-password", params: { email: normalised } });
    } catch (e) {
      setServerError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Screen centred>
      <AuthHeader title="نسيت كلمة المرور" />
      <AppText variant="sm" tone="muted">
        سنرسل رمزاً من 6 أرقام إلى بريدك لتعيين كلمة مرور جديدة. إذا كنت تدخل عادةً بحساب Google، يمكنك بهذه
        الطريقة إنشاء كلمة مرور للدخول من التطبيق.
      </AppText>
      <ErrorBanner message={serverError} />
      <TextField
        label="البريد الإلكتروني"
        value={email}
        onChangeText={setEmail}
        error={emailError}
        ltr
        autoCapitalize="none"
        autoCorrect={false}
        autoComplete="email"
        keyboardType="email-address"
        textContentType="emailAddress"
        returnKeyType="send"
        onSubmitEditing={send}
        testID="forgot-email"
      />
      <Button label="إرسال الرمز" onPress={send} loading={busy} testID="forgot-send" />
      <AppText variant="xs" tone="muted" align="center">
        {MSG_CHECK_SPAM}
      </AppText>
      <View style={styles.links}>
        <LinkText label="العودة إلى تسجيل الدخول" onPress={() => router.back()} />
      </View>
    </Screen>
  );
}

const styles = StyleSheet.create({
  links: { alignItems: "center", marginTop: 4 },
});
