import { useFocusEffect } from "expo-router";
import { useCallback, useRef, useState, type ReactNode } from "react";
import { StyleSheet, View, type TextInput } from "react-native";

import { ApiError, errorMessage, fallbackForStatus } from "@/api/errors";
import { useCooldown } from "@/hooks/use-cooldown";
import { validatePassword, validatePasswordConfirm } from "@/lib/validation";

import { AppText } from "./AppText";
import { Button } from "./Button";
import { CODE_LENGTH, CodeInput } from "./CodeInput";
import { ErrorBanner } from "./ErrorBanner";
import { LinkText } from "./LinkText";
import { TextField } from "./TextField";

const RESEND_COOLDOWN_S = 60;
export const MSG_CHECK_SPAM = "تحقّق من مجلد البريد غير المرغوب فيه إن لم تجده";
const MSG_CODE_REQUIRED = "أدخل الرمز المكوّن من 6 أرقام";
const MSG_CODE_INVALID = "الرمز غير صحيح أو منتهي الصلاحية";
const MSG_PASSWORD_REJECTED = "كلمة المرور غير مقبولة، اختر كلمة مرور أقوى";

type FieldErrors = { code?: string | null; password?: string | null; confirm?: string | null };

/** Backend errors without their own Arabic text get a reset-specific one
 *  (the generic 401 fallback reads «انتهت الجلسة», which is wrong here). */
function resetErrorMessage(err: unknown): string {
  if (err instanceof ApiError && err.kind === "http" && err.message === fallbackForStatus(err.status)) {
    if (err.status === 401) return MSG_CODE_INVALID;
    if (err.status === 400) return MSG_PASSWORD_REJECTED;
  }
  return errorMessage(err);
}

export interface PasswordCodeFormProps {
  /** Rendered first (title / where the code went / spam hint). */
  header: ReactNode;
  submitLabel: string;
  /** POST /auth/password/reset via the session store. Throws ApiError. */
  onSubmit: (code: string, password: string) => Promise<void>;
  /** POST /auth/password/forgot again. Throws ApiError. */
  onResend: () => Promise<void>;
  /** Extra links under «إعادة إرسال الرمز». */
  links?: ReactNode;
  /** testID prefix — `${prefix}-code`, `-password`, `-confirm`, `-submit`. */
  testIDPrefix: string;
}

/**
 * Emailed 6-digit code + new password + confirmation — the one form behind
 * «نسيت كلمة المرور» (signed out) and «تغيير كلمة المرور» (signed in). The
 * code was just sent when this mounts, so the resend link starts in cooldown.
 * 401 (wrong/expired code) goes under the boxes; everything else is a banner.
 */
export function PasswordCodeForm({ header, submitLabel, onSubmit, onResend, links, testIDPrefix }: PasswordCodeFormProps) {
  const [code, setCode] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [errors, setErrors] = useState<FieldErrors>({});
  const [serverError, setServerError] = useState<string | null>(null);
  const [info, setInfo] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const passwordRef = useRef<TextInput>(null);
  const confirmRef = useRef<TextInput>(null);
  const cooldown = useCooldown(RESEND_COOLDOWN_S, true);

  // Leaving the screen drops the code, both passwords and every message.
  useFocusEffect(
    useCallback(
      () => () => {
        setErrors({});
        setServerError(null);
        setInfo(null);
        setCode("");
        setPassword("");
        setConfirm("");
      },
      [],
    ),
  );

  const submit = async () => {
    if (busy) return;
    const next: FieldErrors = {
      code: code.length === CODE_LENGTH ? null : MSG_CODE_REQUIRED,
      password: validatePassword(password),
      confirm: validatePasswordConfirm(password, confirm),
    };
    setErrors(next);
    setServerError(null);
    setInfo(null);
    if (Object.values(next).some(Boolean)) return;
    setBusy(true);
    try {
      await onSubmit(code, password);
    } catch (e) {
      if (e instanceof ApiError && e.kind === "http" && e.status === 401) {
        // Wrong or expired code: the message goes under the boxes, retype it.
        setErrors({ code: resetErrorMessage(e) });
        setCode("");
      } else {
        setServerError(resetErrorMessage(e));
      }
    } finally {
      setBusy(false);
    }
  };

  const resend = async () => {
    setServerError(null);
    setInfo(null);
    setBusy(true);
    try {
      await onResend();
      setCode("");
      setInfo("أرسلنا رمزاً جديداً إلى بريدك");
      cooldown.start();
    } catch (e) {
      setServerError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      {header}
      <ErrorBanner message={serverError} />
      <ErrorBanner message={info} tone="info" />
      <View style={styles.code}>
        <CodeInput
          value={code}
          onChange={(v) => {
            setCode(v);
            if (errors.code) setErrors((e) => ({ ...e, code: null }));
          }}
          // The code alone does not finish the form — move on to the password.
          onComplete={() => passwordRef.current?.focus()}
          error={!!errors.code}
          editable={!busy}
          testID={`${testIDPrefix}-code`}
        />
        {errors.code ? (
          <AppText variant="xs" tone="destructive" align="center">
            {errors.code}
          </AppText>
        ) : null}
      </View>
      <TextField
        ref={passwordRef}
        label="كلمة المرور الجديدة"
        value={password}
        onChangeText={setPassword}
        error={errors.password}
        ltr
        secureTextEntry
        autoCapitalize="none"
        autoComplete="new-password"
        textContentType="newPassword"
        placeholder="8 أحرف على الأقل"
        returnKeyType="next"
        onSubmitEditing={() => confirmRef.current?.focus()}
        testID={`${testIDPrefix}-password`}
      />
      <TextField
        ref={confirmRef}
        label="تأكيد كلمة المرور"
        value={confirm}
        onChangeText={setConfirm}
        error={errors.confirm}
        ltr
        secureTextEntry
        autoCapitalize="none"
        autoComplete="new-password"
        textContentType="newPassword"
        returnKeyType="go"
        onSubmitEditing={submit}
        testID={`${testIDPrefix}-confirm`}
      />
      <Button label={submitLabel} onPress={submit} loading={busy} testID={`${testIDPrefix}-submit`} />
      <View style={styles.links}>
        {cooldown.active ? (
          <AppText variant="sm" tone="muted">
            {`إعادة الإرسال بعد ${cooldown.secondsLeft} ثانية`}
          </AppText>
        ) : (
          <LinkText label="إعادة إرسال الرمز" onPress={resend} disabled={busy} />
        )}
        {links}
      </View>
    </>
  );
}

const styles = StyleSheet.create({
  code: { gap: 6 },
  links: { alignItems: "center", marginTop: 4 },
});
