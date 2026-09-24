"use client";

import { useEffect, useState } from "react";
import { BellRing, Check, Loader2, Smartphone } from "lucide-react";

import { cn } from "@/lib/utils";
import {
  currentPermission,
  enablePush,
  isSubscribed,
  pushSupport,
  PushError,
} from "@/lib/push";
import { InstallAppDialog } from "@/components/Settings/InstallAppDialog";

/**
 * «نبّهني عند الجاهزية» — the primary web-push opt-in (pwa_step1.md §1D).
 *
 * Lives on the deep-search note while a run is in flight: the user is
 * literally waiting, which is the one moment a permission prompt earns its
 * keep. Never prompts on its own — the tap IS the user gesture iOS requires.
 *
 * Renders nothing when push can't work here, when the user already
 * subscribed, or after a «رفض» (remembered per device — the browser won't
 * show the prompt again anyway; the settings row explains how to re-enable).
 * On iPhone in Safari it becomes an install nudge instead, since iOS only
 * delivers push to the home-screen app.
 */

/** Set after a denial — per device, like the permission itself. */
const HIDDEN_KEY = "rayhan.push_chip_hidden";

/** How long «سننبّهك ✓» / an error line stays before the chip settles. */
const FLASH_MS = 4000;

const LABEL_ENABLE = "نبّهني عند الجاهزية";
const LABEL_INSTALL = "ثبّت ريحان لتصلك الإشعارات";
/** The ✓ is the Check icon, so screen readers don't read "check mark". */
const LABEL_DONE = "سننبّهك";

type ChipState =
  | { kind: "hidden" }
  | { kind: "enable" }
  | { kind: "install" }
  | { kind: "working" }
  | { kind: "done" }
  | { kind: "error"; message: string };

function readHidden(): boolean {
  try {
    return window.localStorage.getItem(HIDDEN_KEY) === "1";
  } catch {
    return false;
  }
}

function writeHidden(): void {
  try {
    window.localStorage.setItem(HIDDEN_KEY, "1");
  } catch {
    // Private mode / storage blocked — the chip just reappears next run.
  }
}

export function PushReadyChip({ className }: { className?: string }) {
  // Starts hidden (SSR + first paint) and resolves on the client, so a
  // subscribed user never sees the chip flash in and out.
  const [state, setState] = useState<ChipState>({ kind: "hidden" });
  const [installOpen, setInstallOpen] = useState(false);

  useEffect(() => {
    let cancelled = false;
    const support = pushSupport();
    if (support === "unsupported" || readHidden()) return;
    if (support === "needs_install") {
      setState({ kind: "install" });
      return;
    }
    const permission = currentPermission();
    if (permission === "denied" || permission === "unsupported") return;
    void isSubscribed().then((subscribed) => {
      if (!cancelled && !subscribed) setState({ kind: "enable" });
    });
    return () => {
      cancelled = true;
    };
  }, []);

  // Confirmation / error lines are transient.
  useEffect(() => {
    if (state.kind !== "done" && state.kind !== "error") return;
    const next: ChipState =
      state.kind === "done" ? { kind: "hidden" } : { kind: "enable" };
    const id = window.setTimeout(() => setState(next), FLASH_MS);
    return () => window.clearTimeout(id);
  }, [state.kind]);

  const handleEnable = async () => {
    setState({ kind: "working" });
    try {
      const result = await enablePush("chip");
      if (result === "subscribed") {
        setState({ kind: "done" });
      } else if (result === "denied") {
        writeHidden();
        setState({ kind: "hidden" });
      } else {
        setState({ kind: "enable" });
      }
    } catch (err) {
      setState({
        kind: "error",
        message:
          err instanceof PushError ? err.message : "تعذّر تفعيل الإشعارات.",
      });
    }
  };

  if (state.kind === "hidden") return null;

  const pillClass = cn(
    "inline-flex items-center gap-1.5 rounded-full border border-primary/30 bg-background px-3 py-1 text-xs font-medium text-primary transition-colors",
    // ≥44px touch target on phones without inflating the pill on desktop.
    "[@media(pointer:coarse)]:min-h-11 [@media(pointer:coarse)]:px-4",
    className,
  );

  if (state.kind === "done") {
    return (
      <span role="status" className={cn(pillClass, "border-primary/20 bg-primary/10")}>
        {LABEL_DONE}
        <Check className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
      </span>
    );
  }

  if (state.kind === "error") {
    return (
      <p role="alert" className={cn("text-xs leading-5 text-destructive", className)}>
        {state.message}
      </p>
    );
  }

  if (state.kind === "install") {
    return (
      <>
        <button
          type="button"
          onClick={() => setInstallOpen(true)}
          className={cn(pillClass, "hover:bg-primary/10")}
          data-testid="push-chip-install"
        >
          <Smartphone className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
          {LABEL_INSTALL}
        </button>
        <InstallAppDialog open={installOpen} onOpenChange={setInstallOpen} />
      </>
    );
  }

  const working = state.kind === "working";
  return (
    <button
      type="button"
      onClick={() => void handleEnable()}
      disabled={working}
      aria-busy={working}
      className={cn(pillClass, "hover:bg-primary/10 disabled:opacity-70")}
      data-testid="push-chip-enable"
    >
      {working ? (
        <Loader2 className="h-3.5 w-3.5 shrink-0 animate-spin" aria-hidden="true" />
      ) : (
        <BellRing className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
      )}
      {LABEL_ENABLE}
    </button>
  );
}
