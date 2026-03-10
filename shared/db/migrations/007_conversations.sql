-- 007_conversations.sql
-- Chat conversations — either general (no case) or within a specific case
-- After this table is created, we also add deferred FK constraints
-- on case_documents and case_memories that reference conversations.

CREATE TABLE IF NOT EXISTS public.conversations (
    conversation_id     UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id             UUID NOT NULL REFERENCES public.users(user_id) ON DELETE CASCADE,
    case_id             UUID REFERENCES public.lawyer_cases(case_id) ON DELETE SET NULL,

    -- Content
    title_ar            VARCHAR(500),
    title_en            VARCHAR(500),
    message_count       INTEGER NOT NULL DEFAULT 0,
    model               VARCHAR(100),

    -- Vector embedding for semantic search
    embedding           vector(1536),

    -- Session tracking
    ended_at            TIMESTAMPTZ,

    -- Timestamps
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    deleted_at          TIMESTAMPTZ              -- Soft delete
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_conversations_user
    ON public.conversations (user_id)
    WHERE deleted_at IS NULL;

CREATE INDEX IF NOT EXISTS idx_conversations_case
    ON public.conversations (case_id)
    WHERE case_id IS NOT NULL AND deleted_at IS NULL;

CREATE INDEX IF NOT EXISTS idx_conversations_created
    ON public.conversations (created_at DESC)
    WHERE deleted_at IS NULL;

-- HNSW index for semantic search over conversations
CREATE INDEX IF NOT EXISTS idx_conversations_embedding
    ON public.conversations
    USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);

-- Now add the deferred FK constraints from earlier tables:

-- case_documents.conversation_id -> conversations.conversation_id
ALTER TABLE public.case_documents
    ADD CONSTRAINT fk_case_documents_conversation
    FOREIGN KEY (conversation_id)
    REFERENCES public.conversations(conversation_id)
    ON DELETE SET NULL;

-- case_memories.source_conversation_id -> conversations.conversation_id
ALTER TABLE public.case_memories
    ADD CONSTRAINT fk_case_memories_source_conversation
    FOREIGN KEY (source_conversation_id)
    REFERENCES public.conversations(conversation_id)
    ON DELETE SET NULL;

-- case_memories.source_document_id -> case_documents.document_id
ALTER TABLE public.case_memories
    ADD CONSTRAINT fk_case_memories_source_document
    FOREIGN KEY (source_document_id)
    REFERENCES public.case_documents(document_id)
    ON DELETE SET NULL;

-- Add conversation_id index on case_documents now that FK exists
CREATE INDEX IF NOT EXISTS idx_documents_conversation
    ON public.case_documents (conversation_id)
    WHERE conversation_id IS NOT NULL AND deleted_at IS NULL;

COMMENT ON TABLE public.conversations IS 'Chat conversations. Can be general (case_id NULL) or case-specific.';
COMMENT ON COLUMN public.conversations.model IS 'Primary LLM model identifier used in this conversation.';
COMMENT ON COLUMN public.conversations.deleted_at IS 'Soft delete timestamp. NULL means active.';
