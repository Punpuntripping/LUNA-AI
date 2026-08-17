"""Case-B eval — task item 3: THE REGRESSION THAT MATTERS.

An earlier cut synthesized any attached library page directly and skipped the
searcher. It was removed because **attaching a page is context, not a routing
decision** (plan §2.3, CORRECTED 2026-08-16). This proves it stays removed:

    carry نظام العمل  →  ask «اش هي المادة 5 من نظام التنفيذ؟»

    * the searcher MUST run;
    * ITS object must win — the answer is about نظام التنفيذ, not نظام العمل;
    * the carried page must still be offered as candidate handle C1 with a
      preview line (demoted to a hint, not dropped).

The searcher's ``SearcherDeps`` is captured by wrapping ``runner.run_tracked``
IN THIS SCRIPT — the production module is not touched.

Costs real tier_2 LLM calls (one searcher + one synthesizer, plus the control).

    python agents/simple_search/eval/case_b_regression.py
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
except Exception:  # noqa: BLE001
    pass

from dotenv import load_dotenv

load_dotenv()

from agents.simple_search import runner as RUNNER  # noqa: E402
from agents.simple_search.eval import case_b_fixtures as FX  # noqa: E402
from agents.simple_search.prompts import build_searcher_instructions  # noqa: E402
from shared.db.client import get_supabase_client  # noqa: E402

RESULTS: list[dict[str, Any]] = []
CAPTURED: dict[str, Any] = {"searcher_runs": 0, "deps": None, "decisions": []}


def check(name: str, ok: bool, detail: Any = "") -> bool:
    RESULTS.append({"check": name, "ok": bool(ok), "detail": detail})
    print(("  PASS  " if ok else "  FAIL  ") + name)
    if not ok:
        print(f"        → {detail}")
    return bool(ok)


def install_capture() -> None:
    """Wrap ``runner.run_tracked`` so the searcher's deps + decision are visible.

    Test-harness instrumentation only: it delegates to the real function and
    changes nothing about the run.
    """
    real = RUNNER.run_tracked

    async def wrapper(agent, prompt, **kwargs):  # noqa: ANN001, ANN003
        stage = kwargs.get("stage") or ""
        if stage == "simple_search.search":
            CAPTURED["searcher_runs"] += 1
            CAPTURED["deps"] = kwargs.get("deps")
            CAPTURED["searcher_prompt"] = prompt
        result = await real(agent, prompt, **kwargs)
        if stage == "simple_search.search":
            out = result.output
            CAPTURED["decisions"].append({
                "data_type": getattr(out, "data_type", None),
                "selected": list(getattr(out, "selected", []) or []),
                "aborted": getattr(out, "aborted", None),
                "rationale": getattr(out, "rationale", ""),
                "resolved": [
                    {"level": o.level, "ref_id": o.ref_id(),
                     "regulation_id": o.regulation_id, "article_id": o.article_id,
                     "article_number": o.article_number, "title": o.title,
                     "subtitle": o.subtitle}
                    for o in (getattr(out, "resolved", []) or [])
                ],
            })
        return result

    RUNNER.run_tracked = wrapper  # type: ignore[assignment]


async def main() -> int:
    sb = get_supabase_client()
    rows = (
        sb.table("conversations").select("conversation_id")
        .eq("user_id", FX.USER_ID).eq("title_ar", FX.SCRATCH_CONVO_TITLE)
        .is_("deleted_at", "null").limit(1).execute()
    ).data or []
    if not rows:
        print("no [EVAL-CASE-B] conversation — run case_b_carry.py first")
        return 1
    convo_id = str(rows[0]["conversation_id"])
    print(f"scratch conversation: {convo_id}")

    # The carried نظام العمل library item, exactly as the router would attach it.
    item = (
        sb.table("workspace_items").select("*")
        .eq("conversation_id", convo_id).eq("kind", "references")
        .eq("metadata->>source_page_type", "regulation")
        .eq("metadata->>source_page_id", FX.REG_LABOR_SLUG)
        .is_("deleted_at", "null").limit(1).execute()
    ).data
    if not item:
        print("the نظام العمل carry is missing — run case_b_carry.py first")
        return 1
    item = item[0]
    print(f"attached WI: {item['item_id']}  «{item['title']}»")

    install_capture()

    # Extra paraphrases of the hijack question + a positive control where the
    # carried page IS the target (the handle should then be SELECTED, which is
    # the round-trip saving §2.3 promises). Run after the main assertions.
    EXTRA = [
        ("hijack", "ابغى المادة 2 من نظام التنفيذ", FX.REG_ENFORCE_ID),
        ("hijack", "وش تقول المادة 7 من نظام التنفيذ؟", FX.REG_ENFORCE_ID),
        ("control", "اش يقول نظام العمل؟", FX.REG_LABOR_ID),
    ]

    question = "اش هي المادة 5 من نظام التنفيذ؟"
    print(f"\n── question: {question}")
    result = await RUNNER.run_simple_search(
        question, sb, FX.USER_ID, convo_id, None,
        attached_items=[item],
        recent_messages=[],
    )

    # 1 — the searcher ran at all.
    check("the searcher RAN (Case B does not skip it)",
          CAPTURED["searcher_runs"] >= 1, CAPTURED["searcher_runs"])

    deps = CAPTURED["deps"]
    lines = list(getattr(deps, "candidate_lines", []) or [])
    cands = dict(getattr(deps, "candidates", {}) or {})

    # 2 — the carried page is still a CANDIDATE HANDLE with a preview line.
    c1 = cands.get("C1")
    check("the carried page is registered as handle C1",
          c1 is not None and c1.regulation_id == FX.REG_LABOR_ID,
          {"handles": sorted(cands), "C1": (c1.model_dump() if c1 else None)})
    check("C1 carries a preview line",
          bool(lines) and lines[0].startswith("C1 — "), lines)
    instructions = build_searcher_instructions(deps)
    check("the preview line reaches the searcher's instructions",
          bool(lines) and lines[0].split(" — ", 1)[-1][:20] in instructions,
          {"lines": lines, "instructions_chars": len(instructions)})

    # 3 — the SEARCHER's object wins, and it is نظام التنفيذ.
    decisions = CAPTURED["decisions"]
    resolved = decisions[-1]["resolved"] if decisions else []
    reg_ids = {r["regulation_id"] for r in resolved}
    check("the searcher resolved at least one object", bool(resolved), decisions)
    check("the winning object belongs to نظام التنفيذ (NOT نظام العمل)",
          FX.REG_ENFORCE_ID in reg_ids and FX.REG_LABOR_ID not in reg_ids,
          {"resolved": resolved,
           "enforcement_id": FX.REG_ENFORCE_ID, "labor_id": FX.REG_LABOR_ID})
    check("the carried C1 handle was NOT selected",
          "C1" not in (decisions[-1]["selected"] if decisions else []),
          decisions[-1]["selected"] if decisions else None)

    # 4 — the ANSWER is about نظام التنفيذ.
    answer = "\n\n".join(result.chat_messages)
    print(f"\n── answer ({len(answer)} chars)\n{answer[:1200]}\n")
    check("the answer mentions نظام التنفيذ", "التنفيذ" in answer, answer[:300])
    check("the answer is NOT about نظام العمل",
          "نظام العمل" not in answer, answer[:400])
    check("the turn did not pause / abort",
          not result.paused and not result.aborted,
          {"paused": result.paused, "aborted": result.aborted})

    # ── extra paraphrases + the positive control ───────────────────────────
    extra_rows: list[dict[str, Any]] = []
    for kind, q, expect_reg in EXTRA:
        CAPTURED["decisions"] = []
        CAPTURED["searcher_runs"] = 0
        print(f"\n── {kind}: {q}")
        r = await RUNNER.run_simple_search(
            q, sb, FX.USER_ID, convo_id, None,
            attached_items=[item], recent_messages=[],
        )
        dec = CAPTURED["decisions"][-1] if CAPTURED["decisions"] else {}
        got = {o["regulation_id"] for o in dec.get("resolved", [])}
        ans = "\n\n".join(r.chat_messages)
        row = {"kind": kind, "question": q, "expected_regulation": expect_reg,
               "resolved": dec.get("resolved", []), "selected": dec.get("selected", []),
               "searcher_runs": CAPTURED["searcher_runs"],
               "answer_head": ans[:400], "created": r.created_item_ids}
        extra_rows.append(row)
        check(f"[{kind}] «{q}» → the right document",
              expect_reg in got, {"resolved": dec.get("resolved"), "expected": expect_reg})
        if kind == "control":
            check("[control] the carried handle C1 WAS selected (round-trip saved)",
                  "C1" in (dec.get("selected") or []), dec.get("selected"))

    out = {
        "conversation_id": convo_id,
        "attached_item_id": item["item_id"],
        "question": question,
        "searcher_runs": CAPTURED["searcher_runs"],
        "candidate_lines": lines,
        "decisions": decisions,
        "answer": answer,
        "extra": extra_rows,
        "created_item_ids": result.created_item_ids,
        "results": RESULTS,
        "passed": sum(1 for r in RESULTS if r["ok"]),
        "failed": sum(1 for r in RESULTS if not r["ok"]),
    }
    path = Path(__file__).with_name("case_b_regression_results.json")
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"{out['passed']} passed · {out['failed']} failed → {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
