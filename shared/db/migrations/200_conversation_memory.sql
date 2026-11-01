-- 200_conversation_memory.sql
-- Feature: conversation memory — one hidden list per conversation of what must
-- not be forgotten (.claude/plans/conversation_memory.md).
--
-- Purpose:
--   The router saves the user's key requests, corrections and direction-changing
--   facts here; every agent except rerankers and Layer 4 reads them as one
--   rendered block. A corrected amount then stops coming back from OCR text,
--   document summaries or earlier replies.
--
-- Data model:
--   * seq: the model-facing alias M-{seq}, stable for the life of the entry.
--     UNIQUE (conversation_id, seq). Assigned by the app as max(seq) + 1 (a
--     concurrent insert loses on the UNIQUE and retries with a fresh max).
--     Removal is a hard delete and this table keeps no counter, so nothing
--     records a removed TOP entry: a later call can hand its seq out again.
--   * kind: 'request' | 'fact' | 'legal_position'.
--   * insisted: only on legal_position. The disclaimer agents see comes from
--     this flag at render time and is never stored in content.
--   * content: stored REAL (store-real invariant), encoded at load like
--     compaction summaries. Limits are enforced in the app first; the CHECK is
--     the backstop. Removal is a hard delete: there is no edit history.
--
-- Indexes:
--   None added. The per-turn load is "WHERE conversation_id = ? AND user_id = ?
--   ORDER BY seq", which UNIQUE (conversation_id, seq) already serves; user_id
--   only filters a handful of rows. The user_id FK cascade is unindexed: it
--   runs on account deletion only, and purge_user_data removes the
--   conversations (cascading through conversation_id) first.
--
-- Security / RLS:
--   * DENY-ALL from clients: RLS enabled with NO policies + explicit REVOKE from
--     anon/authenticated. Only the backend (service role, bypasses RLS) touches
--     this table, so every query filters on BOTH conversation_id and user_id.
--     Not a column on conversations: anon/authenticated hold full grants there,
--     so it could not stay hidden.
--
-- Soft delete:
--   trg_conversations_delete_memory hard-deletes a conversation's entries the
--   moment deleted_at is set, whichever path set it: delete_conversation,
--   soft_delete_case_cascade or anything later. Conversations have no restore
--   path. The function is SECURITY DEFINER because clients hold UPDATE on
--   conversations and no grant on this table, so a plain trigger would fail a
--   client's update. EXECUTE is revoked from clients: Postgres checks it when a
--   trigger is created, not when it fires, so their updates still fire it.
--   A hard delete of the conversation goes through the FK cascade instead.
--
-- LOCKS: DROP TRIGGER and CREATE TRIGGER lock public.conversations, which every
-- turn reads, for the length of this transaction. lock_timeout fails fast
-- instead of queueing requests behind it.
--
-- NOT verified live: written without database access. 199 is reserved by the
-- supersession branch; recheck the number against prod before applying.
--
-- Idempotent: CREATE TABLE IF NOT EXISTS, CREATE OR REPLACE FUNCTION, DROP
-- TRIGGER IF EXISTS + CREATE; RLS enable, REVOKE and COMMENT are naturally
-- re-runnable.

BEGIN;

SET LOCAL lock_timeout = '5s';

CREATE TABLE IF NOT EXISTS public.conversation_memory_entries (
    entry_id        uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    conversation_id uuid NOT NULL REFERENCES public.conversations(conversation_id) ON DELETE CASCADE,
    user_id         uuid NOT NULL REFERENCES public.users(user_id) ON DELETE CASCADE,
    seq             integer NOT NULL,          -- alias M-{seq}, see header
    kind            text NOT NULL,
    content         text NOT NULL,             -- stored REAL (store-real invariant)
    insisted        boolean NOT NULL DEFAULT false,
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT cme_kind_chk CHECK (kind IN ('request', 'fact', 'legal_position')),
    CONSTRAINT cme_content_len_chk CHECK (char_length(content) BETWEEN 1 AND 1500),
    CONSTRAINT cme_insisted_chk CHECK (NOT insisted OR kind = 'legal_position'),
    CONSTRAINT cme_convo_seq_uniq UNIQUE (conversation_id, seq)
);

ALTER TABLE public.conversation_memory_entries ENABLE ROW LEVEL SECURITY;

-- Deny-all: no policies are created on purpose. Belt-and-suspenders: strip the
-- default PostgREST grants so even a future accidental policy can't expose it.
REVOKE ALL ON public.conversation_memory_entries FROM anon, authenticated;

COMMENT ON TABLE public.conversation_memory_entries IS
    'Hidden per-conversation memory written by the router and read by the agents. Service-role only — deny-all RLS, no client access. content is stored real; removal is a hard delete.';

-- Soft delete: see the header.
CREATE OR REPLACE FUNCTION public.delete_conversation_memory_on_soft_delete()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path TO 'public'
AS $function$
BEGIN
    DELETE FROM public.conversation_memory_entries
     WHERE conversation_id = NEW.conversation_id;
    RETURN NULL;
END;
$function$;

COMMENT ON FUNCTION public.delete_conversation_memory_on_soft_delete() IS
    'AFTER UPDATE OF deleted_at on conversations (200): hard-deletes the conversation''s memory entries when it is soft-deleted. SECURITY DEFINER because clients hold UPDATE on conversations and no grant on conversation_memory_entries.';

REVOKE EXECUTE ON FUNCTION public.delete_conversation_memory_on_soft_delete()
    FROM PUBLIC, anon, authenticated;

DROP TRIGGER IF EXISTS trg_conversations_delete_memory ON public.conversations;
CREATE TRIGGER trg_conversations_delete_memory
    AFTER UPDATE OF deleted_at ON public.conversations
    FOR EACH ROW
    WHEN (OLD.deleted_at IS NULL AND NEW.deleted_at IS NOT NULL)
    EXECUTE FUNCTION public.delete_conversation_memory_on_soft_delete();

-- Self-check: the feature's whole privacy claim is that clients cannot reach
-- this table, so a re-run on a drifted table must fail, not pass quietly. The
-- trigger is checked with its function: without SECURITY DEFINER it would break
-- a client's update of conversations.
DO $$
BEGIN
    IF NOT (SELECT c.relrowsecurity FROM pg_class c
             WHERE c.oid = 'public.conversation_memory_entries'::regclass) THEN
        RAISE EXCEPTION '200: RLS is off on conversation_memory_entries';
    END IF;
    IF EXISTS (SELECT 1 FROM pg_policy p
                WHERE p.polrelid = 'public.conversation_memory_entries'::regclass) THEN
        RAISE EXCEPTION '200: a policy exists on conversation_memory_entries — it must stay deny-all';
    END IF;
    IF NOT EXISTS (SELECT 1
                     FROM pg_trigger t
                     JOIN pg_proc p ON p.oid = t.tgfoid
                    WHERE t.tgrelid = 'public.conversations'::regclass
                      AND t.tgname = 'trg_conversations_delete_memory'
                      AND t.tgenabled <> 'D'
                      AND p.proname = 'delete_conversation_memory_on_soft_delete'
                      AND p.prosecdef
                      AND 'search_path=public' = ANY (p.proconfig)) THEN
        RAISE EXCEPTION '200: trg_conversations_delete_memory is missing, disabled, or its function is not SECURITY DEFINER with search_path=public';
    END IF;
END $$;

COMMIT;
