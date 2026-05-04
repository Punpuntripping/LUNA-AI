-- Migration 022: Add 'legal_synthesis' to artifact_type_enum
-- Deep-search-v3 aggregator produces cited legal syntheses, not legal opinions.
-- Extending the enum rather than reusing 'legal_opinion' keeps artifact filtering
-- clean in analytics and frontend queries.

DO $$ BEGIN
    ALTER TYPE artifact_type_enum ADD VALUE IF NOT EXISTS 'legal_synthesis';
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;
