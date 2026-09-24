"use client";

import { useEffect, useState } from "react";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { InstallSteps } from "@/components/install/InstallSteps";
import { track } from "@/lib/analytics/client";
import {
  detectInstallPlatform,
  useDeferredInstallPrompt,
  type InstallPlatform,
} from "@/lib/install-app";
import { runInstallPrompt } from "@/components/install/install-device-state";

interface InstallAppDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Which surface opened it — the `install_dialog_opened` funnel split. */
  source?: "settings" | "nudge";
}

/**
 * «ثبّت ريحان على جوالك» — opened from the settings popover in
 * `SidebarFooter` and from the chat nudge's «كيف؟». The instructions
 * themselves live in `InstallSteps`.
 */
export function InstallAppDialog({
  open,
  onOpenChange,
  source = "settings",
}: InstallAppDialogProps) {
  // Detected after mount — the UA is not available during SSR.
  const [platform, setPlatform] = useState<InstallPlatform>("other");
  useEffect(() => setPlatform(detectInstallPlatform()), []);
  const { canPrompt, install } = useDeferredInstallPrompt();

  useEffect(() => {
    if (open) track("install_dialog_opened", { source });
  }, [open, source]);

  const handleInstall = async () => {
    if (await runInstallPrompt(install)) onOpenChange(false);
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        className="max-w-sm"
        presentation="mobileSheet"
        dir="rtl"
        lang="ar"
        data-testid="install-app-dialog"
      >
        <DialogHeader>
          <DialogTitle>ثبّت ريحان على جوالك</DialogTitle>
          <DialogDescription>
            افتح ريحان من الشاشة الرئيسية كتطبيق، بدون شريط المتصفح.
          </DialogDescription>
        </DialogHeader>

        {/* A live Chrome prompt (Android or desktop) collapses to one button;
            desktop without one falls through to «open it on your phone». */}
        <InstallSteps
          platform={platform}
          canPrompt={canPrompt}
          onInstall={handleInstall}
        />
      </DialogContent>
    </Dialog>
  );
}
