# Wave 5: Polish + Testing + Deployment Verification

> Final wave — validation, security hardening, deployment, and end-to-end testing.
> Runs AFTER Wave 4 is complete and verified.

---

## Overview

Wave 5 is the quality gate before production. No new features — only testing, fixing, hardening, and deploying. All agents in this wave are Quality Agents (read-only) except for targeted fix passes.

---

## Sub-Wave 5A: Cross-Layer Verification (Parallel)

### @integration-lead
Full contract audit across all 28 endpoints:
- TypeScript types vs Pydantic models — field names, types, nullability
- Frontend API URLs vs backend route decorators — method, path, params
- SSE event names and payload shapes — backend emits vs frontend parses
- Error response format — consistent `{ detail: string }` pattern
- Enum values — Python enums vs TypeScript unions vs SQL enum types
- Pagination — consistent `{ items[], total, has_more }` pattern

### @security-reviewer
Full security audit:
1. **JWT**: Verify local decode with ES256+HS256, token expiry checks, no secret leakage
2. **RLS**: All 11 tables have policies, user isolation enforced, `(SELECT auth.uid())` pattern
3. **Input validation**: XSS prevention, SQL injection guards, file upload validation
4. **CORS**: Only allowed origins, no wildcard in production
5. **Credentials**: No secrets in code, .env.example has no real values
6. **Rate limiting**: Applied to message send, auth endpoints; fail-open on Redis down
7. **File upload**: MIME validation, size limits, path traversal prevention
8. **SSE**: No sensitive data in error events

### @rls-auditor
Live RLS verification via Supabase SQL:
- Test all 11 tables with two different user contexts
- Verify INSERT/SELECT/UPDATE/DELETE policies
- Verify soft-delete filters (WHERE deleted_at IS NULL in policies)
- Verify cross-user isolation (user A cannot see user B's data)

---

## Sub-Wave 5B: Fix Pass (Sequential, Based on 5A Results)

### @fastapi-backend
Fix any security or contract issues found by @security-reviewer and @integration-lead.

### @nextjs-frontend
Fix any type mismatches or contract issues found by @integration-lead.

### @sql-migration
Fix any RLS gaps found by @rls-auditor (new migration files if needed).

---

## Sub-Wave 5C: Full Test Suite (After 5B)

### @validate
Run comprehensive test suite:
- All 28 API endpoint tests
- SSE streaming end-to-end test
- File upload + download test
- Database integrity checks (FK constraints, enum values, indexes)
- Auth flow test (register → login → refresh → me → logout)
- Ownership isolation tests (cross-user access denied)
- Rate limiting test (verify 429 response)
- Playwright browser tests:
  - Login flow
  - Sidebar loading
  - Send message → see streaming response
  - Create case → add conversation → send message
  - Upload document → see in case
  - Add/edit/delete memory

---

## Sub-Wave 5D: Deployment Verification (After 5C)

### @deploy-checker
Verify Railway deployment health:
- Backend service running (health endpoint responds)
- Frontend service running (page loads)
- Redis connected (ping succeeds)
- Environment variables set correctly
- CORS configured for Railway domains
- SSE streaming works through Railway proxy (no buffering)
- Both Dockerfiles build successfully

---

## Success Criteria (Wave 5)

- [ ] All 28 endpoints tested and passing
- [ ] Zero security findings at Critical/High level
- [ ] RLS verified on all 11 tables with live queries
- [ ] TypeScript types match Pydantic models (zero mismatches)
- [ ] Frontend URLs match backend routes (zero mismatches)
- [ ] SSE streaming works end-to-end (local + Railway)
- [ ] Playwright tests pass (login, chat, case, documents, memories)
- [ ] Railway deployment healthy (both services, Redis connected)
- [ ] No secrets in codebase
- [ ] All error messages in Arabic
