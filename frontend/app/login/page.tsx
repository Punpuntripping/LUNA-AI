import { LoginForm } from "@/components/auth/LoginForm";
import { LegalLinksFooter } from "@/components/legal/LegalLinksFooter";
import { ThemeToggle } from "@/components/ui/theme-toggle";
import { SignupStartedTracker } from "@/components/analytics/SignupStartedTracker";
import { AskRayhanLoginIntent } from "@/components/auth/AskRayhanLoginIntent";
import { BrandLockup } from "@/components/brand/BrandLockup";

export default function LoginPage() {
  return (
    <div className="relative flex min-h-screen items-center justify-center bg-background px-4">
      {/* `signup_started` when this page opens on `mode=register` — the step
          that joins a gate CTA click to an account (product_analytics.md §5.4).
          A client LEAF on purpose: this page must stay a server component, or
          reading the query string forces the whole route into client rendering
          (see the useSearchParams note in LoginForm). Renders no DOM. */}
      <SignupStartedTracker />

      {/* «اسأل ريحان» on a public library page sends an anon reader here with
          the page in the querystring. This turns that into the post-login carry
          intent AuthGuard already consumes, so signing in lands them in a chat
          holding the page. Another client LEAF, same reason. Renders no DOM. */}
      <AskRayhanLoginIntent />

      {/* Theme toggle — top-start corner (top-right in RTL) */}
      <div className="absolute top-4 start-4">
        <ThemeToggle />
      </div>

      <div className="w-full max-w-md space-y-8">
        {/* Header */}
        <div className="text-center space-y-2">
          {/* Rayhan logo. Shown large here, which is the size the outline leaf
              was drawn for — it reads as a leaf rather than the faint strokes
              it becomes in the 40px header slot. */}
          <div className="flex justify-center pb-2">
            <BrandLockup className="h-16" priority />
          </div>
          <h1 className="text-3xl font-bold tracking-tight text-foreground">
            مرحباً بك في ريحان
          </h1>
          <p className="text-muted-foreground">
            المساعد القانوني الذكي
          </p>
        </div>

        {/* Login Form */}
        <LoginForm />

        {/* Legal links */}
        <LegalLinksFooter />
      </div>
    </div>
  );
}
