-- Migration 027: Add 'writing' to agent_family_enum.
--
-- Cut-1 step 5 added task_type='writing' to OpenTask + the writer dispatch
-- path in agents/orchestrator.py. The orchestrator persists task_state with
-- agent_family=task_type, so writing tasks try to insert agent_family='writing'
-- into task_state, but the enum (defined in 018_enums_agent.sql) only has
-- {deep_search, simple_search, end_services, extraction, memory}.
--
-- This migration adds the missing enum value. PostgreSQL ADD VALUE is
-- idempotent via IF NOT EXISTS.

ALTER TYPE agent_family_enum ADD VALUE IF NOT EXISTS 'writing';
