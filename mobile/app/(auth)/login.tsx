import { router, useFocusEffect, useLocalSearchParams } from "expo-router";
import { useCallback, useRef, useState } from "react";
import { StyleSheet, View, type TextInput } from "react-native";

import { errorMessage } from "@/api/errors";
import { useSession } from "@/auth/session-store";
import { AppText } from "@/components/AppText";
import { AuthHeader } from "@/components/AuthHeader";
import { Button } from "@/components/Button";
import { ErrorBanner } from "@/components/ErrorBanner";
import { LinkText } from "@/components/LinkText";
import { Screen } from "@/components/Screen";
import { TextField } from "@/components/TextField";
import { validateEmail } from "@/lib/validation";

export default function LoginScreen() {
  const params = useLocalSearchParams<{ email?: string; notice?: string }>();
  const login = useSession((s) => s.login);

  const [email, setEmail] = useState(params.email ?? "");
  const [password, setPassword] = useState("");
  const [errors, setErrors] = useState<{ email?: string | null; password?: string | null }>({});
  const [serverError, setServerError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const passwordRef = useRef<TextInput>(null);

  // Leaving the screen (to otp / signup / forgot-password) drops errors and the password, so
  // coming back shows a clean form; the typed email is kept.
  useFocusEffect(
    useCallback(
      () => () => {
        setErrors({});
        setServerError(null);
        setPassword("");
      },
      [],
    ),
  );

  const submit = async () => {
    const next = { email: validateEmail(email), password: password ? null : "كلمة المرور مطلوبة" };
    setErrors(next);
    setServerError(null);
    if (next.email || next.password) return;
    setSubmitting(true);
    try {
      await login(email, password);
      // The root guard swaps to (app) — nothing to navigate here.
    } catch (err) {
      setServerError(errorMessage(err));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Screen centred>
      <AuthHeader title="تسجيل الدخول" />
      <ErrorBanner message={params.notice ?? null} tone="info" />
      <ErrorBanner message={serverError} />
      <TextField
        label="البريد الإلكتروني"
        value={email}
        onChangeText={setEmail}
        error={errors.email}
        ltr
        autoCapitalize="none"
        autoCorrect={false}
        autoComplete="email"
        keyboardType="email-address"
        textContentType="emailAddress"
        returnKeyType="next"
        onSubmitEditing={() => passwordRef.current?.focus()}
        testID="login-email"
      />
      <TextField
        ref={passwordRef}
        label="كلمة المرور"
        value={password}
        onChangeText={setPassword}
        error={errors.password}
        ltr
        secureTextEntry
        autoCapitalize="none"
        autoComplete="current-password"
        textContentType="password"
        returnKeyType="go"
        onSubmitEditing={submit}
        testID="login-password"
      />
      <View style={styles.forgot}>
        <LinkText
          label="نسيت كلمة المرور؟"
          onPress={() =>
            router.push({ pathname: "/forgot-password", params: email.trim() ? { email: email.trim() } : {} })
          }
          testID="login-forgot"
        />
      </View>
      <Button label="تسجيل الدخول" onPress={submit} loading={submitting} testID="login-submit" />
      <View style={styles.links}>
        <LinkText
          label="الدخول برمز عبر البريد"
          onPress={() => router.push({ pathname: "/otp", params: email.trim() ? { email: email.trim() } : {} })}
        />
        <View style={styles.row}>
          <AppText variant="sm" tone="muted">
            ليس لديك حساب؟
          </AppText>
          <LinkText label="إنشاء حساب" onPress={() => router.push("/signup")} />
        </View>
      </View>
    </Screen>
  );
}

const styles = StyleSheet.create({
  // Hugs the start edge (right in RTL) under the password field.
  forgot: { alignItems: "flex-start", marginTop: -8 },
  links: { alignItems: "center", marginTop: 4 },
  row: { flexDirection: "row", alignItems: "center", gap: 6 },
});
