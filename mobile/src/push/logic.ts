import type { TurnReadyPushData } from "@contracts";

// Pure rules of the «إجابتك جاهزة» push (plan 06 §3 P3–P5) — no React Native,
// no expo-notifications, so node:test covers them (`__tests__/push.test.ts`).

/** Android channel the backend sends to (`channelId: "turn-ready"`). */
export const TURN_READY_CHANNEL_ID = "turn-ready";
/** Channel name shown in the system's notification settings. */
export const TURN_READY_CHANNEL_NAME = "إجابات ريحان";

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** `data.conversation_id` of a turn-ready push, or null for anything else.
 *  Only a UUID passes, so a payload can never steer the router elsewhere. */
export function conversationIdFromPushData(data: unknown): string | null {
  if (!data || typeof data !== "object") return null;
  const id = (data as Partial<Record<keyof TurnReadyPushData, unknown>>).conversation_id;
  return typeof id === "string" && UUID_RE.test(id) ? id.toLowerCase() : null;
}

export interface ConversationRoute {
  pathname: "/c/[id]";
  params: { id: string };
}

/** Push payload → the chat route `/c/<conversation_id>` (null = not ours). */
export function routeForPushData(data: unknown): ConversationRoute | null {
  const id = conversationIdFromPushData(data);
  return id ? { pathname: "/c/[id]", params: { id } } : null;
}

/** The last token the backend accepted from this device (SecureStore cache). */
export interface SentPushToken {
  token: string;
  userId: string;
  /** `app_version` sent with it — the server row is stale once it changes. */
  appVersion: string;
}

/** Re-POST only when the token, the account or the app version changed. */
export function shouldRepost(last: SentPushToken | null, next: SentPushToken): boolean {
  if (!last) return true;
  return last.token !== next.token || last.userId !== next.userId || last.appVersion !== next.appVersion;
}

/** Tolerant read of the cached value — anything malformed counts as "never sent". */
export function parseSentPushToken(raw: string | null): SentPushToken | null {
  if (!raw) return null;
  try {
    const v: unknown = JSON.parse(raw);
    if (!v || typeof v !== "object") return null;
    const { token, userId, appVersion } = v as Record<string, unknown>;
    if (typeof token !== "string" || !token || typeof userId !== "string" || !userId) return null;
    return { token, userId, appVersion: typeof appVersion === "string" ? appVersion : "" };
  } catch {
    return null;
  }
}

/** Same fields as expo-notifications' `NotificationBehavior`. */
export interface ForegroundBehavior {
  shouldShowBanner: boolean;
  shouldShowList: boolean;
  shouldPlaySound: boolean;
  shouldSetBadge: boolean;
}

/** App in the foreground: silent when the pushed conversation is the chat on
 *  screen (its answer is already arriving there), else banner + sound. */
export function foregroundBehavior(pushConversationId: string | null, visibleConversationId: string | null): ForegroundBehavior {
  const onScreen = pushConversationId !== null && pushConversationId === visibleConversationId;
  return {
    shouldShowBanner: !onScreen,
    shouldShowList: !onScreen,
    shouldPlaySound: !onScreen,
    shouldSetBadge: false,
  };
}

/**
 * Notification permission, as the opt-in UI needs it:
 * - `granted`  — notifications may be shown;
 * - `askable`  — not granted, but the system prompt can still be shown;
 * - `blocked`  — refused for good: only the device settings can change it.
 */
export type PushPermission = "granted" | "askable" | "blocked";

export function pushPermissionFrom(p: { granted: boolean; canAskAgain: boolean }): PushPermission {
  if (p.granted) return "granted";
  return p.canAskAgain ? "askable" : "blocked";
}

/** «نبّهني عند الجاهزية» shows unless the device already gets the push, the
 *  user refused it on the chip, or the permission is blocked (web parity). */
export function shouldOfferChip(s: { permission: PushPermission; registered: boolean; refused: boolean }): boolean {
  if (s.refused || s.permission === "blocked") return false;
  return !(s.permission === "granted" && s.registered);
}

/** Remembers which notification responses were handled — a tap reaches the app
 *  through the listener AND the "last response", it must route only once. */
export function createOnceGate(max = 50): (key: string) => boolean {
  const seen: string[] = [];
  return (key) => {
    if (seen.includes(key)) return false;
    seen.push(key);
    if (seen.length > max) seen.shift();
    return true;
  };
}
