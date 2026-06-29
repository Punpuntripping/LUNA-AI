"use client";

import { useState, useCallback } from "react";
import { Save, Check, Loader2, AlertCircle } from "lucide-react";
import { useQueryClient } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { templatesApi } from "@/lib/api";
import { templateKeys } from "@/hooks/use-templates";

/**
 * Wave E (writer_planner_user_templates §D6/D8): inline "save attachment as
 * template" chip.
 *
 * Rendered on an assistant bubble after a writing turn when the writer
 * pipeline emitted a ``template_save_offer`` SSE event (the offer is stashed
 * on ``chat-store.templateOffersByMessage`` keyed by the assistant
 * message_id, so it survives the post-stream messages-cache invalidate).
 *
 * State machine — strictly forward, no double-submit:
 *   idle   → «💾 احفظ المرفق كقالب؟ [نعم]»
 *   saving → «جاري الحفظ…» (spinner, button disabled)
 *   saved  → «✓ تم حفظ القالب «<title>»» (terminal)
 *   failed → Arabic failure message (terminal)
 *
 * On success it invalidates the قوالبي templates list query so the sidebar /
 * templates page refetch reflects the new row.
 */

type ChipState =
  | { kind: "idle" }
  | { kind: "saving" }
  | { kind: "saved"; title: string }
  | { kind: "failed"; message: string };

const FAILURE_MESSAGE =
  "فشل حفظ القالب، يمكنك حفظه يدويًا من خلال قوالبي";

interface TemplateSaveOfferChipProps {
  /** The attached workspace_item to ingest as a template. */
  itemId: string;
  /** The attached document's title — context hint shown in the prompt. */
  titleHint: string;
}

export function TemplateSaveOfferChip({
  itemId,
  titleHint,
}: TemplateSaveOfferChipProps) {
  const qc = useQueryClient();
  const [state, setState] = useState<ChipState>({ kind: "idle" });

  const handleSave = useCallback(async () => {
    // Guard: only start from idle. Disabling the button covers the UI, this
    // covers any stray programmatic call — no double-insert.
    if (state.kind !== "idle") return;
    setState({ kind: "saving" });

    try {
      const result = await templatesApi.ingest(itemId);
      if (result.ok) {
        setState({ kind: "saved", title: result.title });
        // Refresh قوالبي so the new template appears without a manual reload.
        void qc.invalidateQueries({ queryKey: templateKeys.list() });
      } else {
        setState({ kind: "failed", message: result.error || FAILURE_MESSAGE });
      }
    } catch {
      // Network / auth / unexpected error → same Arabic fallback.
      setState({ kind: "failed", message: FAILURE_MESSAGE });
    }
  }, [state.kind, itemId, qc]);

  // -------- terminal: saved --------
  if (state.kind === "saved") {
    return (
      <div
        dir="rtl"
        lang="ar"
        className="flex items-center gap-1.5 rounded-full bg-muted/50 px-3 py-1.5 text-[11px] text-muted-foreground"
      >
        <Check className="h-3.5 w-3.5 text-success-fg shrink-0" />
        <span className="truncate max-w-[280px]">
          تم حفظ القالب «{state.title}»
        </span>
      </div>
    );
  }

  // -------- terminal: failed --------
  if (state.kind === "failed") {
    return (
      <div
        dir="rtl"
        lang="ar"
        className="flex items-center gap-1.5 rounded-full bg-destructive/10 px-3 py-1.5 text-[11px] text-destructive"
      >
        <AlertCircle className="h-3.5 w-3.5 shrink-0" />
        <span>{state.message}</span>
      </div>
    );
  }

  // -------- idle / saving --------
  const isSaving = state.kind === "saving";
  return (
    <div
      dir="rtl"
      lang="ar"
      className="flex items-center gap-2 rounded-full border border-border/70 bg-muted/30 ps-3 pe-1.5 py-1"
    >
      <span className="flex items-center gap-1.5 text-[11px] text-muted-foreground">
        <Save className="h-3.5 w-3.5 shrink-0" />
        <span className="truncate max-w-[220px]">
          احفظ «{titleHint}» كقالب؟
        </span>
      </span>
      <Button
        variant="secondary"
        size="sm"
        className={cn("h-6 gap-1 rounded-full px-2.5 text-[11px]")}
        onClick={handleSave}
        disabled={isSaving}
        aria-label="احفظ المرفق كقالب"
      >
        {isSaving ? (
          <>
            <Loader2 className="h-3 w-3 animate-spin" />
            جاري الحفظ…
          </>
        ) : (
          "نعم"
        )}
      </Button>
    </div>
  );
}
