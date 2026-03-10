-- 016_rls.sql
-- Row Level Security: enable RLS on ALL tables + create ALL policies
-- Principle: Users can only access their own data.
-- Admin operations use service_role key which bypasses RLS entirely.

-- ============================================
-- HELPER FUNCTION
-- Maps auth.uid() (Supabase Auth UUID) to public.users.user_id
-- ============================================
CREATE OR REPLACE FUNCTION public.get_current_user_id()
RETURNS UUID AS $$
    SELECT user_id FROM public.users WHERE auth_id = auth.uid()
$$ LANGUAGE sql SECURITY DEFINER STABLE;


-- ============================================
-- ENABLE RLS ON ALL APP TABLES
-- ============================================
ALTER TABLE public.users ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.lawyer_cases ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.case_memories ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.case_documents ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.conversations ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.messages ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.message_attachments ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.message_feedback ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.consultation_articles ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.audit_logs ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.model_pricing ENABLE ROW LEVEL SECURITY;


-- ============================================
-- USERS — own row only
-- ============================================

-- SELECT: users can only read their own profile
CREATE POLICY "users_select_own"
ON public.users
FOR SELECT
TO authenticated
USING (auth_id = auth.uid());

-- UPDATE: users can only update their own profile
CREATE POLICY "users_update_own"
ON public.users
FOR UPDATE
TO authenticated
USING (auth_id = auth.uid())
WITH CHECK (auth_id = auth.uid());

-- INSERT: handled by trigger (profile created on auth.users insert)
-- But allow insert for the trigger to work with service role,
-- and for edge cases where the user's auth_id matches
CREATE POLICY "users_insert_own"
ON public.users
FOR INSERT
TO authenticated
WITH CHECK (auth_id = auth.uid());

-- DELETE: not allowed (admin operation only, done via service_role)


-- ============================================
-- LAWYER CASES — own cases only
-- ============================================

-- SELECT: lawyers see only their own cases (excluding soft-deleted)
CREATE POLICY "cases_select_own"
ON public.lawyer_cases
FOR SELECT
TO authenticated
USING (
    lawyer_user_id = (SELECT user_id FROM users WHERE auth_id = auth.uid())
    AND deleted_at IS NULL
);

-- INSERT: lawyers can create cases for themselves
CREATE POLICY "cases_insert_own"
ON public.lawyer_cases
FOR INSERT
TO authenticated
WITH CHECK (
    lawyer_user_id = (SELECT user_id FROM users WHERE auth_id = auth.uid())
);

-- UPDATE: lawyers can update their own cases
CREATE POLICY "cases_update_own"
ON public.lawyer_cases
FOR UPDATE
TO authenticated
USING (
    lawyer_user_id = (SELECT user_id FROM users WHERE auth_id = auth.uid())
)
WITH CHECK (
    lawyer_user_id = (SELECT user_id FROM users WHERE auth_id = auth.uid())
);

-- DELETE: soft delete only (set deleted_at via UPDATE policy)
CREATE POLICY "cases_delete_own"
ON public.lawyer_cases
FOR DELETE
TO authenticated
USING (
    lawyer_user_id = (SELECT user_id FROM users WHERE auth_id = auth.uid())
);


-- ============================================
-- CASE DOCUMENTS — access via case ownership
-- ============================================

-- SELECT: access documents for own cases only (excluding soft-deleted)
CREATE POLICY "case_documents_select_own"
ON public.case_documents
FOR SELECT
TO authenticated
USING (
    case_id IN (
        SELECT case_id FROM lawyer_cases
        WHERE lawyer_user_id = (SELECT user_id FROM users WHERE auth_id = auth.uid())
    )
    AND deleted_at IS NULL
);

-- INSERT: upload documents to own cases
CREATE POLICY "case_documents_insert_own"
ON public.case_documents
FOR INSERT
TO authenticated
WITH CHECK (
    case_id IN (
        SELECT case_id FROM lawyer_cases
        WHERE lawyer_user_id = (SELECT user_id FROM users WHERE auth_id = auth.uid())
    )
);

-- UPDATE: update own case documents
CREATE POLICY "case_documents_update_own"
ON public.case_documents
FOR UPDATE
TO authenticated
USING (
    case_id IN (
        SELECT case_id FROM lawyer_cases
        WHERE lawyer_user_id = (SELECT user_id FROM users WHERE auth_id = auth.uid())
    )
)
WITH CHECK (
    case_id IN (
        SELECT case_id FROM lawyer_cases
        WHERE lawyer_user_id = (SELECT user_id FROM users WHERE auth_id = auth.uid())
    )
);

-- DELETE: soft delete via UPDATE
CREATE POLICY "case_documents_delete_own"
ON public.case_documents
FOR DELETE
TO authenticated
USING (
    case_id IN (
        SELECT case_id FROM lawyer_cases
        WHERE lawyer_user_id = (SELECT user_id FROM users WHERE auth_id = auth.uid())
    )
);


-- ============================================
-- CASE MEMORIES — access via case ownership
-- ============================================

-- SELECT: access memories for own cases only (excluding soft-deleted)
CREATE POLICY "case_memories_select_own"
ON public.case_memories
FOR SELECT
TO authenticated
USING (
    case_id IN (
        SELECT case_id FROM lawyer_cases
        WHERE lawyer_user_id = (SELECT user_id FROM users WHERE auth_id = auth.uid())
    )
    AND deleted_at IS NULL
);

-- INSERT: create memories for own cases
CREATE POLICY "case_memories_insert_own"
ON public.case_memories
FOR INSERT
TO authenticated
WITH CHECK (
    case_id IN (
        SELECT case_id FROM lawyer_cases
        WHERE lawyer_user_id = (SELECT user_id FROM users WHERE auth_id = auth.uid())
    )
);

-- UPDATE: update own case memories
CREATE POLICY "case_memories_update_own"
ON public.case_memories
FOR UPDATE
TO authenticated
USING (
    case_id IN (
        SELECT case_id FROM lawyer_cases
        WHERE lawyer_user_id = (SELECT user_id FROM users WHERE auth_id = auth.uid())
    )
)
WITH CHECK (
    case_id IN (
        SELECT case_id FROM lawyer_cases
        WHERE lawyer_user_id = (SELECT user_id FROM users WHERE auth_id = auth.uid())
    )
);

-- DELETE: soft delete via UPDATE
CREATE POLICY "case_memories_delete_own"
ON public.case_memories
FOR DELETE
TO authenticated
USING (
    case_id IN (
        SELECT case_id FROM lawyer_cases
        WHERE lawyer_user_id = (SELECT user_id FROM users WHERE auth_id = auth.uid())
    )
);


-- ============================================
-- CONVERSATIONS — own conversations only
-- ============================================

-- SELECT: users see only their own conversations (excluding soft-deleted)
CREATE POLICY "conversations_select_own"
ON public.conversations
FOR SELECT
TO authenticated
USING (
    user_id = (SELECT user_id FROM users WHERE auth_id = auth.uid())
    AND deleted_at IS NULL
);

-- INSERT: users create their own conversations
CREATE POLICY "conversations_insert_own"
ON public.conversations
FOR INSERT
TO authenticated
WITH CHECK (
    user_id = (SELECT user_id FROM users WHERE auth_id = auth.uid())
);

-- UPDATE: users update their own conversations
CREATE POLICY "conversations_update_own"
ON public.conversations
FOR UPDATE
TO authenticated
USING (
    user_id = (SELECT user_id FROM users WHERE auth_id = auth.uid())
)
WITH CHECK (
    user_id = (SELECT user_id FROM users WHERE auth_id = auth.uid())
);

-- DELETE: soft delete via UPDATE
CREATE POLICY "conversations_delete_own"
ON public.conversations
FOR DELETE
TO authenticated
USING (
    user_id = (SELECT user_id FROM users WHERE auth_id = auth.uid())
);


-- ============================================
-- MESSAGES — access via conversation ownership
-- ============================================

-- SELECT: access messages in own conversations
CREATE POLICY "messages_select_own"
ON public.messages
FOR SELECT
TO authenticated
USING (
    conversation_id IN (
        SELECT conversation_id FROM conversations
        WHERE user_id = (SELECT user_id FROM users WHERE auth_id = auth.uid())
    )
);

-- INSERT: send messages to own conversations
CREATE POLICY "messages_insert_own"
ON public.messages
FOR INSERT
TO authenticated
WITH CHECK (
    conversation_id IN (
        SELECT conversation_id FROM conversations
        WHERE user_id = (SELECT user_id FROM users WHERE auth_id = auth.uid())
    )
);

-- UPDATE: not typically needed (messages are immutable)
-- DELETE: not allowed (messages persist for audit, cascade-deleted with conversation)


-- ============================================
-- MESSAGE ATTACHMENTS — access via message -> conversation -> user chain
-- ============================================

-- SELECT: access attachments for messages in own conversations
CREATE POLICY "message_attachments_select_own"
ON public.message_attachments
FOR SELECT
TO authenticated
USING (
    message_id IN (
        SELECT m.message_id FROM messages m
        JOIN conversations c ON c.conversation_id = m.conversation_id
        WHERE c.user_id = (SELECT user_id FROM users WHERE auth_id = auth.uid())
    )
);

-- INSERT: attach files to messages in own conversations
CREATE POLICY "message_attachments_insert_own"
ON public.message_attachments
FOR INSERT
TO authenticated
WITH CHECK (
    message_id IN (
        SELECT m.message_id FROM messages m
        JOIN conversations c ON c.conversation_id = m.conversation_id
        WHERE c.user_id = (SELECT user_id FROM users WHERE auth_id = auth.uid())
    )
);

-- DELETE: remove attachments from own messages
CREATE POLICY "message_attachments_delete_own"
ON public.message_attachments
FOR DELETE
TO authenticated
USING (
    message_id IN (
        SELECT m.message_id FROM messages m
        JOIN conversations c ON c.conversation_id = m.conversation_id
        WHERE c.user_id = (SELECT user_id FROM users WHERE auth_id = auth.uid())
    )
);


-- ============================================
-- MESSAGE FEEDBACK — own feedback only
-- ============================================

-- SELECT: users see only their own feedback
CREATE POLICY "message_feedback_select_own"
ON public.message_feedback
FOR SELECT
TO authenticated
USING (
    user_id = (SELECT user_id FROM users WHERE auth_id = auth.uid())
);

-- INSERT: users can rate messages in their own conversations
CREATE POLICY "message_feedback_insert_own"
ON public.message_feedback
FOR INSERT
TO authenticated
WITH CHECK (
    user_id = (SELECT user_id FROM users WHERE auth_id = auth.uid())
    AND message_id IN (
        SELECT m.message_id FROM messages m
        JOIN conversations c ON c.conversation_id = m.conversation_id
        WHERE c.user_id = (SELECT user_id FROM users WHERE auth_id = auth.uid())
    )
);

-- UPDATE/DELETE: not allowed (feedback is immutable)


-- ============================================
-- CONSULTATION ARTICLES — access via conversation ownership
-- ============================================

-- SELECT: see cited articles for own conversations
CREATE POLICY "consultation_articles_select_own"
ON public.consultation_articles
FOR SELECT
TO authenticated
USING (
    conversation_id IN (
        SELECT conversation_id FROM conversations
        WHERE user_id = (SELECT user_id FROM users WHERE auth_id = auth.uid())
    )
);

-- INSERT: record citations in own conversations
CREATE POLICY "consultation_articles_insert_own"
ON public.consultation_articles
FOR INSERT
TO authenticated
WITH CHECK (
    conversation_id IN (
        SELECT conversation_id FROM conversations
        WHERE user_id = (SELECT user_id FROM users WHERE auth_id = auth.uid())
    )
);

-- UPDATE/DELETE: not allowed (citation records are immutable)


-- ============================================
-- AUDIT LOGS — append-only
-- ============================================

-- INSERT: any authenticated user (actions are logged by the system)
CREATE POLICY "audit_logs_insert_authenticated"
ON public.audit_logs
FOR INSERT
TO authenticated
WITH CHECK (true);

-- SELECT: admin only (regular users cannot read audit logs)
-- Admin access uses service_role key which bypasses RLS
-- No SELECT policy for regular users

-- UPDATE: never allowed
-- DELETE: never allowed


-- ============================================
-- MODEL PRICING — read-only reference data
-- ============================================

-- SELECT: all authenticated users can read pricing data
CREATE POLICY "model_pricing_select_authenticated"
ON public.model_pricing
FOR SELECT
TO authenticated
USING (true);

-- INSERT/UPDATE/DELETE: admin only (via service_role key which bypasses RLS)
