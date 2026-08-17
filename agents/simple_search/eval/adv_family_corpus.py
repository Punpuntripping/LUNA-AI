"""PART C — the corpus-shape traps (``corpus-*``) and the family side of ``bab-*``.

All six probes are regulations/articles, which D12 leaves **unmetered**, so this
whole part is money-free by construction.

Two fixture premises had to be corrected against the live corpus before the
probes could mean anything — recorded here rather than silently patched:

* **bab-03 as written is not a miss.** «الباب العاشر من نظام العمل» EXISTS
  (الباب العاشر: تشغيل الأحداث). نظام العمل has **16 أبواب** (chunk titles,
  reg ``da51024f``), so the probe uses «الباب العشرين» — genuinely absent, on
  the same نظام the sibling probe uses.
* **corpus-05's typo is not a near-miss, it is an exact hit.**
  «نظام العلم للمملكة العربية السعودية» is a real regulation in the corpus (the
  FLAG law). So «اش يقول نظام العلم؟» does not "match nothing cleanly" as the
  fixture predicted — it matches one document perfectly, which makes a confident
  open *more* likely, not less, and makes the probe sharper than intended.
"""
from __future__ import annotations

import argparse
import asyncio
import json

from adv_family_common import (  # noqa: E402
    USER_ID, hr, ledger, load, record, service_client, short,
)

from agents.models import ChatMessageSnapshot  # noqa: E402
from agents.simple_search.runner import run_simple_search  # noqa: E402

#: نظام العمل — 30 chunks, 16 أبواب. The reg every article/باب probe uses.
REG_LABOUR = "da51024f-a713-48e7-af87-b6a541f055e4"


async def probe(sb, doc, pid, *, fixture, question, expect, recent=None, note=""):
    before = {str(r["unlock_id"]) for r in ledger(sb)}
    hr(f"{pid} · {fixture}")
    print(f"Q: {question}")
    if recent:
        print(f"history: {[(m.role, short(m.content, 70)) for m in recent]}")
    print(f"expect: {expect}")
    err, res = None, None
    try:
        res = await run_simple_search(
            question, sb, USER_ID, doc["scratch_conversation_id"], None,
            attached_items=[], recent_messages=list(recent or []),
            user_preferences={}, emit_sse=None,
        )
    except Exception as exc:  # noqa: BLE001
        err = f"{type(exc).__name__}: {exc}"
    after = {str(r["unlock_id"]) for r in ledger(sb)}
    payload = {"fixture": fixture, "question": question, "expect": expect,
               "note": note, "error": err,
               "history": [{"role": m.role, "content": m.content} for m in (recent or [])],
               "ledger": {"new_rows_any_type": sorted(after - before)}}
    if res is not None:
        published = []
        for iid in res.created_item_ids:
            rows = (sb.table("workspace_item_references")
                    .select("n, domain, item_id, ref_id").eq("wi_id", iid)
                    .order("n").execute()).data or []
            item = (sb.table("workspace_items").select("title, metadata")
                    .eq("item_id", iid).execute()).data or [{}]
            published.append({"card": iid, "title": item[0].get("title"),
                              "metadata": item[0].get("metadata"),
                              "n_refs": len(rows), "refs": rows})
        payload["result"] = {
            "aborted": res.aborted, "abort_reason": res.abort_reason,
            "paused": res.paused, "question_text": res.question_text,
            "n_chat_messages": len(res.chat_messages),
            "n_created_items": len(res.created_item_ids),
            "chat_messages": list(res.chat_messages)}
        payload["published"] = published
        print(f"→ aborted={res.aborted} paused={res.paused} "
              f"replies={len(res.chat_messages)} cards={len(res.created_item_ids)}")
        for i, m in enumerate(res.chat_messages, 1):
            print(f"   reply {i} ({len(m)} chars):\n      {short(m, 900)}")
        if res.question_text:
            print(f"   ask_user: {short(res.question_text, 400)}")
        for p in published:
            print(f"   card {p['card']} «{short(str(p['title']), 70)}» refs={p['n_refs']} "
                  f"meta={json.dumps(p['metadata'], ensure_ascii=False)[:200]}")
    else:
        print(f"→ ERROR {err}")
    if payload["ledger"]["new_rows_any_type"]:
        print(f"   !! NEW LEDGER ROWS: {payload['ledger']['new_rows_any_type']}")
    return payload


def snap(role: str, content: str) -> ChatMessageSnapshot:
    return ChatMessageSnapshot(role=role, content=content,
                               created_at="2026-08-16T09:00:00+00:00")


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="")
    args = ap.parse_args()
    only = {s.strip() for s in args.only.split(",") if s.strip()}
    sb = service_client()
    doc = load()

    plan = [
        ("C1-corpus-02", dict(
            fixture="corpus-02 — 14 article resolves vs tool_calls_limit=10",
            question="اعطيني المواد من 77 إلى 90 من نظام العمل",
            expect="PASS = whole-parent resolve, or an explicit subset that SAYS so. "
                   "FAIL = dies mid-budget, or serves 10 of 14 unlabelled as complete",
            note="tool_calls_limit=10, request_limit=12 (SEARCHER_LIMITS)")),
        ("C2-corpus-04", dict(
            fixture="corpus-04 — «واللي بعدها؟» resolved off the history window",
            question="واللي بعدها؟",
            recent=[snap("user", "اش تقول المادة 77 من نظام العمل؟"),
                    snap("assistant", "المادة السابعة والسبعون من نظام العمل تتناول "
                                      "التعويض عن إنهاء عقد العمل لسبب غير مشروع…")],
            expect="resolves المادة 78 of نظام العمل — not ask_user, not المادة 77 again")),
        ("C3-corpus-05", dict(
            fixture="corpus-05 — «نظام العلم» typo (and an EXACT title hit)",
            question="اش يقول نظام العلم؟",
            expect="ask / candidates. A confident open with no hint that «العمل» "
                   "may have been meant is the failure the fixture names",
            note="«نظام العلم للمملكة العربية السعودية» is a real corpus row — the flag law")),
        ("C4-corpus-03", dict(
            fixture="corpus-03 — «نظام الشركات القديم» has no old version in corpus",
            question="وش يقول نظام الشركات القديم؟",
            expect="serves the current نظام الشركات AND discloses that the corpus "
                   "holds the current version",
            note="only one 'نظام الشركات' row exists (92b8d296), status_class=in_force")),
        ("C5-bab-01", dict(
            fixture="bab-01 — parent-reg resolve + a باب-SCOPED answer",
            question="اعطيني الباب الثالث من نظام العمل",
            expect="answer about الباب الثالث (توظيف غير السعوديين، المواد 32–41), "
                   "not a whole-نظام overview",
            note="الباب الثالث spans TWO chunks of da51024f (positions 6 and 7)")),
        ("C6-bab-03", dict(
            fixture="bab-03 — a باب that does not exist (نظام العمل has 16)",
            question="اعطيني الباب العشرين من نظام العمل",
            expect="says the باب is not in the document; NO renumbering, no "
                   "substitute section presented as الباب العشرين",
            note="fixture's own «الباب العاشر» EXISTS (تشغيل الأحداث) — corrected")),
    ]

    for pid, kw in plan:
        if only and pid not in only:
            continue
        payload = await probe(sb, doc, pid, **kw)
        record(pid, payload | {"verdict": "SEE-DETAIL"})


if __name__ == "__main__":
    asyncio.run(main())
