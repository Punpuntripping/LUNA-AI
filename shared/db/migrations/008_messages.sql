-- 008_messages.sql
-- Individual chat messages with branching support via parent_message_id

CREATE TABLE IF NOT EXISTS public.messages (
    message_id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    conversation_id     UUID NOT NULL REFERENCES public.conversations(conversation_id) ON DELETE CASCADE,

    -- For regeneration tree: links to the message this one replaces/branches from
    parent_message_id   UUID REFERENCES public.messages(message_id) ON DELETE SET NULL,

    -- Content
    role                message_role_enum NOT NULL,
    content             TEXT NOT NULL,

    -- LLM metadata (only for assistant messages)
    model               VARCHAR(100),
    prompt_tokens       INTEGER,
    completion_tokens   INTEGER,
    cost                DECIMAL(10,6),
    finish_reason       finish_reason_enum,

    -- Additional context
    metadata            JSONB NOT NULL DEFAULT '{}'::jsonb,

    -- Timestamp (no updated_at — messages are immutable once created)
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- NOTE: No deleted_at — messages are deleted via CASCADE when conversation is deleted.
-- Individual message deletion is not supported (preserves conversation integrity).

-- Indexes
CREATE INDEX IF NOT EXISTS idx_messages_conversation
    ON public.messages (conversation_id, created_at ASC);

CREATE INDEX IF NOT EXISTS idx_messages_parent
    ON public.messages (parent_message_id)
    WHERE parent_message_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_messages_created
    ON public.messages (created_at DESC);

COMMENT ON TABLE public.messages IS 'Individual chat messages. Cascade-deleted with conversation.';
COMMENT ON COLUMN public.messages.parent_message_id IS 'For regeneration: points to the message this replaces. Self-referencing FK.';
COMMENT ON COLUMN public.messages.cost IS 'USD cost of LLM generation. NULL for user messages.';
