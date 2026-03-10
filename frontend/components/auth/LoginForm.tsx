"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { z } from "zod";
import { Eye, EyeOff, Loader2 } from "lucide-react";
import { useAuthStore } from "@/stores/auth-store";
import { ApiClientError } from "@/lib/api";

// -----------------------------------------------
// Zod schemas with Arabic error messages
// -----------------------------------------------

const loginSchema = z.object({
  email: z
    .string()
    .min(1, "البريد الإلكتروني مطلوب")
    .email("صيغة البريد الإلكتروني غير صحيحة"),
  password: z
    .string()
    .min(1, "كلمة المرور مطلوبة")
    .min(8, "كلمة المرور يجب أن تحتوي على 8 أحرف على الأقل"),
});

const registerSchema = loginSchema.extend({
  full_name_ar: z.string().min(1, "الاسم الكامل مطلوب"),
});

type LoginFormData = z.infer<typeof loginSchema>;
type RegisterFormData = z.infer<typeof registerSchema>;

// -----------------------------------------------
// Component
// -----------------------------------------------

export function LoginForm() {
  const router = useRouter();
  const { login, register } = useAuthStore();

  const [mode, setMode] = useState<"login" | "register">("login");
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [showPassword, setShowPassword] = useState(false);
  const [serverError, setServerError] = useState<string | null>(null);
  const [registrationSuccess, setRegistrationSuccess] = useState(false);

  // Field values
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [fullNameAr, setFullNameAr] = useState("");

  // Field errors
  const [errors, setErrors] = useState<Record<string, string>>({});

  const toggleMode = () => {
    setMode((prev) => (prev === "login" ? "register" : "login"));
    setErrors({});
    setServerError(null);
    setRegistrationSuccess(false);
  };

  const validate = (): boolean => {
    const schema = mode === "login" ? loginSchema : registerSchema;
    const data =
      mode === "login"
        ? { email, password }
        : { email, password, full_name_ar: fullNameAr };

    const result = schema.safeParse(data);

    if (!result.success) {
      const fieldErrors: Record<string, string> = {};
      for (const issue of result.error.issues) {
        const field = issue.path[0] as string;
        if (!fieldErrors[field]) {
          fieldErrors[field] = issue.message;
        }
      }
      setErrors(fieldErrors);
      return false;
    }

    setErrors({});
    return true;
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setServerError(null);

    if (!validate()) return;

    setIsSubmitting(true);

    try {
      if (mode === "login") {
        await login(email, password);
        router.push("/chat");
      } else {
        await register(email, password, fullNameAr);
        setRegistrationSuccess(true);
      }
    } catch (err) {
      if (err instanceof ApiClientError) {
        setServerError(err.message);
      } else {
        setServerError("حدث خطأ غير متوقع. حاول مرة أخرى.");
      }
    } finally {
      setIsSubmitting(false);
    }
  };

  // Registration success message
  if (registrationSuccess) {
    return (
      <div className="rounded-lg border border-border bg-card p-6 text-center space-y-4">
        <div className="mx-auto flex h-12 w-12 items-center justify-center rounded-full bg-green-100">
          <svg
            className="h-6 w-6 text-green-600"
            fill="none"
            viewBox="0 0 24 24"
            stroke="currentColor"
          >
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              strokeWidth={2}
              d="M5 13l4 4L19 7"
            />
          </svg>
        </div>
        <h3 className="text-lg font-semibold text-foreground">
          تم إنشاء الحساب بنجاح
        </h3>
        <p className="text-sm text-muted-foreground">
          تم إرسال رابط التحقق إلى بريدك الإلكتروني. يرجى تأكيد بريدك الإلكتروني ثم تسجيل الدخول.
        </p>
        <button
          type="button"
          onClick={() => {
            setMode("login");
            setRegistrationSuccess(false);
          }}
          className="inline-flex items-center justify-center rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground hover:bg-primary/90 transition-colors"
        >
          تسجيل الدخول
        </button>
      </div>
    );
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-6" noValidate>
      <div className="rounded-lg border border-border bg-card p-6 space-y-4">
        {/* Server error */}
        {serverError && (
          <div className="rounded-md bg-destructive/10 border border-destructive/20 p-3 text-sm text-destructive">
            {serverError}
          </div>
        )}

        {/* Full Name — register only */}
        {mode === "register" && (
          <div className="space-y-2">
            <label
              htmlFor="full_name_ar"
              className="block text-sm font-medium text-foreground"
            >
              الاسم الكامل
            </label>
            <input
              id="full_name_ar"
              type="text"
              value={fullNameAr}
              onChange={(e) => setFullNameAr(e.target.value)}
              placeholder="أدخل اسمك الكامل"
              className={`w-full rounded-md border bg-background px-3 py-2 text-sm text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring focus:border-transparent transition-colors ${
                errors.full_name_ar ? "border-destructive" : "border-input"
              }`}
              dir="rtl"
            />
            {errors.full_name_ar && (
              <p className="text-xs text-destructive">{errors.full_name_ar}</p>
            )}
          </div>
        )}

        {/* Email */}
        <div className="space-y-2">
          <label
            htmlFor="email"
            className="block text-sm font-medium text-foreground"
          >
            البريد الإلكتروني
          </label>
          <input
            id="email"
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            placeholder="example@email.com"
            className={`w-full rounded-md border bg-background px-3 py-2 text-sm text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring focus:border-transparent transition-colors ${
              errors.email ? "border-destructive" : "border-input"
            }`}
            dir="ltr"
            autoComplete="email"
          />
          {errors.email && (
            <p className="text-xs text-destructive">{errors.email}</p>
          )}
        </div>

        {/* Password */}
        <div className="space-y-2">
          <label
            htmlFor="password"
            className="block text-sm font-medium text-foreground"
          >
            كلمة المرور
          </label>
          <div className="relative">
            <input
              id="password"
              type={showPassword ? "text" : "password"}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder="••••••••"
              className={`w-full rounded-md border bg-background px-3 py-2 pe-10 text-sm text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring focus:border-transparent transition-colors ${
                errors.password ? "border-destructive" : "border-input"
              }`}
              dir="ltr"
              autoComplete={mode === "login" ? "current-password" : "new-password"}
            />
            <button
              type="button"
              onClick={() => setShowPassword((prev) => !prev)}
              className="absolute end-2 top-1/2 -translate-y-1/2 p-1 text-muted-foreground hover:text-foreground transition-colors"
              tabIndex={-1}
              aria-label={showPassword ? "إخفاء كلمة المرور" : "إظهار كلمة المرور"}
            >
              {showPassword ? (
                <EyeOff className="h-4 w-4" />
              ) : (
                <Eye className="h-4 w-4" />
              )}
            </button>
          </div>
          {errors.password && (
            <p className="text-xs text-destructive">{errors.password}</p>
          )}
        </div>

        {/* Submit button */}
        <button
          type="submit"
          disabled={isSubmitting}
          className="w-full flex items-center justify-center gap-2 rounded-md bg-primary px-4 py-2.5 text-sm font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
        >
          {isSubmitting && <Loader2 className="h-4 w-4 animate-spin" />}
          {mode === "login" ? "تسجيل الدخول" : "إنشاء حساب"}
        </button>
      </div>

      {/* Toggle mode */}
      <div className="text-center text-sm text-muted-foreground">
        {mode === "login" ? (
          <>
            ليس لديك حساب؟{" "}
            <button
              type="button"
              onClick={toggleMode}
              className="font-medium text-primary hover:text-primary/80 transition-colors"
            >
              إنشاء حساب جديد
            </button>
          </>
        ) : (
          <>
            لديك حساب بالفعل؟{" "}
            <button
              type="button"
              onClick={toggleMode}
              className="font-medium text-primary hover:text-primary/80 transition-colors"
            >
              تسجيل الدخول
            </button>
          </>
        )}
      </div>
    </form>
  );
}
