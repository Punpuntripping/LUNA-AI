## Output schema

Return JSON matching this structure exactly (with no text outside the JSON).
The `title_ar`, `heading_ar`, and `body_md` values MUST be written in Modern
Standard Arabic — only the JSON field identifiers are English:

```
{
  "title_ar": "عنوان المستند",
  "sections": [
    {"heading_ar": "## الأطراف", "body_md": "..."},
    {"heading_ar": "## الموضوع", "body_md": "..."}
  ],
  "citations_used": [
    {"wi": "WI-2", "n": 5},
    {"wi": "WI-1", "n": 17}
  ],
  "confidence": "high | medium | low",
  "notes_ar": ["نقطة تحتاج مراجعة المستخدم", "..."],
  "chat_summary": "جملة أو جملتان تصفان المستند المُسوَّد — 500 حرف كحد أقصى.",
  "key_findings": [
    "أبرز نقطة يجب على المستخدم مراجعتها",
    "نقطة ثانية",
    "نقطة ثالثة"
  ]
}
```

- `sections` are ordered as they will appear in the final document.
- Do not repeat the full title in `sections[0]` -- it is added from `title_ar`.
- `citations_used` includes every actual citation that appeared in body_md as `(n)` — each entry is a `(wi, n)` pair that pinpoints the source precisely (e.g. `{wi: "WI-2", n: 5}`). The number `n` is the same one shown in `(n)` inside the body; the `wi` field identifies the source item (from `<source wi="WI-N">` in the writing package) to remove ambiguity when sources overlap.
- `chat_summary`: a brief description of the document in **500 characters maximum, strict**. Do not re-draft the whole document.
- `key_findings`: **3 to 5 items maximum, strict**. Each item is a point that needs the user's attention or review. Do not exceed 5 items under any circumstances.
