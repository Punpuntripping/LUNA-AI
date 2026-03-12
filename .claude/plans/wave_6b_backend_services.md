# Wave 6B — Backend Services + APIs (Artifacts, Preferences, Templates)

> **Parent:** `wave_6_integration_overview.md`
> **Dependencies:** Wave 6A (tables + types must exist)
> **Build Agent:** @fastapi-backend
> **Quality Agents:** @integration-lead
> **MCP:** Supabase (`execute_sql` for testing queries)

---

## Pre-Flight Checks

```
1. Verify Gate 6A passed (artifacts, user_preferences, user_templates tables exist)
2. Verify shared types available: from shared.types import AgentFamily, ArtifactType
3. Verify existing backend imports: from backend.app.main import app
4. Read existing models/requests.py and models/responses.py to understand patterns
```

---

## Stage 1: Artifact Service + API (4 new files)

### 1.1 `backend/app/services/artifact_service.py` (NEW)

**Pattern:** Follow existing `case_service.py` — sync Supabase client, ownership checks, Arabic error messages, soft deletes.

```python
# Functions to implement:

def create_artifact(
    supabase, user_id, conversation_id, case_id,
    agent_family, artifact_type, title, content_md, is_editable=False, metadata=None
) -> dict:
    """Create a new artifact. Called by agents during execution."""

def list_artifacts_by_conversation(supabase, auth_id, conversation_id) -> list[dict]:
    """List artifacts for a conversation. Ownership verified via conversation→user chain."""

def list_artifacts_by_case(supabase, auth_id, case_id) -> list[dict]:
    """List artifacts for a case. Ownership verified via case→user chain."""

def get_artifact(supabase, auth_id, artifact_id) -> dict:
    """Get single artifact. Returns 404 if not found or not owned."""

def update_artifact(supabase, auth_id, artifact_id, content_md=None, title=None) -> dict:
    """Update artifact content/title. Only allowed if is_editable=True."""

def delete_artifact(supabase, auth_id, artifact_id) -> None:
    """Soft delete artifact (set deleted_at)."""
```

**Key rules:**
- `create_artifact` uses `user_id` directly (called from agent context, not HTTP request)
- All list/get/update/delete functions use `auth_id` → `get_user_id()` lookup
- Filter `deleted_at IS NULL` on all reads
- Arabic error messages: "المستند غير موجود", "لا يمكن تعديل هذا المستند", etc.

### 1.2 `backend/app/services/memory_md_service.py` (NEW)

**Purpose:** Manages the special `memory.md` artifact per case.

```python
def get_or_create_memory_md(supabase, user_id, case_id) -> dict:
    """Get existing memory.md artifact or create empty one for a case."""
    # Query: artifacts WHERE user_id=X AND case_id=Y AND artifact_type='memory_file'
    # If not found: create_artifact(..., artifact_type='memory_file', title='ذاكرة القضية', is_editable=True)

def update_memory_md(supabase, user_id, case_id, content_md) -> dict:
    """Update memory.md content. Creates if doesn't exist."""
```

### 1.3 `backend/app/api/artifacts.py` (NEW)

**Endpoints:**
```
GET  /api/v1/conversations/{conversation_id}/artifacts  → ArtifactListResponse
GET  /api/v1/cases/{case_id}/artifacts                  → ArtifactListResponse
GET  /api/v1/artifacts/{artifact_id}                    → ArtifactResponse
PATCH /api/v1/artifacts/{artifact_id}                   → ArtifactResponse
DELETE /api/v1/artifacts/{artifact_id}                  → {"message": "تم الحذف بنجاح"}
```

**Pattern:** Follow existing `cases.py` — dependency injection via `Depends(get_current_user)`, `Depends(get_supabase)`.

**Router prefix:** `/api/v1`

### 1.4 `backend/app/models/` updates

**`requests.py` — ADD:**
```python
class UpdateArtifactRequest(BaseModel):
    title: Optional[str] = None
    content_md: Optional[str] = None
```

**`responses.py` — ADD:**
```python
class ArtifactResponse(BaseModel):
    artifact_id: str
    user_id: str
    conversation_id: Optional[str] = None
    case_id: Optional[str] = None
    agent_family: str
    artifact_type: str
    title: str
    content_md: str
    is_editable: bool
    metadata: dict = {}
    created_at: str
    updated_at: str

class ArtifactListResponse(BaseModel):
    artifacts: list[ArtifactResponse]
    total: int
```

---

## Stage 2: Preferences + Templates Service + API (2 new files)

### 2.1 `backend/app/services/preferences_service.py` (NEW)

```python
# User Preferences (one row per user, JSONB blob)

def get_preferences(supabase, auth_id) -> dict:
    """Get user preferences. Returns default {} if none exist."""

def update_preferences(supabase, auth_id, preferences: dict) -> dict:
    """Upsert user preferences (merge with existing JSONB)."""

# User Templates (many per user)

def list_templates(supabase, auth_id) -> list[dict]:
    """List user's active templates (deleted_at IS NULL)."""

def create_template(supabase, auth_id, title, description, prompt_template, agent_family) -> dict:
    """Create a new user template."""

def update_template(supabase, auth_id, template_id, **kwargs) -> dict:
    """Update template fields. Ownership verified."""

def delete_template(supabase, auth_id, template_id) -> None:
    """Soft delete template."""
```

### 2.2 `backend/app/api/preferences.py` (NEW)

**Endpoints:**
```
GET   /api/v1/preferences                    → PreferencesResponse
PATCH /api/v1/preferences                    → PreferencesResponse
GET   /api/v1/templates                      → TemplateListResponse
POST  /api/v1/templates                      → TemplateResponse
PATCH /api/v1/templates/{template_id}        → TemplateResponse
DELETE /api/v1/templates/{template_id}       → {"message": "تم الحذف بنجاح"}
```

**Models to add in `requests.py`:**
```python
class UpdatePreferencesRequest(BaseModel):
    preferences: dict

class CreateTemplateRequest(BaseModel):
    title: str
    description: str = ""
    prompt_template: str
    agent_family: str = "end_services"

class UpdateTemplateRequest(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    prompt_template: Optional[str] = None
    agent_family: Optional[str] = None
    is_active: Optional[bool] = None
```

**Models to add in `responses.py`:**
```python
class PreferencesResponse(BaseModel):
    user_id: str
    preferences: dict

class TemplateResponse(BaseModel):
    template_id: str
    user_id: str
    title: str
    description: str
    prompt_template: str
    agent_family: str
    is_active: bool
    created_at: str

class TemplateListResponse(BaseModel):
    templates: list[TemplateResponse]
    total: int
```

---

## Stage 3: Register Routers (1 modified file)

### 3.1 `backend/app/main.py` (MODIFY)

Add to existing router registration:

```python
from backend.app.api.artifacts import router as artifacts_router
from backend.app.api.preferences import router as preferences_router

app.include_router(artifacts_router)
app.include_router(preferences_router)
```

---

## Validation Gate 6B

**All must pass before proceeding to Wave 6C:**

| # | Check | Method | Pass Criteria |
|---|-------|--------|---------------|
| 1 | Backend imports | `python -c "from backend.app.main import app"` | No import errors |
| 2 | OpenAPI spec | `GET /openapi.json` | All 11 new endpoints listed |
| 3 | Artifact CRUD | curl tests (authenticated) | Create → Get → Update → Delete lifecycle works |
| 4 | Artifact ownership | curl with wrong user token | 404 for other user's artifacts |
| 5 | Artifact editable check | PATCH non-editable artifact | Rejected with Arabic error |
| 6 | Preferences CRUD | curl tests | GET returns {}, PATCH merges, GET returns updated |
| 7 | Template CRUD | curl tests | Create → List → Update → Delete lifecycle works |
| 8 | Template ownership | curl with wrong user | 404 for other user's templates |
| 9 | @integration-lead | Cross-layer check | Pydantic models match plan spec exactly |

### curl Test Commands (for @validate)

```bash
# Authenticate first
TOKEN=$(curl -s -X POST http://localhost:8000/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"test@luna.ai","password":"TestLuna@2025"}' | jq -r .access_token)

# Create artifact (need valid conversation_id and user_id — use service directly for this)
# List artifacts for conversation
curl -s http://localhost:8000/api/v1/conversations/{conv_id}/artifacts \
  -H "Authorization: Bearer $TOKEN"

# Get preferences
curl -s http://localhost:8000/api/v1/preferences \
  -H "Authorization: Bearer $TOKEN"

# Create template
curl -s -X POST http://localhost:8000/api/v1/templates \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"title":"عقد إيجار","description":"نموذج عقد إيجار تجاري","prompt_template":"أنشئ عقد إيجار تجاري...","agent_family":"end_services"}'
```

---

## File Manifest

| File | Action | Agent |
|------|--------|-------|
| `backend/app/services/artifact_service.py` | NEW | @fastapi-backend |
| `backend/app/services/memory_md_service.py` | NEW | @fastapi-backend |
| `backend/app/api/artifacts.py` | NEW | @fastapi-backend |
| `backend/app/services/preferences_service.py` | NEW | @fastapi-backend |
| `backend/app/api/preferences.py` | NEW | @fastapi-backend |
| `backend/app/models/requests.py` | MODIFY | @fastapi-backend |
| `backend/app/models/responses.py` | MODIFY | @fastapi-backend |
| `backend/app/main.py` | MODIFY | @fastapi-backend |

**Total: 5 new + 3 modified = 8 files**
