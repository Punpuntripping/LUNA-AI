-- 015_indexes.sql
-- Additional indexes not covered in table creation files
-- Composite indexes, partial indexes, and any supplementary indexes

-- ============================================
-- USERS — additional indexes
-- ============================================
-- (auth_id and email already have UNIQUE constraint indexes from 003_users.sql)

-- ============================================
-- LAWYER CASES — additional composite indexes
-- ============================================
-- Composite: lawyer + status for filtered case lists
CREATE INDEX IF NOT EXISTS idx_cases_lawyer_status
    ON public.lawyer_cases (lawyer_user_id, status)
    WHERE deleted_at IS NULL;

-- Composite: lawyer + type for filtered case lists
CREATE INDEX IF NOT EXISTS idx_cases_lawyer_type
    ON public.lawyer_cases (lawyer_user_id, case_type)
    WHERE deleted_at IS NULL;

-- Next hearing date for upcoming hearings queries
CREATE INDEX IF NOT EXISTS idx_cases_next_hearing
    ON public.lawyer_cases (next_hearing_date ASC)
    WHERE next_hearing_date IS NOT NULL AND deleted_at IS NULL AND status = 'active';

-- ============================================
-- CONVERSATIONS — additional composite indexes
-- ============================================
-- User + case for case-specific conversation lists
CREATE INDEX IF NOT EXISTS idx_conversations_user_case
    ON public.conversations (user_id, case_id)
    WHERE deleted_at IS NULL;

-- ============================================
-- MESSAGES — additional composite indexes
-- ============================================
-- Conversation + role for filtering assistant messages
CREATE INDEX IF NOT EXISTS idx_messages_conversation_role
    ON public.messages (conversation_id, role)
    WHERE role = 'assistant';

-- ============================================
-- CASE DOCUMENTS — additional composite indexes
-- ============================================
-- Case + extraction status for pipeline queries
CREATE INDEX IF NOT EXISTS idx_documents_case_extraction
    ON public.case_documents (case_id, extraction_status)
    WHERE deleted_at IS NULL;

-- ============================================
-- AUDIT LOGS — additional composite indexes
-- ============================================
-- Resource type + action for specific event queries
CREATE INDEX IF NOT EXISTS idx_audit_resource_action
    ON public.audit_logs (resource_type, action, created_at DESC);

-- ============================================
-- TRIGRAM INDEXES for fuzzy Arabic search
-- ============================================
-- Trigram index on case name for fuzzy search
CREATE INDEX IF NOT EXISTS idx_cases_name_trgm
    ON public.lawyer_cases USING gin (case_name gin_trgm_ops);

-- Trigram index on conversation title for fuzzy search
CREATE INDEX IF NOT EXISTS idx_conversations_title_trgm
    ON public.conversations USING gin (title_ar gin_trgm_ops);

-- Trigram index on document name for fuzzy search
CREATE INDEX IF NOT EXISTS idx_documents_name_trgm
    ON public.case_documents USING gin (document_name gin_trgm_ops);

-- Trigram index on memory content for fuzzy search
CREATE INDEX IF NOT EXISTS idx_memories_content_trgm
    ON public.case_memories USING gin (content_ar gin_trgm_ops);
