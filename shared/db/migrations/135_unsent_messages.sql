-- ============================================================================
-- Migration 135 — unsent_messages: what the user meant to ask but never got to
--
-- WHY THIS IS ITS OWN TABLE AND NOT A COLUMN ON `messages`.
-- The quota gate (backend/app/services/message_service.py §0c) deliberately
-- persists NOTHING on a block. That ordering is not an accident — an earlier
-- build saved the user row first and let the gate reject afterwards, which left
-- a permanently unanswered user message in the thread: nothing picked it back
-- up when the window reset, no retry path could reach it, and `context_service`
-- fed the orphan turn into the NEXT request's history as a second consecutive
-- user message.
--
-- A `delivery_status` column on `messages` would re-introduce exactly that row
-- and make its invisibility a matter of DISCIPLINE: `messages` is read in ~14
-- places (context_service, conversation_service, blog_service, orchestrator ×3,
-- router/context ×2, base/context, memory/agent ×2, memory/summarize, the list
-- endpoint), and every one of them — plus every future one — would need the
-- filter. One miss silently restores the bug, in the agent's context window,
-- where it is hardest to notice.
--
-- Here, invisibility is STRUCTURAL. No history builder, no memory agent and no
-- message endpoint queries this table, so none of them can leak it. That is the
-- entire point of the shape.
--
-- WHAT IT IS FOR. Product intent only: reading what free users tried to ask at
-- the moment they hit the wall. It is NOT a retry queue and NOT a draft store —
-- nothing replays these rows, and the client still re-hydrates the composer
-- from the `quota_exceeded` SSE event, which remains the user's copy.
--
-- ⚠ NO RLS POLICIES, BY DESIGN. The backend writes through the SERVICE-ROLE
-- client (backend/app/deps.py:123), which bypasses RLS. With RLS on and zero
-- policies, `anon` and `authenticated` can neither read nor write — so this
-- text can never reach a browser, not even the author's own. Read it in the
-- Supabase console. Adding a SELECT policy here would put unsent text on the
-- wire; do not, without deciding that deliberately.
-- ============================================================================

create table if not exists public.unsent_messages (
  unsent_id       uuid primary key default uuid_generate_v4(),

  user_id         uuid not null
                    references public.users(user_id) on delete cascade,

  -- The conversation the send was aimed at. NULLABLE only because of the
  -- `on delete set null` below: a hard-deleted conversation must not take the
  -- intent record with it — the question the user was trying to ask is still
  -- the thing being studied. Always populated at write time (the send route is
  -- /conversations/{id}/messages, so a conversation always exists by then).
  conversation_id uuid
                    references public.conversations(conversation_id) on delete set null,

  -- The message body, verbatim, exactly as the composer sent it. Stored raw —
  -- identical treatment to `messages.content`, which also holds decoded text.
  -- وضع السرية masking is a PIPELINE concern (the turn codec is built at §2c,
  -- downstream of the gate) and has no bearing on what is persisted here.
  content         text not null,

  -- Which gate refused the send. Mirrors the three exception classes in
  -- shared/quota/__init__.py; the check constraint is what keeps a typo in the
  -- caller from quietly creating a fourth, unqueryable bucket.
  --   quota_exceeded    — window spent (the free-user case this exists for)
  --   plan_inactive     — users.plan_id IS NULL, account not activated
  --   quota_unavailable — get_user_quota_state failed, gate failed closed
  reason          text not null
                    check (reason in ('quota_exceeded', 'plan_inactive', 'quota_unavailable')),

  -- Block context, copied off the exception. All nullable: `plan_inactive`
  -- carries no plan and no window, and `quota_unavailable` knows only which
  -- (meter, period) it was asking about when the read failed.
  --
  -- `plan_id` is the EFFECTIVE plan — an expired paid subscription reports
  -- 'free' here, same as it does to the upgrade dialog. That is the field to
  -- filter on when you want "what did free users try to ask".
  plan_id         text,
  meter           text,   -- ocr | ord | web | plan
  period          text,   -- session | weekly | monthly | none
  used_amount     numeric,
  limit_amount    numeric,

  -- Workspace items the user had attached to the blocked send. The items
  -- themselves already exist (upload precedes send) and are untouched by the
  -- block; this only records what the message was carrying. No FK — the array
  -- is a record of intent, and a later attachment purge (125) must not rewrite
  -- history or fail this insert.
  attachment_ids  uuid[],

  created_at      timestamptz not null default now()
);

comment on table public.unsent_messages is
  'Messages the user submitted that the quota gate refused, kept for product '
  'analysis of what users try to ask when they hit a limit. Deliberately NOT '
  'in `messages`: no conversation history, memory agent, or message endpoint '
  'reads this table, so it cannot surface to the user or to any agent. Not a '
  'retry queue — nothing replays these rows. Written by '
  'backend/app/services/message_service.py on the three gate-block paths.';

comment on column public.unsent_messages.reason is
  'Which gate refused the send — mirrors QuotaExceeded / PlanInactive / '
  'QuotaUnavailable in shared/quota/__init__.py.';

comment on column public.unsent_messages.plan_id is
  'EFFECTIVE plan at block time (an expired paid plan reports ''free''). '
  'Filter on this for the free-user question.';

-- The two reads this table exists to serve: "recent blocked intents" and
-- "everything one user tried to send". Both newest-first.
create index if not exists idx_unsent_messages_created
  on public.unsent_messages (created_at desc);

create index if not exists idx_unsent_messages_user_created
  on public.unsent_messages (user_id, created_at desc);

-- --- RLS -------------------------------------------------------------------
-- House rule: every table has RLS enabled. Here it is also the enforcement
-- mechanism for the table's whole premise. RLS ON with NO policies = default
-- deny for `anon` and `authenticated`; only the service-role client (which
-- bypasses RLS) can write, and only the console can read. See the header.
alter table public.unsent_messages enable row level security;
