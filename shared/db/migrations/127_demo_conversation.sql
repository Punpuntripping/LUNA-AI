-- ============================================================================
-- Migration 127 — demo_conversation: trim the shared product-tour fixture
-- Plan: .claude/plans/demo_conversation_product_tour.md  (§2.1, §2.2, §2.3)
--
-- DATA-ONLY. NO SCHEMA CHANGE. Touches exactly two rows and one child table,
-- all of them the single shared demo fixture:
--
--   conversation  f4804262-da8c-45eb-87c2-911025377d13
--   workspace item ac478719-4897-48ee-a844-30bbb482da27  (wi_seq=1, agent_search)
--
-- WHAT IT DOES
--   1. renames the conversation to «محادثة تجريبية»
--   2. deletes 7 of the 18 workspace_item_references (18 → 11)
--   3. renumbers the 11 survivors to a dense 1..11
--   4. rewrites content_md so every [n] marker resolves to a survivor
--   5. clears workspace_items.feedback
--
-- ⚠⚠ THIS MIGRATION MUST RUN **BEFORE** THE DEPLOY. ⚠⚠
-- The body and the reference rows are ONE artifact split across two columns,
-- and NOTHING in the schema ties them together. Ship them out of order and the
-- product tour renders a reference panel whose card numbers do not match the
-- [n] markers in the text beside it: dead clicks in the exact surface the tour
-- exists to teach, on the first conversation every new account opens. Same
-- ordering rule as [[project_moyasar_payments]]: migration, THEN deploy.
--
-- ⚠ WHY THE BODY IS REWRITTEN AND NOT JUST RENUMBERED.
-- Seven references are dropped, and §سادساً built FIVE قضائية principles on
-- them; only two survive the trim. Two more claims above §سادساً also rested
-- solely on dropped rows and are deleted outright rather than re-pointed at a
-- reference that does not carry them:
--
--   old [3]  المخالفة 38 / غرامة 1,000–3,000 ريال   → sentence DELETED
--   old [6]  إنشاء إدارات التسوية الودية              → sentence DELETED
--
-- A third claim — old [4], المادة 234 / تقادم 12 شهراً — was cut in the first
-- draft of this migration and RESTORED by owner decision (2026-08-11): it is
-- the most actionable fact in the answer, and its source chunk belongs to نظام
-- العمل (regulation da51024f-a713-48e7-af87-b6a541f055e4), which has both a
-- library page and an official landing URL — so its card renders the same two
-- buttons as the other nine. It is APPENDED as [11], never inserted in reading
-- order. See the numbering lock below.
--
-- ⚠ [1]–[10] ARE FROZEN. THE TOUR IS PINNED TO TWO OF THEM BY NUMBER.
-- `frontend/components/tour/tour-content.ts` anchors on `citation-3` (plan §6
-- step 6) and `ref-card-10` (step 10). Therefore:
--   [3]  MUST remain old [5] — regulations/نظام, carries إحالات and a library
--        page. It is the only reference that can demo الإحالات at all.
--   [10] MUST remain old [27] — the compliance card, the ONLY one with no
--        library button, which IS the «كلها ما عدا الخدمات» lesson.
-- Inserting the restored reference in body-flow order would have pushed the
-- compliance card to [11] and broken that lesson silently — copy that is
-- already written and already deployed. Hence [11] is cited in §خامساً, ahead
-- of §سادساً's [4]–[9] in reading order. That is deliberate: the anchors win.
-- The verification block at the foot of this file asserts both positions.
--
-- Every surviving claim was re-verified against the source row it cites
-- (chunks_v2.content / cases.reasoning+ruling) before this file was written.
-- NOTHING WAS INVENTED TO BACKFILL THE GAPS. If you edit the body again, hold
-- that line — this WI is the one artifact in the product every new user reads
-- with a tour pointing at it.
--
-- ⚠ workspace_item_references JOINS ON `wi_id`, NOT `item_id`.
-- `item_id` on that table is the SOURCE ROW pk (cases.id / chunks_v2.id), which
-- is why `where item_id = 'ac478719…'` returns zero rows and reads like "this
-- WI has no references". See backend/app/services/library_items_service.py:940.
--
-- ⚠ UNIQUE (wi_id, n) EXISTS — constraint `workspace_item_references_wi_id_n_key`,
-- NOT DEFERRABLE. A single UPDATE remapping n in place would abort on the first
-- transient collision (old 5 → 3 lands on old 3's slot). Hence the two-phase
-- +1000 shuffle in step 3. Verified live 2026-08-11.
--
-- ⚠ `workspace_items.feedback` IS A SHARED COLUMN on a row every account reads.
-- One user's 👍 would be everyone's 👍. It ships NULL, and the tour hides the
-- thumbs entirely (plan §4.2 / trap 3).
--
-- IDEMPOTENT. Steps 2+3 are guarded on "is this still the pre-migration shape?"
-- — any of the seven dropped ref_pks still present, OR any n > 11. Both go
-- false once this has run. Without that guard a second run would re-enter the
-- delete list — `n in (3,6,9,10,…)` matches the RENUMBERED survivors — and
-- silently destroy half the fixture. The delete and the renumber both key on
-- `ref_pk` (immutable) rather than on `n` (the thing being changed).
--
-- ⚠ The first draft guarded on `exists (n > 10)`. That is WRONG now that 11
-- references survive: it stays true forever and the guard never closes. Any
-- future change to the surviving count must revisit this threshold — which is
-- exactly why the guard leads with the ref_pk test, which cannot rot.
--
-- Ends with a hard verification block: the transaction ABORTS unless the WI is
-- left with exactly refs 1..11, a body whose marker set is exactly 1..11, and
-- the two tour-pinned cards still at [3] (regulations) and [10] (compliance).
-- ============================================================================


-- ============================================================================
-- RESTORE BLOCK — the pre-migration state, verbatim, captured 2026-08-11.
--
-- This migration is IRREVERSIBLE against live prod data: it DELETEs 7 rows and
-- overwrites a 4,152-char body that exists in no other copy (no seeder, no
-- file-based mirror). Everything needed to put it back is below. Uncomment and
-- run to restore.
-- ============================================================================
/*
-- ---- restore the 18 reference rows ----------------------------------------
delete from workspace_item_references
 where wi_id = 'ac478719-4897-48ee-a844-30bbb482da27';

insert into workspace_item_references
    (ref_pk, wi_id, domain, n, relevance, used, sub_queries, created_at, ref_id, item_id, content_word_count)
values
 ('585e3481-b4be-494d-ba6a-b92bb5ee619b','ac478719-4897-48ee-a844-30bbb482da27','regulations', 1,'high',  true,'{1,2,3,4}','2026-08-10 15:48:55.898828+00','reg:dd494692-a220-5261-b819-79c1000cc50c','dd494692-a220-5261-b819-79c1000cc50c',712),
 ('fa10b870-9bb5-4d5b-a84e-b8e78f521930','ac478719-4897-48ee-a844-30bbb482da27','regulations', 2,'high',  true,'{0}',      '2026-08-10 15:48:55.898828+00','reg:bf16dadf-dd23-59a2-a32b-52961fc988d0','bf16dadf-dd23-59a2-a32b-52961fc988d0',365),
 ('1afdcbdd-7fb4-45d5-af4f-cbf84d5f770e','ac478719-4897-48ee-a844-30bbb482da27','regulations', 3,'high',  true,'{0}',      '2026-08-10 15:48:55.898828+00','reg:8845e61a-9616-5bcb-9d64-4e7973b7fad8','8845e61a-9616-5bcb-9d64-4e7973b7fad8',210),
 ('7de92bd1-27b4-4a2c-add9-3fb075591f52','ac478719-4897-48ee-a844-30bbb482da27','regulations', 4,'high',  true,'{6}',      '2026-08-10 15:48:55.898828+00','reg:5fcaded5-2b05-5d36-a294-eb8b71e049e7','5fcaded5-2b05-5d36-a294-eb8b71e049e7',302),
 ('a54090ff-af65-42b1-bd96-7c43ee7eb32f','ac478719-4897-48ee-a844-30bbb482da27','regulations', 5,'high',  true,'{6}',      '2026-08-10 15:48:55.898828+00','reg:37869562-3cf7-5cc3-aaae-4e1be32de526','37869562-3cf7-5cc3-aaae-4e1be32de526',892),
 ('6276b618-f668-4f02-ba1f-5f30476ce8b6','ac478719-4897-48ee-a844-30bbb482da27','regulations', 6,'high',  true,'{6}',      '2026-08-10 15:48:55.898828+00','reg:dfb11963-8422-5f44-80a0-8b90ee03caa1','dfb11963-8422-5f44-80a0-8b90ee03caa1',235),
 ('e071587a-f38b-4885-865f-fd6b40155cc4','ac478719-4897-48ee-a844-30bbb482da27','cases',       8,'high',  true,'{11}',     '2026-08-10 15:48:55.898828+00','case:17642_ap_4630783708','4d3db62d-9d14-4dd0-a030-d3b6599a4ee3',394),
 ('6855324a-ff20-464b-9a2d-7798c384f3b3','ac478719-4897-48ee-a844-30bbb482da27','cases',       9,'high',  true,'{9}',      '2026-08-10 15:48:55.898828+00','case:17486_الأحكام_الإدارية_1402-1426هـ_المجلد_الثالث_742_ت_1_1411','7533eac7-735e-49ba-bbb7-a117a73f8b80',475),
 ('1c8b92f6-11f4-4b6c-a5cc-04e0feec9e73','ac478719-4897-48ee-a844-30bbb482da27','cases',      10,'high',  true,'{10}',     '2026-08-10 15:48:55.898828+00','case:17486_الأحكام_الإدارية_1402-1426هـ_المجلد_الثالث_1_1325_ق_1415','4d9ed93d-2411-4364-802f-bbb9f4bcf04e',487),
 ('7df33b31-044e-4688-af5a-8d1799537f25','ac478719-4897-48ee-a844-30bbb482da27','cases',      11,'high',  true,'{11}',     '2026-08-10 15:48:55.898828+00','case:17642_fi_401387071','5b9fd8f3-7a16-4ad8-b73d-3103ce6b0976',476),
 ('f83248d9-946e-4308-bb79-b7a35f736321','ac478719-4897-48ee-a844-30bbb482da27','regulations',12,'medium',true,'{1,2,3}',  '2026-08-10 15:48:55.898828+00','reg:eb5f7143-1470-5f05-8178-4b5a45ee03ca','eb5f7143-1470-5f05-8178-4b5a45ee03ca',758),
 ('9c9bcf4d-ef56-4acb-b2ba-e55a71f7dee8','ac478719-4897-48ee-a844-30bbb482da27','regulations',13,'medium',true,'{4,6}',    '2026-08-10 15:48:55.898828+00','reg:ae395597-d656-5324-87ea-64882a875899','ae395597-d656-5324-87ea-64882a875899',839),
 ('3069130b-fa76-4aad-ad90-c6eaf37e49d2','ac478719-4897-48ee-a844-30bbb482da27','cases',      17,'medium',true,'{7,11}',   '2026-08-10 15:48:55.898828+00','case:17486_الأحكام_التجارية_1430هـ_الاداري_1430_الجزء_الأول_935_4_ق_1427','d00c474d-ae9c-4351-8b71-c0a0a04527bd',469),
 ('427b0fe7-3f32-4e92-adb0-43421385bb02','ac478719-4897-48ee-a844-30bbb482da27','cases',      20,'medium',true,'{7}',      '2026-08-10 15:48:55.898828+00','case:17486_الأحكام_التجارية_1428هـ_مجموعة_الاحكام_الادارية_-_الجزء_2_18_4_ل_1428','24766e21-b384-4a90-8f1d-e2b9909c5d37',443),
 ('724aa957-cb0a-4c95-ae46-bc034bb0a49a','ac478719-4897-48ee-a844-30bbb482da27','cases',      22,'medium',true,'{9}',      '2026-08-10 15:48:55.898828+00','case:17486_الأحكام_الإدارية_1402-1426هـ_المجلد_الثالث_1437_1_ق_1422','5c1d65af-081a-4277-a05f-772491c65f14',557),
 ('c8993019-50b9-4a3f-9bd2-ff0af4278963','ac478719-4897-48ee-a844-30bbb482da27','cases',      25,'medium',true,'{10}',     '2026-08-10 15:48:55.898828+00','case:17486_الأحكام_الإدارية_1444هـ_Volume_1_1402_1443','e3814845-6ac7-4f0c-b2b9-318dd67cb800',446),
 ('6f4ab772-383f-4443-878d-481a58b5b98b','ac478719-4897-48ee-a844-30bbb482da27','cases',      26,'medium',true,'{9}',      '2026-08-10 15:48:55.898828+00','case:17642_ap_46','1757e576-5c77-412b-9f59-e4e6e3447daa',546),
 ('f5182d98-2f5f-4bc1-9e93-4945839152aa','ac478719-4897-48ee-a844-30bbb482da27','compliance', 27,'medium',true,'{6}',      '2026-08-10 15:48:55.898828+00','compliance:3d05ae084124b8a0','ab7fdfae-24bd-42d6-953d-907c7abc9f8b',181);

-- ---- restore the conversation title ---------------------------------------
update conversations
   set title_ar = 'ما حقوق العامل عند الفصل دون سبب مشروع؟ مع قضايا مشابهة'
 where conversation_id = 'f4804262-da8c-45eb-87c2-911025377d13';

-- ---- restore the WI body, counters and summary -----------------------------
update workspace_items
   set content_md = $restore_body$إذا أنهي عقد العمل دون سبب مشروع، يقرّر نظام العمل السعودي للعامل عدة حقوق مالية وإجرائية، أبرزها: التعويض عن الإنهاء غير المشروع، وبدل الإشعار (إن لم يُراعَ)، وشهادة الخدمة، ومكافأة نهاية الخدمة، إضافة إلى الحق في التقاضي أمام المحاكم العمالية خلال مدة محددة.

## أولاً: التعويض عن الإنهاء دون سبب مشروع

تنص المادة 77 من نظام العمل على أنه إذا أنهي العقد من أحد الطرفين لسبب غير مشروع، ولم يتضمن العقد تعويضاً محدداً، يستحق الطرف المتضرر تعويضاً يُحتسب كالآتي [1]:

- **العقد غير محدد المدة**: أجر 15 يوماً عن كل سنة من سنوات خدمة العامل.
- **العقد محدد المدة**: أجر المدة الباقية من العقد.
- **الحد الأدنى للتعويض**: لا يقل عن أجر العامل لمدة شهرين [1][12].

## ثانياً: بدل الإشعار

إذا كان العقد غير محدد المدة، وأراد أحد الطرفين إنهاءه بناءً على سبب مشروع، فيجب أن يسبق الإنهاء إشعار كتابي [1][13]:

- **إذا كان الإنهاء من طرف صاحب العمل** والأجر يدفع شهرياً: 60 يوماً على الأقل.
- **إذا كان الإنهاء من طرف العامل** والأجر يدفع شهرياً: 30 يوماً على الأقل.
- **إذا كان الأجر لا يدفع شهرياً**: 30 يوماً لأي من الطرفين.

فإذا لم يُراعِ الطرف المُنهي للعقد مهلة الإشعار، يلتزم بدفع تعويض للطرف الآخر يساوي أجر العامل عن المهلة نفسها، وفقاً للمادة 76 [1][12]. ويجوز للعامل خلال مهلة الإشعار التغيب يوماً كاملاً في الأسبوع أو 8 ساعات أسبوعياً للبحث عن عمل آخر مع استحقاق الأجر [1].

## ثالثاً: شهادة الخدمة

يلتزم صاحب العمل عند انتهاء عقد العمل – أياً كان سبب الإنهاء – بتسليم العامل شهادة خدمة دون مقابل، توضح تاريخ الالتحاق وتاريخ الانتهاء والمهنة وآخر أجر، ولا يجوز تضمين الشهادة ما يسيء إلى العامل [2][11]. ويجب أيضاً إعادة جميع ما أودعه العامل من وثائق وشهادات [2][3].

وإذا امتنع صاحب العمل عن تسليم شهادة الخدمة، يُعد ذلك مخالفة نظامية تتراوح غرامتها بين 1,000 ريال و3,000 ريال (تتعدد بتعدد العاملين) [3].

## رابعاً: مكافأة نهاية الخدمة

تُحتسب مكافأة نهاية الخدمة عند انتهاء علاقة العمل على أساس آخر أجر كان يتقاضاه العامل [2][13]:

- **عن السنوات الخمس الأولى**: نصف أجر شهري عن كل سنة.
- **عن السنوات التي تلي الخمس الأولى**: أجر شهري كامل عن كل سنة.
- وتُحتسب أجزاء السنة بنسبة ما قضاه العامل في العمل [2][13].

## خامساً: الإجراء القضائي للمطالبة بالحقوق

للعامل الحق في رفع دعوى عمالية للمطالبة بحقوقه، غير أنه يجب أن يسبق قيد الدعوى استيفاء إجراءات التسوية الودية لدى الإدارة المختصة في وزارة الموارد البشرية والتنمية الاجتماعية [5][6]. وتُنشأ لهذا الغرض إدارات للتسوية الودية في مكاتب العمل [6].

تُرفع الدعوى بعد ذلك أمام المحكمة العمالية عبر بوابة ناجز الإلكترونية [27]، مع مراعاة أن المادة 234 من نظام العمل تنص على عدم قبول الدعوى بعد مضي 12 شهراً من تاريخ انتهاء علاقة العمل، ما لم يقدم المدعي عذراً تقبله المحكمة أو يصدر من المدعى عليه إقرار بالحق [4].

## سادساً: أحكام قضائية مشابهة

تضمنت التطبيقات القضائية مبادئ مهمة في دعاوى الفصل دون سبب مشروع:

**1. وجوب وجود سبب مشروع للفصل:** قضى ديوان المظالم بإلغاء قرار إنهاء عقد موظف غير سعودي لعدم ثبوت أسباب عدم الصلاحية المنسوبة إليه، وألزم الجهة بتعويضه براتب شهرين [10]. وفي قضية أخرى، ألغت المحكمة قرار فصل معلمة لعدم ثبوت المخالفات المنسوبة إليها، واعتبرت القرار معيباً بعيب السبب [25].

**2. العذر المرضي يمنع الفصل المشروع:** قضت المحكمة بإلغاء قرار فصل موظف انقطع عن العمل بسبب مرض ثابت بتقارير طبية، واعتبرت العذر مشروعاً ينتفي معه سبب الفصل [9]. كما ألغت المحكمة قرار إنهاء خدمة موظف كان قد حصل على إجازات مرضية وإجازة مرافقة تغطي فترة غيابه [20].

**3. التأخير الجزئي لا يبرر طي القيد:** قضت المحكمة بأن التأخير (ساعات) لا يُعد انقطاعاً عن العمل، وأن طي قيد الموظف لا يجوز إلا إذا بلغ غيابه 15 يوماً متصلة أو 30 يوماً متفرقة دون عذر مشروع [22].

**4. المخالصة النهائية تمنع المطالبة اللاحقة:** قضت المحكمة العمالية (استئناف) برد دعوى عامل كان قد وقّع على مخالصة نهائية، وأيدت أن الإكراه يتطلب تهديداً بخطر جسيم لم يثبت في الدعوى [8]. كما أيدت محكمة الاستئناف حكماً قضى بعدم قبول مطالب عامل بعد أن وقّع على تسوية مستحقات وجدولة سداد [26].

**5. الانقطاع دون عذر مشروع يبرر الفصل:** قضت المحكمة برفض دعوى تعويض موظف انقطع عن العمل دون إجازة نظامية، لثبوت أن قرار طي قيده كان مبرراً لانتفاء ركن الخطأ في حق جهة العمل [17].

---

يتضح مما سبق أن نظام العمل يوفّر للعامل حماية موسعة عند الفصل دون سبب مشروع، تشمل التعويض المالي وبدل الإشعار وشهادة الخدمة والمكافأة، مع ضمانات إجرائية للمطالبة بها. إلا أن ثبوت هذه الحقوق يخضع في النهاية لظروف كل حالة وتقدير المحكمة المختصة.$restore_body$,
       word_count = 727,
       summary_source_length = 4152,
       metadata = metadata || '{"ref_count": 18, "cited_count": 18}'::jsonb,
       summary = $restore_summary$**ملخص المحتوى:**
هذا المستند هو نتاج بحث قانوني (وكيل) يُجيب عن سؤال: ما حقوق العامل عند الفصل دون سبب مشروع في السعودية؟ يغطّي المستند الأحكام النظامية والإجراءات القضائية مع تطبيقات قضائية سابقة.

**المحاور الرئيسية:**
- **التعويض عن الإنهاء غير المشروع (م77):** أجر 15 يوماً عن كل سنة (عقد غير محدد المدة)، أو أجر المدة الباقية (عقد محدد المدة)، مع حد أدنى لا يقل عن شهرين.
- **بدل الإشعار:** مهلة 60 يوماً إذا كان الإنهاء من صاحب العمل (أجر شهري)، و30 يوماً للعامل، وتعويض يساوي الأجر عن المهلة إذا لم تراعَ.
- **شهادة الخدمة:** التزام بتسليمها دون مقابل وغرامة 1,000–3,000 ريال عند الامتناع.
- **مكافأة نهاية الخدمة:** نصف أجر عن كل سنة من أول 5 سنوات، وأجر كامل عن كل سنة بعدها.
- **الإجراء القضائي:** التسوية الودية في وزارة الموارد البشرية أولاً ثم رفع الدعوى عبر ناجز، مع مدة تقادم 12 شهراً من تاريخ انتهاء العلاقة.
- **أحكام قضائية مشابهة:** 5 قضايا تغطي: وجوب سبب مشروع، العذر المرضي، التأخير الجزئي لا يبرر طي القيد، المخالصة النهائية تمنع المطالبة، والانقطاع دون عذر يبرر الفصل.

**الخلاصة:**
المستند كافٍ من حيث الإطار النظامي العام وعرض الأحكام القضائية التطبيقية. الفجوة الرئيسية تكمن في عدم التعمّق في تقدير التعويض القضائي (كيف تحسب المحكمة مقدار التعويض في الممارسة الفعلية) وعدم ذكر تفاصيل التسوية الودية ومدتها الزمنية. المستند مفيد بدرجة جيدة للوكيل التالي لكن قد يحتاج إلى استكمال في جانب التقدير القضائي للتعويضات.$restore_summary$
 where item_id = 'ac478719-4897-48ee-a844-30bbb482da27';
*/
-- ============================================================================
-- END RESTORE BLOCK
-- ============================================================================


begin;


-- ---------------------------------------------------------------------------
-- 1. Conversation title  →  «محادثة تجريبية»
--    Idempotent by value. The tour's sidebar chip is driven by the id, not by
--    this string, so the rename is cosmetic — but it is what the user reads.
-- ---------------------------------------------------------------------------
update conversations
   set title_ar = 'محادثة تجريبية'
 where conversation_id = 'f4804262-da8c-45eb-87c2-911025377d13'
   and title_ar is distinct from 'محادثة تجريبية';


-- ---------------------------------------------------------------------------
-- 2 + 3. Trim 18 → 11 references, then renumber the survivors to a dense 1..11.
--
-- GUARD: true ONLY in the pre-migration shape, and it leads with the ref_pk
-- test because that one cannot rot if the surviving count changes again. The
-- `n > 11` half additionally catches a fixture whose rows were deleted by hand
-- but never renumbered. This guard is the whole reason the migration is safe to
-- re-run: the delete list (3,6,9,10,…) collides head-on with the RENUMBERED
-- survivors, so an unguarded second run would delete four of the eleven cards
-- that are supposed to survive.
--
-- Both statements key on ref_pk — immutable — not on n, which is the column
-- being rewritten. Deleting by n would be correct exactly once; deleting by
-- ref_pk is correct always.
-- ---------------------------------------------------------------------------
do $$
declare
    v_wi     constant uuid := 'ac478719-4897-48ee-a844-30bbb482da27';
    v_dropped constant uuid[] := array[
        '1afdcbdd-7fb4-45d5-af4f-cbf84d5f770e',  -- old  3  regulations  المخالفة 38 (غرامة شهادة الخدمة)
        '6276b618-f668-4f02-ba1f-5f30476ce8b6',  -- old  6  regulations  إنشاء إدارات التسوية الودية
        '6855324a-ff20-464b-9a2d-7798c384f3b3',  -- old  9  cases        العذر المرضي (بلا صفحة مكتبة)
        '1c8b92f6-11f4-4b6c-a5cc-04e0feec9e73',  -- old 10  cases        عدم الصلاحية (بلا صفحة مكتبة)
        '3069130b-fa76-4aad-ad90-c6eaf37e49d2',  -- old 17  cases        الانقطاع دون عذر (بلا صفحة مكتبة)
        '724aa957-cb0a-4c95-ae46-bc034bb0a49a',  -- old 22  cases        التأخير الجزئي (بلا صفحة مكتبة)
        'c8993019-50b9-4a3f-9bd2-ff0af4278963'   -- old 25  cases        فصل معلمة (بلا صفحة مكتبة)
    ]::uuid[];
    v_before int;
    v_after  int;
begin
    if not exists (
        select 1 from workspace_item_references
         where wi_id = v_wi
           and (ref_pk = any (v_dropped) or n > 11)
    ) then
        raise notice '127: references already trimmed for % — skipping steps 2+3', v_wi;
        return;
    end if;

    select count(*) into v_before from workspace_item_references where wi_id = v_wi;

    -- 2. drop the 7 references the rewritten body no longer cites.
    --    old n = 3, 6, 9, 10, 17, 22, 25. NOTE old [4] is NOT in this list —
    --    it is kept and renumbered to [11] (owner decision 2026-08-11).
    delete from workspace_item_references
     where wi_id = v_wi
       and ref_pk = any (v_dropped);

    -- 3a. PHASE A — park every survivor at n + 1000.
    --     UNIQUE (wi_id, n) is NOT DEFERRABLE, so it is enforced row-by-row
    --     inside a single UPDATE. Remapping straight to 1..11 would abort the
    --     moment old 5 → 3 lands on a slot old 3 still occupies mid-statement.
    --     Current n ∈ [1,27]; +1000 lands in [1001,1027] — provably disjoint.
    update workspace_item_references
       set n = n + 1000
     where wi_id = v_wi;

    -- 3b. PHASE B — settle each survivor on its final n. Plan §2.1 mapping,
    --     plus [11] (owner decision 2026-08-11).
    --     ⚠ [3] and [10] ARE LOAD-BEARING FOR THE TOUR — do not resequence.
    update workspace_item_references r
       set n = m.new_n
      from (values
              ('585e3481-b4be-494d-ba6a-b92bb5ee619b'::uuid,  1),  -- old  1  regulations  نظام العمل — انتهاء العقد (م74-79)
              ('fa10b870-9bb5-4d5b-a84e-b8e78f521930'::uuid,  2),  -- old  2  regulations  دليل — الحقوق والمكافآت
              ('a54090ff-af65-42b1-bd96-7c43ee7eb32f'::uuid,  3),  -- old  5  regulations  نظام — تعديل م41 مرافعات  ← ACT 3 ANCHOR (citation-3)
              ('e071587a-f38b-4885-865f-fd6b40155cc4'::uuid,  4),  -- old  8  cases        استئناف عمالي — مخالصة
              ('7df33b31-044e-4688-af5a-8d1799537f25'::uuid,  5),  -- old 11  cases        ابتدائي عمالي — شهادة خدمة ووثائق
              ('f83248d9-946e-4308-bb79-b7a35f736321'::uuid,  6),  -- old 12  regulations  دليل — انتهاء عقد العمل
              ('9c9bcf4d-ef56-4acb-b2ba-e55a71f7dee8'::uuid,  7),  -- old 13  regulations  دليل — إنهاء الخدمة والتقاعد
              ('427b0fe7-3f32-4e92-adb0-43421385bb02'::uuid,  8),  -- old 20  cases        ديوان المظالم — العذر المرضي
              ('6f4ab772-383f-4443-878d-481a58b5b98b'::uuid,  9),  -- old 26  cases        استئناف عمالي — تسوية وجدولة
              ('f5182d98-2f5f-4bc1-9e93-4945839152aa'::uuid, 10),  -- old 27  compliance   خدمة حكومية — ناجز  ← STEP 10 ANCHOR (ref-card-10)
              ('7de92bd1-27b4-4a2c-add9-3fb075591f52'::uuid, 11)   -- old  4  regulations  نظام العمل — م234 التقادم (RESTORED, appended)
           ) as m(ref_pk, new_n)
     where r.wi_id = v_wi
       and r.ref_pk = m.ref_pk;

    select count(*) into v_after from workspace_item_references where wi_id = v_wi;
    raise notice '127: references % → % for %', v_before, v_after, v_wi;
end
$$;


-- ---------------------------------------------------------------------------
-- 4 + 5. Rewrite the body, resync the counters, clear the shared feedback.
--
-- Idempotent: rerunning writes byte-identical values.
--
-- `word_count` and `summary_source_length` are recomputed FROM the new body
-- rather than hardcoded — they are derived columns and the writer_planner reads
-- word_count to size a rewrite (agents/writer_planner/walkers.py:238). The
-- regexp/length pair reproduces the stored values exactly on the old body
-- (727 words / 4,152 chars — verified live), so this is the same arithmetic the
-- pipeline used, not a second opinion.
--
-- `summary` IS REWRITTEN TOO, and that is not scope creep. The stored summary
-- itemises «غرامة 1,000–3,000 ريال» and «5 قضايا» — claims this migration
-- removes from the body. (It also itemises «مدة تقادم 12 شهراً», which SURVIVES
-- as [11] and is therefore kept in the rewritten summary.) Left alone it would
-- be a summary that contradicts the artifact it summarises, on the one WI every
-- new user is walked through, and it feeds writer_planner as ground truth
-- (backend/app/services/writer_planner_context.py:99). No LLM is needed to fix
-- it: it is descriptive text about a body we are authoring here.
-- ---------------------------------------------------------------------------
update workspace_items
   set content_md = $body$إذا أنهي عقد العمل دون سبب مشروع، يقرّر نظام العمل السعودي للعامل عدة حقوق مالية وإجرائية، أبرزها: التعويض عن الإنهاء غير المشروع، وبدل الإشعار (إن لم يُراعَ)، وشهادة الخدمة، ومكافأة نهاية الخدمة، إضافة إلى الحق في التقاضي أمام المحاكم العمالية خلال مدة محددة.

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

يتضح مما سبق أن نظام العمل يوفّر للعامل حماية موسعة عند الفصل دون سبب مشروع، تشمل التعويض المالي وبدل الإشعار وشهادة الخدمة والمكافأة، مع ضمانات إجرائية للمطالبة بها. إلا أن ثبوت هذه الحقوق يخضع في النهاية لظروف كل حالة وتقدير المحكمة المختصة.$body$,

       metadata = metadata || '{"ref_count": 11, "cited_count": 11}'::jsonb,

       -- 5. shared column — must never ship carrying one viewer's thumb.
       feedback = null
 where item_id = 'ac478719-4897-48ee-a844-30bbb482da27';


-- ---------------------------------------------------------------------------
-- 5b. SUMMARY — DELIBERATELY A SEPARATE STATEMENT. DO NOT MERGE IT ABOVE.
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
-- with NULL. The first draft of this migration set both in one UPDATE and would
-- have committed a fixture with NO summary at all. Nothing in the app surfaces
-- that: the summary is invisible in the UI and only read server-side by
-- writer_planner (backend/app/services/writer_planner_context.py:99), so it
-- would have failed silently, on the one WI every new account opens.
--
-- Here content_md is NOT in the SET list, the trigger's IS DISTINCT test is
-- false, and these three columns are left alone.
--
-- `word_count` is NOT set anywhere in this migration: the other BEFORE UPDATE
-- trigger, `set_workspace_item_word_count`, recomputes it from the new body via
-- compute_word_count() — the same regexp this file used to use by hand. One
-- owner for that column, not two.
-- ---------------------------------------------------------------------------
update workspace_items
   set summary = $summary$**ملخص المحتوى:**
هذا المستند هو نتاج بحث قانوني (وكيل) يُجيب عن سؤال: ما حقوق العامل عند الفصل دون سبب مشروع في السعودية؟ يغطّي المستند الأحكام النظامية والإجراءات القضائية مع تطبيقين قضائيين.

**المحاور الرئيسية:**
- **التعويض عن الإنهاء غير المشروع (م77):** أجر 15 يوماً عن كل سنة (عقد غير محدد المدة)، أو أجر المدة الباقية (عقد محدد المدة)، مع حد أدنى لا يقل عن شهرين.
- **بدل الإشعار:** مهلة 60 يوماً إذا كان الإنهاء من صاحب العمل (أجر شهري)، و30 يوماً للعامل، وتعويض يساوي الأجر عن المهلة إذا لم تراعَ.
- **شهادة الخدمة:** التزام بتسليمها دون مقابل وإعادة وثائق العامل، أياً كان سبب الإنهاء.
- **مكافأة نهاية الخدمة:** نصف أجر عن كل سنة من أول 5 سنوات، وأجر كامل عن كل سنة بعدها.
- **الإجراء القضائي:** التسوية الودية لدى وزارة الموارد البشرية أولاً، ثم رفع الدعوى أمام المحكمة العمالية عبر ناجز، مع مدة تقادم 12 شهراً من تاريخ انتهاء العلاقة (م234).
- **تطبيقات قضائية:** مبدآن — العذر المرضي ينفي مشروعية الفصل، والمخالصة النهائية تحجب المطالبة المالية اللاحقة دون أن تُسقط الالتزام بشهادة الخدمة.

**الخلاصة:**
المستند كافٍ من حيث الإطار النظامي العام وعرض التطبيقين القضائيين. الفجوات الرئيسية: عدم التعمّق في تقدير التعويض القضائي (كيف تحسب المحكمة مقداره في الممارسة الفعلية)، وعدم ذكر تفاصيل التسوية الودية ومدتها الزمنية.$summary$,

       -- Both were NULLed by the invalidate trigger when the body landed above;
       -- content_md is already the new value here, so this reads the new length.
       summary_source_length = length(content_md),
       summary_updated_at    = now()
 where item_id = 'ac478719-4897-48ee-a844-30bbb482da27';


-- ---------------------------------------------------------------------------
-- VERIFY — abort the transaction rather than ship a half-trimmed fixture.
--
-- Checks the ONE invariant the product tour depends on and nothing else can
-- enforce: the reference set and the body's marker set are the same set.
-- A dead [n] here is a dead click in Act 3.
-- ---------------------------------------------------------------------------
do $$
declare
    v_wi      constant uuid := 'ac478719-4897-48ee-a844-30bbb482da27';
    v_refs    int[];
    v_markers int[];
    v_expect  constant int[] := array[1,2,3,4,5,6,7,8,9,10,11];
    v_domain  text;
begin
    select array_agg(n order by n) into v_refs
      from workspace_item_references where wi_id = v_wi;

    if v_refs is distinct from v_expect then
        raise exception '127 FAILED: reference n set is %, expected 1..11', v_refs;
    end if;

    select array_agg(distinct m[1]::int order by m[1]::int) into v_markers
      from workspace_items wi,
           lateral regexp_matches(wi.content_md, '\[([0-9]+)\]', 'g') m
     where wi.item_id = v_wi;

    if v_markers is distinct from v_expect then
        raise exception
            '127 FAILED: body cites %, expected exactly 1..11 (dead or missing markers)',
            v_markers;
    end if;

    -- Plan trap 2: [3] is the Act 3 anchor and its properties are asserted by
    -- the tour's copy («فتح النظام في ريحان» + الإحالات). Cheapest half of that
    -- assertion that SQL can make — the rest lives in the frontend test.
    select domain into v_domain
      from workspace_item_references where wi_id = v_wi and n = 3;
    if v_domain is distinct from 'regulations' then
        raise exception '127 FAILED: [3] must be a regulations reference, got %', v_domain;
    end if;

    -- [10] is the step-10 anchor. The whole «كلها ما عدا الخدمات» lesson is
    -- that this ONE card has no library button — which is true only while it is
    -- the compliance reference. Appending [11] made this assertion necessary:
    -- one careless resequence and the tour teaches a falsehood, visibly, to
    -- every new user, with nothing else in the stack to catch it.
    select domain into v_domain
      from workspace_item_references where wi_id = v_wi and n = 10;
    if v_domain is distinct from 'compliance' then
        raise exception '127 FAILED: [10] must be the compliance reference, got %', v_domain;
    end if;

    -- CONTENT INTEGRITY. Every check above validates STRUCTURE — the marker set,
    -- the two pinned domains — and none of them can see a mangled word inside
    -- the prose. This file reaches the database by being transcribed through a
    -- tool boundary (there is no psql and no direct DB URL on the dev machine),
    -- and a single corrupted Arabic character would commit silently and sit in
    -- the one artifact every new account reads. The hashes are of the $body$ /
    -- $summary$ literals in THIS file, computed at authoring time. If you edit
    -- either literal you MUST recompute them, or this migration will refuse to
    -- apply — which is the intended failure mode.
    if md5((select content_md from workspace_items where item_id = v_wi))
       is distinct from '8566ddf07e12850c3e554068f982f98e' then
        raise exception '127 FAILED: content_md md5 mismatch — the body was altered in transit or edited without recomputing the hash';
    end if;

    if md5((select summary from workspace_items where item_id = v_wi))
       is distinct from '5ea0adce7d9d03bc6c959af052d36a42' then
        raise exception '127 FAILED: summary md5 mismatch — see the note above';
    end if;

    raise notice '127 OK: 11 references, markers 1..11, [3] = regulations, [10] = compliance, body+summary hashes match';
end
$$;


commit;
