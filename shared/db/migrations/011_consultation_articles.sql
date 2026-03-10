-- 011_consultation_articles.sql
-- Bridge table: tracks which legal articles were cited in conversations
-- Links app DB conversations to legal DB articles

CREATE TABLE IF NOT EXISTS public.consultation_articles (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    conversation_id     UUID NOT NULL REFERENCES public.conversations(conversation_id) ON DELETE CASCADE,
    article_id          UUID NOT NULL,              -- References articles in legal DB (cross-schema)
    relevance_score     FLOAT,
    cited_in_message_id UUID REFERENCES public.messages(message_id) ON DELETE SET NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_consultation_articles_convo
    ON public.consultation_articles (conversation_id);

CREATE INDEX IF NOT EXISTS idx_consultation_articles_article
    ON public.consultation_articles (article_id);

CREATE INDEX IF NOT EXISTS idx_consultation_articles_relevance
    ON public.consultation_articles (relevance_score DESC);

-- Unique: don't cite same article twice in same conversation
ALTER TABLE public.consultation_articles
    ADD CONSTRAINT uq_convo_article UNIQUE (conversation_id, article_id);

COMMENT ON TABLE public.consultation_articles IS 'Which legal articles/regulations were cited in each conversation.';
COMMENT ON COLUMN public.consultation_articles.article_id IS 'References articles table in legal DB. Cross-schema reference (not enforced by FK).';
