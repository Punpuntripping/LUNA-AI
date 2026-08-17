"""unlock-03 — corrected scoring of the captured replies. No new model run.

The live probe's ``refusal_surfaced`` check tested for the canonical Arabic
quota sentence **verbatim** (``unfold._JUDGMENT_REFUSAL_AR['quota_exhausted']``).
That was the wrong test: the refusal text is handed to a synthesizer, and the
synthesizer *paraphrases* it. Two of the three replies plainly convey the quota
state («لانتهاء رصيد فتح المصادر», «رصيد فتح المصادر قد استُنفد») while matching
the exact string nowhere.

This re-scores the replies already captured in the results file. Nothing is
re-run, so the prose being judged is exactly the prose the live probe produced.

It also records the finding the original checks had no slot for, which is the
one that actually answers the fixture's structural question — see
``cross_reply_contradiction`` below.

    .venv/Scripts/python.exe agents/simple_search/eval/adv_unlock_03_rescore.py
"""
from __future__ import annotations

import json

from adv_ms_common import hr, load, save, short  # noqa: E402

FIXTURE = "unlock-03"

#: The quota CONCEPT, not the canonical sentence.
QUOTA_CONCEPT = ["رصيد فتح المصادر", "رصيد المصادر", "استُنفد", "انتهى رصيد",
                 "لانتهاء رصيد", "ترقية الخطة"]

#: Phrases in which a synthesizer denies that the OTHER documents of the same
#: fan-out exist. Each synthesizer sees one document (§2.2), so it answers as if
#: its document were all there is.
DENIAL_PHRASES = ["لا يتوفر لدي أي تقرير", "لا يتوفر لدي التقرير",
                  "لم يرد مع طلبك أي تقرير", "حكم واحد فقط",
                  "الموجود لدي حكم واحد", "لا يتوفر أي نص أو تفصيل",
                  "ولا الأحكام الثلاثة", "لا أحكام أخرى"]


def main() -> int:
    doc = load()
    probe = next((p for p in doc["probes"] if p["fixture"] == FIXTURE), None)
    if not probe:
        raise SystemExit("no unlock-03 probe recorded — run adv_unlock_03.py first")

    replies: list[str] = probe["chat_messages"]
    hr(f"{FIXTURE} — RE-SCORING {len(replies)} captured replies (no new run)")

    per_reply = []
    for i, m in enumerate(replies, 1):
        quota = sorted({w for w in QUOTA_CONCEPT if w in m})
        denial = sorted({p for p in DENIAL_PHRASES if p in m})
        served = len(m) > 500 and ("المنطوق" in m or "الوقائع" in m)
        per_reply.append({
            "n": i, "chars": len(m), "conveys_quota": bool(quota),
            "quota_markers": quota, "denies_other_documents": bool(denial),
            "denial_markers": denial, "appears_to_serve_a_ruling": served,
            "head": short(m, 140),
        })
        print(f"\nreply {i} ({len(m)} chars)")
        print(f"  conveys quota state : {bool(quota)}  {quota}")
        print(f"  denies the others   : {bool(denial)}  {denial}")
        print(f"  actually served     : {served}")

    n_quota = sum(1 for r in per_reply if r["conveys_quota"])
    n_denial = sum(1 for r in per_reply if r["denies_other_documents"])
    n_served = sum(1 for r in per_reply if r["appears_to_serve_a_ruling"])

    hr("CORRECTED VERDICT")
    print(f"replies conveying the quota state : {n_quota}/{len(replies)}")
    print(f"replies DENYING the other rulings : {n_denial}/{len(replies)}")
    print(f"replies that actually served one  : {n_served}/{len(replies)}")

    corrected = [
        {"id": "refusal_surfaced_paraphrased", "want":
            "the quota state does reach the user, in paraphrase",
         "ok": n_quota >= 1,
         "note": "original check demanded the canonical sentence verbatim; the "
                 "synthesizer paraphrases it, so the original FAIL was a scoring "
                 "artifact"},
        {"id": "refusal_attributable", "want":
            "the user can tell WHICH ruling was withheld",
         "ok": False,
         "note": "no reply names a case number, court, or ref for the withheld "
                 "ruling. Reply 1 says «حكم واحد فقط من المحكمة العمالية» — which "
                 "names the court of the ruling it FAILED to open, while "
                 "simultaneously denying the other two exist."},
        {"id": "cross_reply_contradiction", "want":
            "replies must not contradict each other (THE finding)",
         "ok": n_denial == 0,
         "note": f"{n_denial}/{len(replies)} replies assert that the other "
                 "rulings / the attached report do not exist. Each synthesizer "
                 "sees ONE document (§2.2) and answers as though it were the "
                 "whole request, so a 3-ruling fan-out produces replies that "
                 "each deny the premise of the other two."},
    ]
    for c in corrected:
        print(f"\n  [{'PASS' if c['ok'] else 'FAIL'}] {c['id']}")
        print(f"      want: {c['want']}")
        print(f"      note: {c['note']}")

    probe["rescored"] = {
        "why": ("the live check tested for the canonical quota sentence verbatim; "
                "the synthesizer paraphrases it. Re-scored on the SAME captured "
                "replies — no new model run."),
        "per_reply": per_reply,
        "replies_conveying_quota": n_quota,
        "replies_denying_other_documents": n_denial,
        "replies_serving_a_ruling": n_served,
        "corrected_checks": corrected,
        "verdict": ("FAIL (user-hostile) — the partial fan-out is not merely "
                    "indistinguishable, it is self-contradictory: two of three "
                    "replies tell the user there is no attached report and no "
                    "other ruling, while a third serves one and a card is "
                    "published."),
    }
    probe["fake_resolver_artifact"] = {
        "what": ("a 4th access call appeared — c77d4a35 was re-resolved on the "
                 "loop-back and the counter-based fake, having spent its 2 grants, "
                 "refused a ruling it had already granted."),
        "is_production_behaviour": False,
        "why_not": ("a real resolve_access returns already_unlocked/granted/free "
                    "for a ruling opened earlier in the same turn "
                    "(UNIQUE (user_id, content_type, content_id) + ON CONFLICT "
                    "DO NOTHING), so production cannot refuse what it just granted."),
        "what_IS_production_behaviour": ("the loop-back really does re-unfold and "
                                         "therefore re-resolve access for a ruling "
                                         "already opened this turn — free, but a "
                                         "second ledger round-trip per retry."),
    }
    save(doc)
    print(f"\n>>> rescored block flushed into the {FIXTURE} probe")
    print(json.dumps({"quota": n_quota, "denial": n_denial, "served": n_served},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
