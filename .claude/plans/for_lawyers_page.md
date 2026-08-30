# «ريحان للقانونيين» — /for-lawyers

Fifth page in the **عن ريحان** menu (section «تعرّف على ريحان»), beside
`/audiences` and `/vs-chatgpt`. NOT a `/learn` lesson: it is a pitch page that
answers an objection, not a how-to.

## Brief (owner)

Address the three fears a Saudi lawyer carries before trying an AI tool:

1. **AI may take my job** — the whole page. Everything else is a short answer.
2. **AI may take my knowledge and use it** — brief, links to `/learn/data-protection`.
3. **AI may take my client data and use it** — brief, links to `/privacy` + `/masking`.

Then answer #1 in three pillars:

- **تقليل وقت الصياغة** — cite the researched numbers, briefly.
- **الإلمام الشامل بالقضية واحتمالاتها**
- **توسيع قاعدة العملاء** — two angles: (a) time saved goes to business
  development; (b) coverage outside your specialty. Owner's framing: a Saudi
  lawyer knows الأحوال الشخصية cold but not نظام المواد البترولية
  والبتروكيماوية; knows نظام الإثبات but not كود البناء السعودي. **Design as
  cards.**

## Route & wiring

| File | Change |
|---|---|
| `app/for-lawyers/page.tsx` | NEW — metadata, Article JSON-LD, `SitePageShell` |
| `components/marketing/ForLawyersView.tsx` | NEW — the page |
| `lib/nav/site-nav.ts` | NEW child under عن ريحان, section «تعرّف على ريحان», between «لمن ريحان؟» and «ريحان مقابل ChatGPT» |
| `lib/seo/sitemap.ts` | `/for-lawyers` into `getStaticUrls()` |
| `components/auth/AuthGuard.tsx` | `/for-lawyers` into `PUBLIC_PREFIXES` |

Nav order is logical: لمن ريحان؟ (who it's for) → ريحان للقانونيين (deep dive on
the primary audience) → ريحان مقابل ChatGPT (why not the alternative). The
legal rows must stay last and adjacent — `groupChildrenBySection` buckets by
CONTIGUOUS runs, so inserting anywhere before them is safe.

## Grounding — every claim, its source

Claims about data are RESTATEMENTS of the legal docs (same hard rule as
`/learn/data-protection`). Corpus claims are MEASURED against prod, not copied
from an older page.

| Claim on the page | Source | Verified |
|---|---|---|
| «لا نستخدم محتواك المُدخَل لتدريب نماذج» | `content/legal/privacy-ar.md:44` — near-verbatim | ✅ |
| وضع السرية default-ON, scope = هوية/جوال/آيبان/بريد | `content/legal/masking-ar.md` + `DEFAULT_PRIVACY_MASKING = true` | ✅ |
| «أكثر من 3,900 نظام ولائحة» | `select count(*) from regulations_v2` → **3,956** | ✅ |
| «38 قطاعاً» | `count(distinct sector)` over `regulations_v2.sectors` → **38** | ✅ |
| «أكثر من 30,000 حكم» | `select count(*) from cases` → **30,531** | ✅ |
| Corpus stat strip | reuses `HERO_TRUST` — single-sourced, deliberately conservative | ✅ |

### Named regulations in the gap cards — all pulled from the corpus verbatim

Per the never-retype-Arabic-predicates rule, each title below was copied out of
`regulations_v2`, and each is `status_class = 'in_force'`:

| تعرفه | ماذا عن…؟ |
|---|---|
| نظام الأحوال الشخصية | نظام المواد الهيدروكربونية |
| نظام الإثبات | كود البناء السعودي العام |
| نظام العمل | نظام الاستثمار التعديني |

**Substitution made — read this before editing the cards.** The owner's brief
named «نظام المواد البترولية والبتروكيماوية». That نظام is **not** a row in
`regulations_v2`. What the corpus holds is «دليل اشتراطات مزاولة العمليات
والأنشطة الخاضعة لنظام المواد البترولية والبتروكيماوية…» (وزارة الطاقة, a
`guide`) — the دليل that hangs off it, not the نظام. Naming the نظام would send
a lawyer looking for something the library cannot open, so the card uses
«نظام المواد الهيدروكربونية» instead: same energy domain, `in_force`, and a real
row. If the نظام is later ingested, swap it back.

`نظام الأحوال الشخصية` and `نظام الإثبات` carry a NULL `clean_title` — they live
in `title`. Any future query for them must `coalesce(clean_title, title)` or it
will wrongly conclude they are missing.

## The research numbers (pillar 1)

Presented as «دراسات عالمية» with each attributed inline, and captioned that no
equivalent Saudi study exists. Four only — the brief said مختصر:

| Number | Claim | Source | Tier |
|---|---|---|---|
| 40–60% | of a lawyer's time on drafting + contract review | Thomson Reuters | vendor — attributed, no methodology published |
| ~17% | of the workday on legal research alone | ABA Profile of the Profession | **independent** |
| 2.9 / 8 | billable hours in an 8-hour day | Clio Legal Trends (7M+ time entries) | vendor, but *measured* not surveyed |
| 200 hrs | AI could free per professional per year | Thomson Reuters Future of Professionals (n=2,200+) | vendor |

Deliberately NOT used: the LexisNexis 15-hrs/week figure (2013, vendor-sponsored,
PDF unverifiable) and the Stanford hallucination study (belongs on `/vs-chatgpt`,
which already carries the no-hallucination claim).

## Public-content rules honored

Same rules as the `/learn` lessons: models described as open-source but never
named; search mechanics stay vague (يوسّع ← يغوص ← ينتقي ويعيد تقريراً موثّقاً),
never the real pipeline; no KSA data-residency claim; no certifications.

## Follow-up: stale corpus numbers sitewide

Not touched by this page — flagged for a separate pass. `HERO_TRUST` says
«+3,000 نظام ولائحة ودليل» and `CORPUS_STATS` says «+3,300»; prod holds 3,956
regulations + 825 guides. Both are still *true* ("أكثر من"), just stale, so
there is no contradiction with this page — but the landing hero, onboarding
popup and nav description all under-sell the corpus and should be refreshed
together.
