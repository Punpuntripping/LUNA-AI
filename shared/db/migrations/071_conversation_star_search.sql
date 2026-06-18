-- 071_conversation_star_search.sql
-- Feature: صفحة المحادثات (/chats) + تمييز بنجمة (Star) + بحث داخل الرسائل
-- Plan: .claude/plans/chats_page_star_search.md  (§ "Data model")
--
-- Purpose:
--   1. Add a per-conversation STAR (favorite). starred_at is a timestamp (not a
--      bool) so the sidebar/chats list can order "most-recently-starred first".
--      NULL = not starred.
--   2. Add pg_trgm GIN indexes so the new search endpoint can do fast substring
--      (ILIKE '%q%') search over message content AND conversation titles. Arabic
--      substring search via trigram matches the user's literal typing better than
--      Postgres' weak Arabic stemming; FTS/semantic can come later.
--   3. Add a partial index for the starred-first ordering of a user's list.
--
-- Dependencies:
--   - 001_extensions.sql   (pg_trgm — verified present in live DB)
--   - conversations table  (conversation_id, user_id, title_ar, updated_at,
--                           deleted_at — all verified live)
--   - messages table       (message_id, conversation_id, content — verified live)
--
-- Verified live-state facts (Supabase MCP, 2026-06-18):
--   * pg_trgm + vector extensions installed; 'arabic' FTS config exists (unused here).
--   * conversations has NO star/pin column yet -> this migration adds starred_at.
--   * Corpus tiny today (911 messages / 322 conversations) — indexes are
--     forward-safety, not a current performance need.
--
-- Security / RLS:
--   * No RLS change. starred_at is just another column on the already-RLS'd
--     conversations table; all reads/writes stay user-scoped through the service
--     layer (get_user_id + eq("user_id", …)). Search is service-role + explicit
--     user_id filter — no cross-user leakage.
--
-- Idempotent: ADD COLUMN IF NOT EXISTS, CREATE INDEX IF NOT EXISTS.

BEGIN;

------------------------------------------------------------------------
-- 1. Star column
------------------------------------------------------------------------
ALTER TABLE public.conversations
    ADD COLUMN IF NOT EXISTS starred_at TIMESTAMPTZ;

COMMENT ON COLUMN public.conversations.starred_at IS
    'When the user starred (favorited) this conversation. NULL = not starred. Timestamp (not bool) so starred conversations order most-recently-starred first.';

------------------------------------------------------------------------
-- 2. Trigram indexes for substring search (ILIKE %q%)
------------------------------------------------------------------------
-- Message-content search (the "بحث داخل الرسائل" requirement).
CREATE INDEX IF NOT EXISTS idx_messages_content_trgm
    ON public.messages USING gin (content gin_trgm_ops);

-- Conversation-title search.
CREATE INDEX IF NOT EXISTS idx_conversations_title_ar_trgm
    ON public.conversations USING gin (title_ar gin_trgm_ops);

------------------------------------------------------------------------
-- 3. Starred-first ordering (partial — only starred, live rows)
------------------------------------------------------------------------
CREATE INDEX IF NOT EXISTS idx_conversations_user_starred
    ON public.conversations (user_id, starred_at DESC)
    WHERE starred_at IS NOT NULL AND deleted_at IS NULL;

COMMIT;
