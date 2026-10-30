-- ============================================================================
-- Migration 196b — product_docs.terms: the renewal sentences 196 makes false
--
-- Source: .claude/plans/renewal_retry_grace.md §5 + owner decisions 2026-10-30
-- (keep the billing date — a renewal's 30 days count from the renewal date, not
-- from the payment; a cancel during the retry hours lapses at once). The page
-- itself is frontend/content/legal/terms-ar.md (same commit); this row is the
-- copy the router reads through open_rayhan_page (600 s cache), so it must say
-- the same thing.
--
-- Live state 2026-10-30 (SELECT only): each OLD fragment below occurs exactly
-- once in product_docs 'terms'. The row is an older copy of the page (its date
-- line already differs), so only these two sentences are touched — each
-- replaced iff still present verbatim, NOTICE otherwise. Copied from the page
-- diff, never retyped. Latin digits only.
--
-- APPLY WITH the backend that ships 196's ladder (renewal_retry_grace.md §8):
-- before it the new sentences would be false the other way round.
-- IDEMPOTENT: a re-run finds the new text and writes nothing.
--
-- ROLLBACK: the reverse replace (new → old) of the two fragments below.
-- ============================================================================

BEGIN;

DO $$
DECLARE
    v_a_old text := 'بخصم قيمة الخطة السارية من وسيلة الدفع نفسها وبدء مدة جديدة مدتها 30 يومًا، ما لم تُوقِف التجديد قبل انتهاء المدة الجارية.';
    v_a_new text := 'بخصم قيمة الخطة السارية من وسيلة الدفع نفسها وبدء مدة جديدة مدتها 30 يومًا تُحتسب من تاريخ التجديد المستحق، أي من نهاية المدة السابقة، ما لم تُوقِف التجديد قبل انتهاء المدة الجارية. وإذا لم يُقبل الدفع عند التجديد، نعيد المحاولة تلقائيًا خلال 24 ساعة وتبقى خطتك فعّالة خلالها؛ فإن لم يُقبل، أو رفضه البنك رفضًا نهائيًا (كبطاقة منتهية الصلاحية أو مفقودة)، ينتقل حسابك إلى الخطة المجانية.';
    v_b_old text := 'ولك أن تتراجع عن الإيقاف قبل انتهاء المدة الجارية فيعود التجديد كما كان.';
    v_b_new text := 'ولك أن تتراجع عن الإيقاف قبل انتهاء المدة الجارية فيعود التجديد كما كان. أما إذا أوقفت التجديد خلال ساعات إعادة محاولة الدفع، فينتقل حسابك إلى الخطة المجانية فورًا، لأن المدة المدفوعة تكون قد انتهت.';
    v_live  text;
    v_n     integer;
BEGIN
    IF v_a_new ~ '[٠-٩۰-۹]' OR v_b_new ~ '[٠-٩۰-۹]' THEN
        RAISE EXCEPTION '196b: new text carries Arabic-Indic digits (Latin digits only)';
    END IF;

    SELECT content_md INTO v_live FROM public.product_docs WHERE doc_key = 'terms';
    IF NOT FOUND THEN
        RAISE NOTICE '196b: no product_docs row terms — nothing to do.';
        RETURN;
    END IF;

    -- A: the renewal term counts from the renewal date + the 24h retry hours.
    IF position(v_a_new IN v_live) > 0 THEN
        RAISE NOTICE '196b: terms — renewal sentence already new (re-run).';
    ELSIF (length(v_live) - length(replace(v_live, v_a_old, ''))) = length(v_a_old) THEN
        v_live := replace(v_live, v_a_old, v_a_new);
        RAISE NOTICE '196b: terms — renewal sentence replaced.';
    ELSE
        RAISE NOTICE '196b: terms — renewal sentence changed since 2026-10-30; NOT replaced, edit by hand.';
    END IF;

    -- B: a cancel during the retry hours lapses at once (appended to §3).
    IF position(v_b_new IN v_live) > 0 THEN
        RAISE NOTICE '196b: terms — cancel sentence already new (re-run).';
    ELSIF (length(v_live) - length(replace(v_live, v_b_old, ''))) = length(v_b_old) THEN
        v_live := replace(v_live, v_b_old, v_b_new);
        RAISE NOTICE '196b: terms — cancel sentence extended.';
    ELSE
        RAISE NOTICE '196b: terms — cancel sentence changed since 2026-10-30; NOT extended, edit by hand.';
    END IF;

    UPDATE public.product_docs
       SET content_md = v_live, updated_at = now()
     WHERE doc_key = 'terms' AND content_md IS DISTINCT FROM v_live;
    GET DIAGNOSTICS v_n = ROW_COUNT;

    -- self-test: both new fragments present exactly once, neither old one left
    SELECT content_md INTO v_live FROM public.product_docs WHERE doc_key = 'terms';
    IF position(v_a_new IN v_live) > 0
       AND (length(v_live) - length(replace(v_live, v_a_new, ''))) <> length(v_a_new) THEN
        RAISE EXCEPTION '196b: the renewal sentence appears more than once';
    END IF;
    IF position(v_b_new IN v_live) > 0
       AND (length(v_live) - length(replace(v_live, v_b_new, ''))) <> length(v_b_new) THEN
        RAISE EXCEPTION '196b: the cancel sentence appears more than once';
    END IF;

    RAISE NOTICE '196b OK: % row(s) written', v_n;
END $$;

COMMIT;
