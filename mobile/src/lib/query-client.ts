import { focusManager, QueryClient } from "@tanstack/react-query";
import { AppState, type AppStateStatus } from "react-native";

export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 60 * 1000,
      retry: 1,
    },
  },
});

// TanStack's "window focus" = the app returning to the foreground.
focusManager.setEventListener((setFocused) => {
  const sub = AppState.addEventListener("change", (state: AppStateStatus) => {
    setFocused(state === "active");
  });
  return () => sub.remove();
});
