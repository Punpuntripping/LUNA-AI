import Constants from "expo-constants";
import type { DevicePushToken } from "expo-notifications";
import { Platform } from "react-native";

import { ApiError, errorMessage } from "@/api/errors";
import { deleteAppPushToken, deleteAppPushTokenWith, registerAppPushToken } from "@/api/push";
import { APP_PLATFORM, APP_VERSION } from "@/lib/app-info";
import { reportQuietFailure } from "@/observability/sentry";
import { lightColours } from "@/theme/tokens.generated";

import {
  pushPermissionFrom,
  shouldRepost,
  TURN_READY_CHANNEL_ID,
  TURN_READY_CHANNEL_NAME,
  type PushPermission,
  type SentPushToken,
} from "./logic";
import { Notifications } from "./notifications";
import { readChipHidden, readOptedIn, readSentToken, writeOptedIn, writeSentToken } from "./storage";

/**
 * «إجابتك جاهزة» — the device half (plan 06 §3 P2/P3/P4/P6), the app twin of
 * the web's `lib/push.ts`.
 *
 * - Opt-in like the web: only `enablePush()` (a tap on the chip or the
 *   settings switch) ever shows the system prompt. Background registration
 *   (`syncPushRegistration`: sign-in, app start, token rotation) runs only
 *   when the user opted in on this device AND the permission is granted.
 * - The backend gets the Expo push token once per {token, account, app
 *   version} — the last accepted one is cached in SecureStore.
 * - Background jobs never throw into the UI; failures go to Sentry as a code
 *   only (`reportQuietFailure`). The token is never logged or reported.
 * - All token work runs one job at a time (`serial`), so a sign-out can never
 *   interleave with a registration in flight.
 */

export type EnablePushResult = "subscribed" | "denied" | "dismissed";

/** Failures carry an Arabic message fit to show as-is. */
export class PushError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "PushError";
  }
}

export const MSG_PUSH_UNSUPPORTED = "الإشعارات غير مدعومة على هذا الجهاز.";
const MSG_FAILED = "تعذّر تفعيل الإشعارات. حاول مجددًا.";
const MSG_DISABLE_FAILED = "تعذّر إيقاف الإشعارات. حاول مجددًا.";

/** Sign-out waits at most this long for the token DELETE (best-effort). */
const SIGN_OUT_BUDGET_MS = 3_000;
const SIGN_OUT_DELETE_TIMEOUT_MS = 2_500;

let tail: Promise<unknown> = Promise.resolve();

function serial<T>(job: () => Promise<T>): Promise<T> {
  const run = tail.then(job, job);
  tail = run.catch(() => undefined);
  return run;
}

function failureCode(err: unknown): string {
  if (err instanceof ApiError) return `api_${err.kind}_${err.status}`;
  if (err && typeof err === "object" && "code" in err && typeof err.code === "string") {
    return err.code.replace(/[^A-Za-z0-9_]/g, "").slice(0, 64) || "coded";
  }
  return err instanceof Error ? err.name.replace(/[^A-Za-z0-9_]/g, "").slice(0, 64) : "unknown";
}

function report(stage: string, err: unknown): void {
  const code = failureCode(err);
  const expected =
    (err instanceof ApiError && (err.kind !== "http" || err.status === 401)) || code === "ERR_NOTIFICATIONS_NETWORK_ERROR";
  reportQuietFailure("push", stage, code, !expected);
}

// ---------------------------------------------------------------------------
// Channel + permission
// ---------------------------------------------------------------------------

let channelReady: Promise<void> | null = null;

/**
 * Android channel «إجابات ريحان» (`turn-ready`, importance HIGH). Created once
 * at startup, before any token request — Android 13+ shows the permission
 * prompt only once a channel exists. No-op on iOS.
 */
export function ensureTurnReadyChannel(): Promise<void> {
  const N = Notifications;
  if (Platform.OS !== "android" || !N) return Promise.resolve();
  if (!channelReady) {
    channelReady = N.setNotificationChannelAsync(TURN_READY_CHANNEL_ID, {
      name: TURN_READY_CHANNEL_NAME,
      importance: N.AndroidImportance.HIGH,
      lightColor: lightColours.primary,
    }).then(
      () => undefined,
      (err: unknown) => {
        channelReady = null;
        report("channel", err);
      },
    );
  }
  return channelReady;
}

export function pushSupported(): boolean {
  return Notifications !== null;
}

export async function readPushPermission(): Promise<PushPermission> {
  if (!Notifications) return "blocked";
  try {
    return pushPermissionFrom(await Notifications.getPermissionsAsync());
  } catch {
    return "askable";
  }
}

export interface PushStatus {
  supported: boolean;
  permission: PushPermission;
  /** This device gets the push for `userId`. */
  registered: boolean;
  /** «نبّهني عند الجاهزية» was refused on this device. */
  chipHidden: boolean;
}

/** Per-device state, re-read whenever a surface shows (web parity). */
export async function readPushStatus(userId: string | null): Promise<PushStatus> {
  if (!Notifications) return { supported: false, permission: "blocked", registered: false, chipHidden: true };
  const [permission, optedIn, sent, chipHidden] = await Promise.all([
    readPushPermission(),
    readOptedIn(),
    readSentToken(),
    readChipHidden(),
  ]);
  const registered = permission === "granted" && optedIn && userId !== null && sent?.userId === userId;
  return { supported: true, permission, registered, chipHidden };
}

// ---------------------------------------------------------------------------
// Token
// ---------------------------------------------------------------------------

/** Last native (FCM/APNs) token seen — tells a real rotation from the echo of
 *  our own fetch (iOS re-emits the token on every registration). */
let lastDeviceToken: string | null = null;

function easProjectId(): string | null {
  const extra = Constants.expoConfig?.extra as { eas?: { projectId?: unknown } } | undefined;
  const id: unknown = Constants.easConfig?.projectId ?? extra?.eas?.projectId;
  return typeof id === "string" && id ? id : null;
}

async function fetchExpoToken(device?: DevicePushToken): Promise<string> {
  const N = Notifications;
  if (!N) throw new PushError(MSG_PUSH_UNSUPPORTED);
  const projectId = easProjectId();
  if (!projectId) throw Object.assign(new Error("no EAS projectId"), { code: "ERR_PUSH_NO_PROJECT_ID" });
  const devicePushToken = device ?? (await N.getDevicePushTokenAsync());
  if (typeof devicePushToken.data === "string") lastDeviceToken = devicePushToken.data;
  const { data } = await N.getExpoPushTokenAsync({ projectId, devicePushToken });
  return data;
}

/** POST the current token unless the backend already has it (`force`: always). */
async function registerNow(userId: string, force: boolean, device?: DevicePushToken): Promise<void> {
  const token = await fetchExpoToken(device);
  const next: SentPushToken = { token, userId, appVersion: APP_VERSION };
  if (!force && !shouldRepost(await readSentToken(), next)) return;
  await registerAppPushToken({ token, platform: APP_PLATFORM, app_version: APP_VERSION || null });
  await writeSentToken(next);
}

async function syncBody(userId: string, force: boolean, device?: DevicePushToken): Promise<void> {
  if (!Notifications || !(await readOptedIn())) return;
  if ((await readPushPermission()) !== "granted") return;
  await ensureTurnReadyChannel();
  await registerNow(userId, force, device);
}

/** Sign-in / app start (signed in). Never prompts, never throws. Always
 *  re-posts (`force`): the backend deletes a row after 5 failed sends (e.g. an
 *  Expo outage), and the idempotent upsert once per launch heals that. */
export function syncPushRegistration(userId: string): Promise<void> {
  return serial(() => syncBody(userId, true)).catch((err: unknown) => report("register", err));
}

/** `addPushTokenListener`: the native token rotated — re-register with it. */
export function onDevicePushToken(device: DevicePushToken, userId: string): Promise<void> {
  return serial(async () => {
    if (typeof device.data !== "string" || device.data === lastDeviceToken) return;
    await syncBody(userId, false, device);
  }).catch((err: unknown) => report("rotate", err));
}

// ---------------------------------------------------------------------------
// User actions (chip «نبّهني عند الجاهزية», settings «الإشعارات»)
// ---------------------------------------------------------------------------

/**
 * Ask for permission (system prompt if still askable), then register.
 * Call ONLY from a tap. Throws `PushError` (Arabic message).
 */
export async function enablePush(userId: string): Promise<EnablePushResult> {
  const N = Notifications;
  if (!N) throw new PushError(MSG_PUSH_UNSUPPORTED);

  let granted: boolean;
  let denied: boolean;
  try {
    await ensureTurnReadyChannel();
    let perm = await N.getPermissionsAsync();
    if (!perm.granted && perm.canAskAgain) {
      perm = await N.requestPermissionsAsync({ ios: { allowAlert: true, allowSound: true, allowBadge: false } });
    }
    granted = perm.granted;
    denied = perm.status === "denied";
  } catch (err) {
    report("permission", err);
    throw new PushError(MSG_FAILED);
  }
  if (!granted) return denied ? "denied" : "dismissed";

  try {
    await serial(async () => {
      await registerNow(userId, true);
      await writeOptedIn(true);
    });
  } catch (err) {
    report("enable", err);
    throw new PushError(err instanceof ApiError ? errorMessage(err, MSG_FAILED) : MSG_FAILED);
  }
  return "subscribed";
}

/**
 * Settings switch OFF: the backend DELETE is what stops delivery here (there
 * is no local unsubscribe as on the web), so a failure keeps the push ON and
 * throws `PushError`.
 */
export async function disablePush(): Promise<void> {
  try {
    await serial(async () => {
      let token = (await readSentToken())?.token ?? null;
      if (!token && Notifications && (await readPushPermission()) === "granted") {
        token = await fetchExpoToken().catch(() => null);
      }
      if (token) await deleteAppPushToken(token);
      await writeOptedIn(false);
      await writeSentToken(null);
    });
  } catch (err) {
    report("disable", err);
    throw new PushError(MSG_DISABLE_FAILED);
  }
}

/**
 * Manual sign-out, BEFORE the session is revoked: best-effort DELETE with the
 * current access token (bounded wait), then forget the cached token so the
 * next account to sign in here re-posts it. The opt-in stays — the device
 * keeps asking for the push. A forced sign-out (expired / revoked session)
 * never comes here: its row stays and the next sign-in re-owns it.
 */
export async function unregisterPushOnSignOut(accessToken: string | null): Promise<void> {
  const work = serial(async () => {
    const sent = await readSentToken();
    if (sent && accessToken) {
      await deleteAppPushTokenWith(accessToken, sent.token, SIGN_OUT_DELETE_TIMEOUT_MS).catch((err: unknown) =>
        report("signout", err),
      );
    }
    await writeSentToken(null);
  }).catch(() => undefined);
  let timer: ReturnType<typeof setTimeout> | undefined;
  const budget = new Promise<void>((resolve) => {
    timer = setTimeout(resolve, SIGN_OUT_BUDGET_MS);
  });
  await Promise.race([work, budget]);
  clearTimeout(timer);
}
