"""«اقرأ تاليًا» + «الأنظمة المذكورة» — the related-item strips (Wave B).

Plan: ``.claude/plans/read_next_related_items.md`` §3 (scoring), §5 (backend),
§10 (success criteria). Covers:

    library_service.get_related_next               (the «اقرأ تاليًا» reader)
    library_service._regulation_cited_regulations  («الأنظمة المذكورة» on a نظام)
    library_service._reg_hub_item et al.           (the shared card builders)

No live DB — the row-backed ``FakeSupabase`` from the judgments wing, which
actually applies the filters, the ordering and the ``in.()`` chunking the
service asks for. That matters here more than usual: the two properties most
worth pinning are ORDER (``score desc``) and the PUBLISH FILTER, and a scripted
result queue would assert neither.

WHAT THESE TESTS ARE PROTECTING, in one line each:

  * D5 — the publish filter lives in the READ, not in `related_items`. An
    unpublished target is dropped here and lights up on the next ISR bake with
    no recompute of the graph.
  * D2 — «اقرأ تاليًا» is SAME-TYPE only.
  * D13 — the two strips are disjoint; the citation strip renders first and wins.
  * §3.4 — at most 2 of the 7 may be bonus-only, and that guard is OFF for أحكام.
  * §5.2 — every strip failure is an empty list, never an exception. A missing
    `related_items` table (migration 143 not applied) must not 500 a doc page.
"""
from __future__ import annotations

from typing import Any, Optional

from backend.app.services import library_service as ls
from backend.tests.test_library_judgments import FakeSupabase


# ---------------------------------------------------------------------------
# Fixtures — one نظام corpus with a ranked (published-only) view over it
# ---------------------------------------------------------------------------

SRC = "00000000-0000-0000-0000-00000000000a"


def _reg(n: int) -> dict[str, Any]:
    return {
        "id": f"{n:08d}-1111-1111-1111-111111111111",
        "reg_ref": f"17642_reg_{n:03d}",
        "clean_title": f"نظام رقم {n}",
        "title": f"العنوان الخام {n}",
        "entity_name": "وزارة العدل",
        "status_class": "in_force",
        "doc_type_bucket": "law",
        "summary": f"ملخص النظام رقم {n}.",
        "sectors": ["العقار"],
    }


REGS = [_reg(n) for n in range(1, 9)]
RID = {n: _reg(n)["id"] for n in range(1, 9)}


def _edge(target_id: str, score: float, base: float = 1.5) -> dict[str, Any]:
    return {
        "source_type": "regulation",
        "source_id": SRC,
        "target_type": "regulation",
        "target_id": target_id,
        "score": score,
        "base": base,
        "bonus": max(0.0, score - base),
        "reason": "core_subject_relation" if base else "bonus_only",
    }


def _fake(
    edges: list[dict[str, Any]],
    *,
    published: Optional[list[int]] = None,
    regs: list[dict[str, Any]] = REGS,
    cross_refs: list[dict[str, Any]] = (),
) -> FakeSupabase:
    """Corpus + sidecar + ranked view + the edge store.

    ``published`` is the list of نظام numbers that have a slug. Everything else
    exists in ``regulations_v2`` and is invisible to every strip — which is
    exactly the D5 shape: the graph knows about it, the page does not.
    """
    pub = set(range(1, 9) if published is None else published)
    meta = [
        {"content_type": "regulation", "content_id": RID[n], "slug": f"نظام-{n}"}
        for n in sorted(pub)
    ]
    ranked = [
        dict(r, slug=f"نظام-{n}")
        for n, r in zip(range(1, len(regs) + 1), regs)
        if n in pub
    ]
    return FakeSupabase(
        regulations_v2=list(regs),
        library_regulations_ranked=ranked,
        seo_item_meta=meta,
        related_items=list(edges),
        cross_references_v2=list(cross_refs),
    )


# ---------------------------------------------------------------------------
# get_related_next — order, shape, publish filter
# ---------------------------------------------------------------------------


def test_orders_by_score_desc_and_returns_hub_cards() -> None:
    fake = _fake([_edge(RID[1], 1.7), _edge(RID[2], 4.9), _edge(RID[3], 3.0)])
    out = ls.get_related_next(fake, "regulation", SRC)
    assert [i["slug"] for i in out] == ["نظام-2", "نظام-3", "نظام-1"]
    # …and each entry is the SAME dict the /regulations hub builds.
    assert out[0] == ls._reg_hub_item(dict(REGS[1], slug="نظام-2"))


def test_caps_at_seven() -> None:
    edges = [_edge(RID[n], 5.0 - n * 0.1) for n in range(1, 9)]
    out = ls.get_related_next(_fake(edges), "regulation", SRC)
    assert len(out) == ls.RELATED_NEXT_LIMIT == 7


def test_unpublished_targets_are_dropped_and_the_strip_backfills() -> None:
    """D5 — the publish filter is a read-time join, so an unslugged top
    neighbour costs its own card and NOT the cards behind it."""
    edges = [_edge(RID[n], 9.0 - n) for n in range(1, 9)]
    out = ls.get_related_next(_fake(edges, published=[3, 5, 7]), "regulation", SRC)
    assert [i["slug"] for i in out] == ["نظام-3", "نظام-5", "نظام-7"]


def test_no_edges_is_an_empty_list_not_an_error() -> None:
    assert ls.get_related_next(_fake([]), "regulation", SRC) == []


def test_a_missing_related_items_table_costs_the_strip_not_the_page() -> None:
    """Migration 143 not applied yet → a warning and an empty strip.

    The plan's trap table says to apply 143 BEFORE pushing the backend; this is
    what happens if someone does it the other way round."""

    class Exploding(FakeSupabase):
        def table(self, name: str):
            if name == "related_items":
                raise RuntimeError('relation "related_items" does not exist')
            return super().table(name)

    fake = Exploding(
        regulations_v2=REGS,
        library_regulations_ranked=[dict(r, slug=f"نظام-{n}")
                                    for n, r in enumerate(REGS, start=1)],
        seo_item_meta=[],
    )
    assert ls.get_related_next(fake, "regulation", SRC) == []


def test_unknown_corpus_and_blank_id_yield_nothing() -> None:
    assert ls.get_related_next(_fake([]), "article", SRC) == []
    assert ls.get_related_next(_fake([]), "regulation", "") == []
    assert ls.get_related_next(_fake([]), "regulation", None) == []


def test_same_type_only() -> None:
    """D2 — a cross-type row in the store is not rendered, it is filtered."""
    cross = dict(_edge(RID[2], 9.9), target_type="judgment")
    out = ls.get_related_next(_fake([cross, _edge(RID[1], 1.0)]), "regulation", SRC)
    assert [i["slug"] for i in out] == ["نظام-1"]


def test_a_self_edge_is_never_rendered() -> None:
    out = ls.get_related_next(_fake([_edge(SRC, 9.9), _edge(RID[1], 1.0)]),
                              "regulation", SRC)
    assert [i["slug"] for i in out] == ["نظام-1"]


# ---------------------------------------------------------------------------
# The bonus-only guard (§3.4)
# ---------------------------------------------------------------------------


def test_at_most_two_bonus_only_cards_survive() -> None:
    """A source with one real neighbour must not render it beside six
    "same ministry" coincidences."""
    edges = [_edge(RID[n], 2.0 - n * 0.1, base=0.0) for n in range(1, 8)]
    edges.append(_edge(RID[8], 0.5, base=0.5))
    out = ls.get_related_next(_fake(edges), "regulation", SRC)
    assert len(out) == 3
    assert out[-1]["slug"] == "نظام-8"  # the based edge backfills past the guard


def test_the_guard_is_off_for_judgments() -> None:
    """أحكام have NO base axis at all (§3.3) — every score there is bonus-only by
    construction, so the guard would cap every judgment strip at 2 for no
    reason."""
    cases = [
        {
            "id": f"{n:08d}-2222-2222-2222-222222222222",
            "case_ref": f"c{n}",
            "court": "العليا",
            "court_level": "supreme",
            "city": None,
            "case_number": str(n),
            "judgment_number": None,
            "date_hijri": "1445",
            "date_gregorian": None,
            "legal_domains": ["العقار"],
            "short_summary": f"- نزاع رقم {n}",
            "summary": None,
            "facts": None,
            "ruling": None,
        }
        for n in range(1, 6)
    ]
    fake = FakeSupabase(
        library_judgments_ranked=[dict(c, slug=f"حكم-{i}")
                                  for i, c in enumerate(cases, start=1)],
        seo_item_meta=[
            {"content_type": "judgment", "content_id": c["id"], "slug": f"حكم-{i}"}
            for i, c in enumerate(cases, start=1)
        ],
        related_items=[
            {
                "source_type": "judgment",
                "source_id": SRC,
                "target_type": "judgment",
                "target_id": c["id"],
                "score": 1.0 - i * 0.1,
                "base": 0.0,
                "bonus": 1.0 - i * 0.1,
                "reason": "bonus_only",
            }
            for i, c in enumerate(cases, start=1)
        ],
    )
    out = ls.get_related_next(fake, "judgment", SRC)
    assert len(out) == 5
    assert out[0]["court"] == "العليا"


# ---------------------------------------------------------------------------
# D13 — dedup against «الأنظمة المذكورة»
# ---------------------------------------------------------------------------


def test_excluded_ids_never_appear_in_the_strip() -> None:
    edges = [_edge(RID[n], 9.0 - n) for n in range(1, 5)]
    out = ls.get_related_next(
        _fake(edges), "regulation", SRC, exclude_ids=[RID[1], RID[3]]
    )
    assert [i["slug"] for i in out] == ["نظام-2", "نظام-4"]


def test_a_regulation_page_never_shows_the_same_card_twice() -> None:
    """THE acceptance test for D13, end to end through ``get_regulation_doc``.

    نظام 2 is BOTH the top related neighbour and a cited نظام. The citation strip
    renders first and wins; «اقرأ تاليًا» backfills past it."""
    src_reg = dict(_reg(9), id=SRC)
    regs = [*REGS, src_reg]
    pub_meta = [
        {"content_type": "regulation", "content_id": RID[n], "slug": f"نظام-{n}"}
        for n in range(1, 9)
    ] + [{"content_type": "regulation", "content_id": SRC, "slug": "النظام-المصدر"}]
    ranked = [dict(r, slug=f"نظام-{n}") for n, r in enumerate(REGS, start=1)]
    ranked.append(dict(src_reg, slug="النظام-المصدر"))

    fake = FakeSupabase(
        regulations_v2=regs,
        library_regulations_ranked=ranked,
        seo_item_meta=pub_meta,
        related_items=[_edge(RID[2], 5.0), _edge(RID[4], 4.0)],
        cross_references_v2=[
            {
                "source_type": "reg_chunk",
                "source_regulation_id": SRC,
                "target_regulation_id": RID[2],
            }
        ],
        chunks_v2=[],
        seo_articles=[],
    )
    doc = ls.get_regulation_doc(fake, "النظام-المصدر")
    assert [i["slug"] for i in doc["cited_regulations"]] == ["نظام-2"]
    assert [i["slug"] for i in doc["related_next"]] == ["نظام-4"]
    both = {i["slug"] for i in doc["cited_regulations"]} & {
        i["slug"] for i in doc["related_next"]
    }
    assert both == set()


# ---------------------------------------------------------------------------
# «الأنظمة المذكورة» on a نظام page — _regulation_cited_regulations
# ---------------------------------------------------------------------------


def _xref(target_id: str) -> dict[str, Any]:
    return {
        "source_type": "reg_chunk",
        "source_regulation_id": SRC,
        "target_regulation_id": target_id,
    }


def test_cited_regulations_dedupe_to_one_card_per_regulation() -> None:
    """`cross_references_v2` is at مادة granularity — three refs into one نظام
    are three rows and ONE card (D8)."""
    fake = _fake([], cross_refs=[_xref(RID[2]), _xref(RID[2]), _xref(RID[5])])
    items, ids = ls._regulation_cited_regulations(fake, SRC)
    assert [i["slug"] for i in items] == ["نظام-2", "نظام-5"]
    assert ids == [RID[2], RID[5]]


def test_cited_regulations_drop_unpublished_targets() -> None:
    fake = _fake([], published=[5], cross_refs=[_xref(RID[2]), _xref(RID[5])])
    items, ids = ls._regulation_cited_regulations(fake, SRC)
    assert [i["slug"] for i in items] == ["نظام-5"]
    assert ids == [RID[5]]


def test_cited_regulations_cap_at_seven_and_never_self_cite() -> None:
    refs = [_xref(SRC)] + [_xref(RID[n]) for n in range(1, 9)]
    items, ids = ls._regulation_cited_regulations(_fake([], cross_refs=refs), SRC)
    assert len(items) == ls.REGULATION_CITED_LIMIT == 7
    assert SRC not in ids


def test_cited_regulations_are_empty_when_there_are_none() -> None:
    assert ls._regulation_cited_regulations(_fake([]), SRC) == ([], [])
    assert ls._regulation_cited_regulations(_fake([]), "") == ([], [])


def test_cited_regulations_survive_a_lookup_failure() -> None:
    class Exploding(FakeSupabase):
        def table(self, name: str):
            if name == "cross_references_v2":
                raise RuntimeError("boom")
            return super().table(name)

    fake = Exploding(regulations_v2=REGS, library_regulations_ranked=[],
                     seo_item_meta=[])
    assert ls._regulation_cited_regulations(fake, SRC) == ([], [])
