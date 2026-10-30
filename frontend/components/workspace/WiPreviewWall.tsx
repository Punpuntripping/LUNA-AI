"use client";

import { useState } from "react";
import { usePathname } from "next/navigation";
import { Lock, Sparkles } from "lucide-react";
import { Button } from "@/components/ui/button";
import { QuotaUpgradeDialog } from "@/components/chat/QuotaUpgradeDialog";
import {
  trackGateCtaClick,
  useGateImpression,
} from "@/components/analytics/useGateImpression";
import {
  WI_PREVIEW_COPY,
  WI_PREVIEW_GATE_KIND,
  wiPreviewHiddenRefsLine,
} from "@/lib/contracts/wi-preview";
import { cn } from "@/lib/utils";
import type { WorkspaceItemPreview } from "@/types";

interface WiPreviewWallProps {
  /** The item's ``preview`` block — render the wall only when it is locked. */
  preview: WorkspaceItemPreview;
  /**
   * The analytics ``content_type`` of the gate events — the item's kind
   * (``agent_search`` / ``agent_writing``), so the funnel can split reports
   * from drafts. The path alone says only "a chat".
   */
  contentType?: string;
  /**
   * Also print «وN مصادر أخرى في الجزء المقفل» inside the wall. For hosts that
   * render NO ``ReferencePanel`` (whose own ``hiddenCount`` line already says
   * it) — saying it twice on one screen reads as two different counts.
   */
  showHiddenRefs?: boolean;
  className?: string;
}

/**
 * The upgrade wall at the end of a free user's preview-locked workspace item
 * (.claude/plans/wi_preview_lock.md §7).
 *
 * The SERVER already cut the body (~40%, at a block / sentence boundary) and
 * decided the lock; this only marks where the visible part stops. Two halves,
 * one from each existing surface, because neither fits as-is:
 *
 * - the FADE from the library gate idiom (`FullContentGate`'s panel, the
 *   `DraftShowcase` «the memo continues» gradient) — dissolving the last lines
 *   says "the document goes on", not "the document ends here";
 * - the «عرض الباقات» FLOW from `QuotaUpgradeDialog`, opened deliberately (no
 *   `info`: nothing was exceeded, and a synthetic quota event would put
 *   invented usage on screen — the Settings pattern). `currentPlanId="free"`
 *   is a fact, not a guess: the server locks only effectively-free users.
 *
 * Copy comes ONLY from `WI_PREVIEW_COPY` (lib/contracts/wi-preview.ts), the one
 * place both apps read the wall's words from.
 *
 * Analytics (§8): `gate_view` when the wall is genuinely on screen (it sits at
 * the END of a long report — a render is not a view), `gate_cta_click` with
 * `cta: "upgrade"` on the button.
 */
export function WiPreviewWall({
  preview,
  contentType,
  showHiddenRefs = false,
  className,
}: WiPreviewWallProps) {
  const pathname = usePathname() ?? "";
  const impressionRef = useGateImpression(WI_PREVIEW_GATE_KIND, { contentType });
  const [plansOpen, setPlansOpen] = useState(false);

  const hiddenRefsLine = showHiddenRefs
    ? wiPreviewHiddenRefsLine(preview.hidden_ref_count)
    : "";

  const handleOpenPlans = () => {
    // Tracked before the dialog opens, never awaited (T9 — never throws).
    trackGateCtaClick(WI_PREVIEW_GATE_KIND, pathname, "upgrade");
    setPlansOpen(true);
  };

  return (
    <section
      ref={impressionRef}
      dir="rtl"
      lang="ar"
      data-testid="wi-preview-wall"
      className={cn("relative mt-2", className)}
    >
      {/* The fade — the text above continues behind the wall. Absolute and
          pointer-events-none, so the last visible lines stay selectable and
          their [n] markers stay clickable. */}
      <div
        aria-hidden="true"
        className="pointer-events-none absolute inset-x-0 -top-16 h-16 bg-gradient-to-t from-background to-transparent"
      />
      <div className="rounded-2xl border border-primary/20 bg-gradient-to-b from-primary/5 to-card p-6 text-center shadow-xs">
        <div className="mx-auto mb-3 flex h-12 w-12 items-center justify-center rounded-2xl bg-primary/10 text-primary ring-1 ring-primary/15">
          <Lock aria-hidden="true" className="h-5 w-5" />
        </div>
        <h3 className="text-sm font-bold leading-snug text-foreground">
          {WI_PREVIEW_COPY.title}
        </h3>
        <p className="mx-auto mt-1.5 max-w-sm text-xs leading-relaxed text-muted-foreground">
          {WI_PREVIEW_COPY.body}
        </p>
        {hiddenRefsLine && (
          <p
            className="mt-2 text-xs text-muted-foreground"
            data-testid="wi-preview-wall-hidden-refs"
          >
            {hiddenRefsLine}
          </p>
        )}
        <Button
          type="button"
          onClick={handleOpenPlans}
          className="mt-4 w-full shadow-sm sm:w-auto sm:px-8"
          data-testid="wi-preview-upgrade"
        >
          <Sparkles aria-hidden="true" className="h-4 w-4 shrink-0" />
          {WI_PREVIEW_COPY.cta}
        </Button>
      </div>

      <QuotaUpgradeDialog
        open={plansOpen}
        onOpenChange={setPlansOpen}
        currentPlanId="free"
      />
    </section>
  );
}
