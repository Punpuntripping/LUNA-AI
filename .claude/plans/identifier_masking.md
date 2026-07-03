# تقنيع المعرّفات — Identifier Masking (وضع السرية)

**Status:** BUILT 2026-07-02 (all phases + 2 leak-fix rounds) · acceptance run 2 PASS 2026-07-02 (`agents_reports/masking_acceptance_2026-07-02_run2.md`) · NOT deployed
**Open follow-ups (non-blocking):** (1) truncation-before-encode — note *preview* cuts a digit run mid-way before encoding, minting a spurious fragment `pii_mappings` row (`71450→71464` observed); encode before truncation or truncate at run boundaries. (2) Layer-4 `audit_encoded`/`emit_encoded_count` only wired at the intake encode — extend to the major assembly sites (history, unfold, attached, eager-context) so the runtime self-audit sees them. ~~(3) gap-1 live positive proof~~ — DONE 2026-07-03: targeted probe TRIGGERED + PASS (planner span carried both fakes incl. a fresh mint, zero reals in full-trace sweep; see "Gap-1 live probe" section of the run-2 report).
**Design source:** memory `project_pdpl_number_masking.md` (design converged 2026-06-23 — do NOT re-litigate the rules below)
**Public page draft:** `legal/masking-ar.md` (repo root, uncommitted)

## What this is

Reversible masking of numeric identifiers + emails before any user text reaches an external LLM. Per-user swap table; real values restored in everything the user sees. Optional per-user toggle **وضع السرية** in Settings; public "اقرأ المزيد" page **تقنيع المعرّفات** at `/masking`.

PDPL posture: masking is defense-in-depth on top of DPA/residency controls. Target ~80% recall — accepted; residual risks documented, never promised as 100% (no تشفير / حماية كاملة wording anywhere).

## The invariant (governs every hook decision)

> **Store REAL everywhere. Encode at every prompt-assembly point. Decode at every persist/display point.**

The DB (messages, workspace_items, summaries, templates) always holds real
values — UI reads need no decode pass. The codec wraps the seams where stored
content becomes LLM input, and where LLM output becomes stored/streamed
content. Any new agent added later must apply the same two wraps.

Idempotence mechanism (makes double-encode harmless on mixed text): before
masking a candidate run, check it against the user's known `fake_value` set —
already-a-fake → leave untouched. So `encode(encode(x)) == encode(x)` even
when real and previously-encoded text are assembled into one prompt.

## Locked rules (from design memory — implement exactly)

```
NORMALIZE   Arabic-Indic digits (٠-٩ U+0660-0669, ۰-۹ U+06F0-06F9) → ASCII.
DATES       (runs BEFORE dash-joining) 2–3 digit-groups split by / or - where one
            group is EXACTLY 4 digits starting 13|14 (Hijri) or 19|20 (Gregorian)
            and the others fit day ≤31 / month ≤12  → untouched.
            Prefix test NEVER runs on unseparated runs (an ID can start 1446).
MONEY       nearby money word (مبلغ|ريال|ر.س|SAR|دولار) or comma-grouping
            (750,000) → untouched.
JOIN        remaining digit-groups linked by dashes/spaces → one run.
MASK        run of ≥5 digits → keep first 4 (first 3 if run is 5–7 digits),
            RANDOM digits for the rest, same length.
            NOT a substitution cipher — randomness per value, table remembers.
EMAILS      own @ rule: whole address → fake address (fake local + neutral fake
            domain; never keep the real domain — it identifies employers).
TABLE       per USER (not per case/convo): real ↔ fake, reuse forever
            (same real → same fake). Stable fakes keep encoded history
            byte-stable → prompt-cache safe.
DECODE      exact full-string match only (normalize the LLM output digits first —
            models can emit Arabic-Indic). First-3/4 prefix matches a fake but
            tail differs → DAMAGED-FAKE TRIPWIRE: Logfire warn, never auto-decode
            by prefix (two-clients-same-prefix swap hazard). v1 = log only.
TOOLS       any server-side tool needing a real value decodes via the table
            before executing (v1: hook exists, no consumer wired).
```

## Architecture — where it hooks (verified against code 2026-06-23)

```
user msg ──► messages table (ORIGINAL, untouched — rule #7 unchanged)
         ──► _handle_message_inner (agents/orchestrator.py:1106)
                 │  encode(question) ONCE at top — covers normal + resume paths
                 │  (pause branch at :1151 consumes the same `question`)
                 ▼
             _load_recent_messages (agents/orchestrator.py:202)
                 │  encode each loaded content — SINGLE choke point for history
                 │  (feeds router :621, resume :760, planners :879, :1410)
                 ▼
             pipeline runs entirely on ENCODED text
             (router, planners, deep_search, writer, Logfire spans — bonus:
              covered prompts logged to Logfire now carry fakes, not real PII)
                 ▼
   ┌─ SSE text deltas ──► buffered stream-decode in message relay
   │                      (backend/app/services/message_service.py)
   ├─ assistant message persist ──► full-text decode before
   │                      {"content": full_content} (message_service.py:745,
   │                      timeout paths :840 :850)
   └─ workspace_items content_md ──► decode before insert/update in:
                          agents/writer/publisher.py, agents/writer/lock.py,
                          agents/agent_search/publisher.py,
                          agents/tool_repository/save_memo.py (memo pins the
                          user msg — must show REAL values back to the user)
```

### Full agent-surface coverage (Phase 3b — ALL agents, per user decision 2026-06-23)

Beyond the intake/history/exit hooks above, every remaining path where stored
user content becomes LLM input gets an encode wrap:

| Surface | File(s) | Wrap |
|---|---|---|
| **Router prior-turn history (gap found in Phase 3, 2026-07-02):** router history comes from `load_router_context` → `messages_to_history`, NOT `_load_recent_messages` | `agents/utils/history.py` (`messages_to_history`) | encode each history message at assembly |
| **ask_user / agent_question surfacing (gap found in Phase 3):** planner pause questions are generated from encoded input → can carry fakes to the user | orchestrator `_record_deferred` + the SSE `agent_question` payload path | decode BOTH the SSE payload and the `_record_deferred` messages insert (decoding only one makes streamed vs reloaded views inconsistent) |
| **Force-attached WI render into deep_search planner (GAP found by acceptance run 2026-07-02 — leaked memo content_md verbatim into planner_decider spans)** | `agents/orchestrator.py` `_load_attached_items` → `agents/deep_search_v4/planner/prompts.py` `_render_attached_items` | encode title+content_md at assembly + persist_new before the planner LLM |
| **Eager WI summaries/titles + case memory (was "accepted residual" — acceptance run proved it leaks; UPGRADED to wrapped)** | router context builder, planner_decider + writer_planner_decider context (`load_writer_planner_context`), case_memory_md injection | encode at assembly; stored summaries stay real |
| unfold_workspace_item (router, planner_decider, writer_planner context) | `agents/tool_repository/unfold_workspace_item.py` | encode the rendered content_md + manifest string. **Bonus: OCR'd attachment content flows through here too — covered for free** (stored content_md stays real) |
| Provenance tags (WI titles can carry PII) | `agents/utils/history.py` (`build_provenance_tag`) | encode assembled tag |
| Memory agents — resummarize, compaction, inline attachment summarizer (Layer 4) | ~~`agents/memory/agent.py`~~ **`agents/memory/summarize.py`** (3b finding: agent.py is still the Wave-9 MOCK, no LLM — real summarizer is summarize.py + artifact_summarizer flows) | encode LLM inputs at assembly; **decode summaries before store**. Detached callers (internal webhook + summary_sweeper) have no turn ContextVar → codec built explicitly per user |
| artifact_editor (router tool, reads WI content_md from DB) | `agents/artifact_editor/agent.py`, `agents/tool_repository/edit_artifact.py` | encode WI content fed to the editor; decode editor output in `agents/tool_repository/edit_supabase_md.py` before batch write (`prev_content_md` snapshot is of stored-real content — untouched) |
| User templates (قوالبي) — template text injected into writer planning | `agents/writer_planner/runner.py` + `agents/writer/prompts.py` injection points | encode template content at assembly. **3b: wrapped at the `render_package_for_system_prompt` choke point — beneficial superset also covers the writer's `<sources>`/`<references>`/`<prior_draft>` bodies (fresh DB fetches, LLM-bound)** |
| template_ingester (ingests raw user document) | `agents/memory/template_ingester/agent.py` | encode ingest input; decode cleaned template before store |
| Blog/editorial internal API (reads agent_writing artifacts) | `backend/app/api/deepsearch_api/generate.py` | encode artifact content at its LLM call; decode generated post before store. **Phase 3 finding: generate.py also persists the bot assistant message itself (own path, not message_service) — decode that too** |
| paused_runs state snapshots | — no change | state is captured mid-pipeline (already encoded) and only ever re-fed to prompts; consistent by construction |
| deep_search_v4 internals (planner/expanders/rerankers/aggregator) | — no change | inputs are the encoded question, encoded history, unfold output (covered), and public corpus text (no user PII) |

Toggle check: once per turn in `_handle_message_inner` via preferences (JSONB key
`privacy_masking`, mirroring `get_detail_level` in
`backend/app/services/preferences_service.py:23`). OFF → codec is a no-op
passthrough (zero regex work). Global env kill-switch `PRIVACY_MASKING_ENABLED`
(default `true`) gates the whole feature server-side.

### Toggle semantics (user decision 2026-06-23: default ON at launch)

- **Default ON** for all users (`get_privacy_masking` defaults True; env
  kill-switch unchanged). Privacy by default.
- **Global, immediate, all conversations.** The flag is read once per turn at
  intake and gates ENCODE only. Disable → next message onward goes raw in
  every conversation, including continued old ones (history window = last
  `_RECENT_MESSAGES_N`=5 msgs + unfolded WIs). No per-conversation state, no
  ratchet.
- **Nothing to migrate on toggle** — store-real invariant means no stored data
  is ever encoded; the toggle only changes future prompt assembly. What was
  already sent masked stays masked in provider history forever (fakes are
  immutable in past prompts).
- **DECODE ALWAYS RUNS, unconditionally — never gated by the flag.** Covers
  stragglers in any toggle order: paused_runs state snapshots captured while
  ON and resumed after OFF, in-flight turns, mixed content. Without this, a
  pause→disable→resume sequence would show fake numbers to the user.
- **Mapping table is never deleted on disable** — re-enable reuses the same
  fakes (consistency + prompt-cache stability preserved).
- **Disable confirmation dialog** (frontend):
  «إيقاف وضع السرية؟ سترسل رسائلك الجديدة — في جميع المحادثات بما فيها
  السابقة عند متابعتها — بأرقامها الحقيقية إلى نماذج الذكاء الاصطناعي.
  ما أُرسل سابقًا أثناء التفعيل يبقى مقنّعًا ولا يتأثر.»
- Toggle flip busts the prompt-cache prefix once per continued conversation
  (history re-encodes differently). Accepted.
- v2 candidate (NOT v1): per-conversation override chip for troubleshooting.

### Streaming decode buffering (the one tricky part)

A fake can be split across SSE chunks (`…هويته 10328` | `49275 و…`). The relay
must hold back the tail of a chunk when it ends inside a potential run
(trailing ASCII/Arabic-Indic digits, or digits+dash/space; hold-back cap 32
chars) and flush on the first non-run char or stream end. Decode operates on
the joined pending text. Heartbeats/status events pass through untouched.

### Concurrency & storage

- Mapping dict (fake→real and real→fake) loaded once per turn (one SELECT).
- New fake insert: `UNIQUE(user_id, real_value)` + `UNIQUE(user_id, fake_value)`;
  on conflict → re-select (another concurrent turn won the race) or regenerate
  (fake collision). Never two fakes for one real.
- Toggle flip mid-conversation busts the prompt-cache prefix once (history
  re-encodes). Accepted.

## File manifest

### New

| File | Contents |
|---|---|
| `shared/privacy/__init__.py` | exports `encode`, `decode`, `PrivacyCodec` |
| `shared/privacy/codec.py` | normalize → date/money excluders → join → mask; decode + tripwire; pure functions, no I/O |
| `shared/privacy/store.py` | per-user mapping load/upsert (Supabase), conflict-retry, fake generator (random, keep-prefix, length-preserving, unique) |
| `shared/db/migrations/087_pii_mappings.sql` | table + RLS (deny-all client; service-role only) + indexes |
| `frontend/components/Settings/PrivacyMaskingDialog.tsx` | mirrors `UsageLimitsDialog.tsx`; switch + copy from the dialog draft + اقرأ المزيد link → `/masking` |
| `frontend/app/masking/page.tsx` | mirrors `frontend/app/terms/page.tsx` (static md render, prerendered, public) |
| `frontend/content/legal/masking-ar.md` | copy of `legal/masking-ar.md` |
| `shared/privacy/tests/test_codec.py` | unit tests (see Test plan). NOTE: `.gitignore` ignores `tests/` globally — a `!shared/privacy/tests/` negation is required (added 2026-07-02) so these are actually committed |

### Modified

| File | Change |
|---|---|
| `agents/orchestrator.py` | encode `question` at `_handle_message_inner` top; encode inside `_load_recent_messages`; thread codec/mapping through ctx |
| `backend/app/services/message_service.py` | buffered stream-decode on text deltas; full decode at content persist points (:745, :840, :850) |
| `backend/app/services/preferences_service.py` | `get_privacy_masking(supabase, user_id) -> bool` (default **True** — default-ON decision), same resilience pattern as `get_detail_level` |
| `agents/writer/publisher.py`, `agents/agent_search/publisher.py`, `agents/tool_repository/save_memo.py` | decode `content_md` before workspace_items write (Phase 3 finding: `agents/writer/lock.py` only writes `locked_by_agent_until`, never content_md — no hook needed) |
| `agents/tool_repository/unfold_workspace_item.py`, `agents/utils/history.py`, `agents/memory/agent.py`, `agents/artifact_editor/agent.py`, `agents/tool_repository/edit_artifact.py`, `agents/tool_repository/edit_supabase_md.py`, `agents/writer_planner/runner.py`, `agents/writer/prompts.py`, `agents/memory/template_ingester/agent.py`, `backend/app/api/deepsearch_api/generate.py` | Phase 3b agent-surface wraps (see coverage table) |
| `frontend/hooks/use-preferences.ts`, `frontend/stores/preferences-store.ts`, `frontend/types/index.ts` | `privacy_masking` boolean through existing preferences plumbing |
| `frontend/components/sidebar/SidebarFooter.tsx` | menu item **وضع السرية** alongside حدود الاستخدام / تفعيل برمز |
| `frontend/components/auth/AuthGuard.tsx` | allow-list `/masking` |
| `shared/config.py` (or equivalent settings module) | `PRIVACY_MASKING_ENABLED` env flag |

### Migration 087 sketch

```sql
CREATE TABLE pii_mappings (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id     uuid NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,  -- users PK is user_id, not id
  kind        text NOT NULL DEFAULT 'number',      -- 'number' | 'email'
  real_value  text NOT NULL,                       -- normalized ASCII form
  fake_value  text NOT NULL,
  created_at  timestamptz NOT NULL DEFAULT now(),
  UNIQUE (user_id, real_value),
  UNIQUE (user_id, fake_value)
);
CREATE INDEX idx_pii_mappings_user ON pii_mappings (user_id);
ALTER TABLE pii_mappings ENABLE ROW LEVEL SECURITY;
-- deny-all: no client policies; backend uses service role only.
```

(No migration for the preference itself — JSONB key on `user_preferences`.)

## Phases & agents

**Phase 1 — codec core** (@shared-foundation)
`shared/privacy/` pure logic + unit tests green. No wiring. Exit: test suite
covers every rule in "Locked rules" including tripwire and buffering helper.

**Phase 2 — DB** (@sql-migration)
087 written + applied via Supabase MCP; RLS verified deny-all from anon/auth
roles. Exit: live-schema check.

**Phase 3 — backend wiring** (@fastapi-backend)
Preferences getter, orchestrator encode hooks, publisher/save_memo decode,
message_service stream + persist decode, env flag. Exit: with flag OFF,
byte-identical passthrough (regression suite unchanged); with ON, manual turn
shows fakes in Logfire span payloads and reals in the UI.

**Phase 3b — full agent-surface coverage** (@fastapi-backend, after Phase 3)
All wraps in the coverage table: unfold, provenance, memory agents (encode in /
decode out), artifact_editor round-trip, templates (injection + ingester),
blog API. Exit: with flag ON, a turn touching each surface shows fakes in the
corresponding Logfire provider spans; DB rows (WI content_md, summaries,
templates, blog posts) contain reals.

**Phase 4 — frontend** (@nextjs-frontend)
Dialog + menu item + preferences plumbing + `/masking` page + AuthGuard.
Exit: `npx tsc --noEmit` + `npm run build` green; dialog toggles and persists;
page renders RTL, public, prerendered.

**Phase 5 — validation** (@validate)
Fixture-driven E2E (below) + Playwright UI pass + Logfire span inspection.
Writes `agents_reports/validation_masking.md`.

## Test plan

Fixture `shared/privacy/tests/masking_fixtures.json` (~30 messages), asserting
exact expected encode output per message. Categories:

1. Saudi ID 10-digit (ASCII + Arabic-Indic) → keep-4 masked
2. Phones: `0501234567`, `+966 50 123 4567`, `050-123-4567`, `٠٥٠١٢٣٤٥٦٧` → all collapse to consistent masking
3. Hijri dates `1446/09/15`, `15-9-1446` → untouched; Gregorian `2024-05-12` → untouched
4. Elderly/future years `1355/02/10`, `2035/01/01` → untouched (13/14/19/20 rule)
5. Money `500,000 ريال`, `مبلغ 750000` → untouched; bare `753493` → masked keep-3, magnitude survives
6. Article/case refs: `المادة 77`, `الفقرة 3` → untouched (<5 digits); case no. `4350123456` → masked (accepted)
7. IBAN `SA44 2000 0001 2345 6789 1234` → digits joined + masked
8. Emails incl. corporate domain → full swap
9. 5–7 digit runs → keep-3; 9-digit typo ID → still caught (length trigger)
10. Compact date `20240512` → masked but year survives (documented residual)
11. Idempotence: encode(encode(x)) == encode(x); same value twice in one msg → same fake; across turns → same fake
12. Decode: exact restore; Arabic-Indic output digits restore; split-across-chunks restore (buffering); tripwire fires on mangled fake, does NOT decode by prefix
13. Flag OFF → passthrough byte-identical
14. Mixed-text idempotence: prompt assembled from real WI content + encoded question → single encode pass masks the reals, leaves existing fakes untouched
15. artifact_editor round-trip: WI with an ID → edit instruction → editor span shows fakes, edited content_md stored with reals
16. Memory round-trip: summarize a message containing an ID → summarizer span shows fake, stored summary shows real
17. Template ingest: raw template with phone → ingester span fake, stored template real

E2E (staging): toggle ON → send fixture-style message → assert Logfire provider
span shows fakes, chat UI shows reals, workspace item content_md (memo + writer
doc) shows reals, `pii_mappings` rows created once and reused on turn 2.

## Validation strategy — proving every number is correctly masked

Five layers. Layers 1–2 prove the codec; Layer 3 proves the wiring; Layers 4–5
prove it stays true in production. The key trick used by Layers 2 and 4: **the
detector is its own auditor** — re-running detection on already-encoded text
must find nothing but known fakes. Any other hit = a leak, mechanically found.

### Layer 1 — fixture unit tests (exact expectations)
The ~30-message fixture above (`masking_fixtures.json`): every locked rule and
counter-rule with byte-exact expected output. Fails loudly on any rule drift.

### Layer 2 — property/fuzz tests (invariants over generated inputs)
Generator composes random Arabic/English messages from parts: random PII-shaped
values (IDs, phones ±dashes/spaces/Arabic-Indic digits, IBANs, emails), legal
numbers (amounts ±money words, Hijri/Gregorian dates, article/case refs), and
noise text. ~10k cases in CI. Assert on every case:

1. **Completeness (the core "all numbers masked" proof):** run the DETECTOR
   again on the encoded output — every hit must be a known `fake_value` for
   this user. Any non-fake hit = a real value survived = fail.
2. **Round-trip:** `decode(encode(m))` restores every masked value exactly
   (whole message equality after normalization).
3. **Exclusion safety:** date/money spans byte-identical in the output.
4. **Consistency:** same value N times (any digit-script/format variant) →
   one fake.
5. **Shape:** length preserved, prefix rule respected (4 / 3 for short runs).
6. **Idempotence:** `encode(encode(m)) == encode(m)`.

### Layer 3 — cross-agent sentinel E2E (the acceptance gate)
Plan: `agents_reports/smoke_tests/masking_sentinel_baseline_plan.md` — one
conversation, 4 turns (search → writer → editor → history-recall), unique
correctly-shaped sentinels (ID, phone, IBAN, case no., amount, email), traced
through every agent's Logfire LLM spans and every DB write.

- **Baseline run (pre-build, optional):** maps which agents SEE/COMMIT raw
  sentinels today — empirically double-checks the Phase 3/3b coverage tables
  before building. Any span carrying a sentinel the tables miss = plan gap.
- **Acceptance run (post-build, flag ON — gates default-ON rollout):**
  (1) real sentinels ABSENT from every LLM span across ALL agents;
  (2) ONE consistent fake per sentinel across all agents and turns (incl.
  history threading in turn 4); (3) SENT_AMOUNT still raw in spans (money
  exclusion works); (4) user-facing DB rows hold REALS, zero fakes;
  (5) turn-4 UI answer repeats the REAL ID/phone (decode round-trip).

### Layer 4 — runtime self-audit (validates every real message, forever)
After each encode of an outgoing prompt, run the detector once more on the
result (cheap regex pass): any hit that is not a known fake → emit Logfire
event `masking.leak_candidate` (count + kind only — NEVER the value itself).
Companion counters: `masking.encoded_count`, `masking.tripwire_hit`,
`masking.decode_restored_count`. Alert on `leak_candidate > 0`. This turns
"did we mask everything?" from a test-time question into a production metric.

Scheduled deep audit (weekly, server-side script — outputs counts, never
values): pull each opted-in user's `real_value` set from `pii_mappings` and
grep their recent Logfire span payloads for any occurrence. Expected: 0.
Catches wiring regressions (a new agent added without the encode wrap) that
the self-audit can't see.

**Canary probe via the editorial Blog-Post API (user idea 2026-06-23 —
adopted):** `POST /internal/blog-post-jobs` consumes
`agents.orchestrator.handle_message` directly (generate.py:135) — the REAL
pipeline front door, service-authed, scriptable, no browser. Scheduled (post-
deploy + weekly): submit a sentinel-laden `question` as a dedicated canary
editorial user with `privacy_masking=ON`, then assert via Logfire + DB that
spans carry only the canary's known fakes and the produced WI holds reals.
MUST use `publish_policy: "never"` (sentinel posts must never reach the public
blog) + a fresh `idempotency_key` per run. Covers: intake encode, router,
deep_search, writer, publisher decode, toggle read. Does NOT cover (chat E2E
only): SSE stream decode, message_service persist decode, multi-turn history
encode, artifact_editor/save_memo, UI round-trip — the probe complements
Layer 3, never replaces it.

### Layer 5 — recall measurement vs the 80% target
Labeled eval corpus: ~200 synthetic legal messages with hand-labeled PII spans
(generated once, reviewed by user — includes the nasty tail: spelled-out
numbers, broken formats, mixed scripts). Report recall/precision per category
on every codec change. Pass bar: ≥80% overall recall, 100% on
checksum-shaped IDs/phones/IBANs written normally, 0 false masks on the
date/money fixture rows.

## Rollout (updated 2026-06-23: default ON from launch — user decision)

1. Ship with per-user default **ON** + `PRIVACY_MASKING_ENABLED=true`
   (env kill-switch = the emergency brake if decode misbehaves in prod).
2. Acceptance gate (Layer 3 sentinel E2E) MUST pass before deploy — with
   default ON there is no opt-in soak period, so the gate carries the weight.
3. Watch `masking.leak_candidate`, tripwire warnings + decode latency in
   Logfire closely for the first 2 weeks; canary blog-post probe on every
   deploy.

## Residual risks / out of scope v1 (document, don't fix)

- Names: out of scope by design — «الطرف الأول» advice on the public page.
- ≤4-digit numbers, spelled-out numbers, formats the joiner misses (~20% accepted).
- **Mistral OCR API sees raw document bytes** — OCR extraction itself sends the
  original PDF/image to Mistral before any text exists to mask. Accepted by
  user decision 2026-06-23 ("it's ok for the OCR") — cover contractually (DPA),
  not technically. Note the *extracted text* IS masked downstream (unfold /
  summarizer assembly hooks), so OCR content reaching chat LLMs is covered.
- Tripwire is log-only in v1; UI badge for damaged fakes is v2.
- ~~Eager router/planner context not wrapped~~ **UPGRADED to wrapped 2026-07-02**: the acceptance run proved eager summaries leak sentinels (router/planner spans quoted them raw across turns 2–4) — no longer an accepted residual.
- **Layer-4 blindness (acceptance-run lesson):** `masking.leak_candidate` only audits text that passed through `encode()` — it is structurally blind to UN-wrapped assembly paths (zero events fired during a run with confirmed leaks). Wiring gaps are caught only by the Layer-3 sentinel E2E and the weekly deep audit (grep spans for `pii_mappings.real_value`), never by the runtime self-audit.
- `real_value` stored plaintext (Supabase at-rest encryption); pgcrypto
  column-level encryption is optional hardening later.
