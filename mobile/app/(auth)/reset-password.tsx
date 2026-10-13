import { router, useLocalSearchParams } from "expo-router";

import { forgotPassword } from "@/api/auth";
import { useSession } from "@/auth/session-store";
import { AppText } from "@/components/AppText";
import { AuthHeader } from "@/components/AuthHeader";
import { LinkText } from "@/components/LinkText";
import { MSG_CHECK_SPAM, PasswordCodeForm } from "@/components/PasswordCodeForm";
import { Screen } from "@/components/Screen";

export default function ResetPasswordScreen() {
  const { email = "" } = useLocalSearchParams<{ email?: string }>();
  const resetPassword = useSession((s) => s.resetPassword);

  return (
    <Screen>
      <PasswordCodeForm
        header={
          <>
            <AuthHeader title="تعيين كلمة مرور جديدة" subtitle={`أدخل الرمز المكوّن من 6 أرقام المرسل إلى ${email}`} />
            <AppText variant="xs" tone="muted" align="center">
              {MSG_CHECK_SPAM}
            </AppText>
          </>
        }
        submitLabel="حفظ والدخول"
        // The root guard swaps to (app) on success — nothing to navigate here.
        onSubmit={(code, password) => resetPassword(email, code, password)}
        onResend={async () => {
          await forgotPassword(email);
        }}
        links={<LinkText label="تغيير البريد الإلكتروني" onPress={() => router.back()} />}
        testIDPrefix="reset"
      />
    </Screen>
  );
}
