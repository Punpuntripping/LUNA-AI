"use client";

import { useEffect } from "react";
import {
  asLibraryItemPageType,
  hasPendingIntent,
  setPendingIntent,
} from "@/lib/post-login-intent";

/**
 * «اسأل ريحان» → sign in → the page is ALREADY in the chat.
 *
 * Every «اسأل ريحان» affordance on a public library page sends an anonymous
 * reader to `/login?intent=ask_rayhan&page_type=…&page_id=…&page_title=…`:
 * the popup's «سجّل وجرّب اسأل ريحان» stub (what an anon reader gets whenever
 * `ANON_ASK_ENABLED` is off, which is the live state) and the inline «اسأل
 * ريحان عن هذه المادة» card on a مادة page. Both are server-rendered `<Link>`s,
 * so neither can stash anything on the way out — the querystring was the whole
 * carrier, and **nothing read it**. The reader signed in and landed on an empty
 * /chat holding none of the page they had been reading, which is the bug this
 * component exists to close.
 *
 * It reads that querystring once and converts it into the EXISTING
 * `chat_with_library_item` post-login intent, so the resume itself is not a new
 * mechanism: `AuthGuard` stays the one consumer, and the reader lands in a fresh
 * conversation with the page already attached as a composer chip — byte for byte
 * the flow «تحدّث مع ريحان عن هذه الصفحة» runs for a signed-in reader
 * (`.claude/plans/simple_search_family.md` §8, "Anon return path").
 *
 * Ordering is load-bearing and it is free: this is a CHILD of `AuthGuard`, and
 * React flushes child effects before parent ones — so the intent is in storage
 * before the guard's consumer looks for it, including for a visitor who is
 * already signed in and is being bounced straight to /chat.
 *
 * Renders nothing, and reads `window.location.search` inside an effect rather
 * than `useSearchParams()` — `/login` is a SERVER component and must stay one
 * (see `SignupStartedTracker`, whose idiom this follows).
 */
export function AskRayhanLoginIntent() {
  useEffect(() => {
    try {
      const params = new URLSearchParams(window.location.search);
      if (params.get("intent") !== "ask_rayhan") return;

      // A URL is the weaker claim — see `hasPendingIntent`. A teaser's
      // «سجّل مجاناً لعرض الإجابة كاملة» has already stored a `claim_anon_answer`
      // by the time its login link resolves, and that answer wins.
      if (hasPendingIntent()) return;

      const pageType = asLibraryItemPageType(params.get("page_type"));
      const pageId = (params.get("page_id") ?? "").trim();
      // An uncarryable wing (`/circulars`, `/forms`, `/calculators`) still
      // renders the popup and still links here. There is nothing to carry, so
      // the reader lands on their normal /chat — today's behaviour, not a
      // failure, and far better than a stored intent the backend would refuse.
      if (!pageType || !pageId) return;

      setPendingIntent({
        type: "chat_with_library_item",
        page_type: pageType,
        page_id: pageId,
        // The chip's label until the POST returns the server's own title.
        title: (params.get("page_title") ?? "").trim() || null,
      });
    } catch {
      // Storage unavailable, or a malformed query string. The visitor just
      // signs in and lands on /chat — never a broken login page.
    }
  }, []);

  return null;
}
