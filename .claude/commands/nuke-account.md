---
name: nuke-account
description: Hard-delete a dev account and every trace of it so the signup flow can be validated from zero
user_invocable: true
allowed-tools: Bash, Read
---

# /nuke-account — make an email brand new again

You erase one account so the **signup experience can be re-run from a truly cold
start**: no `public.users` row, no GoTrue user, no Google identity link, no
free-window usage stamp, no onboarding flags, no conversations, no storage
objects, no browser state.

`scripts/nuke_account.py` owns every read and write. You never issue SQL, never
call the Supabase MCP, never touch `auth.admin` yourself — the script reuses the
production purge (`backend/app/services/account_purge_service._purge_one`), so
the erasure order lives in exactly one place. Your job is to run it, read the
census back to the user, and hand them the browser snippet.

## Argument: $ARGUMENTS

Free-form; parse case-insensitively:

| In `$ARGUMENTS` | Effect |
|---|---|
| an email address | target that account instead of the default (`mhfallath99@gmail.com`) |
| `force`, `now`, `-y`, `yes` | skip the confirmation step — go straight to the erase |
| `analytics` | also delete `analytics_events` (default: kept, `user_id` → NULL) |
| `payments` | also delete `payment_transactions` (default: kept — real Moyasar records) |
| `keep-llm` | keep `llm_calls` (default: deleted) |
| `keep-audit` | keep `audit_logs` (default: deleted) |
| `codes` | hand back activation-code capacity this account consumed |
| `snippet` | print ONLY the browser-clearing snippet and stop |
| empty | default account, with confirmation |

Map those onto the script's flags: `--yes --analytics --payments --keep-llm-calls
--keep-audit --release-plan-codes --snippet`.

## Procedure

**1. Census first.** Always run the dry run before anything destructive:

```
python scripts/nuke_account.py <email-if-given> [--analytics] [--payments] [--keep-llm-calls] [--keep-audit]
```

It prints the account's ids, its linked auth providers, and a per-table row
count. Nothing is written.

**2. Show it and confirm.** Relay the census as a short table — the user cannot
see your tool output. State the total and call out anything surprising (a
`user_subscriptions` row means a paid plan is about to vanish; a
`payment_transactions` count means real money records exist). Then ask for
confirmation in one line.

Skip this step **only** if `$ARGUMENTS` carried `force`/`now`/`-y`/`yes`.

**3. Erase.** Re-run the same command with `--yes` appended. Read the output:
every step prints what it did, and the script re-resolves both `public.users`
and `auth.users` afterwards to verify rather than assume.

**4. Hand over the browser snippet.** The script prints it last. Give it to the
user verbatim in a copyable block and say which origin to paste it on
(`https://rayhanai.com` or `http://localhost:3000` — the two are separate
origins with separate storage, so testing both means pasting it twice).

## What to tell the user afterwards

- Whether the email is confirmed fresh (both rows gone).
- Anything the script reported as `SKIPPED` or `STILL PRESENT`.
- If a `google` identity was linked, mention it is gone — the one-tap signup
  path is testable again.

## Traps

- **The script refuses unlisted emails.** `ALLOWED_LOCAL_PARTS` in
  `scripts/nuke_account.py` is the allowlist and it exits 2 on anything else.
  Gmail `+tag` aliases of a listed address pass (`…+t4@gmail.com`), so a burn
  loop needs no edit. If the user wants a genuinely new address covered, ask
  before adding it — that constant is the only thing between a typo and a
  deleted customer.

- **Redis defaults to LOCALHOST.** The repo `.env` sets
  `REDIS_URL=redis://localhost:6379`. Clearing it does nothing for
  rayhanai.com. That is usually fine — the GoTrue user is gone, so any token
  minted for it is already dead — but if the user specifically wants the prod
  session key gone, pass `--redis-url` with the Railway public URL
  (`hopper.proxy.rlwy.net:11864`). Never invent that URL's password; ask.

- **Auth rate limit is per-IP, not per-user.** `/api/v1/auth/*` allows 10
  req/min per IP and there is no per-user key to clear. If a signup loop starts
  429ing mid-run, that is the cause and the fix is to wait a minute — do not go
  flush `ratelimit:*`, which would loosen the limiter for every other caller in
  that bucket.

- **Supabase's own email throttle is separate.** GoTrue caps confirmation emails
  per hour, independent of everything here. Repeated email-password signups can
  hit it even on a perfectly clean account.

- **`analytics_events` is kept by default on purpose.** The FK is `SET NULL`, so
  kept rows survive as anonymous events and the funnel keeps remembering your
  test signups. Pass `analytics` when you want the funnel clean.

- **This is production.** There is one Supabase project and the repo `.env`
  points at it whether you run locally or not. Nothing is recoverable.
