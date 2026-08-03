# اكتشف ريحان — Piece 3: Data Protection (كيف نحمي بياناتك وبيانات عملائك؟)

Third lesson of the اكتشف ريحان hub — NEW endpoint `/learn/data-protection`.
Marketing-register companion to the two legal pages (`/privacy`, `/masking`).

**STATUS: BUILT 2026-08-02** (local):
- `frontend/components/learn/DataProtectionView.tsx` — the lesson body.
- `frontend/app/learn/data-protection/page.tsx` — indexable, Article JSON-LD.
- `frontend/lib/nav/site-nav.ts` — «حماية البيانات» child enabled (3rd live
  lesson in the اكتشف ريحان dropdown; hub grid follows automatically).
- `frontend/lib/seo/sitemap.ts` — `/learn/data-protection` in static section.
- `frontend/app/learn/page.tsx` — hub description mentions the lesson.

## Brief (owner, 2026-08-02)

Produce «كيف نحمي بياناتك وبيانات عملائك؟». Begin with our care that data
does NOT leave our servers; then honestly explain that serving the user
requires relying on other vendors for processing; add خدمة تقنيع المعرّفات;
reassure that we rely on global partners "such as Alibaba" / of good repute.

## Grounding rule (hard)

**Every claim on this page is a restatement of `/privacy` or `/masking`.**
If a claim needs to change, change the legal page FIRST, then this lesson.
Claims used and their sources:

| Lesson claim | Source |
|---|---|
| Account isolation enforced in the DB itself (RLS, reader-level wording) | privacy §3, §6 |
| Encrypted transport, access control, privacy-by-design | privacy §6 |
| No selling; no training general models on user content | privacy §3, §4 |
| Processor categories: AI models / OCR / hosting+monitoring; bound to our instructions, minimum necessary | privacy §4 |
| Masking mechanics: detect IDs/phones/IBAN/email → lookalike substitutes → table never leaves → auto-restore on display | /masking |
| Worked example numbers (1032323434 → 1032849275 etc.) | /masking — SAME numbers, keep in sync |
| Amounts/dates/article numbers pass through; names out of scope → use roles | /masking |
| وضع السرية default-ON, toggle in settings | preferences-store DEFAULT_PRIVACY_MASKING = true (087) |
| Account deletion with undo grace | delete-account flow (090) |

## Named partner decision

Alibaba Cloud is the ONE named processing partner (owner asked for "global
partners such as alibaba (or with a good repetition)"). Models remain
open-source and unnamed (piece-1 voice rules). Other processors stay
categorical («مزوّد متخصص») — do not name the OCR or hosting vendors.

**Do NOT claim**: KSA data residency (the SCCC/Riyadh move is still planning —
`project_ksa_data_residency_migration`), certifications, or "100% security".
The hero phrasing is deliberately «لا تغادر خوادمنا **إلا** للمعالجة اللازمة»,
never an absolute.

## Structure

```
Hero      كيف نحمي بياناتك وبيانات عملائك؟ (owner's exact H1)
§1        بياناتك تبقى عندنا           ← 3 cards: isolation / encryption+access
                                         / no-sell no-train
§2        لماذا نستعين بموردين للمعالجة؟ ← honest need + 3 processor cards
                                         (models=Alibaba Cloud named, OCR,
                                         hosting) + contractual-bound callout
                                         linking /privacy
§3        تقنيع المعرّفات               ← 3 numbered steps + worked example box
                                         (numbers copied from /masking) +
                                         limits note linking /masking
§4        وأنت بيدك القرار             ← masking toggle / PDPL rights / delete
                                         account / sharing is opt-in
CTA       جرّب ريحان مجاناً + سياسة الخصوصية
```
