"use client";

import { z } from "zod";

import { api, ApiClientError } from "@/lib/api";
import { track } from "@/lib/analytics/client";
import { detectInstallPlatform, isStandalone } from "@/lib/install-app";

/**
 * Web push «إجابتك جاهزة» — the client half (`.claude/plans/pwa_step1.md` §1D).
 *
 * A deep search runs for minutes; the user locks the phone, the pipeline keeps
 * going server-side, and a push brings them back when the answer lands. This
 * module owns the browser side: support detection, the permission prompt, the
 * PushManager subscription and its registration with the backend
 * (`/push/subscribe`). The `push` / `notificationclick` handlers live in
 * `public/sw.js`.
 *
 * Rules the callers must respect:
 *   - `enablePush()` MUST run from a user tap. iOS rejects a permission prompt
 *     that is not user-initiated, so the prompt is the FIRST thing it does —
 *     nothing is awaited before `Notification.requestPermission()`.
 *   - Never prompt on load. The two entry points are the progress-bar chip and
 *     the «الإشعارات» row in إعدادات المحادثة.
 *   - iOS delivers push only to the INSTALLED app (16.4+). In Safari proper the
 *     answer is "install first", not "unsupported" — see `pushSupport()`.
 */

export type PushSupport = "supported" | "needs_install" | "unsupported";

/** Browser permission, or "unsupported" where the Notification API is absent. */
export type PushPermission = NotificationPermission | "unsupported";

/** Where the opt-in came from — rides on the analytics events. */
export type PushSource = "chip" | "settings";

/**
 * Outcome of `enablePush()` that is NOT an error:
 *   - `subscribed` — permission granted and the backend has the subscription.
 *   - `denied`     — the user blocked notifications (sticky until they change it
 *                    in the device/browser settings).
 *   - `dismissed`  — the prompt was closed without a choice; asking again later
 *                    is allowed.
 */
export type EnablePushResult = "subscribed" | "denied" | "dismissed";

/** Failures carry an Arabic message fit to show the user as-is. */
export class PushError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "PushError";
  }
}

const MSG_UNSUPPORTED = "الإشعارات غير مدعومة في هذا المتصفح.";
const MSG_NEEDS_INSTALL = "ثبّت ريحان على الشاشة الرئيسية أولًا لتصلك الإشعارات.";
const MSG_NO_WORKER = "تعذّر تجهيز الإشعارات على هذا الجهاز. أعد تحميل الصفحة وحاول مجددًا.";
const MSG_UNCONFIGURED = "الإشعارات غير متاحة حاليًا. حاول لاحقًا.";
const MSG_FAILED = "تعذّر تفعيل الإشعارات. حاول مجددًا.";
const MSG_DISABLE_FAILED = "تعذّر إيقاف الإشعارات. حاول مجددًا.";

/**
 * How long to wait for a service-worker registration. `navigator.serviceWorker
 * .ready` never settles when no worker is registered (dev builds skip the
 * registrar), so an unbounded await would hang the chip forever.
 */
const SW_READY_TIMEOUT_MS = 10_000;

// -----------------------------------------------
// Backend contract
// -----------------------------------------------

const vapidKeySchema = z.object({
  public_key: z.string().min(1, "مفتاح الإشعارات غير صالح"),
});

interface PushSubscribeBody {
  endpoint: string;
  keys: { p256dh: string; auth: string };
}

const pushApi = {
  vapidPublicKey: () => api.get<unknown>("/push/vapid-public-key"),
  subscribe: (body: PushSubscribeBody) =>
    api.post<unknown>("/push/subscribe", body),
  unsubscribe: (endpoint: string) =>
    api.delete<unknown>("/push/subscribe", { endpoint }),
};

// The VAPID key is fixed per deployment — fetch it once per page.
let vapidKeyPromise: Promise<string> | null = null;

function getVapidPublicKey(): Promise<string> {
  if (!vapidKeyPromise) {
    vapidKeyPromise = (async () => {
      try {
        const parsed = vapidKeySchema.safeParse(await pushApi.vapidPublicKey());
        if (!parsed.success) throw new PushError(MSG_UNCONFIGURED);
        return parsed.data.public_key;
      } catch (err) {
        // 503 = VAPID keys not set on the backend.
        if (err instanceof PushError) throw err;
        if (err instanceof ApiClientError && err.status === 503) {
          throw new PushError(MSG_UNCONFIGURED);
        }
        throw new PushError(MSG_FAILED);
      }
    })();
    // Don't pin a failure for the life of the page — the next tap retries.
    vapidKeyPromise.catch(() => {
      vapidKeyPromise = null;
    });
  }
  return vapidKeyPromise;
}

// -----------------------------------------------
// Helpers
// -----------------------------------------------

/**
 * The VAPID public key arrives base64url-encoded; PushManager wants raw bytes.
 * Backed by a plain ArrayBuffer so it satisfies `BufferSource` in every TS lib.
 */
export function urlBase64ToUint8Array(base64String: string): Uint8Array<ArrayBuffer> {
  const padding = "=".repeat((4 - (base64String.length % 4)) % 4);
  const base64 = (base64String + padding).replace(/-/g, "+").replace(/_/g, "/");
  const raw = window.atob(base64);
  const bytes = new Uint8Array(new ArrayBuffer(raw.length));
  for (let i = 0; i < raw.length; i += 1) bytes[i] = raw.charCodeAt(i);
  return bytes;
}

function sameKey(a: ArrayBuffer | null | undefined, b: Uint8Array): boolean {
  if (!a) return false;
  const view = new Uint8Array(a);
  if (view.length !== b.length) return false;
  for (let i = 0; i < view.length; i += 1) if (view[i] !== b[i]) return false;
  return true;
}

function hasPushApis(): boolean {
  return (
    typeof window !== "undefined" &&
    "Notification" in window &&
    "PushManager" in window &&
    "serviceWorker" in navigator
  );
}

/** The active registration, or null — never hangs (see SW_READY_TIMEOUT_MS). */
async function getRegistration(
  waitMs: number,
): Promise<ServiceWorkerRegistration | null> {
  const existing = await navigator.serviceWorker.getRegistration();
  if (existing?.active) return existing;
  return Promise.race([
    navigator.serviceWorker.ready,
    new Promise<null>((resolve) => window.setTimeout(() => resolve(null), waitMs)),
  ]);
}

// -----------------------------------------------
// Public API
// -----------------------------------------------

/**
 * Can this device receive push?
 *   - `needs_install` — iPhone/iPad in the browser: push exists only for the
 *     home-screen app, so the answer is the install guide.
 *   - `unsupported`   — no Push API (old browser, iOS < 16.4 even installed).
 */
export function pushSupport(): PushSupport {
  if (typeof window === "undefined") return "unsupported";
  if (detectInstallPlatform() === "ios" && !isStandalone()) return "needs_install";
  return hasPushApis() ? "supported" : "unsupported";
}

export function currentPermission(): PushPermission {
  if (typeof window === "undefined" || !("Notification" in window)) {
    return "unsupported";
  }
  return Notification.permission;
}

/** True when this browser holds a live push subscription. */
export async function isSubscribed(): Promise<boolean> {
  if (!hasPushApis() || Notification.permission !== "granted") return false;
  try {
    const reg = await navigator.serviceWorker.getRegistration();
    if (!reg) return false;
    return (await reg.pushManager.getSubscription()) !== null;
  } catch {
    return false;
  }
}

/**
 * Ask for permission, subscribe, register with the backend.
 * Call ONLY from a click/tap handler. Throws `PushError` (Arabic message).
 */
export async function enablePush(source: PushSource): Promise<EnablePushResult> {
  const support = pushSupport();
  if (support === "needs_install") throw new PushError(MSG_NEEDS_INSTALL);
  if (support === "unsupported") throw new PushError(MSG_UNSUPPORTED);

  // The prompt goes FIRST, before any await — iOS drops the user-gesture
  // context across an async hop and would refuse to show it.
  let permission = Notification.permission;
  if (permission === "default") {
    permission = await Notification.requestPermission();
    track("push_permission", { result: permission, source });
  }
  if (permission === "denied") return "denied";
  if (permission !== "granted") return "dismissed";

  try {
    const [reg, vapidKey] = await Promise.all([
      getRegistration(SW_READY_TIMEOUT_MS),
      getVapidPublicKey(),
    ]);
    if (!reg) throw new PushError(MSG_NO_WORKER);

    const applicationServerKey = urlBase64ToUint8Array(vapidKey);
    let sub = await reg.pushManager.getSubscription();
    // A subscription minted under a rotated VAPID key can't be sent to — drop
    // it and mint a fresh one.
    if (sub && !sameKey(sub.options.applicationServerKey, applicationServerKey)) {
      await sub.unsubscribe();
      sub = null;
    }
    if (!sub) {
      sub = await reg.pushManager.subscribe({
        userVisibleOnly: true,
        applicationServerKey,
      });
    }

    const json = sub.toJSON();
    const p256dh = json.keys?.p256dh;
    const auth = json.keys?.auth;
    if (!json.endpoint || !p256dh || !auth) throw new PushError(MSG_FAILED);

    // Idempotent server-side (upsert on endpoint), so re-enabling is safe.
    await pushApi.subscribe({ endpoint: json.endpoint, keys: { p256dh, auth } });
    track("push_subscribed", { source });
    return "subscribed";
  } catch (err) {
    if (err instanceof PushError) throw err;
    throw new PushError(MSG_FAILED);
  }
}

/**
 * Stop notifications on this device. The local unsubscribe is what actually
 * stops delivery; the backend DELETE is best-effort — if it fails, the next
 * send gets a 410 from the push service and the row is pruned there.
 */
export async function disablePush(source: PushSource): Promise<void> {
  if (!hasPushApis()) return;
  try {
    const reg = await navigator.serviceWorker.getRegistration();
    const sub = reg ? await reg.pushManager.getSubscription() : null;
    if (!sub) return;
    const endpoint = sub.endpoint;
    await pushApi.unsubscribe(endpoint).catch(() => undefined);
    await sub.unsubscribe();
    track("push_unsubscribed", { source });
  } catch {
    throw new PushError(MSG_DISABLE_FAILED);
  }
}
