import { Stack } from "expo-router";
import { KeyboardProvider } from "react-native-keyboard-controller";

import { useSession } from "@/auth/session-store";
import { useChatResumeOnForeground } from "@/chat/use-messages";
import { AnnouncementPopupHost } from "@/components/announcements/AnnouncementPopupHost";
import { ProfessionPromptHost } from "@/components/profession/ProfessionPromptHost";
import { StackHeader } from "@/components/StackHeader";
import { ToastHost } from "@/components/ToastHost";
import { PushRuntime } from "@/push/PushRuntime";
import { useTheme } from "@/theme/ThemeProvider";

export const unstable_settings = { anchor: "index" };

// Signed-in area. An account inside its 30-day deletion grace window sees
// ONLY the blocking restore screen until it is restored or signed out.
// Screens that show a header use StackHeader (RTL in Expo Go and in builds)
// instead of the native one, which lays out LTR when I18nManager isn't RTL.
// The chat screens draw their own header (they need a «…» menu).
//
// KeyboardProvider (react-native-keyboard-controller — native module, needs a
// dev build) drives the chat composer + list; useChatResumeOnForeground
// recovers streams the OS killed while the app was in the background (once).
// PushRuntime: push registration + opening a tapped «إجابتك جاهزة» (Phase 3).
// AnnouncementPopupHost: unread «رسائل ريحان» pop up one at a time (not on
// the blocking restore screen). ProfessionPromptHost: «وش أقرب وصف لك؟»
// (web OnboardingDialog) — it claims the screen before the messages do.
export default function AppLayout() {
  const { colours } = useTheme();
  const deletionPending = useSession((s) => s.user?.deletion_pending === true);
  useChatResumeOnForeground();

  return (
    <KeyboardProvider>
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
          <Stack.Screen name="c/new" />
          <Stack.Screen name="c/[id]" />
          {/* «مساحة العمل» + one item (2b) — they draw their own header (WI-n badge). */}
          <Stack.Screen name="workspace/[conversationId]" />
          <Stack.Screen name="workspace/item/[itemId]" />
          {/* Source reader (citation `[n]` taps) — owned by the source module; it sets its own title. */}
          <Stack.Screen name="source/[itemId]/[n]" options={{ headerShown: true, title: "المصدر" }} />
          <Stack.Screen name="settings" options={{ headerShown: true, title: "الإعدادات" }} />
          <Stack.Screen name="change-password" options={{ headerShown: true, title: "تغيير كلمة المرور" }} />
          <Stack.Screen name="messages" options={{ headerShown: true, title: "رسائل ريحان" }} />
          {/* K0 spike — dev builds only (the screen redirects home in release). */}
          <Stack.Screen name="dev-markdown" options={{ headerShown: true, title: "Markdown spike" }} />
        </Stack.Protected>
        <Stack.Protected guard={deletionPending}>
          <Stack.Screen name="account-restore" options={{ gestureEnabled: false }} />
        </Stack.Protected>
      </Stack>
      <ToastHost />
      <PushRuntime />
      {deletionPending ? null : <ProfessionPromptHost />}
      {deletionPending ? null : <AnnouncementPopupHost />}
    </KeyboardProvider>
  );
}
