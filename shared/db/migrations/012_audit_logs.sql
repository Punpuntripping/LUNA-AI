-- 012_audit_logs.sql
-- APPEND-ONLY audit trail for compliance and security
-- NEVER delete from this table

CREATE TABLE IF NOT EXISTS public.audit_logs (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id         UUID REFERENCES public.users(user_id) ON DELETE SET NULL,
    action          audit_action_enum NOT NULL,
    resource_type   VARCHAR(100) NOT NULL,
    resource_id     UUID,
    metadata        JSONB NOT NULL DEFAULT '{}'::jsonb,
    ip_address      INET,
    user_agent      TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- APPEND-ONLY enforcement: revoke DELETE and UPDATE from non-admin roles
REVOKE DELETE, UPDATE ON public.audit_logs FROM anon, authenticated;

-- Indexes (optimized for querying audit history)
CREATE INDEX IF NOT EXISTS idx_audit_user
    ON public.audit_logs (user_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_audit_action
    ON public.audit_logs (action, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_audit_resource
    ON public.audit_logs (resource_type, resource_id);

CREATE INDEX IF NOT EXISTS idx_audit_created
    ON public.audit_logs (created_at DESC);

CREATE INDEX IF NOT EXISTS idx_audit_ip
    ON public.audit_logs (ip_address)
    WHERE ip_address IS NOT NULL;

-- GIN index on metadata for JSON queries
CREATE INDEX IF NOT EXISTS idx_audit_metadata
    ON public.audit_logs USING gin (metadata);

COMMENT ON TABLE public.audit_logs IS 'APPEND-ONLY audit trail. Never delete. Used for compliance and security monitoring.';
COMMENT ON COLUMN public.audit_logs.metadata IS 'Flexible JSON: old_values, new_values, request details, etc.';
