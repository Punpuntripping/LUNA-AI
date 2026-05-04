/**
 * Playwright E2E — Detail Level Toggle (Wave F).
 *
 * Assumes:
 *   - Next dev server on http://localhost:3000
 *   - Backend API on whatever NEXT_PUBLIC_API_URL is configured to
 *   - A test user with valid Supabase credentials (see LUNA_E2E_* env vars below)
 *
 * To run once Playwright is wired up:
 *   npx playwright install
 *   npx playwright test e2e/detail-level-toggle.spec.ts
 */
import { expect, test } from "@playwright/test";

const APP_URL = process.env.LUNA_E2E_APP_URL ?? "http://localhost:3000";
const TEST_EMAIL = process.env.LUNA_E2E_EMAIL ?? "";
const TEST_PASSWORD = process.env.LUNA_E2E_PASSWORD ?? "";

// Full path on the backend that the frontend will PATCH.
// The api client prefixes `/api/v1`, so a glob matches both dev and prod URLs.
const PREFERENCES_PATCH_GLOB = "**/api/v1/preferences";

test.describe("DetailLevelToggle", () => {
  test.beforeEach(async ({ page }) => {
    test.skip(
      !TEST_EMAIL || !TEST_PASSWORD,
      "LUNA_E2E_EMAIL / LUNA_E2E_PASSWORD not set — skipping authenticated flow.",
    );

    await page.goto(`${APP_URL}/login`);
    await page.getByLabel(/email|البريد/i).fill(TEST_EMAIL);
    await page.getByLabel(/password|كلمة/i).fill(TEST_PASSWORD);
    await page.getByRole("button", { name: /login|دخول/i }).click();
    await page.waitForURL(/\/chat/);
  });

  test("persists each of the three detail levels via PATCH /preferences", async ({ page }) => {
    // Open the sidebar settings popover.
    await page.getByTestId("sidebar-settings-trigger").click();
    const popover = page.getByTestId("sidebar-settings-popover");
    await expect(popover).toBeVisible();

    const levels: Array<{ value: "low" | "medium" | "high"; label: string }> = [
      { value: "low", label: "مختصر" },
      { value: "medium", label: "متوسط" },
      { value: "high", label: "مفصّل" },
    ];

    for (const { value, label } of levels) {
      // Intercept the PATCH before clicking.
      const patch = page.waitForRequest(
        (req) =>
          req.url().includes("/api/v1/preferences") &&
          req.method() === "PATCH",
      );

      await popover.getByRole("radio", { name: label }).click();

      const request = await patch;
      const body = request.postDataJSON() as {
        preferences?: { detail_level?: string };
      };
      expect(body?.preferences?.detail_level).toBe(value);

      // UI reflects the pressed state.
      await expect(
        popover.getByRole("radio", { name: label }),
      ).toHaveAttribute("aria-checked", "true");
    }
  });

  test("detail level survives a page reload", async ({ page }) => {
    await page.getByTestId("sidebar-settings-trigger").click();
    const popover = page.getByTestId("sidebar-settings-popover");

    const patch = page.waitForResponse(
      (res) =>
        res.url().includes("/api/v1/preferences") &&
        res.request().method() === "PATCH" &&
        res.status() === 200,
    );
    await popover.getByRole("radio", { name: "مفصّل" }).click();
    await patch;

    await page.reload();
    await page.waitForLoadState("networkidle");
    await page.getByTestId("sidebar-settings-trigger").click();

    await expect(
      page
        .getByTestId("sidebar-settings-popover")
        .getByRole("radio", { name: "مفصّل" }),
    ).toHaveAttribute("aria-checked", "true");
  });
});

// Route-level contract check — no auth required since we're inspecting the
// network layer. Skipped unless Playwright's MSW-style mocking is wired.
test("PATCH payload shape matches backend contract", async ({ page }) => {
  test.skip(
    !TEST_EMAIL || !TEST_PASSWORD,
    "Authenticated session required to render the toggle.",
  );

  let seenBody: unknown = null;
  await page.route(PREFERENCES_PATCH_GLOB, async (route, request) => {
    if (request.method() === "PATCH") {
      try {
        seenBody = request.postDataJSON();
      } catch {
        seenBody = request.postData();
      }
    }
    await route.continue();
  });

  await page.goto(`${APP_URL}/login`);
  await page.getByLabel(/email|البريد/i).fill(TEST_EMAIL);
  await page.getByLabel(/password|كلمة/i).fill(TEST_PASSWORD);
  await page.getByRole("button", { name: /login|دخول/i }).click();
  await page.waitForURL(/\/chat/);

  await page.getByTestId("sidebar-settings-trigger").click();
  await page
    .getByTestId("sidebar-settings-popover")
    .getByRole("radio", { name: "متوسط" })
    .click();

  expect(seenBody).toMatchObject({
    preferences: { detail_level: "medium" },
  });
});
