"use client";

import { useSyncExternalStore } from "react";

/**
 * «ثبّت ريحان على جوالك» — platform detection + the Android install prompt.
 *
 * The web app is installable through `app/manifest.ts`, but the two phone
 * platforms reach it differently:
 *   - iOS has NO programmatic install. The only path is Share → «إضافة إلى
 *     الشاشة الرئيسية», so all we can do is show the steps.
 *   - Android Chrome fires `beforeinstallprompt`, which we stash and replay
 *     from our own button.
 *
 * `beforeinstallprompt` fires once, early in page load. A listener attached
 * when the dialog mounts would miss it, so the capture is registered at MODULE
 * load — importing this file from the chat shell is enough.
 */

export type InstallPlatform = "ios" | "android" | "other";

interface BeforeInstallPromptEvent extends Event {
  prompt: () => Promise<void>;
  userChoice: Promise<{ outcome: "accepted" | "dismissed" }>;
}

let deferredPrompt: BeforeInstallPromptEvent | null = null;
const listeners = new Set<() => void>();
const notify = () => listeners.forEach((l) => l());

if (typeof window !== "undefined") {
  window.addEventListener("beforeinstallprompt", (e) => {
    // Suppress Chrome's own mini-infobar; our settings row owns the prompt.
    e.preventDefault();
    deferredPrompt = e as BeforeInstallPromptEvent;
    notify();
  });
  window.addEventListener("appinstalled", () => {
    deferredPrompt = null;
    notify();
  });
}

function subscribe(listener: () => void) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function detectInstallPlatform(): InstallPlatform {
  if (typeof navigator === "undefined") return "other";
  const ua = navigator.userAgent;
  if (/iPhone|iPad|iPod/.test(ua)) return "ios";
  // iPadOS 13+ reports a desktop Mac UA; touch support gives it away.
  if (/Macintosh/.test(ua) && navigator.maxTouchPoints > 1) return "ios";
  if (/Android/.test(ua)) return "android";
  return "other";
}

/** Already running as the installed app — nothing to offer. */
export function isStandalone(): boolean {
  if (typeof window === "undefined") return false;
  return (
    window.matchMedia("(display-mode: standalone)").matches ||
    // iOS Safari's pre-standard flag.
    (navigator as Navigator & { standalone?: boolean }).standalone === true
  );
}

function subscribeDisplayMode(listener: () => void) {
  if (typeof window === "undefined") return () => {};
  const mq = window.matchMedia("(display-mode: standalone)");
  mq.addEventListener("change", listener);
  return () => mq.removeEventListener("change", listener);
}

/**
 * `isStandalone()` as a hook (pwa_step1.md §1B standalone shell). False on the
 * server and during hydration, then the real value — so markup that differs
 * inside the installed app never causes a hydration mismatch.
 */
export function useIsStandalone(): boolean {
  return useSyncExternalStore(subscribeDisplayMode, isStandalone, () => false);
}

/** The stashed Android prompt, or null once used / never fired. */
export function useDeferredInstallPrompt() {
  const prompt = useSyncExternalStore(
    subscribe,
    () => deferredPrompt,
    () => null,
  );

  const install = async () => {
    if (!deferredPrompt) return false;
    const event = deferredPrompt;
    // A prompt can be shown only once — drop it before awaiting.
    deferredPrompt = null;
    notify();
    await event.prompt();
    const { outcome } = await event.userChoice;
    return outcome === "accepted";
  };

  return { canPrompt: prompt !== null, install };
}
