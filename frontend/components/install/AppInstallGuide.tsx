"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { CheckCircle2 } from "lucide-react";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { InstallSteps } from "@/components/install/InstallSteps";
import { runInstallPrompt } from "@/components/install/install-device-state";
import { track } from "@/lib/analytics/client";
import {
  detectInstallPlatform,
  isStandalone,
  useDeferredInstallPrompt,
  type InstallPlatform,
} from "@/lib/install-app";

type GuideTab = "ios" | "android";

/**
 * The interactive half of `/app`: platform-aware steps with «آيفون / أندرويد»
 * tabs (so a desktop visitor, or someone helping a colleague, can read either),
 * and the already-installed swap. UA and display-mode are client-only, so the
 * server HTML renders the iPhone tab and this corrects it after mount.
 */
export function AppInstallGuide() {
  const [platform, setPlatform] = useState<InstallPlatform>("other");
  const [installed, setInstalled] = useState(false);
  const [tab, setTab] = useState<GuideTab>("ios");
  const { canPrompt, install } = useDeferredInstallPrompt();

  useEffect(() => {
    const detected = detectInstallPlatform();
    const standalone = isStandalone();
    setPlatform(detected);
    setInstalled(standalone);
    if (detected === "android") setTab("android");
    if (!standalone) track("install_dialog_opened", { source: "app_page" });
  }, []);

  if (installed) {
    return (
      <div
        className="flex flex-col items-center gap-4 rounded-xl border border-border bg-card p-6 text-center"
        data-testid="app-page-installed"
      >
        <CheckCircle2 className="h-8 w-8 text-primary" />
        <p className="text-base font-semibold text-foreground">
          ريحان مثبّت على جهازك
        </p>
        <Link
          href="/chat"
          className="inline-flex h-11 items-center justify-center rounded-md bg-primary px-6 text-sm font-medium text-primary-foreground transition-colors hover:bg-primary-hover"
        >
          افتح المحادثة
        </Link>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-6">
      <Tabs
        value={tab}
        onValueChange={(v) => setTab(v === "android" ? "android" : "ios")}
        dir="rtl"
      >
        <TabsList className="grid h-11 w-full grid-cols-2">
          <TabsTrigger value="ios" className="h-9">
            آيفون
          </TabsTrigger>
          <TabsTrigger value="android" className="h-9">
            أندرويد
          </TabsTrigger>
        </TabsList>
        <TabsContent value="ios" className="mt-4">
          <InstallSteps platform="ios" />
        </TabsContent>
        <TabsContent value="android" className="mt-4">
          <InstallSteps
            platform="android"
            // The prompt only means something on the phone that received it.
            canPrompt={platform === "android" && canPrompt}
            onInstall={() => void runInstallPrompt(install)}
          />
        </TabsContent>
      </Tabs>

      {platform === "other" && (
        <div
          className="rounded-xl border border-dashed border-border p-4 text-sm leading-relaxed text-muted-foreground"
          data-testid="app-page-desktop-hint"
        >
          {/* TODO(install_app_nudge §C): QR code to https://rayhanai.com/app,
              rendered as inline SVG at build time. No QR library is in
              package.json and new deps are out of scope for this pass. */}
          افتح <span dir="ltr">rayhanai.com/app</span> من متصفح جوالك (Safari
          في الآيفون أو Chrome في الأندرويد) واتبع الخطوات هناك.
        </div>
      )}
    </div>
  );
}
