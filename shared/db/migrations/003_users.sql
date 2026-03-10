-- 003_users.sql
-- Users table — lawyer accounts linked to Supabase Auth (auth.users)

CREATE TABLE IF NOT EXISTS public.users (
    user_id               UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    auth_id               UUID UNIQUE NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    email                 VARCHAR(255) UNIQUE NOT NULL,
    full_name_ar          VARCHAR(255) NOT NULL,
    full_name_en          VARCHAR(255),
    phone                 VARCHAR(20),

    -- Subscription
    subscription_tier     subscription_tier_enum NOT NULL DEFAULT 'free',
    subscription_status   subscription_status_enum NOT NULL DEFAULT 'active',
    subscription_expires_at TIMESTAMPTZ,

    -- Profile
    avatar_url            TEXT,
    settings              JSONB NOT NULL DEFAULT '{}'::jsonb,

    -- Timestamps
    created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at            TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Indexes (auth_id and email already have UNIQUE indexes from constraints)
CREATE INDEX IF NOT EXISTS idx_users_subscription_tier
    ON public.users (subscription_tier);

COMMENT ON TABLE public.users IS 'Lawyer user profiles. Linked to Supabase Auth via auth_id.';
COMMENT ON COLUMN public.users.auth_id IS 'References auth.users(id) from Supabase Auth.';
COMMENT ON COLUMN public.users.settings IS 'User preferences as JSON (language, theme, notifications, etc.)';
