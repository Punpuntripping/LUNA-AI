---
name: test
description: Run Luna Legal AI test suite — full, by category, or single test
user_invocable: true
---

# /test — Luna Legal AI Test Runner

## Description
Runs the comprehensive test suite for Luna Legal AI. Tests cover auth, CRUD, SSE streaming, security, cross-user isolation, rate limiting, and infrastructure bugs.

## Arguments
- No args → run ALL tests
- `auth` → authentication tests only
- `cases` → cases CRUD tests only
- `conversations` → conversations CRUD tests only
- `messages` or `sse` → messages + SSE streaming tests only
- `documents` → document upload/download tests only
- `memories` → memories CRUD tests only
- `isolation` → cross-user data isolation tests only
- `security` → security vulnerability tests only
- `ratelimit` → rate limiting tests only
- `infrastructure` → infrastructure bug tests (from research)
- `workflow` → end-to-end workflow tests only
- `quick` → run only health + auth + isolation (fast smoke test)
- Any pytest expression → passed directly to pytest -k

## Instructions

You are running the Luna Legal AI test suite. Follow these steps:

### Step 1: Check Prerequisites
Verify the backend is running:
```bash
curl -s http://localhost:8000/api/v1/health | head -c 100
```
If not running, tell the user to start it with:
```
cd backend && uvicorn app.main:app --port 8000 --reload
```

### Step 2: Install Test Dependencies (if needed)
```bash
pip install -r tests/requirements-test.txt 2>/dev/null
```

### Step 3: Run Tests
Based on the argument provided, run the appropriate pytest command from the project root (`C:/Programming/LUNA_AI`):

**No args (full suite):**
```bash
cd C:/Programming/LUNA_AI && python -m pytest tests/ -v --tb=short 2>&1
```

**Category markers:**
```bash
cd C:/Programming/LUNA_AI && python -m pytest tests/ -v --tb=short -m "{category}" 2>&1
```

Where `{category}` is the marker name (auth, cases, conversations, messages, documents, memories, isolation, security, ratelimit, infrastructure, sse).

**Quick smoke test:**
```bash
cd C:/Programming/LUNA_AI && python -m pytest tests/test_01_health.py tests/test_02_auth.py tests/test_08_isolation.py -v --tb=short 2>&1
```

**Workflow tests:**
```bash
cd C:/Programming/LUNA_AI && python -m pytest tests/test_11_workflow.py -v --tb=short 2>&1
```

**Custom expression:**
```bash
cd C:/Programming/LUNA_AI && python -m pytest tests/ -v --tb=short -k "{expression}" 2>&1
```

### Step 4: Report Results
After tests complete, provide a summary:
1. Total tests run / passed / failed / skipped / xfailed
2. List each FAILED test with the error message
3. List each XFAIL test (known bugs that were confirmed)
4. Map failures to known bug IDs from the infrastructure research report if applicable
5. Give a confidence score: X% of tested functionality is working

### Environment Variables
Tests target `http://localhost:8000` by default. Override with:
```
TEST_BASE_URL=https://luna-backend-production-35ba.up.railway.app
```

### Test Files
```
tests/
├── conftest.py              # Fixtures, test users, shared setup
├── pytest.ini               # Pytest configuration
├── requirements-test.txt    # Test dependencies
├── test_01_health.py        # Health, CORS, Request-ID
├── test_02_auth.py          # Auth: register, login, refresh, logout, me, JWT security
├── test_03_cases.py         # Cases CRUD
├── test_04_conversations.py # Conversations CRUD
├── test_05_messages_sse.py  # Messages + SSE streaming
├── test_06_documents.py     # Document upload/download
├── test_07_memories.py      # Memories CRUD
├── test_08_isolation.py     # Cross-user data isolation
├── test_09_rate_limiting.py # Rate limiting enforcement
├── test_10_infrastructure.py # Infrastructure bugs from research
└── test_11_workflow.py      # End-to-end user journey tests
```

### Known Bug IDs (from research)
Tests reference these bugs with `pytest.xfail()` or explicit assertions:
- **BUG-B**: POST /memories returns 200 instead of 201
- **BUG-D / BUG-SSE-01**: SSE returns 200 before ownership check
- **BUG-STOR-01**: Document upload 500 (bucket mismatch)
- **BUG-JWT-01**: Algorithm confusion (alg=none, RS256, HS384)
- **BUG-CORS-01**: Wildcard + credentials conflict
- **BUG-REDIS-01**: Rate limit race condition
- **BUG-PYDANTIC-01**: Arabic diacritics in validation
- **BUG-RLS**: Cross-user data isolation failures
