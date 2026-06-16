# agents/prompts — prompt reference catalog

A clean, human-readable copy of **every agent prompt** in the pipeline, one
`.md` per prompt, **pure prompt text only** (the Python variable name and
wrapper are stripped; brand names like ريحان are kept).

**Reference only.** The `.py` files remain the source of truth — nothing here
is loaded at runtime. Edit prompts in code, then regenerate this folder.

## Regenerate

```bash
python scripts/extract_prompts_md.py            # full — re-imports modules (slow, ~minutes; renders f-strings & composed prompts to real text)
python scripts/extract_prompts_md.py --ast-only # fast — only the inline-in-.py prompts (router, artifact_editor, rerankers, populate_sectors)
```

The extractor reads the **live values**, so module-level f-strings (e.g. the
sector catalog) come out fully rendered. Truly dynamic substitutions that can't
be resolved statically are left as `{placeholder}` markers (e.g.
`{MAX_ATTACHED_ITEMS}` in `router__system.md`, `{user_request}` in builders).

## Layout

Prompts are grouped into domain subfolders (the extractor's `SUBDIRS` map
controls this; re-runs write straight into the tree):

```
search/
  reg/            reg_search expander + reranker (+ populate_sectors offline tool)
  case/           case_search expander variants + reranker
  compliance/     compliance_search expander + reranker
  sector_picker/  sector_picker system prompt + rendered catalog
  planner/        planner decider + responder + mode framings
  aggregator/     synthesis prompts (shared role, citation rules, mode/only variants)
writers/          writer (role + subtypes + output contract) + writer_planner
memory/           artifact_summarizer + artifact_editor
template/         template_ingester
router/           router system prompt
```

## Naming

`<agent>__<role>[__<variant>].md` — `<role>` is the symbol with
`SYSTEM`/`PROMPT`/`AR` tokens stripped; `<variant>` is the dict key for
multi-variant prompts (e.g. `search/case/case_search__expander__prompt_3.md`).
The `<agent>` prefix is kept so a file is self-describing even if moved.

## Language status (English-migration progress)

| Agent | Files | Instruction language |
|-------|-------|----------------------|
| reg_search | `expander__prompt_1`, `reranker__prompt_1`, `reranker_retry_msg` | **English** ✅ (migrated + validated) |
| writer_planner | `system` | **English** (was already English) |
| case_search | `expander__prompt_1/2/3`, `reranker__prompt_1`, `reranker_retry_msg` | Arabic — pending |
| compliance_search | `expander`, `reranker`, `reranker_retry_msg` | Arabic — pending |
| sector_picker | `system`, `catalog` | Arabic — pending |
| aggregator | `shared_role`, `citation_rules`, `1_crac`, `2_irac`, `3_draft`, `3_critique`, `3_rewrite`, `4_thematic`, `reg_only`, `cases_only`, `comp_only`, `cases_focus`, `mode_case`, `mode_reg`, `mode_compliance`, `mode_full` | Arabic — pending |
| planner | `decider`, `responder`, `mode_framing__{reg_led,case_led,compliance_led,full}`, `brief_detail_rules`, `decider_context_header` | Arabic — pending |
| writer | `shared_role`, `output_contract`, `system__{6 subtypes}`, `subtype_bodies__{6 subtypes}` | Arabic — pending |
| artifact_summarizer | `system`, `attachment` | Arabic — pending |
| template_ingester | `system` | Arabic — pending |
| artifact_editor | `system`, `retry_msg`, `reason` | Arabic — pending |
| router | `system` | Arabic — pending |
| populate_sectors | `system` | Arabic (offline data tool, not in request path) |

## Notes

- **Component vs composed:** `aggregator__shared_role` / `__citation_rules` and
  `writer__shared_role` / `__output_contract` / `__subtype_bodies__*` are the
  building blocks; the full assembled prompts are `aggregator__mode_*` /
  `aggregator__*_only` / `writer__system__*`. Both are listed for traceability.
- `sector_picker__catalog` is the rendered 38-sector catalog embedded inside
  `sector_picker__system`.
- `*_retry_msg` and `artifact_editor__reason` are short model-facing strings
  (ModelRetry text), included for completeness.
- The verbatim **Arabic backup** (pre-migration, with code) lives separately in
  `agents_reports/AR_prompts/`.
