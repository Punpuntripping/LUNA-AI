"""Corpus-wide Case-C parity sweep — every used ref of every agent_search WI.

Same two bounds as ``case_c_parity.py`` but over the whole eval account
(135 WIs / 1,522 refs), so the failure rates are measured, not sampled.

Additionally counts:
  * `(مصدر غير متوفر)` stubs rendered beside a real card (the §2.3.1 latent
    ref_id-vs-item_id divergence);
  * case cards whose snippet carries the ruling's `referenced_regulations`
    tail — strings the agent line structurally cannot show;
  * the candidate-list char cost per WI (what the searcher pays to identify).

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

STATS = Counter()
FAILS: list[dict] = []
ROWS: list[dict] = []


def norm(s: str) -> str:
    return " ".join((s or "").split())


async def main() -> None:
    sb = service_client()
    hr("CORPUS-WIDE CASE-C PARITY SWEEP")
    wis = (
        sb.table("workspace_items")
        .select("item_id, wi_seq, title, conversation_id")
        .eq("user_id", USER_ID)
        .eq("kind", "agent_search")
        .is_("deleted_at", "null")
        .order("created_at", desc=True)
        .execute()
    ).data or []
    print(f"WIs: {len(wis)}")

    for i, wi in enumerate(wis, 1):
        wi_id = wi["item_id"]
        try:
            payload = await fetch_item_references_payload(sb, wi_id, used_only=True)
        except Exception as exc:  # noqa: BLE001
            FAILS.append({"kind": "payload_error", "wi": wi_id, "err": str(exc)[:200]})
            continue
        cards = {int(c["n"]): c for c in payload}
        lines = {ln.n: ln for ln in resolve_used_sources(sb, wi_id)}
        STATS["wis"] += 1
        STATS["cards"] += len(cards)
        STATS["lines"] += len(lines)
        wi_chars = sum(len(l.text) for l in lines.values())
        ROWS.append({"wi": wi_id, "seq": wi.get("wi_seq"), "title": wi.get("title"),
                     "refs": len(cards), "chars": wi_chars})

        for n in sorted(set(cards) & set(lines)):
            card, line = cards[n], lines[n]
            dom = card.get("domain")
            STATS[f"dom_{dom}"] += 1
            text = line.text

            if text == "(مصدر غير متوفر)":
                STATS["stub_lines"] += 1
                FAILS.append({"kind": "stub_beside_card", "wi": wi_id, "n": n,
                              "domain": dom, "ref_id": card.get("ref_id"),
                              "card_title": short(card.get("title", ""), 60),
                              "has_source": card.get("has_source")})
                continue

            if dom == "regulations":
                chip = (card.get("doc_type") or "").strip()
                if chip:
                    STATS["reg_with_chip"] += 1
                    if chip != "نظام":
                        STATS["reg_chip_non_default"] += 1
                    if chip in text:
                        STATS["reg_chip_present"] += 1
                    else:
                        FAILS.append({"kind": "missing_chip", "wi": wi_id, "n": n,
                                      "chip": chip, "line": short(text, 120)})
                for key, fk in (("regulation_title", "missing_reg_title"),
                                ("title", "missing_chunk_title")):
                    v = norm(card.get(key) or "")
                    if v and v[:50] not in text:
                        FAILS.append({"kind": fk, "wi": wi_id, "n": n,
                                      "card": short(v, 90), "line": short(text, 140)})

            elif dom == "cases":
                ctitle = norm(card.get("title") or "")
                if ctitle:
                    STATS["case_cards"] += 1
                    if ctitle[:70] in text:
                        STATS["case_title_present"] += 1
                    else:
                        FAILS.append({"kind": "missing_case_title", "wi": wi_id,
                                      "n": n, "card": short(ctitle, 120),
                                      "line": short(text, 160)})
                    if ctitle.startswith("حكم "):
                        STATS["case_title_court_fallback"] += 1
                body = text.split(" — ", 1)[1] if " — " in text else ""
                snip = norm(card.get("snippet") or "")
                if len(body) > _SNIPPET_MAX_CHARS:
                    FAILS.append({"kind": "case_body_over_cap", "wi": wi_id, "n": n,
                                  "chars": len(body)})
                else:
                    STATS["case_body_within_cap"] += 1
                if snip and body:
                    if body[:60] != snip[:60]:
                        STATS["case_snippet_head_diverges"] += 1
                    if len(snip) > len(body):
                        STATS["case_loses_card_tail"] += 1
                        STATS["case_tail_chars_lost"] += len(snip) - len(body)
                    if len(body) > len(snip):
                        STATS["case_exceeds_card"] += 1
                # the referenced-regulations tail the agent shell cannot build
                if snip and len(snip) < _SNIPPET_MAX_CHARS - 5 and (
                    "اسم النظام:" in snip or "— المادة" in snip
                ):
                    STATS["case_card_has_refregs_tail"] += 1
                    if "اسم النظام:" not in body and "— المادة" not in body:
                        FAILS.append({"kind": "missing_refregs_tail", "wi": wi_id,
                                      "n": n, "card_tail": short(snip[len(body):], 160)})

            elif dom == "circulars":
                for key, fk in (("title", "missing_circ_title"),
                                ("regulation_title", "missing_circ_entity")):
                    v = norm(card.get(key) or "")
                    if v and v[:40] not in text:
                        FAILS.append({"kind": fk, "wi": wi_id, "n": n,
                                      "card": short(v, 80), "line": short(text, 120)})

            elif dom == "compliance":
                v = norm(card.get("title") or "")
                if v and v[:50] not in text:
                    FAILS.append({"kind": "missing_service_name", "wi": wi_id, "n": n,
                                  "card": short(v, 90), "line": short(text, 120)})

        if i % 25 == 0:
            print(f"  … {i}/{len(wis)} WIs")

    hr("STATS")
    print(json.dumps(dict(STATS), ensure_ascii=False, indent=2))
    hr("FAILURE KINDS")
    print(json.dumps(dict(Counter(f["kind"] for f in FAILS)), ensure_ascii=False, indent=2))
    ROWS.sort(key=lambda r: -r["chars"])
    hr("HEAVIEST CANDIDATE LISTS (chars the searcher pays to identify)")
    for r in ROWS[:8]:
        print(f"  {r['chars']:>6}  refs={r['refs']:>2}  {short(r['title'] or '', 60)}")
    with open("agents/simple_search/eval/case_c_parity_sweep_dump.json", "w",
              encoding="utf-8") as fh:
        json.dump({"stats": dict(STATS), "fails": FAILS, "rows": ROWS},
                  fh, ensure_ascii=False, indent=2)
    print("\ndump → agents/simple_search/eval/case_c_parity_sweep_dump.json")


if __name__ == "__main__":
    asyncio.run(main())
