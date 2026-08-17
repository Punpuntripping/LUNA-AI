"""Case-C parity — plan §2.3.1 / §2.3.2, BOTH bounds, against real rows.

For each sampled workspace item we render the SAME refs twice:

* **card side** — ``references_service.fetch_item_references_payload`` (what the
  المراجع panel receives, i.e. what the user reads);
* **agent side** — ``unfold_workspace_item.resolve_used_sources`` (the
  ``SourceLine`` list that becomes the searcher's case-C candidate list).

Then it asserts, per domain:

* *at least the card* — every string the panel prints (doc_type chip, the case
  card's derived title incl. the «حكم {court}» fallback, circular title +
  entity, service name) is present in the agent line;
* *no more than the card* — no body-derived string exceeds the card's snippet
  cap (500), and a case line carries no summary text past that cap.

Read-only. No LLM calls.
"""
from __future__ import annotations

import asyncio
import json
from collections import Counter

from case_c_common import USER_ID, hr, service_client, short  # noqa: E402

from agents.tool_repository.unfold_workspace_item import (  # noqa: E402
    _SNIPPET_MAX_CHARS,
    resolve_used_sources,
)
from backend.app.services.references_service import (  # noqa: E402
    fetch_item_references_payload,
)

# WIs sampled to cover all four domains, cases-heavy first (a panel with 8+
# rulings is the interesting one — it is where the over-fetch bit hardest).
SAMPLE_WIS = [
    ("91680d79-b236-411b-a7d3-ef5f5c15453f", "تصفية شراكة (18 حكم + 10 نظام)"),
    ("3f6ba51b-5f53-44a2-8805-83541683740e", "الفصل التعسفي (9 أحكام)"),
    ("06c898ee-8284-4f4c-9bf3-19887d6c846c", "إقرار الورثة (8 أحكام + 4 أنظمة)"),
    ("94903243-cd68-4f92-98b1-6bf453d6c61e", "السيارة المسحوبة (كل النطاقات + تعميم)"),
    ("24b01fd8-4eae-4917-a21f-1b20f5e75f9b", "الهوية الوطنية (تعاميم + خدمات)"),
    ("2d197e0c-5112-4af8-bab9-0de50dc01651", "حقوق الأرملة (خدمات)"),
    ("4c789ca0-4600-4d74-9a5a-69ccffcce27a", "تطبيق AI (34 مرجع نظامي)"),
]

FAILURES: list[dict] = []


def fail(kind: str, **info) -> None:
    FAILURES.append({"kind": kind, **info})
    print(f"    !! FAIL [{kind}] " + json.dumps(info, ensure_ascii=False)[:600])


async def check_wi(sb, wi_id: str, label: str) -> dict:
    payload = await fetch_item_references_payload(sb, wi_id, used_only=True)
    cards = {int(c["n"]): c for c in payload}
    lines = {ln.n: ln for ln in resolve_used_sources(sb, wi_id)}

    stats = Counter()
    print(f"\n--- WI {wi_id[:8]} · {label} · cards={len(cards)} lines={len(lines)}")

    if set(cards) != set(lines):
        fail(
            "n_set_mismatch",
            wi=wi_id,
            only_card=sorted(set(cards) - set(lines)),
            only_agent=sorted(set(lines) - set(cards)),
        )

    for n in sorted(set(cards) & set(lines)):
        card, line = cards[n], lines[n]
        dom = card.get("domain")
        stats[dom] += 1
        text = line.text

        if line.domain != dom:
            fail("domain_mismatch", wi=wi_id, n=n, card=dom, agent=line.domain)

        if text == "(مصدر غير متوفر)":
            # The §2.3.1 latent divergence: card renders, agent stubs.
            fail(
                "stub_beside_real_card",
                wi=wi_id, n=n, domain=dom,
                card_title=short(card.get("title", ""), 80),
                has_source=card.get("has_source"),
            )
            continue

        # ---------- at least the card ----------
        if dom == "regulations":
            chip = (card.get("doc_type") or "").strip()
            if chip and chip not in text:
                fail("missing_doc_type_chip", wi=wi_id, n=n, chip=chip,
                     line=short(text, 200))
            elif chip:
                stats["chip_ok"] += 1
                if chip != "نظام":
                    stats["chip_non_default"] += 1
            reg_title = (card.get("regulation_title") or "").strip()
            if reg_title and reg_title[:60] not in text:
                fail("missing_reg_title", wi=wi_id, n=n,
                     card_reg_title=short(reg_title, 100), line=short(text, 200))
            ctitle = (card.get("title") or "").strip()
            if ctitle and ctitle[:60] not in text:
                fail("missing_chunk_title", wi=wi_id, n=n,
                     card_title=short(ctitle, 100), line=short(text, 200))

        elif dom == "cases":
            ctitle = (card.get("title") or "").strip()
            if ctitle and ctitle[:80] not in text:
                fail("missing_case_card_title", wi=wi_id, n=n,
                     card_title=short(ctitle, 140), line=short(text, 260))
            else:
                stats["case_title_ok"] += 1
            # ---------- no more than the card ----------
            head_sep = " — "
            body = text.split(head_sep, 1)[1] if head_sep in text else ""
            snippet = " ".join((card.get("snippet") or "").split())
            if len(body) > _SNIPPET_MAX_CHARS:
                fail("case_body_over_cap", wi=wi_id, n=n, body_chars=len(body),
                     cap=_SNIPPET_MAX_CHARS, body=short(body, 200))
            else:
                stats["case_cap_ok"] += 1
            if snippet and body:
                # The agent body must be the card's own snippet (whitespace
                # collapsed), not a longer slice of the same summary.
                if body[:120] != snippet[:120]:
                    fail("case_snippet_divergence", wi=wi_id, n=n,
                         card=short(snippet, 220), agent=short(body, 220))
                else:
                    stats["case_snippet_match"] += 1
                if len(body) > len(snippet) + 5:
                    fail("case_body_longer_than_card", wi=wi_id, n=n,
                         card_chars=len(snippet), agent_chars=len(body))

        elif dom == "circulars":
            ctitle = (card.get("title") or "").strip()
            if ctitle and ctitle[:60] not in text:
                fail("missing_circular_title", wi=wi_id, n=n,
                     card_title=short(ctitle, 100), line=short(text, 200))
            ent = (card.get("regulation_title") or "").strip()
            if ent and ent[:40] not in text:
                fail("missing_circular_entity", wi=wi_id, n=n,
                     card_entity=ent, line=short(text, 200))

        elif dom == "compliance":
            ctitle = (card.get("title") or "").strip()
            if ctitle and ctitle[:60] not in text:
                fail("missing_service_name", wi=wi_id, n=n,
                     card_title=short(ctitle, 120), line=short(text, 200))

        # universal upper bound
        if len(text) > _SNIPPET_MAX_CHARS * 3:
            fail("line_over_3x_cap", wi=wi_id, n=n, chars=len(text))

    # Report chars: what the whole candidate list costs the searcher.
    total = sum(len(l.text) for l in lines.values())
    case_lines = [l for l in lines.values() if l.domain == "cases"]
    print(f"    candidate-list chars: {total} (cases {sum(len(l.text) for l in case_lines)} "
          f"over {len(case_lines)} rulings)")
    for n in sorted(lines)[:3]:
        print(f"    [{n}] {short(lines[n].text, 200)}")
    return {"wi": wi_id, "label": label, "chars": total, "stats": dict(stats),
            "lines": {n: lines[n].text for n in sorted(lines)},
            "cards": {n: cards[n] for n in sorted(cards)}}


async def main() -> None:
    sb = service_client()
    hr("CASE-C PARITY — §2.3.1 (at least the card) + §2.3.2 (no more than the card)")
    out = []
    for wi_id, label in SAMPLE_WIS:
        out.append(await check_wi(sb, wi_id, label))

    hr("SUMMARY")
    agg = Counter()
    for r in out:
        for k, v in r["stats"].items():
            agg[k] += v
    print(json.dumps(dict(agg), ensure_ascii=False, indent=2))
    print(f"\nFAILURES: {len(FAILURES)}")
    for f in FAILURES:
        print("  - " + json.dumps(f, ensure_ascii=False)[:400])

    with open("agents/simple_search/eval/case_c_parity_dump.json", "w", encoding="utf-8") as fh:
        json.dump({"results": out, "failures": FAILURES}, fh, ensure_ascii=False, indent=2)
    print("\ndump → agents/simple_search/eval/case_c_parity_dump.json")


if __name__ == "__main__":
    asyncio.run(main())
