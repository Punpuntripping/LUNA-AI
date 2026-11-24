"""Phase 1 evaluation — the Completion Search helper against the REAL library.

Plan: ``.claude/plans/completion_search.md`` §7 Phase 1. Read-only on the database. It
calls the real model for the requests code cannot answer alone, with NO ledger scope:
nothing is charged to any user. Requests are public-law questions written for this
evaluation — no user text.

    python scripts/completion_search_eval.py                       # all requests, the slot's model
    python scripts/completion_search_eval.py --reasoning default   # the other flash setting
    python scripts/completion_search_eval.py --only 3,7 --show     # print what the caller would read
    python scripts/completion_search_eval.py --out DIR             # save JSON (DIR outside git)

For each request it prints: answered by code or by the model, seconds, and per search the
status, confidence, rounds and the titles found — and whether that matches the expectation.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# (request, [expected (status, confidence, title-must-contain)…] in order of the request)
CASES: list[tuple[str, list[tuple[str, str | None, str]]]] = [
    # -- plain: one law, article numbers --------------------------------------
    ("نظام المعاملات المدنية المادة 56 والمادة 234", [("found", "high", "المادة 56"), ("found", "high", "المادة 234")]),
    ("المادة السابعة والسبعون من نظام العمل", [("found", "high", "المادة 77")]),
    ("المادة ٨١ من نظام العمل", [("found", "high", "المادة 81")]),
    ("المادتين 76 و77 من نظام المرافعات الشرعية", [("found", "high", "المادة 76"), ("found", "high", "المادة 77")]),
    ("المادة 9999 من نظام العمل", [("not_found", None, "")]),
    ("نظام الإثبات المادة الثانية", [("found", "high", "المادة 2")]),
    ("المادة 3 من نظام الشركات", [("found", "high", "المادة 3")]),
    # -- two laws in one request ----------------------------------------------
    ("المادة 77 من نظام العمل والمادة 94 من نظام المعاملات المدنية",
     [("found", "high", "نظام العمل"), ("found", "high", "نظام المعاملات المدنية")]),
    # -- names that need another wording --------------------------------------
    # two laws open with this name: listing both and choosing neither is the right answer
    ("المادة 76 من نظام المرافعات", [("candidates|found", None, "")]),
    ("المادة 5 من نظام التأمينات", [("found", "medium", "التأمينات الاجتماعية")]),
    # the law is in the library, its article texts are not
    ("المادة 10 من نظام مكافحة المخدرات", [("not_found", None, "")]),
    ("المادة العاشرة من النظام الجزائي لجرائم التزوير", [("found", "high", "المادة 10")]),
    ("المادة 14 من اللائحة التنفيذية لنظام العمل", [("found|candidates|not_found|not_in_library", None, "")]),
    ("المادة 2 من نظام مكافحة غسيل الأموال", [("found", "medium", "غسل الأموال")]),
    ("المادة 5 من نظام العمل السعودي", [("found", "medium", "نظام العمل")]),
    ("المادة 3 من اللائحة التنفيذية لنظام المرافعات أمام ديوان المظالم", [("found", None, "ديوان المظالم")]),
    # -- not in the library ----------------------------------------------------
    ("المادة 13 من نظام ديوان المظالم", [("not_in_library|candidates", None, "")]),
    ("المادة 4 من نظام حماية الفضاء السيبراني الوطني", [("not_in_library", None, "")]),
    # -- phrases and definitions ----------------------------------------------
    ("تعريف البيع في نظام المعاملات المدنية", [("found", None, "نظام المعاملات المدنية")]),
    ("أين عرّف نظام العمل صاحب العمل", [("found", None, "نظام العمل")]),
    ("ما المادة التي ذكرت «الشرط الجزائي» في نظام المعاملات المدنية", [("found", None, "نظام المعاملات المدنية")]),
    ("أين ورد «فترة التجربة» في نظام العمل", [("found", None, "نظام العمل")]),
    ("تعريف البيانات الشخصية في نظام حماية البيانات الشخصية", [("found", None, "حماية البيانات الشخصية")]),
    # -- mixed ----------------------------------------------------------------
    ("نظام العمل المادة 77 وتعريف العامل فيه", [("found", "high", "المادة 77"), ("found", None, "نظام العمل")]),
    # -- services --------------------------------------------------------------
    ("خدمة قيد سجل تجاري لمؤسسة فردية من وزارة التجارة", [("found", None, "سجل تجاري")]),
    ("خدمة التحقق من وكالة من وزارة العدل", [("found", None, "وكالة")]),
    ("خدمة إصدار ترخيص مقيد من الهيئة العامة للنقل", [("found", None, "ترخيص مقيد")]),
    ("خدمة إصدار جواز السفر السعودي", [("found|candidates|not_found", None, "")]),
    # -- circulars -------------------------------------------------------------
    ("تعميم البنك المركزي «التحقق من هوية العملاء عبر مصدر موثوق»", [("found", None, "هوية العملاء")]),
    ("تعميم هيئة التأمين عن توطين وظائف مبيعات المنتجات التأمينية", [("found", None, "توطين")]),
    ("تعميم وزارة العدل عن التبليغ الإلكتروني للأحكام الغيابية", [("found|candidates|not_found", None, "")]),
    # -- rulings by case number (published rulings) ----------------------------
    ("الحكم في القضية رقم 439526685", [("found", "high", "439526685")]),
    ("الحكم في القضية رقم ٣٤٣٥٢٢٣٧", [("found", "high", "34352237")]),
    ("الحكم في القضية رقم 4029", [("candidates", "medium", "")]),
    ("الحكم في القضية رقم 4029 الصادر من المحكمة التجارية", [("found", None, "التجارية")]),
    ("حكم المحكمة التجارية في فسخ عقد توريد", [("not_found", None, "")]),
    # -- judicial principles ----------------------------------------------------
    ("المبدأ القضائي رقم 15", [("found", "high", "المبدأ رقم 15")]),
    ("المبدأ القضائي الذي يقرر أن الأصل البراءة من الجرم", [("found", "medium", "المبدأ رقم 15")]),
]


def judge(outcomes, expected) -> tuple[bool, str]:
    if len(outcomes) < len(expected):
        return False, f"searches {len(outcomes)} < expected {len(expected)}"
    notes = []
    ok = True
    for i, (status, confidence, contains) in enumerate(expected):
        o = outcomes[i]
        titles = " | ".join(p.title for p in o.pins)
        good = o.status in status.split("|")
        if good and confidence:
            good = o.confidence == confidence
        if good and contains and o.status == "found":
            good = contains in titles or contains in " ".join(p.source_title for p in o.pins)
        ok = ok and good
        notes.append(f"{'ok ' if good else 'BAD'} {o.status}/{o.confidence or '-'} r{o.rounds} [{titles[:90]}]")
    return ok, " ; ".join(notes)


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reasoning", choices=("slot", "default"), default="slot",
                    help="slot = the slot's policy (_FLASH_MEDIUM); default = plain _FLASH")
    ap.add_argument("--only", default="", help="comma-separated 1-based case numbers")
    ap.add_argument("--show", action="store_true", help="print what the caller would read")
    ap.add_argument("--trace", action="store_true", help="print the tool calls and what the tools returned")
    ap.add_argument("--no-code-first", action="store_true", help="force every request through the model")
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    from agents.completion_search import SupabaseLibrary, render_for_caller, run_completion_search
    from agents.completion_search.agent import create_completion_agent
    from agents.tool_repository.fetch_article import _cap_for_planner
    from agents.utils import agent_models
    from shared.db.client import get_supabase_client

    override = agent_models._FLASH if args.reasoning == "default" else None
    agent = create_completion_agent(override)
    library = SupabaseLibrary(get_supabase_client())
    await library.title_index()     # warm the title list so it is not counted in the first request

    picked = {int(x) for x in args.only.split(",") if x.strip()} if args.only else None
    rows = []
    for n, (request, expected) in enumerate(CASES, 1):
        if picked and n not in picked:
            continue
        t0 = time.perf_counter()
        trace: list = []
        try:
            result = await run_completion_search(
                request, library=library, agent=agent, caller="deep_planner",
                code_first=not args.no_code_first, trace=trace,
            )
            err = result.error
        except Exception as exc:  # noqa: BLE001
            result, err = None, f"{type(exc).__name__}: {exc}"
        secs = time.perf_counter() - t0
        if result is None:
            ok, note, by = False, err, "error"
        else:
            ok, note = judge(result.searches, expected)
            by = "model" if result.model_used else "code"
        rows.append({"n": n, "request": request, "ok": ok, "by": by, "seconds": round(secs, 1), "note": note})
        print(f"{n:>2} {'PASS' if ok else 'FAIL'} {by:<5} {secs:5.1f}s  {request}\n      {note}", flush=True)
        if args.trace:
            for message in trace:
                for part in getattr(message, "parts", []):
                    kind = type(part).__name__
                    if kind == "ToolCallPart":
                        print(f"      -> {part.tool_name}({part.args})", flush=True)
                    elif kind in ("ToolReturnPart", "RetryPromptPart"):
                        text = " / ".join(str(part.content).splitlines())
                        print(f"      <- {text[:420]}", flush=True)
        if args.show and result is not None:
            print(render_for_caller(result, cap_text=_cap_for_planner)[:1500], "\n", flush=True)

    passed = sum(r["ok"] for r in rows)
    by_model = [r["seconds"] for r in rows if r["by"] == "model"]
    by_code = [r["seconds"] for r in rows if r["by"] == "code"]
    summary = {
        "reasoning": args.reasoning, "cases": len(rows), "passed": passed,
        "by_code": len(by_code), "by_model": len(by_model),
        "code_seconds_avg": round(sum(by_code) / len(by_code), 2) if by_code else None,
        "model_seconds_avg": round(sum(by_model) / len(by_model), 1) if by_model else None,
        "model_seconds_max": max(by_model) if by_model else None,
    }
    print(json.dumps(summary, ensure_ascii=False))
    if args.out:
        out = Path(args.out)
        if any((q / ".git").exists() for q in [out.resolve(), *out.resolve().parents]):
            raise SystemExit("--out is inside a git repository")
        out.mkdir(parents=True, exist_ok=True)
        (out / f"eval_{args.reasoning}.json").write_text(
            json.dumps({"summary": summary, "rows": rows}, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    asyncio.run(main())
