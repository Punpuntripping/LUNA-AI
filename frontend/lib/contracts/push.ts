// Native push «إجابتك جاهزة» (mobile Phase 3, `.claude/plans/mobile/06_phase3_push.md`).
// Types only — the web keeps its own Web Push shapes in `lib/push.ts`.

import type { AppPlatform } from "./app-config";

/** `POST /api/v1/push/app-token` body (authed) → 204; 422 on a malformed token.
 *  Idempotent: the same token posted by another account moves to the caller. */
export interface AppPushTokenRequest {
  /** Expo push token (`ExponentPushToken[…]`). A device identifier — never log it. */
  token: string;
  platform: AppPlatform;
  /** Native marketing version (`1.2.3`); null when unknown. */
  app_version?: string | null;
}

/** `DELETE /api/v1/push/app-token` body (authed) → 204, idempotent. */
export interface AppPushTokenDeleteRequest {
  token: string;
}

/** `data` of the «إجابتك جاهزة» push (title «ريحان», body «إجابتك جاهزة»,
 *  Android channel `turn-ready`). Content-free by design: the app builds the
 *  route `/c/<conversation_id>` itself. */
export interface TurnReadyPushData {
  conversation_id: string;
}
