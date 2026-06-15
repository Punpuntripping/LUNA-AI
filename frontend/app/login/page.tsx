import { LoginForm } from "@/components/auth/LoginForm";
import { ThemeToggle } from "@/components/ui/theme-toggle";

export default function LoginPage() {
  return (
    <div className="relative flex min-h-screen items-center justify-center bg-background px-4">
      {/* Theme toggle — top-start corner (top-right in RTL) */}
      <div className="absolute top-4 start-4">
        <ThemeToggle />
      </div>

      <div className="w-full max-w-md space-y-8">
        {/* Header */}
        <div className="text-center space-y-2">
          {/* Rayhan Logo */}
          <div className="mx-auto flex h-16 w-16 items-center justify-center rounded-2xl bg-primary text-primary-foreground text-2xl font-bold">
            ريحان
          </div>
          <h1 className="text-3xl font-bold tracking-tight text-foreground">
            مرحباً بك في ريحان
          </h1>
          <p className="text-muted-foreground">
            المساعد القانوني الذكي
          </p>
          <div className="flex justify-center pt-1">
            <span className="inline-flex items-center gap-1.5 rounded-full bg-primary px-3.5 py-1 text-xs font-semibold text-primary-foreground shadow-sm">
              <span className="h-1.5 w-1.5 rounded-full bg-primary-foreground/80" />
              إطلاق تجريبي
            </span>
          </div>
        </div>

        {/* Login Form */}
        <LoginForm />
      </div>
    </div>
  );
}
