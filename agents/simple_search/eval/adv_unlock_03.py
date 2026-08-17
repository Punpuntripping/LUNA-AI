"""unlock-03 — the PARTIAL fan-out at the quota edge. What does the user SEE?

§13h: *"partial fan-out has never been rendered — three half-replies where one is
a refusal could read as a system error."* The structural question is whether the
reply distinguishes the two rulings that opened from the one that did not, per
ruling, or whether it comes out as mush.

**Zero real ledger writes, by construction.** The brief forbids draining live
quota, so ``runner.judgment_access_resolver`` is replaced wholesale with a
wrapper that never calls ``library_service.resolve_access`` at all: it hands back
:class:`JudgmentAccess` verdicts directly — ``granted`` for the first two rulings
it is asked about, ``quota_exhausted`` for the third. Nothing can reach
``library_unlocks``; the probe asserts the ledger is byte-identical anyway.

The searcher is scripted so the same three rulings are selected every time (the
live ``unlock-02`` run showed a real searcher can wander off into ``ask_user``,
which would measure nothing). The **synthesizers are real** — their prose is the
entire object of study.

    .venv/Scripts/python.exe agents/simple_search/eval/adv_unlock_03.py
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from adv_ms_common import (  # noqa: E402
    USER_ID, WI_THREE_CASES, WI_THREE_RULINGS, WI_THREE_TITLE, flush_probe, hr,
    ledger_fingerprint, record_workspace_items, scratch_id, service_client, short,
)

from agents.simple_search import runner as R  # noqa: E402
from agents.simple_search.models import ResolvedObject  # noqa: E402
from agents.simple_search.searcher import SearcherDecision  # noqa: E402
from agents.simple_search.unfold import JudgmentAccess  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))
from _fmodels import output_model  # noqa: E402

FIXTURE = "unlock-03"
QUESTION = "اعطيني تفاصيل الأحكام الثلاثة المذكورة في التقرير المرفق"

GRANTS_ALLOWED = 2

#: The Arabic quota line `unfold._JUDGMENT_REFUSAL_AR['quota_exhausted']` serves.
QUOTA_LINE_MARKER = "انتهى رصيد فتح المصادر"


async def main() -> int:
    sb = service_client()
    convo = scratch_id()
    hr(f"{FIXTURE} — 2 rulings open, 1 refuses. What does the reply look like?")
    print(f"scratch conversation: {convo}")
    print(f"attached WI: {WI_THREE_RULINGS} — «{WI_THREE_TITLE}»")
    print(f"resolver: grants the first {GRANTS_ALLOWED}, then quota_exhausted")
    print("NO real ledger writes — resolve_access is never called.")

    base = ledger_fingerprint(sb)
    print(f"\nledger before: {base['total']} rows")

    # ── The fake resolver. Never touches the ledger. ─────────────────────────
    access_log: list[dict] = []
    lock = asyncio.Lock()

    def _fake_resolver(supabase, user_id):
        async def _resolve(case_id: str) -> JudgmentAccess:
            async with lock:
                n = len(access_log) + 1
                granted = n <= GRANTS_ALLOWED
                access_log.append({
                    "order": n, "case_id": case_id, "granted": granted,
                    "reason": "granted" if granted else "quota_exhausted",
                })
            print(f"  access[{n}] {case_id} → "
                  f"{'GRANTED' if granted else 'REFUSED (quota_exhausted)'}")
            return JudgmentAccess(
                case_id=case_id, granted=granted, charged=granted,
                reason="granted" if granted else "quota_exhausted",
            )
        return _resolve

    objects = [
        ResolvedObject(level="judgment", case_id=cid, case_ref=cref,
                       title=f"حكم {court}", subtitle=court)
        for cid, cref, court in WI_THREE_CASES
    ]

    import agents.simple_search.searcher as S
    import agents.simple_search.synthesizer as SY

    searcher_script = output_model(
        SearcherDecision(data_type="judgments", objects=objects,
                         rationale="الأحكام الثلاثة المرفقة")
    )
    # Real synthesizer models — restore whatever the module had.
    real_resolver = R.judgment_access_resolver
    real_synth_model = SY.get_agent_model
    real_unfold = R.unfold

    # Per-unfold telemetry. The first attempt at this fixture was invalidated by
    # a transient `[WinError 10035]` on the SECOND round's reads, which turned
    # every ruling into «تعذّر العثور». Recording ok/notes/chars per call makes
    # that failure mode visible instead of silently mis-measured.
    unfold_log: list[dict] = []

    async def _watched_unfold(supabase, obj, *, judgment_access=None):
        res = await real_unfold(supabase, obj, judgment_access=judgment_access)
        unfold_log.append({
            "seq": len(unfold_log) + 1, "case_id": obj.case_id,
            "case_ref": obj.case_ref, "ok": res.ok, "chars": res.chars,
            "notes": list(res.notes), "text_head": short(res.text, 90),
        })
        print(f"  unfold[{len(unfold_log)}] {obj.case_ref} ok={res.ok} "
              f"chars={res.chars} notes={res.notes}")
        return res

    result = None
    try:
        R.judgment_access_resolver = _fake_resolver          # type: ignore[assignment]
        R.unfold = _watched_unfold                           # type: ignore[assignment]
        S.get_agent_model = lambda *_a, **_k: searcher_script.model  # type: ignore[assignment]

        hr("RUN — scripted searcher, REAL synthesizers, faked entitlement")
        result = await R.run_simple_search(
            QUESTION, sb, USER_ID, convo, None,
            attached_items=[{"item_id": WI_THREE_RULINGS, "kind": "agent_search",
                             "title": WI_THREE_TITLE, "metadata": {}}],
            recent_messages=[],
        )
        record_workspace_items(list(result.created_item_ids), fixture=FIXTURE)
    finally:
        R.judgment_access_resolver = real_resolver           # type: ignore[assignment]
        R.unfold = real_unfold                               # type: ignore[assignment]
        SY.get_agent_model = real_synth_model                # type: ignore[assignment]

    # ── Ledger must not have moved ──────────────────────────────────────────
    hr("LEDGER — must be untouched")
    final = ledger_fingerprint(sb)
    untouched = final["ids"] == base["ids"]
    print(f"{base['total']} → {final['total']} rows · byte-identical: {untouched}")

    granted_ids = [a["case_id"] for a in access_log if a["granted"]]
    refused_ids = [a["case_id"] for a in access_log if not a["granted"]]
    print(f"granted: {granted_ids}")
    print(f"refused: {refused_ids}")

    # ── The user-facing surface ─────────────────────────────────────────────
    hr("THE REPLY — is the refusal distinguishable per ruling?")
    replies = list(result.chat_messages)
    print(f"chat replies: {len(replies)} · cards: {len(result.created_item_ids)}")
    print(f"paused={result.paused} aborted={result.aborted}")
    for i, m in enumerate(replies, 1):
        print(f"\n--- reply {i} ({len(m)} chars) ---")
        print(m[:1200])

    joined = "\n\n".join(replies)
    quota_replies = [i for i, m in enumerate(replies, 1) if QUOTA_LINE_MARKER in m]
    says_quota = bool(quota_replies)
    # Does anything tell the user WHICH ruling was withheld?
    names_refused = any(
        (cref in joined or cid in joined)
        for cid, cref, _ in WI_THREE_CASES if cid in refused_ids
    )
    refused_courts = [court for cid, _, court in WI_THREE_CASES if cid in refused_ids]

    print(f"\nreplies containing the Arabic quota line: {quota_replies or 'NONE'}")
    print(f"reply names the refused ruling by ref/id: {names_refused}")
    print(f"refused ruling's court: {refused_courts}")

    checks = [
        {"id": "no_ledger_writes", "want": "ledger byte-identical (brief's hard rule)",
         "ok": untouched},
        {"id": "two_granted_one_refused", "want":
            f"resolver asked 3×, granted {GRANTS_ALLOWED}, refused 1",
         "ok": len(access_log) == 3 and len(refused_ids) == 1},
        {"id": "three_replies", "want": "one reply per ruling — served AND refused",
         "ok": len(replies) == 3},
        {"id": "refusal_surfaced", "want":
            "the quota refusal reaches the user in Arabic",
         "ok": says_quota},
        {"id": "refusal_attributable", "want":
            "the user can tell WHICH ruling was withheld",
         "ok": says_quota and len(quota_replies) == 1},
    ]
    hr("VERDICT")
    for c in checks:
        print(f"  [{'PASS' if c['ok'] else 'FAIL'}] {c['id']:<24} — {c['want']}")

    flush_probe(
        FIXTURE,
        verdict=("PASS — partial fan-out renders as one reply per ruling, the "
                 "refusal isolated to its own")
        if all(c["ok"] for c in checks)
        else "SEE CHECKS — partial fan-out does not render cleanly",
        question=QUESTION,
        setup=f"resolver grants {GRANTS_ALLOWED} then refuses with quota_exhausted",
        models="scripted searcher · REAL synthesizers",
        ledger_writes=0,
        ledger_untouched=untouched,
        access_calls=access_log,
        unfolds=unfold_log,
        unfold_rounds=len(unfold_log) // 3 if unfold_log else 0,
        granted_case_ids=granted_ids,
        refused_case_ids=refused_ids,
        refused_courts=refused_courts,
        n_replies=len(replies),
        n_cards=len(result.created_item_ids),
        chat_messages=replies,
        quota_line_in_replies=quota_replies,
        reply_names_refused_ruling=names_refused,
        checks=checks,
    )
    print(json.dumps({"replies": len(replies), "refused": len(refused_ids),
                      "quota_replies": quota_replies, "untouched": untouched},
                     ensure_ascii=False))
    return 0 if untouched else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
