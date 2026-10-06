-- Migration 171: plans.max_parallel_runs — up to 5 concurrent conversations for
-- `max` and `dev`, 1 for every other plan.
--
-- Plan: .claude/plans/parallel_conversations.md (owner decisions 2026-10-04).
--
-- WHY: in-flight dedup was per conversation only, so any plan could already run
-- N conversations at once via N tabs, and the ord meter (settled AFTER a run)
-- let all N pass a near-empty window. The backend now counts a user's live runs
-- in-process (message_service._user_inflight_runs) and refuses a send past
-- this cap with the `parallel_limit` SSE event. The cap is read off the
-- EFFECTIVE plan (an expired paid plan → free → 1).
--
-- ⚠ get_user_quota_state is deliberately NOT touched. Adding this column to its
--   RETURNS TABLE would force a DROP + rebuild of user_subscriptions_live (137
--   keeps those signatures byte-identical for exactly that reason). The backend
--   reads plans.max_parallel_runs with a separate small cached query instead.
--
-- §2 widens unsent_messages.reason (135's inline CHECK) with 'parallel_limit',
-- so a send refused by the cap is captured like the three quota-gate blocks.
-- For those rows used_amount / limit_amount hold RUN COUNTS (other live runs /
-- the cap), not points; meter and period are NULL.
--
-- Additive: an old backend ignores the column. Idempotent: re-runnable.
-- Verify live after applying (migration-drift rule):
--   select plan_id, max_parallel_runs from public.plans order by plan_id;
-- Dependencies: 068 (plans), 135 (unsent_messages).

-- §1 ─ the cap column ─────────────────────────────────────────────────────────

ALTER TABLE public.plans
    ADD COLUMN IF NOT EXISTS max_parallel_runs int NOT NULL DEFAULT 1;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'public.plans'::regclass
          AND conname  = 'plans_max_parallel_runs_check'
    ) THEN
        ALTER TABLE public.plans
            ADD CONSTRAINT plans_max_parallel_runs_check
            CHECK (max_parallel_runs BETWEEN 1 AND 10);
    END IF;
END $$;

COMMENT ON COLUMN public.plans.max_parallel_runs IS
    'How many conversations a user on this (effective) plan may have answering '
    'at the same time. Enforced in-process by backend message_service '
    '(parallel_limit SSE event); exposed on GET /api/v1/usage. Migration 171.';

UPDATE public.plans SET
    max_parallel_runs = 5,
    updated_at        = now()
WHERE plan_id IN ('max', 'dev')
  AND max_parallel_runs IS DISTINCT FROM 5;

-- §2 ─ unsent_messages.reason accepts 'parallel_limit' ────────────────────────
--
-- 135 declared the CHECK inline, so its name is Postgres-generated. Drop every
-- CHECK on unsent_messages that mentions `reason` (whatever it is called live),
-- then add the widened one under a fixed name.

DO $$
DECLARE
    c record;
BEGIN
    FOR c IN
        SELECT conname
        FROM pg_constraint
        WHERE conrelid = 'public.unsent_messages'::regclass
          AND contype  = 'c'
          AND pg_get_constraintdef(oid) ILIKE '%reason%'
    LOOP
        EXECUTE format('ALTER TABLE public.unsent_messages DROP CONSTRAINT %I', c.conname);
    END LOOP;

    ALTER TABLE public.unsent_messages
        ADD CONSTRAINT unsent_messages_reason_check
        CHECK (reason IN (
            'quota_exceeded', 'plan_inactive', 'quota_unavailable', 'parallel_limit'
        ));
END $$;

COMMENT ON COLUMN public.unsent_messages.reason IS
    'Which gate refused the send — mirrors QuotaExceeded / PlanInactive / '
    'QuotaUnavailable in shared/quota/__init__.py, plus parallel_limit (171: the '
    'plan''s max_parallel_runs was already in use; used/limit_amount = run counts).';
