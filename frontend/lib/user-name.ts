import type { User } from "@/types";

/**
 * What the UI calls the user — one answer, used by every surface that prints
 * a name (sidebar chip, avatar initial, own-message label).
 *
 * `call_name` FIRST: it is the server-resolved answer from shared/identity.py
 * (preferred_name override → first name derived from full_name_ar), the same
 * value the router greets the reader with. Changing «بماذا تحب أن نناديك؟»
 * has to change the name on screen too, or the setting looks broken.
 *
 * Never re-derive a first name here — the server owns that rule.
 */
export function userDisplayName(user: User | null | undefined): string | null {
  return (
    user?.call_name?.trim() ||
    user?.full_name_ar?.trim() ||
    user?.email?.trim() ||
    null
  );
}

/** First character of the display name, for the avatar circle. */
export function userInitial(user: User | null | undefined): string {
  const source = userDisplayName(user);
  if (!source) return "ر";
  return Array.from(source)[0].toUpperCase();
}
