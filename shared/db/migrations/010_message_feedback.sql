-- 010_message_feedback.sql
-- User feedback on AI responses (thumbs up/down + optional comment)

CREATE TABLE IF NOT EXISTS public.message_feedback (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    message_id      UUID NOT NULL REFERENCES public.messages(message_id) ON DELETE CASCADE,
    user_id         UUID NOT NULL REFERENCES public.users(user_id) ON DELETE CASCADE,
    rating          feedback_rating_enum NOT NULL,
    comment         TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- One feedback per user per message
ALTER TABLE public.message_feedback
    ADD CONSTRAINT uq_user_message_feedback UNIQUE (message_id, user_id);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_feedback_message
    ON public.message_feedback (message_id);

CREATE INDEX IF NOT EXISTS idx_feedback_user
    ON public.message_feedback (user_id);

CREATE INDEX IF NOT EXISTS idx_feedback_rating
    ON public.message_feedback (rating);

CREATE INDEX IF NOT EXISTS idx_feedback_created
    ON public.message_feedback (created_at DESC);

COMMENT ON TABLE public.message_feedback IS 'User ratings on AI responses. Used for quality monitoring and fine-tuning.';
