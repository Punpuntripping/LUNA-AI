"""Validate the sectioned expander (prompt_3) against real queries.

Runs ONLY the expander — no DB search, no reranker, no aggregator. Picks a
handful of commercial/contractual queries from agents/test_queries.json and
prints the structured ExpanderOutputV2 for inspection, plus a battery of
rule checks (≥2 channels covered, sectors are canonicalizable, channel style
cues present, no rare details leaked, etc.).

Usage:
    python -m agents.deep_search_v3.case_search.planning.validate_prompt3
    python -m agents.deep_search_v3.case_search.planning.validate_prompt3 --ids 13,18,21,23
    python -m agents.deep_search_v3.case_search.planning.validate_prompt3 --model or-gemma-4-31b
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

# Force UTF-8 console on Windows
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

from agents.deep_search_v3.case_search.expander import (
    EXPANDER_LIMITS,
    create_expander_agent,
)
from agents.deep_search_v3.case_search.models import ExpanderOutputV2, TypedQuery
from agents.deep_search_v3.case_search.prompts import build_expander_user_message
from agents.deep_search_v3.case_search.sector_vocab import (
    VALID_SECTORS,
    canonicalize_sectors,
)


TEST_QUERIES_PATH = Path(__file__).resolve().parents[3] / "test_queries.json"

# Hand-picked defaults: commercial / labor / contracts — the live DB scope.
DEFAULT_IDS = [13, 16, 18, 21, 22, 23]


# ── Rule checks ───────────────────────────────────────────────────────────────


def _check_channels_covered(output: ExpanderOutputV2) -> tuple[bool, str]:
    channels = {q.channel for q in output.queries}
    ok = len(channels) >= 2
    return ok, f"channels covered: {sorted(channels)} ({len(channels)}/3)"


def _check_sectors_valid(output: ExpanderOutputV2) -> tuple[bool, str]:
    if not output.legal_sectors:
        return True, "legal_sectors = null (no filter — acceptable)"
    canon = canonicalize_sectors(output.legal_sectors)
    invalid = [s for s in output.legal_sectors if s not in VALID_SECTORS and s not in canon]
    ok = not invalid and len(canon) > 0
    note = f"raw={output.legal_sectors} canon={canon}"
    if invalid:
        note += f" INVALID={invalid}"
    return ok, note


def _check_channel_style(output: ExpanderOutputV2) -> list[tuple[bool, str]]:
    """Loose per-channel style heuristics.

    - principle: short (≤14 words), contains doctrinal cue words
    - facts: present-tense narrative, typically longer, contains actor words
    - basis: references regulation / article / نظام
    """
    out: list[tuple[bool, str]] = []
    principle_cues = ("مبدأ", "الأصل", "قاعدة", "حدود", "شروط", "أثر", "بطلان",
                      "سقوط", "نطاق", "حجية", "عبء الإثبات", "المُقرّر", "المقرر",
                      "اشتراط", "سلطة", "تقدير", "انقلاب", "جواز")
    basis_cues = ("نظام", "المادة", "مادة", "لائحة", "قانون")
    fact_actor_cues = ("المدعي", "المدعى", "الطرف", "دائن", "مدين", "مقاول", "موظف",
                       "بائع", "مشتري", "مستأجر", "مؤجر", "صاحب", "العامل", "المستأنف",
                       "طلب", "طالب", "تعاقد", "رفع", "أوقف", "تصرف", "أنهى", "دفع",
                       "اشترى", "أجّر", "باع")

    for q in output.queries:
        words = q.text.split()
        nw = len(words)
        note = f"[{q.channel}] ({nw}w) {q.text[:100]}"
        ok = True
        reasons: list[str] = []

        if q.channel == "principle":
            if nw > 14:
                ok = False
                reasons.append("too long (>14w)")
            if not any(cue in q.text for cue in principle_cues):
                ok = False
                reasons.append("no doctrinal cue words")
        elif q.channel == "basis":
            if not any(cue in q.text for cue in basis_cues):
                # basis may also be procedural — relaxed: flag as soft-warn
                reasons.append("no نظام/مادة/لائحة reference (soft)")
        elif q.channel == "facts":
            if nw < 6:
                ok = False
                reasons.append("too short (<6w) for facts narrative")
            if not any(cue in q.text for cue in fact_actor_cues):
                ok = False
                reasons.append("no actor/verb cues")

        # Pruning: numeric leaks — only arabic/western digits
        if any(ch.isdigit() for ch in q.text) and q.channel != "basis":
            reasons.append("contains digits (possible rare-detail leak)")

        if reasons:
            note += "  ⚠ " + "; ".join(reasons)
        out.append((ok, note))
    return out


def _check_no_duplicate_queries(output: ExpanderOutputV2) -> tuple[bool, str]:
    seen = [q.text.strip() for q in output.queries]
    dup = len(seen) - len(set(seen))
    ok = dup == 0
    return ok, f"{dup} duplicates" if dup else "no duplicates"


# ── Main ──────────────────────────────────────────────────────────────────────


async def run_one(
    query_id: int,
    query: dict,
    model_override: str | None,
    thinking_effort: str | None,
) -> dict:
    """Run the prompt_3 expander on one query and return a validation report."""
    expander = create_expander_agent(
        prompt_key="prompt_3",
        thinking_effort=thinking_effort,
        model_override=model_override,
    )
    user_message = build_expander_user_message(
        focus_instruction=query["text"],
        user_context=query.get("category", ""),
    )

    import time
    t0 = time.perf_counter()
    try:
        result = await expander.run(user_message, usage_limits=EXPANDER_LIMITS)
    except Exception as e:
        return {
            "id": query_id,
            "category": query.get("category", ""),
            "error": f"{type(e).__name__}: {e}",
            "elapsed_s": round(time.perf_counter() - t0, 2),
        }

    output: ExpanderOutputV2 = result.output
    elapsed = round(time.perf_counter() - t0, 2)

    channels_ok, channels_note = _check_channels_covered(output)
    sectors_ok, sectors_note = _check_sectors_valid(output)
    style_checks = _check_channel_style(output)
    dup_ok, dup_note = _check_no_duplicate_queries(output)

    return {
        "id": query_id,
        "category": query.get("category", ""),
        "elapsed_s": elapsed,
        "output": {
            "legal_sectors": output.legal_sectors,
            "queries": [
                {"channel": q.channel, "text": q.text, "rationale": q.rationale}
                for q in output.queries
            ],
        },
        "usage": {
            "input_tokens": result.usage().input_tokens,
            "output_tokens": result.usage().output_tokens,
            "total_tokens": result.usage().total_tokens,
            "requests": result.usage().requests,
        },
        "checks": {
            "channels_covered": {"ok": channels_ok, "note": channels_note},
            "sectors_valid": {"ok": sectors_ok, "note": sectors_note},
            "duplicates": {"ok": dup_ok, "note": dup_note},
            "per_query_style": [
                {"ok": ok, "note": note} for ok, note in style_checks
            ],
        },
    }


def _print_report(r: dict) -> None:
    bar = "─" * 70
    print(f"\n{bar}")
    print(f"ID {r['id']} [{r['category']}]  ({r['elapsed_s']}s)")
    print(bar)

    if "error" in r:
        print(f"  ❌ ERROR: {r['error']}")
        return

    out = r["output"]
    print(f"  legal_sectors: {out['legal_sectors']}")
    print(f"  queries ({len(out['queries'])}):")
    for i, q in enumerate(out["queries"], 1):
        print(f"    {i}. [{q['channel']:<9}] {q['text']}")
        if q["rationale"]:
            print(f"       ↳ {q['rationale']}")

    u = r["usage"]
    print(f"  usage: in={u['input_tokens']} out={u['output_tokens']} req={u['requests']}")

    ch = r["checks"]
    print(f"  checks:")
    for k in ("channels_covered", "sectors_valid", "duplicates"):
        c = ch[k]
        mark = "✓" if c["ok"] else "✗"
        print(f"    {mark} {k}: {c['note']}")
    print(f"    per-query style:")
    for item in ch["per_query_style"]:
        mark = "✓" if item["ok"] else "✗"
        print(f"      {mark} {item['note']}")


def _summarize(reports: list[dict]) -> None:
    bar = "═" * 70
    print(f"\n{bar}")
    print("SUMMARY")
    print(bar)

    errors = [r for r in reports if "error" in r]
    ok = [r for r in reports if "error" not in r]
    print(f"  completed: {len(ok)}/{len(reports)}   errors: {len(errors)}")

    if errors:
        print("  errors:")
        for r in errors:
            print(f"    {r['id']}: {r['error']}")

    if ok:
        total_tok = sum(r["usage"]["total_tokens"] for r in ok)
        total_sec = sum(r["elapsed_s"] for r in ok)
        print(f"  total tokens: {total_tok}   total time: {total_sec:.1f}s")

        channels_ok = sum(1 for r in ok if r["checks"]["channels_covered"]["ok"])
        sectors_ok = sum(1 for r in ok if r["checks"]["sectors_valid"]["ok"])
        dup_ok = sum(1 for r in ok if r["checks"]["duplicates"]["ok"])
        style_fail = sum(
            1 for r in ok
            for item in r["checks"]["per_query_style"] if not item["ok"]
        )
        style_total = sum(len(r["checks"]["per_query_style"]) for r in ok)

        print(f"  ≥2 channels covered:  {channels_ok}/{len(ok)}")
        print(f"  sectors valid:        {sectors_ok}/{len(ok)}")
        print(f"  no duplicate queries: {dup_ok}/{len(ok)}")
        print(f"  per-query style ok:   {style_total - style_fail}/{style_total}")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--ids", default=None, help="Comma-separated query IDs (default: 13,16,18,21,22,23)")
    p.add_argument("--model", default=None)
    p.add_argument("--thinking", default=None, choices=["low", "medium", "high", "none"])
    p.add_argument("--save-json", default=None, help="Write full reports JSON to this path")
    args = p.parse_args()

    with open(TEST_QUERIES_PATH, encoding="utf-8") as f:
        data = json.load(f)
    all_queries = {q["id"]: q for q in data["queries"]}

    if args.ids:
        ids = [int(x.strip()) for x in args.ids.split(",")]
    else:
        ids = list(DEFAULT_IDS)

    missing = [i for i in ids if i not in all_queries]
    if missing:
        print(f"[WARN] unknown query ids: {missing}")
        ids = [i for i in ids if i in all_queries]

    print(f"Running prompt_3 expander on {len(ids)} queries: {ids}")
    print(f"Model: {args.model or 'default'}   Thinking: {args.thinking or 'prompt default'}")

    async def _go():
        reports = []
        for qid in ids:
            r = await run_one(qid, all_queries[qid], args.model, args.thinking)
            reports.append(r)
            _print_report(r)
        _summarize(reports)
        if args.save_json:
            Path(args.save_json).write_text(
                json.dumps(reports, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            print(f"\nReport saved → {args.save_json}")

    asyncio.run(_go())


if __name__ == "__main__":
    main()
