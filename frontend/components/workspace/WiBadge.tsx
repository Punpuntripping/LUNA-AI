"use client";

import { cn } from "@/lib/utils";

interface WiBadgeProps {
  /** ``workspace_item.wi_seq`` — the number in «WI-3». */
  seq?: number | null;
  className?: string;
  /**
   * Optional ``data-tour`` id for the product tour's anchor resolver.
   *
   * A prop rather than a wrapping element on purpose: this badge sits in flex
   * rows whose spacing it owns (``me-1`` in the pane header), and wrapping it
   * would add a flex item — and, when ``seq`` is null and the badge renders
   * nothing, a stray margin. The tour anchors must be inert.
   */
  dataTour?: string;
}

/**
 * «WI-3» — the conversation-scoped alias every workspace item carries
 * (``workspace_items.wi_seq``, migration 052).
 *
 * The agents don't just use this alias internally: the router says it to the
 * user's face («تم حفظ النص … بعنوان "مذكرة دفاع" (WI-1)»). Until this badge
 * existed the alias appeared nowhere in the UI, so that sentence named
 * something the reader couldn't see. Rendering it on the card, in the pane
 * header and on the chat chips makes the reference resolvable.
 *
 * Rendered ``dir="ltr"``: the alias is Latin letters + digits sitting inside an
 * RTL layout, and without the override the hyphen and number reorder.
 *
 * Renders nothing when there is no alias — case-only items have ``wi_seq =
 * NULL`` (the trigger only assigns within a conversation).
 */
export function WiBadge({ seq, className, dataTour }: WiBadgeProps) {
  if (seq === null || seq === undefined) return null;
  return (
    <span
      dir="ltr"
      data-tour={dataTour}
      title={`رمز العنصر في هذه المحادثة: WI-${seq}`}
      className={cn(
        "inline-flex shrink-0 items-center rounded-full border border-border/70",
        "bg-muted/50 px-1.5 py-0.5 font-mono text-xs font-medium",
        "tabular-nums leading-none text-muted-foreground",
        className,
      )}
    >
      WI-{seq}
    </span>
  );
}
