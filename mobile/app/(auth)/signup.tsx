import { router, useFocusEffect } from "expo-router";
import * as WebBrowser from "expo-web-browser";
import { useCallback, useRef, useState } from "react";
import { StyleSheet, View, type TextInput } from "react-native";

import { LEGAL_ROUTES } from "@contracts";

import { ApiError, errorMessage, fallbackForStatus } from "@/api/errors";
import { useSession } from "@/auth/session-store";
import { AppText } from "@/components/AppText";
import { AuthHeader } from "@/components/AuthHeader";
import { Button } from "@/components/Button";
import { Checkbox } from "@/components/Checkbox";
import { ErrorBanner } from "@/components/ErrorBanner";
import { LinkText } from "@/components/LinkText";
import { Screen } from "@/components/Screen";
import { TextField } from "@/components/TextField";
import { WEB_URL } from "@/config/env";
import { MSG_TERMS_REQUIRED, validateEmail, validateFullName, validatePassword } from "@/lib/validation";

const MSG_EMAIL_TAKEN = "البريد الإلكتروني مسجل مسبقاً";

type FieldErrors = { fullName?: string | null; email?: string | null; password?: string | null; terms?: string | null };

function openLegal(path: string) {
  WebBrowser.openBrowserAsync(`${WEB_URL}${path}`).catch(() => undefined);
}

export default function SignupScreen() {
  const signup = useSession((s) => s.signup);

  const [fullName, setFullName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [agreed, setAgreed] = useState(false);
  // Promotional email consent: OFF unless the user ticks it (migration 167).
  const [marketing, setMarketing] = useState(false);
  const [errors, setErrors] = useState<FieldErrors>({});
  const [serverError, setServerError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const emailRef = useRef<TextInput>(null);
  const passwordRef = useRef<TextInput>(null);

  // Back from verify-email / a legal page: no stale errors, password retyped.
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
    const next: FieldErrors = {
      fullName: validateFullName(fullName),
      email: validateEmail(email),
      password: validatePassword(password),
      terms: agreed ? null : MSG_TERMS_REQUIRED,
    };
    setErrors(next);
    setServerError(null);
    if (Object.values(next).some(Boolean)) return;
    setSubmitting(true);
    try {
      const outcome = await signup({ email, password, fullName, marketingOptIn: marketing });
      if (outcome === "verify") {
        router.push({ pathname: "/verify-email", params: { email: email.trim().toLowerCase() } });
      }
    } catch (err) {
      // 409 without its own Arabic text (generic fallback) = duplicate email.
      const genericConflict = err instanceof ApiError && err.status === 409 && err.message === fallbackForStatus(409);
      setServerError(genericConflict ? MSG_EMAIL_TAKEN : errorMessage(err));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Screen>
      <AuthHeader title="إنشاء حساب" />
      <ErrorBanner message={serverError} />
      <TextField
        label="الاسم الكامل"
        value={fullName}
        onChangeText={setFullName}
        error={errors.fullName}
        autoComplete="name"
        textContentType="name"
        returnKeyType="next"
        onSubmitEditing={() => emailRef.current?.focus()}
        testID="signup-name"
      />
      <TextField
        ref={emailRef}
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
        testID="signup-email"
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
        autoComplete="new-password"
        textContentType="newPassword"
        placeholder="8 أحرف على الأقل"
        returnKeyType="done"
        onSubmitEditing={submit}
        testID="signup-password"
      />

      <View style={styles.consents}>
        <Checkbox
          checked={agreed}
          onChange={(v) => {
            setAgreed(v);
            if (v) setErrors((e) => ({ ...e, terms: null }));
          }}
          accessibilityLabel="أوافق على الشروط والأحكام وسياسة الخصوصية"
          testID="signup-terms"
        >
          <View style={styles.inline}>
            <AppText variant="sm">أوافق على</AppText>
            <LinkText label="الشروط والأحكام" onPress={() => openLegal(LEGAL_ROUTES.terms)} />
            <AppText variant="sm">و</AppText>
            <LinkText label="سياسة الخصوصية" onPress={() => openLegal(LEGAL_ROUTES.privacy)} />
          </View>
        </Checkbox>
        {errors.terms ? (
          <AppText variant="xs" tone="destructive">
            {errors.terms}
          </AppText>
        ) : null}
        <Checkbox
          checked={marketing}
          onChange={setMarketing}
          accessibilityLabel="أوافق على استلام محتوى ترويجي وتحديثات عبر البريد الإلكتروني"
          testID="signup-marketing"
        >
          <AppText variant="sm">أوافق على استلام محتوى ترويجي وتحديثات عبر البريد الإلكتروني</AppText>
        </Checkbox>
      </View>

      <Button label="إنشاء حساب" onPress={submit} loading={submitting} disabled={!agreed} testID="signup-submit" />
      <View style={styles.links}>
        <View style={styles.inline}>
          <AppText variant="sm" tone="muted">
            لديك حساب؟
          </AppText>
          <LinkText label="تسجيل الدخول" onPress={() => router.back()} />
        </View>
      </View>
    </Screen>
  );
}

const styles = StyleSheet.create({
  consents: { gap: 12 },
  inline: { flexDirection: "row", flexWrap: "wrap", alignItems: "center", columnGap: 4 },
  links: { alignItems: "center", marginTop: 4 },
});
