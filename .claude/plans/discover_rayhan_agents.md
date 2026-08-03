# اكتشف ريحان — Piece 1: The Agents (كيف يعمل ريحان)

First content piece of the اكتشف ريحان hub (`/learn`). This designs the **Agents
section** of the first lesson page — `/learn/how-it-works` («كيف يعمل ريحان —
من السؤال إلى التقرير الموثّق»). Later pieces (usage guide, best practices,
example questions) get their own designs.

**STATUS: BUILT 2026-08-02** (local, validated via Playwright; not yet shipped):
- `frontend/components/learn/HowItWorksView.tsx` — the lesson body (copy below).
- `frontend/app/learn/how-it-works/page.tsx` — indexable, Article JSON-LD,
  in the sitemap `static` section.
- `frontend/app/learn/page.tsx` — hub upgraded from `ComingSoonHub` to a lesson
  grid driven by the site-nav SSoT (enabled child → live card, disabled →
  «قريباً» card). Hub stays `noindex` until a second lesson lands.
- `frontend/lib/nav/site-nav.ts` — how-it-works child flipped `enabled: true`
  (slot renders as a flat link to /learn until a 2nd child is enabled).

## Sources & voice

Inspired by two existing artifacts — stay consistent with both:

- **Pitch deck** (`marketing/pitch_deck/rayhan_pitch_deck.html`, pipeline slide):
  Router picks the specialist → Deep Search plans → retrieves in parallel →
  strict filtering → cited synthesis → Writer drafts from verified research +
  user templates.
- **اكتشف ريحان popup** (`frontend/components/onboarding/` — `STEP_AGENTS` +
  `AgentsDiagram`): سؤالك → الموجّه → {الكاتب، الباحث}، والباحث له وضعان:
  بحث الأنظمة واللوائح (يشمل فحص الالتزام) وبحث الأحكام القضائية.

**Voice rules (hard):**
- General terms only. Describe the three agents by what the reader gains, not
  how they are built.
- NEVER mention: internal agent names (deep_search_v4, aggregator,
  planner_decider…), models, providers, tiers/layers, SSE, prompts, reranking
  internals. The reader-facing vocabulary is: الموجّه، الباحث (بوضعيه)، الكاتب،
  المكتبة القانونية، مساحة العمل، تقرير موثّق.
- Models are described as **open-source, never named** (owner decision
  2026-08-02) — «نماذج مفتوحة المصدر نطوّعها للعمل القانوني السعودي».
- Search mechanics stay **deliberately vague** (owner decision 2026-08-02):
  no query-expansion, no parallelism, no filtering-stage specifics. The public
  story is 3 soft steps — يدرس سؤالك ← يغوص في المكتبة ← ينتقي الأنسب ويعيده
  تقريرًا موثّقًا — not the real 4-stage pipeline.
- Arabic-first; same register as the onboarding popup (خطاب مباشر بصيغة «أنت»).

## Structure (as requested)

```
### ما وكيل الذكاء الاصطناعي؟  ← AI/agents primer (added 2026-08-02)
### الوكلاء
#### البحث: الأنظمة والأحكام   ← regulation & cases search (one section, two scopes)
#### الكاتب                    ← writer
#### الموجّه                    ← router
```

---

## The copy

### قبل أن تتعرف على الفريق — ما وكيل الذكاء الاصطناعي؟

> نماذج الذكاء الاصطناعي الحديثة تجيد اللغة إجادة مدهشة، لكنها وحدها تجيب من
> ذاكرتها — وفي القانون قد يعني ذلك مادة نظامية لا وجود لها. الوكيل هو الحل.

- **النموذج اللغوي** — ذكاء اصطناعي يجيد فهم اللغة: يقرأ ويلخّص ويحلّل
  ويصيغ. لكنه حين يُسأل وحده يجيب من ذاكرته — وقد يخطئ بثقة تامة.
- **الوكيل** — نموذج أُسندت إليه مهمة محددة وأدوات حقيقية يستخدمها: يبحث
  ويقرأ ويتحقق، ثم يبني إجابته على ما وجده أمامه — لا على ما يتذكره.
- **فريق من الوكلاء** — في ريحان يتعاون وكلاء متخصصون على سؤالك، مبنيون
  على نماذج مفتوحة المصدر نطوّعها للعمل القانوني السعودي، ويعملون جميعًا
  على مكتبة قانونية واحدة موثّقة.

### الوكلاء — من يعمل على سؤالك؟

> خلف كل إجابة في ريحان فريق من الوكلاء المتخصصين يعمل معًا: موجّه يفهم
> طلبك، وباحث يغوص في المكتبة القانونية، وكاتب يصوغ مستنداتك. كلٌّ منهم
> متخصص في مهمته — وهذا ما يجعل الإجابة موثّقة لا مرتجلة.

*(Under the intro: reuse `AgentsDiagram` + the `CORPUS_STATS` row from the
onboarding popup — same numbers, one source of truth.)*

#### البحث المعمّق: الأنظمة والأحكام

عندما يحتاج سؤالك إلى بحث، يتولاه أحد وكيلين بحسب ما تبحث عنه:

- **وكيل الأنظمة والامتثال** — يبحث في الأنظمة واللوائح والأدلة التنظيمية
  والخدمات الحكومية والتعاميم، ويشمل ذلك فحص الالتزام: متطلبات الجهات
  الرقابية والاشتراطات والتراخيص التي تنطبق على نشاطك.
- **وكيل الأحكام القضائية** — يبحث في أكثر من ٢٠ ألف حكم منشور ليجد
  السوابق المشابهة لواقعتك والمبادئ التي استقرت عليها المحاكم.

كلاهما يعمل بالطريقة نفسها (kept deliberately vague — see voice rules):

1. **يدرس سؤالك** — يقرأ سؤالك من زواياه المختلفة ويحدد ما يحتاج الوصول
   إليه — لا يكتفي بظاهر الصياغة.
2. **يغوص في المكتبة القانونية** — مصادر سعودية رسمية مفهرسة، يبحث فيها
   من أكثر من اتجاه حتى يغطي سؤالك كاملًا.
3. **ينتقي الأنسب ويعيده تقريرًا موثّقًا** — لا يصلك إلا ما يخدم سؤالك:
   تقرير واحد مرتّب، كل معلومة فيه تحمل مرجعًا مرقّمًا يفتح لك النص الرسمي
   من مصدره.

وأثناء البحث ترى تقدّمه أمامك خطوة بخطوة — تعرف ماذا يبحث ولماذا.

#### الكاتب

عندما تطلب مستندًا — مذكرة، صحيفة دعوى، لائحة اعتراضية، عقدًا — يتولى
الكاتب المهمة:

- **يبدأ من القوالب**: يستعرض عناوين القوالب المخزّنة — قوالب ريحان الجاهزة
  وقوالبك الخاصة التي أضفتها في «قوالبي» — ويختار الأنسب لطلبك، فيخرج
  مستندك على البنية التي اعتدتها.
- **يكتب من بحث موثّق، لا من فراغ**: يعتمد على نتائج البحث في المكتبة
  القانونية، وتنتقل المراجع المرقّمة معه إلى داخل المستند.
- **مسودته تظهر في مساحة العمل** بجانب المحادثة، تراجعها وتطلب تعديلها
  حتى تصل إلى الصيغة النهائية.

#### الموجّه

أول من يقرأ رسالتك:

- **يفهم قصدك** من سؤالك وسياق محادثتك وما جمعته في مساحة العمل.
- **يقرّر من يتولى المهمة**: يجيبك مباشرة على الأسئلة العامة والسريعة،
  ويكلّف الباحث أو الكاتب بما يحتاج تخصصًا.
- **يسألك عند اللبس**: إذا كان في سؤالك أكثر من احتمال — أطراف متعددة،
  جهة غير محددة — يطرح عليك سؤال توضيح قبل أن يبدأ، فدقيقة توضيح توفّر
  بحثًا كاملًا في الاتجاه الخطأ.

---

## Build notes

- **Illustrations (added 2026-08-02, after ship):** the about_us mock report
  card was extracted into `components/landing/ShowcaseReportCard.tsx` (window
  chrome → question → answer with inline [n] → live «المراجع» panel with a
  working «عرض المصدر» dialog; data stays in `landing/content.ts`).
  `SearchShowcase` (about_us) now renders it, and BOTH lessons embed it at
  their references moment: how-it-works after the search steps, workspace in
  «المرجع قبل الإجابة». One showcase, no forks — edit `content.ts` to change
  all three surfaces.

- Surface: `/learn/how-it-works` (currently `ComingSoonHub` placeholder at
  `frontend/app/learn/page.tsx`; the child route doesn't exist yet). Public,
  `SitePageShell`, noindex until the hub has ≥2 lessons (site-nav comment).
- Reuse, don't fork: `AgentsDiagram` and `CORPUS_STATS` import from
  `frontend/components/onboarding/` — corpus numbers stay single-sourced.
- التعاميم caveat: circulars are in the copy (per scope decision) but are not a
  distinct corpus today (~1 doc — see `onboarding-content.ts` header comment).
  Fine to name them as part of the scope; do not give them their own stat.
- «قوالبي» link opportunity: the writer bullet can link to `/templates`.
- The onboarding popup stays 3-step and short; this page is the long-form
  version of its Step 1. If copy drifts, the popup is the one to update — it
  must stay a summary of this page, not diverge from it.
