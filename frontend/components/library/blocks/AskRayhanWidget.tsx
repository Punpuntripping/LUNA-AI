"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { MessageCircle, Send, Sparkles, X, Lock, Loader2 } from "lucide-react";
import { cn } from "@/lib/utils";
import { buttonVariants } from "@/components/ui/button";
import { MarkdownRenderer } from "@/components/chat/MarkdownRenderer";
import { useAuthStore } from "@/stores/auth-store";
import { setPendingIntent } from "@/lib/post-login-intent";
import {
  postAnonAsk,
  getAnonTeaser,
  getAskSession,
  setAskSession,
  getPageQuestion,
  setPageQuestion,
  readClaimedAnswerForPage,
  AskError,
  type AskErrorKind,
  type ClaimedAnswer,
} from "@/lib/library/ask";
import {
  TurnstileGate,
  TURNSTILE_PENDING,
  resolveTurnstileToken,
  type TurnstileState,
} from "./TurnstileGate";
import type { AskRayhanWidgetProps } from "@/types/library";

const MIN_LEN = 3;
const MAX_LEN = 500;

// Decorative skeleton bar widths (matches GateBanner's ragged-text look). The
// hidden answer bytes never reach the client — these are purely cosmetic.
const BAR_WIDTHS = ["100%", "92%", "84%", "96%", "78%"] as const;

interface QuestionRef {
  questionId: string;
  sessionKey: string;
}

/**
 * The floating «اسأل ريحان» conversion widget — the real Phase 4 popup (upgrades
 * the earlier stub). A fixed bottom-LEFT pill (RTL: the physical-left corner)
 * opens an RTL panel (desktop card / mobile bottom sheet).
 *
 *   Anon:  ask one question grounded in THIS page → teaser prefix + decorative
 *          bars + «سجّل مجاناً لعرض الإجابة كاملة» (stashes a claim intent → login).
 *          503 (kill switch) shows the login stub; 429 (session cap) shows a CTA.
 *          A refresh re-shows the teaser from localStorage without spending a
 *          new question.
 *   Authed: «افتح محادثة مع ريحان» → /chat. After a post-signup claim, the full
 *          answer is auto-revealed here once (the continuity moment).
 */
export function AskRayhanWidget({
  pageType,
  pageId,
  pageTitle,
}: AskRayhanWidgetProps) {
  const router = useRouter();
  const pathname = usePathname();
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated);

  const [open, setOpen] = useState(false);
  const [question, setQuestion] = useState("");
  const [submitting, setSubmitting] = useState(false);

  const [teaserPrefix, setTeaserPrefix] = useState<string | null>(null);
  const [questionRef, setQuestionRef] = useState<QuestionRef | null>(null);
  const [claimed, setClaimed] = useState<ClaimedAnswer | null>(null);
  const [alreadyClaimed, setAlreadyClaimed] = useState(false);
  const [errorKind, setErrorKind] = useState<AskErrorKind | null>(null);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  // Turnstile lives in a ref on purpose — nothing renders from it and the send
  // button is never gated on it, so a blocked/unconfigured challenge degrades to
  // a null token instead of a dead form. `turnstileKey` remounts the gate for a
  // fresh token after a failed attempt (tokens are single-use).
  const turnstileState = useRef<TurnstileState>(TURNSTILE_PENDING);
  const [turnstileKey, setTurnstileKey] = useState(0);
  const handleTurnstileState = useCallback((state: TurnstileState) => {
    turnstileState.current = state;
  }, []);

  // On mount: reveal a just-claimed answer for this page (auto-open once), else
  // re-hydrate a prior teaser from localStorage (refresh continuity).
  useEffect(() => {
    const claimedAnswer = readClaimedAnswerForPage(pageType, pageId);
    if (claimedAnswer) {
      setClaimed(claimedAnswer);
      setOpen(true);
      return;
    }
    const stored = getPageQuestion(pageType, pageId);
    if (!stored) return;
    let active = true;
    void (async () => {
      const teaser = await getAnonTeaser(stored.question_id, stored.session_key);
      if (!active || !teaser) return;
      setQuestionRef({
        questionId: stored.question_id,
        sessionKey: stored.session_key,
      });
      if (teaser.claimed) {
        setAlreadyClaimed(true);
      } else {
        setTeaserPrefix(teaser.visible_prefix);
      }
    })();
    return () => {
      active = false;
    };
  }, [pageType, pageId]);

  const trimmed = question.trim();
  const canSubmit =
    !submitting && trimmed.length >= MIN_LEN && trimmed.length <= MAX_LEN;

  async function handleSubmit(): Promise<void> {
    if (!canSubmit) return;
    setSubmitting(true);
    setErrorKind(null);
    setErrorMsg(null);
    try {
      const turnstileToken = await resolveTurnstileToken(
        () => turnstileState.current,
      );
      const result = await postAnonAsk({
        question: trimmed,
        pageType,
        pageId,
        sessionKey: getAskSession(),
        turnstileToken,
      });
      setAskSession(result.session_key);
      setPageQuestion(pageType, pageId, result.question_id, result.session_key);
      setQuestionRef({
        questionId: result.question_id,
        sessionKey: result.session_key,
      });
      setTeaserPrefix(result.visible_prefix);
    } catch (err) {
      // Whatever token we just sent is spent — hand any retry a fresh one.
      turnstileState.current = TURNSTILE_PENDING;
      setTurnstileKey((n) => n + 1);
      if (err instanceof AskError) {
        setErrorKind(err.kind);
        setErrorMsg(err.message);
      } else {
        setErrorKind("error");
        setErrorMsg("تعذّر معالجة سؤالك، حاول مجدداً");
      }
    } finally {
      setSubmitting(false);
    }
  }

  function goClaim(): void {
    if (!questionRef) return;
    setPendingIntent({
      type: "claim_anon_answer",
      question_id: questionRef.questionId,
      session_key: questionRef.sessionKey,
      return_to: pathname ?? "/",
    });
    router.push("/login");
  }

  const loginHref = useMemo(() => {
    const params = new URLSearchParams({
      intent: "ask_rayhan",
      page_type: pageType,
      page_id: pageId,
      page_title: pageTitle,
    });
    return `/login?${params.toString()}`;
  }, [pageType, pageId, pageTitle]);

  if (!open) {
    return (
      <button
        type="button"
        onClick={() => setOpen(true)}
        aria-label={`اسأل ريحان عن: ${pageTitle}`}
        // The offset is measured from the SAFE bottom edge: `viewportFit:"cover"`
        // (app/layout.tsx) puts the viewport under the iPhone home indicator, so
        // a bare `bottom-5` parks the pill on top of it.
        className="animate-fab-in fixed bottom-[calc(1.25rem+env(safe-area-inset-bottom))] left-5 z-40 inline-flex items-center gap-2 rounded-full bg-primary px-4 py-3 text-sm font-semibold text-primary-foreground shadow-lg ring-1 ring-primary/20 transition-all duration-200 hover:-translate-y-0.5 hover:scale-105 hover:bg-primary-hover hover:shadow-xl focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 sm:bottom-[calc(1.5rem+env(safe-area-inset-bottom))] sm:left-6"
      >
        <Sparkles aria-hidden="true" className="h-4 w-4" />
        اسأل ريحان
      </button>
    );
  }

  return (
    <>
      {/* Mobile backdrop — tap to dismiss. */}
      <div
        aria-hidden="true"
        onClick={() => setOpen(false)}
        className="fixed inset-0 z-40 bg-black/40 sm:hidden"
      />

      <div
        dir="rtl"
        role="dialog"
        aria-label="اسأل ريحان"
        // `pb-[env(safe-area-inset-bottom)]` only on the phone sheet (it is
        // flush with the screen edge); the ≥sm floating card already sits well
        // above the home indicator, so it resets the padding.
        className="fixed inset-x-0 bottom-0 z-50 flex max-h-[82vh] flex-col rounded-t-2xl border border-border bg-card pb-[env(safe-area-inset-bottom)] shadow-2xl sm:inset-x-auto sm:bottom-24 sm:left-6 sm:w-[380px] sm:rounded-2xl sm:pb-0"
      >
        {/* Header */}
        <div className="flex items-center justify-between gap-2 border-b border-border px-4 py-3">
          <span className="flex items-center gap-2 text-sm font-bold text-foreground">
            <Sparkles aria-hidden="true" className="h-4 w-4 text-primary" />
            اسأل ريحان
          </span>
          <button
            type="button"
            onClick={() => setOpen(false)}
            aria-label="إغلاق"
            className="rounded-lg p-1 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
          >
            <X aria-hidden="true" className="h-4 w-4" />
          </button>
        </div>

        {/* Body */}
        <div className="flex-1 overflow-y-auto px-4 py-4">
          {claimed ? (
            <FullAnswer question={claimed.question} answerMd={claimed.answer_md} />
          ) : isAuthenticated || alreadyClaimed ? (
            <AuthedCta />
          ) : errorKind === "disabled" ? (
            <DisabledStub loginHref={loginHref} />
          ) : errorKind === "rate_limited" ? (
            <RateLimited onSignup={() => router.push("/login")} />
          ) : teaserPrefix !== null ? (
            <Teaser prefix={teaserPrefix} onClaim={goClaim} />
          ) : (
            <AskForm
              pageTitle={pageTitle}
              question={question}
              setQuestion={setQuestion}
              submitting={submitting}
              canSubmit={canSubmit}
              errorMsg={errorMsg}
              onSubmit={handleSubmit}
              turnstileKey={turnstileKey}
              onTurnstileState={handleTurnstileState}
            />
          )}
        </div>
      </div>
    </>
  );
}

// ------------------------------------------------------------------
// Panel states
// ------------------------------------------------------------------

function AskForm({
  pageTitle,
  question,
  setQuestion,
  submitting,
  canSubmit,
  errorMsg,
  onSubmit,
  turnstileKey,
  onTurnstileState,
}: {
  pageTitle: string;
  question: string;
  setQuestion: (v: string) => void;
  submitting: boolean;
  canSubmit: boolean;
  errorMsg: string | null;
  onSubmit: () => void;
  turnstileKey: number;
  onTurnstileState: (state: TurnstileState) => void;
}) {
  return (
    <div className="space-y-3">
      <p className="text-xs leading-relaxed text-muted-foreground">
        اطرح سؤالاً عن «{pageTitle}» واحصل على إجابة موثّقة من ريحان.
      </p>
      <textarea
        dir="rtl"
        value={question}
        onChange={(e) => setQuestion(e.target.value.slice(0, MAX_LEN))}
        onKeyDown={(e) => {
          if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
            e.preventDefault();
            onSubmit();
          }
        }}
        rows={4}
        maxLength={MAX_LEN}
        placeholder="اكتب سؤالك هنا..."
        className="w-full resize-none rounded-xl border border-border bg-background px-3 py-2.5 text-right text-sm text-foreground outline-none transition-colors placeholder:text-muted-foreground focus:border-primary/50"
      />
      {/* Invisible in managed mode — only paints if Cloudflare asks for an
          interaction, and never blocks إرسال. */}
      <TurnstileGate key={turnstileKey} onStateChange={onTurnstileState} />
      <div className="flex items-center justify-between gap-2">
        <span className="text-xs text-muted-foreground">
          {question.trim().length}/{MAX_LEN}
        </span>
        <button
          type="button"
          onClick={onSubmit}
          disabled={!canSubmit}
          className="inline-flex items-center gap-2 rounded-full bg-primary px-4 py-2 text-sm font-semibold text-primary-foreground transition-colors hover:bg-primary-hover disabled:cursor-not-allowed disabled:opacity-50"
        >
          {submitting ? (
            <Loader2 aria-hidden="true" className="h-4 w-4 animate-spin" />
          ) : (
            <Send aria-hidden="true" className="h-4 w-4" />
          )}
          إرسال
        </button>
      </div>
      {errorMsg && (
        <p role="alert" className="text-xs font-medium text-destructive">
          {errorMsg}
        </p>
      )}
    </div>
  );
}

function Teaser({
  prefix,
  onClaim,
}: {
  prefix: string;
  onClaim: () => void;
}) {
  return (
    <div className="space-y-3">
      <p className="whitespace-pre-line text-sm leading-relaxed text-foreground">
        {prefix}
      </p>

      {/* Decorative bars — no hidden text reaches the DOM. */}
      <div
        aria-hidden="true"
        className="space-y-2.5 [mask-image:linear-gradient(to_bottom,black,transparent)]"
      >
        {BAR_WIDTHS.map((width, index) => (
          <div
            key={index}
            className="h-3 rounded bg-gradient-to-l from-surface-3 via-surface-2 to-surface-3"
            style={{ width }}
          />
        ))}
      </div>

      <div className="rounded-xl border border-border bg-background p-4 text-center">
        <div className="mx-auto mb-2 flex h-10 w-10 items-center justify-center rounded-2xl bg-primary/10 text-primary">
          <Lock aria-hidden="true" className="h-5 w-5" />
        </div>
        <p className="text-sm font-bold text-foreground">
          سجّل مجاناً لعرض الإجابة كاملة
        </p>
        <p className="mx-auto mt-1 max-w-xs text-xs leading-relaxed text-muted-foreground">
          أنشئ حسابك المجاني لقراءة إجابة ريحان كاملة مع إمكانية طرح المزيد.
        </p>
        <button
          type="button"
          onClick={onClaim}
          className={cn(buttonVariants({ size: "default" }), "mt-3 w-full")}
        >
          سجّل مجاناً لعرض الإجابة كاملة
        </button>
      </div>
    </div>
  );
}

function FullAnswer({
  question,
  answerMd,
}: {
  question: string;
  answerMd: string;
}) {
  return (
    <div className="space-y-3">
      <p className="rounded-lg bg-muted/50 px-3 py-2 text-xs font-medium text-foreground">
        {question}
      </p>
      <div className="text-sm leading-relaxed text-foreground">
        <MarkdownRenderer content={answerMd} />
      </div>
      <Link
        href="/chat"
        className={cn(buttonVariants({ size: "sm", variant: "outline" }), "w-full")}
      >
        <MessageCircle aria-hidden="true" className="h-4 w-4" />
        افتح محادثة مع ريحان
      </Link>
    </div>
  );
}

function AuthedCta() {
  return (
    <div className="space-y-3 text-center">
      <div className="mx-auto flex h-11 w-11 items-center justify-center rounded-2xl bg-primary/10 text-primary">
        <Sparkles aria-hidden="true" className="h-5 w-5" />
      </div>
      <p className="text-sm font-bold text-foreground">
        تحدّث مع ريحان عن هذه الصفحة
      </p>
      <p className="mx-auto max-w-xs text-xs leading-relaxed text-muted-foreground">
        افتح محادثة جديدة واحصل على إجابات موثّقة مع المراجع النظامية.
      </p>
      <Link
        href="/chat"
        className={cn(buttonVariants({ size: "default" }), "w-full")}
      >
        <MessageCircle aria-hidden="true" className="h-4 w-4" />
        افتح محادثة مع ريحان
      </Link>
    </div>
  );
}

function DisabledStub({ loginHref }: { loginHref: string }) {
  return (
    <div className="space-y-3 text-center">
      <div className="mx-auto flex h-11 w-11 items-center justify-center rounded-2xl bg-primary/10 text-primary">
        <Sparkles aria-hidden="true" className="h-5 w-5" />
      </div>
      <p className="text-sm font-bold text-foreground">اسأل ريحان</p>
      <p className="mx-auto max-w-xs text-xs leading-relaxed text-muted-foreground">
        سجّل مجاناً وجرّب طرح أسئلتك القانونية على ريحان مع إجابات موثّقة.
      </p>
      <Link
        href={loginHref}
        className={cn(buttonVariants({ size: "default" }), "w-full")}
      >
        سجّل وجرّب اسأل ريحان
      </Link>
    </div>
  );
}

function RateLimited({ onSignup }: { onSignup: () => void }) {
  return (
    <div className="space-y-3 text-center">
      <div className="mx-auto flex h-11 w-11 items-center justify-center rounded-2xl bg-primary/10 text-primary">
        <Lock aria-hidden="true" className="h-5 w-5" />
      </div>
      <p className="text-sm font-bold text-foreground">
        سؤالك المجاني مستخدم
      </p>
      <p className="mx-auto max-w-xs text-xs leading-relaxed text-muted-foreground">
        سجّل مجاناً لطرح المزيد من الأسئلة على ريحان دون حدود.
      </p>
      <button
        type="button"
        onClick={onSignup}
        className={cn(buttonVariants({ size: "default" }), "w-full")}
      >
        سجّل لطرح المزيد
      </button>
    </div>
  );
}
