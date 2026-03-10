-- 002_enums.sql
-- All custom enum types used across the schema
-- 13 enums total (12 app enums + subscription_status_enum)

-- Case type (Saudi legal system categories)
DO $$ BEGIN
    CREATE TYPE case_type_enum AS ENUM (
        'عقاري',           -- Real Estate
        'تجاري',           -- Commercial
        'عمالي',           -- Labor
        'جنائي',           -- Criminal
        'أحوال_شخصية',     -- Personal Status / Family
        'إداري',           -- Administrative
        'تنفيذ',           -- Enforcement
        'عام'              -- General
    );
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

-- Case status
DO $$ BEGIN
    CREATE TYPE case_status_enum AS ENUM (
        'active',
        'closed',
        'archived'
    );
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

-- Case priority
DO $$ BEGIN
    CREATE TYPE case_priority_enum AS ENUM (
        'high',
        'medium',
        'low'
    );
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

-- Memory type
DO $$ BEGIN
    CREATE TYPE memory_type_enum AS ENUM (
        'fact',
        'document_reference',
        'strategy',
        'deadline',
        'party_info'
    );
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

-- Document type
DO $$ BEGIN
    CREATE TYPE document_type_enum AS ENUM (
        'pdf',
        'image',
        'docx',
        'other'
    );
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

-- Document extraction status
DO $$ BEGIN
    CREATE TYPE extraction_status_enum AS ENUM (
        'pending',
        'processing',
        'completed',
        'failed'
    );
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

-- Message role
DO $$ BEGIN
    CREATE TYPE message_role_enum AS ENUM (
        'user',
        'assistant',
        'system'
    );
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

-- Message finish reason
DO $$ BEGIN
    CREATE TYPE finish_reason_enum AS ENUM (
        'stop',
        'length',
        'error'
    );
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

-- Feedback rating
DO $$ BEGIN
    CREATE TYPE feedback_rating_enum AS ENUM (
        'thumbs_up',
        'thumbs_down'
    );
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

-- Audit action
DO $$ BEGIN
    CREATE TYPE audit_action_enum AS ENUM (
        'create',
        'read',
        'update',
        'delete',
        'login',
        'logout',
        'upload',
        'download'
    );
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

-- Subscription tier
DO $$ BEGIN
    CREATE TYPE subscription_tier_enum AS ENUM (
        'free',
        'basic',
        'professional',
        'enterprise'
    );
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

-- Attachment type
DO $$ BEGIN
    CREATE TYPE attachment_type_enum AS ENUM (
        'image',
        'pdf',
        'file'
    );
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

-- Subscription status
DO $$ BEGIN
    CREATE TYPE subscription_status_enum AS ENUM (
        'active',
        'past_due',
        'cancelled',
        'expired',
        'trial'
    );
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;
