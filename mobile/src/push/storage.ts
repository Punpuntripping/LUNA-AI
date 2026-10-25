import * as SecureStore from "expo-secure-store";

import { parseSentPushToken, type SentPushToken } from "./logic";

// Per-device push state, in SecureStore (the token is a device identifier):
// - `sent`       the last {token, userId, appVersion} the backend accepted;
// - `opted_in`   the user turned the push on here (chip / settings) and has not
//                turned it off — device-wide, so it follows a new sign-in;
// - `chip_hidden` «نبّهني عند الجاهزية» was refused on this device (web
//                `rayhan.push_chip_hidden`).
// Every read/write swallows storage errors: push must never break the app.

const SENT_KEY = "rayhan.push.sent";
const OPTED_IN_KEY = "rayhan.push.opted_in";
const CHIP_HIDDEN_KEY = "rayhan.push.chip_hidden";
const OPTS: SecureStore.SecureStoreOptions = {
  keychainAccessible: SecureStore.AFTER_FIRST_UNLOCK_THIS_DEVICE_ONLY,
};

async function read(key: string): Promise<string | null> {
  try {
    return await SecureStore.getItemAsync(key, OPTS);
  } catch {
    return null;
  }
}

async function write(key: string, value: string | null): Promise<void> {
  try {
    if (value === null) await SecureStore.deleteItemAsync(key, OPTS);
    else await SecureStore.setItemAsync(key, value, OPTS);
  } catch {
    // Worst case: one extra POST / the chip shows again.
  }
}

export async function readSentToken(): Promise<SentPushToken | null> {
  return parseSentPushToken(await read(SENT_KEY));
}

export function writeSentToken(sent: SentPushToken | null): Promise<void> {
  return write(SENT_KEY, sent ? JSON.stringify(sent) : null);
}

export async function readOptedIn(): Promise<boolean> {
  return (await read(OPTED_IN_KEY)) === "1";
}

export function writeOptedIn(on: boolean): Promise<void> {
  return write(OPTED_IN_KEY, on ? "1" : null);
}

export async function readChipHidden(): Promise<boolean> {
  return (await read(CHIP_HIDDEN_KEY)) === "1";
}

export function writeChipHidden(): Promise<void> {
  return write(CHIP_HIDDEN_KEY, "1");
}

/** Fresh install: the iOS keychain outlives an uninstall — start clean. */
export async function wipePushStorage(): Promise<void> {
  await Promise.all([write(SENT_KEY, null), write(OPTED_IN_KEY, null), write(CHIP_HIDDEN_KEY, null)]);
}
