// Same rules and Arabic messages as the web's login/register zod schemas
// (frontend/components/auth/LoginForm.tsx).

const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

export function validateEmail(email: string): string | null {
  const v = email.trim();
  if (!v) return "البريد الإلكتروني مطلوب";
  if (!EMAIL_RE.test(v)) return "صيغة البريد الإلكتروني غير صحيحة";
  return null;
}

export function validatePassword(password: string): string | null {
  if (!password) return "كلمة المرور مطلوبة";
  if (password.length < 8) return "كلمة المرور يجب أن تحتوي على 8 أحرف على الأقل";
  return null;
}

/** Same message as the web's «تعيين كلمة المرور» dialog. */
export function validatePasswordConfirm(password: string, confirm: string): string | null {
  if (!confirm) return "أعد كتابة كلمة المرور";
  if (confirm !== password) return "كلمتا المرور غير متطابقتين";
  return null;
}

export function validateFullName(name: string): string | null {
  return name.trim() ? null : "الاسم الكامل مطلوب";
}

export const MSG_TERMS_REQUIRED = "يجب الموافقة على الشروط وسياسة الخصوصية";
