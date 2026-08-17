"""Router A/B **re-run** — did the 2026-08-16 prompt fix (§13b-eval) land?

Re-fires the exact sentences the two prior evals measured, with the attachment
as the only variable, plus an over-correction control block the prior evals did
not run. Every assertion is the object ``run_router`` returned — Logfire is dark.

Cells
-----
Case B (the clean A/B — `simple_search_eval_case_b.md` §4)
    B-A   deictic «هذا النظام», page attached          → simple_search   (was 0/4)
    B-B   deictic + qualifier INSIDE, page attached    → deep_search     (was 5/6)
    B-C   deictic writing «بناءً عليه», page attached  → writing         (was 0/5)
    B-D   a DIFFERENT نظام, page attached              → simple_search   (was 3/3)
    B-E   نظام NAMED, page attached                    → simple_search   (was 0/3)
    B-E2  NAMED + qualifier, page attached             → deep_search     (was 2/2)
    B-E3  NAMED + writing, page attached               → writing         (was 2/2)
    B-F   نظام NAMED, NO attachment  (the baseline)    → simple_search   (was 3/3)
    B-F2  NAMED + qualifier, NO attachment             → deep_search     (was 2/2)
    B-F3  NAMED + writing, NO attachment               → writing         (was 1/2)

Case C (`simple_search_eval_case_c.md` §5) — real workspace context, read-only
    C-SS  «اش الحكم اللي في WI-N …؟ اعطيني تفاصيله»    → simple_search   (was 0/4)
    C-DS  «قارن الحكمين اللي في WI-N»                  → deep_search     (was 0/3)
    C-WR  writing-provenance «فصّل أكثر»               → writing         (was 2/3)

Controls — guard against OVER-correction (new this run)
    X-GREET-A/B  greetings, with and without the page  → chat_response
    X-REPORT     «ماذا استنتج التقرير السابق؟»          → chat_response  (the
                 legitimate answer-directly case the fix must NOT break)
    X-RAYHAN     «كم تكلفة الاشتراك؟»                   → chat_response
    X-PAIR-S/D   the §1.1 dangerous pair                → simple / deep
    X-CMP        «قارن نظام العمل بنظام العمل التطوعي»  → deep_search

    .venv/Scripts/python.exe agents/simple_search/eval/ab_rerun.py [--only PREFIX]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import time
from pathlib import Path
from typing import Any

from ab_common import (  # noqa: E402
    REG_LABOR_TITLE, S_CASES, S_HEIRS, SCRATCH_TITLE, USER_ID,
    ensure_scratch_conversation, hr, service_client, short,
)

from agents.models import DispatchAgent  # noqa: E402
from agents.router.context import load_router_context  # noqa: E402
from agents.router.router import run_router  # noqa: E402

CHAT = "chat_response"

# (cell, label, context_mode, expected, [questions])
#
# Context modes:
#   b_attached — scratch convo, summaries = ONLY the carried نظام العمل page
#   b_bare     — scratch convo, no summaries, no history  (the A/B's B leg)
#   c_cases    — real convo 2ff014cd: WI-2 = 18 rulings + 10 regs
#   c_heirs    — real convo e70cecfa: WI-3 = 8 rulings, WI-4 = agent_writing
CELLS: list[tuple[str, str, str, str, list[str]]] = [
    # ── Case B ────────────────────────────────────────────────────────────────
    ("B-A", "deictic «هذا النظام», page ATTACHED", "b_attached", "simple_search", [
        "اش يقول هذا النظام؟",
        "ودّي أعرف وش فيه هذا النظام اللي فتحته",
        "أعطني نظرة عامة على هذا النظام",
        "وش محتوى هذا النظام باختصار؟",
    ]),
    ("B-B", "deictic + qualifier INSIDE, page ATTACHED", "b_attached", "deep_search", [
        "اش يقول هذا النظام عن علاقة الإيجار؟",
        "وش الأحكام المتعلقة بعلاقة الإيجار في هذا النظام؟",
        "هذا النظام فيما يخص الإيجار وش ينص؟",
        "ابغى المواد اللي تبيّن علاقة الإيجار في هذا النظام",
        "اش يقول هذا النظام عن الإجازات السنوية؟",
        "وش الأحكام المتعلقة بساعات العمل في هذا النظام؟",
    ]),
    ("B-C", "deictic writing «بناءً عليه», page ATTACHED", "b_attached", "writing", [
        "اكتب لي عقد إيجار بناءً عليه",
        "صيغ لي عقد إيجار مستند على هذا النظام",
        "أبغاك تجهز مسودة عقد إيجار حسب هذا النظام",
        "اكتب لي عقد عمل بناءً عليه",
        "صيغ لي عقد عمل مستند على هذا النظام",
    ]),
    ("B-D", "a DIFFERENT نظام, page ATTACHED (no hijack)", "b_attached", "simple_search", [
        "اش هي المادة 5 من نظام التنفيذ؟",
        "ورّني نص المادة الخامسة من نظام التنفيذ",
        "أبغى المادة 5 من نظام التنفيذ نصها",
    ]),
    ("B-E", "نظام NAMED, page ATTACHED", "b_attached", "simple_search", [
        "اش يقول نظام العمل؟",
        "أعطني نظرة عامة على نظام العمل",
        "وش محتوى نظام العمل باختصار؟",
    ]),
    ("B-E2", "NAMED + qualifier, page ATTACHED", "b_attached", "deep_search", [
        "اش يقول نظام العمل عن الإجازات السنوية؟",
        "وش الأحكام المتعلقة بساعات العمل في نظام العمل؟",
        "اش يقول نظام العمل عن مكافأة نهاية الخدمة؟",
    ]),
    ("B-E3", "NAMED + writing, page ATTACHED", "b_attached", "writing", [
        "اكتب لي عقد عمل بناءً على نظام العمل",
        "صيغ لي عقد عمل وفق نظام العمل",
        "أبغاك تجهز مسودة عقد عمل حسب نظام العمل",
    ]),
    ("B-F", "نظام NAMED, NO attachment (baseline leg)", "b_bare", "simple_search", [
        "اش يقول نظام العمل؟",
        "أعطني نظرة عامة على نظام العمل",
        "وش محتوى نظام العمل باختصار؟",
    ]),
    ("B-F2", "NAMED + qualifier, NO attachment", "b_bare", "deep_search", [
        "اش يقول نظام العمل عن الإجازات السنوية؟",
        "وش الأحكام المتعلقة بساعات العمل في نظام العمل؟",
        "اش يقول نظام العمل عن مكافأة نهاية الخدمة؟",
    ]),
    ("B-F3", "NAMED + writing, NO attachment", "b_bare", "writing", [
        "اكتب لي عقد عمل بناءً على نظام العمل",
        "صيغ لي عقد عمل وفق نظام العمل",
        "أبغاك تجهز مسودة عقد عمل حسب نظام العمل",
    ]),
    # ── Case C ────────────────────────────────────────────────────────────────
    ("C-SS", "open a ruling cited in a prior WI", "c_cases", "simple_search", [
        "اش الحكم اللي في WI-2 وعن نزاع تاجرين؟ اعطيني تفاصيله",
        "افتح لي الحكم اللي في WI-2 عن شركة المحاصة، ابغى تفاصيله كاملة",
        "في WI-2 فيه حكم تجاري عن شريكين تنازعوا على رأس المال — ورّني نصه",
    ]),
    ("C-SS", "open a ruling cited in a prior WI (heirs)", "c_heirs", "simple_search", [
        "الحكم اللي في WI-3 عن إقرار الورثة، اعطيني تفاصيله",
    ]),
    ("C-DS", "comparison across WI rulings (D6)", "c_cases", "deep_search", [
        "قارن الحكمين اللي في WI-2",
        "قارن بين حكم الابتدائية وحكم الاستئناف اللي في WI-2 وش الفرق بينهم",
    ]),
    ("C-DS", "comparison across WI rulings (heirs)", "c_heirs", "deep_search", [
        "وازن بين الأحكام اللي في WI-3 وأيها أقوى سنداً",
    ]),
    ("C-WR", "writing provenance follow-up", "c_heirs", "writing", [
        "فصّل أكثر في المذكرة",
        "وسّع التحليل القانوني اللي كتبته وأضف قسم عن الدفوع",
        "حدّث المذكرة السابقة وزد فيها تفصيل عن أثر الإقرار",
    ]),
    # ── Controls — over-correction guards ─────────────────────────────────────
    ("X-GREET-A", "greeting WITH the page attached", "b_attached", CHAT, [
        "شكرًا",
        "مرحبا",
        "السلام عليكم",
    ]),
    ("X-GREET-B", "greeting, no attachment", "b_bare", CHAT, [
        "شكرًا",
    ]),
    ("X-REPORT", "what did OUR prior report conclude", "c_cases", CHAT, [
        "ماذا استنتج التقرير السابق؟",
        "وش كانت خلاصة البحث؟",
        "وش أهم النتائج اللي طلعت من البحث السابق؟",
    ]),
    ("X-RAYHAN", "questions about ريحان itself", "b_bare", CHAT, [
        "كم تكلفة الاشتراك؟",
        "ما هو ريحان وكيف أستخدمه؟",
    ]),
    ("X-PAIR-S", "§1.1 dangerous pair — whole object", "b_bare", "simple_search", [
        "اش يقول نظام المعاملات المدنية",
        "اش يقول نظام المعاملات المدنية، اهم احكامه",
    ]),
    ("X-PAIR-D", "§1.1 dangerous pair — qualifier INSIDE", "b_bare", "deep_search", [
        "اش يقول نظام المعاملات المدنية عن علاقة الإيجار",
        "اش يقول نظام المعاملات المدنية عن الشرط التعسفي",
    ]),
    ("X-CMP", "comparison is never simple_search", "b_bare", "deep_search", [
        "قارن نظام العمل بنظام العمل التطوعي",
        "قارن بين نظام العمل ونظام العمل التطوعي وش الفرق",
    ]),
    # ── Probes — isolate the MECHANISM behind the cells that still fail ───────
    # P-W: is the writing-leg failure the «search then write» gate rather than
    #      the attachment or the demonstrative? P-W1 is a simple letter (the
    #      gate exempts it); P-W2 has a RELEVANT prior agent_search item (the
    #      gate's own "then route writing directly" branch).
    ("P-W1", "simple letter, page attached (gate exempt)", "b_attached", "writing", [
        "اكتب لي خطاب إنذار لعامل متغيب عن العمل",
    ]),
    ("P-W2", "drafting WITH a relevant prior search item", "c_cases", "writing", [
        "اكتب لي مذكرة قانونية بناءً على البحث اللي في WI-2",
    ]),
    # P-C: is the Case-C failure the answer-directly rule, or the ambiguity
    #      check? P-C1/P-C2 remove all ambiguity; P-C3/P-C4 re-fire the exact
    #      failing sentences to separate determinism from phrasing.
    ("P-C1", "Case C, ruling named by NUMBER (unambiguous)", "c_cases", "simple_search", [
        "اعطيني تفاصيل الحكم رقم 439185965 اللي في WI-2",
    ]),
    ("P-C2", "Case C, «نص الحكم كامل مو الملخص»", "c_heirs", "simple_search", [
        "الحكم اللي في WI-3 عن إقرار الورثة، ابغى نص الحكم كامل مو الملخص",
    ]),
    ("P-C3", "Case C, the failing sentence re-fired", "c_heirs", "simple_search", [
        "الحكم اللي في WI-3 عن إقرار الورثة، اعطيني تفاصيله",
    ]),
    ("P-C4", "Case C comparison re-fired", "c_cases", "deep_search", [
        "قارن الحكمين اللي في WI-2",
    ]),
    # P-D: determinism on the two sentences that still miss. Both are re-fires
    # of an exact sentence already run above, so cell totals are n≥3.
    ("P-D1", "the plan's canonical Case-C sentence, re-fired", "c_cases", "simple_search", [
        "اش الحكم اللي في WI-2 وعن نزاع تاجرين؟ اعطيني تفاصيله",
        "اش الحكم اللي في WI-2 وعن نزاع تاجرين؟ اعطيني تفاصيله",
    ]),
    ("P-D2", "the one B-A miss, re-fired", "b_attached", "simple_search", [
        "وش محتوى هذا النظام باختصار؟",
        "وش محتوى هذا النظام باختصار؟",
    ]),
]


# --------------------------------------------------------------------------- #
# Contexts — loaded ONCE so a mid-run save_memo cannot change a later cell's
# input. The `conversation_id` handed to run_router is ALWAYS the scratch one.
# --------------------------------------------------------------------------- #


def build_contexts(sb, scratch_id: str) -> dict[str, dict[str, Any]]:
    scratch_ctx = load_router_context(sb, USER_ID, scratch_id, None)
    carried = [
        s for s in scratch_ctx.workspace_item_summaries
        if (s.get("title") or "").strip() == REG_LABOR_TITLE
    ]
    if len(carried) != 1:
        raise SystemExit(
            f"expected exactly 1 carried «{REG_LABOR_TITLE}» summary, got {len(carried)} "
            "— run ab_setup.py first"
        )

    ctxs: dict[str, dict[str, Any]] = {
        "b_attached": {"ctx": scratch_ctx, "summaries": carried, "history": []},
        "b_bare": {"ctx": scratch_ctx, "summaries": [], "history": []},
    }
    for name, src in (("c_cases", S_CASES), ("c_heirs", S_HEIRS)):
        c = load_router_context(sb, USER_ID, src, None)
        ctxs[name] = {
            "ctx": c,
            "summaries": c.workspace_item_summaries,
            "history": c.message_history,
            "aliases": {
                str(s.get("wi_seq")): f"{s.get('kind')} · {short(s.get('title') or '', 50)}"
                for s in c.workspace_item_summaries
            },
        }
    return ctxs


async def route_one(sb, scratch_id: str, bundle: dict, question: str) -> dict[str, Any]:
    ctx = bundle["ctx"]
    t0 = time.monotonic()
    rr = await run_router(
        question,
        sb,
        USER_ID,
        scratch_id,                      # every side effect lands in the scratch convo
        None,
        ctx.case_memory_md,
        ctx.case_metadata,
        ctx.user_preferences,
        bundle["history"],
        workspace_item_summaries=bundle["summaries"],
        compaction_summary_md=ctx.compaction_summary_md,
        user_call_name=ctx.user_call_name,
        welcome=None,
    )
    out = rr.output
    if isinstance(out, DispatchAgent):
        return {
            "got": out.agent_family,
            "task_label": out.task_label,
            "target_wi": out.target_wi,
            "attached_wis": list(out.attached_wis or []),
            "subtype": getattr(out, "subtype", None),
            "message": "",
            "message_chars": 0,
            "secs": round(time.monotonic() - t0, 1),
        }
    msg = getattr(out, "message", "") or ""
    return {
        "got": CHAT,
        "task_label": None,
        "target_wi": None,
        "attached_wis": [],
        "subtype": None,
        "message": msg[:900],
        "message_chars": len(msg),
        "secs": round(time.monotonic() - t0, 1),
    }


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="", help="run only cells whose id starts with this")
    ap.add_argument("--skip", default="", help="comma-separated EXACT cell ids to skip")
    ap.add_argument("--out", default="ab_rerun_results.json")
    ap.add_argument("--concurrency", type=int, default=3)
    args = ap.parse_args()
    skip = {s.strip() for s in args.skip.split(",") if s.strip()}

    sb = service_client()
    scratch_id = ensure_scratch_conversation(sb)
    hr(f"ROUTER A/B RE-RUN — scratch {scratch_id} «{SCRATCH_TITLE}»")

    ctxs = build_contexts(sb, scratch_id)
    print(f"b_attached summaries: {len(ctxs['b_attached']['summaries'])} "
          f"(«{ctxs['b_attached']['summaries'][0].get('title')}», "
          f"kind={ctxs['b_attached']['summaries'][0].get('kind')})")
    for n in ("c_cases", "c_heirs"):
        print(f"{n}: {len(ctxs[n]['summaries'])} summaries, "
              f"{len(ctxs[n]['history'])} history messages · {ctxs[n]['aliases']}")

    cells = [
        c for c in CELLS
        if (not args.only or c[0].startswith(args.only)) and c[0] not in skip
    ]
    jobs: list[tuple[str, str, str, str, str]] = [
        (cell, label, mode, expected, q)
        for cell, label, mode, expected, qs in cells
        for q in qs
    ]
    print(f"\n{len(jobs)} router runs queued (concurrency {args.concurrency})\n")

    # One client per concurrency slot — never share an httpx.Client across
    # overlapping runs.
    clients = [service_client() for _ in range(args.concurrency)]
    sem = asyncio.Semaphore(args.concurrency)
    slots: asyncio.Queue[int] = asyncio.Queue()
    for i in range(args.concurrency):
        slots.put_nowait(i)

    async def worker(idx: int, job) -> dict[str, Any]:
        cell, label, mode, expected, q = job
        async with sem:
            slot = await slots.get()
            try:
                r = await route_one(clients[slot], scratch_id, ctxs[mode], q)
            except Exception as exc:  # noqa: BLE001
                r = {"got": "ERROR", "error": repr(exc)[:400], "message": "",
                     "message_chars": 0, "attached_wis": [], "task_label": None,
                     "target_wi": None, "subtype": None, "secs": None}
            finally:
                slots.put_nowait(slot)
        r.update({"n": idx, "cell": cell, "label": label, "mode": mode,
                  "expected": expected, "question": q})
        r["ok"] = r["got"] == expected
        print(f"[{idx:>2}] {'PASS' if r['ok'] else 'FAIL'}  {cell:<10} "
              f"exp={expected:<14} got={r['got']:<14} {short(q, 60)}")
        if r["got"] == CHAT and r.get("message"):
            print(f"        chat[{r['message_chars']}]: {short(r['message'], 170)}")
        elif r["got"] == "ERROR":
            print(f"        {r.get('error')}")
        elif r.get("task_label"):
            print(f"        label={r['task_label']!r} attached={r['attached_wis']} "
                  f"target={r['target_wi']}")
        return r

    results = await asyncio.gather(*(worker(i, j) for i, j in enumerate(jobs)))

    # ── per-cell tally ────────────────────────────────────────────────────────
    hr("PER-CELL")
    tally: dict[tuple[str, str, str], dict[str, Any]] = {}
    for r in results:
        key = (r["cell"], r["mode"], r["expected"])
        t = tally.setdefault(key, {"n": 0, "ok": 0, "got": {}, "label": r["label"]})
        t["n"] += 1
        t["ok"] += int(r["ok"])
        t["got"][r["got"]] = t["got"].get(r["got"], 0) + 1
    for (cell, mode, expected), t in tally.items():
        print(f"{cell:<10} {mode:<12} exp={expected:<14} "
              f"{t['ok']}/{t['n']}   {t['got']}")

    passed = sum(1 for r in results if r["ok"])
    print(f"\nTOTAL {passed}/{len(results)}")

    path = Path(__file__).with_name(args.out)
    path.write_text(json.dumps(
        {"conversation_id": scratch_id,
         "cells": [{"cell": c, "mode": m, "expected": e, "label": t["label"],
                    "ok": t["ok"], "n": t["n"], "got": t["got"]}
                   for (c, m, e), t in tally.items()],
         "results": sorted(results, key=lambda r: r["n"]),
         "passed": passed, "total": len(results)},
        ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"dump → {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
