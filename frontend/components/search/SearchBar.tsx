"use client";

import { useCallback, useId, useRef, useState } from "react";
import { Loader2, Search, X } from "lucide-react";
import { cn } from "@/lib/utils";
import { useAuthStore } from "@/stores/auth-store";
import { SearchCtaModal } from "@/components/search/SearchCtaModal";
import { SEARCH_COPY, SEARCH_MIN_LENGTH } from "@/lib/search/copy";

/**
 * The anon conversion gate (D9). Present ⇒ this box is registered-only and an
 * anonymous visitor's click opens `SearchCtaModal` instead of searching. Absent
 * ⇒ a plain live search box that never reads the auth store at all, which is
 * what the already-authed surfaces (مكتبتي، مدوّناتي، قوالبي) want.
 *
 * One prop, not two, on purpose: `returnTo` is REQUIRED whenever the gate is on,
 * so it is impossible to ship a gated box whose CTA silently drops the visitor
 * on `/chat`.
 */
export interface SearchBarGate {
  /**
   * Where `?next=` should return the visitor after signup/login — a
   * site-relative path, query string included (`/regulations?q=إجازة الأمومة`).
   * Validated on the read side by `safeNext`; anything not allowlisted
   * degrades to `/chat`.
   */
  returnTo: string;
}

export interface SearchBarProps {
  /** Current raw value. The CALLER owns the state and the debounce. */
  value: string;
  onChange: (value: string) => void;
  placeholder: string;
  /** Accessible name — the placeholder is not a label. */
  ariaLabel: string;
  /** D9 gate. Omit on surfaces that are already behind auth. */
  gate?: SearchBarGate;
  /** Swap the clear button for a spinner while a query is in flight. */
  isPending?: boolean;
  /**
   * Characters required before the «اكتب ٣ أحرف» hint stops showing. Defaults to
   * `SEARCH_MIN_LENGTH` (3) — the floor the BM25 library surfaces need, because
   * `search_service.normalize_query` 400s below it.
   *
   * ⚠ This is NOT a licence for surfaces to disagree on a whim; the two old
   * search boxes disagreeing is the bug this component ended. It exists because
   * `/chats` is not a BM25 surface at all. It searches `conversations` through
   * `GET /api/v1/conversations?q=` (trigram over `title_ar` + `messages.content`),
   * which has NO minimum length — verified at `api/conversations.py:41`. Forcing
   * 3 there would silently remove one- and two-character search from a shipped
   * feature that has nothing to do with this project, and a reader with six
   * conversations typing «ع» to find one is a real case.
   */
  minLength?: number;
  /** Extra classes on the WRAPPER (sizing/placement), not on the input. */
  className?: string;
}

/**
 * The one search input in the app (plan §6.1, success criterion 2).
 *
 * ── ONE MODE, and this is D9's doing ────────────────────────────────────────
 * The plan once carried a second `mode="form"` variant — a real GET `<form>`
 * whose submission navigated to `?q=…` — so that anonymous `?q=` responses
 * could stay crawlable and ISR-cacheable. Registered-only search deleted that
 * whole branch: no anon `?q=` response is ever baked or indexed, so there is
 * nothing for a form navigation to buy. Every surface now uses the live,
 * debounced `/chats` pattern, and this component takes NO `mode` prop. If you
 * find `mode="form"` in a stale copy of the plan, that is the superseded D2.
 *
 * ── THE RTL RESOLUTION (§6.1) ───────────────────────────────────────────────
 * The two boxes this replaces disagreed with each other: `ConversationSearch`
 * put the icon at `end-2.5` with the clear button at `start-2`;
 * `JudgmentsFilterBar` put the icon at `start-3`. This picks ONE and every
 * caller adopts it —
 *
 *     icon LEADING the text at `start-3`  ·  clear button at `end-2.5`
 *
 * In RTL `start` is the physical RIGHT edge, so the magnifier sits where the
 * reader begins typing and the dismiss control sits at the far end, out of the
 * way of the text. Do not re-litigate this per surface: the disagreement itself
 * was the bug.
 *
 * ── The anonymous branch ────────────────────────────────────────────────────
 * A gated box for an anonymous visitor is `readOnly`, not `disabled`: it keeps
 * its focus ring, its cursor and its place in the tab order, because it is a
 * real control — it just answers with a pitch. The modal opens on
 * `pointerdown` or on a keystroke, and deliberately NOT on `focus`: Radix
 * returns focus to the trigger when a dialog closes, so a focus trigger would
 * reopen the modal forever.
 */
export function SearchBar({
  value,
  onChange,
  placeholder,
  ariaLabel,
  gate,
  isPending,
  minLength,
  className,
}: SearchBarProps) {
  const inputRef = useRef<HTMLInputElement>(null);
  const hintId = useId();
  const [ctaOpen, setCtaOpen] = useState(false);

  // Read only when gated, so an authed-only surface never subscribes to auth
  // state it does not use. `isLoading` is the session probe: treating an
  // unresolved session as anonymous would flash the pitch at signed-in readers
  // on every page load (anon_conversion_popup T7).
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated);
  const isLoading = useAuthStore((s) => s.isLoading);
  const locked = Boolean(gate) && !isAuthenticated && !isLoading;
  const effectiveMinLength = minLength ?? SEARCH_MIN_LENGTH;

  const handleClear = useCallback(() => {
    onChange("");
    inputRef.current?.blur();
  }, [onChange]);

  const openCta = useCallback(() => setCtaOpen(true), []);

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent<HTMLInputElement>) => {
      if (e.key === "Escape") {
        handleClear();
        return;
      }
      if (!locked) return;
      // Tab must still move focus THROUGH a locked box, and a bare modifier
      // press is not a gesture. Everything else — a letter, Enter, Space — is
      // someone trying to search, and that is the conversion moment.
      if (e.key === "Tab" || e.key === "Shift" || e.key === "Control") return;
      if (e.key === "Alt" || e.key === "Meta") return;
      e.preventDefault();
      openCta();
    },
    [handleClear, locked, openCta],
  );

  // ⚠ NOTHING OPENS THIS MODAL EXCEPT A GESTURE. The session probe can still be
  // in flight when a fast reader starts typing, and if it then resolves
  // ANONYMOUS the box they are typing into is inert. The tempting fix — an
  // effect that converts as soon as `locked` turns true with a non-empty value
  // — is WRONG: a caller seeds this box from a shared `?q=` link, so that
  // effect would fire the modal on ARRIVAL. That is an interstitial, on the one
  // surface where an interstitial is both a Google-demotion risk and an ambush.
  // The inert window is milliseconds wide and closes itself: the very next
  // keystroke goes through `handleKeyDown`, which is locked, and converts.

  const trimmed = value.trim();
  const showHint =
    !locked && trimmed.length > 0 && trimmed.length < effectiveMinLength;

  return (
    <div dir="rtl" className={cn("w-full sm:w-72", className)}>
      <div className="relative">
        {/* RTL: `start-3` is the physical RIGHT edge — the icon leads the text
            the way the reader enters it. See the RTL note above. In flight, the
            magnifier BECOMES the spinner rather than the clear button doing so:
            a dismiss control that vanishes on every keystroke is a control the
            reader learns not to aim for. */}
        {isPending ? (
          <Loader2
            aria-hidden="true"
            className="pointer-events-none absolute start-3 top-1/2 h-4 w-4 -translate-y-1/2 animate-spin text-text-muted"
          />
        ) : (
          <Search
            aria-hidden="true"
            className="pointer-events-none absolute start-3 top-1/2 h-4 w-4 -translate-y-1/2 text-text-muted"
          />
        )}

        <input
          ref={inputRef}
          type="search"
          value={value}
          // A locked box never mutates: `readOnly` stops the keystroke the
          // `keydown` handler did not (paste, IME commit, dictation).
          readOnly={locked}
          onChange={(e) => onChange(e.target.value)}
          onKeyDown={handleKeyDown}
          onPointerDown={locked ? openCta : undefined}
          placeholder={placeholder}
          aria-label={ariaLabel}
          aria-describedby={showHint ? hintId : undefined}
          aria-haspopup={locked ? "dialog" : undefined}
          className={cn(
            "h-9 w-full rounded-full border border-border bg-card ps-9 pe-9 text-sm text-foreground outline-none transition-colors",
            "placeholder:text-text-muted focus:border-primary/50",
            // `type="search"` earns the searchbox role and native Esc handling,
            // but WebKit also draws its OWN cancel button — which would sit
            // beside ours, in the wrong place, styled by nobody. Hide it.
            "[&::-webkit-search-cancel-button]:appearance-none",
            locked && "cursor-pointer",
          )}
        />

        {/* `end-2.5` — the far end of the box, clear of the text. Rendered for
            a LOCKED box too: the value there came from someone else's shared
            `?q=` link, and a visitor must always be able to get back to the
            unfiltered wing they were shown. */}
        {value.length > 0 && (
          <button
            type="button"
            onClick={handleClear}
            aria-label={SEARCH_COPY.clear}
            className="absolute end-2.5 top-1/2 -translate-y-1/2 rounded-sm p-0.5 text-text-muted transition-colors hover:text-foreground"
          >
            <X aria-hidden="true" className="h-3.5 w-3.5" />
          </button>
        )}
      </div>

      {/* The server's 3-character floor, surfaced inline instead of as a 400.
          Reserves no layout: it replaces nothing and appears under a box that
          already sits in a `gap`-spaced row. */}
      {showHint && (
        <p id={hintId} className="mt-1 ps-3 text-xs leading-tight text-text-muted">
          {SEARCH_COPY.minLengthHint}
        </p>
      )}

      {gate && (
        <SearchCtaModal
          open={ctaOpen}
          onOpenChange={setCtaOpen}
          returnTo={gate.returnTo}
        />
      )}
    </div>
  );
}
