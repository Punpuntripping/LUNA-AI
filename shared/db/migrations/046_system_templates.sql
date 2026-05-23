-- 046_system_templates.sql
-- System-curated template library for the writer agent.
-- Minimal v1 shape: a small set of platform-seeded templates that the writer
-- can retrieve by semantic similarity over `summary`. User-authored templates
-- are deferred to v2 (Rayhan) and will land in a separate `user_templates` table.
--
-- Ownership model:
--   * No user_id — these are global, system-owned rows.
--   * Reads: any authenticated user (RLS allows SELECT).
--   * Writes: out-of-band only (ingestion scripts using service_role bypass RLS).
--
-- Embeddings:
--   * vector(1024) to match the project's default ingestion stack
--     (Alibaba DashScope text-embedding-v4 — see agentic_for_ministry/ingestion/embedding.py).
--   * Ingestion / backfill is intentionally NOT part of this migration.

-- ============================================
-- 1. ENUM — template type (Saudi-lawyer vocabulary)
-- ============================================
DO $$ BEGIN
    CREATE TYPE template_type_enum AS ENUM (
        'عقد',                -- Contract
        'مذكرة',              -- Legal memo / pleading brief
        'رأي_قانوني',         -- Legal opinion
        'لائحة_دعوى',         -- Statement of claim
        'رد_على_دعوى',        -- Response to claim
        'شكوى',               -- Complaint
        'إنذار',              -- Formal warning notice
        'تظلم',               -- Grievance
        'استشارة',            -- Consultation memo
        'وكالة',              -- Power of attorney
        'إقرار',              -- Acknowledgment / declaration
        'تنازل',              -- Waiver
        'اتفاقية',            -- Agreement / MoU
        'صيغة_قانونية'        -- Legal formula / boilerplate
    );
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;


-- ============================================
-- 2. TABLE
-- ============================================
CREATE TABLE IF NOT EXISTS public.system_templates (
    template_id        UUID PRIMARY KEY DEFAULT uuid_generate_v4(),

    -- Business fields (the user-specified shape: title, content, type, summary)
    -- `summary` doubles as `context` — the field embedded for similarity search.
    title              TEXT                NOT NULL,
    content            TEXT                NOT NULL,
    type               template_type_enum  NOT NULL,
    summary            TEXT                NOT NULL,

    -- Embedding over `summary` (Alibaba text-embedding-v4 → 1024-d).
    -- Nullable so rows can be inserted before ingestion fills it in.
    summary_embedding  vector(1024),

    -- Timestamps
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    deleted_at         TIMESTAMPTZ                          -- Soft delete
);


-- ============================================
-- 3. INDEXES
-- ============================================

-- Filter by type (the "maybe" pre-filter for similarity search).
CREATE INDEX IF NOT EXISTS idx_system_templates_type
    ON public.system_templates (type)
    WHERE deleted_at IS NULL;

-- HNSW cosine index for semantic retrieval over `summary`.
-- Partial index on active rows only.
CREATE INDEX IF NOT EXISTS idx_system_templates_summary_embedding
    ON public.system_templates
    USING hnsw (summary_embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64)
    WHERE deleted_at IS NULL;


-- ============================================
-- 4. updated_at TRIGGER
-- ============================================
CREATE TRIGGER trg_system_templates_updated_at
    BEFORE UPDATE ON public.system_templates
    FOR EACH ROW
    EXECUTE FUNCTION public.update_updated_at();


-- ============================================
-- 5. ROW LEVEL SECURITY
-- Global-read; writes only via service_role (which bypasses RLS).
-- ============================================
ALTER TABLE public.system_templates ENABLE ROW LEVEL SECURITY;

-- SELECT: any authenticated user can read active templates.
CREATE POLICY "system_templates_select_all"
ON public.system_templates
FOR SELECT
TO authenticated
USING (deleted_at IS NULL);

-- No INSERT / UPDATE / DELETE policies for authenticated users —
-- ingestion runs with service_role which bypasses RLS entirely.


-- ============================================
-- 6. COMMENTS
-- ============================================
COMMENT ON TABLE public.system_templates IS
    'System-curated legal templates retrievable by semantic similarity. v1: system-owned, no user authorship (deferred to v2/Rayhan).';

COMMENT ON COLUMN public.system_templates.summary IS
    'Short description of when/how this template applies. Doubles as the "context" field — this is what gets embedded for similarity search.';

COMMENT ON COLUMN public.system_templates.summary_embedding IS
    'vector(1024) embedding of `summary` produced by Alibaba text-embedding-v4. Nullable — populated out-of-band by ingestion scripts using service_role.';

COMMENT ON COLUMN public.system_templates.deleted_at IS
    'Soft delete timestamp. NULL means active.';
