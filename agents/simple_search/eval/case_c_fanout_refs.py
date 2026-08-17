"""Case-C downstream: does the identity the searcher hands on survive?

Two deterministic checks on the objects ``collect_case_c_candidates`` actually
produces from real WI ref rows — no LLM, no writes:

1. **D5 grouping.** N chunks of ONE نظام must reach ONE synthesizer
   (``runner.document_key`` → ``group_documents``). Case C resolves a chunk ref
   to ``chunk_id`` only, so this measures whether the parent regulation is still
   known at grouping time.
2. **The published reference.** ``runner.build_references`` titles the new card
   from ``obj.title`` — which case C fills with the 500-char preview LINE. This
   prints exactly what a user would see on the المراجع card of a Case-C answer.
"""
from __future__ import annotations

import json
from collections import Counter

from case_c_common import USER_ID, hr, service_client, short  # noqa: E402

from agents.simple_search.models import UnfoldResult  # noqa: E402
from agents.simple_search.runner import (  # noqa: E402
    build_references,
    document_key,
    group_documents,
)
from agents.simple_search.searcher import collect_case_c_candidates  # noqa: E402

WI_IDENT = "24b01fd8-4eae-4917-a21f-1b20f5e75f9b"
WI_CASES = "91680d79-b236-411b-a7d3-ef5f5c15453f"

# The three refs the searcher selected together for «اللائحة التنفيذية … الفصل
# الثامن» (case_c_selection a3). All three are chunks of ONE regulation
# (chunks_v2.regulation_id = cf14f315-e674-4b91-935b-cace12f39119).
A3_CHUNKS = {
    "f015b1f0-bd05-5cfc-899d-2230fa3f37d5",
    "d9cd8236-479f-5484-8f75-eb0db06abf58",
    "5626b7fc-0ba2-531a-b9fe-dff84bb51b06",
}


def main() -> None:
    sb = service_client()
    hr("1 · D5 GROUPING — three chunks of ONE لائحة, selected together")
    cands = collect_case_c_candidates(sb, [WI_IDENT])
    objs = [o for o, _ in cands if o.level == "chunk" and o.chunk_id in A3_CHUNKS]
    print(f"objects: {len(objs)}")
    for o in objs:
        print(f"  chunk_id={o.chunk_id}  regulation_id={o.regulation_id!r}  "
              f"document_key={document_key(o)}")
    groups = group_documents(objs)
    print(f"\ngroups → {len(groups)}  (D5 requires 1: they are one document)")
    print(f"  keys: {[g.key for g in groups]}")
    print(f"  VERDICT: {'PASS' if len(groups) == 1 else 'FAIL — ' + str(len(groups)) + ' synthesizers / WIs / chat replies for one نظام'}")

    hr("2 · THE PUBLISHED REFERENCE — what the new card is titled")
    for wi, want_level in ((WI_CASES, "judgment"), (WI_IDENT, "chunk"),
                           (WI_IDENT, "circular"), (WI_IDENT, "service")):
        pick = next((o for o, _ in collect_case_c_candidates(sb, [wi])
                     if o.level == want_level), None)
        if pick is None:
            continue
        groups = group_documents([pick])
        refs = build_references(groups[0], [UnfoldResult(level=pick.level, text="…")])
        r = refs[0]
        print(f"\n  level={want_level}")
        print(f"    ref.title           ({len(r.title)} chars): {short(r.title, 190)}")
        print(f"    ref.regulation_title({len(r.regulation_title)} chars): "
              f"{short(r.regulation_title, 120)}")
        print(f"    ref.domain={r.domain} source_type={r.source_type} "
              f"ref_id={r.ref_id[:48]} doc_type={r.doc_type!r}")
        flags = []
        if len(r.title) > 120:
            flags.append("title far longer than a card label")
        if r.title.startswith(("قضية:", "تعميم:", "نظام:", "لائحة")):
            flags.append("title carries the manifest CHIP PREFIX")
        if "##" in r.title or "**" in r.title:
            flags.append("title carries markdown from the snippet")
        if r.title == r.regulation_title:
            flags.append("title == regulation_title (card renders it twice)")
        if not r.doc_type:
            flags.append("doc_type EMPTY → card falls back to the «نظام» chip")
        print(f"    FLAGS: {flags or 'none'}")

    hr("3 · IDENTITY FIELDS CARRIED OUT OF CASE C")
    fields = Counter()
    for wi in (WI_CASES, WI_IDENT):
        for o, _ in collect_case_c_candidates(sb, [wi]):
            for f in ("regulation_id", "chunk_id", "article_id", "case_id",
                      "case_ref", "circular_id", "service_id", "doc_type",
                      "subtitle", "source_url"):
                if getattr(o, f):
                    fields[f"{o.level}.{f}"] += 1
            fields[f"{o.level}.__total"] += 1
    print(json.dumps(dict(sorted(fields.items())), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
