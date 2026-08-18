"""Erase ONE account and every trace of it, so the signup flow can be re-run.

WHY THIS EXISTS
---------------
Validating the signup experience needs the *same* email to be brand new again:
no ``public.users`` row, no GoTrue user, no Google identity, no free-window
usage stamp, no onboarding flags, no analytics attribution. The product's own
delete path cannot do that — it is a 30-day grace period followed by a nightly
sweep (``backend/app/services/account_purge_service.py``), which is correct for
customers and useless as a dev loop.

This script is that sweep's ``_purge_one`` with the grace period removed and the
deliberately-retained tables made optional. It reuses the real purge code rather
than re-implementing it, so the erasure order — storage → provider card tokens →
child rows → GoTrue user — stays in one place. What it adds on top:

  * the tables the production purge INTENTIONALLY keeps (``llm_calls``,
    ``audit_logs``, ``analytics_events``, ``payment_transactions``), each behind
    its own flag, because "keep the cost ledger honest" and "leave my test
    signup out of the funnel" are opposite goals;
  * orphaned GoTrue users — an ``auth.users`` row with no ``public.users`` row.
    A half-failed signup leaves exactly that, and it is the state that makes the
    *next* signup with the same email fail with "user already registered". This
    is the single most common reason a re-run does not look fresh;
  * Redis session + quota keys, so a stale cached session cannot answer for a
    user who no longer exists;
  * the browser-side snippet — see BROWSER STATE below.

⚠ THIS IS A HARD DELETE AGAINST PRODUCTION. There is one Supabase project; the
repo ``.env`` points at it whether you are running locally or not. Nothing here
is recoverable and nothing is soft-deleted.

⚠ THE ALLOWLIST IS THE SAFETY. ``ALLOWED_LOCAL_PARTS`` below is the only thing
standing between a typo and a deleted customer. An email outside it exits 2
without touching anything. Gmail ``+tag`` aliases of an allowlisted address are
accepted (``you+test7@gmail.com``), which is the intended way to burn through
many signups without editing this file.

BROWSER STATE
-------------
Half the signup experience lives in the browser, not the database: the anon-ask
continuity keys, the edu-popup impression counters, the gate-attribution note
that joins a CTA click to a signup, and the Supabase auth token itself. The DB
purge cannot reach any of it. The script therefore prints a paste-able snippet
(``--snippet`` prints only that) enumerating every key this repo writes. An
incognito window achieves the same thing for the sessionStorage half only —
``rayhan.edu.impressions``, ``rayhan_ask_session``, ``rayhan_ask_q*``,
``luna.workspace.splitRatio`` and the ``sb-*-auth-token`` are localStorage and
survive a new tab.

USAGE
-----
    python scripts/nuke_account.py                      # census only, no writes
    python scripts/nuke_account.py --yes                # erase the default account
    python scripts/nuke_account.py you+t3@gmail.com --yes
    python scripts/nuke_account.py --yes --analytics    # also drop analytics_events
    python scripts/nuke_account.py --snippet            # just the browser one-liner

Exit codes: 0 ok · 1 purge failed · 2 refused (allowlist / no such account).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shared.config import get_settings              # noqa: E402
from shared.db.client import get_supabase_client    # noqa: E402


# ---------------------------------------------------------------------------
# Allowlist
# ---------------------------------------------------------------------------
# Local-parts (the bit before "@") this script will ever delete, matched after
# stripping a "+tag" suffix and lowercasing. Domain is compared whole.
#
# Add a new entry ONLY for an address you own and use as a throwaway. Every
# address in here is one typo away from being erased with no undo.
ALLOWED_LOCAL_PARTS: set[str] = {
    "mhfallath99@gmail.com",
}

DEFAULT_EMAIL = "mhfallath99@gmail.com"


def normalize_email(email: str) -> str:
    """Lowercase, and drop a Gmail ``+tag`` so aliases match the allowlist.

    ``You+Test4@Gmail.com`` -> ``you@gmail.com``. Only the +tag is stripped;
    dots are left alone, because Gmail's dot-insensitivity is a *delivery*
    property and Supabase stores (and matches) the address as typed — treating
    ``a.b@`` and ``ab@`` as one account here would let an unlisted address pass
    the allowlist.
    """
    email = email.strip().lower()
    if "@" not in email:
        return email
    local, _, domain = email.partition("@")
    local = local.partition("+")[0]
    return f"{local}@{domain}"


def is_allowed(email: str) -> bool:
    return normalize_email(email) in {normalize_email(e) for e in ALLOWED_LOCAL_PARTS}


# ---------------------------------------------------------------------------
# Table map
# ---------------------------------------------------------------------------
# (table, user column) for everything that carries this user's identity.
#
# CASCADE-from-users tables are listed even though deleting the GoTrue user
# would take them anyway. Two reasons: the census has to be able to SHOW them
# before you commit, and an explicit delete gives a real row count instead of a
# silent cascade you have to trust. Children-of-children (messages,
# message_attachments, case_documents, case_memories,
# workspace_item_references) are NOT listed — they cascade from the rows below
# and have no user column to filter on.
CASCADING_TABLES: list[tuple[str, str]] = [
    ("retrieval_artifacts", "user_id"),        # must precede workspace_items (NO ACTION FK)
    ("workspace_items", "user_id"),
    ("conversations", "user_id"),
    ("lawyer_cases", "lawyer_user_id"),
    ("message_feedback", "user_id"),
    ("pii_mappings", "user_id"),
    ("user_templates", "user_id"),
    ("user_preferences", "user_id"),
    ("blog_posts", "owner_user_id"),
    ("paused_runs", "user_id"),
    ("task_state", "user_id"),
    ("library_items", "user_id"),
    ("library_unlocks", "user_id"),
    ("search_index", "owner_user_id"),
    ("unsent_messages", "user_id"),
    ("subscription_cancellations", "user_id"),
    ("payment_methods", "user_id"),
    ("user_subscriptions", "user_id"),
    ("anon_questions", "claimed_by_user_id"),  # no FK at all — would dangle
]

# Tables the PRODUCTION purge deliberately keeps. Each is opt-in/opt-out here
# because the right answer differs per run — see the module docstring.
#   llm_calls          no FK      cost ledger SSoT; orphan rows are pseudonymous
#   audit_logs         SET NULL   PDPL-safe to retain, noise on a test account
#   analytics_events   SET NULL   keeping them = the funnel remembers your tests
#   payment_transactions SET NULL real Moyasar money records (migration 117)
OPTIONAL_TABLES: dict[str, tuple[str, str]] = {
    "llm_calls": ("llm_calls", "user_id"),
    "audit_logs": ("audit_logs", "user_id"),
    "analytics_events": ("analytics_events", "user_id"),
    "payment_transactions": ("payment_transactions", "user_id"),
}


def _is_missing_table(exc: Exception) -> bool:
    """PostgREST reports an unknown relation as PGRST205 / 'does not exist'."""
    text = str(exc)
    return "PGRST205" in text or "PGRST106" in text or "does not exist" in text


def count_rows(supabase: Any, table: str, column: str, user_id: str) -> Optional[int]:
    """Row count for one user in one table. ``None`` = table not in this DB."""
    try:
        result = (
            supabase.table(table)
            .select(column, count="exact")
            .eq(column, user_id)
            .limit(1)
            .execute()
        )
        return result.count or 0
    except Exception as exc:  # noqa: BLE001
        if _is_missing_table(exc):
            return None
        raise


def delete_rows(supabase: Any, table: str, column: str, user_id: str) -> Optional[int]:
    """Delete one user's rows. Returns the count deleted, ``None`` if absent."""
    try:
        result = supabase.table(table).delete().eq(column, user_id).execute()
        return len(result.data or [])
    except Exception as exc:  # noqa: BLE001
        if _is_missing_table(exc):
            return None
        raise


# ---------------------------------------------------------------------------
# Account resolution
# ---------------------------------------------------------------------------
def find_auth_user(supabase: Any, email: str) -> Optional[dict]:
    """Locate the GoTrue user by email.

    ``auth.users`` is not exposed through PostgREST, so this pages the admin
    API and filters client-side. Deliberately matches the address EXACTLY as
    given (after lowercasing) — the +tag stripping in ``normalize_email`` is an
    allowlist concern only; ``you+t1@`` and ``you+t2@`` are two real accounts
    and must never be confused for one another here.
    """
    target = email.strip().lower()
    page = 1
    while page <= 40:  # 40 * 200 = 8000 users; far past this project's size
        users = supabase.auth.admin.list_users(page=page, per_page=200)
        if not users:
            return None
        for user in users:
            if (getattr(user, "email", "") or "").lower() == target:
                return {
                    "id": str(user.id),
                    "email": user.email,
                    "created_at": str(getattr(user, "created_at", "")),
                    # list_users does NOT populate `identities` (it comes back
                    # None); app_metadata.providers is the only place the linked
                    # providers show up on this endpoint. Whether a `google`
                    # identity is attached matters: deleting the GoTrue user is
                    # what makes the one-tap signup path testable again, and a
                    # leftover link stays silent until you walk into it.
                    "providers": ",".join(
                        (getattr(user, "app_metadata", None) or {}).get("providers")
                        or []
                    ) or "no providers",
                }
        page += 1
    return None


def find_db_user(supabase: Any, email: str) -> Optional[dict]:
    result = (
        supabase.table("users")
        .select("user_id, auth_id, email, created_at, deletion_requested_at")
        .eq("email", email.strip().lower())
        .execute()
    )
    rows = result.data or []
    return rows[0] if rows else None


# ---------------------------------------------------------------------------
# Redis
# ---------------------------------------------------------------------------
def clear_redis(user_id: str, auth_id: str, url: Optional[str]) -> list[str]:
    """Drop the user's session + quota keys. Best-effort; never raises.

    ⚠ The repo ``.env`` sets ``REDIS_URL=redis://localhost:6379`` — your LOCAL
    docker Redis, not Railway's. Clearing the local one does nothing for
    rayhanai.com. Pass ``--redis-url`` with the public Railway URL to reach
    prod, or accept that the prod session key simply expires on its own (the
    GoTrue user is gone, so any token minted for it is already unusable).

    Rate-limit keys are NOT touched. Auth routes key their bucket by IP, never
    by user (``middleware/rate_limit.py:455``), so there is no per-user key to
    clear — and wiping ``ratelimit:*`` would loosen the limiter for every other
    caller sharing that bucket. If you trip the 10/min auth window mid-loop,
    wait a minute.
    """
    notes: list[str] = []
    url = url or get_settings().REDIS_URL
    try:
        import redis as redis_lib

        client = redis_lib.from_url(url, socket_connect_timeout=5)
        deleted = client.delete(f"session:{auth_id}")
        quota_keys = list(client.scan_iter(match=f"quota:{user_id}:*", count=500))
        if quota_keys:
            client.delete(*quota_keys)
        notes.append(
            f"redis {url.split('@')[-1]}: session={deleted} quota_keys={len(quota_keys)}"
        )
    except Exception as exc:  # noqa: BLE001
        notes.append(f"redis {url.split('@')[-1]}: SKIPPED ({type(exc).__name__}: {exc})")
    return notes


# ---------------------------------------------------------------------------
# Browser snippet
# ---------------------------------------------------------------------------
def browser_snippet() -> str:
    """Every browser key this repo writes, as one paste-able console line.

    Sources, so this list can be re-derived when a key is added:
      localStorage   rayhan.edu.impressions        stores/edu-store.ts
                     rayhan_ask_session            lib/library/ask.ts
                     rayhan_ask_q*                 lib/library/ask.ts (prefix)
                     luna.workspace.splitRatio     stores/chat-store.ts
                     sb-<ref>-auth-token           @supabase/ssr
      sessionStorage rayhan_analytics_v1           lib/analytics/session.ts
                     rayhan_analytics_gate_v1      components/analytics/signup-attribution.ts
                     rayhan_analytics_signup_done_v1  components/analytics/SignupCompletedTracker.tsx
                     rayhan_claimed_answer         lib/library/ask.ts
                     luna_anon_cta_v1              lib/anon-cta/session.ts
                     luna_pending_intent           (post-login intent replay)
    """
    ref = get_settings().SUPABASE_URL.split("//")[-1].split(".")[0]
    return (
        "(()=>{const L=['rayhan.edu.impressions','rayhan_ask_session',"
        "'luna.workspace.splitRatio','sb-" + ref + "-auth-token'];"
        "const S=['rayhan_analytics_v1','rayhan_analytics_gate_v1',"
        "'rayhan_analytics_signup_done_v1','rayhan_claimed_answer',"
        "'luna_anon_cta_v1','luna_pending_intent'];"
        "L.forEach(k=>localStorage.removeItem(k));"
        "S.forEach(k=>sessionStorage.removeItem(k));"
        "Object.keys(localStorage).filter(k=>k.startsWith('rayhan_ask_q')"
        "||k.startsWith('sb-')).forEach(k=>localStorage.removeItem(k));"
        "console.log('rayhan: browser state cleared');location.reload();})()"
    )


# ---------------------------------------------------------------------------
# Census
# ---------------------------------------------------------------------------
def census(supabase: Any, user_id: str, optional_on: set[str]) -> list[tuple[str, Any]]:
    rows: list[tuple[str, Any]] = []
    for table, column in CASCADING_TABLES:
        rows.append((table, count_rows(supabase, table, column, user_id)))
    for key, (table, column) in OPTIONAL_TABLES.items():
        n = count_rows(supabase, table, column, user_id)
        mark = "" if key in optional_on else "  (KEPT)"
        rows.append((f"{table}{mark}", n))
    try:
        result = (
            supabase.table("plan_codes")
            .select("code", count="exact")
            .or_(f"redeemed_by.eq.{user_id},redeemed_by_users.cs.{{{user_id}}}")
            .limit(1)
            .execute()
        )
        rows.append(("plan_codes (redeemed)", result.count or 0))
    except Exception:  # noqa: BLE001
        rows.append(("plan_codes (redeemed)", "?"))
    return rows


# ---------------------------------------------------------------------------
# The purge
# ---------------------------------------------------------------------------
def purge(
    supabase: Any,
    user_id: str,
    auth_id: str,
    optional_on: set[str],
    release_codes: bool,
) -> list[str]:
    """Erase everything. Raises on any failure before the GoTrue delete.

    ORDER IS LOAD-BEARING and mirrors ``account_purge_service._purge_one``:

      (a) read case_ids  — the storage prefixes are unreachable once
          ``lawyer_cases`` is gone, so they must be captured FIRST;
      (b) storage        — raises, so a storage failure aborts before any row
          dies; never orphan files whose owning ids you just deleted;
      (c) card tokens    — revoked AT THE PROVIDER while the row still exists;
          deleting the row first would destroy the ability to ever revoke;
      (d) SET NULL / no-FK tables — these must go BEFORE the GoTrue delete.
          ``audit_logs``, ``analytics_events`` and ``payment_transactions`` are
          ON DELETE SET NULL: once the users row cascades away their user_id is
          NULL and they can no longer be found, only counted;
      (e) child rows     — the transactional RPC, then the rest explicitly;
      (f) GoTrue user    — TERMINAL. Cascades auth.users → public.users →
          everything still pointing at it, and takes the Google identity with
          it. The users row surviving until this step is what makes a partial
          failure re-runnable.
    """
    from shared.storage.client import delete_folder_recursive
    from backend.app.services.payment_method_service import revoke_all_for_user_sync

    log: list[str] = []
    bucket = get_settings().STORAGE_BUCKET_DOCUMENTS

    # (a)
    result = (
        supabase.table("lawyer_cases")
        .select("case_id")
        .eq("lawyer_user_id", user_id)
        .execute()
    )
    case_ids = [row["case_id"] for row in (result.data or [])]

    # (b) No deleted_at filter: soft-deleted cases still own files.
    files = delete_folder_recursive(bucket, f"general/{user_id}", supabase=supabase)
    for case_id in case_ids:
        files += delete_folder_recursive(bucket, f"cases/{case_id}", supabase=supabase)
    log.append(f"storage: {files} objects across {len(case_ids)} case prefixes")

    # (c) Never raises — a purge that failed over a card token is the worse
    #     failure. An unconfirmed token is logged at ERROR for manual revocation.
    #     ⚠ MOYASAR_SECRET_KEY in the repo .env is a TEST key: it cannot revoke a
    #     token minted under sk_live. Zero stored cards makes this a no-op.
    revoked = revoke_all_for_user_sync(supabase, user_id, reason="dev_account_reset")
    log.append(f"card tokens revoked at provider: {revoked}")

    # (d)
    for key, (table, column) in OPTIONAL_TABLES.items():
        if key not in optional_on:
            continue
        n = delete_rows(supabase, table, column, user_id)
        log.append(f"{table}: {'absent' if n is None else n} deleted")

    # (e) The RPC is one transaction and covers the heaviest children; the
    #     explicit pass below catches the tables it predates (library_*,
    #     unsent_messages, search_index, subscription_cancellations,
    #     payment_methods, user_subscriptions) and anon_questions, which has no
    #     FK and would otherwise dangle.
    supabase.rpc("purge_user_data", {"p_user_id": user_id}).execute()
    log.append("purge_user_data(): ok")

    for table, column in CASCADING_TABLES:
        n = delete_rows(supabase, table, column, user_id)
        if n:
            log.append(f"{table}: {n} deleted")

    if release_codes:
        # uses_count is NOT decremented by the production purge — a consumed
        # seat stays consumed. For a dev loop that burns a code per run, this
        # hands the capacity back.
        codes = (
            supabase.table("plan_codes")
            .select("code, uses_count")
            .eq("redeemed_by", user_id)
            .execute()
        )
        for row in codes.data or []:
            supabase.table("plan_codes").update(
                {
                    "redeemed_by": None,
                    "redeemed_at": None,
                    "uses_count": max(0, (row.get("uses_count") or 1) - 1),
                }
            ).eq("code", row["code"]).execute()
        if codes.data:
            log.append(f"plan_codes released: {len(codes.data)}")

    # (f)
    supabase.auth.admin.delete_user(auth_id)
    log.append(f"auth.users {auth_id}: DELETED (identities + public.users cascade)")

    return log


def purge_orphan_auth_user(supabase: Any, auth_id: str) -> list[str]:
    """Delete a GoTrue user that has no ``public.users`` row.

    This is the state a half-failed signup leaves behind, and it is invisible
    from the app: nothing renders, no data exists, and yet the next signup with
    that email is rejected as already registered. There is nothing to purge but
    the auth row itself.
    """
    supabase.auth.admin.delete_user(auth_id)
    return [f"auth.users {auth_id}: DELETED (orphan - no public.users row)"]


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main() -> int:
    parser = argparse.ArgumentParser(
        description="Hard-delete one account and every trace of it.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("email", nargs="?", default=DEFAULT_EMAIL,
                        help=f"account to erase (default: {DEFAULT_EMAIL})")
    parser.add_argument("--yes", action="store_true",
                        help="actually delete; without it this is a census only")
    parser.add_argument("--analytics", action="store_true",
                        help="also delete analytics_events (default: kept, user_id -> NULL)")
    parser.add_argument("--payments", action="store_true",
                        help="also delete payment_transactions (default: kept — real money records)")
    parser.add_argument("--keep-llm-calls", action="store_true",
                        help="keep llm_calls rows (default: deleted)")
    parser.add_argument("--keep-audit", action="store_true",
                        help="keep audit_logs rows (default: deleted)")
    parser.add_argument("--release-plan-codes", action="store_true",
                        help="hand back any activation-code capacity this account consumed")
    parser.add_argument("--redis-url", default=None,
                        help="override REDIS_URL (repo .env points at LOCAL redis)")
    parser.add_argument("--no-redis", action="store_true", help="skip Redis entirely")
    parser.add_argument("--snippet", action="store_true",
                        help="print the browser-storage clearing snippet and exit")
    args = parser.parse_args()

    if args.snippet:
        print(browser_snippet())
        return 0

    email = args.email.strip().lower()
    if not is_allowed(email):
        print(f"REFUSED: {email} is not in ALLOWED_LOCAL_PARTS.", file=sys.stderr)
        print("Add it to scripts/nuke_account.py only if you own it.", file=sys.stderr)
        return 2

    optional_on = {"llm_calls", "audit_logs"}
    if args.keep_llm_calls:
        optional_on.discard("llm_calls")
    if args.keep_audit:
        optional_on.discard("audit_logs")
    if args.analytics:
        optional_on.add("analytics_events")
    if args.payments:
        optional_on.add("payment_transactions")

    supabase = get_supabase_client()
    settings = get_settings()

    db_user = find_db_user(supabase, email)
    auth_user = find_auth_user(supabase, email)

    print(f"\ntarget   {email}")
    print(f"supabase {settings.SUPABASE_URL}")

    if not db_user and not auth_user:
        print("\nNothing to delete - no public.users row and no GoTrue user.")
        print("This email is already fresh for signup.\n")
        return 0

    if not db_user and auth_user:
        print(f"\nORPHAN GoTrue user: {auth_user['id']}  ({auth_user['providers']})")
        print("No public.users row. This is what blocks re-signup with 'already registered'.")
        if not args.yes:
            print("\nDry run. Re-run with --yes to delete it.\n")
            return 0
        for line in purge_orphan_auth_user(supabase, auth_user["id"]):
            print(f"  {line}")
        print("\nDone. Browser snippet:\n")
        print(browser_snippet())
        print()
        return 0

    user_id = db_user["user_id"]
    auth_id = db_user["auth_id"]
    print(f"user_id  {user_id}")
    print(f"auth_id  {auth_id}  ({auth_user['providers'] if auth_user else 'NO GOTRUE USER'})")
    print(f"created  {db_user['created_at']}")

    print("\nrows owned by this account")
    print("-" * 52)
    total = 0
    for name, n in census(supabase, user_id, optional_on):
        if n is None:
            print(f"  {name:<34} (table absent)")
        else:
            if isinstance(n, int):
                total += n
            print(f"  {name:<34} {n}")
    print("-" * 52)
    print(f"  {'total':<34} {total}")

    if not args.yes:
        print("\nDry run - nothing was deleted. Re-run with --yes.\n")
        return 0

    print("\nerasing")
    print("-" * 52)
    try:
        for line in purge(supabase, user_id, auth_id, optional_on, args.release_plan_codes):
            print(f"  {line}")
    except Exception as exc:  # noqa: BLE001
        print(f"\nFAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        print("The users row survives, so a re-run retries the whole account.", file=sys.stderr)
        return 1

    if not args.no_redis:
        for line in clear_redis(user_id, auth_id, args.redis_url):
            print(f"  {line}")

    # Verify rather than assume: re-resolve both sides.
    print("\nverify")
    print("-" * 52)
    left_db = find_db_user(supabase, email)
    left_auth = find_auth_user(supabase, email)
    print(f"  public.users row  {'STILL PRESENT' if left_db else 'gone'}")
    print(f"  auth.users row    {'STILL PRESENT' if left_auth else 'gone'}")
    ok = not left_db and not left_auth

    print("\nbrowser state - paste into the devtools console on the origin you test")
    print("-" * 52)
    print(browser_snippet())
    print()
    if ok:
        print(f"{email} is fresh. Sign up again.\n")
        return 0
    print("INCOMPLETE - see above.\n", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
