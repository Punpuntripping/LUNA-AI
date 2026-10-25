import type { NotificationResponse } from "expo-notifications";
import { create } from "zustand";

import { conversationKeys, messageKeys } from "@/chat/keys";
import { streamStore } from "@/chat/stream-store";
import { queryClient } from "@/lib/query-client";

import { conversationIdFromPushData, createOnceGate, foregroundBehavior, routeForPushData, type ConversationRoute } from "./logic";
import { Notifications } from "./notifications";
import { ensureTurnReadyChannel } from "./registration";

// Incoming «إجابتك جاهزة» pushes (plan 06 §3 P5): the foreground decision,
// cache refresh, and taps → `/c/<id>`. Installed once at module scope from
// app/_layout.tsx — iOS wants the response listener registered before the
// first render to catch a tap that launched the app.

/** The chat on screen (set by ChatScreen while focused). */
let visibleConversationId: string | null = null;

export function setVisibleConversation(id: string): void {
  visibleConversationId = id;
}

export function clearVisibleConversation(id: string): void {
  if (visibleConversationId === id) visibleConversationId = null;
}

export function getVisibleConversation(): string | null {
  return visibleConversationId;
}

interface PushTapState {
  /** A tapped push waiting to be opened — survives until signed in. */
  target: ConversationRoute | null;
  setTarget: (route: ConversationRoute) => void;
  clear: () => void;
}

export const usePushTap = create<PushTapState>((set) => ({
  target: null,
  setTarget: (target) => set({ target }),
  clear: () => set({ target: null }),
}));

/** The answer landed: refetch the conversation + the list. A stream this app
 *  is still reading refetches by itself on `done` — leave its cache alone. */
function refreshConversation(id: string): void {
  if (!streamStore.isActive(id)) void queryClient.invalidateQueries({ queryKey: messageKeys.list(id) });
  void queryClient.invalidateQueries({ queryKey: conversationKeys.lists() });
}

const firstTime = createOnceGate();

function onResponse(response: NotificationResponse): void {
  const N = Notifications;
  if (!N || response.actionIdentifier !== N.DEFAULT_ACTION_IDENTIFIER) return;
  if (!firstTime(response.notification.request.identifier)) return;
  try {
    // Handled — a later cold start must not open it again.
    N.clearLastNotificationResponse();
  } catch {
    // Older native module: the once-gate still covers this process.
  }
  const route = routeForPushData(response.notification.request.content.data);
  if (!route) return;
  refreshConversation(route.params.id);
  usePushTap.getState().setTarget(route);
}

let installed = false;

/** Call once, at module scope of the root layout. No-op without the native module. */
export function installPushHandling(): void {
  const N = Notifications;
  if (!N || installed) return;
  installed = true;

  N.setNotificationHandler({
    handleNotification: async (notification) => {
      const id = conversationIdFromPushData(notification.request.content.data);
      if (id) refreshConversation(id);
      return foregroundBehavior(id, visibleConversationId);
    },
  });

  // Warm / background taps (and the cold-start tap on iOS)…
  N.addNotificationResponseReceivedListener(onResponse);
  // …and the tap that launched a killed app (Android delivers it only here).
  try {
    const last = N.getLastNotificationResponse();
    if (last) onResponse(last);
  } catch {
    // Nothing to replay.
  }

  // P2: the channel exists before any token request or permission prompt.
  void ensureTurnReadyChannel();
}
