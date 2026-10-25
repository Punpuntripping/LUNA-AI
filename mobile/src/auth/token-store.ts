import { File, Paths } from "expo-file-system";
import * as SecureStore from "expo-secure-store";

// Refresh token: SecureStore only (Keychain / Android Keystore), readable after
// the first unlock, never synced or migrated to another device.
// Access token: memory only — it dies with the process, like the web's.
// Neither is ever logged.

const RT_KEY = "rayhan.auth.rt";
const SECURE_OPTS: SecureStore.SecureStoreOptions = {
  keychainAccessible: SecureStore.AFTER_FIRST_UNLOCK_THIS_DEVICE_ONLY,
};

// The iOS keychain survives an uninstall, so a reinstalled app would wake up
// signed in as whoever used it before. A plain marker file does NOT survive
// the uninstall: no marker ⇒ fresh install ⇒ wipe the keychain entry.
const INSTALL_MARKER = "rayhan.installed";

let accessToken: string | null = null;

export function getAccessToken(): string | null {
  return accessToken;
}

export function setAccessToken(token: string | null): void {
  accessToken = token;
}

export async function getRefreshToken(): Promise<string | null> {
  try {
    return await SecureStore.getItemAsync(RT_KEY, SECURE_OPTS);
  } catch {
    // Undecryptable (e.g. Android keystore reset after a backup restore) =
    // no session. The caller lands on login.
    return null;
  }
}

export async function setRefreshToken(token: string): Promise<void> {
  await SecureStore.setItemAsync(RT_KEY, token, SECURE_OPTS);
}

export async function clearTokens(): Promise<void> {
  accessToken = null;
  try {
    await SecureStore.deleteItemAsync(RT_KEY, SECURE_OPTS);
  } catch {
    // Nothing stored — nothing to wipe.
  }
}

/** Run once at cold start, BEFORE reading the refresh token. Resolves true
 *  when this is a fresh install (the caller wipes its own keychain state). */
export async function wipeIfFreshInstall(): Promise<boolean> {
  try {
    const marker = new File(Paths.document, INSTALL_MARKER);
    if (marker.exists) return false;
    await clearTokens();
    marker.create();
    marker.write("1");
    return true;
  } catch {
    // A marker failure must never block startup; worst case the next launch
    // retries the check.
    return false;
  }
}
