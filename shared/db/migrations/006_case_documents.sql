-- 006_case_documents.sql
-- Documents uploaded to cases (PDFs, images, contracts, etc.)
-- NOTE: conversation_id FK is added after conversations table is created in 007.

CREATE TABLE IF NOT EXISTS public.case_documents (
    document_id         UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    case_id             UUID NOT NULL REFERENCES public.lawyer_cases(case_id) ON DELETE CASCADE,

    -- Upload context (FK added after conversations table exists)
    conversation_id     UUID,

    -- File metadata
    document_name       VARCHAR(500) NOT NULL,
    document_type       document_type_enum,
    storage_path        TEXT NOT NULL,
    file_size_bytes     BIGINT,
    mime_type           VARCHAR(100),

    -- Extracted content
    content_text        TEXT,
    extracted_data      JSONB DEFAULT '{}'::jsonb,
    extraction_status   extraction_status_enum NOT NULL DEFAULT 'pending',
    extraction_error    TEXT,

    -- Vector embedding for semantic search
    embedding           vector(1536),

    -- Timestamps
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    deleted_at          TIMESTAMPTZ              -- Soft delete
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_documents_case
    ON public.case_documents (case_id)
    WHERE deleted_at IS NULL;

CREATE INDEX IF NOT EXISTS idx_documents_extraction
    ON public.case_documents (extraction_status)
    WHERE extraction_status IN ('pending', 'processing') AND deleted_at IS NULL;

CREATE INDEX IF NOT EXISTS idx_documents_type
    ON public.case_documents (document_type)
    WHERE deleted_at IS NULL;

CREATE INDEX IF NOT EXISTS idx_documents_created
    ON public.case_documents (created_at DESC)
    WHERE deleted_at IS NULL;

-- HNSW index for semantic document search
CREATE INDEX IF NOT EXISTS idx_documents_embedding
    ON public.case_documents
    USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);

-- GIN index on extracted_data for JSON queries
CREATE INDEX IF NOT EXISTS idx_documents_extracted
    ON public.case_documents USING gin (extracted_data);

COMMENT ON TABLE public.case_documents IS 'Documents uploaded to cases. Supports OCR extraction and semantic search.';
COMMENT ON COLUMN public.case_documents.storage_path IS 'Path in Supabase Storage: cases/{case_id}/docs/{filename}';
COMMENT ON COLUMN public.case_documents.extracted_data IS 'Structured data extracted by AI (parties, dates, amounts, etc.)';
COMMENT ON COLUMN public.case_documents.deleted_at IS 'Soft delete timestamp. NULL means active.';
