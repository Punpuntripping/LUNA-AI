"use client";

import { SearchBar } from "@/components/search/SearchBar";
import { SEARCH_PRIVATE_COPY } from "@/lib/search/copy";

/**
 * The `/chats` search box — now a THIN DELEGATE over `<SearchBar>`
 * (bm25_navigation_search.md §9: "`<SearchBar>` is the only search input in the
 * app; `ConversationSearch` and `JudgmentsFilterBar`'s inline box are both gone
 * or delegating").
 *
 * ── WHY THE FILE SURVIVES INSTEAD OF BEING DELETED ──────────────────────────
 * It still does one job: bind the `/chats` copy and the full-width sizing, so
 * `ChatsPage` names the surface once and knows nothing about search internals.
 * Inlining `<SearchBar>` at the call site would move two Arabic strings back
 * into a component, which is exactly what `lib/search/copy.ts` exists to stop.
 *
 * ── WHAT CHANGED, AND WHY IT HAD TO ─────────────────────────────────────────
 * The old box put the magnifier at `end-2.5` and the clear button at `start-2`;
 * `JudgmentsFilterBar` put its magnifier at `start-3`. The two disagreed, and
 * THE DISAGREEMENT WAS THE BUG — §6.1 picks one resolution (icon leading the
 * text at `start-3`, clear at `end-2.5`) and every caller adopts it. In RTL
 * `start` is the physical right edge, so the magnifier now sits where the
 * reader begins typing rather than trailing it. Do not re-litigate it here.
 *
 * No `gate` prop: that is D9's anonymous conversion modal, and `/chats` is
 * behind auth. Omitting it keeps this box from subscribing to the auth store.
 *
 * The 250 ms debounce stays with the CALLER (`ChatsPage` → `useSearchQuery`) —
 * this component is a controlled input with no state of its own beyond what
 * `SearchBar` provides. The caller passes `minLength: 1`; see the prop below.
 */
interface ConversationSearchProps {
  value: string;
  onChange: (value: string) => void;
  /** Swap the magnifier for a spinner while a search request is in flight. */
  isPending?: boolean;
}

export function ConversationSearch({
  value,
  onChange,
  isPending,
}: ConversationSearchProps) {
  return (
    <SearchBar
      value={value}
      onChange={onChange}
      placeholder={SEARCH_PRIVATE_COPY.chats.placeholder}
      ariaLabel={SEARCH_PRIVATE_COPY.chats.ariaLabel}
      isPending={isPending}
      // `/chats` is not a BM25 surface — it hits `GET /api/v1/conversations?q=`
      // (trigram), which has no minimum length. Keeping the library's 3-char
      // floor here would have silently deleted 1–2 character search from a
      // shipped feature. See the `minLength` docs on `SearchBarProps`.
      minLength={1}
      // `SearchBar` defaults to `w-full sm:w-72`; /chats wants the full width of
      // its `max-w-xl` column at every breakpoint. `cn`'s tailwind-merge drops
      // the losing `sm:w-72`.
      className="sm:w-full"
    />
  );
}
