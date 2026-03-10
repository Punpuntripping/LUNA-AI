-- 005_case_memories.sql
-- Per-case extracted facts, parties, deadlines, and strategy notes
-- NOTE: source_conversation_id and source_document_id FK constraints are added
-- after the referenced tables are created (conversations in 007, case_documents in 006).
-- We create the table here with the columns but add FKs later via ALTER TABLE.

CREATE TABLE IF NOT EXISTS public.case_memories (
    memory_id               UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    case_id                 UUID NOT NULL REFERENCES public.lawyer_cases(case_id) ON DELETE CASCADE,

    -- Memory classification
    memory_type             memory_type_enum NOT NULL,

    -- Content
    content_ar              TEXT NOT NULL,
    content_en              TEXT,

    -- Quality indicators
    confidence_score        FLOAT CHECK (confidence_score >= 0 AND confidence_score <= 1),

    -- Source tracking (FK constraints added after referenced tables are created)
    source_conversation_id  UUID,
    source_document_id      UUID,

    -- Vector embedding for semantic memory retrieval
    embedding               vector(1536),

    -- Timestamps
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    deleted_at              TIMESTAMPTZ              -- Soft delete
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_memories_case
    ON public.case_memories (case_id)
    WHERE deleted_at IS NULL;

CREATE INDEX IF NOT EXISTS idx_memories_type
    ON public.case_memories (case_id, memory_type)
    WHERE deleted_at IS NULL;

CREATE INDEX IF NOT EXISTS idx_memories_confidence
    ON public.case_memories (confidence_score DESC)
    WHERE deleted_at IS NULL;

CREATE INDEX IF NOT EXISTS idx_memories_source_convo
    ON public.case_memories (source_conversation_id)
    WHERE source_conversation_id IS NOT NULL AND deleted_at IS NULL;

CREATE INDEX IF NOT EXISTS idx_memories_source_doc
    ON public.case_memories (source_document_id)
    WHERE source_document_id IS NOT NULL AND deleted_at IS NULL;

-- HNSW index for semantic memory search
CREATE INDEX IF NOT EXISTS idx_memories_embedding
    ON public.case_memories
    USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);

COMMENT ON TABLE public.case_memories IS 'AI-extracted or user-added memories/facts per case. Supports semantic search.';
COMMENT ON COLUMN public.case_memories.confidence_score IS 'AI confidence in this memory (0.0 to 1.0). NULL if user-added.';
COMMENT ON COLUMN public.case_memories.deleted_at IS 'Soft delete timestamp. NULL means active.';
