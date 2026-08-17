"""Assign the explicit verdict to every probe already flushed. No DB, no LLM.

Kept separate from the drivers on purpose: the drivers record RAW outcome so a
session death never loses evidence, and grading is a pure re-read of what they
wrote. Re-runnable.
"""
from __future__ import annotations

import json

from adv_family_common import flush, load  # noqa: E402

VERDICTS: dict[str, tuple[str, str]] = {
    "struct-n-prefix": ("PASS", (
        "Every preview carries a [n] and it equals the ref row's own n — on the "
        "1,2,3 card, the 4,5,7,11 card, and the 2-ruling scratch card. The "
        "2026-08-16 fix holds.")),
    "struct-panel-order": ("PASS-NOT-DIAGNOSTIC", (
        "Candidate slot == panel [n] on the scratch card, so this card cannot "
        "tell an ordinal pick apart from a panel pick. B1b is the probe that "
        "separates them, and it FAILS.")),
    "struct-writing-refs": ("PASS", (
        "agent_writing WI 639cab9b: 17 used ref rows → 17 candidates, 5 case "
        "rows → 5 judgment candidates, 0 NULL item_id. The copied rows join "
        "exactly like an agent_search item's — casec-04's structural fear is "
        "unfounded.")),
    "struct-dedup": ("PASS-ONE-LAYER-LATER", (
        "collect_case_c_candidates does NOT dedup: the shared ruling is offered "
        "to the searcher TWICE, as C1 and C2, with identical identity_key. The "
        "collapse is group_documents' doing at fan-out — 1 group, so 1 "
        "synthesizer and 1 unlock. The money property holds; the candidate list "
        "still shows the model the same document twice.")),

    "A1-hair-01": ("PASS", (
        "aborted=False, 2 synthesizers, 2 replies, 2 cards, Δunlocks 0. The "
        "§13g guard does NOT over-fire on two independent lookups — the "
        "over-fire direction is clean. See the report for the framing defect in "
        "the replies themselves.")),
    "A2-abort-direct": ("PASS", (
        "THE HEADLINE. aborted=True with a populated abort_reason naming the "
        "integrative case, 0 chat messages, 0 cards, 0 new ledger rows. A "
        "router miss on «قارن الحكمين» costs nothing and hands off.")),
    "A3-corpus-01": ("PASS", (
        "One document, one synthesizer, one card, no phantom-plurality abort. "
        "The reply covers both the ابتدائي and the استئناف stages off the one "
        "cases row (appeal_judgment_number=149, appeal_result=إلغاء_كلي).")),
    "A4-hair-03": ("PASS", (
        "No abort. D5 grouped both مواد of نظام العمل into ONE synthesizer, "
        "which actually performed the comparison ('كل منهما تنظم حالة مختلفة "
        "تماماً'). Same-document comparison is correctly not 'across more than "
        "one document'.")),

    "B1-casec-01": ("PASS", (
        "«افتح المصدر رقم 3» on a card printing [1][2][3] opened ref [3] "
        "(نظام مكافحة جرائم المعلوماتية، المواد 8–16). The [n] prefix is being "
        "used.")),
    "B1b-casec-01-gappy": ("FAIL", (
        "On a card whose panel prints [4][5][7][11], «افتح المصدر رقم 3» "
        "confidently opened ref [7] — the THIRD candidate in internal order — "
        "and answered as if it were المصدر رقم 3. No hedge, no ask. This is "
        "casec-03's 'ordinal pick keyed to internal ordering' trap firing "
        "through casec-01's phrasing, and the [n] fix does not prevent it.")),
    "B2-casec-03": ("PASS-NOT-DIAGNOSTIC", (
        "Opened panel [2] (the تأميني ruling), Δunlocks 0. Correct — but on "
        "this card slot order and panel order coincide, so it does not "
        "discriminate. B1b shows that when they diverge, internal order wins.")),
    "B3-casec-04": ("PARTIAL", (
        "No guess, no charge — it asked. But it asked the WRONG question: «لم "
        "أجد مصدراً باسم مذكرة في القائمة المعروضة لديك». «المذكرة» IS the "
        "attached writing card; the searcher's candidate list carries the "
        "card's refs but not the card's own identity, so it looked for a "
        "source called مذكرة among them. The correct ask is «أي من الأحكام "
        "الخمسة في المذكرة؟». The join itself is fine (struct-writing-refs).")),
    "B4-casec-05": ("PASS", (
        "The same ruling reached the searcher as two candidates from two "
        "attached WIs and produced ONE reply, ONE card, ONE ref (dac45545), "
        "Δunlocks 0. No double charge.")),

    "C1-corpus-02": ("PASS", (
        "The 14-article range did NOT burn the tool budget. The searcher "
        "resolved the PARENT نظام once (level=regulation_doc) — the باب "
        "strategy — and one synthesizer served 77–90 off the whole-reg unfold, "
        "with an explicit caveat about coverage ('قد لا ترد المادة 89'). "
        "Neither failure mode (dying mid-budget / silently serving 10 of 14) "
        "occurred.")),
    "C2-corpus-04": ("PASS", (
        "«واللي بعدها؟» with the referent one turn up resolved to المادة 78 of "
        "نظام العمل — card titled «المادة 78 من نظام العمل», level=article. The "
        "history window carries the referent and the arithmetic worked for a "
        "plain integer.")),
    "C3-corpus-05": ("PASS", (
        "Paused with ask_user rather than opening anything. Note the corpus "
        "fact that sharpens the fixture: «نظام العلم للمملكة العربية السعودية» "
        "is a REAL row (the flag law), so a confident open was available and "
        "was not taken. The ask stays inside the العلم title-space and never "
        "floats «العمل» as a possible typo.")),
    "C4-corpus-03": ("PASS", (
        "Did not silently serve the current نظام الشركات as «القديم» — it "
        "paused and asked which is meant, naming the repealed م/٦ 1385هـ vs the "
        "current one. Better than the fixture's own expectation. Caveat: the "
        "distinction comes from the model's parametric knowledge, not the "
        "corpus (only ONE نظام الشركات row exists, 92b8d296, in_force), so if "
        "the user answers «القديم» there is nothing to serve.")),
    "C5-bab-01": ("PASS", (
        "Resolved the parent نظام (level=regulation_doc) and scoped the answer "
        "to الباب الثالث: توظيف غير السعوديين, المواد 32–41 — correct against "
        "the two chunks (positions 6, 7) that carry it. Card titled «الباب "
        "الثالث من نظام العمل: توظيف غير السعوديين». Not a whole-نظام "
        "overview.")),
    "C6-bab-03": ("PASS", (
        "«الباب العشرين» — said outright the نظام has no such باب, listed the "
        "real 16 in order, and offered to serve one. Zero renumbering, zero "
        "substitution.")),
}


def main() -> None:
    doc = load()
    missing = sorted(set(doc["probes"]) - set(VERDICTS))
    for pid, (verdict, why) in VERDICTS.items():
        if pid not in doc["probes"]:
            print(f"  (not run: {pid})")
            continue
        doc["probes"][pid]["verdict"] = verdict
        doc["probes"][pid]["why"] = why
    doc["summary"] = {
        "counts": {v: sum(1 for p in doc["probes"].values()
                          if p.get("verdict") == v)
                   for v in sorted({p.get("verdict") for p in doc["probes"].values()})},
        "abort_guard": {
            "over_fire_hair_01": "PASS — fanned out 2, did not abort",
            "under_fire_direct_comparison": "PASS — aborted, 0 synthesizers, 0 unlocks",
        },
        "fails": [p for p, v in doc["probes"].items() if v.get("verdict") == "FAIL"],
        "partials": [p for p, v in doc["probes"].items()
                     if v.get("verdict", "").startswith(("PARTIAL", "PASS-NOT"))],
    }
    flush(doc)
    print(json.dumps(doc["summary"], ensure_ascii=False, indent=2))
    if missing:
        print(f"ungraded probes: {missing}")


if __name__ == "__main__":
    main()
