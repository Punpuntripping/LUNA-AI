# Wave 6A — Database Migrations + Shared Layer

> **Parent:** `wave_6_integration_overview.md`
> **Dependencies:** None (first sub-wave)
> **Build Agents:** @sql-migration, @shared-foundation
> **Quality Agents:** @rls-auditor
> **MCP:** Supabase (`apply_migration`, `list_tables`, `execute_sql`, `list_migrations`)

---

## Pre-Flight Checks

Before starting, verify current state:

```
1. mcp__supabase__list_migrations → confirm migrations 001-017 applied
2. mcp__supabase__list_tables → confirm existing tables (users, lawyer_cases, etc.)
3. Python import: from shared.types import CaseType, MessageRole → works
4. Python import: from shared.config import Settings → works
```

---

## Stage 1: SQL Migrations (3 files)

**Agent:** @sql-migration
**MCP:** `mcp__supabase__apply_migration` for each, `mcp__supabase__execute_sql` to verify

### 1.1 Migration 018: `shared/db/migrations/018_enums_agent.sql`

New enum types for the agent framework.

```sql
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
```

**Validation:**
```sql
SELECT typname, enumlabel FROM pg_enum
JOIN pg_type ON pg_enum.enumtypid = pg_type.oid
WHERE typname IN ('agent_family_enum', 'artifact_type_enum')
ORDER BY typname, enumsortorder;
-- Expect: 5 agent_family values + 6 artifact_type values = 11 rows
```

### 1.2 Migration 019: `shared/db/migrations/019_artifacts.sql`

Artifacts table — stores agent-generated documents (reports, contracts, memos, etc.).

**Table spec:**
```
artifacts
├── artifact_id     UUID PK DEFAULT uuid_generate_v4()
├── user_id         UUID NOT NULL FK → users(user_id)
├── conversation_id UUID FK → conversations(conversation_id) [NULLABLE]
├── case_id         UUID FK → lawyer_cases(case_id) [NULLABLE]
├── agent_family    agent_family_enum NOT NULL
├── artifact_type   artifact_type_enum NOT NULL
├── title           TEXT NOT NULL
├── content_md      TEXT NOT NULL DEFAULT ''
├── is_editable     BOOLEAN NOT NULL DEFAULT false
├── metadata        JSONB DEFAULT '{}'
├── created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
├── updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
└── deleted_at      TIMESTAMPTZ [NULLABLE, soft delete]
```

**Indexes:**
```sql
CREATE INDEX idx_artifacts_user_id ON artifacts(user_id);
CREATE INDEX idx_artifacts_conversation_id ON artifacts(conversation_id) WHERE conversation_id IS NOT NULL;
CREATE INDEX idx_artifacts_case_id ON artifacts(case_id) WHERE case_id IS NOT NULL;
CREATE INDEX idx_artifacts_agent_family ON artifacts(agent_family);
CREATE INDEX idx_artifacts_not_deleted ON artifacts(artifact_id) WHERE deleted_at IS NULL;
```

**RLS policies (CRITICAL):**
```sql
ALTER TABLE artifacts ENABLE ROW LEVEL SECURITY;

-- User owns artifact via user_id
CREATE POLICY artifacts_select ON artifacts FOR SELECT
    USING (user_id = (SELECT u.user_id FROM users u WHERE u.auth_id = (SELECT auth.uid())));

CREATE POLICY artifacts_insert ON artifacts FOR INSERT
    WITH CHECK (user_id = (SELECT u.user_id FROM users u WHERE u.auth_id = (SELECT auth.uid())));

CREATE POLICY artifacts_update ON artifacts FOR UPDATE
    USING (user_id = (SELECT u.user_id FROM users u WHERE u.auth_id = (SELECT auth.uid())));

CREATE POLICY artifacts_delete ON artifacts FOR DELETE
    USING (user_id = (SELECT u.user_id FROM users u WHERE u.auth_id = (SELECT auth.uid())));
```

**Trigger:** Reuse existing `update_updated_at()` trigger function:
```sql
CREATE TRIGGER update_artifacts_updated_at
    BEFORE UPDATE ON artifacts
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();
```

**Validation:**
```sql
-- Table exists
SELECT column_name, data_type, is_nullable FROM information_schema.columns
WHERE table_name = 'artifacts' ORDER BY ordinal_position;

-- RLS enabled
SELECT relname, relrowsecurity FROM pg_class WHERE relname = 'artifacts';
-- Expect: relrowsecurity = true

-- Policies exist
SELECT policyname, cmd FROM pg_policies WHERE tablename = 'artifacts';
-- Expect: 4 policies (SELECT, INSERT, UPDATE, DELETE)
```

### 1.3 Migration 020: `shared/db/migrations/020_user_preferences_templates.sql`

Two tables for user customization.

**Table: user_preferences**
```
user_preferences
├── id              UUID PK DEFAULT uuid_generate_v4()
├── user_id         UUID NOT NULL UNIQUE FK → users(user_id)
├── preferences     JSONB NOT NULL DEFAULT '{}'
├── created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
└── updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
```

**Table: user_templates**
```
user_templates
├── template_id     UUID PK DEFAULT uuid_generate_v4()
├── user_id         UUID NOT NULL FK → users(user_id)
├── title           TEXT NOT NULL
├── description     TEXT DEFAULT ''
├── prompt_template TEXT NOT NULL
├── agent_family    agent_family_enum NOT NULL DEFAULT 'end_services'
├── is_active       BOOLEAN NOT NULL DEFAULT true
├── created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
├── updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
└── deleted_at      TIMESTAMPTZ [NULLABLE, soft delete]
```

**RLS policies:** Same pattern as artifacts — user_id ownership.

**Validation:**
```sql
-- Both tables exist
SELECT table_name FROM information_schema.tables
WHERE table_name IN ('user_preferences', 'user_templates');

-- RLS enabled on both
SELECT relname, relrowsecurity FROM pg_class
WHERE relname IN ('user_preferences', 'user_templates');

-- Policies exist
SELECT tablename, policyname, cmd FROM pg_policies
WHERE tablename IN ('user_preferences', 'user_templates');
```

---

## Stage 2: Shared Layer Updates (2 files)

**Agent:** @shared-foundation
**Dependencies:** Stage 1 (enum types must exist in DB)

### 2.1 `shared/types.py` — ADD enums + AgentContext

Add to existing file (DO NOT remove existing types):

```python
class AgentFamily(str, Enum):
    DEEP_SEARCH = "deep_search"
    SIMPLE_SEARCH = "simple_search"
    END_SERVICES = "end_services"
    EXTRACTION = "extraction"
    MEMORY = "memory"

class ArtifactType(str, Enum):
    REPORT = "report"
    CONTRACT = "contract"
    MEMO = "memo"
    SUMMARY = "summary"
    MEMORY_FILE = "memory_file"
    LEGAL_OPINION = "legal_opinion"

@dataclass
class AgentContext:
    question: str
    conversation_id: str
    user_id: str
    case_id: Optional[str] = None
    memory_md: Optional[str] = None
    conversation_history: list[ChatMessage] = field(default_factory=list)
    case_metadata: Optional[dict] = None
    user_preferences: Optional[dict] = None
    user_templates: Optional[list[dict]] = None
    document_summaries: Optional[list[dict]] = None
    modifiers: list[str] = field(default_factory=list)
```

### 2.2 `shared/config.py` — ADD agent config vars

Add to existing `Settings` class:

```python
AGENT_AUTO_ROUTE_MODEL: str = "anthropic/claude-haiku-4-5-20251001"
AGENT_DEFAULT_MODEL: str = "anthropic/claude-sonnet-4"
```

**Validation:**
```python
# Import check
from shared.types import AgentFamily, ArtifactType, AgentContext
from shared.types import ChatMessage  # existing — verify not broken

# Enum values
assert AgentFamily.DEEP_SEARCH.value == "deep_search"
assert ArtifactType.REPORT.value == "report"

# AgentContext creation
ctx = AgentContext(question="test", conversation_id="123", user_id="456")
assert ctx.modifiers == []

# Config
from shared.config import settings
assert hasattr(settings, 'AGENT_AUTO_ROUTE_MODEL')
```

---

## Validation Gate 6A

**All must pass before proceeding to Wave 6B:**

| # | Check | Agent/Tool | Pass Criteria |
|---|-------|------------|---------------|
| 1 | Migrations applied | `mcp__supabase__list_migrations` | 018, 019, 020 appear in list |
| 2 | Tables created | `mcp__supabase__list_tables` | artifacts, user_preferences, user_templates present |
| 3 | Enum types exist | `mcp__supabase__execute_sql` | 11 enum values returned |
| 4 | RLS enabled (3 tables) | @rls-auditor via `mcp__supabase__execute_sql` | relrowsecurity = true for all 3 |
| 5 | RLS policies correct | @rls-auditor via `mcp__supabase__execute_sql` | 4 policies per table (SELECT, INSERT, UPDATE, DELETE) |
| 6 | Python imports | Bash: `python -c "from shared.types import ..."` | No import errors |
| 7 | Existing types intact | Bash: `python -c "from shared.types import CaseType, ..."` | No regressions |
| 8 | Config loads | Bash: `python -c "from shared.config import settings"` | No errors |

**@rls-auditor specific checks:**
- Cross-user isolation: User A cannot SELECT artifacts owned by User B
- INSERT requires valid user_id matching auth.uid()
- Soft-deleted artifacts (deleted_at IS NOT NULL) are still visible via RLS (filtering is app-level)

---

## File Manifest

| File | Action | Agent |
|------|--------|-------|
| `shared/db/migrations/018_enums_agent.sql` | NEW | @sql-migration |
| `shared/db/migrations/019_artifacts.sql` | NEW | @sql-migration |
| `shared/db/migrations/020_user_preferences_templates.sql` | NEW | @sql-migration |
| `shared/types.py` | MODIFY | @shared-foundation |
| `shared/config.py` | MODIFY | @shared-foundation |

**Total: 3 new + 2 modified = 5 files**
