import { router, useFocusEffect, useLocalSearchParams } from "expo-router";
import { useCallback, useState } from "react";
import { StyleSheet, View } from "react-native";

import { resendSignup } from "@/api/auth";
import { errorMessage } from "@/api/errors";
import { useSession } from "@/auth/session-store";
import { AppText } from "@/components/AppText";
import { AuthHeader } from "@/components/AuthHeader";
import { Button } from "@/components/Button";
import { CODE_LENGTH, CodeInput } from "@/components/CodeInput";
import { ErrorBanner } from "@/components/ErrorBanner";
import { LinkText } from "@/components/LinkText";
import { Screen } from "@/components/Screen";
import { useCooldown } from "@/hooks/use-cooldown";

const RESEND_COOLDOWN_S = 60;

export default function VerifyEmailScreen() {
  const { email = "" } = useLocalSearchParams<{ email?: string }>();
  const verifySignup = useSession((s) => s.verifySignup);

  const [code, setCode] = useState("");
  const [serverError, setServerError] = useState<string | null>(null);
  const [info, setInfo] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  // The signup call just sent a code — start in cooldown.
  const cooldown = useCooldown(RESEND_COOLDOWN_S, true);

  useFocusEffect(
    useCallback(
      () => () => {
        setServerError(null);
        setInfo(null);
        setCode("");
      },
      [],
    ),
  );

  const verify = async (value: string = code) => {
    if (value.length !== CODE_LENGTH || busy) return;
    setServerError(null);
    setInfo(null);
    setBusy(true);
    try {
      await verifySignup(email, value);
    } catch (e) {
      setServerError(errorMessage(e));
      setCode("");
    } finally {
      setBusy(false);
    }
  };

  const resend = async () => {
    setServerError(null);
    setInfo(null);
    setBusy(true);
    try {
      await resendSignup(email);
      setInfo("أرسلنا رمزاً جديداً إلى بريدك");
      cooldown.start();
    } catch (e) {
      setServerError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Screen centred>
      <AuthHeader title="تأكيد البريد الإلكتروني" subtitle={`أدخل الرمز المكوّن من 6 أرقام المرسل إلى ${email}`} />
      <ErrorBanner message={serverError} />
      <ErrorBanner message={info} tone="info" />
      <CodeInput value={code} onChange={setCode} onComplete={verify} error={!!serverError} editable={!busy} testID="verify-code" />
      <Button label="تأكيد" onPress={() => verify()} loading={busy} disabled={code.length !== CODE_LENGTH} testID="verify-submit" />
      <View style={styles.links}>
        {cooldown.active ? (
          <AppText variant="sm" tone="muted">
            {`إعادة الإرسال بعد ${cooldown.secondsLeft} ثانية`}
          </AppText>
        ) : (
          <LinkText label="إعادة إرسال الرمز" onPress={resend} disabled={busy} />
        )}
        <LinkText label="تعديل البيانات" onPress={() => router.back()} />
      </View>
    </Screen>
  );
}

const styles = StyleSheet.create({
  links: { alignItems: "center", marginTop: 4 },
});
