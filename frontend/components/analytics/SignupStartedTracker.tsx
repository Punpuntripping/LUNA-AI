"use client";

import { useEffect, useRef } from "react";
import { track } from "@/lib/analytics/client";
import { DEFAULT_NEXT, safeNext } from "@/lib/safe-next";

/**
 * `signup_started` — the third step of the funnel in `product_analytics.md` §5.4
 * (`gate_view` → `gate_cta_click` → **signup_started** → `signup_completed`).
 *
 * Renders nothing. It exists as its own client leaf for one reason: `/login` is
 * a SERVER component, and it has to stay one. `LoginForm` already documents the
 * trap — `useSearchParams()` would drag the whole route into client rendering
 * and fail `next build` with «should be wrapped in a suspense boundary» — so
 * this reads `window.location.search` inside an effect, which is that file's
 * existing idiom and needs no boundary.
 *
 * ⚠ T4 — `next_path` is a PATHNAME. `?next=` legitimately carries a query
 * string (the search CTA returns a reader to «/regulations?q=…», and that `q` is
 * user-typed legal text), so the value is validated through `safeNext` and then
 * cut at the first `?` or `#`. What is sent is the page whose gate produced this
 * signup and nothing else.
 *
 * ⚠ T9 — never breaks the page: one guarded effect, and the whole call wrapped.
 */
export function SignupStartedTracker() {
  /** Effects can re-run under StrictMode; a funnel step must count once. */
  const firedRef = useRef(false);

  useEffect(() => {
    if (firedRef.current) return;

    const params = new URLSearchParams(window.location.search);
    // The page is only a signup START when it OPENS on signup — that is what a
    // gate CTA («ابدأ الآن», «سجّل مجاناً») sends. Toggling to «إنشاء حساب جديد»
    // inside the form afterwards is a different, later decision and is not this
    // event.
    if (params.get("mode") !== "register") return;

    firedRef.current = true;
    try {
      // The key is OMITTED rather than sent empty when there is nothing to
      // attribute: an absent `next_path` reads as «this signup carries no gate»,
      // while an empty string would read as «there was one, and it was blank».
      const nextPath = attributionPath(params.get("next"));
      track("signup_started", nextPath ? { next_path: nextPath } : undefined);
    } catch {
      // T9 — analytics must never surface anything to the visitor.
    }
  }, []);

  return null;
}

/**
 * The page this signup should be attributed back to, or "" when there is none.
 *
 * "" covers both «no `next` at all» (someone opened /login directly) and «a
 * `next` that validated down to the default /chat» — neither names a gate, and
 * emitting "/chat" for them would invent an attribution to a page the visitor
 * never saw.
 */
function attributionPath(raw: string | null): string {
  const next = safeNext(raw);
  if (!raw || next === DEFAULT_NEXT) return "";

  // Drop the query string and fragment — T4. `safeNext` deliberately preserves
  // them for the redirect; the beacon must not.
  const cut = [next.indexOf("?"), next.indexOf("#")]
    .filter((i) => i >= 0)
    .sort((a, b) => a - b)[0];
  return cut === undefined ? next : next.slice(0, cut);
}
