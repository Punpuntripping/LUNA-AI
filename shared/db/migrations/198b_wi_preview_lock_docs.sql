-- ============================================================================
-- Migration 198b — product_docs: the sentences the WI preview lock makes false
--
-- Source: .claude/plans/wi_preview_lock.md §10 (copy) + §11 step 4.
--
-- ⚠ APPLY ONLY AT THE FLIP — right before WI_PREVIEW_LOCK_ENABLED goes on
-- (§11 step 5), never with 198. The router reads these rows through
-- open_rayhan_page before answering anything about ريحان itself; until the flag
-- is on these additions would be false the other way round. Rows are cached
-- in-process for up to 600 s (agents/tool_repository/rayhan_docs.py), so a
-- running backend serves the new text within ten minutes.
-- To UNDO at a flag-off: the reverse replace is in the ROLLBACK block below.
--
-- WHAT CHANGES — additions only, appended right after the sentence each one
-- qualifies; no live sentence is removed or rewritten. Every old sentence
-- below was COPIED from the live rows read on 2026-10-30 (SELECT only), and
-- each row is guarded by the md5 of the WHOLE live row from that read: a row
-- that changed since is left alone with a NOTICE (paste by hand), never a
-- failure. Wording rules kept:
--   * no digit anywhere in the additions — the rule every pricing section
--     since M126 / 140 / 183 / 192 follows, so the share is said in words
--     («الجزء الأول من نصّها»); the exact share is the router marker's job (§6);
--   * no amount, no plan price, no URL, never «المشتركون الأوائل»;
--   * the lock is called deliberate (owner requirement §6), and what stays
--     whole is said: items made during a paid subscription.
--
--   pricing       P1 the free plan line          + what free users see
--                 P2 «ما لا يُخصم منه شيء أبداً»   + no share / export of a preview
--   usage_limits  U1 the free plan line          + what free users see
--                 U2 «ما لا يُخصم منه شيء»        + no share / export of a preview
--   workspace     W1 «البطاقة مفتوحة — كاملاً»    + the preview, deliberate
--                 W2 «حرّر وقيّم»                  + a preview is read-only
--                 W3 «شارك خارج ريحان»            + no share / blog / export
--   guide         G1 «حرّرها بنفسك»               + a preview is read-only
--                 G2 «شارك أو صدّر»               + except a preview
--   how_it_works  H1 the researcher's report     + previewed on free
--                 H2 the writer's draft          + previewed on free
--
-- IDEMPOTENT: an addition already present is skipped (re-run = no-op).
--
-- VERIFY AFTER APPLY (every row must report present = true):
--   SELECT d.doc_key, position('معاينةً' IN d.content_md) > 0 AS present,
--          length(d.content_md), md5(d.content_md)
--     FROM public.product_docs d
--    WHERE d.doc_key IN ('pricing','usage_limits','workspace','guide','how_it_works');
--
-- ROLLBACK (flag back off): for each (doc_key, old, add) below,
--   UPDATE public.product_docs SET content_md = replace(content_md, old || add, old)
--    WHERE doc_key = <doc_key>;
-- ============================================================================

BEGIN;

CREATE TEMP TABLE m198b_edits (
    ord      int  NOT NULL,
    doc_key  text NOT NULL,
    live_md5 text NOT NULL,
    old_text text NOT NULL,
    add_text text NOT NULL
) ON COMMIT DROP;

INSERT INTO m198b_edits (ord, doc_key, live_md5, old_text, add_text) VALUES
-- ── pricing (length 3077) ──
(1, 'pricing', 'e123ce52660612e653f2f9ba5c030c0b',
 '**المجانية** — للتجربة، بحدّ يكفي لبحث معمّق واحد في الجلسة ومثله في الأسبوع.',
 ' وفي المجانية تظهر تقارير الباحث ومسودات الكاتب التي تُنشأ عليها معاينةً: الجزء الأول من نصّها مع المراجع المذكورة فيه، والبقية — مع مشاركتها وتصديرها — في الباقات المدفوعة. هذا قفل مقصود في الباقة المجانية لا خلل، وما أُنشئ أثناء اشتراك مدفوع يبقى كاملاً.'),
(2, 'pricing', 'e123ce52660612e653f2f9ba5c030c0b',
 'قراءة محادثاتك السابقة ومساحة عملك، وتصفّح المدونة والمكتبة القانونية العامة، وحفظ العناصر وتصديرها ومشاركتها، وإنشاء القضايا والقوالب وتنظيمها.',
 ' (والمخرَج المعروض معاينةً في الباقة المجانية لا يُشارَك ولا يُصدَّر.)'),
-- ── usage_limits (length 1960) ──
(3, 'usage_limits', 'f40afbc0922b6d64d798bd29b16bbee5',
 'الباقة المجانية خارج الجدول: نافذة واحدة بـ5 نقاط كل 30 يوماً — بحث معمّق واحد تقريباً، لتجربة ريحان قبل الاشتراك.',
 ' وتظهر فيها تقارير الباحث ومسودات الكاتب التي تُنشأ عليها معاينةً: الجزء الأول من نصّها، والبقية في الباقات المدفوعة.'),
(4, 'usage_limits', 'f40afbc0922b6d64d798bd29b16bbee5',
 'قراءة محادثاتك السابقة ومساحة عملك ومكتبتك، وتصفّح المدونة والمكتبة القانونية العامة، وحفظ العناصر وتصديرها ومشاركتها، وإنشاء القضايا والقوالب وتنظيمها.',
 ' (والمخرَج المعروض معاينةً في الباقة المجانية لا يُشارَك ولا يُصدَّر.)'),
-- ── workspace (length 4705) ──
(5, 'workspace', 'db2ce846b094ecc4590c5af0f98a0d94',
 '- **البطاقة مفتوحة** — نص المخرَج كاملاً ومراجعه تحته.',
 ' وفي الباقة المجانية تُفتح تقارير الباحث ومسودات الكاتب التي تُنشأ عليها معاينةً: الجزء الأول من نصّها والمراجع المذكورة فيه، ثم تنبيه بأن البقية متاحة في الباقات المدفوعة. هذا قفل مقصود في الباقة المجانية لا خلل، وما أُنشئ أثناء اشتراك مدفوع يبقى كاملاً.'),
(6, 'workspace', 'db2ce846b094ecc4590c5af0f98a0d94',
 '- **حرّر وقيّم** — عدّل أي مسودة مباشرة داخل مساحة العمل، وقيّم العناصر بإعجاب أو عدمه.',
 ' (المسودة المعروضة معاينةً في الباقة المجانية للقراءة فقط.)'),
(7, 'workspace', 'db2ce846b094ecc4590c5af0f98a0d94',
 '- **شارك خارج ريحان** — زر **«مشاركة»** يعطيك رابطاً يفتح هذا المخرَج، وزر **«حفظ كمدونة»** يحفظه في «مدوناتي».',
 ' ولا يظهر الزرّان، ولا زر التصدير، على مخرَج معروض معاينةً في الباقة المجانية.'),
-- ── guide (length 2076) ──
(8, 'guide', '33bbc69d769027c8009fb9f4df8a7698',
 '- **حرّرها بنفسك** مباشرة داخل مساحة العمل.',
 ' (في الباقة المجانية تظهر المسودة معاينةً للقراءة فقط.)'),
(9, 'guide', '33bbc69d769027c8009fb9f4df8a7698',
 'أي عنصر في مساحة العمل يمكن مشاركته برابط، ومسودة أعجبتك تحفظها مدونة في «مدوناتي».',
 ' ويُستثنى من ذلك المخرَج المعروض معاينةً في الباقة المجانية.'),
-- ── how_it_works (length 2629) ──
(10, 'how_it_works', '30691baba6bcc0fbcbb2fe18c6337c8a',
 '3. **ينتقي الأنسب ويعيده تقريراً موثّقاً** — تقرير واحد مرتّب، كل معلومة فيه تحمل مرجعاً مرقّماً يفتح لك النص الرسمي من مصدره.',
 ' وفي الباقة المجانية يظهر التقرير معاينةً: الجزء الأول منه، والبقية في الباقات المدفوعة.'),
(11, 'how_it_works', '30691baba6bcc0fbcbb2fe18c6337c8a',
 '- **مسودته تظهر في مساحة العمل** — بجانب المحادثة مباشرة، تراجعها وتطلب تعديلها حتى تصل إلى الصيغة النهائية.',
 ' وفي الباقة المجانية تظهر المسودة معاينةً، والبقية في الباقات المدفوعة.');

-- ── the new text itself (a failure here is this file's bug → abort) ─────────
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM m198b_edits e WHERE e.add_text ~ '[0-9٠-٩۰-۹]') THEN
        RAISE EXCEPTION '198b: an addition contains a digit (pricing sections carry none — M126)';
    END IF;
    IF EXISTS (SELECT 1 FROM m198b_edits e
                WHERE position('المشتركون الأوائل' IN e.add_text) > 0
                   OR position('http' IN e.add_text) > 0) THEN
        RAISE EXCEPTION '198b: an addition names «المشتركون الأوائل» or carries a URL';
    END IF;
END $$;

-- ── apply, one doc at a time, md5-guarded ────────────────────────────────────
DO $$
DECLARE
    v_doc   record;
    v_edit  record;
    v_live  text;
    v_new   text;
    v_n     int;
    v_done  int := 0;
BEGIN
    FOR v_doc IN
        SELECT e.doc_key, min(e.live_md5) AS live_md5, count(*) AS edits
          FROM m198b_edits e GROUP BY e.doc_key ORDER BY min(e.ord)
    LOOP
        SELECT d.content_md INTO v_live
          FROM public.product_docs d WHERE d.doc_key = v_doc.doc_key;
        IF NOT FOUND THEN
            RAISE NOTICE '198b: no product_docs row % — skipped; paste its additions by hand.', v_doc.doc_key;
            CONTINUE;
        END IF;

        -- Re-run: every addition already in place.
        IF NOT EXISTS (SELECT 1 FROM m198b_edits e
                        WHERE e.doc_key = v_doc.doc_key
                          AND position(e.old_text || e.add_text IN v_live) = 0) THEN
            RAISE NOTICE '198b: % already carries its % addition(s) — nothing written (re-run).',
                v_doc.doc_key, v_doc.edits;
            CONTINUE;
        END IF;

        IF md5(v_live) <> v_doc.live_md5 THEN
            RAISE NOTICE
                '198b: product_docs.% md5 is % (length %), expected % (read 2026-10-30) — '
                'the row changed since; NOTHING written to it. Apply its additions by hand.',
                v_doc.doc_key, md5(v_live), length(v_live), v_doc.live_md5;
            CONTINUE;
        END IF;

        v_new := v_live;
        FOR v_edit IN
            SELECT * FROM m198b_edits e WHERE e.doc_key = v_doc.doc_key ORDER BY e.ord
        LOOP
            -- Exactly one occurrence, or the anchor is ambiguous.
            IF (length(v_new) - length(replace(v_new, v_edit.old_text, '')))
               <> length(v_edit.old_text) THEN
                RAISE EXCEPTION '198b: edit % (%): the anchor sentence does not occur exactly once',
                    v_edit.ord, v_doc.doc_key;
            END IF;
            v_new := replace(v_new, v_edit.old_text, v_edit.old_text || v_edit.add_text);
        END LOOP;

        UPDATE public.product_docs d
           SET content_md = v_new
         WHERE d.doc_key = v_doc.doc_key
           AND md5(d.content_md) = v_doc.live_md5;
        GET DIAGNOSTICS v_n = ROW_COUNT;
        IF v_n <> 1 THEN
            RAISE NOTICE '198b: product_docs.% changed under us — nothing written.', v_doc.doc_key;
            CONTINUE;
        END IF;

        -- Self-test: every addition landed, once, right after its anchor.
        IF EXISTS (SELECT 1 FROM m198b_edits e
                     JOIN public.product_docs d ON d.doc_key = e.doc_key
                    WHERE e.doc_key = v_doc.doc_key
                      AND (length(d.content_md)
                           - length(replace(d.content_md, e.old_text || e.add_text, '')))
                          <> length(e.old_text || e.add_text)) THEN
            RAISE EXCEPTION '198b: product_docs.% — an addition is missing or doubled after the write',
                v_doc.doc_key;
        END IF;
        v_done := v_done + 1;
        RAISE NOTICE '198b: product_docs.% — % addition(s) written (length % → %).',
            v_doc.doc_key, v_doc.edits, length(v_live), length(v_new);
    END LOOP;

    RAISE NOTICE '198b OK: % doc(s) updated', v_done;
END $$;

COMMIT;
