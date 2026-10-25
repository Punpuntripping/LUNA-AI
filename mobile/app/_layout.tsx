import { QueryClientProvider } from "@tanstack/react-query";
import { useFonts } from "expo-font";
import { Stack } from "expo-router";
import * as SplashScreen from "expo-splash-screen";
import { StatusBar } from "expo-status-bar";
import { useEffect } from "react";
import { AppState, I18nManager, StyleSheet, View } from "react-native";

import { AppText } from "@/components/AppText";
import { useAppConfigGate } from "@/hooks/use-app-config-gate";
import { useSession } from "@/auth/session-store";
import { queryClient } from "@/lib/query-client";
import { initSentry, wrapRoot } from "@/observability/sentry";
import { installPushHandling } from "@/push/handling";
import { useUpdateGate } from "@/stores/update-gate";
import { fontMap } from "@/theme/fonts";
import { ThemeProvider, useTheme } from "@/theme/ThemeProvider";

initSentry();
// Push «إجابتك جاهزة»: foreground handler, tap listener (before the first
// render — a tap that launched the app must not be missed) and the Android
// channel. Never asks for permission.
installPushHandling();

// Arabic-only app: RTL always. The expo-localization plugin (app.json) sets
// this natively in builds; these calls cover the JS side. Expo Go on SDK 57
// keeps RTL off regardless — see the dev banner below.
I18nManager.allowRTL(true);
I18nManager.forceRTL(true);

SplashScreen.preventAutoHideAsync().catch(() => undefined);

export default wrapRoot(RootLayout);

function RootLayout() {
  const [fontsLoaded, fontError] = useFonts(fontMap);
  const status = useSession((s) => s.status);
  const bootstrap = useSession((s) => s.bootstrap);

  useEffect(() => {
    bootstrap();
  }, [bootstrap]);

  const ready = (fontsLoaded || fontError !== null) && status !== "booting";

  useEffect(() => {
    if (ready) SplashScreen.hide();
  }, [ready]);

  if (!ready) return null;

  return (
    <QueryClientProvider client={queryClient}>
      <ThemeProvider>
        <RootNavigator />
      </ThemeProvider>
    </QueryClientProvider>
  );
}

function RootNavigator() {
  const { colours, scheme } = useTheme();
  const status = useSession((s) => s.status);
  const resume = useSession((s) => s.resume);
  const updateRequired = useUpdateGate((s) => s.required);

  useAppConfigGate();

  useEffect(() => {
    const sub = AppState.addEventListener("change", (state) => {
      if (state === "active") resume();
    });
    return () => sub.remove();
  }, [resume]);

  return (
    <View style={[styles.root, { backgroundColor: colours.canvas }]}>
      <StatusBar style={scheme === "dark" ? "light" : "dark"} />
      <Stack screenOptions={{ headerShown: false, contentStyle: { backgroundColor: colours.canvas } }}>
        <Stack.Protected guard={updateRequired}>
          <Stack.Screen name="update-required" options={{ gestureEnabled: false }} />
        </Stack.Protected>
        <Stack.Protected guard={!updateRequired && status === "unavailable"}>
          <Stack.Screen name="session-unavailable" options={{ gestureEnabled: false }} />
        </Stack.Protected>
        <Stack.Protected guard={!updateRequired && status === "signedIn"}>
          <Stack.Screen name="(app)" />
        </Stack.Protected>
        <Stack.Protected guard={!updateRequired && status === "signedOut"}>
          <Stack.Screen name="(auth)" />
        </Stack.Protected>
      </Stack>
      {__DEV__ && !I18nManager.isRTL ? (
        <View style={styles.devBanner} pointerEvents="none">
          <AppText variant="xs" weight={600} align="center" style={styles.devBannerText}>
            تنبيه للمطوّر: I18nManager ليس RTL (متوقع في Expo Go) — التخطيط معكوس عبر direction
          </AppText>
        </View>
      ) : null}
    </View>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, direction: "rtl" },
  devBanner: { position: "absolute", bottom: 0, start: 0, end: 0, backgroundColor: "#B91C1C", paddingVertical: 2 },
  devBannerText: { color: "#FFFFFF" },
});
