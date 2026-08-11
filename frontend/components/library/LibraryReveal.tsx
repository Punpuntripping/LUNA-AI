"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useState,
  type ReactNode,
} from "react";
import { useAuthStore } from "@/stores/auth-store";
import {
  fetchFullContent,
  fetchLibraryBalance,
  type FullContentType,
  type FullContentPayload,
  type LibraryBalance,
} from "@/lib/library/full-content";
import {
  balanceCopy,
  refusalCardCopy,
  rateLimitedCopy,
  sourceUnavailableCopy,
  staleSessionCopy,
  transportErrorCopy,
  type RefusalCardCopy,
} from "@/lib/library/gate-copy";

/**
 * ONE metered reveal, usable from more than one place on a page.
 *
 * `FullContentGate` used to own this state privately, which was right while a
 * document had exactly one reveal action. The judgment page has two — «ملخص
 * ريحان» in the المعلومات الأساسية card and «اعرض النص كاملاً» under the ruling —
 * and they are NOT two purchases: one `/library/full/judgment/{slug}` response
 * carries both the summary and the full sections, so both surfaces must read and
 * write ONE state or the page would charge (and load) twice for one ruling.
 *
 * So the state lives in `useLibraryReveal`, and `LibraryRevealProvider` shares a
 * single instance down the tree. A page that never renders the provider is
 * unchanged: `FullContentGate` falls back to its own instance.
 */

interface UseLibraryRevealInput {
  contentType: FullContentType;
  /** The item key the authed endpoint resolves (the page's own slug). */
  fullKey: string;
  /**
   * Is anything actually behind the gate on this page? Only gates the passive
   * balance read — the reveal itself is always callable, because the caller that
   * offers the action is the one that knows whether it buys anything.
   */
  gated: boolean;
  /**
   * False for the instance a `FullContentGate` keeps as a fallback while a
   * provider is present: it must not fire a second `/usage` request for a
   * balance nobody renders.
   */
  enabled?: boolean;
}

export interface LibraryRevealState {
  isAuthenticated: boolean;
  /** The revealed payload, or null while the anon render still stands. */
  full: FullContentPayload | null;
  isRevealing: boolean;
  /** A refusal / error card, or null. Replaces the action when set. */
  card: RefusalCardCopy | null;
  balance: LibraryBalance | null;
  /** Spend one unlock and fetch the full document. CALL ONLY FROM A GESTURE. */
  reveal: () => void;
}

/**
 * The reveal state machine (plan §5.1). The charge sits on the CLICK, never on a
 * mount — a signed-in reader skimming ten judgments must not burn ten unlocks —
 * so nothing here calls `fetchFullContent` outside `reveal`.
 *
 * Failure is layered and the layers stay distinguishable (the PART 5 bug was a
 * `null` return that made an exhausted quota look exactly like a dead session):
 *   402         → the refusal card, driven by `reason`
 *   401/403     → «انتهت جلستك» (never a redirect — these are public pages)
 *   429         → the shared 20/min budget; says explicitly nothing was charged
 *   404         → «تعذّر عرض هذا المصدر» (retrying cannot help)
 *   network/5xx → a retryable «تعذّر فتح هذا المصدر»
 */
export function useLibraryReveal({
  contentType,
  fullKey,
  gated,
  enabled = true,
}: UseLibraryRevealInput): LibraryRevealState {
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated);
  const [full, setFull] = useState<FullContentPayload | null>(null);
  const [isRevealing, setIsRevealing] = useState(false);
  const [card, setCard] = useState<RefusalCardCopy | null>(null);
  const [balance, setBalance] = useState<LibraryBalance | null>(null);

  // A client-side navigation between two documents reuses this instance — drop
  // any revealed content so item B never renders under item A's key.
  useEffect(() => {
    setFull(null);
    setCard(null);
    setIsRevealing(false);
  }, [contentType, fullKey]);

  // Passive balance — «no prompt, but never a silent meter» (§5.1). Reading the
  // allowance costs nothing and charges nothing, so this one IS a mount effect.
  useEffect(() => {
    if (!enabled || !isAuthenticated || !gated) {
      setBalance(null);
      return;
    }
    let active = true;
    void (async () => {
      const next = await fetchLibraryBalance();
      if (active) setBalance(next);
    })();
    return () => {
      active = false;
    };
  }, [enabled, isAuthenticated, gated]);

  const reveal = useCallback(async () => {
    setIsRevealing(true);
    setCard(null);

    const result = await fetchFullContent<FullContentPayload>(
      contentType,
      fullKey,
    );

    if (result.ok) {
      setFull(result.data);
      // An unlock may have just been spent — resync the chip so the next
      // document in this visit shows a truthful balance.
      void fetchLibraryBalance().then(setBalance);
    } else if (result.kind === "refusal") {
      setCard(
        refusalCardCopy({
          reason: result.refusal.reason,
          resetsAt: result.refusal.resets_at,
          storedCount: result.refusal.stored_count,
        }),
      );
    } else if (result.error === "unauthorized" || result.error === "no_token") {
      setCard(staleSessionCopy);
    } else if (result.error === "rate_limited") {
      // NOT a refusal and NOT a network fault. `/library/full` shares one 20/min
      // budget with the reference-source endpoint (D13.2), so this is reachable
      // in normal use — and its copy says explicitly that nothing was charged.
      setCard(rateLimitedCopy);
    } else if (result.error === "not_found") {
      // Unknown slug, unpublished form, or a corpus row that vanished. Retrying
      // cannot help, so it must not render as a retryable transport error.
      setCard(sourceUnavailableCopy);
    } else {
      setCard(transportErrorCopy);
    }

    setIsRevealing(false);
  }, [contentType, fullKey]);

  return { isAuthenticated, full, isRevealing, card, balance, reveal };
}

/**
 * The passive meter beside a reveal action — «no prompt, but never a silent
 * meter» (§5.1). Silent when the allowance could not be read (anonymous, locked
 * account, or a failed usage call): a wrong number beside a spend button is
 * worse than no number.
 *
 * Lives here rather than in `FullContentGate` because the gate is no longer the
 * only spend surface — a page whose ONLY action is «ملخص ريحان» (a short ruling
 * that ships its text whole) needs the same chip, and a metered click with no
 * meter anywhere on the page is the thing §5.1 rules out.
 */
export function BalanceChip({
  balance,
}: {
  balance: LibraryBalance | null;
}) {
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
    <p className="flex flex-wrap items-center justify-center gap-x-2 gap-y-0.5 text-xs text-text-secondary">
      <span className="rounded-full bg-pill px-2.5 py-0.5 font-medium tabular-nums text-pill-fg">
        {label}
      </span>
      {renews && <span className="text-muted-foreground">{renews}</span>}
    </p>
  );
}

const LibraryRevealContext = createContext<LibraryRevealState | null>(null);

/**
 * The shared reveal, or `null` on a page that renders no provider — in which
 * case the consumer owns its own state. Consumers must handle `null`; that is
 * what keeps the four single-action wings byte-identical to before.
 */
export function useSharedLibraryReveal(): LibraryRevealState | null {
  return useContext(LibraryRevealContext);
}

interface LibraryRevealProviderProps {
  contentType: FullContentType;
  fullKey: string;
  /**
   * Is ANYTHING on this page behind the gate — the body, the summary, or both.
   * Wider than the body-only `gated` a `FullContentGate` takes: a short ruling
   * that ships whole still has a gated «ملخص ريحان», and the balance chip has to
   * load for it.
   */
  gated: boolean;
  children: ReactNode;
}

/** Wrap the region whose reveal actions must all spend the SAME unlock. */
export function LibraryRevealProvider({
  contentType,
  fullKey,
  gated,
  children,
}: LibraryRevealProviderProps) {
  const state = useLibraryReveal({ contentType, fullKey, gated });
  return (
    <LibraryRevealContext.Provider value={state}>
      {children}
    </LibraryRevealContext.Provider>
  );
}
