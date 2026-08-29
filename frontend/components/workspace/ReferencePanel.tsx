"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import {
  Scale,
  Gavel,
  Building2,
  Megaphone,
  ScrollText,
  BookText,
  ExternalLink,
  ChevronDown,
  FileText,
  BookOpen,
  Copy,
  Check,
  Loader2,
  Sparkles,
} from "lucide-react";
import { Button, buttonVariants } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { MarkdownRenderer } from "@/components/chat/MarkdownRenderer";
import { ChunkTable } from "@/components/library/blocks/ChunkTable";
import {
  useLibraryBalance,
  useReferenceSource,
} from "@/hooks/use-reference-source";
import type { LibraryBalance } from "@/lib/library/full-content";
import {
  balanceCopy,
  rateLimitedCopy,
  referenceRevealCopy,
  refusalCardCopy,
  revealCopy,
  sourceUnavailableCopy,
  staleSessionCopy,
  transportErrorCopy,
  unlockedNotice,
  type RefusalCardCopy,
} from "@/lib/library/gate-copy";
import type { ReferenceSourceResult } from "@/lib/api";
import { cn } from "@/lib/utils";
import type {
  CrossRef,
  Reference,
  ReferenceDomain,
  ReferenceUnlockInfo,
  SourceView,
} from "@/types";

interface ReferencePanelProps {
  references: Reference[];
  /**
   * The workspace item these references belong to — the in-app address for the
   * metered source reveal (access-tiers Phase C, §6.2), scoped to
   * ``GET /workspace/{item_id}/references/{n}/source``.
   *
   * Mutually exclusive with ``blogToken``: a reader of someone else's published
   * post does NOT own the author's workspace item, so that endpoint would 404
   * them out of their own reading.
   */
  itemId?: string;
  /**
   * The public blog token, for a reference panel rendered on a published post.
   *
   * «عرض المصدر» and the ``[n]`` preview work here exactly as they do in chat —
   * the click opens the source; it just costs an unlock now. An ANONYMOUS
   * reader still sees the affordance and gets the «سجّل مجاناً» card on click,
   * which is the whole point: hiding the button (an earlier pass did) deleted a
   * feature rather than metering it.
   */
  blogToken?: string;
  /**
   * When non-null, reference ``n`` is opened: its source dialog is revealed
   * when a source exists, otherwise the matching ``<li id="ref-{n}">`` is
   * scrolled into view and briefly flashes. Set by
   * ``openWorkspaceItemAtReference`` in the chat store; cleared via
   * ``onFlashDone`` (called from the ``<li>`` animation-end handler, and on
   * dialog close) so the same marker can be re-clicked.
   */
  focusedReferenceN?: number | null;
  /** Called after the flash animation completes, or the dialog closes. */
  onFlashDone?: () => void;
  /**
   * Migration 049: ``true`` while ``useWorkspaceItemReferences`` is fetching
   * the relational ref list. Renders 3 skeleton cards so the pane doesn't
   * layout-shift when the data arrives.
   */
  isLoading?: boolean;
  /**
   * Open «WI-{seq}» — the source research item a writer-published reference was
   * pulled from — at its own ``sourceN`` citation.
   *
   * Supplied by IN-CHAT hosts only. This panel also renders anonymously on
   * ``/blog/{token}`` (PublicAnswerView), where there is no chat store, no
   * conversation and no workspace, so the navigation cannot be read off a store
   * in here — it arrives as a callback or not at all. Absent ⇒ the «من WI-9»
   * badge stays the plain text it has always been.
   */
  onOpenSourceWi?: (seq: number, sourceN: number | null) => void;
  /**
   * Does ``onOpenSourceWi`` actually have somewhere to go for this ``seq``?
   *
   * The host owns the ``wi_seq → item_id`` map, so only the host can answer —
   * and it must be asked BEFORE we decide what to render, because the target
   * can be missing for perfectly ordinary reasons (item deleted, published from
   * another conversation, workspace list not fetched yet). Answers ``false`` ⇒
   * plain span. **Never a dead button.**
   *
   * Both props are needed for a clickable badge; a host that supplies only one
   * gets the plain span, which is the safe half of the failure.
   */
  canOpenSourceWi?: (seq: number) => boolean;
}

/**
 * Per-domain chip: Arabic label, icon, tint.
 *
 * EXHAUSTIVE ON PURPOSE. `Record<ReferenceDomain, …>` means a new member of the
 * union does not compile until it has a chip here — the alternative (a lookup
 * with a `?? DOMAIN_META.regulations` fallback, which the card also keeps as a
 * runtime guard against an unknown domain off the wire) would silently paint a
 * مادة with the ميزان and call it «نظام».
 *
 * The palette is one hue per WING, so the three regulation-backed domains share
 * sky and separate on icon + shade:
 *
 * - `regulations`      — a chunk of a نظام            · ميزان   · sky-600/400
 * - `regulation_docs`  — the WHOLE نظام               · كتاب    · sky-600/400
 * - `articles`         — one مادة inside a نظام       · مخطوطة · sky-700/300
 *
 * «مادة» is deliberately the same hue as «نظام» (it IS part of one) at a deeper
 * shade, so it reads as a narrower slice of the same wing rather than a fifth
 * unrelated colour. cases amber / compliance emerald / circulars violet are
 * untouched.
 */
const DOMAIN_META: Record<
  ReferenceDomain,
  { label: string; icon: typeof Scale; tint: string }
> = {
  regulations: { label: "نظام", icon: Scale, tint: "text-sky-600 dark:text-sky-400" },
  cases: { label: "قضية", icon: Gavel, tint: "text-amber-600 dark:text-amber-400" },
  compliance: {
    label: "خدمة حكومية",
    icon: Building2,
    tint: "text-emerald-600 dark:text-emerald-400",
  },
  circulars: {
    label: "تعميم",
    icon: Megaphone,
    tint: "text-violet-600 dark:text-violet-400",
  },
  // simple_search (§6.1a). The labels are PINNED by the wire contract — «مادة»
  // and «نظام» — so they match the backend Literals and the DB CHECK byte for
  // byte. `regulation_docs` shares `regulations`' label because both are the
  // document's blanket type; both are overridden by `doc_type` when the corpus
  // knows the real one (لائحة / تنظيم / دليل / …).
  articles: {
    label: "مادة",
    icon: ScrollText,
    tint: "text-sky-700 dark:text-sky-300",
  },
  regulation_docs: {
    label: "نظام",
    icon: BookText,
    tint: "text-sky-600 dark:text-sky-400",
  },
};

/** Domains whose rows carry `regulations_v2.doc_type_raw`. */
const DOC_TYPE_DOMAINS: ReadonlySet<ReferenceDomain> = new Set<ReferenceDomain>([
  "regulations",
  "regulation_docs",
]);

/**
 * The corpus's "we could not determine a type" bucket. A sentinel, not a
 * document type — printing it as the card's chip reads as a broken row.
 */
const DOC_TYPE_UNKNOWN = "غير محدد";

/** `doc_type_raw` when it is a real type, else "" (absent, blank, sentinel). */
function usableDocType(ref: Reference): string {
  if (!DOC_TYPE_DOMAINS.has(ref.domain)) return "";
  const raw = (ref.doc_type ?? "").trim();
  return raw && raw !== DOC_TYPE_UNKNOWN ? raw : "";
}

/**
 * JSON-driven reference list for a deep_search ``agent_search`` artifact.
 *
 * Renders one card per ``Reference`` (from useWorkspaceItemReferences), switching
 * on ``domain``. Each card exposes the primary external link and — when the
 * backend reports ``has_source`` — a popup with the full original source.
 *
 * ACCESS-TIERS PHASE C (§6.2). Source bodies LEFT this payload. The list used
 * to arrive with every ``source_view`` embedded (full case bodies, full chunk
 * content, uncapped circulars up to 168 KB), which made ``[n]`` and
 * «عرض المصدر» pure client-side state changes and metering structurally
 * impossible — no server call happened at reveal time. Now:
 *
 * - ``source_view`` on a list entry is ALWAYS null; branch on ``has_source``.
 * - Opening a source fetches exactly one body, on the click, through
 *   ``useReferenceSource`` — which is why that hook is `enabled`-gated on the
 *   open ``n`` and nothing else. Prefetching the panel would spend an unlock
 *   per card and could burn a free reader's whole month on one artifact.
 * - The click IS the consent (§5.1): no confirmation dialog, no stored
 *   decision. The ledger row is the record. What the reader gets instead is a
 *   passive balance chip beside the list heading and, after a reveal, a quiet
 *   line naming exactly what was unlocked (D15.1).
 *
 * Window C: each card carries ``id="ref-{n}"`` so chat-bubble citation
 * markers can scroll the matching card into view and trigger a brief flash
 * via the ``data-flash`` attribute + ``ref-flash`` keyframe (globals.css).
 */
export function ReferencePanel({
  references,
  itemId,
  blogToken,
  focusedReferenceN,
  onFlashDone,
  isLoading = false,
  onOpenSourceWi,
  canOpenSourceWi,
}: ReferencePanelProps) {
  // The open reference is tracked by ``n``, not by the object: the dialog now
  // owns a fetch keyed on (itemId, n), and holding a stale Reference across a
  // list refresh would point that fetch at the wrong citation.
  const [openN, setOpenN] = useState<number | null>(null);
  // Per-card refs so we can scrollIntoView the focused one without a global
  // querySelector on every focus change. Only used for the no-source fallback
  // path — references that CAN be revealed open the dialog directly (same as
  // clicking the «عرض المصدر» button on the card).
  const itemRefs = useRef<Map<number, HTMLLIElement | null>>(new Map());

  // Stable sorted list — memoized so the effect below doesn't fire on every
  // parent re-render (a fresh array literal would defeat React's dep check).
  const ordered = useMemo(
    () => [...(references ?? [])].sort((a, b) => a.n - b.n),
    [references],
  );

  // A source can be revealed when we can ADDRESS it (an owned workspace item,
  // or a public blog token) and the backend says a body can be built.
  //
  // ⚠ Deliberately NOT gated on being signed in. An anonymous reader must still
  // see «عرض المصدر» and get the «سجّل مجاناً» card when they click — the reveal
  // was metered, not removed. `has_source` is the authoritative bit; it costs no
  // request to learn, and `source_view` is null on every list entry now.
  const canReveal = useCallback(
    (ref: Reference | undefined | null): boolean =>
      (!!itemId || !!blogToken) && ref?.has_source === true,
    [itemId, blogToken],
  );

  const hasRevealable = useMemo(
    () => ordered.some((ref) => canReveal(ref)),
    [ordered, canReveal],
  );

  // Passive meter — «no prompt, but never a silent meter» (§5.1). Reading the
  // allowance charges nothing, but it is still gated on there being something
  // to reveal so a blog panel issues no authed request at all.
  const { data: balance } = useLibraryBalance({ enabled: hasRevealable });

  useEffect(() => {
    if (focusedReferenceN == null) return;
    const ref = ordered.find((r) => r.n === focusedReferenceN);
    if (!ref) return;
    // Preferred path: behave exactly like clicking the card's «عرض المصدر»
    // button — open the dialog, which fetches the source on demand.
    if (canReveal(ref)) {
      setOpenN(ref.n);
      return;
    }
    // Fallback: no source can be built for this reference (stub rows, legacy
    // or anonymous snapshots, manually authored references). Scroll the card
    // into view and flash it so the user at least sees which entry was meant.
    const el = itemRefs.current.get(focusedReferenceN);
    if (!el) return;
    el.scrollIntoView({ behavior: "smooth", block: "center" });
    el.setAttribute("data-flash", "true");
  }, [focusedReferenceN, ordered, canReveal]);

  const openReference = useMemo(
    () => (openN === null ? null : ordered.find((r) => r.n === openN) ?? null),
    [openN, ordered],
  );

  if ((!references || references.length === 0) && isLoading) {
    return (
      <div dir="rtl" className="mt-6 border-t pt-4" aria-busy="true">
        <h3 className="mb-3 text-sm font-semibold text-foreground">المراجع</h3>
        <ul className="flex flex-col gap-2">
          {[0, 1, 2].map((i) => (
            <li
              key={i}
              className="h-[78px] rounded-lg border bg-muted/40 animate-pulse"
            />
          ))}
        </ul>
      </div>
    );
  }

  if (!references || references.length === 0) return null;

  const handleAnimationEnd = (n: number) => {
    const el = itemRefs.current.get(n);
    if (el) el.removeAttribute("data-flash");
    onFlashDone?.();
  };

  return (
    <div dir="rtl" className="mt-6 border-t pt-4">
      <div className="mb-3 flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
        <h3 className="text-sm font-semibold text-foreground">
          المراجع <span className="text-muted-foreground">({ordered.length})</span>
        </h3>
        <BalanceChip balance={balance ?? null} />
      </div>
      <ul className="flex flex-col gap-2">
        {ordered.map((ref) => (
          <ReferenceCard
            key={`${ref.n}-${ref.ref_id}`}
            reference={ref}
            canReveal={canReveal(ref)}
            registerRef={(node) => {
              if (node) {
                itemRefs.current.set(ref.n, node);
              } else {
                itemRefs.current.delete(ref.n);
              }
            }}
            onAnimationEnd={() => handleAnimationEnd(ref.n)}
            onViewSource={() => setOpenN(ref.n)}
            onOpenSourceWi={onOpenSourceWi}
            canOpenSourceWi={canOpenSourceWi}
          />
        ))}
      </ul>

      <Dialog
        // Keyed off the resolved reference, not the raw ``openN``: if the list
        // refreshes and that ``n`` is gone, the dialog closes instead of
        // rendering an empty shell with no title (which Radix also flags).
        open={openReference !== null}
        onOpenChange={(o) => {
          if (o) return;
          setOpenN(null);
          // Mirror the flash-end semantics: tell the parent the focused
          // reference has been consumed so repeat-clicking the same ``[n]``
          // re-opens the dialog instead of going no-op on the unchanged
          // focusedReferenceN value.
          onFlashDone?.();
        }}
      >
        {/* Tour anchor (Act 3, step 7) — the revealed source body. Inert. */}
        <DialogContent
          className="max-w-2xl"
          presentation="mobileSheet"
          dir="rtl"
          data-tour="source-dialog"
        >
          {openReference && (
            <SourceRevealBody
              itemId={itemId}
              blogToken={blogToken}
              reference={openReference}
            />
          )}
        </DialogContent>
      </Dialog>
    </div>
  );
}

/**
 * The passive «فتح المصادر» meter beside the list heading. Silent whenever the
 * allowance could not be read (anonymous, locked account, failed usage call) —
 * a wrong number next to a spend action is worse than no number at all.
 */
function BalanceChip({ balance }: { balance: LibraryBalance | null }) {
  if (!balance) return null;

  const label =
    balance.limit === null
      ? balanceCopy.unlimited
      : balance.remaining !== null && balance.remaining > 0
        ? balanceCopy.remaining(balance.remaining, balance.limit)
        : balanceCopy.exhausted;

  const renews =
    balance.limit === null ? "" : balanceCopy.renewsOn(balance.resets_at);

  return (
    <span
      className="rounded-full bg-pill px-2.5 py-0.5 text-xs font-medium tabular-nums text-pill-fg"
      title={[revealCopy.authedHint, renews].filter(Boolean).join(" ")}
    >
      {label}
    </span>
  );
}

// ---------------------------------------------------------------------------
// Reference card
// ---------------------------------------------------------------------------

function ReferenceCard({
  reference,
  canReveal,
  registerRef,
  onAnimationEnd,
  onViewSource,
  onOpenSourceWi,
  canOpenSourceWi,
}: {
  reference: Reference;
  canReveal: boolean;
  registerRef: (node: HTMLLIElement | null) => void;
  onAnimationEnd: () => void;
  onViewSource: () => void;
  onOpenSourceWi?: (seq: number, sourceN: number | null) => void;
  canOpenSourceWi?: (seq: number) => boolean;
}) {
  const meta = DOMAIN_META[reference.domain] ?? DOMAIN_META.regulations;
  const Icon = meta.icon;
  // The regulation-backed domains carry their own Arabic document type
  // (regulations_v2.doc_type_raw — لائحة / تنظيم / دليل / مواصفة قياسية / …),
  // which is far more informative than the blanket نظام. BOTH of them: a
  // `regulation_docs` ref IS that document, so it gets the same treatment the
  // chunk-level `regulations` ref has always had.
  //
  // Falls back to the domain label when the corpus has no determined type, and
  // for every other domain — whose meta label is already the real thing
  // (قضية / تعميم / خدمة حكومية / مادة).
  //
  // `articles` is NOT in that set on purpose: a مادة is «مادة» whatever its
  // parent's doc type is. It gets its number instead, when the row has one —
  // «مادة 81» is what tells two refs from the same نظام apart at a glance.
  const docType = usableDocType(reference);
  const articleNum =
    reference.domain === "articles" ? (reference.article_num ?? "").trim() : "";
  const typeLabel =
    docType || (articleNum ? `${meta.label} ${articleNum}` : meta.label);

  const primaryUrl = referencePrimaryUrl(reference);
  const label = referenceLabel(reference);

  // «من WI-9» — provenance the panel has always DISPLAYED and never acted on.
  // It becomes a button only when the alias parses AND the host confirms it can
  // resolve that alias; anything less falls back to the plain span it was.
  const sourceWiSeq = parseWiSeq(reference.source_wi);
  const sourceWiOpenable =
    sourceWiSeq !== null &&
    !!onOpenSourceWi &&
    (canOpenSourceWi?.(sourceWiSeq) ?? false);
  const sourceWiTitle =
    reference.source_n != null
      ? `المصدر: ${reference.source_wi} (مرجع ${reference.source_n})`
      : `المصدر: ${reference.source_wi}`;
  const sourceWiBadgeClass =
    "rounded-sm bg-muted px-1 py-px text-xs font-medium tabular-nums text-muted-foreground";
  const handleOpenSourceWi = () => {
    if (sourceWiSeq === null || !onOpenSourceWi) return;
    onOpenSourceWi(sourceWiSeq, reference.source_n ?? null);
  };

  return (
    <li
      ref={registerRef}
      id={`ref-${reference.n}`}
      // Tour anchor `ref-card-<n>` — the four-source-domains step that resolved
      // `ref-card-9` is RETIRED from the five-step script. Emitted for every
      // card anyway; inert, and distinct from the `id` the flash path uses.
      data-tour={`ref-card-${reference.n}`}
      onAnimationEnd={onAnimationEnd}
      className="rounded-lg border bg-card px-3 py-2.5 ref-flash-target"
    >
      <div className="flex items-start gap-2.5">
        {/* [n] badge */}
        <span className="mt-0.5 flex h-6 min-w-6 shrink-0 items-center justify-center rounded-md bg-muted px-1.5 text-xs font-semibold tabular-nums text-foreground">
          {reference.n}
        </span>

        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-1.5">
            <Icon className={cn("h-3.5 w-3.5 shrink-0", meta.tint)} />
            <span className="text-xs font-medium text-muted-foreground">
              {typeLabel}
            </span>
            <span
              className={cn(
                "h-1.5 w-1.5 rounded-full",
                reference.relevance === "high" ? "bg-emerald-500" : "bg-amber-400"
              )}
              title={reference.relevance === "high" ? "صلة عالية" : "صلة متوسطة"}
            />
            {/* Writer-publisher attribution: surfaces the source-WI alias when
                this ref was projected onto an agent_writing item from a
                research WI. Absent for agent_search items.

                Clickable in chat: it opens the source WI at the very citation
                the writer pulled (``source_n``), which is why the tooltip has
                always named both. On the public blog panel — no store, no
                workspace — it stays exactly the text it is here. */}
            {reference.source_wi &&
              (sourceWiOpenable ? (
                <button
                  type="button"
                  onClick={handleOpenSourceWi}
                  title={`${sourceWiTitle} — اضغط للانتقال`}
                  className={cn(
                    sourceWiBadgeClass,
                    "underline decoration-dotted underline-offset-2 transition-colors",
                    "hover:bg-accent hover:text-foreground",
                    "focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring",
                  )}
                >
                  من {reference.source_wi}
                </button>
              ) : (
                <span className={sourceWiBadgeClass} title={sourceWiTitle}>
                  من {reference.source_wi}
                </span>
              ))}
          </div>

          <p className="mt-0.5 text-sm font-medium leading-snug text-foreground">
            {label}
          </p>

          {reference.snippet && (
            <p className="mt-1 line-clamp-2 text-xs leading-relaxed text-muted-foreground">
              {reference.snippet}
            </p>
          )}

          {/* Actions — three of these wrap on a phone, so below `md` they get
              the 36px touch height and a wider gap instead of a cramped
              three-across row of 24px pills. */}
          <div className="mt-1.5 flex flex-wrap items-center gap-1 max-md:gap-1.5">
            {/* PHASE C: gated on ``has_source`` (via ``canReveal``), NEVER on
                ``source_view`` — which is null on every list entry now. The
                click opens the dialog, and the dialog does the fetching. */}
            {canReveal && (
              <Button
                variant="ghost"
                size="sm"
                className="h-6 max-md:h-9 gap-1 px-2 text-xs"
                onClick={onViewSource}
              >
                <FileText className="h-3 w-3" />
                {referenceRevealCopy.cta}
              </Button>
            )}
            {/* NAVIGATION, NOT A REVEAL — free and unmetered, exactly like the
                dialog's twin (the library page enforces its own tier, and
                charging for the link too would double-charge the same read).
                Dropped when the item has no published page: never a hub
                fallback, never a guessed URL. Absent on blog snapshots frozen
                before ``library_url`` existed, which is why the field is
                optional. New tab on purpose — this panel can sit over a
                streaming chat, and navigating the tab away kills the stream. */}
            {reference.library_url && (
              <Link
                href={reference.library_url}
                target="_blank"
                rel="noopener"
                className={cn(
                  // FILLED (primary), not ghost — the same treatment its twin
                  // has in the reveal dialog. This is the one action on the card
                  // that keeps the reader inside ريحان; «عرض المصدر» and «فتح
                  // المصدر الرسمي» are ghosts beside it, so the emphasis is what
                  // tells them apart at a glance.
                  buttonVariants({ size: "sm" }),
                  "h-6 max-md:h-9 gap-1 px-2 text-xs"
                )}
              >
                <BookOpen className="h-3 w-3" />
                {referenceLibraryExitLabel(reference)}
              </Link>
            )}
            {primaryUrl && (
              <a
                href={primaryUrl}
                target="_blank"
                rel="noopener noreferrer"
                className={cn(
                  buttonVariants({ variant: "ghost", size: "sm" }),
                  "h-6 max-md:h-9 gap-1 px-2 text-xs"
                )}
              >
                <ExternalLink className="h-3 w-3" />
                فتح المصدر الرسمي
              </a>
            )}
            {/* NOTE: no «إحالات» toggle here any more. The list moved inside the
                source-reveal dialog, directly under the body it belongs to — a
                card is two lines of snippet, and expanding a citation mesh into
                it pushed every following reference down the panel. */}
          </div>
        </div>
      </div>
    </li>
  );
}

// ---------------------------------------------------------------------------
// Source reveal dialog — the metered path (§6.2)
// ---------------------------------------------------------------------------

/**
 * Fetches ONE source and renders whichever of the three states came back:
 * loading, the source itself, or a refusal/error card.
 *
 * The hook is mounted only while the dialog is open, so `enabled` is exactly
 * "the user asked for this one" — that is the whole metering contract. Nothing
 * here may run on the panel's mount path.
 */
function SourceRevealBody({
  itemId,
  blogToken,
  reference,
}: {
  itemId: string | undefined;
  blogToken: string | undefined;
  reference: Reference;
}) {
  const { data: result, isFetching, refetch } = useReferenceSource(
    itemId,
    reference.n,
    { blogToken },
  );
  const fallbackTitle = referenceLabel(reference);

  // Defensive: with no address at all the query is permanently disabled, so a
  // skeleton here would spin forever. Unreachable today (``canReveal`` requires
  // one before the dialog can open) — but an eternal spinner is the worst
  // possible failure mode for a dialog, so it is closed off explicitly.
  if (!itemId && !blogToken) {
    return (
      <RevealRefusal
        result={{ ok: false, kind: "error", error: "not_found", status: null }}
        reference={reference}
        onRetry={() => {}}
        isRetrying={false}
      />
    );
  }

  if (!result) {
    return <SourceLoadingBody />;
  }

  if (result.ok) {
    const { source_view: view, unlocked, balance, library_url } = result.data;
    // Two resolvers answer this question and they disagree on exactly one wing.
    // The reveal endpoint resolves from the ledger's `(content_type,
    // content_id)` pair, where a compliance citation is `('service',
    // services.id)` — a bare service, which has no page of ours. The LIST
    // resolves from the reference row and hops `services.id →
    // service_guides.id → /compliance/{slug}`, which does. So the card's answer
    // is the fallback: the dialog must not drop a button the card behind it is
    // already showing. The precedence is never reversed — where the reveal has
    // an answer it is the fresher one.
    const exitUrl = library_url ?? reference.library_url ?? null;
    return (
      <RevealedSource
        view={view}
        unlocked={unlocked}
        balanceLimit={balance?.limit ?? null}
        balanceUsed={balance?.used ?? null}
        fallbackTitle={fallbackTitle}
        reference={reference}
        libraryUrl={exitUrl}
      />
    );
  }

  return (
    <RevealRefusal
      result={result}
      reference={reference}
      onRetry={() => {
        void refetch();
      }}
      isRetrying={isFetching}
    />
  );
}

/** Dialog skeleton while the one source is in flight. */
function SourceLoadingBody() {
  return (
    <>
      <DialogHeader>
        <DialogTitle className="flex items-center gap-2 text-base">
          <Loader2 aria-hidden="true" className="h-4 w-4 shrink-0 animate-spin" />
          {referenceRevealCopy.loading}
        </DialogTitle>
      </DialogHeader>
      <div className="space-y-2.5" aria-busy="true">
        {[0, 1, 2, 3, 4].map((i) => (
          <div
            key={i}
            className={cn(
              "h-3.5 animate-pulse rounded bg-muted/60",
              i === 0 && "w-2/3",
              i === 4 && "w-1/2",
            )}
          />
        ))}
      </div>
    </>
  );
}

/**
 * The revealed source, plus the two things §5.1 and D15.1 require next to it:
 * a quiet line naming WHAT was unlocked, and the remaining balance.
 *
 * EXITS ARE EXACTLY TWO (2026-08-01). «فتح المصدر الرسمي» goes to the official
 * government page; the in-app one (`referenceLibraryExitLabel`) goes to the same
 * document inside our own library. Everything else that used to hang off this
 * dialog — «رابط النظام»، «ملف PDF»، «تفاصيل الحكم»، «المنصة الوطنية»، «رابط
 * الخدمة»، «رابط التعميم» — was a per-domain variation on those same two ideas,
 * plus a raw-file exit for content we now publish ourselves. One pair of
 * buttons, identical in every domain, is the whole point: the reader learns it
 * once.
 *
 * A government service is the one place the two exits point at genuinely
 * different documents rather than two views of one: the official button opens
 * the ENTITY's service page, the in-app button opens OUR service guide. That is
 * why compliance is the only domain whose label is rewritten rather than
 * templated — see `referenceLibraryExitLabel`.
 */
function RevealedSource({
  view,
  unlocked,
  balanceLimit,
  balanceUsed,
  fallbackTitle,
  reference,
  libraryUrl,
}: {
  view: SourceView;
  unlocked: ReferenceUnlockInfo;
  balanceLimit: number | null;
  balanceUsed: number | null;
  fallbackTitle: string;
  reference: Reference;
  libraryUrl: string | null;
}) {
  const sourceContent = extractSourceContent(view);
  const notice = unlockedNotice({
    title: unlocked.title ?? "",
    articleNo: unlocked.article_no ?? null,
    contentType: unlocked.content_type ?? "",
    cost: unlocked.cost ?? 0,
    reason: unlocked.reason,
  });
  // Only a `granted` decision spent anything, so only it earns the emphasized
  // treatment. `already_unlocked` / `open` render as a plain reassurance line.
  const charged = unlocked.reason === "granted";
  const remaining =
    balanceLimit === null || balanceUsed === null
      ? null
      : Math.max(balanceLimit - balanceUsed, 0);

  // The reference row's own link wins (it is what the card's «فتح المصدر الرسمي»
  // targets, so the two surfaces agree); the source view is the fallback for
  // rows whose URL columns were never filled.
  const externalUrl =
    referencePrimaryUrl(reference) || sourceViewExternalUrl(view);
  const definiteType = referenceDefiniteType(reference);

  return (
    <>
      <DialogHeader>
        <DialogTitle className="text-base">
          {view.title || fallbackTitle}
        </DialogTitle>
      </DialogHeader>
      <div
        className="max-h-[60dvh] overflow-y-auto text-sm leading-relaxed text-foreground"
        dir="rtl"
      >
        <SourceViewContent
          view={view}
          sourceContent={sourceContent}
          hasFullTextExit={!!(libraryUrl || externalUrl)}
          definiteType={definiteType}
        />
        <CrossRefsSection crossRefs={reference.cross_refs ?? []} />
      </div>

      {notice && (
        <p
          role="status"
          className={cn(
            "flex flex-wrap items-center gap-x-2 gap-y-1 rounded-lg px-3 py-2 text-xs leading-relaxed",
            charged
              ? "bg-primary/5 text-foreground ring-1 ring-primary/15"
              : "bg-muted/50 text-muted-foreground",
          )}
        >
          {charged && (
            <Sparkles
              aria-hidden="true"
              className="h-3.5 w-3.5 shrink-0 text-primary"
            />
          )}
          <span>{notice}</span>
          {/* The meter, right where the spend happened. `limit === null` is an
              unlimited plan — never render it as «0 متبقٍ». */}
          {balanceLimit !== null && remaining !== null && (
            <span className="rounded-full bg-pill px-2 py-0.5 font-medium tabular-nums text-pill-fg">
              {remaining > 0
                ? balanceCopy.remaining(remaining, balanceLimit)
                : balanceCopy.exhausted}
            </span>
          )}
        </p>
      )}

      {/* Tour anchor `ref-exits` — the dialog's two exits: «فتح المصدر الرسمي»
          (the issuing body) + «فتح ال… في ريحان» (our library). Its own step is
          RETIRED; the five-step script describes both in the source-card step
          instead of spotlighting them. */}
      <div className="mt-1 flex flex-wrap items-center gap-2" data-tour="ref-exits">
        {externalUrl && (
          <a
            href={externalUrl}
            target="_blank"
            rel="noopener noreferrer"
            className={cn(
              buttonVariants({ variant: "outline", size: "sm" }),
              "h-8 gap-1.5 px-3 text-xs",
            )}
          >
            <ExternalLink className="h-3.5 w-3.5" />
            فتح المصدر الرسمي
          </a>
        )}
        {/* Dropped entirely when the item has no published page — never a hub
            fallback (§ the reader asked for THIS document, not a list). New tab
            on purpose: this dialog can be open over a streaming chat, and
            navigating the tab away would kill the stream behind it. */}
        {libraryUrl && (
          <Link
            href={libraryUrl}
            target="_blank"
            rel="noopener"
            className={cn(
              buttonVariants({ size: "sm" }),
              "h-8 gap-1.5 px-3 text-xs",
            )}
          >
            <BookOpen className="h-3.5 w-3.5" />
            {referenceLibraryExitLabel(reference)}
          </Link>
        )}
        {sourceContent ? <SourceCopyButton content={sourceContent} /> : null}
      </div>
    </>
  );
}

/**
 * «الإحالات» — the citations the revealed source points at, collapsed by default
 * at the very bottom of the dialog's scroll container.
 *
 * Collapsed is the right default: the reader opened this dialog to read the
 * source, and a مادة can carry ten إحالات whose bodies dwarf it. Opening scrolls
 * the header to the top of the SAME scroll container the body lives in, so the
 * list the click just produced is on screen instead of below the fold —
 * `scrollIntoView` walks up to the nearest scrollable ancestor, which is that
 * container, and the dialog is fixed-position so the page behind never moves.
 *
 * The rAF is not decoration: the scroll must happen after React has committed
 * the expanded list, or it targets a zero-height node and lands short.
 *
 * Both domains reach here — regulations from `cross_references_v2`, cases from a
 * ruling's `referenced_regulations` — because the backend normalises them onto
 * one `CrossRef` shape. Compliance and circulars have no mesh and render nothing.
 *
 * simple_search (§6.1a) — DECISION, recorded because the answer differs per
 * domain and this renderer asks nothing of the caller:
 *
 * - `articles` (`article_full`) **DOES** carry إحالات, and is in fact the mesh's
 *   natural unit: every one of the 34,537 `cross_references_v2` rows is keyed on
 *   a مادة (`target_type = "madda"`), which is exactly what a chunk-level
 *   citation has to approximate today. The backend populates `cross_refs` on the
 *   article shell and this section renders it with NO change here — the panel
 *   reads `reference.cross_refs`, never the source view.
 * - `regulation_docs` (`regulation_summary`) does **not**. A whole نظام has no
 *   single مادة to project إحالات from, and flattening every مادة's mesh into
 *   one list would produce hundreds of entries under a summary. Empty array ⇒
 *   this component returns null, so no guard is needed for it either.
 */
function CrossRefsSection({ crossRefs }: { crossRefs: CrossRef[] }) {
  const [expanded, setExpanded] = useState(false);
  const sectionRef = useRef<HTMLDivElement | null>(null);

  if (crossRefs.length === 0) return null;

  const toggle = () => {
    setExpanded((wasExpanded) => {
      if (!wasExpanded) {
        requestAnimationFrame(() => {
          sectionRef.current?.scrollIntoView({
            behavior: "smooth",
            block: "start",
          });
        });
      }
      return !wasExpanded;
    });
  };

  return (
    <div ref={sectionRef} className="mt-4 border-t pt-2">
      <button
        type="button"
        onClick={toggle}
        aria-expanded={expanded}
        // Tour anchor `ref-crossrefs` — «الإحالات … جرّب افتحها». RETIRED from
        // the five-step script (its `crossrefs-expanded` DOM beat still exists
        // in the engine). `aria-expanded` is published regardless.
        data-tour="ref-crossrefs"
        className="flex w-full items-center gap-1.5 rounded-md py-1 text-start text-xs font-medium text-muted-foreground transition-colors hover:text-foreground"
      >
        <ChevronDown
          aria-hidden="true"
          className={cn(
            "h-3.5 w-3.5 shrink-0 transition-transform",
            expanded && "rotate-180",
          )}
        />
        الإحالات ({crossRefs.length})
      </button>

      {expanded && (
        <ul className="mt-1.5 flex flex-col gap-2.5">
          {crossRefs.map((cr, i) => {
            const heading = [cr.target_reg_title, crossRefUnit(cr)]
              .filter(Boolean)
              .join(" — ");
            return (
              <li
                key={i}
                className="rounded-lg bg-muted/40 px-2.5 py-2 text-xs leading-relaxed"
              >
                {heading && (
                  <span className="block font-medium text-foreground">
                    {heading}
                  </span>
                )}
                {cr.content && (
                  <span className="mt-0.5 block text-muted-foreground">
                    {cr.content}
                  </span>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}

/**
 * Everything that is not a source: the D14 refusal cards and the transport /
 * session / rate-limit states.
 *
 * A 429 is deliberately NOT a quota refusal and must never read as one — the
 * reader's allowance is untouched, they simply asked faster than the shared
 * 20/min library budget allows. Showing «رصيدك انتهى» there would be a lie
 * that pushes a paying user at the pricing page for no reason.
 */
function RevealRefusal({
  result,
  reference,
  onRetry,
  isRetrying,
}: {
  result: Extract<ReferenceSourceResult, { ok: false }>;
  reference: Reference;
  onRetry: () => void;
  isRetrying: boolean;
}) {
  let copy: RefusalCardCopy;
  let retryable = false;

  if (result.kind === "refusal") {
    copy = refusalCardCopy({
      reason: result.refusal.reason,
      resetsAt: result.refusal.resets_at,
      storedCount: result.refusal.stored_count,
    });
  } else if (result.error === "rate_limited") {
    copy = rateLimitedCopy;
    retryable = true;
  } else if (result.error === "unauthorized" || result.error === "no_token") {
    copy = staleSessionCopy;
  } else if (result.error === "not_found") {
    copy = sourceUnavailableCopy;
  } else {
    copy = transportErrorCopy;
    retryable = true;
  }

  return (
    <>
      <DialogHeader>
        <DialogTitle className="text-base">{copy.title}</DialogTitle>
      </DialogHeader>
      <div
        dir="rtl"
        role="status"
        data-testid="reference-source-refusal"
        className="rounded-xl border border-primary/20 bg-gradient-to-b from-primary/5 to-card p-5 text-center"
      >
        <div className="mx-auto mb-3 flex h-11 w-11 items-center justify-center rounded-2xl bg-primary/10 text-primary ring-1 ring-primary/15">
          <Sparkles aria-hidden="true" className="h-5 w-5" />
        </div>
        <p className="mx-auto max-w-sm text-xs leading-relaxed text-muted-foreground">
          {copy.body}
        </p>
        {copy.ctaHref && copy.ctaLabel ? (
          <Link
            href={copy.ctaHref}
            className={cn(
              buttonVariants({ size: "sm" }),
              "mt-4 w-full shadow-sm sm:w-auto sm:px-8",
            )}
          >
            {copy.ctaLabel}
          </Link>
        ) : retryable ? (
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={onRetry}
            disabled={isRetrying}
            className="mt-4 w-full sm:w-auto sm:px-8"
          >
            {isRetrying && (
              <Loader2
                aria-hidden="true"
                className="h-3.5 w-3.5 shrink-0 animate-spin"
              />
            )}
            {revealCopy.retryCta}
          </Button>
        ) : null}
      </div>

      {/* الإحالات survive a refusal on purpose. §1.3 puts the citation mesh in
          the never-gated class — only the source BODY is metered — and this list
          rides the references payload the panel already holds, so showing it
          costs neither a request nor an unlock. Withholding it here would gate
          the mesh by accident, which is exactly what the public blog page's
          credibility layer depends on NOT happening. */}
      <div dir="rtl" className="text-sm">
        <CrossRefsSection crossRefs={reference.cross_refs ?? []} />
      </div>
    </>
  );
}

function SourceCopyButton({ content }: { content: string }) {
  const [copied, setCopied] = useState(false);
  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(content);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    } catch {
      // See ArtifactPreview: clipboard can fail silently — the user can
      // still highlight & copy by hand.
    }
  };
  // No wrapper — the caller owns the action-bar layout.
  return (
    <Button
      type="button"
      variant="ghost"
      size="sm"
      className="h-8 gap-1.5 px-3 text-xs"
      onClick={handleCopy}
    >
      {copied ? (
        <>
          <Check className="h-3.5 w-3.5" />
          تم النسخ
        </>
      ) : (
        <>
          <Copy className="h-3.5 w-3.5" />
          نسخ المحتوى
        </>
      )}
    </Button>
  );
}

function extractSourceContent(view: SourceView): string {
  // A ruling's rendered body is its ملخص — `content` is only ever populated for
  // the few rulings that have no summary at all. Copy what is on screen.
  if (view.source_type === "case") return view.summary || view.content || "";
  // Everything else keys off ONE field name. This is why §6.1a pins the body of
  // both simple_search views to `content` (article_full = the full مادة,
  // regulation_summary = the نظام's summary): they get the body, the dialog's
  // markdown render and the «نسخ المحتوى» button for free, with no branch here.
  if ("content" in view && typeof view.content === "string") return view.content;
  return "";
}

/**
 * «المصدر» — where a ruling was published, under the ملخص in the source dialog.
 *
 * Two thirds of the أحكام corpus is وزارة العدل and has always had its own page behind
 * «فتح المصدر الرسمي». The other 9,860 rulings were parsed out of PDFs and had NOTHING:
 * a قرار زكوي or a ديوان المظالم ruling opened as an unattributed digest. This block is
 * their attribution — the مجلد and page range as text, then the publisher's links.
 *
 * Renders `null` when the view carries neither, which is every وزارة العدل ruling and
 * every artifact persisted before these fields existed. Both are optional on the type,
 * so `?? []` is load-bearing, not defensive noise.
 */
function CaseProvenance({
  view,
}: {
  view: Extract<SourceView, { source_type: "case" }>;
}) {
  const citation = view.citation ?? [];
  const links = view.official_sources ?? [];
  if (citation.length === 0 && links.length === 0) return null;

  return (
    <div className="space-y-2 rounded-lg border border-border/70 bg-muted/30 p-3">
      <p className="text-xs font-medium text-muted-foreground">المصدر</p>

      {citation.length > 0 && (
        <dl className="space-y-1">
          {citation.map((row) => (
            <div key={row.label} className="flex gap-2 text-xs leading-relaxed">
              <dt className="shrink-0 text-muted-foreground">{row.label}:</dt>
              <dd className="font-medium text-foreground">{row.value}</dd>
            </div>
          ))}
        </dl>
      )}

      {links.length > 0 && (
        <div className="flex flex-col gap-1">
          {links.map((link) => (
            <a
              key={link.href}
              href={link.href}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-1.5 text-xs text-primary underline-offset-2 hover:underline"
            >
              <ExternalLink aria-hidden="true" className="h-3 w-3 shrink-0" />
              {link.title}
            </a>
          ))}
        </div>
      )}
    </div>
  );
}

function SourceViewContent({
  view,
  sourceContent,
  hasFullTextExit = false,
  definiteType = "النظام",
}: {
  view: SourceView;
  sourceContent: string;
  /** Does the dialog's action bar actually offer a way to the full document? */
  hasFullTextExit?: boolean;
  /**
   * «النظام» / «اللائحة» / «الدليل» — the reference row's own doc type with the
   * definite article, so a ملخص label names what the reader is actually looking
   * at instead of calling every regulation-backed document a نظام.
   */
  definiteType?: string;
}) {
  // NOTE (2026-08-01): no per-domain link lists in here any more. Every exit
  // lives in the dialog's single action bar — «فتح المصدر الرسمي» + «فتح ال… في ريحان»
  // — so this function renders CONTENT only.
  if (view.source_type === "case") {
    // A ruling reads as its ملخص here, never as raw judgment text (2026-08-03):
    // 8.5k chars of «الوقائع/الأسباب/المنطوق» is a worse answer to "is this the
    // ruling I need?" than the structured digest is. Labelled, so nobody mistakes
    // the digest for the ruling.
    //
    // The label points at the full text ONLY when this dialog actually offers a
    // way to it. Most rulings have no library page yet (100 of 30.5k carry an
    // seo_item_meta slug) and a third carry no details_url either, so an
    // unconditional «النص الكامل في…» would promise an exit that isn't on screen.
    // `view.summary` empty ⇒ `sourceContent` IS the raw ruling (the no-summary
    // fallback), so the label is dropped rather than mislabelling it.
    //
    // «المصدر» sits BELOW the body, and it is the answer to a question the ملخص cannot
    // answer on its own: which مجلد is this from, and at which pages. 9,860 rulings —
    // ديوان المظالم, لجان الزكاة والضريبة, لجان التأمين — carry no `details_url`, so
    // until this block they revealed a digest with no attribution whatsoever.
    return (
      <div className="space-y-3">
        {view.summary && (
          <p className="text-xs font-medium text-muted-foreground">
            {hasFullTextExit ? "ملخص الحكم — النص الكامل بالأسفل" : "ملخص الحكم"}
          </p>
        )}
        {sourceContent && <MarkdownRenderer content={sourceContent} />}
        <CaseProvenance view={view} />
      </div>
    );
  }
  if (view.source_type === "chunk") {
    // A مقطع whose tables survived ingestion renders as prose runs with real
    // grids between them; anything else renders exactly as it always has.
    //
    // ⚠ The tables are NOT handed to `MarkdownRenderer`. It carries no
    // `rehype-raw`, so react-markdown would silently strip the `<table>` and
    // the grid would simply vanish — and turning raw HTML ON there is not the
    // fix, because that same component renders MODEL OUTPUT. Enabling raw
    // markup on generated text to solve a corpus problem trades a real security
    // boundary for a rendering nicety. So the body is split instead: prose
    // through markdown, grids through the component the library already uses.
    const segments = view.display_segments ?? [];
    if (segments.length > 0) {
      return (
        <div className="space-y-3">
          {segments.map((segment, index) =>
            segment.kind === "table" ? (
              <ChunkTable key={`${segment.ref}-${index}`} html={segment.html} />
            ) : (
              <MarkdownRenderer key={index} content={segment.text} />
            ),
          )}
        </div>
      );
    }
    return (
      <div className="space-y-3">
        {sourceContent && <MarkdownRenderer content={sourceContent} />}
      </div>
    );
  }
  if (view.source_type === "gov_service") {
    // A service is STILL a title and a link here, and the guides wing does not
    // change that. The four blocks that used to fill this body — intro,
    // الخطوات, المتطلبات, المستندات المطلوبة — were the issuing entity's own
    // text: it goes stale the moment the entity edits it, and restating it under
    // our chrome made ريحان read as the authority on a process it does not own.
    // They are not coming back.
    //
    // What `/compliance` publishes now is a different document — a SERVICE
    // GUIDE, our own authored rewrite of the entity's official PDF user guide,
    // in full and ungated. It is reached by LEAVING to its own page via the
    // action bar's «افتح الدليل الشامل للخدمة في ريحان», which the backend adds
    // only for the 825 services that have one. Nothing from the guide is
    // inlined into THIS body — that part is still deliberate, and it is why the
    // popup stays a link.
    //
    // The guide DOES reach the agent, as of 2026-08-23: `unfold_service`
    // (`agents/simple_search/unfold.py`) hands `guide_md` plus each screenshot's
    // description to the L6 synthesizer, so an answer about a خدمة can now walk
    // our guide. That is a different surface from this popup and does not change
    // anything here — a rendered body and a model's context are not the same
    // question.
    //
    // So the line below stays deliberately about the OFFICIAL source: it names
    // where the authoritative procedure lives, which is the entity's site, not
    // our guide. Dropped when there IS no exit at all, so it never promises a
    // link that isn't on screen.
    return hasFullTextExit ? (
      <p className="text-sm leading-relaxed text-muted-foreground">
        شروط هذه الخدمة ومستنداتها وخطواتها منشورة على موقع الجهة الرسمي.
      </p>
    ) : null;
  }
  if (view.source_type === "circular") {
    // Full circular body, uncapped — the parent dialog wraps this in a
    // ``max-h-[60vh] overflow-y-auto`` container, so even a ~168k-char outlier
    // scrolls inside the dialog instead of blowing up the layout (same
    // constraint the chunk / gov_service views rely on). Phase C also means
    // those bytes only cross the wire when this one source is asked for.
    return (
      <div className="space-y-3">
        {view.entity_name && (
          <p className="text-xs font-medium text-muted-foreground">
            الجهة: <span className="text-foreground">{view.entity_name}</span>
          </p>
        )}
        {sourceContent && <MarkdownRenderer content={sourceContent} />}
      </div>
    );
  }
  if (view.source_type === "article_full") {
    // simple_search L3 — ONE مادة, served whole and VERBATIM. It must read as
    // statutory text, not as a digest: no «ملخص» framing, no truncation notice,
    // and a header line naming the unit and its parent نظام the way the statute
    // itself does («المادة 81 — نظام العمل»). That line is also what stops the
    // reader mistaking a مادة for the whole document when both kinds of card sit
    // in the same المراجع list under the same sky chip.
    const heading = [
      view.article_num?.trim() ? `المادة ${view.article_num.trim()}` : "نص المادة",
      view.regulation_title?.trim(),
    ]
      .filter(Boolean)
      .join(" — ");
    return (
      <div className="space-y-3">
        <p className="text-xs font-medium text-muted-foreground">{heading}</p>
        {sourceContent && <MarkdownRenderer content={sourceContent} />}
      </div>
    );
  }
  if (view.source_type === "regulation_summary") {
    // simple_search L2 — the WHOLE نظام, represented by its ملخص. Labelled for
    // the same reason the ruling's ملخص is: an unlabelled body of prose under a
    // نظام's title reads as the نظام. It is not — the statute runs to hundreds
    // of thousands of chars and lives on the library page the action bar links
    // to, so the label points there whenever that exit is actually on screen.
    return (
      <div className="space-y-3">
        <p className="text-xs font-medium text-muted-foreground">
          {hasFullTextExit
            ? `ملخص ${definiteType} — النص الكامل بالأسفل`
            : `ملخص ${definiteType}`}
        </p>
        {sourceContent && <MarkdownRenderer content={sourceContent} />}
      </div>
    );
  }
  // Legacy variants (article / section / regulation) ONLY. ⚠ Nothing new may
  // land here: this branch renders bare markdown with no label and no header,
  // which looks like it works — it is how a mis-discriminated variant hides.
  return (
    <div className="space-y-3">
      {sourceContent && <MarkdownRenderer content={sourceContent} />}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/**
 * «WI-9» → ``9``. Anything else → ``null``.
 *
 * The writer publisher writes exactly this form (``agents/writer/publisher.py``),
 * so a strict pattern is the honest test: an alias we cannot parse is an alias we
 * cannot navigate to, and the badge must stay plain text rather than become a
 * button that goes nowhere.
 */
function parseWiSeq(alias: string | null | undefined): number | null {
  if (!alias) return null;
  const match = /^WI-(\d+)$/.exec(alias.trim());
  if (!match) return null;
  const seq = Number(match[1]);
  return Number.isSafeInteger(seq) ? seq : null;
}

/** Best human-readable title for a reference card. */
export function referenceLabel(ref: Reference): string {
  if (ref.title) return ref.title;
  if (ref.domain === "cases") {
    return [ref.entity_name, ref.regulation_title].filter(Boolean).join(" — ") || "قضية";
  }
  if (ref.domain === "articles") {
    // A titleless مادة would otherwise fall through to its PARENT نظام's name
    // and render as if the card were the whole document. Rebuild the unit —
    // «المادة 81 من نظام العمل» — from the two columns that carry it.
    const num = (ref.article_num ?? "").trim();
    const parent = (ref.regulation_title ?? "").trim();
    if (num) return parent ? `المادة ${num} من ${parent}` : `المادة ${num}`;
    return parent || ref.ref_id || "مادة";
  }
  return ref.regulation_title || ref.ref_id || "مرجع";
}

/**
 * Fallback external URL, read off the revealed source view.
 *
 * Only consulted when the reference row itself carries no URL — the row is the
 * authority, so the card and the dialog can never point at different places.
 */
function sourceViewExternalUrl(view: SourceView): string {
  switch (view.source_type) {
    case "chunk":
      return view.regulation_source_url || "";
    case "case":
      return view.details_url || "";
    case "gov_service":
      // `service_url` only. The المنصة الوطنية portal link is no longer on the
      // view, and was never the cited document — see the SourceView type.
      return view.service_url || "";
    case "circular":
      return view.url || "";
    case "article_full":
      // A مادة has no official page of its own — the issuing body publishes the
      // نظام. The parent's landing page IS the مادة's official source.
      return view.regulation_source_url || "";
    case "regulation_summary":
      return view.regulation_source_url || "";
    default:
      // Legacy views only (article / section / regulation). ⚠ Falling here is
      // how a new variant silently loses its «فتح المصدر الرسمي» button — no
      // error, just a missing exit.
      return "";
  }
}

/**
 * Arabic doc type WITH the definite article, for «فتح ال… في ريحان».
 *
 * `regulations_v2.doc_type_raw` is a closed 21-value corpus vocabulary, so this
 * is a lookup rather than morphology: prefixing «ال» programmatically mangles
 * every multi-word entry («تقرير/وثيقة» → «التقرير/الوثيقة», not
 * «التقرير/والثيقة»; «مبادئ وأحكام» takes the article on BOTH words). An
 * unknown single word still gets the naive prefix — a new corpus value should
 * read slightly plain, never broken.
 */
const DEFINITE_DOC_TYPE: Record<string, string> = {
  نظام: "النظام",
  لائحة: "اللائحة",
  "لائحة تنفيذية": "اللائحة التنفيذية",
  "لائحة فنية": "اللائحة الفنية",
  تنظيم: "التنظيم",
  دليل: "الدليل",
  قواعد: "القواعد",
  ضوابط: "الضوابط",
  متطلبات: "المتطلبات",
  "مواصفة قياسية": "المواصفة القياسية",
  إجراءات: "الإجراءات",
  سياسة: "السياسة",
  "جدول/قائمة": "الجدول/القائمة",
  تعليمات: "التعليمات",
  "مبادئ وأحكام": "المبادئ والأحكام",
  اتفاقية: "الاتفاقية",
  "تقرير/وثيقة": "التقرير/الوثيقة",
  "برنامج/خطة": "البرنامج/الخطة",
  ترجمة: "الترجمة",
  "قرار/مرسوم": "القرار/المرسوم",
};

/**
 * Per-domain default when the row carries no usable `doc_type`. EXHAUSTIVE —
 * an omission here would silently render «فتح  في ريحان» with a hole in it.
 *
 * `articles` is «المادة», PINNED by §6.1a: «فتح المادة في ريحان» — and the
 * button lands on the مادة's own `/regulations/{reg}/{article}` page, which the
 * library wing already publishes.
 */
const DEFINITE_DOMAIN_TYPE: Record<ReferenceDomain, string> = {
  regulations: "النظام",
  cases: "الحكم",
  compliance: "الخدمة",
  circulars: "التعميم",
  articles: "المادة",
  regulation_docs: "النظام",
};

export function referenceDefiniteType(ref: Reference): string {
  // `doc_type` belongs to the two regulation-backed domains (a chunk of a
  // document, and the document itself); «غير محدد» is the corpus's "we could
  // not determine one" bucket — a sentinel, not a document type. `articles` is
  // excluded: «فتح المادة في ريحان» stays المادة even under a لائحة.
  const raw = usableDocType(ref);
  if (!raw) return DEFINITE_DOMAIN_TYPE[ref.domain] ?? DEFINITE_DOMAIN_TYPE.regulations;
  const known = DEFINITE_DOC_TYPE[raw];
  if (known) return known;
  return raw.startsWith("ال") ? raw : `ال${raw}`;
}

/**
 * The FULL label on the in-app exit — «فتح النظام في ريحان», «فتح الحكم في
 * ريحان»… and, for a government service, «افتح الدليل الشامل للخدمة في ريحان».
 *
 * Compliance needs the whole string rather than another `DEFINITE_DOMAIN_TYPE`
 * entry because it does not fit the `فتح {X} في ريحان` template at all: the verb
 * changes to «افتح» and the object is «الدليل الشامل للخدمة», not «الخدمة». What
 * the button opens is not the service — it is the SERVICE GUIDE, our own
 * authored rewrite of the entity's official PDF user guide, and naming it
 * «الخدمة» would promise the reader the entity's service page, which is what the
 * OTHER exit («فتح المصدر الرسمي») already is. Two buttons that read as the same
 * destination is the failure this arm avoids.
 *
 * `referenceDefiniteType` is deliberately left returning «الخدمة» for the
 * `compliance` domain: it is also the ملخص label inside `SourceViewContent`,
 * where «الخدمة» is right and the guide has no business appearing.
 *
 * The two callers — the card and the reveal dialog — share this so the same
 * reference cannot be labelled two ways on two surfaces.
 */
export function referenceLibraryExitLabel(ref: Reference): string {
  if (ref.domain === "compliance") return "افتح الدليل الشامل للخدمة في ريحان";
  return `فتح ${referenceDefiniteType(ref)} في ريحان`;
}

/** The single external URL a card's "فتح المصدر الرسمي" button targets. */
function referencePrimaryUrl(ref: Reference): string {
  switch (ref.domain) {
    case "regulations":
      return ref.landing_url || "";
    case "compliance":
      // `ref.url` (the المنصة الوطنية portal) is deliberately NOT a fallback:
      // it is a directory, not the service, and a card that offers it is
      // promising a source it does not deliver. No service_url ⇒ no exit.
      return ref.service_url || "";
    case "circulars":
      return ref.url || "";
    case "cases":
      return ref.details_url || "";
    case "regulation_docs":
      // Same column as a chunk ref: `landing_url` is the نظام's page on the
      // issuing body's site, and a `regulation_docs` ref IS that نظام.
      return ref.landing_url || "";
    case "articles":
      // The parent نظام's landing page — a مادة is not published standalone by
      // any issuing body, so this is the honest official exit for it.
      return ref.landing_url || "";
    default:
      // ⚠ A domain that lands here loses «فتح المصدر الرسمي» with no error.
      return "";
  }
}

/**
 * Arabic label for a cross-ref target unit — «المادة 12».
 *
 * `target_type` is a corpus token, not display text: every one of the 34,537
 * `cross_references_v2` rows carries the Latin transliteration `"madda"`, which
 * the old `${type}:${number}` template printed verbatim into an Arabic RTL
 * panel. Unknown types fall through to the raw token rather than being dropped —
 * a new corpus value should read slightly odd, never vanish.
 */
const CROSS_REF_UNIT_AR: Record<string, string> = {
  madda: "المادة",
  article: "المادة",
  appendix: "الملحق",
};

function crossRefUnit(cr: { target_type: string; target_number: number | null }): string {
  const unit = CROSS_REF_UNIT_AR[cr.target_type] ?? cr.target_type ?? "";
  if (cr.target_number == null) return unit;
  return unit ? `${unit} ${cr.target_number}` : String(cr.target_number);
}
