# Wave 3: Sidebar + Cases CRUD + Conversations CRUD

> Status: **MOSTLY DONE — bugs and gaps found**
> Audit date: 2026-03-11

---

## Overview

Wave 3 covers the sidebar UI and full CRUD for cases and conversations. The backend is 100% complete. The frontend has a critical bug and several missing UI features.

---

## Audit Results

### What's DONE (no issues)

| Component | Status |
|-----------|--------|
| Backend `cases.py` — 6 endpoints | DONE, all working |
| Backend `conversations.py` — 6 endpoints | DONE, all working |
| Backend `case_service.py` — full CRUD, auto-create conversation, cascade delete | DONE |
| Backend `conversation_service.py` — full CRUD, end-session | DONE |
| Backend request/response models | DONE, all fields correct |
| Frontend `Sidebar.tsx` — mobile responsive, RTL, tabs | DONE |
| Frontend `SidebarHeader.tsx` — new conversation button | DONE |
| Frontend `SidebarFooter.tsx` — user display, logout | DONE |
| Frontend `ConversationList.tsx` — date grouping (Arabic), skeletons, empty state | DONE |
| Frontend `ConversationItem.tsx` — rename inline, click to navigate | DONE |
| Frontend `CaseList.tsx` — new case dialog, skeletons, empty state | DONE |
| Frontend `CaseCard.tsx` — expandable, nested conversations, badges | DONE |
| Frontend `sidebar-store.ts` — all state and actions | DONE |
| Frontend `types/index.ts` — all Case/Conversation types | DONE |
| Frontend `lib/api.ts` — casesApi (6 methods), conversationsApi (6 methods) | DONE |
| Frontend `chat/layout.tsx` — renders sidebar | DONE |
| Frontend `chat/page.tsx` — empty state, new conversation creation | DONE |
| Cross-layer contract — types match, URLs match | DONE |

### Bugs Found

#### BUG 1 — CRITICAL: `case_id=null` sent as literal string

**File:** `frontend/lib/api.ts`
**Line:** ~245
**Problem:** When `ConversationList` calls `useConversations(null)` to list ALL conversations (no case filter), the API client sends `?case_id=null` as a query parameter. The backend receives the string `"null"` instead of omitting the parameter, causing `.eq("case_id", "null")` which returns zero results. **This breaks the entire conversations tab.**
**Fix (@nextjs-frontend):** Remove the `if (params?.case_id === null) searchParams.set("case_id", "null")` block. When `case_id` is `null`, don't send it at all.

#### BUG 2 — LOW: `endSession` return type mismatch

**File:** `frontend/lib/api.ts`
**Line:** ~264-265
**Problem:** `endSession` is typed to return `{ success: boolean }` but the backend returns `ConversationResponse` (`{ conversation: ConversationDetail }`).
**Fix (@nextjs-frontend):** Update the return type to match the backend response.

### Missing Frontend Features

#### GAP 1 — HIGH: No delete confirmation for conversations

**File:** `frontend/components/sidebar/ConversationItem.tsx`
**Problem:** Clicking delete immediately calls `deleteConversation.mutate()` without confirmation. User can accidentally lose a conversation.
**Fix (@nextjs-frontend):** Add `AlertDialog` from shadcn/ui wrapping the delete action. Arabic confirmation text: "هل أنت متأكد من حذف هذه المحادثة؟" with "حذف" and "إلغاء" buttons.

#### GAP 2 — MEDIUM: No UI to delete a case

**File:** `frontend/components/sidebar/CaseCard.tsx`
**Problem:** No delete button or dropdown menu exists. `useDeleteCase` hook and `casesApi.delete` exist but have no UI trigger.
**Fix (@nextjs-frontend):** Add a dropdown menu (DropdownMenu from shadcn/ui) with "حذف القضية" option + `AlertDialog` confirmation. Arabic text: "هل أنت متأكد من حذف هذه القضية؟ سيتم حذف جميع المحادثات المرتبطة بها."

#### GAP 3 — MEDIUM: No UI to update a case (rename, change type/priority)

**File:** `frontend/components/sidebar/CaseCard.tsx`
**Problem:** No edit/rename mechanism. Backend PATCH endpoint exists.
**Fix (@nextjs-frontend):** Add "تعديل القضية" option in the dropdown menu → opens Dialog with editable fields (case_name, case_type, priority, description). Also add `useUpdateCase` hook in `use-cases.ts`.

#### GAP 4 — MEDIUM: No UI to change case status

**File:** `frontend/components/sidebar/CaseCard.tsx`
**Problem:** No way to close/archive a case from the UI. Backend PATCH status endpoint exists.
**Fix (@nextjs-frontend):** Add status options in the dropdown menu: "إغلاق القضية" and "أرشفة القضية". Also add `useUpdateCaseStatus` hook in `use-cases.ts`.

### Missing Hooks

#### GAP 5 — LOW: Missing hooks in `use-cases.ts`

- `useUpdateCase()` — for PATCH `/{case_id}`
- `useUpdateCaseStatus()` — for PATCH `/{case_id}/status`

#### GAP 6 — LOW: Missing hooks in `use-conversations.ts`

- `useEndSession()` — for POST `/{id}/end-session`
- `useConversationDetail()` — for GET `/{id}` (useful for Wave 4 chat page)

### Cosmetic Issues

#### GAP 7 — LOW: Arabic dual/plural forms in date helper

**File:** `frontend/lib/utils.ts` → `getRelativeTimeAr()`
**Problem:** "منذ 2 أيام" should be "منذ يومين" (dual form). Arabic grammar has special forms for 2 (dual), 3-10 (plural), 11+ (singular).
**Fix (@nextjs-frontend):** Update the helper to handle Arabic numeral grammar rules.

---

## Fix Plan

### Fix Wave 3A — Critical Bug (Immediate)

**@nextjs-frontend:**
1. Fix `frontend/lib/api.ts` — remove `case_id=null` string bug
2. Fix `frontend/lib/api.ts` — correct `endSession` return type

### Fix Wave 3B — Missing UI (After 3A)

**@nextjs-frontend:**
1. Add delete confirmation dialog to `ConversationItem.tsx`
2. Add dropdown menu to `CaseCard.tsx` with: edit, change status, delete
3. Add case edit dialog (reuse pattern from CaseList's create dialog)
4. Add `useUpdateCase()` and `useUpdateCaseStatus()` hooks to `use-cases.ts`
5. Add `useEndSession()` and `useConversationDetail()` hooks to `use-conversations.ts`

### Fix Wave 3C — Cosmetic (Optional, Low Priority)

**@nextjs-frontend:**
1. Fix Arabic dual/plural forms in `getRelativeTimeAr()`

---

## Post-Wave Validation

After fixes are applied, run:

**@integration-lead:**
- Verify `conversationsApi.list` no longer sends `case_id=null`
- Verify `endSession` return type matches backend
- Verify new hooks match API client methods

**@validate:**
- Test conversation list loads correctly (the main tab)
- Test case delete from sidebar
- Test case edit/status change
- Test delete confirmation dialog appears before deletion

---

## File Manifest

### Files to Modify (Wave 3 Fixes)

| # | Path | Agent | Fix |
|---|------|-------|-----|
| 1 | `frontend/lib/api.ts` | @nextjs-frontend | Remove `case_id=null` bug, fix `endSession` return type |
| 2 | `frontend/components/sidebar/ConversationItem.tsx` | @nextjs-frontend | Add delete confirmation AlertDialog |
| 3 | `frontend/components/sidebar/CaseCard.tsx` | @nextjs-frontend | Add dropdown menu (edit, status, delete) + dialogs |
| 4 | `frontend/hooks/use-cases.ts` | @nextjs-frontend | Add `useUpdateCase`, `useUpdateCaseStatus` hooks |
| 5 | `frontend/hooks/use-conversations.ts` | @nextjs-frontend | Add `useEndSession`, `useConversationDetail` hooks |
| 6 | `frontend/lib/utils.ts` | @nextjs-frontend | Fix Arabic dual/plural forms (optional) |

### No New Files Required

All Wave 3 features were already scaffolded. Only modifications needed.

---

## Success Criteria (Wave 3 Complete)

- [ ] Conversations tab loads and shows all general conversations (bug #1 fixed)
- [ ] Conversations grouped by Arabic date labels (اليوم، أمس، هذا الأسبوع)
- [ ] Delete conversation shows confirmation dialog before deleting
- [ ] Cases tab shows all cases with stats
- [ ] Case card has dropdown menu with edit/status/delete options
- [ ] Case edit dialog allows renaming and changing type/priority
- [ ] Case status can be changed to closed/archived from dropdown
- [ ] Case delete shows confirmation with cascade warning
- [ ] New case creation works with all fields
- [ ] New conversation creation works from header button
- [ ] Inline conversation rename works
- [ ] Sidebar responsive on mobile (overlay)
- [ ] All UI text in Arabic, RTL layout
- [ ] Skeleton loaders on initial load
- [ ] Empty states with Arabic messages
