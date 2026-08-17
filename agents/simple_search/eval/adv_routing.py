"""ADVERSARIAL ROUTING — the router-level surfaces of the battery, re-measured.

Three blocks, all fired at the REAL ``agents.router.router.run_router``:

**A · comparison re-measure.** The three sentences that scored **0/9, all
``chat_response``** in the §13f re-run, verbatim, in the same borrowed contexts.
The §13g gate (``router.py:256-260``) landed AFTER that measurement and has
never been fired at the queries it was written for.

**B · battery fixtures** whose ``surface`` is ``hairline`` / ``bab`` /
``control``, plus ``casec-02`` — the ones a router decision, not a searcher
decision, settles.

**C · regression sentinels** — the §1.1 dangerous pair (both halves), a prior
report question, a greeting, and the plain whole-نظام lookup. If a fix broke one
of these, the fix costs more than it bought.

Discipline this lane is under (the previous batch died on a session limit and
lost everything it had not flushed):

* the scratch conversation id is written to ``adv_routing_results.json``
  **before the first router call**;
* every probe is appended and flushed the moment it returns — a kill at any
  point leaves a readable, correct partial result;
* re-running resumes: probes already on disk for the same conversation are
  skipped.

Costs money (one tier_2 router call per probe · 54 probes). Writes nothing
outside the ``[ADV-ROUTING]`` scratch conversation.

    .venv/Scripts/python.exe agents/simple_search/eval/adv_routing.py
"""
from __future__ import annotations

import asyncio
import json
import logging
import time

from adv_routing_common import (  # noqa: E402
    CTRL02_CASE_ID, RESULTS_PATH, S_CASES, S_HEIRS, S_ONECASE, USER_ID,
    Results, ensure_scratch_conversation, hr, service_client, short,
)

from agents.models import DispatchAgent  # noqa: E402
from agents.router.context import load_router_context  # noqa: E402
from agents.router.router import run_router  # noqa: E402

REPS = 3

# (id, block, context key, query, expected family, why/trap)
PROBES: list[tuple[str, str, str, str, str, str]] = [
    # ── A · comparison re-measure — baseline 0/9, all chat_response ─────────
    ("cmp-01", "A", "cases", "قارن الحكمين اللي في WI-2",
     "deep_search", "§13g gate, verbatim failing query #1 (was chat_response 3/3)"),
    ("cmp-02", "A", "cases",
     "قارن بين حكم الابتدائية وحكم الاستئناف اللي في WI-2 وش الفرق بينهم",
     "deep_search", "§13g gate, verbatim failing query #2 (was chat_response 3/3)"),
    ("cmp-03", "A", "heirs", "وازن بين الأحكام اللي في WI-3 وأيها أقوى سنداً",
     "deep_search", "§13g gate, verbatim failing query #3 (was chat_response 3/3)"),

    # ── B · battery: hairline ───────────────────────────────────────────────
    ("hair-01", "B", "cases", "اش الحكمين اللي في WI-2؟",
     "simple_search",
     "ONE word from cmp-01. Two independent lookups, not a comparison — the "
     "new gate/abort must not eat it."),
    ("hair-02", "B", "cases", "اعطيني الحكمين اللي في WI-2 وايش الفرق بينهم",
     "deep_search",
     "compound: the comparison half governs the whole request"),
    ("hair-03", "B", "empty", "قارن المادة 77 بالمادة 78 من نظام العمل",
     "deep_search",
     "router gate (all comparison → deep) vs searcher (same document ⇒ not "
     "integrative) disagree by design; record what the ROUTER does"),
    ("hair-04", "B", "empty", "وش تقول المادة 77 عن التعويض؟",
     "simple_search",
     "the Test-1 qualifier rule must NOT fire — a مادة is the atom, «عن …» is "
     "focus not narrowing"),
    # Added AFTER the first pass, to isolate a confound the first pass exposed:
    # hair-04 names no نظام, so its 0/3 could be the KNOWN bare-article «من أي
    # نظام؟» behaviour (§13f measured 3/6) rather than the qualifier rule the
    # fixture is aimed at. Same sentence, law named — the qualifier is then the
    # ONLY thing that could move it off simple_search.
    ("hair-04b", "B", "empty", "وش تقول المادة 77 من نظام العمل عن التعويض؟",
     "simple_search",
     "hair-04 with the law NAMED — isolates the §1.1 qualifier rule from the "
     "bare-article ask-which-law behaviour"),

    # ── B · battery: باب / فصل (addressable since 2026-08-16) ───────────────
    ("bab-01", "B", "empty", "اعطيني الباب الثالث من نظام العمل",
     "simple_search", "باب just entered the router's closed addressable set"),
    ("bab-02", "B", "empty",
     "اش يقول الفصل الثاني من اللائحة التنفيذية لنظام المنافسات والمشتريات الحكومية؟",
     "simple_search", "same via «الفصل»; plus the لائحة-vs-نظام lookalike hazard"),

    # ── B · battery: over-correction controls ───────────────────────────────
    ("ctrl-01a", "B", "cases", "اش رقم القضية اللي في WI-2؟",
     "chat_response",
     "verbatim fixture wording against the 18-ruling WI — metadata question; "
     "dispatching a specialist for a card field is waste"),
    ("ctrl-01b", "B", "onecase", "اش رقم القضية اللي في WI-1؟",
     "chat_response",
     "the fixture's TRUE setup: the only WI in the account citing exactly one "
     "ruling, so the number is unambiguous card metadata"),
    ("ctrl-02", "B", "ss_card", "هل الحكم اللي فتحته لي نهائي؟",
     "chat_response",
     "a prior simple_search card holds the FULL ruling incl. the appeal stage — "
     "re-dispatching re-opens (and can re-meter) a document already in hand"),

    # ── B · battery: Case-C bracket phrasing ────────────────────────────────
    ("casec-02", "B", "cases", "وش يقول المرجع [2]؟",
     "simple_search",
     "must DISPATCH and let the specialist open [2]; answering from the "
     "manifest snippet is the 13b-eval failure in bracket costume"),

    # ── C · regression sentinels ────────────────────────────────────────────
    ("sent-pair-a", "C", "empty", "اش يقول نظام المعاملات المدنية، اهم احكامه",
     "simple_search", "§1.1 dangerous pair — the whole-object half"),
    ("sent-pair-b", "C", "empty", "اش يقول نظام المعاملات المدنية عن علاقة الإيجار",
     "deep_search", "§1.1 dangerous pair — the narrowed half"),
    ("sent-reg", "C", "empty", "اش يقول نظام العمل",
     "simple_search", "the family's canonical reason to exist"),
    ("sent-report", "C", "heirs", "ماذا استنتج التقرير السابق؟",
     "chat_response",
     "answer-directly on what WE wrote must survive the scoping patch"),
    ("sent-greet", "C", "empty", "السلام عليكم",
     "chat_response", "a greeting must never reach a specialist"),
]

# Log lines that prove which router tools actually ran this probe.
_TOOL_LOGGERS = (
    "agents.tool_repository.unfold_workspace_item",
    "agents.tool_repository.save_memo",
    "agents.tool_repository.rayhan_docs",
    "agents.tool_repository.edit_artifact",
    "agents.router.router",
)


class _ToolTap(logging.Handler):
    """Capture the tool-call INFO lines for one probe."""

    def __init__(self) -> None:
        super().__init__(level=logging.INFO)
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:  # noqa: D102
        try:
            msg = record.getMessage()
        except Exception:  # noqa: BLE001
            return
        if any(k in msg for k in ("unfold_workspace_item", "list_workspace_items",
                                  "save_memo", "rayhan", "edit_artifact")):
            self.lines.append(msg[:220])


def _install_tap() -> _ToolTap:
    tap = _ToolTap()
    for name in _TOOL_LOGGERS:
        logging.getLogger(name).addHandler(tap)
        logging.getLogger(name).setLevel(logging.INFO)
    return tap


def _remove_tap(tap: _ToolTap) -> None:
    for name in _TOOL_LOGGERS:
        logging.getLogger(name).removeHandler(tap)


# ── ctrl-02 setup — a real ruling, published as a simple_search card ─────────


def build_ctrl02_card(sb, convo: str) -> dict:
    """Publish ONE synthetic prior-simple_search turn into the scratch convo.

    The account holds **zero** ``agent_family='simple_search'`` workspace items,
    so ctrl-02's setup does not exist and must be constructed. It is built from
    a REAL ``cases`` row (``CTRL02_CASE_ID``) so the card genuinely holds the
    whole ruling — including the appeal stage that makes «هل الحكم نهائي؟»
    answerable without opening anything.

    Shape matches ``agents/simple_search/publisher.py``: ``kind='agent_search'``,
    ``created_by='agent'``, ``agent_family='simple_search'``, no
    ``metadata.subtype``. ``summary`` is passed explicitly so the
    ``summarize_artifact_on_insert`` trigger does not fire an analyzer pass.
    """
    row = (
        sb.table("cases").select(
            "id, case_number, court, city, date_hijri, facts, claims, reasoning, "
            "ruling, appeal_court, appeal_judgment_number, appeal_date_hijri, "
            "appeal_result, appeal_reasoning, appeal_ruling"
        ).eq("id", CTRL02_CASE_ID).single().execute()
    ).data

    body = "\n\n".join([
        f"# حكم المحكمة {row['court']} بـ{row['city']} — القضية رقم {row['case_number']}",
        f"**المحكمة:** {row['court']} — {row['city']}  \n"
        f"**رقم القضية:** {row['case_number']}  \n"
        f"**التاريخ:** {row['date_hijri']}  \n"
        f"**محكمة الاستئناف:** {row['appeal_court']} — الحكم رقم "
        f"{row['appeal_judgment_number']} بتاريخ {row['appeal_date_hijri']}  \n"
        f"**نتيجة الاستئناف:** {row['appeal_result']}",
        "## الوقائع\n\n" + (row.get("facts") or ""),
        "## الطلبات\n\n" + (row.get("claims") or ""),
        "## الأسباب\n\n" + (row.get("reasoning") or ""),
        "## منطوق الحكم\n\n" + (row.get("ruling") or ""),
        "## أسباب حكم الاستئناف\n\n" + (row.get("appeal_reasoning") or ""),
        "## منطوق حكم الاستئناف\n\n" + (row.get("appeal_ruling") or ""),
    ])

    from backend.app.services.workspace_service import create_workspace_item

    item = create_workspace_item(
        sb, USER_ID,
        kind="agent_search", created_by="agent",
        title=f"حكم المحكمة {row['court']} بـ{row['city']} — القضية رقم {row['case_number']}",
        conversation_id=convo, agent_family="simple_search",
        content_md=body,
        metadata={"ref_count": 1, "cited_count": 1, "level": "L4",
                  "data_type": "judgment", "eval_synthetic": True},
        summary=(
            f"نص الحكم كاملاً في القضية رقم {row['case_number']} الصادر عن المحكمة "
            f"{row['court']} بـ{row['city']} بتاريخ {row['date_hijri']}، ويشمل الوقائع "
            f"والطلبات والأسباب ومنطوق الحكم، إضافةً إلى حكم الاستئناف الصادر عن "
            f"{row['appeal_court']} برقم {row['appeal_judgment_number']} ونتيجته."
        ),
    )
    item_id = str(item["item_id"])

    sb.table("messages").insert({
        "conversation_id": convo, "role": "user",
        "content": f"اعطيني تفاصيل الحكم رقم {row['case_number']}",
    }).execute()
    sb.table("messages").insert({
        "conversation_id": convo, "role": "assistant",
        "content": (
            f"فتحت لك الحكم رقم {row['case_number']} كاملاً في بطاقة مساحة العمل — "
            "الوقائع والأسباب والمنطوق وحكم الاستئناف."
        ),
        "artifact_ids": [item_id],
    }).execute()

    return {"item_id": item_id, "case_id": CTRL02_CASE_ID,
            "case_number": row["case_number"], "content_chars": len(body),
            "appeal_result": row["appeal_result"],
            "appeal_court": row["appeal_court"]}


# ── The run ─────────────────────────────────────────────────────────────────


async def one_probe(sb, convo: str, ctx, pid: str, block: str, ctx_key: str,
                    q: str, expect: str, why: str, rep: int) -> dict:
    tap = _install_tap()
    t0 = time.monotonic()
    try:
        rr = await run_router(
            q, sb, USER_ID, convo, None,
            ctx.case_memory_md, ctx.case_metadata, ctx.user_preferences,
            ctx.message_history,
            workspace_item_summaries=ctx.workspace_item_summaries,
            compaction_summary_md=ctx.compaction_summary_md,
            user_call_name=ctx.user_call_name,
            welcome=None,
        )
    except Exception as exc:  # noqa: BLE001
        _remove_tap(tap)
        return {"id": pid, "block": block, "rep": rep, "context": ctx_key, "q": q,
                "expect": expect, "why": why, "got": "ERROR", "ok": False,
                "error": f"{type(exc).__name__}: {exc}"[:400],
                "elapsed_s": round(time.monotonic() - t0, 2),
                "tools": tap.lines}
    _remove_tap(tap)

    out = rr.output
    row: dict = {"id": pid, "block": block, "rep": rep, "context": ctx_key, "q": q,
                 "expect": expect, "why": why,
                 "elapsed_s": round(time.monotonic() - t0, 2), "tools": tap.lines}
    if isinstance(out, DispatchAgent):
        row["got"] = out.agent_family
        row["dispatch"] = {"task_label": out.task_label,
                           "target_wi": out.target_wi,
                           "attached_wis": out.attached_wis,
                           "subtype": getattr(out, "subtype", None)}
        row["message"] = ""
    else:
        row["got"] = "chat_response"
        msg = str(getattr(out, "message", "") or "")
        row["message"] = msg
        row["message_chars"] = len(msg)
    row["ok"] = row["got"] == expect
    return row


async def main() -> None:
    sb = service_client()
    convo = ensure_scratch_conversation(sb)
    res = Results(convo)          # resumes from disk; never truncates first
    hr(f"ADVERSARIAL ROUTING — scratch convo {convo}")
    print(f"results → {RESULTS_PATH}")
    print(f"{len(PROBES)} fixtures × {REPS} reps = {len(PROBES) * REPS} router calls")

    res.doc["baseline"] = {
        "convos_before": (sb.table("conversations").select("conversation_id", count="exact")
                          .eq("user_id", USER_ID).is_("deleted_at", "null").execute()).count,
        "unlocks_before": (sb.table("library_unlocks").select("unlock_id", count="exact")
                           .eq("user_id", USER_ID).execute()).count,
        "judgment_unlocks_before": (
            sb.table("library_unlocks").select("unlock_id", count="exact")
            .eq("user_id", USER_ID).eq("content_type", "judgment").execute()).count,
    }
    res.save()
    print(f"baseline: {res.doc['baseline']}")

    # Contexts. The EMPTY one is loaded from the scratch convo BEFORE the
    # ctrl-02 card lands there, so "empty" stays empty for the whole run.
    ctxs = {
        "empty": load_router_context(sb, USER_ID, convo, None),
        "cases": load_router_context(sb, USER_ID, S_CASES, None),
        "heirs": load_router_context(sb, USER_ID, S_HEIRS, None),
        "onecase": load_router_context(sb, USER_ID, S_ONECASE, None),
    }
    for k, c in ctxs.items():
        print(f"  ctx[{k}]: {len(c.workspace_item_summaries)} items, "
              f"{len(c.message_history)} history msgs")

    # ctrl-02 needs a card that does not exist anywhere in the account. Built
    # last-minute and run last, so it can never leak into another probe's
    # list_workspace_items view of the scratch conversation.
    ordered = [p for p in PROBES if p[2] != "ss_card"]
    ss_card = [p for p in PROBES if p[2] == "ss_card"]

    for pid, block, ctx_key, q, expect, why in ordered:
        for rep in range(REPS):
            if res.has(pid, rep):
                print(f"[{pid}#{rep}] cached — skip")
                continue
            row = await one_probe(sb, convo, ctxs[ctx_key], pid, block,
                                  ctx_key, q, expect, why, rep)
            res.add(row)
            _print(row)

    if ss_card:
        if not res.doc.get("synthetic_wi"):
            card = build_ctrl02_card(sb, convo)
            res.set("synthetic_wi", card)
            print(f"\nctrl-02 card published: {card}")
        ctx_ss = load_router_context(sb, USER_ID, convo, None)
        print(f"  ctx[ss_card]: {len(ctx_ss.workspace_item_summaries)} items, "
              f"{len(ctx_ss.message_history)} history msgs")
        for pid, block, ctx_key, q, expect, why in ss_card:
            for rep in range(REPS):
                if res.has(pid, rep):
                    print(f"[{pid}#{rep}] cached — skip")
                    continue
                row = await one_probe(sb, convo, ctx_ss, pid, block, ctx_key,
                                      q, expect, why, rep)
                res.add(row)
                _print(row)

    res.doc["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    res.set("done", True)
    _summarize(res.doc["probes"])
    print(f"\ndump → {RESULTS_PATH}")
    print(f"SCRATCH CONVERSATION TO DELETE: {convo}")


def _print(row: dict) -> None:
    tag = "PASS" if row["ok"] else "FAIL"
    print(f"\n[{row['id']}#{row['rep']}] {tag}  expect={row['expect']} "
          f"got={row['got']}  ({row['elapsed_s']}s)")
    print(f"    Q: {row['q']}")
    if row.get("tools"):
        print(f"    tools: {row['tools']}")
    if row["got"] == "chat_response":
        print(f"    → chat ({row.get('message_chars', 0)} chars): "
              f"{short(row.get('message', ''), 300)}")
    elif row["got"] == "ERROR":
        print(f"    → {row.get('error')}")
    else:
        print(f"    → {json.dumps(row.get('dispatch'), ensure_ascii=False)[:300]}")


def _summarize(probes: list[dict]) -> None:
    hr("PER-FIXTURE")
    from collections import Counter
    seen: list[str] = []
    for p in probes:
        if p["id"] in seen:
            continue
        seen.append(p["id"])
        rows = [r for r in probes if r["id"] == p["id"]]
        got = Counter(r["got"] for r in rows)
        n_ok = sum(1 for r in rows if r["ok"])
        mark = "PASS" if n_ok == len(rows) else ("PART" if n_ok else "FAIL")
        print(f"  [{p['id']:<12}] {mark}  want={p['expect']:<14} "
              f"{n_ok}/{len(rows)}  {dict(got)}")
    hr("BY BLOCK")
    for b in ("A", "B", "C"):
        rows = [r for r in probes if r["block"] == b]
        if rows:
            print(f"  block {b}: {sum(1 for r in rows if r['ok'])}/{len(rows)} runs")
    print(f"\nTOTAL {sum(1 for r in probes if r['ok'])}/{len(probes)} runs correct")


if __name__ == "__main__":
    asyncio.run(main())
