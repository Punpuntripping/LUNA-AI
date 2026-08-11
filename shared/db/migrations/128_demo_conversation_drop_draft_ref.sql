-- ============================================================================
-- Migration 128 — demo fixture: drop the DRAFT reference at [3], renumber 11 → 10
-- Plan: .claude/plans/demo_conversation_product_tour.md
-- Follows: 127_demo_conversation.sql (APPLIED — do not edit that file)
--
-- DATA-ONLY. NO SCHEMA CHANGE. One reference row, one body, one summary.
--
-- WHY
-- Migration 127 left the product tour pointing Act 3 at reference [3]:
--
--     «نظامية لتنظيم تسوية المنازعات قبل قيدها لدى المحاكم» — وزارة العدل
--     regulations_v2.id = 34efc991-44ca-4d6b-9503-27e1ba6759cc
--     status_class = 'consultation_ended'
--
-- That is a DRAFT under public consultation, not enacted law. Its chunk body
-- literally opens «خامساً: جدول المواد المقترح تعديلها … تم اقتراح إضافة
-- الفقرة» — a table of PROPOSED amendments. The tour told a brand-new user to
-- click it as the worked example of «افتح النظام في ريحان»: the first piece of
-- Saudi law the product ever shows a lawyer, and it was a proposal.
--
-- 128 drops it and promotes the دليل at [7] (regulation
-- 904d35ce-61bb-4b50-bdc2-caeeddbe66d7, status_class = 'in_force', 5 إحالات,
-- has a library page and an official landing URL) to be the new Act 3 anchor.
--
-- ⚠⚠ THIS MIGRATION AND THE TOUR ANCHOR CHANGE MUST SHIP TOGETHER. ⚠⚠
-- This is NOT 127's "migration first, then deploy" — the coupling runs both
-- ways, because BOTH tour anchors move:
--
--     citation-3   →  citation-6    (Act 3, plan §6 step 6)
--     ref-card-10  →  ref-card-9    (step 10, «كلها ما عدا الخدمات»)
--
-- Apply 128 without updating `frontend/components/tour/tour-content.ts` and the
-- tour points Act 3 at [3] — which is now a قضية, not a نظام, so the copy
-- «جرّب اضغط [3]» lands on the wrong kind of source entirely — while step 10
-- highlights card 10, which is now نظام العمل م234 and DOES render the library
-- button the step exists to say it lacks. Deploy the frontend first and the
-- anchors point at nothing. Land them in one window.
--
-- ⚠ WHY §خامساً LOSES A PARAGRAPH.
-- The dropped draft was the ONLY source for «يجب أن يسبق قيد الدعوى استيفاء
-- إجراءات التسوية الودية …». Its co-source (old [6], إنشاء إدارات التسوية
-- الودية) was already cut in 127, so after this drop NOTHING in the reference
-- set carries the التسوية-الودية claim. Same rule 127 followed: a claim whose
-- support is gone is DELETED, never re-pointed at a reference that does not say
-- it. The surviving paragraph's «تُرفع الدعوى **بعد ذلك**» then referred to a
-- paragraph that no longer existed, so its opening clause is reworded — the
-- only prose edit in this file that is not a marker remap.
--
-- ⚠ THE SUMMARY MAKES THE SAME CLAIM and is fixed in the same breath: its
-- «الإجراء القضائي» bullet still walked the reader through التسوية الودية
-- first. (Its «الخلاصة» line naming التسوية الودية as a GAP is deliberately
-- left — that statement is about what the document does NOT cover, and it is
-- more true now, not less.)
--
-- ⚠ workspace_item_references JOINS ON `wi_id`, NOT `item_id` — `item_id` is
-- the SOURCE ROW pk. See backend/app/services/library_items_service.py:940.
--
-- ⚠ UNIQUE (wi_id, n) — `workspace_item_references_wi_id_n_key`, NOT
-- DEFERRABLE, so it is enforced row-by-row inside a single UPDATE. Remapping
-- straight to 1..10 would abort the moment 4 → 3 lands on the slot the draft
-- still occupies. Hence the two-phase +1000 park, same as 127.
--
-- IDEMPOTENT. The guard is `exists (ref_pk = <the draft>)` and NOTHING ELSE.
-- That predicate IS the question this migration asks — "is the draft reference
-- still here?" — so it cannot rot: it does not encode the surviving count, and
-- no future addition or removal can make it accidentally true again. 127 used
-- `(ref_pk = any(dropped) or n > 11)`; the `n >` half is DELIBERATELY DROPPED
-- here, because with 10 survivors the analogous `n > 10` sits directly adjacent
-- to a legitimate future state — add a 12th reference later and the block would
-- re-open, park every row at +1000, and leave the unmapped newcomer stranded
-- there. The verification block still catches any hand-drift, loudly.
--
-- Ends with the same hard verification 127 used, retargeted: refs exactly
-- 1..10, body markers exactly 1..10, [6] = regulations, [9] = compliance, and
-- md5 assertions on both texts.
-- ============================================================================


-- ============================================================================
-- RESTORE BLOCK — the post-127 state, verbatim, captured 2026-08-11.
--
-- 128 DELETEs a row and overwrites two texts that exist in no other copy.
-- Uncomment and run to put the fixture back exactly as 127 left it.
-- Do NOT run this against a pre-127 database.
-- ============================================================================
/*
-- ---- restore the 11 post-127 reference rows ------------------------------
delete from workspace_item_references
 where wi_id = 'ac478719-4897-48ee-a844-30bbb482da27';

insert into workspace_item_references
    (ref_pk, wi_id, domain, n, relevance, used, sub_queries, created_at, ref_id, item_id, content_word_count)
values
 ('585e3481-b4be-494d-ba6a-b92bb5ee619b','ac478719-4897-48ee-a844-30bbb482da27','regulations', 1,'high',true,'{1,2,3,4}','2026-08-10 15:48:55.898828+00','reg:dd494692-a220-5261-b819-79c1000cc50c','dd494692-a220-5261-b819-79c1000cc50c',712),
 ('fa10b870-9bb5-4d5b-a84e-b8e78f521930','ac478719-4897-48ee-a844-30bbb482da27','regulations', 2,'high',true,'{0}','2026-08-10 15:48:55.898828+00','reg:bf16dadf-dd23-59a2-a32b-52961fc988d0','bf16dadf-dd23-59a2-a32b-52961fc988d0',365),
 ('a54090ff-af65-42b1-bd96-7c43ee7eb32f','ac478719-4897-48ee-a844-30bbb482da27','regulations', 3,'high',true,'{6}','2026-08-10 15:48:55.898828+00','reg:37869562-3cf7-5cc3-aaae-4e1be32de526','37869562-3cf7-5cc3-aaae-4e1be32de526',892),
 ('e071587a-f38b-4885-865f-fd6b40155cc4','ac478719-4897-48ee-a844-30bbb482da27','cases', 4,'high',true,'{11}','2026-08-10 15:48:55.898828+00','case:17642_ap_4630783708','4d3db62d-9d14-4dd0-a030-d3b6599a4ee3',394),
 ('7df33b31-044e-4688-af5a-8d1799537f25','ac478719-4897-48ee-a844-30bbb482da27','cases', 5,'high',true,'{11}','2026-08-10 15:48:55.898828+00','case:17642_fi_401387071','5b9fd8f3-7a16-4ad8-b73d-3103ce6b0976',476),
 ('f83248d9-946e-4308-bb79-b7a35f736321','ac478719-4897-48ee-a844-30bbb482da27','regulations', 6,'medium',true,'{1,2,3}','2026-08-10 15:48:55.898828+00','reg:eb5f7143-1470-5f05-8178-4b5a45ee03ca','eb5f7143-1470-5f05-8178-4b5a45ee03ca',758),
 ('9c9bcf4d-ef56-4acb-b2ba-e55a71f7dee8','ac478719-4897-48ee-a844-30bbb482da27','regulations', 7,'medium',true,'{4,6}','2026-08-10 15:48:55.898828+00','reg:ae395597-d656-5324-87ea-64882a875899','ae395597-d656-5324-87ea-64882a875899',839),
 ('427b0fe7-3f32-4e92-adb0-43421385bb02','ac478719-4897-48ee-a844-30bbb482da27','cases', 8,'medium',true,'{7}','2026-08-10 15:48:55.898828+00','case:17486_الأحكام_التجارية_1428هـ_مجموعة_الاحكام_الادارية_-_الجزء_2_18_4_ل_1428','24766e21-b384-4a90-8f1d-e2b9909c5d37',443),
 ('6f4ab772-383f-4443-878d-481a58b5b98b','ac478719-4897-48ee-a844-30bbb482da27','cases', 9,'medium',true,'{9}','2026-08-10 15:48:55.898828+00','case:17642_ap_46','1757e576-5c77-412b-9f59-e4e6e3447daa',546),
 ('f5182d98-2f5f-4bc1-9e93-4945839152aa','ac478719-4897-48ee-a844-30bbb482da27','compliance',10,'medium',true,'{6}','2026-08-10 15:48:55.898828+00','compliance:3d05ae084124b8a0','ab7fdfae-24bd-42d6-953d-907c7abc9f8b',181),
 ('7de92bd1-27b4-4a2c-add9-3fb075591f52','ac478719-4897-48ee-a844-30bbb482da27','regulations',11,'high',true,'{6}','2026-08-10 15:48:55.898828+00','reg:5fcaded5-2b05-5d36-a294-eb8b71e049e7','5fcaded5-2b05-5d36-a294-eb8b71e049e7',302);

-- ---- restore the post-127 body (md5 8566ddf07e12850c3e554068f982f98e) ----
update workspace_items
   set content_md = $restore_body$إذا أنهي عقد العمل دون سبب مشروع، يقرّر نظام العمل السعودي للعامل عدة حقوق مالية وإجرائية، أبرزها: التعويض عن الإنهاء غير المشروع، وبدل الإشعار (إن لم يُراعَ)، وشهادة الخدمة، ومكافأة نهاية الخدمة، إضافة إلى الحق في التقاضي أمام المحاكم العمالية خلال مدة محددة.

## أولاً: التعويض عن الإنهاء دون سبب مشروع

تنص المادة 77 من نظام العمل على أنه إذا أنهي العقد من أحد الطرفين لسبب غير مشروع، ولم يتضمن العقد تعويضاً محدداً، يستحق الطرف المتضرر تعويضاً يُحتسب كالآتي [1]:

- **العقد غير محدد المدة**: أجر 15 يوماً عن كل سنة من سنوات خدمة العامل.
- **العقد محدد المدة**: أجر المدة الباقية من العقد.
- **الحد الأدنى للتعويض**: لا يقل عن أجر العامل لمدة شهرين [1][6].

## ثانياً: بدل الإشعار

إذا كان العقد غير محدد المدة، وأراد أحد الطرفين إنهاءه بناءً على سبب مشروع، فيجب أن يسبق الإنهاء إشعار كتابي [1][7]:

- **إذا كان الإنهاء من طرف صاحب العمل** والأجر يدفع شهرياً: 60 يوماً على الأقل.
- **إذا كان الإنهاء من طرف العامل** والأجر يدفع شهرياً: 30 يوماً على الأقل.
- **إذا كان الأجر لا يدفع شهرياً**: 30 يوماً لأي من الطرفين.

فإذا لم يُراعِ الطرف المُنهي للعقد مهلة الإشعار، يلتزم بدفع تعويض للطرف الآخر يساوي أجر العامل عن المهلة نفسها، وفقاً للمادة 76 [1][6]. ويجوز للعامل خلال مهلة الإشعار التغيب يوماً كاملاً في الأسبوع أو 8 ساعات أسبوعياً للبحث عن عمل آخر مع استحقاق الأجر [1].

## ثالثاً: شهادة الخدمة

يلتزم صاحب العمل عند انتهاء عقد العمل – أياً كان سبب الإنهاء – بتسليم العامل شهادة خدمة دون مقابل، توضح تاريخ الالتحاق وتاريخ الانتهاء والمهنة وآخر أجر، ولا يجوز تضمين الشهادة ما يسيء إلى العامل [2][5]. ويجب أيضاً إعادة جميع ما أودعه العامل من وثائق وشهادات [2].

## رابعاً: مكافأة نهاية الخدمة

تُحتسب مكافأة نهاية الخدمة عند انتهاء علاقة العمل على أساس آخر أجر كان يتقاضاه العامل [2][7]:

- **عن السنوات الخمس الأولى**: نصف أجر شهري عن كل سنة.
- **عن السنوات التي تلي الخمس الأولى**: أجر شهري كامل عن كل سنة.
- وتُحتسب أجزاء السنة بنسبة ما قضاه العامل في العمل [2][7].

## خامساً: الإجراء القضائي للمطالبة بالحقوق

للعامل الحق في رفع دعوى عمالية للمطالبة بحقوقه، غير أنه يجب أن يسبق قيد الدعوى استيفاء إجراءات التسوية الودية لدى الإدارة المختصة في وزارة الموارد البشرية والتنمية الاجتماعية [3].

تُرفع الدعوى بعد ذلك أمام المحكمة العمالية عبر بوابة ناجز الإلكترونية [10]، مع مراعاة أن المادة 234 من نظام العمل تنص على عدم قبول الدعوى بعد مضي 12 شهراً من تاريخ انتهاء علاقة العمل، ما لم يقدم المدعي عذراً تقبله المحكمة أو يصدر من المدعى عليه إقرار بالحق [11].

## سادساً: مبدآن من التطبيقات القضائية

من التطبيقات القضائية في هذا الباب مبدآن يهمّان العامل عند المنازعة:

**1. العذر المرضي ينفي مشروعية الفصل:** ألغى ديوان المظالم قرار إنهاء خدمة موظف صدر بحجة غيابه خمسة عشر يوماً متتالية دون عذر، بعد أن ثبت أنه مُنح إجازات مرضية وإجازة مرافقة تغطي الفترة ذاتها، فتخلّف شرط «عدم وجود عذر مشروع» وصار القرار معيباً بعيب السبب. وأضافت المحكمة أن جهة العمل لم تُنذر الموظف كتابةً للاستفسار عن أسباب انقطاعه رغم إمكانية التواصل معه [8].

**2. المخالصة النهائية تحجب المطالبة المالية اللاحقة:** أيّدت محكمة الاستئناف العمالية رد دعوى عامل طالب بتعويض عن إنهاء عقده، لإقراره بالتوقيع على مخالصة وعدم إثباته الإكراه — الذي يتطلب تهديداً بخطر جسيم محدق [4]. وفي حكم آخر، عُدّت التسوية وجدولة السداد عقد صلح لازماً لا يجوز نقضه، فلم تُقبل مطالب العامل المالية [9].

على أن المخالصة لا تمتد إلى الالتزامات غير المالية: ففي الحكم ذاته أُلزمت المنشأة بتسليم العامل شهادة خدمة مستوفية للبيانات النظامية رغم التسوية [9]، وهو ما يتفق مع إلزام صاحب العمل في حكم آخر بتسليم شهادة الخدمة ووثائق العامل ضمن مستحقاته [5].

---

يتضح مما سبق أن نظام العمل يوفّر للعامل حماية موسعة عند الفصل دون سبب مشروع، تشمل التعويض المالي وبدل الإشعار وشهادة الخدمة والمكافأة، مع ضمانات إجرائية للمطالبة بها. إلا أن ثبوت هذه الحقوق يخضع في النهاية لظروف كل حالة وتقدير المحكمة المختصة.$restore_body$,
       metadata = metadata || '{"ref_count": 11, "cited_count": 11}'::jsonb
 where item_id = 'ac478719-4897-48ee-a844-30bbb482da27';

-- ---- then the summary, SEPARATELY (see the trigger note below) -----------
update workspace_items
   set summary = $restore_summary$**ملخص المحتوى:**
هذا المستند هو نتاج بحث قانوني (وكيل) يُجيب عن سؤال: ما حقوق العامل عند الفصل دون سبب مشروع في السعودية؟ يغطّي المستند الأحكام النظامية والإجراءات القضائية مع تطبيقين قضائيين.

**المحاور الرئيسية:**
- **التعويض عن الإنهاء غير المشروع (م77):** أجر 15 يوماً عن كل سنة (عقد غير محدد المدة)، أو أجر المدة الباقية (عقد محدد المدة)، مع حد أدنى لا يقل عن شهرين.
- **بدل الإشعار:** مهلة 60 يوماً إذا كان الإنهاء من صاحب العمل (أجر شهري)، و30 يوماً للعامل، وتعويض يساوي الأجر عن المهلة إذا لم تراعَ.
- **شهادة الخدمة:** التزام بتسليمها دون مقابل وإعادة وثائق العامل، أياً كان سبب الإنهاء.
- **مكافأة نهاية الخدمة:** نصف أجر عن كل سنة من أول 5 سنوات، وأجر كامل عن كل سنة بعدها.
- **الإجراء القضائي:** التسوية الودية لدى وزارة الموارد البشرية أولاً، ثم رفع الدعوى أمام المحكمة العمالية عبر ناجز، مع مدة تقادم 12 شهراً من تاريخ انتهاء العلاقة (م234).
- **تطبيقات قضائية:** مبدآن — العذر المرضي ينفي مشروعية الفصل، والمخالصة النهائية تحجب المطالبة المالية اللاحقة دون أن تُسقط الالتزام بشهادة الخدمة.

**الخلاصة:**
المستند كافٍ من حيث الإطار النظامي العام وعرض التطبيقين القضائيين. الفجوات الرئيسية: عدم التعمّق في تقدير التعويض القضائي (كيف تحسب المحكمة مقداره في الممارسة الفعلية)، وعدم ذكر تفاصيل التسوية الودية ومدتها الزمنية.$restore_summary$,
       summary_source_length = length(content_md),
       summary_updated_at    = now()
 where item_id = 'ac478719-4897-48ee-a844-30bbb482da27';
*/
-- ============================================================================
-- END RESTORE BLOCK
-- ============================================================================


begin;


-- ---------------------------------------------------------------------------
-- 1 + 2. Drop the draft reference, then renumber the survivors to a dense 1..10.
--
-- GUARD: the draft's ref_pk. See the header — this is the one predicate that
-- states the migration's own precondition and therefore cannot rot.
--
-- Both statements key on ref_pk — immutable — not on n, the column being
-- rewritten. Deleting by n would be correct exactly once.
-- ---------------------------------------------------------------------------
do $$
declare
    v_wi    constant uuid := 'ac478719-4897-48ee-a844-30bbb482da27';
    -- reg:37869562-… «نظامية لتنظيم تسوية المنازعات قبل قيدها لدى المحاكم»
    -- وزارة العدل · status_class = 'consultation_ended' · A DRAFT.
    v_draft constant uuid := 'a54090ff-af65-42b1-bd96-7c43ee7eb32f';
    v_before int;
    v_after  int;
begin
    if not exists (
        select 1 from workspace_item_references
         where wi_id = v_wi and ref_pk = v_draft
    ) then
        raise notice '128: draft reference already removed from % — skipping steps 1+2', v_wi;
        return;
    end if;

    select count(*) into v_before from workspace_item_references where wi_id = v_wi;

    delete from workspace_item_references
     where wi_id = v_wi and ref_pk = v_draft;

    -- PHASE A — park every survivor at n + 1000. Current n ∈ [1,11];
    -- +1000 lands in [1001,1011] — provably disjoint from the target range.
    update workspace_item_references
       set n = n + 1000
     where wi_id = v_wi;

    -- PHASE B — settle each survivor on its final n.
    --   ⚠ [6] and [9] ARE LOAD-BEARING FOR THE TOUR — do not resequence.
    update workspace_item_references r
       set n = m.new_n
      from (values
              ('585e3481-b4be-494d-ba6a-b92bb5ee619b'::uuid,  1),  -- was  1  regulations  نظام العمل م74-79
              ('fa10b870-9bb5-4d5b-a84e-b8e78f521930'::uuid,  2),  -- was  2  regulations  دليل — الحقوق والمكافآت
              ('e071587a-f38b-4885-865f-fd6b40155cc4'::uuid,  3),  -- was  4  cases        استئناف عمالي — مخالصة
              ('7df33b31-044e-4688-af5a-8d1799537f25'::uuid,  4),  -- was  5  cases        ابتدائي عمالي — شهادة خدمة ووثائق
              ('f83248d9-946e-4308-bb79-b7a35f736321'::uuid,  5),  -- was  6  regulations  دليل — انتهاء عقد العمل
              ('9c9bcf4d-ef56-4acb-b2ba-e55a71f7dee8'::uuid,  6),  -- was  7  regulations  دليل — إنهاء الخدمة والتقاعد  ← NEW ACT 3 ANCHOR (citation-6), in_force, 5 إحالات
              ('427b0fe7-3f32-4e92-adb0-43421385bb02'::uuid,  7),  -- was  8  cases        ديوان المظالم — العذر المرضي
              ('6f4ab772-383f-4443-878d-481a58b5b98b'::uuid,  8),  -- was  9  cases        استئناف عمالي — تسوية وجدولة
              ('f5182d98-2f5f-4bc1-9e93-4945839152aa'::uuid,  9),  -- was 10  compliance   خدمة حكومية — ناجز  ← STEP 10 ANCHOR MOVES HERE (ref-card-9)
              ('7de92bd1-27b4-4a2c-add9-3fb075591f52'::uuid, 10)   -- was 11  regulations  نظام العمل — م234 التقادم
           ) as m(ref_pk, new_n)
     where r.wi_id = v_wi
       and r.ref_pk = m.ref_pk;

    select count(*) into v_after from workspace_item_references where wi_id = v_wi;
    raise notice '128: references % → % for %', v_before, v_after, v_wi;
end
$$;


-- ---------------------------------------------------------------------------
-- 3. Body — §خامساً loses its first paragraph, its second is reworded to stand
--    alone, and every marker is remapped. Nothing else in the prose changes.
--
--    `word_count` is NOT set here: the BEFORE UPDATE trigger
--    `set_workspace_item_word_count` owns that column and recomputes it from
--    the new body via compute_word_count(). One owner, not two.
-- ---------------------------------------------------------------------------
update workspace_items
   set content_md = $body$إذا أنهي عقد العمل دون سبب مشروع، يقرّر نظام العمل السعودي للعامل عدة حقوق مالية وإجرائية، أبرزها: التعويض عن الإنهاء غير المشروع، وبدل الإشعار (إن لم يُراعَ)، وشهادة الخدمة، ومكافأة نهاية الخدمة، إضافة إلى الحق في التقاضي أمام المحاكم العمالية خلال مدة محددة.

## أولاً: التعويض عن الإنهاء دون سبب مشروع

تنص المادة 77 من نظام العمل على أنه إذا أنهي العقد من أحد الطرفين لسبب غير مشروع، ولم يتضمن العقد تعويضاً محدداً، يستحق الطرف المتضرر تعويضاً يُحتسب كالآتي [1]:

- **العقد غير محدد المدة**: أجر 15 يوماً عن كل سنة من سنوات خدمة العامل.
- **العقد محدد المدة**: أجر المدة الباقية من العقد.
- **الحد الأدنى للتعويض**: لا يقل عن أجر العامل لمدة شهرين [1][5].

## ثانياً: بدل الإشعار

إذا كان العقد غير محدد المدة، وأراد أحد الطرفين إنهاءه بناءً على سبب مشروع، فيجب أن يسبق الإنهاء إشعار كتابي [1][6]:

- **إذا كان الإنهاء من طرف صاحب العمل** والأجر يدفع شهرياً: 60 يوماً على الأقل.
- **إذا كان الإنهاء من طرف العامل** والأجر يدفع شهرياً: 30 يوماً على الأقل.
- **إذا كان الأجر لا يدفع شهرياً**: 30 يوماً لأي من الطرفين.

فإذا لم يُراعِ الطرف المُنهي للعقد مهلة الإشعار، يلتزم بدفع تعويض للطرف الآخر يساوي أجر العامل عن المهلة نفسها، وفقاً للمادة 76 [1][5]. ويجوز للعامل خلال مهلة الإشعار التغيب يوماً كاملاً في الأسبوع أو 8 ساعات أسبوعياً للبحث عن عمل آخر مع استحقاق الأجر [1].

## ثالثاً: شهادة الخدمة

يلتزم صاحب العمل عند انتهاء عقد العمل – أياً كان سبب الإنهاء – بتسليم العامل شهادة خدمة دون مقابل، توضح تاريخ الالتحاق وتاريخ الانتهاء والمهنة وآخر أجر، ولا يجوز تضمين الشهادة ما يسيء إلى العامل [2][4]. ويجب أيضاً إعادة جميع ما أودعه العامل من وثائق وشهادات [2].

## رابعاً: مكافأة نهاية الخدمة

تُحتسب مكافأة نهاية الخدمة عند انتهاء علاقة العمل على أساس آخر أجر كان يتقاضاه العامل [2][6]:

- **عن السنوات الخمس الأولى**: نصف أجر شهري عن كل سنة.
- **عن السنوات التي تلي الخمس الأولى**: أجر شهري كامل عن كل سنة.
- وتُحتسب أجزاء السنة بنسبة ما قضاه العامل في العمل [2][6].

## خامساً: الإجراء القضائي للمطالبة بالحقوق

يرفع العامل دعواه أمام المحكمة العمالية عبر بوابة ناجز الإلكترونية [9]، مع مراعاة أن المادة 234 من نظام العمل تنص على عدم قبول الدعوى بعد مضي 12 شهراً من تاريخ انتهاء علاقة العمل، ما لم يقدم المدعي عذراً تقبله المحكمة أو يصدر من المدعى عليه إقرار بالحق [10].

## سادساً: مبدآن من التطبيقات القضائية

من التطبيقات القضائية في هذا الباب مبدآن يهمّان العامل عند المنازعة:

**1. العذر المرضي ينفي مشروعية الفصل:** ألغى ديوان المظالم قرار إنهاء خدمة موظف صدر بحجة غيابه خمسة عشر يوماً متتالية دون عذر، بعد أن ثبت أنه مُنح إجازات مرضية وإجازة مرافقة تغطي الفترة ذاتها، فتخلّف شرط «عدم وجود عذر مشروع» وصار القرار معيباً بعيب السبب. وأضافت المحكمة أن جهة العمل لم تُنذر الموظف كتابةً للاستفسار عن أسباب انقطاعه رغم إمكانية التواصل معه [7].

**2. المخالصة النهائية تحجب المطالبة المالية اللاحقة:** أيّدت محكمة الاستئناف العمالية رد دعوى عامل طالب بتعويض عن إنهاء عقده، لإقراره بالتوقيع على مخالصة وعدم إثباته الإكراه — الذي يتطلب تهديداً بخطر جسيم محدق [3]. وفي حكم آخر، عُدّت التسوية وجدولة السداد عقد صلح لازماً لا يجوز نقضه، فلم تُقبل مطالب العامل المالية [8].

على أن المخالصة لا تمتد إلى الالتزامات غير المالية: ففي الحكم ذاته أُلزمت المنشأة بتسليم العامل شهادة خدمة مستوفية للبيانات النظامية رغم التسوية [8]، وهو ما يتفق مع إلزام صاحب العمل في حكم آخر بتسليم شهادة الخدمة ووثائق العامل ضمن مستحقاته [4].

---

يتضح مما سبق أن نظام العمل يوفّر للعامل حماية موسعة عند الفصل دون سبب مشروع، تشمل التعويض المالي وبدل الإشعار وشهادة الخدمة والمكافأة، مع ضمانات إجرائية للمطالبة بها. إلا أن ثبوت هذه الحقوق يخضع في النهاية لظروف كل حالة وتقدير المحكمة المختصة.$body$,

       metadata = metadata || '{"ref_count": 10, "cited_count": 10}'::jsonb,

       -- Shared column, same reason as 127: this row is read by every
       -- account, so it must not ship carrying one viewer's thumb. Re-cleared
       -- because the owner has had the fixture in their sidebar since 127.
       feedback = null
 where item_id = 'ac478719-4897-48ee-a844-30bbb482da27';


-- ---------------------------------------------------------------------------
-- 4. SUMMARY — DELIBERATELY A SEPARATE STATEMENT. DO NOT MERGE IT ABOVE.
--
-- `trg_workspace_items_invalidate_summary` is a BEFORE UPDATE trigger on this
-- table:
--
--     IF NEW.content_md IS DISTINCT FROM OLD.content_md THEN
--         NEW.summary := NULL; NEW.summary_source_length := NULL;
--         NEW.summary_updated_at := NULL;
--
-- so any statement that writes content_md has its summary assignment silently
-- discarded — the trigger runs after the SET list is built and overwrites it
-- with NULL. This is not hypothetical: 127's first apply attempt aborted on
-- exactly this, which is how the trigger was found. A merged UPDATE here would
-- commit a fixture with NO summary, and nothing in the UI would show it — the
-- summary is read server-side only, by writer_planner
-- (backend/app/services/writer_planner_context.py:99).
--
-- Here content_md is NOT in the SET list, the trigger's IS DISTINCT test is
-- false, and these three columns are left alone.
-- ---------------------------------------------------------------------------
update workspace_items
   set summary = $summary$**ملخص المحتوى:**
هذا المستند هو نتاج بحث قانوني (وكيل) يُجيب عن سؤال: ما حقوق العامل عند الفصل دون سبب مشروع في السعودية؟ يغطّي المستند الأحكام النظامية والإجراءات القضائية مع تطبيقين قضائيين.

**المحاور الرئيسية:**
- **التعويض عن الإنهاء غير المشروع (م77):** أجر 15 يوماً عن كل سنة (عقد غير محدد المدة)، أو أجر المدة الباقية (عقد محدد المدة)، مع حد أدنى لا يقل عن شهرين.
- **بدل الإشعار:** مهلة 60 يوماً إذا كان الإنهاء من صاحب العمل (أجر شهري)، و30 يوماً للعامل، وتعويض يساوي الأجر عن المهلة إذا لم تراعَ.
- **شهادة الخدمة:** التزام بتسليمها دون مقابل وإعادة وثائق العامل، أياً كان سبب الإنهاء.
- **مكافأة نهاية الخدمة:** نصف أجر عن كل سنة من أول 5 سنوات، وأجر كامل عن كل سنة بعدها.
- **الإجراء القضائي:** رفع الدعوى أمام المحكمة العمالية عبر ناجز، مع مدة تقادم 12 شهراً من تاريخ انتهاء العلاقة (م234).
- **تطبيقات قضائية:** مبدآن — العذر المرضي ينفي مشروعية الفصل، والمخالصة النهائية تحجب المطالبة المالية اللاحقة دون أن تُسقط الالتزام بشهادة الخدمة.

**الخلاصة:**
المستند كافٍ من حيث الإطار النظامي العام وعرض التطبيقين القضائيين. الفجوات الرئيسية: عدم التعمّق في تقدير التعويض القضائي (كيف تحسب المحكمة مقداره في الممارسة الفعلية)، وعدم ذكر تفاصيل التسوية الودية ومدتها الزمنية.$summary$,

       -- Both were NULLed by the invalidate trigger when the body landed above;
       -- content_md already holds the new value, so this reads the new length.
       summary_source_length = length(content_md),
       summary_updated_at    = now()
 where item_id = 'ac478719-4897-48ee-a844-30bbb482da27';


-- ---------------------------------------------------------------------------
-- VERIFY — abort rather than ship a fixture whose panel and prose disagree.
--
-- The md5 literals are of the $body$ / $summary$ text in THIS file, computed at
-- authoring time from the same in-memory strings that were spliced in above.
-- This file reaches the database through a tool boundary (no psql, no direct DB
-- URL on the dev machine), and a single corrupted Arabic character would commit
-- silently into the one artifact every new account reads. If you edit either
-- literal you MUST recompute its hash, or this migration will refuse to apply —
-- which is the intended failure mode.
-- ---------------------------------------------------------------------------
do $$
declare
    v_wi      constant uuid := 'ac478719-4897-48ee-a844-30bbb482da27';
    v_refs    int[];
    v_markers int[];
    v_expect  constant int[] := array[1,2,3,4,5,6,7,8,9,10];
    v_domain  text;
begin
    select array_agg(n order by n) into v_refs
      from workspace_item_references where wi_id = v_wi;

    if v_refs is distinct from v_expect then
        raise exception '128 FAILED: reference n set is %, expected 1..10', v_refs;
    end if;

    select array_agg(distinct m[1]::int order by m[1]::int) into v_markers
      from workspace_items wi,
           lateral regexp_matches(wi.content_md, '\[([0-9]+)\]', 'g') m
     where wi.item_id = v_wi;

    if v_markers is distinct from v_expect then
        raise exception
            '128 FAILED: body cites %, expected exactly 1..10 (dead or missing markers)',
            v_markers;
    end if;

    -- [6] is the NEW Act 3 anchor (citation-6), and it must stay a regulations
    -- reference: it is the only domain whose cards carry إحالات, which is the
    -- whole point of the step that anchors here.
    --
    -- ⚠ It does NOT keep the button reading «فتح النظام في ريحان».
    -- `referenceDefiniteType` (ReferencePanel.tsx) only falls back to the domain
    -- when `doc_type` is null or «غير محدد»; this card's doc_type is «دليل»
    -- (verified on the live payload), so DEFINITE_DOC_TYPE wins and the button
    -- reads «فتح الدليل في ريحان». That is why tour-content.ts step 9 no longer
    -- names a document type at all. Do not "restore" the نظام wording.
    --
    -- The in_force check lives upstream of SQL — it is the whole reason this
    -- migration exists, so re-read status_class before ever moving this anchor.
    select domain into v_domain
      from workspace_item_references where wi_id = v_wi and n = 6;
    if v_domain is distinct from 'regulations' then
        raise exception '128 FAILED: [6] must be a regulations reference, got %', v_domain;
    end if;

    -- [9] is where the step-10 anchor moved. The «كلها ما عدا الخدمات» lesson
    -- is that this ONE card has no library button — true only while it is the
    -- compliance reference.
    select domain into v_domain
      from workspace_item_references where wi_id = v_wi and n = 9;
    if v_domain is distinct from 'compliance' then
        raise exception '128 FAILED: [9] must be the compliance reference, got %', v_domain;
    end if;

    if md5((select content_md from workspace_items where item_id = v_wi))
       is distinct from '93a2d2dac7343d3e380f37f1f24775b9' then
        raise exception '128 FAILED: content_md md5 mismatch — the body was altered in transit or edited without recomputing the hash';
    end if;

    if md5((select summary from workspace_items where item_id = v_wi))
       is distinct from '8d20653c3c83e4cd22bd5b98a71322dd' then
        raise exception '128 FAILED: summary md5 mismatch — see the note above';
    end if;

    raise notice '128 OK: 10 references, markers 1..10, [6] = regulations, [9] = compliance, body+summary hashes match';
end
$$;


commit;
