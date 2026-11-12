"""Segmentation preview — what the segmenter would make of attachments, and what deep
search vs the writer would receive.

Test tool for ``.claude/plans/attachment_segmentation.md``. It runs the REAL segmenter
(model call included, masking of what the model reads included, the user's words
included) in preview mode and feeds the result through the REAL delivery code
(``build_case_document_blocks_segmented``, ``writer_bodies``). Nothing else:

- nothing is written to the database (no map, no masking fakes, no llm_calls row —
  the user's quota is never charged);
- no planner, deep search, writer or aggregator is called;
- the flags are not touched (preview bypasses the segmenter gate).

Usage, from the worktree root::

    python scripts/segmentation_preview.py --conversation <id> [--out DIR]
    python scripts/segmentation_preview.py --items <item_id,item_id,…> [--out DIR] [--name NAME]
        [--repeat 2] [--min-chars 5000] [--concurrency 4]
    python scripts/segmentation_preview.py --from-json <saved.json> [--out DIR]

Delivery is computed per conversation (the caps are per reader per turn), as if
every attachment of that conversation in the run were named by the planner and
selected by the writer planner. ``--repeat 2`` runs the segmenter twice per
document and reports how far the two runs agree; delivery uses run 1.

Output: ``segmentation_preview_<name>.json`` and ``.html`` in ``--out``. Both hold
customer documents: keep them OUT of the repo.
"""
from __future__ import annotations

import argparse
import asyncio
import html
import json
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agents.deep_search_v4.shared.case_documents import (  # noqa: E402
    PER_DOC_FULL_TEXT_CAP,
    TOTAL_FULL_TEXT_CAP,
    build_case_document_blocks,
    build_case_document_blocks_segmented,
    forwardable_documents,
)
from agents.memory.attachment_segmenter import (  # noqa: E402
    PARAGRAPH_RULE_VERSION,
    PROMPT_VERSION,
    plan_units,
    run_attachment_bundle,
    run_attachment_segmenter,
)
from agents.models import WorkspaceItemSnapshot  # noqa: E402
from agents.utils.attachment_delivery import (  # noqa: E402
    PER_DOC_CAP,
    SEGMENT_MIN_CHARS,
    TOTAL_CAP,
    sections_from_rows,
    writer_bodies,
)

VALUES = ("metadata", "core", "supporting", "boilerplate", "repeat")
_COLS = "item_id, wi_seq, kind, title, summary, content_md, metadata, created_at, conversation_id"


# --------------------------------------------------------------------------- #
# Data
# --------------------------------------------------------------------------- #


def _rows_for_conversation(sb: Any, conversation_id: str) -> list[dict]:
    resp = (
        sb.table("workspace_items").select(_COLS)
        .eq("conversation_id", conversation_id).eq("kind", "attachment")
        .is_("deleted_at", "null").order("wi_seq").execute()
    )
    return list(getattr(resp, "data", None) or [])


def _rows_for_items(sb: Any, item_ids: list[str]) -> list[dict]:
    resp = (
        sb.table("workspace_items").select(_COLS)
        .in_("item_id", item_ids).is_("deleted_at", "null").execute()
    )
    rows = {str(r["item_id"]): r for r in (getattr(resp, "data", None) or [])}
    return [rows[i] for i in item_ids if i in rows]


def _message_of(sb: Any, item_ids: list[str]) -> dict[str, str]:
    """item_id → the message it was attached to (for "sent together" grouping)."""
    try:
        resp = sb.table("message_attachments").select("message_id, document_id").in_("document_id", item_ids).execute()
        return {str(r["document_id"]): str(r["message_id"]) for r in (getattr(resp, "data", None) or [])}
    except Exception:  # noqa: BLE001
        return {}


def _doc_type(row: dict) -> str:
    md = row.get("metadata") or {}
    return str(md.get("doc_type") or "") if isinstance(md, dict) else ""


def _load_prices(sb: Any) -> None:
    """The app loads the pricing registry at startup; a script must do it itself."""
    try:
        from shared import pricing

        pricing.load_pricing(sb)
    except Exception as exc:  # noqa: BLE001
        print(f"pricing not loaded ({type(exc).__name__}) — costs show as 0")


def _cost(model: str, tokens_in: int, tokens_out: int, cached: int = 0) -> float:
    try:
        from agents.utils.agent_models import cost_usd

        return float(cost_usd(model, tokens_in, tokens_out, 0, cached))
    except Exception:  # noqa: BLE001
        return 0.0


def _run_info(res: Any) -> dict:
    model = res.model_used or ""
    return {
        "outcome": res.outcome, "paragraphs": res.paragraphs, "split": res.split,
        "attempts": res.attempts, "segments": res.segments, "repeats": res.repeats,
        "core_chars": res.core_chars, "repairs": res.repairs, "model": model,
        "tokens_in": res.tokens_in, "tokens_out": res.tokens_out,
        "tokens_reasoning": res.tokens_reasoning, "tokens_cached": res.tokens_cached,
        "ms": res.duration_ms, "user_words_chars": getattr(res, "user_words_chars", 0),
        "cost_usd": round(_cost(model, res.tokens_in, res.tokens_out, res.tokens_cached), 5),
        "error": res.error,
    }


def _para_values(segments: list[dict], n_paras: int) -> list[str]:
    vals = [""] * (n_paras + 1)
    for s in segments:
        for p in range(int(s["from_p"]), int(s["to_p"]) + 1):
            if 0 < p <= n_paras:
                vals[p] = s["value"]
    return vals[1:]


def _stability(runs: list[Any], content_len: int) -> dict | None:
    """Agreement between run 1 and run 2 (char-weighted, by paragraph)."""
    ok = [r for r in runs if r.outcome == "ok" and r.preview]
    if len(ok) < 2:
        return None
    a, b = ok[0].preview, ok[1].preview
    paras = a["paragraphs"]
    va = _para_values(a["segments"], len(paras))
    vb = _para_values(b["segments"], len(paras))
    total = sum(len(p["text"]) for p in paras) or 1
    same = sum(len(p["text"]) for p, x, y in zip(paras, va, vb) if x == y)
    core_a = sum(len(s["content_md"]) for s in a["segments"] if s["value"] == "core")
    core_b = sum(len(s["content_md"]) for s in b["segments"] if s["value"] == "core")
    both_core = sum(len(p["text"]) for p, x, y in zip(paras, va, vb) if x == "core" and y == "core")
    either_core = sum(len(p["text"]) for p, x, y in zip(paras, va, vb) if "core" in (x, y))
    return {
        "value_agreement": round(100 * same / total, 1),
        "core_pct_1": round(100 * core_a / max(1, content_len), 1),
        "core_pct_2": round(100 * core_b / max(1, content_len), 1),
        "core_overlap": round(100 * both_core / max(1, either_core), 1),
    }


async def collect(rows: list[dict], *, min_chars: int, concurrency: int, repeat: int, label: str) -> dict:
    from shared.db.client import get_supabase_client

    sb = get_supabase_client()
    _load_prices(sb)
    rows = [r for r in rows if (r.get("content_md") or "").strip()]
    if not rows:
        raise SystemExit("no attachments with text")
    msg_of = await asyncio.to_thread(_message_of, sb, [str(r["item_id"]) for r in rows])

    sem = asyncio.Semaphore(max(1, concurrency))

    # Owner rule D26: per message, every file over the threshold runs alone; the
    # small files of the message run as ONE bundle when they pass the threshold
    # together. All units run in parallel.
    units = plan_units(
        [
            {
                "item_id": str(r["item_id"]),
                "chars": len(r.get("content_md") or ""),
                "message_id": msg_of.get(str(r["item_id"]), ""),
                "order": (r.get("wi_seq") or 0, str(r.get("created_at") or "")),
            }
            for r in rows
        ],
        min_chars=min_chars,
    )

    async def seg(unit: list[str], k: int) -> list[tuple[str, int, Any]]:
        async with sem:
            if len(unit) == 1:
                res = await run_attachment_segmenter(sb, unit[0], preview=True, min_chars=min_chars)
                return [(unit[0], k, res)]
            out = await run_attachment_bundle(sb, unit, preview=True)
            return [(iid, k, out[iid]) for iid in unit if iid in out]

    t0 = time.perf_counter()
    done_units = await asyncio.gather(*(seg(u, k) for u in units for k in range(max(1, repeat))))
    wall_s = round(time.perf_counter() - t0, 1)
    runs: dict[str, list[Any]] = defaultdict(list)
    for iid, k, res in sorted((x for u in done_units for x in u), key=lambda x: (x[0], x[1])):
        runs[iid].append(res)

    sections_by_id = {
        iid: sections_from_rows(rs[0].preview["segments"])
        for iid, rs in runs.items()
        if rs and rs[0].outcome == "ok" and rs[0].preview
    }

    # Delivery per conversation: the caps are per reader per turn.
    by_conv: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_conv[str(r.get("conversation_id") or "")].append(r)
    views: dict[str, dict[str, Any]] = {}
    conv_stats: dict[str, dict] = {}
    for conv, conv_rows in by_conv.items():
        snaps = [
            WorkspaceItemSnapshot(
                item_id=str(r["item_id"]), kind="attachment", title=r.get("title") or "",
                content_md=r.get("content_md") or "", summary=r.get("summary") or "",
                metadata=r.get("metadata") or {}, wi_seq=r.get("wi_seq"),
            )
            for r in conv_rows
        ]
        docs = forwardable_documents(snaps)
        ids = [str(d.item_id) for d in docs]
        st_new: dict = {}
        st_old: dict = {}
        new_b = {b.source_item_id: b.body for b in build_case_document_blocks_segmented(docs, ids, sections_by_id, stats=st_new)}
        old_b = {b.source_item_id: b.body for b in build_case_document_blocks(docs, ids, stats=st_old)}
        un_b = {b.source_item_id: b.body for b in build_case_document_blocks_segmented(docs, [], sections_by_id)}
        w_b, w_st = writer_bodies({str(r["item_id"]): r.get("content_md") or "" for r in conv_rows}, sections_by_id)
        for r in conv_rows:
            iid = str(r["item_id"])
            views[iid] = {
                "forwarded": iid in set(ids),
                "deep_search_named": new_b.get(iid),
                "deep_search_not_named": un_b.get(iid),
                "deep_search_today": old_b.get(iid),
                "writer": w_b.get(iid, r.get("content_md") or ""),
            }
        conv_stats[conv] = {"deep_search": st_new, "today": st_old, "writer": w_st, "docs": len(conv_rows)}

    out_docs = []
    for r in rows:
        iid = str(r["item_id"])
        content = r.get("content_md") or ""
        rs = runs.get(iid) or []
        first = rs[0] if rs else None
        doc = {
            "item_id": iid,
            "conversation_id": str(r.get("conversation_id") or ""),
            "message_id": msg_of.get(iid, ""),
            "wi": f"WI-{r.get('wi_seq')}",
            "doc_type": _doc_type(r),
            "title": r.get("title") or "",
            "summary": r.get("summary") or "",
            "chars": len(content),
            "content_md": content,
            "segmented": iid in sections_by_id,
            "writer_today": content,
            **views.get(iid, {}),
        }
        if first is not None:
            doc["run"] = _run_info(first)
            doc["runs"] = [_run_info(x) for x in rs]
            doc["stability"] = _stability(rs, len(content))
            # Every run's map (no text) — to trace what flips between runs.
            doc["runs_segments"] = [
                [{k: seg[k] for k in ("from_p", "to_p", "label", "value")} | {"chars": len(seg["content_md"])}
                 for seg in x.preview["segments"]]
                for x in rs if x.outcome == "ok" and x.preview
            ]
            if first.preview:
                doc["map"] = first.preview["map"]
                doc["segments"] = first.preview["segments"]
                doc["split_info"] = first.preview.get("split_info") or {}
                doc["user_words"] = first.preview.get("user_words") or ""
                doc["bundle"] = first.preview.get("bundle") or None
        out_docs.append(doc)

    return {
        "name": label,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "prompt_version": PROMPT_VERSION,
        "paragraph_rule_version": PARAGRAPH_RULE_VERSION,
        "min_chars": min_chars,
        "repeat": repeat,
        "wall_s": wall_s,
        "caps": {
            "segment_min_chars": SEGMENT_MIN_CHARS, "per_doc": PER_DOC_CAP, "total": TOTAL_CAP,
            "today_per_doc": PER_DOC_FULL_TEXT_CAP, "today_total": TOTAL_FULL_TEXT_CAP,
        },
        "conversations": conv_stats,
        "documents": out_docs,
    }


# --------------------------------------------------------------------------- #
# Page
# --------------------------------------------------------------------------- #

CSS = """
/* Layout: a summary ledger, then documents grouped by conversation and by the
   message they came with — each a sheet with a proportional map bar, the section
   table, and tabs comparing what each reader receives. */
:root {
  --bg: #f6f7f4; --surface: #ffffff; --fg: #18211c; --muted: #5d6a62; --line: #dfe3dc;
  --accent: #2f6b4f; --accent-soft: #e3efe8; --warn: #9a5b12; --warn-soft: #f6ead8;
  --v-metadata: #4a6fa5; --v-core: #2f7d4f; --v-supporting: #b7862b; --v-boilerplate: #9aa29c; --v-repeat: #b4545f;
  --font-ui: "Inter", system-ui, -apple-system, "Segoe UI", sans-serif;
  --font-head: "Space Grotesk", "Inter", system-ui, sans-serif;
  --font-ar: "Noto Naskh Arabic", "Amiri", "Segoe UI", Tahoma, serif;
  --font-mono: "JetBrains Mono", ui-monospace, Consolas, monospace;
}
@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) {
  --bg: #111613; --surface: #181f1b; --fg: #e4ebe6; --muted: #98a59d; --line: #2a332e;
  --accent: #7cc49d; --accent-soft: #1f3128; --warn: #e0a65a; --warn-soft: #332716;
  --v-metadata: #7d9fd6; --v-core: #5cbf86; --v-supporting: #d9aa4f; --v-boilerplate: #6f7872; --v-repeat: #d97c87;
  color-scheme: dark; } }
:root[data-theme="dark"] {
  --bg: #111613; --surface: #181f1b; --fg: #e4ebe6; --muted: #98a59d; --line: #2a332e;
  --accent: #7cc49d; --accent-soft: #1f3128; --warn: #e0a65a; --warn-soft: #332716;
  --v-metadata: #7d9fd6; --v-core: #5cbf86; --v-supporting: #d9aa4f; --v-boilerplate: #6f7872; --v-repeat: #d97c87;
  color-scheme: dark; }
* { box-sizing: border-box; }
body { background: var(--bg); color: var(--fg); font-family: var(--font-ui); font-size: 14px; line-height: 1.5; }
.wrap { max-width: 1180px; margin: 0 auto; padding-block: 28px 64px;
  padding-left: max(16px, env(safe-area-inset-left, 0px)); padding-right: max(16px, env(safe-area-inset-right, 0px));
  display: grid; gap: 28px; }
h1, h2, h3 { font-family: var(--font-head); text-wrap: balance; margin: 0; letter-spacing: -0.01em; }
h1 { font-size: 26px; } h2 { font-size: 19px; } h3 { font-size: 16px; }
a { color: var(--accent); }
.eyebrow { text-transform: uppercase; letter-spacing: .08em; font-size: 11px; color: var(--muted); font-weight: 600; }
.muted { color: var(--muted); }
.mono, .num { font-family: var(--font-mono); font-variant-numeric: tabular-nums; }
.ar { font-family: var(--font-ar); direction: rtl; text-align: right; line-height: 1.9; }
header.top { display: grid; gap: 10px; }
.facts { display: flex; flex-wrap: wrap; gap: 6px 18px; color: var(--muted); font-size: 13px; }
.facts b { color: var(--fg); font-weight: 600; }
.note { background: var(--accent-soft); border-radius: 8px; padding: 10px 14px; font-size: 13px; margin: 0; }
.note.warn { background: var(--warn-soft); }
.legend { display: flex; flex-wrap: wrap; gap: 8px 16px; font-size: 12px; }
.chip { display: inline-flex; align-items: center; gap: 6px; border-radius: 999px; padding: 1px 9px 1px 7px;
  font-size: 12px; font-weight: 600; border: 1px solid var(--line); background: var(--surface); white-space: nowrap; }
.chip::before { content: ""; width: 8px; height: 8px; border-radius: 50%; background: var(--c, var(--muted)); }
.v-metadata { --c: var(--v-metadata); } .v-core { --c: var(--v-core); } .v-supporting { --c: var(--v-supporting); }
.v-boilerplate { --c: var(--v-boilerplate); } .v-repeat { --c: var(--v-repeat); }
.scroll { overflow-x: auto; }
table { border-collapse: collapse; width: 100%; font-size: 13px; }
th, td { text-align: left; padding: 7px 10px; border-bottom: 1px solid var(--line); vertical-align: top; }
th { font-size: 11px; text-transform: uppercase; letter-spacing: .06em; color: var(--muted); font-weight: 600; }
td.num, th.num { text-align: right; }
.group { display: grid; gap: 14px; }
.group-head { display: grid; gap: 4px; border-top: 2px solid var(--line); padding-top: 14px; }
.sheet { background: var(--surface); border: 1px solid var(--line); border-radius: 12px; padding: 20px; display: grid; gap: 16px; min-width: 0; }
.sheet-head { display: grid; gap: 4px; min-width: 0; }
.bar { display: flex; height: 22px; border-radius: 6px; overflow: hidden; border: 1px solid var(--line); }
.bar span { background: var(--c); min-width: 2px; }
.stats { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 10px; }
.stat { border: 1px solid var(--line); border-radius: 8px; padding: 8px 12px; display: grid; gap: 2px; }
.stat .k { font-size: 11px; color: var(--muted); text-transform: uppercase; letter-spacing: .06em; }
.stat .v { font-family: var(--font-mono); font-variant-numeric: tabular-nums; font-size: 15px; }
.tabs { display: grid; gap: 10px; min-width: 0; }
.tabs > input { position: absolute; opacity: 0; pointer-events: none; }
.tabbar { display: flex; flex-wrap: wrap; gap: 6px; }
.tabbar label { cursor: pointer; border: 1px solid var(--line); border-radius: 8px; padding: 5px 11px; font-size: 13px; background: var(--bg); }
.pane { display: none; min-width: 0; }
pre.txt { white-space: pre-wrap; word-break: break-word; margin: 0; background: var(--bg); border: 1px solid var(--line);
  border-radius: 8px; padding: 14px 16px; max-height: 560px; overflow: auto; font-size: 14px; }
.seg { border-inline-start: 4px solid var(--c); padding: 6px 12px 8px; margin: 0 0 6px; background: var(--bg); border-radius: 4px; }
.seg .h { font-family: var(--font-ui); direction: ltr; text-align: left; font-size: 11px; color: var(--muted); display: flex; gap: 10px; flex-wrap: wrap; }
.seg .h .what { font-family: var(--font-ar); direction: rtl; color: var(--fg); font-size: 13px; }
.seg .body { white-space: pre-wrap; word-break: break-word; }
.segtext { max-height: 640px; overflow: auto; padding: 4px; border: 1px solid var(--line); border-radius: 8px; }
details > summary { cursor: pointer; color: var(--accent); font-weight: 600; }
.why { font-size: 12px; }
:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
@media (max-width: 640px) { .sheet { padding: 14px; } h1 { font-size: 21px; } }
@media (prefers-reduced-motion: reduce) { * { scroll-behavior: auto; } }
"""

_TEXT_HEADINGS = ("#### مقاطع من النص بنصها الأصلي", "#### النص الكامل")
_OVER_TOTAL_MARK = "لم يُرفق النص الكامل"


def _e(text: Any) -> str:
    return html.escape("" if text is None else str(text))


def _chip(value: str) -> str:
    return f'<span class="chip v-{_e(value)}">{_e(value)}</span>'


def _block_text_chars(body: str | None) -> int:
    if body and _OVER_TOTAL_MARK in body:
        return 0
    for heading in _TEXT_HEADINGS:
        if body and heading in body:
            return len(body.split(heading, 1)[1].strip())
    return 0


def _text_label(body: str | None) -> str:
    if body and _OVER_TOTAL_MARK in body:
        return "summary only (total cap spent)"
    n = _block_text_chars(body)
    return f"{n:,}" if n else "summary only"


def _value_chars(doc: dict) -> dict[str, int]:
    out = {v: 0 for v in VALUES}
    for s in doc.get("segments") or []:
        out[s["value"]] = out.get(s["value"], 0) + len(s.get("content_md") or "")
    return out


def _pct(n: int, d: int) -> str:
    return f"{round(100 * n / max(1, d))}%"


def _bar(doc: dict) -> str:
    segs = doc.get("segments") or []
    if not segs:
        return ""
    parts = []
    for s in segs:
        n = max(1, int(s["char_end"]) - int(s["char_start"]))
        title = f'P{s["from_p"]}–P{s["to_p"]} · {s["label"]} · {s["value"]} · {n:,} chars · {s.get("what") or ""}'
        parts.append(f'<span class="v-{_e(s["value"])}" style="flex-grow:{n}" title="{_e(title)}"></span>')
    return f'<div class="bar" role="img" aria-label="Section map by value">{"".join(parts)}</div>'


def _seg_table(doc: dict) -> str:
    rows = []
    for s in doc.get("segments") or []:
        n = len(s.get("content_md") or "")
        rows.append(
            f'<tr><td class="num">{s["seq"]}</td><td class="mono">P{s["from_p"]:03d}–P{s["to_p"]:03d}</td>'
            f'<td class="mono">{_e(s["label"])}</td><td>{_chip(s["value"])}</td>'
            f'<td class="ar">{_e(s.get("what"))}</td><td class="num">{n:,}</td></tr>'
        )
    return (
        '<div class="scroll"><table><thead><tr><th class="num">#</th><th>Paragraphs</th><th>Label</th>'
        '<th>Value</th><th>Description</th><th class="num">Chars</th></tr></thead><tbody>'
        + "".join(rows) + "</tbody></table></div>"
    )


def _segmented_text(doc: dict) -> str:
    blocks = []
    for s in doc.get("segments") or []:
        blocks.append(
            f'<div class="seg v-{_e(s["value"])}"><div class="h"><span class="mono">#{s["seq"]} '
            f'P{s["from_p"]:03d}–P{s["to_p"]:03d}</span><span class="mono">{_e(s["label"])}</span>'
            f'<span>{_e(s["value"])}</span><span class="what">{_e(s.get("what"))}</span></div>'
            f'<div class="body ar">{_e(s.get("content_md"))}</div></div>'
        )
    return '<div class="segtext">' + "".join(blocks) + "</div>"


def _pre(text: str | None) -> str:
    return f'<pre class="txt ar">{_e(text)}</pre>' if text else '<p class="muted">—</p>'


def _tabs(doc: dict, idx: int) -> str:
    panes: list[tuple[str, str]] = []
    if doc.get("segments"):
        panes.append(("Segmented text", _segmented_text(doc)))
    if doc.get("forwarded"):
        panes.append(("Deep search · named", _pre(doc.get("deep_search_named"))))
        panes.append(("Deep search · not named", _pre(doc.get("deep_search_not_named"))))
        panes.append(("Deep search · today", _pre(doc.get("deep_search_today"))))
    panes.append(("Writer", _pre(doc.get("writer"))))
    panes.append(("Writer · today", _pre(doc.get("writer_today"))))
    name = f"t{idx}"
    inputs = "".join(
        f'<input type="radio" name="{name}" id="{name}-{i}"{" checked" if i == 0 else ""}>' for i in range(len(panes))
    )
    labels = "".join(f'<label for="{name}-{i}">{_e(t)}</label>' for i, (t, _) in enumerate(panes))
    bodies = "".join(f'<div class="pane" data-pane="{i}">{b}</div>' for i, (_, b) in enumerate(panes))
    css = "".join(
        f'#{name}-{i}:checked ~ .panes .pane[data-pane="{i}"]{{display:block}}'
        f'#{name}-{i}:checked ~ .tabbar label[for="{name}-{i}"]{{background:var(--accent);color:var(--bg);border-color:var(--accent)}}'
        f'#{name}-{i}:focus-visible ~ .tabbar label[for="{name}-{i}"]{{outline:2px solid var(--accent);outline-offset:2px}}'
        for i in range(len(panes))
    )
    return f'<style>{css}</style><div class="tabs">{inputs}<div class="tabbar">{labels}</div><div class="panes">{bodies}</div></div>'


def _verdict(doc: dict, caps: dict) -> tuple[str, bool]:
    run = doc.get("run") or {}
    bundle = doc.get("bundle") or {}
    if doc["segmented"] and bundle:
        members = len(bundle.get("members") or [])
        return (
            f"Segmented in a bundle with the other small files of its message "
            f"(file {int(bundle.get('index', 0))} of {members}, {int(bundle.get('chars', 0)):,} chars together): "
            "deep search gets its core sections, the writer metadata + core + supporting.",
            False,
        )
    if doc["chars"] <= caps["segment_min_chars"] and not doc["segmented"]:
        return "Under 5,000 characters and not part of a bundle: travels whole, not segmented.", False
    if doc["segmented"]:
        fwd = "deep search gets the core sections" if doc.get("forwarded") else "not forwarded to deep search (its doc type stays with the planner)"
        return f"Segmented: {fwd}; the writer gets metadata + core + supporting.", False
    return f"No usable map ({run.get('outcome', 'not run')}): every reader gets the full text within the caps.", True


def _sheet(doc: dict, idx: int, caps: dict) -> str:
    run = doc.get("run") or {}
    vc = _value_chars(doc)
    verdict, warn = _verdict(doc, caps)
    stab = doc.get("stability") or {}
    stats = [
        ("Characters", f"{doc['chars']:,}"),
        ("Paragraphs", f"{run.get('paragraphs', 0):,}" if run else "—"),
        ("Core", f"{vc['core']:,} · {_pct(vc['core'], doc['chars'])}" if doc.get("segments") else "—"),
        ("Supporting", f"{vc['supporting']:,} · {_pct(vc['supporting'], doc['chars'])}" if doc.get("segments") else "—"),
        ("Deep search text", _text_label(doc.get("deep_search_named")) if doc.get("forwarded") else "not forwarded"),
        ("Today: deep search", _text_label(doc.get("deep_search_today")) if doc.get("forwarded") else "not forwarded"),
        ("Writer gets", f"{len(doc.get('writer') or ''):,}"),
    ]
    if stab:
        stats.append(("Two runs: core %", f"{stab['core_pct_1']} / {stab['core_pct_2']}"))
        stats.append(("Two runs: agree", f"{stab['value_agreement']}%"))
    stat_html = "".join(f'<div class="stat"><span class="k">{_e(k)}</span><span class="v">{_e(v)}</span></div>' for k, v in stats)
    run_line = ""
    if run:
        runs = doc.get("runs") or [run]
        run_line = (
            f'<div class="facts mono"><span>outcome <b>{_e(" / ".join(r["outcome"] for r in runs))}</b></span>'
            f'<span>split <b>{"yes" if run["split"] else "no"}</b></span>'
            f'<span>sections <b>{run["segments"]}</b></span><span>repeats <b>{run["repeats"]}</b></span>'
            f'<span>attempts <b>{run["attempts"]}</b></span>'
            f'<span>tokens <b>{run["tokens_in"]:,} in · {run["tokens_out"]:,} out</b></span>'
            f'<span>time <b>{" / ".join(str(round(r["ms"] / 1000)) for r in runs)} s</b></span>'
            f'<span>cost <b>${sum(r["cost_usd"] for r in runs):.4f}</b></span>'
            f'<span>user words <b>{run.get("user_words_chars", 0):,} chars</b></span>'
            + (f'<span>repairs <b>{_e(json.dumps(run["repairs"]))}</b></span>' if run.get("repairs") else "")
            + (f'<span>error <b>{_e(run["error"])}</b></span>' if run.get("error") else "")
            + "</div>"
        )
    uw = doc.get("user_words") or ""
    uw_html = (
        f'<details><summary>What the user said ({len(uw):,} chars)</summary><pre class="txt ar">{_e(uw)}</pre></details>'
        if uw else ('<p class="muted">No user words found for this file.</p>' if run else "")
    )
    m = doc.get("map") or {}
    docs_in = m.get("documents_in_file") or []
    docs_html = ""
    if docs_in:
        items = "".join(
            f'<li><span class="mono">P{d.get("from_p", 0):03d}–P{d.get("to_p", 0):03d}</span> '
            f'<span class="ar">{_e(d.get("name") or d.get("name_ar"))}</span></li>'
            for d in docs_in
        )
        docs_html = f'<details><summary>Documents inside the file ({len(docs_in)})</summary><ul>{items}</ul></details>'
    keys = m.get("key_paragraphs") or []
    keys_html = ""
    if keys:
        items = "".join(
            f'<li><span class="mono">P{int(k.get("p", 0)):03d}</span> <span class="ar why">{_e(k.get("why"))}</span></li>'
            for k in keys
        )
        keys_html = f'<details><summary>Key paragraphs ({len(keys)})</summary><ul>{items}</ul></details>'
    table = f'<details open><summary>Sections ({len(doc["segments"])})</summary>{_seg_table(doc)}</details>' if doc.get("segments") else ""
    type_chip = f'<span class="chip">{_e(doc["doc_type"] or "type unset")}</span>'
    return f"""
<section class="sheet" id="d{idx}">
  <div class="sheet-head"><span class="eyebrow">{_e(doc['wi'])} · {type_chip}</span><h3 class="ar">{_e(doc['title'])}</h3></div>
  <p class="note{' warn' if warn else ''}">{_e(verdict)}</p>
  {_bar(doc)}
  <div class="stats">{stat_html}</div>
  {run_line}
  {uw_html}
  {docs_html}
  {table}
  {keys_html}
  {_tabs(doc, idx)}
</section>"""


def render(data: dict) -> str:
    caps = data["caps"]
    docs = data["documents"]
    index = {d["item_id"]: i for i, d in enumerate(docs)}
    total_cost = sum(sum(r["cost_usd"] for r in (d.get("runs") or [])) for d in docs)
    seg_n = sum(1 for d in docs if d["segmented"])
    stabs = [d["stability"] for d in docs if d.get("stability")]
    rows = []
    for d in docs:
        vc = _value_chars(d)
        st = d.get("stability") or {}
        run = d.get("run") or {}
        rows.append(
            f'<tr><td><a href="#d{index[d["item_id"]]}">{_e(d["wi"])}</a> <span class="muted mono">{_e(d["conversation_id"][:8])}</span></td>'
            f'<td class="mono">{_e(d["doc_type"] or "unset")}</td><td class="num">{d["chars"]:,}</td>'
            f'<td>{"split" if run.get("split") else ("single" if run else "—")}</td>'
            f'<td class="num">{_pct(vc["core"], d["chars"]) if d.get("segments") else "—"}</td>'
            f'<td class="num">{(str(st["core_pct_1"]) + " / " + str(st["core_pct_2"])) if st else "—"}</td>'
            f'<td class="num">{(str(st["value_agreement"]) + "%") if st else "—"}</td>'
            f'<td class="num">{_text_label(d.get("deep_search_named")) if d.get("forwarded") else "—"}</td>'
            f'<td class="num">{_text_label(d.get("deep_search_today")) if d.get("forwarded") else "—"}</td>'
            f'<td class="num">{len(d.get("writer") or ""):,}</td></tr>'
        )
    # groups: conversation → message (files sent together)
    groups: dict[tuple[str, str], list[int]] = defaultdict(list)
    for i, d in enumerate(docs):
        groups[(d["conversation_id"], d.get("message_id") or "")].append(i)
    group_html = []
    for (conv, msg), idxs in groups.items():
        together = f" · {len(idxs)} files sent together in one message" if msg and len(idxs) > 1 else ""
        cst = (data.get("conversations") or {}).get(conv, {}).get("deep_search", {})
        group_html.append(
            f'<div class="group"><div class="group-head"><span class="eyebrow">conversation</span>'
            f'<h2 class="mono">{_e(conv[:8])}{_e(together)}</h2>'
            f'<span class="muted">deep search text for this conversation: {cst.get("full_text_chars", 0):,} chars · '
            f'trim factor {cst.get("trim_factor", 1.0)}</span></div>'
            + "".join(_sheet(docs[i], i, caps) for i in idxs) + "</div>"
        )
    agree = (
        f'<span>two-run agreement <b>{round(sum(s["value_agreement"] for s in stabs) / len(stabs), 1)}% avg · '
        f'min {min(s["value_agreement"] for s in stabs)}%</b></span>'
        if stabs else ""
    )
    legend = "".join(_chip(v) for v in VALUES)
    name = data.get("name") or "run"
    return f"""<title>Segmenter Preview {_e(name)}</title>
<meta name="claude:viewport-fit-sides" content="cover">
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600&family=Space+Grotesk:wght@500;600&family=Noto+Naskh+Arabic:wght@400;600&family=JetBrains+Mono:wght@400;600&display=swap">
<style>{CSS}</style>
<div class="wrap">
<header class="top">
  <span class="eyebrow">Attachment segmentation · preview run</span>
  <h1>Segmenter Preview {_e(name)}</h1>
  <div class="facts">
    <span>prompt <b class="mono">{_e(data['prompt_version'])}</b></span>
    <span>paragraphs <b class="mono">{_e(data['paragraph_rule_version'])}</b></span>
    <span>run <b>{_e(data['generated_at'])}</b></span>
    <span>documents <b>{len(docs)} in {len(groups)} groups</b></span>
    <span>segmented <b>{seg_n}</b></span>
    <span>runs per document <b>{data.get('repeat', 1)}</b></span>
    <span>segmenter cost <b>${total_cost:.4f}</b></span>
    <span>wall time <b>{data['wall_s']} s</b></span>
    {agree}
  </div>
  <p class="note">Preview only. The segmenter ran for real — what the model reads was masked, and it read the user's own messages up to the upload — and its output went through the real delivery code. Nothing was stored, no planner, deep search or writer was called, and nothing was charged to any user. Delivery is computed per conversation as if the planner named every document here. Deep search reads masked text in production; this page shows the real text.</p>
  <div class="legend">{legend}</div>
</header>
<section class="sheet">
  <h2>Summary</h2>
  <p class="muted">Caps: {caps['per_doc']:,} per document and {caps['total']:,} per reader (today {caps['today_per_doc']:,} and {caps['today_total']:,}). Deep search gets core; the writer gets metadata + core + supporting, uncapped.</p>
  <div class="scroll"><table>
    <thead><tr><th>Item</th><th>Type</th><th class="num">Chars</th><th>Mode</th><th class="num">Core</th><th class="num">Core % run 1 / 2</th><th class="num">Agree</th><th class="num">Deep search text</th><th class="num">Today</th><th class="num">Writer</th></tr></thead>
    <tbody>{''.join(rows)}</tbody>
  </table></div>
</section>
{''.join(group_html)}
</div>"""


def _outside_repo(path: str) -> Path:
    """The output holds customer documents: refuse any folder inside the repo."""
    out = Path(path).resolve()
    if out == ROOT or ROOT in out.parents:
        raise SystemExit(f"--out must be OUTSIDE the repo ({ROOT}); use the scratchpad")
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("conversation_id", nargs="?", default="")
    ap.add_argument("--conversation", default="")
    ap.add_argument("--items", default="", help="comma list of workspace item ids")
    ap.add_argument("--name", default="")
    ap.add_argument("--out", required=True, help="a folder OUTSIDE the repo (output holds customer documents)")
    ap.add_argument("--min-chars", type=int, default=SEGMENT_MIN_CHARS)
    ap.add_argument("--concurrency", type=int, default=4)
    ap.add_argument("--repeat", type=int, default=1)
    ap.add_argument("--from-json", default="", help="re-render a saved run (no model call)")
    args = ap.parse_args()
    if args.from_json:
        data = json.loads(Path(args.from_json).read_text(encoding="utf-8"))
        name = data.get("name") or Path(args.from_json).stem.replace("segmentation_preview_", "")
    else:
        from shared.db.client import get_supabase_client

        sb = get_supabase_client()
        conv = args.conversation or args.conversation_id
        if args.items:
            rows = _rows_for_items(sb, [x.strip() for x in args.items.split(",") if x.strip()])
            name = args.name or f"{len(rows)}docs"
        elif conv:
            rows = _rows_for_conversation(sb, conv)
            name = args.name or conv[:8]
        else:
            raise SystemExit("give --conversation, --items or --from-json")
        data = asyncio.run(collect(rows, min_chars=args.min_chars, concurrency=args.concurrency, repeat=args.repeat, label=name))
    out = _outside_repo(args.out)
    out.mkdir(parents=True, exist_ok=True)
    stem = f"segmentation_preview_{name}"
    (out / f"{stem}.json").write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    (out / f"{stem}.html").write_text(render(data), encoding="utf-8")
    for d in data["documents"]:
        run = d.get("run") or {}
        st = d.get("stability") or {}
        core = _pct(_value_chars(d)["core"], d["chars"]) if d.get("segments") else "-"
        print(f"{d['conversation_id'][:8]} {d['wi']:>6} {d['doc_type'] or 'unset':<16} {d['chars']:>7,} "
              f"{run.get('outcome', 'not run'):<15} split={run.get('split', '-')!s:<5} core={core:>4} "
              f"runs={st.get('core_pct_1', '-')}/{st.get('core_pct_2', '-')} agree={st.get('value_agreement', '-')} "
              f"uw={run.get('user_words_chars', 0)}")
    print(f"written {out / (stem + '.html')}")


if __name__ == "__main__":
    main()
