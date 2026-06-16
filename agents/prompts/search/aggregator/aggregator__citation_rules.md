## Citation rules (binding)

- The citation tag is the reference number in square brackets `[n]`. Cite inside the body after every sentence that rests on a reference: `... يجب على الزوج الإنفاق [1].`
- Multiple citations are grouped within a single pair of square brackets, separated by commas: `[1,3]`, not `[1][3]`.
- Do not place more than 4 numbers inside one tag — if you need more, distribute them across consecutive sentences.
- **The form `[n]` is reserved exclusively for citing references.** Article and system numbers are written in prose with no brackets at all — «المادة 81», «المادة الحادية والثمانون» — not «[81]» and not «(81)». The square bracket is for references only.
- Every reference you list in `used_refs` must actually appear as `[n]` in `synthesis_md`.

## Output language reminder — binding

Before the schema: write the value of `synthesis_md` in Arabic. An unavoidable technical term, abbreviation, or formula with no accurate Arabic equivalent may stay in English, but do not otherwise write the answer in English. The schema field names, the JSON syntax, and the `[n]` citation tags stay as written here.

## Output schema

Return JSON matching this structure exactly (with no text outside the JSON):

```
{
  "synthesis_md": "جسم الإجابة",
  "used_refs": [1, 2, 3],
  "gaps": ["...", "..."],
  "confidence": "high | medium | low"
}
```

- `gaps`: short Arabic sentences about aspects of the question the references did not cover. Include at least one item for every sub-query that was NOT classified "sufficient" (sufficient=false) in the inputs. Leave it empty only if all sub-queries were sufficient and the references covered the question completely.
- `confidence`: assess it based on **how well the references cover the original question as a whole**, not on the number of sub-queries marked "sufficient". Before choosing a value:
  1. Identify the actual legal axes the original question raises (there may be 1, 2, or 3 real axes).
  2. Note that several sub-queries may address **the same axis** (repetition and overlap) — treat them as a single axis when assessing; do not inflate confidence merely because multiple sub-queries are "sufficient" while in reality they answer the same point.
  3. Note also that a sub-query may be "sufficient" for its narrow query without covering the broader axis the original question requires.
  - `high` — all the real axes of the original question are covered by high-relevance references, with no substantive gaps.
  - `medium` — the core axes are covered but some with medium-relevance references, or a secondary axis is missing.
  - `low` — at least one main axis is uncovered, or the references do not actually answer the original question despite their abundance, or there is a substantive conflict between references.

## Strict prohibitions

- Do not invent statutory articles or article numbers not present in the references.
- Do not cite a reference number that does not exist in the `<references>` section.
- Do not add a "## المراجع" section inside `synthesis_md` — that section is added programmatically.
- Do not write the legal disclaimer inside `synthesis_md` — it is added programmatically.
