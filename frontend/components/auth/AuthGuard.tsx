"use client";

import { useEffect } from "react";
import { useRouter, usePathname } from "next/navigation";
import { useAuthStore } from "@/stores/auth-store";
import { useSidebarStore } from "@/stores/sidebar-store";
import { api, conversationsApi, formsApi } from "@/lib/api";
import { consumePendingIntent } from "@/lib/post-login-intent";
import { storeClaimedAnswer } from "@/lib/library/ask";
import { AccountDeletionPendingScreen } from "@/components/auth/AccountDeletionPendingScreen";

interface Props {
  children: React.ReactNode;
}

// Route prefixes that anonymous visitors may view without a session. These
// pages must render for logged-out users — no /login redirect, no
// `return null`. The public share-by-link surface (/blog/{token}) serves an
// immutable snapshot to prospects without an account; /terms + /privacy are
// the public legal pages reached from the login footer; /pricing is the public
// plans page (a pre-signup decision); /audiences is the public «ريحان يستهدف
// مين؟» page; /masking is the public «تقنيع المعرّفات» explainer linked from
// the وضع السرية settings dialog — all reachable before signing up. /about_us
// is the marketing-landing content at an address that does NOT bounce
// authenticated users (the bare "/" does) — their way back to the front door.
// /regulations + /compliance are the public SEO library surfaces (Phase 2) —
// anon searchers land on them from Google, signed-in users may browse freely.
// /judgments is the الأحكام القضائية wing — public and anon-viewable like the
// other library surfaces, but currently `noindex` pending the PDPL
// anonymization audit (a crawler gate, NOT an auth gate — it must still render
// for logged-out visitors who reach it from an internal link or a shared URL).
// NOTE: /cases is the PRIVATE case-workspace route and is a different path — it
// stays out of this list and stays disallowed in app/robots.ts.
// /calculators is the public calculators wing (Phase 3) — free, never gated.
// /circulars is the public التعاميم wing and /forms the public نماذج wing (Phase
// 5 / Phase 3) — anon-viewable SEO surfaces like the others. /vs-chatgpt is the
// «ريحان مقابل ChatGPT» comparison page (inside the عن ريحان menu); /library and
// /learn are the المكتبة القانونية and اكتشف hub endpoints (placeholder today,
// filled by later phases) — all three are public marketing surfaces the global
// header links to and must render for anon visitors.
// Routes that sit UNDER a public prefix but are private. Checked before the
// prefix list, because the prefix test is a `startsWith` and would otherwise
// swallow them: /library is a public marketing hub, but /library/mine is the
// authed «مكتبتي» shelf — a per-user reading history. Left public it renders for
// anonymous visitors who then watch every shelf call 401.
const PRIVATE_EXCEPTIONS = ["/library/mine"] as const;

const PUBLIC_PREFIXES = [
  "/blog",
  "/terms",
  "/privacy",
  "/pricing",
  "/audiences",
  "/masking",
  "/about_us",
  "/vs-chatgpt",
  "/library",
  "/learn",
  "/regulations",
  "/compliance",
  "/calculators",
  "/circulars",
  "/forms",
  "/judgments",
] as const;

function isPublicPath(pathname: string | null): boolean {
  if (!pathname) return false;
  // The marketing landing page is the public front door. Matched exactly — a
  // bare-prefix "/" would swallow every authenticated route.
  if (pathname === "/") return true;
  // Private routes nested under a public prefix win over the prefix match.
  if (
    PRIVATE_EXCEPTIONS.some(
      (prefix) => pathname === prefix || pathname.startsWith(`${prefix}/`),
    )
  ) {
    return false;
  }
  return PUBLIC_PREFIXES.some(
    (prefix) => pathname === prefix || pathname.startsWith(`${prefix}/`),
  );
}

export function AuthGuard({ children }: Props) {
  const router = useRouter();
  const pathname = usePathname();
  const { user, isAuthenticated, isLoading, loadUser, revalidateSession } =
    useAuthStore();

  const isPublic = isPublicPath(pathname);

  useEffect(() => {
    loadUser();
  }, [loadUser]);

  // Revalidate the session whenever the tab becomes visible again. While a
  // tab is backgrounded the proactive-refresh timer is throttled/frozen, so
  // the token can expire silently; refreshing on the visibility change
  // catches a dead session before the user acts (e.g. before sending).
  //
  // We listen ONLY to `visibilitychange` — never the window `focus` event.
  // `focus` also fires every time a native dialog (file picker, print,
  // basic-auth prompt) closes and returns focus to the page. That would
  // force a token refresh mid-interaction which races with Supabase's own
  // single-use-refresh-token rotation and spuriously logs the user out.
  // `visibilitychange` does not fire for those dialogs — the tab stays
  // "visible" the whole time — so it is the safe signal.
  useEffect(() => {
    function handleVisible() {
      if (document.visibilityState === "visible") {
        void revalidateSession();
      }
    }
    document.addEventListener("visibilitychange", handleVisible);
    return () => {
      document.removeEventListener("visibilitychange", handleVisible);
    };
  }, [revalidateSession]);

  // Post-login intent: an anonymous visitor triggered a "resume after login"
  // action on a public page → the intent was stashed in sessionStorage → they
  // signed in (email, signup, or the OAuth full-page round-trip — all funnel
  // through an authed render of this guard). This is the ONE consumer for every
  // intent type. ``consumePendingIntent`` clears on read, so re-renders and
  // StrictMode double-effects can't run a flow twice. Best-effort — any failure
  // (revoked token, network) just leaves the user on their default landing.
  //
  //   chat_with_blog      → create a conversation, import the blog, land in chat
  //                         (blog_import plan §D6).
  //   claim_anon_answer   → claim the full اسأل ريحان answer, stash it for the
  //                         widget, return to the source page (continuity moment).
  //   open_form_in_writer → copy the form into قوالبي, open the writer.
  useEffect(() => {
    if (isLoading || !isAuthenticated) return;
    const intent = consumePendingIntent();
    if (!intent) return;
    void (async () => {
      try {
        if (intent.type === "chat_with_blog") {
          const created = await conversationsApi.create({ case_id: null });
          const newId = created.conversation.conversation_id;
          await api.createBlogItem(newId, intent.token);
          useSidebarStore.getState().setSelectedConversation(newId);
          router.replace(`/chat/${newId}`);
        } else if (intent.type === "claim_anon_answer") {
          const claimed = await api.claimAnonAnswer(
            intent.question_id,
            intent.session_key,
          );
          storeClaimedAnswer(claimed);
          router.replace(intent.return_to);
        } else if (intent.type === "open_form_in_writer") {
          const template = await formsApi.openInWriter(intent.slug);
          router.replace(`/templates/${template.template_id}`);
        }
      } catch {
        // Dropped silently — the default post-login landing takes over.
      }
    })();
  }, [isLoading, isAuthenticated, router]);

  useEffect(() => {
    if (isLoading) return;
    // Logged-in users hitting the login screen OR the marketing landing page
    // belong in the app — send them to /chat (their real home). This runs
    // BEFORE the public-page early-return below so the landing ("/") bounces
    // authenticated visitors even though it's a public path for anon.
    if (isAuthenticated && (pathname === "/login" || pathname === "/")) {
      router.replace("/chat");
      return;
    }
    // Other public pages (/blog, /terms, /privacy, /pricing) never redirect —
    // anon visitors must see them, and logged-in users may browse them freely.
    if (isPublic) return;
    if (!isAuthenticated && pathname !== "/login") {
      router.replace("/login");
    }
  }, [isLoading, isAuthenticated, pathname, router, isPublic]);

  // Public pages render immediately for everyone (logged-in or anon) without
  // waiting on the session probe or gating on auth state.
  if (isPublic) {
    // Exception: an authenticated visitor on the landing ("/") is mid-bounce
    // to /chat (effect above) — don't flash the marketing page at them.
    if (pathname === "/" && !isLoading && isAuthenticated) {
      return null;
    }
    return <>{children}</>;
  }

  if (isLoading) {
    return (
      <div className="flex h-screen items-center justify-center gap-3">
        <div className="h-8 w-8 animate-spin rounded-full border-4 border-primary border-t-transparent" />
        <span className="text-sm text-muted-foreground">
          جارٍ تحميل الجلسة...
        </span>
      </div>
    );
  }

  // Account inside the 30-day deletion grace period: every data route 403s
  // server-side, so the app is replaced by a restore-or-logout screen. Public
  // pages already returned above — a pending user may still browse /pricing,
  // /blog, etc.
  if (isAuthenticated && user?.deletion_pending && !isPublic) {
    return <AccountDeletionPendingScreen />;
  }

  if (!isAuthenticated && pathname !== "/login") {
    return null;
  }

  return <>{children}</>;
}
