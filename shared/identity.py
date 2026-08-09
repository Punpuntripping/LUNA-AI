"""How to address the user — the single source of truth for «call name».

Two consumers need the same answer to "what do we call this user?":

* ``backend/app/api/auth.py`` — ``GET /auth/me`` ships it to the frontend so
  إعدادات الحساب can prefill the «بماذا تحب أن نناديك؟» field.
* ``agents/router/context.py`` — the router injects it so its replies can
  address the user by name.

Resolution order (first non-empty wins):

1. ``users.preferred_name`` — what the user explicitly typed in settings.
2. The first name derived from ``users.full_name_ar`` (the registration /
   Google name).
3. Nothing — the caller renders no name at all rather than guessing.

The derived default is a FIRST name, not the full one: «مرحبًا محمد» reads
like a person talking, «مرحبًا محمد عبدالله الفلاح» reads like a form letter.

Note on step 2's ``"@" in name`` guard: before migration 122 the signup
trigger fell back to ``NEW.email`` when the metadata carried no
``full_name_ar`` — which is exactly what happened to every Google sign-in,
since Google sets ``name`` / ``full_name``, not ``full_name_ar``. Migration 122
fixes the trigger and backfills those rows, but the guard stays: an email
address is never a name, whatever put it there.
"""
from __future__ import annotations

import re

# Long enough for «عبدالرحمن» or a nickname, short enough that the value stays
# a name. Mirrors the VARCHAR(60) on users.preferred_name and the maxLength on
# the settings input — enforced here too because this text is rendered into the
# router's instructions.
MAX_CALL_NAME_LEN = 60

# Control characters and line breaks are stripped, not merely trimmed: this
# string lands inside the router's system instructions, where a newline is the
# cheapest way to fake a new instruction block.
_WHITESPACE_RUN = re.compile(r"\s+")
_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")

# Arabic first names that are two tokens by construction. Taking token[0] alone
# would address «عبد الله» as «عبد» — not a name, and a jarring thing to be
# called. When the name is written solid («عبدالله») there is nothing to join
# and the plain first-token path already gets it right.
_COMPOUND_FIRST_TOKENS = {"عبد", "أبو", "ابو", "أبا", "ابا", "أم", "ام"}


def clean_name(value: str | None) -> str | None:
    """Normalise a user-supplied name: strip control chars, collapse runs, cap.

    Returns None for anything that is empty once cleaned.
    """
    if not value:
        return None
    text = _CONTROL_CHARS.sub(" ", str(value))
    text = _WHITESPACE_RUN.sub(" ", text).strip()
    if not text:
        return None
    return text[:MAX_CALL_NAME_LEN].strip() or None


def derive_call_name(full_name_ar: str | None) -> str | None:
    """First name derived from the registration / Google full name.

    Returns None when there is no usable name (empty, or an email address
    standing in for one — see the module docstring).
    """
    name = clean_name(full_name_ar)
    if not name or "@" in name:
        return None
    tokens = name.split(" ")
    if tokens[0] in _COMPOUND_FIRST_TOKENS and len(tokens) > 1:
        return f"{tokens[0]} {tokens[1]}"
    return tokens[0]


def resolve_call_name(
    preferred_name: str | None, full_name_ar: str | None
) -> str | None:
    """What to call this user, or None if we have nothing to call them.

    ``preferred_name`` (users.preferred_name) always wins when set — it is the
    user's own answer to «بماذا تحب أن نناديك؟».
    """
    return clean_name(preferred_name) or derive_call_name(full_name_ar)


__all__ = [
    "MAX_CALL_NAME_LEN",
    "clean_name",
    "derive_call_name",
    "resolve_call_name",
]
