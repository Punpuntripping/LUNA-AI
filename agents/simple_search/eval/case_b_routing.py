"""Case-B eval — task item 4: does an ATTACHED library page distort the route?

Runs the REAL router (``agents.router.router.run_router``) with the carried
نظام العمل item present in the workspace summaries, over four question shapes
× ≥3 paraphrases each (§1.1):

    A  «اش يقول هذا النظام؟»                     → simple_search
    B  «اش يقول هذا النظام عن علاقة الإيجار؟»    → deep_search  (qualifier INSIDE)
    C  «اكتب لي عقد إيجار بناءً عليه»            → writing
    D  a question about a DIFFERENT نظام          → must not be hijacked

Costs real tier_2 LLM calls — 13 router runs. Writes nothing to the DB except
what the router itself may do (it does not, for these shapes).

    python agents/simple_search/eval/case_b_routing.py
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

from agents.models import DispatchAgent  # noqa: E402
from agents.router.context import load_router_context  # noqa: E402
from agents.router.router import run_router  # noqa: E402
from agents.simple_search.eval import case_b_fixtures as FX  # noqa: E402
from shared.db.client import get_supabase_client  # noqa: E402

# (label, expected_family, attach_the_page, [paraphrases])
#
# Two deliberate splits in the fixtures:
#
# * The brief's literal «علاقة الإيجار» / «عقد إيجار» sentences are kept, but
#   نظام العمل genuinely says nothing about leases, so a model that objects is
#   not necessarily mis-routing. Semantically COHERENT paraphrases (إجازات /
#   عقد عمل) run alongside them so the two causes can be told apart.
# * Axis F re-runs the axis-E sentences with NO attachment at all. E vs F is the
#   only pair that answers the question actually asked — does attaching a page
#   change the family the router picks for the SAME sentence?
CASES: list[tuple[str, str, bool, list[str]]] = [
    ("A · whole attached object (deictic)", "simple_search", True, [
        "اش يقول هذا النظام؟",
        "ودّي أعرف وش فيه هذا النظام اللي فتحته",
        "أعطني نظرة عامة على هذا النظام",
        "وش محتوى هذا النظام باختصار؟",
    ]),
    ("B · qualifier INSIDE the document (deictic)", "deep_search", True, [
        "اش يقول هذا النظام عن علاقة الإيجار؟",          # the brief's literal
        "وش الأحكام المتعلقة بعلاقة الإيجار في هذا النظام؟",
        "هذا النظام فيما يخص الإيجار وش ينص؟",
        "ابغى المواد اللي تبيّن علاقة الإيجار في هذا النظام",
        "اش يقول هذا النظام عن الإجازات السنوية؟",        # coherent
        "وش الأحكام المتعلقة بساعات العمل في هذا النظام؟",  # coherent
    ]),
    ("C · writing on top of it (deictic)", "writing", True, [
        "اكتب لي عقد إيجار بناءً عليه",                   # the brief's literal
        "صيغ لي عقد إيجار مستند على هذا النظام",
        "أبغاك تجهز مسودة عقد إيجار حسب هذا النظام",
        "اكتب لي عقد عمل بناءً عليه",                     # coherent
        "صيغ لي عقد عمل مستند على هذا النظام",            # coherent
    ]),
    ("D · a totally different نظام", "simple_search", True, [
        "اش هي المادة 5 من نظام التنفيذ؟",
        "ورّني نص المادة الخامسة من نظام التنفيذ",
        "أبغى المادة 5 من نظام التنفيذ نصها",
    ]),
    ("E · NAMED, page attached", "simple_search", True, [
        "اش يقول نظام العمل؟",
        "أعطني نظرة عامة على نظام العمل",
        "وش محتوى نظام العمل باختصار؟",
    ]),
    ("E2 · NAMED + qualifier, page attached", "deep_search", True, [
        "اش يقول نظام العمل عن الإجازات السنوية؟",
        "وش الأحكام المتعلقة بساعات العمل في نظام العمل؟",
    ]),
    ("E3 · NAMED + writing, page attached", "writing", True, [
        "اكتب لي عقد عمل بناءً على نظام العمل",
        "صيغ لي عقد عمل وفق نظام العمل",
    ]),
    ("F · NAMED, NO attachment (baseline)", "simple_search", False, [
        "اش يقول نظام العمل؟",
        "أعطني نظرة عامة على نظام العمل",
        "وش محتوى نظام العمل باختصار؟",
    ]),
    ("F2 · NAMED + qualifier, NO attachment", "deep_search", False, [
        "اش يقول نظام العمل عن الإجازات السنوية؟",
        "وش الأحكام المتعلقة بساعات العمل في نظام العمل؟",
    ]),
    ("F3 · NAMED + writing, NO attachment", "writing", False, [
        "اكتب لي عقد عمل بناءً على نظام العمل",
        "صيغ لي عقد عمل وفق نظام العمل",
    ]),
]


async def route_one(sb, convo_id: str, question: str, attach: bool = True) -> dict[str, Any]:
    ctx = load_router_context(sb, FX.USER_ID, convo_id, None)
    # Isolate the variable under test: exactly ONE attached library page — the
    # carried نظام العمل — rendered exactly as _load_workspace_item_summaries
    # built it. The other carried types stay out so the axis is clean.
    summaries = [
        s for s in ctx.workspace_item_summaries
        if (s.get("title") or "") == FX.REG_LABOR_TITLE
    ] if attach else []
    result = await run_router(
        question,
        sb,
        FX.USER_ID,
        convo_id,
        None,
        ctx.case_memory_md,
        ctx.case_metadata,
        ctx.user_preferences,
        [],                               # fresh turn — no prior messages
        workspace_item_summaries=summaries,
        compaction_summary_md=ctx.compaction_summary_md,
        user_call_name=ctx.user_call_name,
    )
    out = result.output
    is_dispatch = isinstance(out, DispatchAgent)
    return {
        "question": question,
        "decision": getattr(out, "type", None),
        "family": getattr(out, "agent_family", None) if is_dispatch else None,
        "task_label": getattr(out, "task_label", None) if is_dispatch else None,
        "attached": list(getattr(out, "attached_item_ids", []) or []) if is_dispatch else [],
        # What the router SAID when it chose to answer instead of dispatching —
        # the difference between "asked a clarifying question" and "answered the
        # legal question itself off a 6k snapshot" is the whole finding.
        "chat_message": (getattr(out, "message", "") or "")[:600] if not is_dispatch else "",
        "chat_message_chars": len(getattr(out, "message", "") or "") if not is_dispatch else 0,
        "summaries_seen": len(summaries),
    }


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
    print(f"scratch conversation: {convo_id}\n")

    matrix: list[dict[str, Any]] = []
    for label, expected, attach, questions in CASES:
        print(f"── {label}  (expect {expected}, attached={attach})")
        for q in questions:
            try:
                r = await route_one(sb, convo_id, q, attach)
            except Exception as exc:  # noqa: BLE001
                r = {"question": q, "decision": "ERROR", "family": None,
                     "error": repr(exc)}
            r["axis"] = label
            r["expected"] = expected
            r["attached_page"] = attach
            r["ok"] = (r.get("family") == expected)
            matrix.append(r)
            mark = "PASS" if r["ok"] else "FAIL"
            print(f"  {mark}  {q}")
            print(f"        decision={r.get('decision')} family={r.get('family')} "
                  f"label={r.get('task_label')!r} attached={len(r.get('attached') or [])}")
            if r.get("chat_message"):
                print(f"        chat[{r.get('chat_message_chars')}]: "
                      f"{r['chat_message'][:200]}")

    out = {
        "conversation_id": convo_id,
        "matrix": matrix,
        "passed": sum(1 for r in matrix if r["ok"]),
        "failed": sum(1 for r in matrix if not r["ok"]),
    }
    path = Path(__file__).with_name("case_b_routing_results.json")
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n{out['passed']} passed · {out['failed']} failed → {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
