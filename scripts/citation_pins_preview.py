"""Citation pins preview — what the attachment flow would do with ONE real file.

Test tool for ``.claude/plans/completion_search.md`` §4.6–4.7. It runs the REAL code
path — the scan, the library facts, the decisions, the delivery under the stored
segments, the card text — and stores NOTHING:

- nothing is written to the database (no scan row, no citation rows, no card);
- by default no model is called. ``--agent`` runs the real agent step on what code
  could not prove (a live model call, outside any ledger scope, so nobody is charged;
  masking fakes are kept in memory only);
- the flags are not read: this shows what WOULD happen with them on;
- migrations 229–231 are not needed (the rows it would store are kept in memory).

Usage, from the worktree root::

    python scripts/citation_pins_preview.py --file <user_files.file_id> [--out DIR]
    python scripts/citation_pins_preview.py --item <workspace_items.item_id> [--out DIR]
    python scripts/citation_pins_preview.py --file <id> --show          # print the texts too
    python scripts/citation_pins_preview.py --file <id> --agent         # + the agent step, live

Without ``--show`` only counts and the citation table are printed. ``--out`` (a folder
OUTSIDE every git repository) gets the full texts: what deep search would read, what
the writer would read, and the card. Those files hold a user's document — keep them
out of the repo.
"""
from __future__ import annotations

import argparse
import asyncio
import io
import json
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _inside_git(path: Path) -> bool:
    p = path.resolve()
    return any((q / ".git").exists() for q in [p, *p.parents])


def _load_file(sb: Any, file_id: str | None, item_id: str | None) -> dict:
    if not file_id:
        rows = sb.table("workspace_items").select("item_id,file_id").eq("item_id", item_id).limit(1).execute().data or []
        if not rows or not rows[0].get("file_id"):
            raise SystemExit("that item has no file")
        file_id = rows[0]["file_id"]
    rows = (
        sb.table("user_files").select("file_id,user_id,title,doc_type,content_md,content_chars")
        .eq("file_id", file_id).limit(1).execute().data or []
    )
    if not rows:
        raise SystemExit("file not found")
    return rows[0]


def _segment_rows(sb: Any, file_id: str) -> list[dict]:
    return (
        sb.table("attachment_segments").select("*").eq("item_id", file_id).order("seq").execute().data or []
    )


def main() -> None:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default="")
    ap.add_argument("--item", default="")
    ap.add_argument("--out", default="", help="folder OUTSIDE every git repository for the full texts")
    ap.add_argument("--show", action="store_true", help="also print the delivered text and the card")
    ap.add_argument("--agent", action="store_true",
                    help="run the agent step for real on what code could not prove (a live model call)")
    args = ap.parse_args()
    if not (args.file or args.item):
        raise SystemExit("give --file or --item")
    out_dir = Path(args.out) if args.out else None
    if out_dir and _inside_git(out_dir):
        raise SystemExit("--out is inside a git repository; choose a folder outside it")

    from agents.completion_search.lookups import load_title_index
    from agents.memory.citation_pins import store
    from agents.memory.citation_pins.resolve import PINNED, counts, resolve_citations
    from agents.memory.citation_pins.runner import SKIPPED_DOC_TYPES
    from agents.tool_repository.citation_scan import scan_citations
    from agents.utils.attachment_delivery import (
        DEEP_SEARCH_VALUES,
        WRITER_VALUES,
        attach_pins,
        kept_chars,
        render_sections,
        sections_from_rows,
        with_loose_pins,
    )
    from shared.db.client import get_supabase_client
    from shared.workspace.pinned_texts import attachment_entries, card_summary, render_card

    sb = get_supabase_client()
    row = _load_file(sb, args.file or None, args.item or None)
    file_id, content, doc_type = row["file_id"], row.get("content_md") or "", row.get("doc_type") or ""
    print(f"file {file_id}  type={doc_type or '—'}  chars={len(content)}")
    if doc_type in SKIPPED_DOC_TYPES:
        print("  a contract: never scanned (owner decision C16). Showing what a scan WOULD find, for reference.")

    # ---- 1. scan + decide (the production functions) ----
    t0 = time.perf_counter()
    titles = load_title_index(sb)
    t_titles = time.perf_counter() - t0
    t0 = time.perf_counter()
    scan = scan_citations(content, doc_type or None, titles)
    t_scan = time.perf_counter() - t0
    t0 = time.perf_counter()
    facts = store.load_facts(sb, scan, titles)
    citations = resolve_citations(scan, facts)
    t_decide = time.perf_counter() - t0
    tally = counts(scan, citations)
    print(f"\nscan: {tally['mentions']} citing mentions, {tally['headings']} own headings, "
          f"{len(scan.named_laws)} law names ({len(scan.distinct_named())} distinct), "
          f"{len(citations)} citations")
    print(f"time: title list {t_titles:.2f}s (cached per process) · scan {t_scan * 1000:.0f} ms · "
          f"library facts + decisions {t_decide:.2f}s")
    status = Counter(c.status for c in citations)
    reasons = Counter(f"{c.status}: {c.reason}" for c in citations if c.reason and c.status not in (PINNED, "other_target"))
    print("outcome by code:", dict(status))
    if reasons:
        print("reasons:", dict(reasons))

    # ---- 1b. the agent step (live), only for what code could not prove ----
    from agents.memory.citation_pins.agent_step import build_tasks, render_request, run_agent_step

    tasks, over = build_tasks(scan, citations, content)
    print(f"\nfor the agent: {sum(len(t.seqs) for t in tasks)} citations in {len(tasks)} searches"
          + (f" ({over} more left to code, over the cap)" if over else ""))
    agent_request = ""
    if tasks:
        agent_request = render_request(tasks[:10], scan, doc_type)
    if args.agent and tasks:
        from agents.completion_search.lookups import SupabaseLibrary
        from agents.memory.attachment_segmenter.runner import _codec_for, _enc

        codec = _codec_for(sb, str(row.get("user_id") or ""))     # fakes stay in memory: a preview

        async def reg_status(ids):
            return store.reg_status(sb, ids)

        trace: list = []
        citations, stats = asyncio.run(run_agent_step(
            scan, citations, content, library=SupabaseLibrary(sb), reg_status=reg_status, doc_type=doc_type,
            encode=(lambda text: _enc(codec, text)) if codec is not None else None, trace=trace,
        ))
        tally = {**counts(scan, citations), **stats.row()}
        status = Counter(c.status for c in citations)
        print(f"agent step: {stats.seconds:.1f}s · sent {stats.sent} · pinned {stats.pinned} · "
              f"not in the library {stats.not_in_library} · failed {stats.failed}"
              + (" · masked" if codec is not None else ""))
        print("outcome after the agent:", dict(status))
        calls, answers = [], []
        for message in trace:
            for part in getattr(message, "parts", []) or []:
                if type(part).__name__ != "ToolCallPart":
                    continue
                call = part.args_as_dict() if hasattr(part, "args_as_dict") else {}
                if getattr(part, "tool_name", "") == "final_result":
                    answers = [f"{s.get('search_id')}: {s.get('status')} {s.get('selected') or ''} {s.get('note') or ''}"
                               for s in (call.get("searches") or [])]
                else:
                    calls.append(f"{part.tool_name}({json.dumps(call, ensure_ascii=False)})")
        if calls:
            print("the agent's tool calls:")
            for line in calls:
                print("   ", line[:200])
        if answers:
            print("the agent's answer:")
            for line in answers:
                print("   ", line[:200])

    # ---- 2. segments ----
    seg_rows = _segment_rows(sb, file_id)
    sections = sections_from_rows(seg_rows)
    print(f"\nsegments stored for this file: {len(sections)}"
          + ("" if sections else "  (none — the articles follow the document's text once)"))

    def segment_of(pos: int) -> str:
        for s in sections:
            if s.char_start <= pos < s.char_end:
                return f"{s.seq}/{s.value}"
        return "—"

    print("\n#  article  status          by     tied by        segment     law as written")
    for c in citations:
        print(f"{c.seq:<2} {c.article_number:<8} {c.status:<15} {c.decided_by:<6} {c.basis:<14} "
              f"{segment_of(c.char_start):<11} {c.law_as_written or '—'}" + (f"   [{c.reason}]" if c.reason else ""))

    # ---- 3. what would be stored → pins with the official text ----
    pinned_rows = [c.row(file_id) for c in citations if c.status == PINNED]
    pins = store.hydrate_pins(sb, {file_id: pinned_rows}).get(file_id, [])
    distinct = {p.article_id for p in pins}
    print(f"\npins: {len(pins)} citations → {len(distinct)} distinct official articles")
    for p in sorted({(pp.title, len(pp.text)) for pp in pins}):
        print(f"   {p[0]}  ({p[1]} chars)")

    # ---- 4. delivery ----
    with_pins = attach_pins(sections, pins)
    outputs: dict[str, str] = {}
    if sections:
        for name, keep in (("deep_search", DEEP_SEARCH_VALUES), ("writer", WRITER_VALUES)):
            plain, _ = render_sections(sections, keep)
            pinned_text, _ = render_sections(with_pins, keep)
            outputs[name] = pinned_text
            under = sum(len(s.pins) for s in with_pins if s.value in keep)
            print(f"delivery to {name}: {len(plain)} chars today → {len(pinned_text)} with pins "
                  f"(+{len(pinned_text) - len(plain)}), {under} article blocks; "
                  f"counted toward the caps: {kept_chars(with_pins, keep)} (unchanged: {kept_chars(sections, keep)})")
    else:
        text = with_loose_pins(content, with_pins)
        outputs["document"] = text
        print(f"delivery (no segments): {len(content)} chars today → {len(text)} with the articles after the text")

    # ---- 5. the card ----
    entries = attachment_entries(attachment_id=str(file_id), attachment_title=row.get("title") or "",
                                 content=content, pins=pins)
    card = render_card(entries) if entries else ""
    outputs["card"] = card
    print(f"card «النصوص المثبّتة»: {len(entries)} entries, {len(card)} chars"
          + (f"\n  summary other agents read: {card_summary(entries)}" if entries else ""))

    if args.show:
        for name, text in outputs.items():
            print(f"\n{'=' * 30} {name} {'=' * 30}\n{text}")
    if out_dir:
        out_dir.mkdir(parents=True, exist_ok=True)
        stem = f"citation_pins_{str(file_id)[:8]}"
        for name, text in outputs.items():
            (out_dir / f"{stem}_{name}.md").write_text(text, encoding="utf-8")
        if agent_request:
            (out_dir / f"{stem}_agent_request.md").write_text(agent_request, encoding="utf-8")
        (out_dir / f"{stem}_citations.json").write_text(json.dumps(
            {"file_id": file_id, "doc_type": doc_type, "counts": tally, "outcome": dict(status),
             "citations": [{**c.row(file_id), "segment": segment_of(c.char_start),
                            "sentence": content[c.sentence_start:c.sentence_end]} for c in citations]},
            ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\nsaved to {out_dir} ({stem}_*.md / .json) — these hold the document's text; keep them out of the repo")


if __name__ == "__main__":
    main()
