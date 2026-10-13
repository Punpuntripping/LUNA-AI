import { router, useFocusEffect, useLocalSearchParams } from "expo-router";
import { useCallback, useState } from "react";
import { StyleSheet, View } from "react-native";

import { requestOtp } from "@/api/auth";
import { errorMessage } from "@/api/errors";
import { useSession } from "@/auth/session-store";
import { AppText } from "@/components/AppText";
import { AuthHeader } from "@/components/AuthHeader";
import { Button } from "@/components/Button";
import { CODE_LENGTH, CodeInput } from "@/components/CodeInput";
import { ErrorBanner } from "@/components/ErrorBanner";
import { LinkText } from "@/components/LinkText";
import { Screen } from "@/components/Screen";
import { TextField } from "@/components/TextField";
import { useCooldown } from "@/hooks/use-cooldown";
import { validateEmail } from "@/lib/validation";

const RESEND_COOLDOWN_S = 60;
const MSG_PASSWORD_FALLBACK = "الدخول بالرمز غير متاح لهذا الحساب حالياً — سجّل الدخول بكلمة المرور";

export default function OtpScreen() {
  const params = useLocalSearchParams<{ email?: string }>();
  const loginWithOtp = useSession((s) => s.loginWithOtp);

  const [step, setStep] = useState<"email" | "code">("email");
  const [email, setEmail] = useState(params.email ?? "");
  const [emailError, setEmailError] = useState<string | null>(null);
  const [code, setCode] = useState("");
  const [serverError, setServerError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const cooldown = useCooldown(RESEND_COOLDOWN_S);

  // No stale error banner or half-typed code when the screen is left.
  useFocusEffect(
    useCallback(
      () => () => {
        setEmailError(null);
        setServerError(null);
        setCode("");
      },
      [],
    ),
  );

  const goToPassword = (notice?: string) =>
    router.replace({ pathname: "/login", params: { email: email.trim(), ...(notice ? { notice } : {}) } });

  const send = async () => {
    const err = validateEmail(email);
    setEmailError(err);
    setServerError(null);
    if (err) return;
    setBusy(true);
    try {
      const res = await requestOtp(email.trim());
      if (res.fallback === "password") {
        goToPassword(MSG_PASSWORD_FALLBACK);
        return;
      }
      setStep("code");
      setCode("");
      cooldown.start();
    } catch (e) {
      setServerError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  };

  const verify = async (value: string = code) => {
    if (value.length !== CODE_LENGTH || busy) return;
    setServerError(null);
    setBusy(true);
    try {
      await loginWithOtp(email.trim(), value);
    } catch (e) {
      setServerError(errorMessage(e));
      setCode("");
    } finally {
      setBusy(false);
    }
  };

  if (step === "email") {
    return (
      <Screen centred>
        <AuthHeader title="الدخول برمز عبر البريد" subtitle="سنرسل رمزاً من 6 أرقام إلى بريدك الإلكتروني" />
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
          testID="otp-email"
        />
        <Button label="إرسال الرمز" onPress={send} loading={busy} testID="otp-send" />
        <View style={styles.links}>
          <LinkText label="الدخول بكلمة المرور" onPress={() => goToPassword()} />
        </View>
      </Screen>
    );
  }

  return (
    <Screen centred>
      <AuthHeader title="أدخل الرمز" subtitle={`أرسلنا رمزاً إلى ${email.trim()}`} />
      <ErrorBanner message={serverError} />
      <CodeInput value={code} onChange={setCode} onComplete={verify} error={!!serverError} editable={!busy} testID="otp-code" />
      <Button label="تأكيد" onPress={() => verify()} loading={busy} disabled={code.length !== CODE_LENGTH} testID="otp-verify" />
      <View style={styles.links}>
        {cooldown.active ? (
          <AppText variant="sm" tone="muted">
            {`إعادة الإرسال بعد ${cooldown.secondsLeft} ثانية`}
          </AppText>
        ) : (
          <LinkText label="إعادة إرسال الرمز" onPress={send} disabled={busy} />
        )}
        <LinkText label="تغيير البريد الإلكتروني" onPress={() => setStep("email")} />
        <LinkText label="الدخول بكلمة المرور" onPress={() => goToPassword()} />
      </View>
    </Screen>
  );
}

const styles = StyleSheet.create({
  links: { alignItems: "center", marginTop: 4 },
});
