-- Migration 018: Agent framework enum types
-- agent_family_enum: which agent family handles the request
DO $$ BEGIN
    CREATE TYPE agent_family_enum AS ENUM (
        'deep_search', 'simple_search', 'end_services', 'extraction', 'memory'
    );
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

-- artifact_type_enum: what kind of artifact an agent produces
DO $$ BEGIN
    CREATE TYPE artifact_type_enum AS ENUM (
        'report', 'contract', 'memo', 'summary', 'memory_file', 'legal_opinion'
    );
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;
