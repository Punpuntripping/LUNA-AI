"use client";

import { useEffect, useState } from "react";
import { Smartphone, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { InstallAppDialog } from "@/components/Settings/InstallAppDialog";
import {
  detectInstallPlatform,
  useDeferredInstallPrompt,
} from "@/lib/install-app";
import { useChatStore } from "@/stores/chat-store";
import { runInstallPrompt } from "@/components/install/install-device-state";
import { useInstallNudgeStore } from "@/stores/install-nudge-store";

/**
 * «ثبّت ريحان على شاشتك الرئيسية» — the chat nudge. Design:
 * `.claude/plans/install_app_nudge.md` §B.
 *
 * Mounted unconditionally in `ChatLayoutClient` beside `<EduLessonHost />` and
 * renders `null` almost always; every gate lives in `install-nudge-store`.
 *
 * Same slot and visual family as `EduLessonCard`: bottom-END (left in RTL, clear
 * of the sidebar), z-40 — under the sidebar, the mobile workspace overlay and
 * every portalled Radix layer. NOT a Dialog: it never traps focus or blocks the
 * composer. The two cards never stack — each engine stands down while the
 * other is up.
 */
export function InstallNudgeCard() {
  const isOpen = useInstallNudgeStore((s) => s.isOpen);
  const isStreaming = useChatStore((s) => s.isStreaming);
  const [dialogOpen, setDialogOpen] = useState(false);
  const { canPrompt, install } = useDeferredInstallPrompt();

  // A second send can start before the user reads the card — step aside. The
  // impression is already counted; the card does not come back this session.
  useEffect(() => {
    if (isStreaming) useInstallNudgeStore.getState().hide();
  }, [isStreaming]);

  const handleHow = () => {
    useInstallNudgeStore.getState().how();
    // Android with a live prompt: skip the guide, go straight to Chrome's sheet.
    if (detectInstallPlatform() === "android" && canPrompt) {
      void runInstallPrompt(install);
      return;
    }
    setDialogOpen(true);
  };

  return (
    <>
      {isOpen && !isStreaming && (
        <div
          dir="rtl"
          lang="ar"
          role="status"
          aria-live="polite"
          data-testid="install-nudge-card"
          className="
            animate-fab-in fixed bottom-24 end-4 z-40
            w-[min(20rem,calc(100vw-2rem))]
            rounded-lg border border-border bg-card p-4 shadow-lg
          "
          style={{ marginBottom: "env(safe-area-inset-bottom)" }}
        >
          <div className="flex items-start justify-between gap-2">
            <div className="flex min-w-0 items-start gap-2">
              <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-primary/10 text-primary">
                <Smartphone className="h-4 w-4" />
              </span>
              <div className="min-w-0">
                <h3 className="text-sm font-semibold text-foreground">
                  ثبّت ريحان على شاشتك الرئيسية
                </h3>
                <p className="mt-1 text-xs leading-relaxed text-muted-foreground">
                  افتحه كتطبيق بضغطة، بدون شريط المتصفح
                </p>
              </div>
            </div>
            {/* 44px hit area around a small glyph (mobile-compat rule). */}
            <button
              type="button"
              onClick={() => useInstallNudgeStore.getState().dismiss()}
              aria-label="إغلاق"
              data-testid="install-nudge-close"
              className="-me-3 -mt-3 flex h-11 w-11 shrink-0 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
            >
              <X className="h-4 w-4" />
            </button>
          </div>

          <div className="mt-3 flex justify-end">
            <Button
              size="sm"
              className="h-11 min-w-[5.5rem] px-4 text-sm"
              data-testid="install-nudge-how"
              onClick={handleHow}
            >
              كيف؟
            </Button>
          </div>
        </div>
      )}
      <InstallAppDialog
        open={dialogOpen}
        onOpenChange={setDialogOpen}
        source="nudge"
      />
    </>
  );
}
