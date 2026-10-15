import { Stack } from "expo-router";

import { useSession } from "@/auth/session-store";
import { StackHeader } from "@/components/StackHeader";
import { useTheme } from "@/theme/ThemeProvider";

export const unstable_settings = { anchor: "index" };

// Signed-in area. An account inside its 30-day deletion grace window sees
// ONLY the blocking restore screen until it is restored or signed out.
// Screens that show a header use StackHeader (RTL in Expo Go and in builds)
// instead of the native one, which lays out LTR when I18nManager isn't RTL.
export default function AppLayout() {
  const { colours } = useTheme();
  const deletionPending = useSession((s) => s.user?.deletion_pending === true);

  return (
    <Stack
      screenOptions={{
        headerShown: false,
        contentStyle: { backgroundColor: colours.canvas },
        header: ({ options, navigation, back }) => (
          <StackHeader
            title={typeof options.title === "string" ? options.title : ""}
            onBack={back ? () => navigation.goBack() : undefined}
          />
        ),
      }}
    >
      <Stack.Protected guard={!deletionPending}>
        <Stack.Screen name="index" />
        <Stack.Screen name="settings" options={{ headerShown: true, title: "الإعدادات" }} />
        <Stack.Screen name="change-password" options={{ headerShown: true, title: "تغيير كلمة المرور" }} />
        {/* K0 spike — dev builds only (the screen redirects home in release). */}
        <Stack.Screen name="dev-markdown" options={{ headerShown: true, title: "Markdown spike" }} />
      </Stack.Protected>
      <Stack.Protected guard={deletionPending}>
        <Stack.Screen name="account-restore" options={{ gestureEnabled: false }} />
      </Stack.Protected>
    </Stack>
  );
}
