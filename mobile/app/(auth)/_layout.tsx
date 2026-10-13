import { Stack } from "expo-router";

import { useTheme } from "@/theme/ThemeProvider";

// Login is always the bottom of this stack (back from otp/signup/forgot-password lands on it).
export const unstable_settings = { anchor: "login" };

// Reached only while signed out — the root Stack.Protected guard swaps this
// group for (app) the moment a session is established.
export default function AuthLayout() {
  const { colours } = useTheme();
  return (
    <Stack screenOptions={{ headerShown: false, contentStyle: { backgroundColor: colours.canvas } }}>
      <Stack.Screen name="login" />
      <Stack.Screen name="otp" />
      <Stack.Screen name="signup" />
      <Stack.Screen name="verify-email" />
      <Stack.Screen name="forgot-password" />
      <Stack.Screen name="reset-password" />
    </Stack>
  );
}
