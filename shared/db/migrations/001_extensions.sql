-- 001_extensions.sql
-- Enable required PostgreSQL extensions
-- Run FIRST before any other migration

-- Vector similarity search (pgvector for embeddings)
CREATE EXTENSION IF NOT EXISTS vector;

-- UUID generation
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- Cryptographic functions
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- Trigram similarity for fuzzy Arabic search
CREATE EXTENSION IF NOT EXISTS pg_trgm;
