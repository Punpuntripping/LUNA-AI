import { LoginForm } from "@/components/auth/LoginForm";

export default function LoginPage() {
  return (
    <div className="flex min-h-screen items-center justify-center bg-background px-4">
      <div className="w-full max-w-md space-y-8">
        {/* Header */}
        <div className="text-center space-y-2">
          {/* Luna Logo */}
          <div className="mx-auto flex h-16 w-16 items-center justify-center rounded-2xl bg-primary text-primary-foreground text-2xl font-bold">
            لونا
          </div>
          <h1 className="text-3xl font-bold tracking-tight text-foreground">
            مرحباً بك في لونا
          </h1>
          <p className="text-muted-foreground">
            المساعد القانوني الذكي
          </p>
        </div>

        {/* Login Form */}
        <LoginForm />
      </div>
    </div>
  );
}
