-- 004_lawyer_cases.sql
-- Cases managed by lawyers

CREATE TABLE IF NOT EXISTS public.lawyer_cases (
    case_id             UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    lawyer_user_id      UUID NOT NULL REFERENCES public.users(user_id) ON DELETE CASCADE,

    -- Case identification
    case_name           VARCHAR(500) NOT NULL,
    case_number         VARCHAR(100),
    case_type           case_type_enum NOT NULL DEFAULT 'عام',

    -- Status & priority
    status              case_status_enum NOT NULL DEFAULT 'active',
    priority            case_priority_enum NOT NULL DEFAULT 'medium',

    -- Content
    description         TEXT,
    parties             JSONB NOT NULL DEFAULT '{}'::jsonb,
    court_name          VARCHAR(255),
    next_hearing_date   DATE,

    -- Vector embedding for semantic search
    embedding           vector(1536),

    -- Timestamps
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    deleted_at          TIMESTAMPTZ              -- Soft delete
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_cases_lawyer
    ON public.lawyer_cases (lawyer_user_id)
    WHERE deleted_at IS NULL;

CREATE INDEX IF NOT EXISTS idx_cases_status
    ON public.lawyer_cases (status)
    WHERE deleted_at IS NULL;

CREATE INDEX IF NOT EXISTS idx_cases_type
    ON public.lawyer_cases (case_type)
    WHERE deleted_at IS NULL;

CREATE INDEX IF NOT EXISTS idx_cases_priority
    ON public.lawyer_cases (priority)
    WHERE deleted_at IS NULL AND status = 'active';

CREATE INDEX IF NOT EXISTS idx_cases_case_number
    ON public.lawyer_cases (case_number)
    WHERE case_number IS NOT NULL AND deleted_at IS NULL;

CREATE INDEX IF NOT EXISTS idx_cases_created
    ON public.lawyer_cases (created_at DESC)
    WHERE deleted_at IS NULL;

-- HNSW index for vector similarity search (cosine distance — best for normalized OpenAI embeddings)
CREATE INDEX IF NOT EXISTS idx_cases_embedding
    ON public.lawyer_cases
    USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);

-- GIN index on JSONB parties for fast lookups
CREATE INDEX IF NOT EXISTS idx_cases_parties
    ON public.lawyer_cases USING gin (parties);

COMMENT ON TABLE public.lawyer_cases IS 'Legal cases managed by lawyers. Supports semantic search via embeddings.';
COMMENT ON COLUMN public.lawyer_cases.parties IS 'Structured JSON: plaintiff, defendant, judge, witnesses, lawyers.';
COMMENT ON COLUMN public.lawyer_cases.embedding IS 'OpenAI text-embedding-3-small vector (1536 dims) for semantic search.';
COMMENT ON COLUMN public.lawyer_cases.deleted_at IS 'Soft delete timestamp. NULL means active.';
