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
  * §3.4 — the bonus-only cap is ARMED BY THE DATA, not by a corpus list: it
    applies only where the candidate set holds a published `base > 0` row for
    filler to crowd out. An all-bonus-only result (أحكام always; تعاميم and
    خدمات until their base axis ships) is NOT capped at 2 — that cap is what
    left three of the four wings with 2 cards in a 3-in-view scroller.
  * §5.2 + migration 145 — the read orders on `score desc, tiebreak desc` IN THE
    DB, because which 200 of 532 tied rows the over-fetch returns is arbitrary
    unless the order is total. A `tiebreak` column that does not exist yet costs
    the TIEBREAK, not the strip.
  * §5.2 — every strip failure is an empty list, never an exception. A missing
    `related_items` table (migration 143 not applied) must not 500 a doc page.
"""
from __future__ import annotations

from typing import Any, Optional

import pytest

from backend.app.services import library_service as ls
from backend.tests.test_library_judgments import FakeSupabase, _Chain


@pytest.fixture(autouse=True)
def _reset_tiebreak_latch():
    """``_related_tiebreak_state`` is module-level and time-keyed.

    One test proving the pre-145 fallback latches would otherwise silently turn
    the tiebreak off for every test that runs after it in the same process —
    including the ones asserting it IS ordered on."""
    ls._related_tiebreak_state["missing_until"] = 0.0
    yield
    ls._related_tiebreak_state["missing_until"] = 0.0


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


def _edge(
    target_id: str, score: float, base: float = 1.5, tiebreak: float = 0.0
) -> dict[str, Any]:
    return {
        "source_type": "regulation",
        "source_id": SRC,
        "target_type": "regulation",
        "target_id": target_id,
        "score": score,
        "base": base,
        "bonus": max(0.0, score - base),
        # Migration 145 — a normalized [0,1] property of the TARGET, second sort
        # key only. Default 0 so every pre-existing fixture keeps ranking on
        # score alone.
        "tiebreak": tiebreak,
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
# The bonus-only guard (§3.4) — ARMED BY THE DATA, NOT BY A CORPUS LIST
#
# The guard's purpose is «don't let weak filler crowd out strong evidence», so
# its trigger is whether strong evidence CAN appear in this particular result
# set — not which wing the reader happens to be on. The first version keyed off a
# hardcoded `{"judgment"}` exemption and starved every other base-less corpus:
# measured on prod, تعاميم averaged 1.97 cards and خدمات 1.86, and 0 of 375 and
# 0 of 83 sources respectively reached the 4 cards a 3-in-view scroller needs
# before anything overflows. The strip rendered; it could not scroll.
# ---------------------------------------------------------------------------


def test_the_cap_applies_when_a_based_candidate_can_render() -> None:
    """The case the guard exists for: a source with one real neighbour must not
    render it beside six "same ministry" coincidences."""
    edges = [_edge(RID[n], 2.0 - n * 0.1, base=0.0) for n in range(1, 8)]
    edges.append(_edge(RID[8], 0.5, base=0.5))
    out = ls.get_related_next(_fake(edges), "regulation", SRC)
    assert len(out) == 3
    assert out[-1]["slug"] == "نظام-8"  # the based edge backfills past the guard


def test_an_all_bonus_only_result_is_not_capped() -> None:
    """THE تعاميم/خدمات FIX. Nothing in this set carries a base, so there is no
    strong evidence for the filler to crowd out and the cap protects nothing —
    it would just hand a 3-in-view scroller 2 cards and no overflow.

    Same corpus as the test above (أنظمة, which DOES have a base axis): the
    trigger is the candidate set, not the wing."""
    edges = [_edge(RID[n], 2.0 - n * 0.1, base=0.0) for n in range(1, 8)]
    out = ls.get_related_next(_fake(edges), "regulation", SRC)
    assert [i["slug"] for i in out] == [f"نظام-{n}" for n in range(1, 8)]


def test_an_unpublished_based_candidate_does_not_arm_the_guard() -> None:
    """The guard is evaluated AFTER the publish filter (D5). A `base > 0` edge
    whose target has no slug can never render, so it is not evidence worth
    protecting — capping the strip on its behalf starves it for nothing."""
    edges = [_edge(RID[8], 9.0, base=5.0)]  # the only based edge — unpublished
    edges += [_edge(RID[n], 2.0 - n * 0.1, base=0.0) for n in range(1, 6)]
    out = ls.get_related_next(_fake(edges, published=[1, 2, 3, 4, 5]),
                              "regulation", SRC)
    assert [i["slug"] for i in out] == [f"نظام-{n}" for n in range(1, 6)]


def test_the_guard_re_arms_by_itself_when_a_base_axis_lands() -> None:
    """Why this is written as a property and not a list: migration 145 gives
    خدمات a base and Wave E gives تعاميم theirs. Flipping ONE candidate's base
    from 0 must re-arm the cap with no code change and no list to edit."""
    bonus_only = [_edge(RID[n], 2.0 - n * 0.1, base=0.0) for n in range(1, 8)]
    assert len(ls.get_related_next(_fake(bonus_only), "regulation", SRC)) == 7

    with_base = [dict(bonus_only[-1], base=1.0), *bonus_only[:-1]]
    assert len(ls.get_related_next(_fake(with_base), "regulation", SRC)) == 3


def test_the_guard_is_off_for_judgments() -> None:
    """أحكام have NO base axis at all (§3.3) — every score there is bonus-only by
    construction, so a cap would hold every judgment strip at 2 for no reason.

    This is now a CONSEQUENCE of the derived condition rather than an exemption:
    the corpus is not named anywhere in the reader."""
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
# `tiebreak` — the second sort key (migration 145)
#
# Judgment scores are PERFECT ties: 22 courts clear the floor, none holds more
# than one entity, so all 532 candidates in the largest chamber score
# identically. `score desc` alone is not a total order, and with
# `_RELATED_SCAN_LIMIT` over-fetching 200 of those 532 rows the strip's contents
# were arbitrary AND unstable between two runs of the same query.
# ---------------------------------------------------------------------------


class _PreTiebreakChain(_Chain):
    """PostgREST before migration 145: `order=tiebreak.desc` is a 400.

    The failure surfaces at EXECUTE, not at build time — same as the real thing,
    where the column name only reaches Postgres when the request is sent."""

    def order(self, col: str, **kw: Any) -> "_PreTiebreakChain":
        if col == "tiebreak":
            self._wants_tiebreak = True
        return super().order(col, **kw)  # type: ignore[return-value]

    def execute(self):
        if getattr(self, "_wants_tiebreak", False):
            raise RuntimeError(
                'column related_items.tiebreak does not exist (code 42703)'
            )
        return super().execute()


class PreTiebreakSupabase(FakeSupabase):
    """A prod where 143 is applied and 145 is not — the deploy-order hazard."""

    def table(self, name: str) -> _PreTiebreakChain:
        return _PreTiebreakChain(self, name)


def test_ties_are_broken_by_tiebreak_desc() -> None:
    """Identical scores — the whole reason the column exists."""
    edges = [
        _edge(RID[1], 1.0, tiebreak=0.10),
        _edge(RID[2], 1.0, tiebreak=0.90),
        _edge(RID[3], 1.0, tiebreak=0.50),
    ]
    out = ls.get_related_next(_fake(edges), "regulation", SRC)
    assert [i["slug"] for i in out] == ["نظام-2", "نظام-3", "نظام-1"]


def test_tiebreak_never_outranks_score() -> None:
    """It is the SECOND key. A better edge with `tiebreak = 0` still wins."""
    edges = [_edge(RID[1], 4.0, tiebreak=0.0), _edge(RID[2], 1.0, tiebreak=1.0)]
    out = ls.get_related_next(_fake(edges), "regulation", SRC)
    assert [i["slug"] for i in out] == ["نظام-1", "نظام-2"]


def test_both_sort_keys_are_pushed_down_to_the_database() -> None:
    """THE load-bearing assertion, and the one a Python re-sort would fail.

    The reader over-fetches `_RELATED_SCAN_LIMIT` rows and cuts to 7. Sorting the
    fetched window in Python cannot help when the window itself was chosen by an
    arbitrary order — with 532 tied rows, WHICH 200 come back is undefined unless
    the DB order is total. So both keys must be in the `order`, matching the
    index `(source_type, source_id, score desc, tiebreak desc)`."""
    fake = _fake([_edge(RID[1], 1.0, tiebreak=0.5)])
    ls.get_related_next(fake, "regulation", SRC)
    ordered = [(c, d) for t, c, d, _ in fake.orders if t == "related_items"]
    assert ordered == [("score", True), ("tiebreak", True)]


def test_a_missing_tiebreak_column_costs_the_tiebreak_not_the_strip() -> None:
    """143 is live on prod and 145 may not be. A hard dependency on the new
    column would take every strip on all four wings dark on deploy."""
    edges = [_edge(RID[n], 5.0 - n) for n in range(1, 4)]
    fake = PreTiebreakSupabase(
        regulations_v2=list(REGS),
        library_regulations_ranked=[dict(r, slug=f"نظام-{n}")
                                    for n, r in enumerate(REGS, start=1)],
        seo_item_meta=[
            {"content_type": "regulation", "content_id": RID[n], "slug": f"نظام-{n}"}
            for n in range(1, 9)
        ],
        related_items=edges,
    )
    out = ls.get_related_next(fake, "regulation", SRC)
    assert [i["slug"] for i in out] == ["نظام-1", "نظام-2", "نظام-3"]


def test_the_pre_145_fallback_latches_instead_of_probing_every_render() -> None:
    """One failed probe per few minutes, not one per doc page."""
    fake = PreTiebreakSupabase(
        regulations_v2=list(REGS),
        library_regulations_ranked=[dict(r, slug=f"نظام-{n}")
                                    for n, r in enumerate(REGS, start=1)],
        seo_item_meta=[
            {"content_type": "regulation", "content_id": RID[1], "slug": "نظام-1"}
        ],
        related_items=[_edge(RID[1], 1.0)],
    )
    assert ls.get_related_next(fake, "regulation", SRC)
    assert ls._related_tiebreak_state["missing_until"] > 0.0

    # …and while latched, the next read does not even ask for the column.
    later = _fake([_edge(RID[1], 1.0)])
    assert ls.get_related_next(later, "regulation", SRC)
    assert [c for t, c, _, _ in later.orders if t == "related_items"] == ["score"]


def test_a_missing_table_does_not_latch_the_tiebreak_off() -> None:
    """143 not applied is not evidence about 145's column. Latching on it would
    keep the tiebreak off for minutes after the real table came back."""

    class Exploding(FakeSupabase):
        def table(self, name: str):
            if name == "related_items":
                raise RuntimeError('relation "related_items" does not exist')
            return super().table(name)

    fake = Exploding(regulations_v2=REGS, library_regulations_ranked=[],
                     seo_item_meta=[])
    assert ls.get_related_next(fake, "regulation", SRC) == []
    assert ls._related_tiebreak_state["missing_until"] == 0.0


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
