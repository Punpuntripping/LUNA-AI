"""Case-B RE-RUN — routing, with the FAMILY SPLIT scored explicitly.

Reuses `ab_rerun`'s cell definitions and its `route_one` verbatim (same
sentences, same context shapes, attachment the only variable) so this is a true
before/after against BOTH prior reports. What this driver adds is the brief's
watch item:

    The router prompt gained a tie-break — "when in doubt between the two,
    choose deep_search". If it OVER-FIRES, an attached whole-object question
    stops reaching `simple_search` and lands on `deep_search` instead. That
    still "answers" and reads as a dispatch, so a pass/fail on "did it
    dispatch" would hide the regression.

So every cell is tallied three ways: `simple_search` / `deep_search` /
`writing` / `chat_response`, and the whole-object legs are scored on reaching
**simple_search specifically**.

Baselines carried in from the two reports:
    attached whole-object → the family : 0/7  → 11/12
    «اش يقول نظام العمل؟» attached/bare : 0/3 vs 3/3 → 6/6 vs 3/3
    deictic «اش يقول هذا النظام؟»       : 0/4  → 5/6
    writing on the attached page        : 0/5  → 0/5 (gate, not the attachment)

    .venv/Scripts/python.exe agents/simple_search/eval/rerun_b_routing.py
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from rerun_b_common import (  # noqa: E402
    SCRATCH_TITLE, ensure_scratch_conversation, hr, service_client, short,
    use_rerun_scratch,
)

use_rerun_scratch()

import ab_rerun as AB  # noqa: E402

CHAT = AB.CHAT
FAMILIES = ("simple_search", "deep_search", "writing", CHAT, "ERROR")

#: Cells to re-fire, and how many times each sentence runs. The counts
#: reproduce the prior run's `n` per cell so the fractions are comparable
#: (B-E ran 3 sentences x2 = 6; B-A ran 4 sentences + 2 re-fires of the miss).
PLAN: list[tuple[str, int]] = [
    ("B-E", 2),      # NAMED whole object, page ATTACHED     → simple_search  (6/6)
    ("B-F", 1),      # the same sentences, BARE              → simple_search  (3/3)
    ("B-A", 1),      # deictic «هذا النظام», ATTACHED        → simple_search  (5/6)
    ("P-D2", 1),     # the one B-A miss, re-fired x2         → simple_search
    ("B-D", 1),      # a DIFFERENT نظام, ATTACHED (no hijack)→ simple_search  (3/3)
    ("B-B", 1),      # deictic + qualifier INSIDE            → deep_search    (5/6)
    ("B-E2", 1),     # NAMED + qualifier, ATTACHED           → deep_search    (3/3)
    ("B-F2", 1),     # NAMED + qualifier, BARE               → deep_search    (3/3)
    ("B-C", 1),      # deictic writing, ATTACHED             → writing        (0/5)
    ("B-E3", 1),     # NAMED + writing, ATTACHED             → writing        (0/3)
    ("B-F3", 1),     # NAMED + writing, BARE                 → writing        (0/3)
    # ── tie-break canaries: if "when in doubt → deep_search" over-fires, these
    #    bare whole-object lookups desert simple_search too.
    ("X-PAIR-S", 1),  # §1.1 pair, whole object              → simple_search  (2/2)
    ("X-PAIR-D", 1),  # §1.1 pair, qualifier                 → deep_search    (2/2)
    ("X-CMP", 1),     # comparison is never simple_search    → deep_search    (2/2)
    ("X-GREET-A", 1),  # greeting WITH the page attached     → chat           (3/3)
    ("X-GREET-B", 1),  # greeting, bare                      → chat           (1/1)
    ("X-RAYHAN", 1),   # product questions                   → chat           (2/2)
]

#: Cells whose PASS means "reached simple_search" — the family-split score.
WHOLE_OBJECT_ATTACHED = {"B-E", "B-A", "P-D2"}


async def main() -> int:
    sb = service_client()
    scratch_id = ensure_scratch_conversation(sb)
    hr(f"CASE-B RE-RUN · routing — scratch {scratch_id} «{SCRATCH_TITLE}»")

    ctxs = AB.build_contexts(sb, scratch_id)
    att = ctxs["b_attached"]["summaries"][0]
    print(f"b_attached: 1 summary «{att.get('title')}» kind={att.get('kind')}")

    jobs: list[tuple[str, str, str, str, str]] = []
    for cell_id, repeat in PLAN:
        matches = [c for c in AB.CELLS if c[0] == cell_id]
        if not matches:
            raise SystemExit(f"unknown cell {cell_id}")
        for cell, label, mode, expected, qs in matches:
            for q in qs:
                for _ in range(repeat):
                    jobs.append((cell, label, mode, expected, q))
    print(f"\n{len(jobs)} router runs queued\n")

    concurrency = 3
    clients = [service_client() for _ in range(concurrency)]
    sem = asyncio.Semaphore(concurrency)
    slots: asyncio.Queue[int] = asyncio.Queue()
    for i in range(concurrency):
        slots.put_nowait(i)

    async def worker(idx: int, job) -> dict[str, Any]:
        cell, label, mode, expected, q = job
        async with sem:
            slot = await slots.get()
            try:
                r = await AB.route_one(clients[slot], scratch_id, ctxs[mode], q)
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
              f"exp={expected:<14} got={r['got']:<14} {short(q, 58)}")
        if r["got"] == CHAT and r.get("message"):
            print(f"        chat[{r['message_chars']}]: {short(r['message'], 160)}")
        elif r["got"] == "ERROR":
            print(f"        {r.get('error')}")
        elif r.get("task_label"):
            print(f"        label={r['task_label']!r} attached={r['attached_wis']}")
        return r

    results = await asyncio.gather(*(worker(i, j) for i, j in enumerate(jobs)))

    # ── per-cell, with the full family split ─────────────────────────────────
    hr("PER-CELL — expected vs the FAMILY SPLIT")
    tally: dict[str, dict[str, Any]] = {}
    for r in results:
        t = tally.setdefault(r["cell"], {"n": 0, "ok": 0, "expected": r["expected"],
                                         "mode": r["mode"], "label": r["label"],
                                         "split": {}})
        t["n"] += 1
        t["ok"] += int(r["ok"])
        t["split"][r["got"]] = t["split"].get(r["got"], 0) + 1
    print(f"{'cell':<10} {'mode':<12} {'expected':<14} {'score':<8} split")
    for cell, t in tally.items():
        split = "  ".join(f"{k}={v}" for k, v in sorted(t["split"].items()))
        print(f"{cell:<10} {t['mode']:<12} {t['expected']:<14} "
              f"{t['ok']}/{t['n']:<6} {split}")

    # ── THE watch item ───────────────────────────────────────────────────────
    hr("TIE-BREAK WATCH — attached whole-object questions")
    wo = [r for r in results if r["cell"] in WHOLE_OBJECT_ATTACHED]
    wo_split: dict[str, int] = {}
    for r in wo:
        wo_split[r["got"]] = wo_split.get(r["got"], 0) + 1
    reached = wo_split.get("simple_search", 0)
    print(f"  attached whole-object runs      : {len(wo)}")
    print(f"  reached simple_search           : {reached}/{len(wo)}   "
          f"(baseline after 1st patch: 11/12)")
    print(f"  went to deep_search (OVER-FIRE) : {wo_split.get('deep_search', 0)}")
    print(f"  answered directly (chat)        : {wo_split.get(CHAT, 0)}")
    print(f"  full split                      : {wo_split}")
    for r in wo:
        if r["got"] != "simple_search":
            print(f"    MISS  «{r['question']}» → {r['got']}"
                  f"{' · ' + short(r.get('message', ''), 150) if r.get('message') else ''}")

    bare_lookup = [r for r in results if r["cell"] in {"B-F", "X-PAIR-S"}]
    bare_ss = sum(1 for r in bare_lookup if r["got"] == "simple_search")
    print(f"\n  BARE lookups reaching simple_search: {bare_ss}/{len(bare_lookup)} "
          f"(the tie-break's other blast radius — baseline 5/5)")
    for r in bare_lookup:
        if r["got"] != "simple_search":
            print(f"    MISS  «{r['question']}» → {r['got']}")

    passed = sum(1 for r in results if r["ok"])
    print(f"\nTOTAL {passed}/{len(results)}")

    path = Path(__file__).with_name("rerun_b_routing_results.json")
    path.write_text(json.dumps(
        {"conversation_id": scratch_id,
         "cells": {c: t for c, t in tally.items()},
         "whole_object_attached": {"n": len(wo), "simple_search": reached,
                                   "split": wo_split},
         "bare_lookup": {"n": len(bare_lookup), "simple_search": bare_ss},
         "results": sorted(results, key=lambda r: r["n"]),
         "passed": passed, "total": len(results)},
        ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"dump → {path.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
