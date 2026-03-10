-- 009_message_attachments.sql
-- Join table linking messages to case documents (files attached in chat)

CREATE TABLE IF NOT EXISTS public.message_attachments (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    message_id          UUID NOT NULL REFERENCES public.messages(message_id) ON DELETE CASCADE,
    document_id         UUID NOT NULL REFERENCES public.case_documents(document_id) ON DELETE CASCADE,
    attachment_type     attachment_type_enum,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_attachments_message
    ON public.message_attachments (message_id);

CREATE INDEX IF NOT EXISTS idx_attachments_document
    ON public.message_attachments (document_id);

-- Unique constraint: same document can't be attached to same message twice
ALTER TABLE public.message_attachments
    ADD CONSTRAINT uq_message_document UNIQUE (message_id, document_id);

COMMENT ON TABLE public.message_attachments IS 'Junction table linking messages to uploaded case documents.';
