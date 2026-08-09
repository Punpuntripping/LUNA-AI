-- ════════════════════════════════════════════════════════════════════════════
-- 121 — appendix chunks join the body's position sequence
--       (one continuous `position` per document, one walkable prev/next chain)
-- ════════════════════════════════════════════════════════════════════════════
--
-- Spec: .claude/plans/chunk_appendix_position_unification.md — §3 (why this is
--       safe), §4 (this file, statement for statement), §8 (risks).
--       ⚠ The plan calls this "migration 120" throughout. It is 121: the number
--       collided with 120_subscription_cancellation.sql, which landed after the
--       plan was written. Read every "120" in that document as "121".
-- Depends on: 067 (RLS + write revokes on regulation_v2.*; the precedent for
--             this repo writing to the pipeline schema at all).
-- Target: regulation_v2.chunks — the BASE TABLE. `public.chunks_v2` is a VIEW
--         over it and cannot be written or indexed (116 header, 109 T6).
--         Nothing here touches the view.
--
-- ⚠⚠ DO NOT RUN THIS UNTIL §7 STEPS 1–3 ARE DONE ⚠⚠ ─────────────────────────
--   1. The read-time ordering fix (`_ordered_chunk_query` — `corpus DESC,
--      position, chunk_ref`) is COMMITTED AND DEPLOYED.
--   2. `/api/revalidate` purge has run. DOC_REVALIDATE = 86400, so without it
--      the published pages keep serving the interleaved bake for a day.
--   3. The labour لائحة (17900_reg_128_p2) and وثيقة الضمان الصحي الأساسية
--      have been eyeballed rendering body-then-ملاحق.
-- That order is deliberate: the deploy fixes all 166 live misplaced sections
-- within minutes and is independent of this file, so this migration lands under
-- a read path that is already correct either way. Running this FIRST would move
-- 3,814 rows under a read path that is still ordering by raw `position`, and if
-- anything here is wrong there is no correct renderer to fall back to.
--
-- ⚠ NOT LIVE-VERIFIED AT AUTHORING TIME ─────────────────────────────────────
-- This file was written without database access (no MCP/psql in the authoring
-- session), so every number quoted below is the PLAN's measurement, not a fresh
-- one. This repo has a standing rule that migration files drift from prod. RUN
-- THE DRY-RUN BLOCK AT THE BOTTOM OF THIS FILE FIRST and compare — it is
-- read-only, it reproduces every figure in §1/§3/§4.4, and it proves the
-- idempotency claim empirically. If any figure disagrees, stop and re-read the
-- plan rather than applying.
--
-- WHAT ──────────────────────────────────────────────────────────────────────
--   0. Preflight — privileges, baseline capture, the affected/chainable sets.
--   1. Preconditions — every assumption §3 and §4 rest on, asserted. RAISE.
--   2. Archive — full pre-image of every row this file writes.
--   3. Renumber — §4.2 VERBATIM. Appendix `position` becomes
--      body_max + rank-within-appendix-stream.
--   4. Join the chains — last body chunk ↔ first appendix chunk.
--   5. Post-conditions — the §4.4 table, as RAISEs. A bad apply ABORTS.
--
-- WHY ───────────────────────────────────────────────────────────────────────
-- `position` is scoped PER STREAM, not per document. Every regulation's
-- appendix chunks restart at 1 alongside its body chunks, so the first ملحق
-- ALWAYS collides with the first body chunk — 1,184 regulations carry both
-- streams and all 1,184 are affected. That is structural, not coincidence.
-- 3,814 of 5,388 appendix chunks currently sit inside their document's body
-- position range; on 49 published pages that renders as body, appendix, body,
-- appendix the whole way down.
--
-- WHAT IS **NOT** CHANGING ──────────────────────────────────────────────────
-- ⚠ `corpus` STAYS. Do not "finish the job" by rewriting 'appendix' to a body
-- value. It drives the «(ملحق)» tag in unfold_reranker.py:273 and
-- ura/reg_adapter.py:106 (decision D13) — the agents are deliberately told when
-- they are reading an annex rather than operative text. Renumbering makes
-- appendices ORDER like body content; the label keeps them READABLE AS what
-- they are. Both properties are wanted, and §5 POST-6 asserts that this file
-- changed no `corpus` and no `chunk_ref`.
--
-- Also unchanged: ids (uuid5 of chunk_ref — renumbering touches no id and
-- therefore no foreign reference), `chunk_titles_v2` (§6: deliberately not
-- backfilled), `articles_v2` (0 rows own an appendix chunk), `seo_articles`
-- (0 rows point at one), and appendix-INTERNAL prev/next links (already a
-- correct chain).
--
-- WHY NO UNIQUE INDEX ON (regulation_id, position) ──────────────────────────
-- Considered and rejected. It would make a re-ingest that reintroduces
-- per-stream numbering fail loudly — but ingestion lives in the external
-- `agentic_for_ministry` project, and a constraint added from here that aborts
-- someone else's pipeline is a worse failure mode than the one it catches. The
-- guard against re-ingest is the READ path (`_ordered_chunk_query`, §5.1), kept
-- permanently for exactly this reason, plus the fact that this migration is
-- re-runnable.
--
-- IDEMPOTENT ────────────────────────────────────────────────────────────────
-- CREATE TABLE IF NOT EXISTS; archive INSERT ... ON CONFLICT (id) DO NOTHING
-- (so a re-run PRESERVES the original pre-image and does not overwrite it with
-- post-migration values); the renumber's `<>` guard (§3 below); the chain joins'
-- `IS DISTINCT FROM` guards. Safe to re-run after a partial re-ingest.
--
-- TRANSACTIONAL ─────────────────────────────────────────────────────────────
-- Explicit BEGIN/COMMIT, same as 067: the post-condition asserts in section 5
-- must be able to abort the renumber and the chain joins together. RUN THIS
-- FILE AS ONE SCRIPT — the temp tables are ON COMMIT DROP and section 5 reads
-- state written by sections 3 and 4. In the Supabase SQL editor the script is
-- already one implicit transaction, so `BEGIN` emits
-- "WARNING: there is already a transaction in progress" — that is expected and
-- harmless.
-- ════════════════════════════════════════════════════════════════════════════

BEGIN;

-- ════════════════════════════════════════════════════════════════════════════
-- 0. Preflight — privileges, baselines, and the two working sets
-- ════════════════════════════════════════════════════════════════════════════

-- 0a. Fail with a sentence rather than with a permission error 400 rows in.
--     `regulation_v2` is the pipeline's schema; this repo has written to it
--     before (067) but only as `postgres`, and only for RLS.
DO $$
BEGIN
    IF to_regclass('regulation_v2.chunks') IS NULL THEN
        RAISE EXCEPTION
          '121 PRE-0: regulation_v2.chunks does not exist. Wrong database, or '
          'the pipeline schema was renamed.';
    END IF;

    IF NOT has_schema_privilege(current_user, 'regulation_v2', 'CREATE') THEN
        RAISE EXCEPTION
          '121 PRE-0: role % cannot CREATE in schema regulation_v2, so the '
          'archive table cannot be built. Apply as postgres.', current_user;
    END IF;

    IF NOT has_table_privilege(current_user, 'regulation_v2.chunks', 'UPDATE') THEN
        RAISE EXCEPTION
          '121 PRE-0: role % cannot UPDATE regulation_v2.chunks. Apply as '
          'postgres.', current_user;
    END IF;
END $$;

-- 0b. Baseline capture. §4.4 wants "chunks_v2 row count unchanged at 48,390",
--     but hard-coding 48,390 would abort this migration the first time the
--     corpus legitimately grows. So the row count is captured HERE and asserted
--     against ITSELF in section 5 — that catches a lost row at any corpus size —
--     while the plan's 48,390 is reported as a NOTICE only.
--     is_local = false so the values survive even if this file is somehow run
--     statement-by-statement; they are session settings, not data.
SELECT set_config('mig121.rows_before',
                  (SELECT count(*)::text FROM regulation_v2.chunks), false);
SELECT set_config('mig121.apx_before',
                  (SELECT count(*)::text FROM regulation_v2.chunks
                    WHERE corpus = 'appendix'), false);

-- 0c. The affected set: regulations carrying BOTH streams. Expected 1,184.
--     A regulation with appendix chunks but NO body chunks is deliberately NOT
--     here — §4.2's inner join skips it, and correctly so: with no body stream
--     there is nothing for its 1..M numbering to collide with.
DROP TABLE IF EXISTS mig121_affected;
CREATE TEMP TABLE mig121_affected ON COMMIT DROP AS
SELECT c.regulation_id,
       count(*) FILTER (WHERE c.corpus =  'appendix') AS n_apx,
       count(*) FILTER (WHERE c.corpus <> 'appendix') AS n_body
  FROM regulation_v2.chunks c
 WHERE c.regulation_id IS NOT NULL
 GROUP BY c.regulation_id
HAVING count(*) FILTER (WHERE c.corpus =  'appendix') > 0
   AND count(*) FILTER (WHERE c.corpus <> 'appendix') > 0;

-- 0d. The chainable subset: affected regulations whose prev/next state is
--     exactly what §3 says it is — two disjoint, well-formed chains (one NULL
--     head and one NULL tail per stream, no cross-stream link).
--
--     WHY A SUBSET AND NOT A PRECONDITION: the renumber is 95% of the value and
--     is correct regardless of chain health. Refusing to renumber 5,388 rows
--     because ONE regulation has a sloppy linked list would be the wrong trade.
--     So chain damage that this migration did not create excludes that
--     regulation from section 4 and is REPORTED; it does not abort anything.
--     Expected: identical to mig121_affected, i.e. 1,184 rows, 0 excluded.
DROP TABLE IF EXISTS mig121_chainable;
CREATE TEMP TABLE mig121_chainable ON COMMIT DROP AS
SELECT a.regulation_id
  FROM mig121_affected a
  JOIN LATERAL (
        SELECT count(*) FILTER (WHERE c.next_chunk_id IS NULL) AS n_null_next,
               count(*) FILTER (WHERE c.prev_chunk_id IS NULL) AS n_null_prev
          FROM regulation_v2.chunks c
         WHERE c.regulation_id = a.regulation_id
  ) s ON true
 WHERE s.n_null_next = 2                     -- last body + last appendix
   AND s.n_null_prev = 2                     -- first body + first appendix
   -- and the two chains do not already reference each other (§3: 0 such links)
   AND NOT EXISTS (
        SELECT 1
          FROM regulation_v2.chunks x
          JOIN regulation_v2.chunks y
            ON y.id IN (x.next_chunk_id, x.prev_chunk_id)
         WHERE x.regulation_id = a.regulation_id
           AND y.regulation_id = a.regulation_id
           AND (x.corpus = 'appendix') <> (y.corpus = 'appendix')
   );

DO $$
DECLARE
    v_affected  bigint;
    v_chainable bigint;
    v_apx       bigint := current_setting('mig121.apx_before')::bigint;
    v_rows      bigint := current_setting('mig121.rows_before')::bigint;
    v_view      bigint;
BEGIN
    SELECT count(*) INTO v_affected  FROM mig121_affected;
    SELECT count(*) INTO v_chainable FROM mig121_chainable;
    SELECT count(*) INTO v_view      FROM public.chunks_v2;

    RAISE NOTICE '121 baseline: regulation_v2.chunks = % rows (plan: 48,390); '
                 'public.chunks_v2 = % rows; appendix rows = % (plan: 5,388); '
                 'regulations with both streams = % (plan: 1,184); chainable = %.',
                 v_rows, v_view, v_apx, v_affected, v_chainable;

    -- chunks_v2 is expected to be an unfiltered view over the base table. If it
    -- is not, every count the plan quotes was measured through a different
    -- population than the one this file writes. Not fatal — the renumber is
    -- right for the base table either way — but it must not pass silently.
    IF v_view <> v_rows THEN
        RAISE WARNING
          '121: public.chunks_v2 (%) <> regulation_v2.chunks (%). The view is '
          'FILTERING. Every plan figure was measured through the view; re-check '
          'them against the base table before trusting the deltas.', v_view, v_rows;
    END IF;

    IF v_chainable < v_affected THEN
        RAISE WARNING
          '121: % of % affected regulations have chain state that is not two '
          'clean disjoint chains (§3 says 0 do). They are RENUMBERED but their '
          'prev/next is left alone — see section 4.',
          v_affected - v_chainable, v_affected;
    END IF;
END $$;


-- ════════════════════════════════════════════════════════════════════════════
-- 1. Preconditions — the assumptions §4.2 rests on, asserted before any write
-- ════════════════════════════════════════════════════════════════════════════
-- §4.2 is reproduced VERBATIM in section 3, including its two `<>` comparisons.
-- `<>` is NULL-unsafe, and both NULL cases are silent corruption rather than an
-- error:
--   * a body chunk with corpus NULL is excluded from `body_max`, dragging bmax
--     DOWN and landing an appendix chunk on top of a live body position;
--   * an appendix chunk with position NULL still consumes a row_number slot but
--     `c.position <> r.new_position` evaluates NULL, so the row is silently NOT
--     updated and keeps its NULL.
-- Rewriting §4.2 to `IS DISTINCT FROM` would fix both — and would mean the SQL
-- that runs is not the SQL that was reviewed. PRE-1 and PRE-2 make the two forms
-- provably equivalent instead, and say so loudly if they ever stop being.
DO $$
DECLARE
    v_n bigint;
BEGIN
    -- PRE-1: no NULL corpus / position anywhere in an affected regulation.
    SELECT count(*) INTO v_n
      FROM regulation_v2.chunks c
      JOIN mig121_affected a ON a.regulation_id = c.regulation_id
     WHERE c.corpus IS NULL OR c.position IS NULL;

    IF v_n > 0 THEN
        RAISE EXCEPTION
          '121 PRE-1: % chunk(s) in affected regulations have a NULL corpus or '
          'a NULL position. §4.2 uses `<>`, which is NULL-blind: a NULL-corpus '
          'body row would lower body_max and collide, and a NULL-position '
          'appendix row would silently stay NULL. Fix the source rows first.',
          v_n;
    END IF;

    -- PRE-2: (position, chunk_ref) is unique within each appendix stream.
    --        This is THE property idempotency rests on — row_number() over a
    --        non-unique ORDER BY is not deterministic between runs, so two tied
    --        rows could swap positions on every re-run and the `<>` guard would
    --        never settle to a no-op.
    SELECT count(*) INTO v_n
      FROM (
        SELECT c.regulation_id
          FROM regulation_v2.chunks c
          JOIN mig121_affected a ON a.regulation_id = c.regulation_id
         WHERE c.corpus = 'appendix'
         GROUP BY c.regulation_id, c.position, c.chunk_ref
        HAVING count(*) > 1
      ) d;

    IF v_n > 0 THEN
        RAISE EXCEPTION
          '121 PRE-2: % appendix (regulation_id, position, chunk_ref) group(s) '
          'are not unique. The §4.2 window ORDER BY would be ambiguous and the '
          'renumber would not be idempotent.', v_n;
    END IF;

    -- PRE-3: §3's regularity claim, re-measured. NOT fatal — a holed body
    --        stream does not make the renumber wrong (appendices still land
    --        strictly above every body position), it only means the document
    --        cannot be contiguous 1..N afterwards either, and section 5 POST-2
    --        excludes it for exactly that reason. Report and continue.
    SELECT count(*) INTO v_n
      FROM (
        SELECT a.regulation_id
          FROM mig121_affected a
          JOIN regulation_v2.chunks c ON c.regulation_id = a.regulation_id
         GROUP BY a.regulation_id, a.n_body, a.n_apx
        HAVING min(c.position) FILTER (WHERE c.corpus <> 'appendix') <> 1
            OR max(c.position) FILTER (WHERE c.corpus <> 'appendix') <> a.n_body
            OR count(DISTINCT c.position) FILTER (WHERE c.corpus <> 'appendix') <> a.n_body
            OR min(c.position) FILTER (WHERE c.corpus =  'appendix') <> 1
            OR max(c.position) FILTER (WHERE c.corpus =  'appendix') <> a.n_apx
            OR count(DISTINCT c.position) FILTER (WHERE c.corpus =  'appendix') <> a.n_apx
      ) x;

    IF v_n > 0 THEN
        RAISE WARNING
          '121 PRE-3: % affected regulation(s) do NOT have both streams '
          'contiguous 1..N (§3 measured 0 of 1,184). They are still renumbered '
          'and still end up appendix-after-body; they are excluded from the '
          'POST-2 contiguity assert because their BODY stream was already holed '
          'and this migration does not touch body positions.', v_n;
    END IF;

    -- PRE-4 (report only): §8's two mid-document appendices. Their annexes are
    --       genuinely followed by more body text, and this renumbering moves
    --       them to the end. Both are unpublished and article-less, so nothing
    --       renders them today — but the operator should see them named.
    SELECT count(*) INTO v_n
      FROM regulation_v2.chunks c
     WHERE starts_with(c.chunk_ref, '17405_reg_645')
        OR starts_with(c.chunk_ref, '17636_reg_091');

    IF v_n > 0 THEN
        RAISE NOTICE
          '121 PRE-4: % chunk(s) belong to the two §8 mid-document appendices '
          '(17405_reg_645, 17636_reg_091). Their annexes move to the end of the '
          'document. Both are unpublished and article-less — expected, not a '
          'fault. Detection query: regulation_article_coverage_fallback.md §8b.',
          v_n;
    END IF;

    -- PRE-5 (report only): §3 says 0 articles_v2 rows and 0 seo_articles rows
    --       point at an appendix chunk, which is why the article and SEO layers
    --       are untouched by a position change. Re-measure rather than trust.
    BEGIN
        SELECT count(*) INTO v_n
          FROM public.articles_v2 av
          JOIN regulation_v2.chunks c ON c.id = av.chunk_parent_id
         WHERE c.corpus = 'appendix';
        IF v_n > 0 THEN
            RAISE WARNING
              '121 PRE-5: % articles_v2 row(s) are owned by an appendix chunk '
              '(§3 measured 0). Positions still move safely — ids do not — but '
              'the article layer now renders annex text; review after applying.',
              v_n;
        END IF;
    EXCEPTION WHEN undefined_table OR undefined_column THEN
        RAISE NOTICE '121 PRE-5: skipped (articles_v2/chunk_parent_id not '
                     'reachable from here: %).', SQLERRM;
    END;
END $$;


-- ════════════════════════════════════════════════════════════════════════════
-- 2. Archive — the full pre-image of every row this file writes (§4.1)
-- ════════════════════════════════════════════════════════════════════════════
-- This is the restore path. It is populated BEFORE anything moves, and it holds
-- both halves of the write set: the 5,388 appendix chunks whose `position`
-- changes, and the 1,184 last-body chunks whose `next_chunk_id` changes.
--
-- The table name keeps the plan's 20260808 date suffix, NOT the migration
-- number — the plan, the archive and the rollback statement all name it, and
-- renaming it because the file number moved would break three references to
-- save nothing.
--
-- ON CONFLICT (id) DO NOTHING is the idempotency contract, and it points the
-- right way: on a re-run the archive KEEPS the original pre-migration values
-- rather than overwriting them with post-migration ones. A second run must not
-- be able to destroy the only copy of the state before the first.
CREATE TABLE IF NOT EXISTS regulation_v2.chunk_position_archive_20260808 (
    id                uuid PRIMARY KEY,
    regulation_id     uuid,
    chunk_ref         text,
    corpus            text,
    old_position      int,
    old_prev_chunk_id uuid,
    old_next_chunk_id uuid,
    archived_at       timestamptz DEFAULT now()
);

COMMENT ON TABLE regulation_v2.chunk_position_archive_20260808 IS
  'Pre-image of every chunk row rewritten by migration 121 (appendix position '
  'unification — the plan calls it 120; the file number moved after a '
  'collision). Holds all appendix chunks plus each affected regulation''s last '
  'body chunk. THE restore path — the restore statement is at the bottom of '
  '121_chunk_appendix_position_unification.sql. Rows are written ON CONFLICT DO '
  'NOTHING, so this is the state BEFORE the FIRST apply no matter how many '
  'times the migration is re-run. Do not drop until §7 step 5 has passed and a '
  'full re-ingest cycle has been observed to survive.';

-- 2a. Every appendix chunk in the corpus. Deliberately NOT restricted to the
--     rows that actually move: 1,574 of the 5,388 already sit on the position
--     the renumber would give them, and an archive that omits them is not a
--     pre-image you can restore from.
INSERT INTO regulation_v2.chunk_position_archive_20260808
       (id, regulation_id, chunk_ref, corpus,
        old_position, old_prev_chunk_id, old_next_chunk_id)
SELECT c.id, c.regulation_id, c.chunk_ref, c.corpus,
       c.position, c.prev_chunk_id, c.next_chunk_id
  FROM regulation_v2.chunks c
 WHERE c.corpus = 'appendix'
ON CONFLICT (id) DO NOTHING;

-- 2b. The last body chunk of every affected regulation — the only body rows
--     this migration writes, and only their `next_chunk_id`. Identified the
--     same way section 4 identifies them, so the two sets cannot disagree.
INSERT INTO regulation_v2.chunk_position_archive_20260808
       (id, regulation_id, chunk_ref, corpus,
        old_position, old_prev_chunk_id, old_next_chunk_id)
SELECT DISTINCT ON (c.regulation_id)
       c.id, c.regulation_id, c.chunk_ref, c.corpus,
       c.position, c.prev_chunk_id, c.next_chunk_id
  FROM regulation_v2.chunks c
  JOIN mig121_affected a ON a.regulation_id = c.regulation_id
 WHERE c.corpus <> 'appendix'
 ORDER BY c.regulation_id, c.position DESC, c.chunk_ref DESC
ON CONFLICT (id) DO NOTHING;

DO $$
DECLARE
    v_n bigint;
BEGIN
    SELECT count(*) INTO v_n FROM regulation_v2.chunk_position_archive_20260808;
    RAISE NOTICE '121: archive holds % row(s) (plan: 5,388 appendix + 1,184 '
                 'last-body = 6,572). A re-run leaves this unchanged.', v_n;

    -- The archive is the restore path; if it is empty the migration must not
    -- proceed, whatever else is true.
    IF v_n = 0 THEN
        RAISE EXCEPTION '121: archive is empty after populating it — refusing '
                        'to renumber without a pre-image.';
    END IF;
END $$;


-- ════════════════════════════════════════════════════════════════════════════
-- 3. Renumber — §4.2, VERBATIM
-- ════════════════════════════════════════════════════════════════════════════
-- Reproduced exactly as the plan reviewed it. Two properties carry the whole
-- migration and neither is obvious from reading the SQL:
--
-- IDEMPOTENT BY CONSTRUCTION. `bmax` is computed from BODY rows only
--   (`corpus <> 'appendix'`), and this statement never writes a body row, so
--   bmax cannot move. The target is `bmax + rank-within-the-appendix-stream`,
--   and rank is order-preserving: re-running lands every row on the position it
--   already holds, and the `c.position <> r.new_position` guard turns that into
--   an UPDATE of 0 rows. That is why this is safe to re-run after a partial
--   re-ingest — and it is also why `bmax` must NEVER be changed to
--   `max(position)` over all rows: that version feeds its own output back in
--   and pushes the appendix further down the document on every run.
--
-- THE ORDER KEY IS `(position, chunk_ref)`. Not `(position)` — that is not
--   unique within an appendix stream in the general case and would make
--   row_number() non-deterministic. Not `(position, id)` — ids are uuid5 of
--   chunk_ref, so they sort pseudo-randomly and would order ملحق 10 before
--   ملحق 2. `(position, chunk_ref)` is the SAME tiebreaker `_ordered_chunk_query`
--   uses at read time (library_service.py), which is the point: data order and
--   read order cannot disagree, so after this migration the read-time ordering
--   is a no-op guard rather than the thing doing the work.
--
-- The `WHERE c.position <> r.new_position` no-op guard is load-bearing and
-- must stay. PRE-1 above guarantees no NULL operand reaches it.
WITH body_max AS (
  SELECT regulation_id, max(position) AS bmax
  FROM regulation_v2.chunks WHERE corpus <> 'appendix' GROUP BY 1
), ranked AS (
  SELECT c.id, b.bmax + row_number() OVER (
           PARTITION BY c.regulation_id ORDER BY c.position, c.chunk_ref
         ) AS new_position
  FROM regulation_v2.chunks c JOIN body_max b USING (regulation_id)
  WHERE c.corpus = 'appendix'
)
UPDATE regulation_v2.chunks c SET position = r.new_position
FROM ranked r WHERE c.id = r.id AND c.position <> r.new_position;


-- ════════════════════════════════════════════════════════════════════════════
-- 4. Join the chains (§4.3) — one walkable document
-- ════════════════════════════════════════════════════════════════════════════
-- `lb` = the body chunk at the regulation's maximum body position.
-- `fa` = the appendix chunk at the (now post-renumber) minimum appendix
--        position, which after section 3 is exactly bmax + 1.
-- Appendix-INTERNAL links are already a correct chain and are not touched;
-- only the two endpoints that were NULL on both sides of the seam are written.
--
-- `IS DISTINCT FROM` rather than `<>` is REQUIRED here, not stylistic: before
-- this migration the last body chunk's next_chunk_id is NULL, and `NULL <> uuid`
-- is NULL, so a `<>` guard would skip every single row and the chains would
-- never join. It is still a no-op on a re-run, which is what the guard is for.
--
-- Scoped to mig121_chainable (section 0d), so a regulation whose linked list
-- was already malformed keeps its renumbered positions and its old chain rather
-- than gaining a fork.

-- 4a. last body chunk → first appendix chunk
WITH lb AS (
    SELECT DISTINCT ON (c.regulation_id) c.regulation_id, c.id
      FROM regulation_v2.chunks c
      JOIN mig121_chainable k ON k.regulation_id = c.regulation_id
     WHERE c.corpus <> 'appendix'
     ORDER BY c.regulation_id, c.position DESC, c.chunk_ref DESC
), fa AS (
    SELECT DISTINCT ON (c.regulation_id) c.regulation_id, c.id
      FROM regulation_v2.chunks c
      JOIN mig121_chainable k ON k.regulation_id = c.regulation_id
     WHERE c.corpus = 'appendix'
     ORDER BY c.regulation_id, c.position, c.chunk_ref
)
UPDATE regulation_v2.chunks t
   SET next_chunk_id = fa.id
  FROM lb JOIN fa ON fa.regulation_id = lb.regulation_id
 WHERE t.id = lb.id
   AND t.next_chunk_id IS DISTINCT FROM fa.id;

-- 4b. first appendix chunk → last body chunk
WITH lb AS (
    SELECT DISTINCT ON (c.regulation_id) c.regulation_id, c.id
      FROM regulation_v2.chunks c
      JOIN mig121_chainable k ON k.regulation_id = c.regulation_id
     WHERE c.corpus <> 'appendix'
     ORDER BY c.regulation_id, c.position DESC, c.chunk_ref DESC
), fa AS (
    SELECT DISTINCT ON (c.regulation_id) c.regulation_id, c.id
      FROM regulation_v2.chunks c
      JOIN mig121_chainable k ON k.regulation_id = c.regulation_id
     WHERE c.corpus = 'appendix'
     ORDER BY c.regulation_id, c.position, c.chunk_ref
)
UPDATE regulation_v2.chunks t
   SET prev_chunk_id = lb.id
  FROM lb JOIN fa ON fa.regulation_id = lb.regulation_id
 WHERE t.id = fa.id
   AND t.prev_chunk_id IS DISTINCT FROM lb.id;


-- ════════════════════════════════════════════════════════════════════════════
-- 5. Post-conditions — the §4.4 table, as ASSERTS
-- ════════════════════════════════════════════════════════════════════════════
-- These RAISE rather than SELECT on purpose: inside the BEGIN/COMMIT above, a
-- violation rolls the whole migration back instead of reporting a broken corpus
-- that has already been committed. §4.4 is titled "assert, don't hope".
--
-- Where a check is scoped, the scope is always "damage this migration could
-- have caused", never "the corpus is perfect" — a pre-existing hole in a body
-- stream is not this file's to fix and must not block a correct renumber. Those
-- cases are reported as WARNINGs instead.
DO $$
DECLARE
    v_n         bigint;
    v_rows_now  bigint;
    v_rows_pre  bigint := current_setting('mig121.rows_before')::bigint;
    v_apx_now   bigint;
    v_apx_pre   bigint := current_setting('mig121.apx_before')::bigint;
BEGIN
    -- POST-1 — duplicate (regulation_id, position) groups involving an appendix
    --          row. §4.4 expects 0, down from 3,814. HARD: this is precisely
    --          the damage the migration exists to remove.
    SELECT count(*) INTO v_n
      FROM (
        SELECT c.regulation_id, c.position
          FROM regulation_v2.chunks c
         WHERE c.regulation_id IS NOT NULL
         GROUP BY c.regulation_id, c.position
        HAVING count(*) > 1
           AND count(*) FILTER (WHERE c.corpus = 'appendix') > 0
      ) d;

    IF v_n > 0 THEN
        RAISE EXCEPTION
          '121 POST-1 FAILED: % (regulation_id, position) group(s) still '
          'contain an appendix chunk sharing a position. Expected 0 (from '
          '3,814). Rolling back.', v_n;
    END IF;

    -- POST-1b — duplicates that are body-only. Not caused here (this file never
    --           writes a body position) and not fixable here. Report.
    SELECT count(*) INTO v_n
      FROM (
        SELECT c.regulation_id, c.position
          FROM regulation_v2.chunks c
         WHERE c.regulation_id IS NOT NULL
         GROUP BY c.regulation_id, c.position
        HAVING count(*) > 1
      ) d;

    IF v_n > 0 THEN
        RAISE WARNING
          '121 POST-1b: % duplicate (regulation_id, position) group(s) remain '
          'corpus-wide, all body-internal (POST-1 proved none involve an '
          'appendix). Pre-existing; migration 121 does not write body positions.',
          v_n;
    END IF;

    -- POST-2 — every affected regulation is contiguous 1..(n_body + n_apx).
    --          Scoped to regulations whose BODY stream was already contiguous
    --          1..n_body: row_number() always emits a dense 1..M, so after the
    --          renumber the appendix stream is dense by construction and the
    --          ONLY possible source of a gap is a pre-existing hole in the body
    --          stream (reported by PRE-3). HARD within that scope.
    SELECT count(*) INTO v_n
      FROM (
        SELECT a.regulation_id
          FROM mig121_affected a
          JOIN regulation_v2.chunks c ON c.regulation_id = a.regulation_id
         GROUP BY a.regulation_id, a.n_body, a.n_apx
        HAVING min(c.position) FILTER (WHERE c.corpus <> 'appendix') = 1
           AND max(c.position) FILTER (WHERE c.corpus <> 'appendix') = a.n_body
           AND count(DISTINCT c.position) FILTER (WHERE c.corpus <> 'appendix') = a.n_body
           AND (   min(c.position)            <> 1
                OR max(c.position)            <> a.n_body + a.n_apx
                OR count(DISTINCT c.position) <> a.n_body + a.n_apx )
      ) x;

    IF v_n > 0 THEN
        RAISE EXCEPTION
          '121 POST-2 FAILED: % regulation(s) with a clean body stream are not '
          'contiguous 1..(n_body + n_apx) after renumbering. Expected 0. '
          'Rolling back.', v_n;
    END IF;

    -- POST-3 — no appendix chunk sits at or below its document's maximum body
    --          position. §4.4 expects 0, down from 3,814. HARD.
    SELECT count(*) INTO v_n
      FROM regulation_v2.chunks c
      JOIN LATERAL (
            SELECT max(b.position) AS bmax
              FROM regulation_v2.chunks b
             WHERE b.regulation_id = c.regulation_id
               AND b.corpus <> 'appendix'
      ) m ON true
     WHERE c.corpus = 'appendix'
       AND m.bmax IS NOT NULL
       AND c.position <= m.bmax;

    IF v_n > 0 THEN
        RAISE EXCEPTION
          '121 POST-3 FAILED: % appendix chunk(s) still sit inside their '
          'document''s body position range. Expected 0 (from 3,814). Rolling '
          'back.', v_n;
    END IF;

    -- POST-4 — exactly one NULL next_chunk_id (last appendix) and one NULL
    --          prev_chunk_id (first body) per JOINED regulation. HARD over
    --          mig121_chainable, which is the set section 4 actually wrote.
    SELECT count(*) INTO v_n
      FROM (
        SELECT c.regulation_id
          FROM regulation_v2.chunks c
          JOIN mig121_chainable k ON k.regulation_id = c.regulation_id
         GROUP BY c.regulation_id
        HAVING count(*) FILTER (WHERE c.next_chunk_id IS NULL) <> 1
            OR count(*) FILTER (WHERE c.prev_chunk_id IS NULL) <> 1
      ) x;

    IF v_n > 0 THEN
        RAISE EXCEPTION
          '121 POST-4 FAILED: % regulation(s) do not have exactly one NULL '
          'next_chunk_id and one NULL prev_chunk_id after the chain join. The '
          'two streams were supposed to become one walkable document. Rolling '
          'back.', v_n;
    END IF;

    -- POST-4b — the chain join must point at the seam it was aimed at: the
    --           joined pair must be (max body position) → (min appendix
    --           position), and the link must be symmetric.
    SELECT count(*) INTO v_n
      FROM mig121_chainable k
      JOIN LATERAL (
            SELECT c.id, c.next_chunk_id
              FROM regulation_v2.chunks c
             WHERE c.regulation_id = k.regulation_id AND c.corpus <> 'appendix'
             ORDER BY c.position DESC, c.chunk_ref DESC LIMIT 1
      ) lb ON true
      JOIN LATERAL (
            SELECT c.id, c.prev_chunk_id
              FROM regulation_v2.chunks c
             WHERE c.regulation_id = k.regulation_id AND c.corpus = 'appendix'
             ORDER BY c.position, c.chunk_ref LIMIT 1
      ) fa ON true
     WHERE lb.next_chunk_id IS DISTINCT FROM fa.id
        OR fa.prev_chunk_id IS DISTINCT FROM lb.id;

    IF v_n > 0 THEN
        RAISE EXCEPTION
          '121 POST-4b FAILED: % regulation(s) have an asymmetric or misaimed '
          'body→appendix seam. Rolling back.', v_n;
    END IF;

    -- POST-5 — row count unchanged. An UPDATE cannot change it, which is the
    --          point: if this fires, something other than an UPDATE ran.
    SELECT count(*) INTO v_rows_now FROM regulation_v2.chunks;
    IF v_rows_now <> v_rows_pre THEN
        RAISE EXCEPTION
          '121 POST-5 FAILED: regulation_v2.chunks went from % rows to %. '
          'Migration 121 only UPDATEs. Rolling back.', v_rows_pre, v_rows_now;
    END IF;
    IF v_rows_now <> 48390 THEN
        RAISE NOTICE
          '121 POST-5: row count is % — the plan''s §4.4 baseline was 48,390. '
          'Unchanged by this migration either way; the corpus has simply moved '
          'since the plan was measured.', v_rows_now;
    END IF;

    -- POST-6 — `corpus` is not cosmetic (§2). Prove this file changed no
    --          corpus and no chunk_ref, by diffing every archived row against
    --          its live self. Also proves no archived row was deleted.
    SELECT count(*) INTO v_apx_now
      FROM regulation_v2.chunks WHERE corpus = 'appendix';
    IF v_apx_now <> v_apx_pre THEN
        RAISE EXCEPTION
          '121 POST-6 FAILED: appendix row count went from % to %. `corpus` '
          'must not be rewritten (§2 — it drives the «(ملحق)» tag in '
          'unfold_reranker.py:273 and ura/reg_adapter.py:106). Rolling back.',
          v_apx_pre, v_apx_now;
    END IF;

    SELECT count(*) INTO v_n
      FROM regulation_v2.chunk_position_archive_20260808 ar
      LEFT JOIN regulation_v2.chunks c ON c.id = ar.id
     WHERE c.id IS NULL
        OR c.chunk_ref     IS DISTINCT FROM ar.chunk_ref
        OR c.corpus        IS DISTINCT FROM ar.corpus
        OR c.regulation_id IS DISTINCT FROM ar.regulation_id;

    IF v_n > 0 THEN
        RAISE EXCEPTION
          '121 POST-6 FAILED: % archived row(s) lost their id, chunk_ref, '
          'corpus or regulation_id. This migration writes position, '
          'prev_chunk_id and next_chunk_id and nothing else. Rolling back.', v_n;
    END IF;

    RAISE NOTICE '121: all post-conditions passed. Committing.';
END $$;

COMMIT;

-- Optional, if the applying role owns the table: refresh planner stats after
-- moving ~3,814 `position` values. Left commented so a privilege error cannot
-- print a red failure after a green migration.
-- ANALYZE regulation_v2.chunks;


-- ════════════════════════════════════════════════════════════════════════════
-- DRY RUN — READ-ONLY. RUN THIS **BEFORE** APPLYING ANYTHING ABOVE.
-- ════════════════════════════════════════════════════════════════════════════
-- Reproduces every figure in plan §1, §3 and §4.4 against live data, and proves
-- the idempotency claim empirically. Nothing here writes. If any number
-- disagrees with the plan, STOP.
--
-- -- D1. Baselines. EXPECT: chunks 48,390 · appendix 5,388 · both-stream regs
-- --     1,184 · duplicate position groups 3,814 · appendix-inside-body 3,814.
-- --     chunks_v2 should equal the base table; if it does not, the view filters
-- --     and every plan figure was measured through a different population.
-- SELECT
--   (SELECT count(*) FROM regulation_v2.chunks)                            AS chunks_total,
--   (SELECT count(*) FROM public.chunks_v2)                                AS chunks_v2_total,
--   (SELECT count(*) FROM regulation_v2.chunks c WHERE c.corpus = 'appendix') AS appendix_rows,
--   (SELECT count(*) FROM (
--        SELECT c.regulation_id FROM regulation_v2.chunks c
--         WHERE c.regulation_id IS NOT NULL GROUP BY c.regulation_id
--        HAVING count(*) FILTER (WHERE c.corpus =  'appendix') > 0
--           AND count(*) FILTER (WHERE c.corpus <> 'appendix') > 0) x)      AS regs_both_streams,
--   (SELECT count(*) FROM (
--        SELECT c.regulation_id, c.position FROM regulation_v2.chunks c
--         WHERE c.regulation_id IS NOT NULL
--         GROUP BY c.regulation_id, c.position HAVING count(*) > 1) d)      AS dup_position_groups,
--   (SELECT count(*) FROM regulation_v2.chunks a
--       JOIN (SELECT c.regulation_id, max(c.position) AS bmax
--               FROM regulation_v2.chunks c WHERE c.corpus <> 'appendix'
--              GROUP BY c.regulation_id) b ON b.regulation_id = a.regulation_id
--      WHERE a.corpus = 'appendix' AND a.position <= b.bmax)                AS appendix_inside_body;
--
-- -- D2. How much the renumber moves. EXPECT: appendix_in_scope 5,388 ·
-- --     would_move 3,814 · already_correct 1,574.
-- WITH body_max AS (
--   SELECT regulation_id, max(position) AS bmax
--   FROM regulation_v2.chunks WHERE corpus <> 'appendix' GROUP BY 1
-- ), ranked AS (
--   SELECT c.id, b.bmax + row_number() OVER (
--            PARTITION BY c.regulation_id ORDER BY c.position, c.chunk_ref
--          ) AS new_position
--   FROM regulation_v2.chunks c JOIN body_max b USING (regulation_id)
--   WHERE c.corpus = 'appendix'
-- )
-- SELECT count(*)                                             AS appendix_in_scope,
--        count(*) FILTER (WHERE c.position <> r.new_position) AS would_move,
--        count(*) FILTER (WHERE c.position =  r.new_position) AS already_correct
--   FROM regulation_v2.chunks c JOIN ranked r ON r.id = c.id;
--
-- -- D3. IDEMPOTENCY, empirically: compute pass 1, materialise the table AS IF
-- --     pass 1 had been applied, then compute pass 2 over that.
-- --     EXPECT second_pass_moves = 0 and second_pass_scope = 5,388.
-- WITH body_max AS (
--   SELECT regulation_id, max(position) AS bmax
--   FROM regulation_v2.chunks WHERE corpus <> 'appendix' GROUP BY 1
-- ), ranked1 AS (
--   SELECT c.id, b.bmax + row_number() OVER (
--            PARTITION BY c.regulation_id ORDER BY c.position, c.chunk_ref
--          ) AS new_position
--   FROM regulation_v2.chunks c JOIN body_max b USING (regulation_id)
--   WHERE c.corpus = 'appendix'
-- ), after1 AS (
--   SELECT c.id, c.regulation_id, c.chunk_ref, c.corpus,
--          COALESCE(r.new_position, c.position) AS position
--     FROM regulation_v2.chunks c LEFT JOIN ranked1 r ON r.id = c.id
-- ), body_max2 AS (
--   SELECT a.regulation_id, max(a.position) AS bmax
--     FROM after1 a WHERE a.corpus <> 'appendix' GROUP BY a.regulation_id
-- ), ranked2 AS (
--   SELECT a.id, a.position AS cur_position, b.bmax + row_number() OVER (
--            PARTITION BY a.regulation_id ORDER BY a.position, a.chunk_ref
--          ) AS new_position
--     FROM after1 a JOIN body_max2 b ON b.regulation_id = a.regulation_id
--    WHERE a.corpus = 'appendix'
-- )
-- SELECT count(*)                                            AS second_pass_scope,
--        count(*) FILTER (WHERE cur_position <> new_position) AS second_pass_moves
--   FROM ranked2;
--
-- -- D4. The labour لائحة, 17900_reg_128_p2 — 31 body + 29 appendix.
-- --     EXPECT: body positions 1..31 unchanged, appendix 1..29 → 32..60.
-- WITH body_max AS (
--   SELECT c.regulation_id, max(c.position) AS bmax
--     FROM regulation_v2.chunks c WHERE c.corpus <> 'appendix'
--    GROUP BY c.regulation_id
-- )
-- SELECT c.corpus, c.chunk_ref, c.position AS old_position,
--        CASE WHEN c.corpus = 'appendix'
--             THEN b.bmax + row_number() OVER (
--                    PARTITION BY c.regulation_id, c.corpus
--                    ORDER BY c.position, c.chunk_ref)
--             ELSE c.position END AS new_position
--   FROM regulation_v2.chunks c
--   LEFT JOIN body_max b ON b.regulation_id = c.regulation_id
--  WHERE c.regulation_id = 'be7a89c5-04f3-4546-8c04-c1c543ef06ff'
--  ORDER BY c.corpus DESC, c.position, c.chunk_ref;
--
-- -- D5. §3's regularity claim. EXPECT: regs_both 1,184 · body_contiguous 1,184
-- --     · apx_contiguous 1,184 · irregular 0.
-- WITH s AS (
--   SELECT c.regulation_id,
--          count(*) FILTER (WHERE c.corpus =  'appendix')                    AS n_apx,
--          count(*) FILTER (WHERE c.corpus <> 'appendix')                    AS n_body,
--          min(c.position) FILTER (WHERE c.corpus =  'appendix')             AS apx_min,
--          max(c.position) FILTER (WHERE c.corpus =  'appendix')             AS apx_max,
--          count(DISTINCT c.position) FILTER (WHERE c.corpus =  'appendix')  AS apx_distinct,
--          min(c.position) FILTER (WHERE c.corpus <> 'appendix')             AS body_min,
--          max(c.position) FILTER (WHERE c.corpus <> 'appendix')             AS body_max,
--          count(DISTINCT c.position) FILTER (WHERE c.corpus <> 'appendix')  AS body_distinct
--     FROM regulation_v2.chunks c WHERE c.regulation_id IS NOT NULL
--    GROUP BY c.regulation_id
-- )
-- SELECT count(*) AS regs_both,
--        count(*) FILTER (WHERE body_min = 1 AND body_max = n_body
--                           AND body_distinct = n_body)              AS body_contiguous,
--        count(*) FILTER (WHERE apx_min = 1 AND apx_max = n_apx
--                           AND apx_distinct = n_apx)                AS apx_contiguous,
--        count(*) FILTER (WHERE NOT (body_min = 1 AND body_max = n_body
--                                      AND body_distinct = n_body)
--                            OR NOT (apx_min = 1 AND apx_max = n_apx
--                                      AND apx_distinct = n_apx))    AS irregular
--   FROM s WHERE n_apx > 0 AND n_body > 0;
--
-- -- D6. The NULL hazards PRE-1/PRE-2 abort on, and the appendix-only
-- --     regulations §4.2's inner join skips. EXPECT all four columns 0.
-- SELECT
--   (SELECT count(*) FROM regulation_v2.chunks c
--     WHERE c.corpus IS NULL OR c.position IS NULL
--       OR c.regulation_id IS NULL)                                  AS null_key_rows,
--   (SELECT count(*) FROM (
--       SELECT c.regulation_id FROM regulation_v2.chunks c
--        WHERE c.corpus = 'appendix'
--        GROUP BY c.regulation_id, c.position, c.chunk_ref
--       HAVING count(*) > 1) d)                                      AS ambiguous_order_keys,
--   (SELECT count(*) FROM (
--       SELECT c.regulation_id FROM regulation_v2.chunks c
--        WHERE c.regulation_id IS NOT NULL GROUP BY c.regulation_id
--       HAVING count(*) FILTER (WHERE c.corpus =  'appendix') > 0
--          AND count(*) FILTER (WHERE c.corpus <> 'appendix') = 0) x) AS appendix_only_regs,
--   (SELECT count(*) FROM regulation_v2.chunks x
--      JOIN regulation_v2.chunks y ON y.id IN (x.next_chunk_id, x.prev_chunk_id)
--     WHERE x.regulation_id = y.regulation_id
--       AND (x.corpus = 'appendix') <> (y.corpus = 'appendix'))       AS cross_stream_links;
--
-- -- D7. Chain shape before the migration. EXPECT: every affected regulation has
-- --     exactly 2 NULL next and 2 NULL prev (one per stream) — i.e. rows = 0.
-- SELECT count(*) AS regs_with_unexpected_chain_shape FROM (
--   SELECT c.regulation_id
--     FROM regulation_v2.chunks c
--    WHERE c.regulation_id IN (
--          SELECT c2.regulation_id FROM regulation_v2.chunks c2
--           WHERE c2.regulation_id IS NOT NULL GROUP BY c2.regulation_id
--          HAVING count(*) FILTER (WHERE c2.corpus =  'appendix') > 0
--             AND count(*) FILTER (WHERE c2.corpus <> 'appendix') > 0)
--    GROUP BY c.regulation_id
--   HAVING count(*) FILTER (WHERE c.next_chunk_id IS NULL) <> 2
--       OR count(*) FILTER (WHERE c.prev_chunk_id IS NULL) <> 2
-- ) x;


-- ════════════════════════════════════════════════════════════════════════════
-- POST-APPLY — §7 step 5. Re-run the §4.4 checks from OUTSIDE the transaction.
-- ════════════════════════════════════════════════════════════════════════════
-- Section 5 already asserted all of this inside the transaction; this is the
-- independent confirmation on committed data. Every count must be 0 except the
-- last two.
--
-- SELECT
--   (SELECT count(*) FROM (
--       SELECT c.regulation_id, c.position FROM regulation_v2.chunks c
--        WHERE c.regulation_id IS NOT NULL
--        GROUP BY c.regulation_id, c.position HAVING count(*) > 1) d)   AS dup_position_groups,
--   (SELECT count(*) FROM regulation_v2.chunks c
--      JOIN LATERAL (SELECT max(b.position) AS bmax FROM regulation_v2.chunks b
--                     WHERE b.regulation_id = c.regulation_id
--                       AND b.corpus <> 'appendix') m ON true
--     WHERE c.corpus = 'appendix' AND m.bmax IS NOT NULL
--       AND c.position <= m.bmax)                                       AS appendix_inside_body,
--   (SELECT count(*) FROM (
--       SELECT c.regulation_id FROM regulation_v2.chunks c
--        WHERE c.regulation_id IN (SELECT regulation_id
--                                    FROM regulation_v2.chunk_position_archive_20260808)
--        GROUP BY c.regulation_id
--       HAVING count(*) FILTER (WHERE c.next_chunk_id IS NULL) <> 1
--           OR count(*) FILTER (WHERE c.prev_chunk_id IS NULL) <> 1) x)  AS bad_chain_regs,
--   (SELECT count(*) FROM public.chunks_v2)                             AS chunks_v2_rows,
--   (SELECT count(*) FROM regulation_v2.chunk_position_archive_20260808) AS archived_rows;
--
-- -- Then §7 step 5 proper: the payload of GET /regulations/<slug> must be
-- -- BYTE-IDENTICAL before and after this migration. The read path
-- -- (`_ordered_chunk_query`) was already producing the correct order; if the
-- -- payload changed, the read path and the data disagree and one of them is
-- -- wrong. Spot-check 17900_reg_128_p2 (labour لائحة, 29 appendices) and
-- -- وثيقة الضمان الصحي الأساسية (18).
--
-- -- And confirm the two §5.2 consumers still on raw `position` were fixed in
-- -- the same pass: ask_service.py `_ground_regulation` and
-- -- scripts/build_seo_article_index.py (both the document walk and the
-- -- (regulation_id, position) pagination, which needs `chunk_ref` as a unique
-- -- tiebreaker or rows duplicate/drop across page boundaries).


-- ════════════════════════════════════════════════════════════════════════════
-- ROLLBACK — restore from the archive. Do NOT run unless reverting.
-- ════════════════════════════════════════════════════════════════════════════
-- Restores position, prev_chunk_id and next_chunk_id to their pre-FIRST-apply
-- values for every row this migration ever wrote. It does not drop the archive;
-- do that by hand once the revert is confirmed. Note that reverting the DATA
-- without also reverting the read-time ordering fix simply returns to the
-- interleaved-on-disk / correct-on-read state, which is where §7 step 1 leaves
-- the system — that is a safe place to sit.
--
-- BEGIN;
-- UPDATE regulation_v2.chunks c
--    SET position      = ar.old_position,
--        prev_chunk_id = ar.old_prev_chunk_id,
--        next_chunk_id = ar.old_next_chunk_id
--   FROM regulation_v2.chunk_position_archive_20260808 ar
--  WHERE c.id = ar.id
--    AND (c.position      IS DISTINCT FROM ar.old_position
--      OR c.prev_chunk_id IS DISTINCT FROM ar.old_prev_chunk_id
--      OR c.next_chunk_id IS DISTINCT FROM ar.old_next_chunk_id);
-- COMMIT;
