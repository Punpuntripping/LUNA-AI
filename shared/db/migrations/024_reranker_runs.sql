-- Migration 024: reranker_runs table.
-- One row per sub-query, per executor, per URA. Forensic layer: captures
-- PRE-merge state (per-SQ relevance nuance, drops) that the URA loses during
-- dedup.

CREATE TABLE IF NOT EXISTS reranker_runs (
    run_id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    ura_id              UUID NOT NULL REFERENCES retrieval_artifacts(ura_id) ON DELETE CASCADE,
    agent_family        TEXT NOT NULL,     -- 'reg_search' | 'compliance_search' | 'case_search'
    sub_query_index     INT NOT NULL,      -- 0-based GLOBAL index across all 3 executors
    sub_query_text      TEXT NOT NULL,
    sub_query_rationale TEXT NOT NULL DEFAULT '',
    kept_results        JSONB NOT NULL DEFAULT '[]'::jsonb,   -- [{ref_id, relevance, reasoning, source_type, title}]
    dropped_results     JSONB NOT NULL DEFAULT '[]'::jsonb,   -- [{ref_id, reasoning}] (optional for v1)
    sufficient          BOOLEAN NOT NULL DEFAULT false,
    summary_note        TEXT NOT NULL DEFAULT '',
    tokens_in           INT,
    tokens_out          INT,
    duration_ms         INT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS reranker_runs_ura_id_idx ON reranker_runs(ura_id);
CREATE INDEX IF NOT EXISTS reranker_runs_agent_family_idx ON reranker_runs(agent_family);
CREATE INDEX IF NOT EXISTS reranker_runs_created_at_idx ON reranker_runs(created_at DESC);

ALTER TABLE reranker_runs ENABLE ROW LEVEL SECURITY;

-- RLS enforced via JOIN to retrieval_artifacts.user_id.
DO $$ BEGIN
    CREATE POLICY reranker_runs_select ON reranker_runs FOR SELECT
        USING (ura_id IN (
            SELECT ura_id FROM retrieval_artifacts
            WHERE user_id = (SELECT user_id FROM users WHERE auth_id = auth.uid())
        ));
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

DO $$ BEGIN
    CREATE POLICY reranker_runs_insert ON reranker_runs FOR INSERT
        WITH CHECK (ura_id IN (
            SELECT ura_id FROM retrieval_artifacts
            WHERE user_id = (SELECT user_id FROM users WHERE auth_id = auth.uid())
        ));
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;
