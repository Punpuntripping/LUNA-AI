/**
 * GET /api/v1/app/config — native app version gate + store links
 * (`backend/app/api/app_config.py`). Public, no auth, cached 5 minutes.
 *
 * Versions are the NATIVE marketing version (`1.2.3`), never an OTA update id.
 * Installed < min_supported_version → blocking «حدّث التطبيق» screen;
 * installed < latest_version → dismissible banner. Store URLs may be empty
 * strings until the listings exist.
 */
export type AppPlatform = "ios" | "android";

export type PerPlatform<T> = Record<AppPlatform, T>;

export interface AppConfig {
  min_supported_version: PerPlatform<string>;
  latest_version: PerPlatform<string>;
  store_urls: PerPlatform<string>;
}

/** Headers every native request carries (the web sends none of them). */
export const APP_HEADERS = {
  platform: "X-App-Platform",
  version: "X-App-Version",
  build: "X-App-Build",
  updateId: "X-App-Update-Id",
  requestId: "X-Request-ID",
} as const;
