"""Wave 4 — case aggregator payload (`cases.summary` + court metadata).

Plan: `.claude/plans/case_topics_loop.md` §8 (decisions D2, D5).

Covers the three layers the payload crosses:

    cases row  --unfold_ura-->  RerankedCaseResult
               --case_adapter-->  CaseURAResult
               --for_aggregator-->  AggregatorItem
               --preprocessor-->  rendered synthesis text

The end-to-end tests below are the regression guard for the `supreme`
coercion bug: `unfold_ura` used to collapse `court_level` to two values,
relabelling all 125 supreme-court rulings as `first_instance`.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from agents.deep_search_v4.aggregator.preprocessor import (
    render_aggregator_content,
)
from agents.deep_search_v4.case_search.models import (
    FusedCandidate,
    RerankerQueryResult as CaseRQR,
)
from agents.deep_search_v4.case_search.unfold_ura import (
    AGGREGATOR_CASE_FIELDS,
    CASE_COURT_LEVELS,
    MAX_AGGREGATOR_CONTENT_CHARS,
    _build_reranked_case_result,
    assemble_kept_cases,
    normalize_court_level,
)
from agents.deep_search_v4.ura.case_adapter import case_to_rqr
from agents.deep_search_v4.ura.schema import AggregatorItem, CaseURAResult

SUMMARY_MD = (
    "## الملخص\n"
    "- نزاع على استرداد جزء من عمولة سمسرة عقارية.\n"
    "## المنطوق\n"
    "- رفض الدعوى.\n"
)


# ---------------------------------------------------------------------------
# Fixtures / stubs
# ---------------------------------------------------------------------------


def make_case_row(**overrides) -> dict:
    row = {
        "id": "11111111-1111-1111-1111-111111111111",
        "case_ref": "case_00042",
        "court": "المحكمة التجارية",
        "city": "الرياض",
        "court_level": "first_instance",
        "case_number": "1234/5",
        "judgment_number": "9876",
        "date_hijri": "1445/03/12",
        "date_gregorian": "2023-09-27",
        "details_url": "https://laws.example.sa/cases/42",
        "summary": SUMMARY_MD,
        "short_summary": "ملخص قصير جدًا.",
        "legal_domains": ["المعاملات التجارية"],
        "referenced_regulations": [{"title": "نظام المحاكم التجارية", "article": "20"}],
        "appeal_result": None,
    }
    row.update(overrides)
    return row


class _StubTable:
    def __init__(self, client: "_StubSupabase") -> None:
        self._client = client
        self._ids: list[str] = []

    def select(self, cols: str) -> "_StubTable":
        self._client.selected_cols = cols
        return self

    def in_(self, col: str, ids) -> "_StubTable":
        assert col == "id"
        self._ids = [str(i) for i in ids]
        return self

    def execute(self):
        rows = [r for r in self._client.rows if str(r.get("id")) in self._ids]
        return SimpleNamespace(data=rows)


class _StubSupabase:
    """Minimal stand-in for the sync Supabase client used by fetch_full_cases."""

    def __init__(self, rows: list[dict]) -> None:
        self.rows = rows
        self.selected_cols: str | None = None

    def table(self, name: str) -> _StubTable:
        assert name == "cases"
        return _StubTable(self)


def fused(case_id: str) -> FusedCandidate:
    return FusedCandidate(
        case_id=case_id,
        fused_score=0.9,
        channel_ranks={"principle": 1},
        channel_scores={"principle": 0.81},
        row={},
    )


def assemble_one(row: dict, *, relevance: str = "high"):
    """Run the real `assemble_kept_cases` against a stub DB, return one result."""
    supabase = _StubSupabase([row])
    results = asyncio.run(
        assemble_kept_cases(
            supabase,
            kept_decisions=[
                {"position": 1, "relevance": relevance, "reasoning": "سبب الإبقاء"}
            ],
            fused_bucket=[fused(row["id"])],
        )
    )
    assert len(results) == 1
    return results[0], supabase


def to_aggregator_item(reranked, n: int = 1) -> AggregatorItem:
    """RerankedCaseResult -> CaseURAResult -> AggregatorItem (real adapters)."""
    rqrs = case_to_rqr(
        [
            CaseRQR(
                query="سؤال فرعي",
                rationale="",
                sufficient=True,
                results=[reranked],
                dropped_count=0,
                summary_note="",
            )
        ]
    )
    ura_results = rqrs[0].results
    assert len(ura_results) == 1
    ura = ura_results[0]
    assert isinstance(ura, CaseURAResult)
    return ura.for_aggregator(n=n)


# ---------------------------------------------------------------------------
# A. unfold_ura — field set + cap
# ---------------------------------------------------------------------------


def test_aggregator_fields_select_summary_not_content():
    """D2 is about which field becomes the PAYLOAD, not which are selected.

    `content` IS selected — as the last rung of the fallback chain for the 18
    cases with neither `summary` nor `short_summary` (17 of which do have
    content). Asserting its absence from the SELECT would forbid that rescue
    and ship those cases as empty citable references, so the guarantee is
    asserted behaviourally instead: when a summary exists, content is not used.
    """
    assert "summary" in AGGREGATOR_CASE_FIELDS
    # short_summary + content are carried only as the NULL-summary fallbacks.
    assert "short_summary" in AGGREGATOR_CASE_FIELDS
    assert "content" in AGGREGATOR_CASE_FIELDS

    # The actual D2 guarantee: summary wins whenever it is present.
    row = make_case_row(summary="الملخص المهيكل", content="نص الحكم الكامل")
    reranked, _ = assemble_one(row)
    assert reranked.content == "الملخص المهيكل"
    assert "نص الحكم الكامل" not in reranked.content


def test_content_rescues_cases_with_no_summary_at_all():
    """The 18-case carve-out: neither summary field, but content exists.

    These rows are all inside the 9,861-case dark set the retarget makes
    reachable, so this path fires for the first time after Wave 1.
    """
    row = make_case_row(summary=None, short_summary=None, content="نص الحكم الكامل")
    reranked, _ = assemble_one(row)
    assert reranked.content == "نص الحكم الكامل"


def test_no_payload_at_all_yields_empty_string_not_none():
    row = make_case_row(summary=None, short_summary=None, content=None)
    reranked, _ = assemble_one(row)
    assert reranked.content == ""
    assert "None" not in reranked.content


def test_aggregator_fields_keep_the_metadata_the_plan_pins():
    for field in (
        "court",
        "city",
        "court_level",
        "case_number",
        "judgment_number",
        "date_hijri",
        "details_url",
        "legal_domains",
        "referenced_regulations",
        "appeal_result",
    ):
        assert field in AGGREGATOR_CASE_FIELDS, field


def test_content_cap_is_6000():
    assert MAX_AGGREGATOR_CONTENT_CHARS == 6_000


def test_summary_is_clipped_at_the_cap():
    row = make_case_row(summary="ن" * 10_000)
    reranked, _ = assemble_one(row)
    assert reranked.content.endswith("...")
    assert len(reranked.content) == MAX_AGGREGATOR_CONTENT_CHARS + 3


def test_summary_under_cap_is_passed_through_verbatim():
    reranked, _ = assemble_one(make_case_row())
    assert reranked.content == SUMMARY_MD.strip()


def test_select_statement_asks_the_db_for_summary_not_content():
    _, supabase = assemble_one(make_case_row())
    assert supabase.selected_cols is not None
    cols = supabase.selected_cols.split(",")
    assert "summary" in cols
    # `content` is also selected, but only as the last fallback rung for the 18
    # cases with neither summary field — see
    # test_content_rescues_cases_with_no_summary_at_all. The D2 guarantee is
    # about which field becomes the payload, not which columns are fetched.
    assert "content" in cols


# ---------------------------------------------------------------------------
# B. unfold_ura — NULL summary handling (18 of 30,531 rows)
# ---------------------------------------------------------------------------


def test_null_summary_falls_back_to_short_summary():
    reranked, _ = assemble_one(make_case_row(summary=None))
    assert reranked.content == "ملخص قصير جدًا."


def test_null_summary_and_null_short_summary_yield_empty_string():
    reranked, _ = assemble_one(make_case_row(summary=None, short_summary=None))
    assert reranked.content == ""


def test_summary_none_never_produces_the_literal_none_anywhere():
    """A NULL summary must not stringify into the synthesis prompt."""
    row = make_case_row(summary=None, short_summary=None, appeal_result=None)
    reranked, _ = assemble_one(row)
    assert "None" not in reranked.content
    assert "None" not in reranked.title

    item = to_aggregator_item(reranked)
    assert "None" not in item.case_content
    assert "None" not in item.court
    assert "None" not in item.court_level

    rendered = render_aggregator_content(item)
    assert "None" not in rendered
    # The court header + referenced_regulations still render; only the
    # summary body is missing — no empty block, no "None" placeholder.
    assert rendered.splitlines()[0] == "المحكمة: المحكمة التجارية (ابتدائي)"
    assert rendered == (
        "المحكمة: المحكمة التجارية (ابتدائي)\n\nنظام المحاكم التجارية — المادة 20"
    )


def test_missing_summary_key_entirely_is_tolerated():
    row = make_case_row()
    row.pop("summary")
    row.pop("short_summary")
    reranked, _ = assemble_one(row)
    assert reranked.content == ""


# ---------------------------------------------------------------------------
# C. unfold_ura — three-value court_level passthrough (the fixed bug)
# ---------------------------------------------------------------------------


def test_court_levels_constant_has_three_values():
    assert CASE_COURT_LEVELS == ("first_instance", "appeal", "supreme")


@pytest.mark.parametrize("level", ["first_instance", "appeal", "supreme"])
def test_normalize_court_level_passthrough(level):
    assert normalize_court_level(level) == level


@pytest.mark.parametrize("raw", [None, "", "   ", "cassation", "Appeal", 0])
def test_normalize_court_level_degrades_unknown_to_first_instance(raw):
    assert normalize_court_level(raw) == "first_instance"


def test_supreme_survives_unfold_ura():
    reranked, _ = assemble_one(make_case_row(court_level="supreme"))
    assert reranked.court_level == "supreme"


def test_appeal_still_survives_unfold_ura():
    reranked, _ = assemble_one(make_case_row(court_level="appeal"))
    assert reranked.court_level == "appeal"


def test_build_reranked_case_result_does_not_relabel_supreme():
    """Direct guard on the line that used to collapse supreme -> first_instance."""
    reranked = _build_reranked_case_result(
        make_case_row(court_level="supreme"),
        channel_ranks={"principle": 1},
        fused_score=0.5,
        relevance="high",
        reasoning="",
    )
    assert reranked.court_level == "supreme"


# ---------------------------------------------------------------------------
# D. End-to-end: DB row -> rendered aggregator content
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "level,label",
    [
        ("first_instance", "ابتدائي"),
        ("appeal", "استئناف"),
        ("supreme", "عليا"),
    ],
)
def test_court_level_reaches_rendered_content_end_to_end(level, label):
    reranked, _ = assemble_one(make_case_row(court_level=level))
    rendered = render_aggregator_content(to_aggregator_item(reranked))
    assert rendered.startswith(f"المحكمة: المحكمة التجارية ({label})")


def test_supreme_case_is_not_rendered_as_first_instance():
    """THE regression guard for the coercion bug (plan §8.2)."""
    reranked, _ = assemble_one(make_case_row(court_level="supreme"))
    rendered = render_aggregator_content(to_aggregator_item(reranked))
    assert "عليا" in rendered
    assert "ابتدائي" not in rendered
    assert "استئناف" not in rendered


def test_end_to_end_payload_is_the_summary_and_carries_court_metadata():
    reranked, _ = assemble_one(make_case_row(court_level="appeal"))
    item = to_aggregator_item(reranked)
    assert item.court == "المحكمة التجارية"
    assert item.court_level == "appeal"
    assert item.case_content == SUMMARY_MD.strip()

    rendered = render_aggregator_content(item)
    assert "## الملخص" in rendered
    # referenced_regulations still render after the body.
    assert rendered.index("## الملخص") < rendered.index("نظام المحاكم التجارية")


def test_unknown_db_court_level_renders_without_a_parenthetical():
    reranked, _ = assemble_one(make_case_row(court_level="cassation"))
    item = to_aggregator_item(reranked)
    # unfold_ura already normalised it away.
    assert item.court_level == "first_instance"
    rendered = render_aggregator_content(item)
    assert rendered.startswith("المحكمة: المحكمة التجارية (ابتدائي)")


# ---------------------------------------------------------------------------
# D2. The resolver-telemetry appendix must never reach a user or an LLM
# (16,505 live `cases.summary` rows end in «## المراجع النظامية المحلولة» —
#  internal reg/chunk ids + match scores written by the ingestion pipeline).
# ---------------------------------------------------------------------------

LEAKY_SUMMARY = (
    SUMMARY_MD
    + "\n## المراجع النظامية المحلولة\n"
    "- **نظام المحاكم التجارية** — المادة 16 → `17642_reg_003` "
    "(chunks: 17642_reg_003_article_16) [confidence: 0.9016]"
)

# The prod shape of one referenced_regulations entry (verified live).
RESOLVER_REF = {
    "bm25_score": 1.0,
    "confidence": 0.9016,
    "الرقم": "16",
    "match_method": "combined_auto",
    "vector_score": 0.7539,
    "النظام": "نظام المحاكم التجارية",
    "matched_title": "نظام المحاكم التجارية",
    "regulation_id": "17642_reg_003",
    "combined_score": 0.9016,
    "target_chunk_ids": ["17642_reg_003_article_16"],
    "reference_content": "اسم النظام: نظام المحاكم التجارية\nالمادة: 16\nتختص المحكمة بالنظر في الآتي.",
}


def test_strip_resolved_refs_section_drops_the_trailing_appendix():
    from agents.deep_search_v4.shared.case_summary import strip_resolved_refs_section

    out = strip_resolved_refs_section(LEAKY_SUMMARY)
    assert "المراجع النظامية المحلولة" not in out
    assert "17642_reg_003" not in out
    assert "confidence" not in out
    # The real sections survive untouched.
    assert "## الملخص" in out
    assert "## المنطوق" in out


def test_strip_resolved_refs_section_keeps_following_sections():
    from agents.deep_search_v4.shared.case_summary import strip_resolved_refs_section

    text = (
        "## الوقائع\nنص.\n\n## المراجع النظامية المحلولة\n- سطر داخلي\n\n"
        "## المنطوق\nرفض الدعوى."
    )
    out = strip_resolved_refs_section(text)
    assert "سطر داخلي" not in out
    assert "## المنطوق\nرفض الدعوى." in out


def test_strip_resolved_refs_section_is_noop_without_the_block():
    from agents.deep_search_v4.shared.case_summary import strip_resolved_refs_section

    assert strip_resolved_refs_section(SUMMARY_MD) == SUMMARY_MD
    assert strip_resolved_refs_section("") == ""


def test_resolve_summary_strips_the_appendix_before_the_aggregator():
    reranked, _ = assemble_one(make_case_row(summary=LEAKY_SUMMARY))
    assert "المراجع النظامية المحلولة" not in reranked.content
    assert "17642_reg_003" not in reranked.content
    assert reranked.content.startswith("## الملخص")


def test_rendered_referenced_regulations_carry_no_resolver_telemetry():
    """The prod dict shape: only النظام + المادة + the article text render."""
    reranked, _ = assemble_one(
        make_case_row(referenced_regulations=[RESOLVER_REF])
    )
    rendered = render_aggregator_content(to_aggregator_item(reranked))
    assert "نظام المحاكم التجارية — المادة 16" in rendered
    assert "تختص المحكمة بالنظر في الآتي." in rendered
    for leaked in ("17642_reg_003", "combined_auto", "0.9016", "0.7539", "1.0"):
        assert leaked not in rendered, leaked


def test_old_persisted_case_content_is_stripped_at_render_time():
    """Artifacts persisted before the publish-time strip still render clean."""
    item = AggregatorItem(
        ref_id="case:x",
        n=1,
        domain="cases",
        relevance="high",
        case_content=LEAKY_SUMMARY,
    )
    rendered = render_aggregator_content(item)
    assert "المراجع النظامية المحلولة" not in rendered
    assert "17642_reg_003" not in rendered
    assert "## الملخص" in rendered


def test_case_source_view_popup_never_shows_the_appendix():
    """THE user-visible surface: the case preview popup renders `cases.summary`."""
    from agents.deep_search_v4.source_viewer import _build_case_view

    class _CaseTable:
        def __init__(self, row):
            self._row = row

        def select(self, cols):
            return self

        def eq(self, col, val):
            return self

        def maybe_single(self):
            return self

        def execute(self):
            return SimpleNamespace(data=self._row)

    class _PopupStub:
        def __init__(self, row):
            self._row = row

        def table(self, name):
            assert name == "cases"
            return _CaseTable(self._row)

    row = make_case_row(summary=LEAKY_SUMMARY)
    ura = CaseURAResult(
        ref_id="case:case_00042", source_type="case", relevance="high"
    )
    view = asyncio.run(_build_case_view(_PopupStub(row), ura))
    assert "المراجع النظامية المحلولة" not in view.summary
    assert "17642_reg_003" not in view.summary
    assert "## الملخص" in view.summary

    # Stored URAs from before the publish-time strip: case_content still leaky.
    ura_old = CaseURAResult(
        ref_id="case:case_00042",
        source_type="case",
        relevance="high",
        case_content=LEAKY_SUMMARY,
    )
    view = asyncio.run(_build_case_view(_PopupStub(None), ura_old))
    assert "المراجع النظامية المحلولة" not in view.summary
    assert "17642_reg_003" not in view.summary


def test_for_reference_drops_internal_keys_and_gates_reference_content():
    ura = CaseURAResult(
        ref_id="case:case_00042",
        source_type="case",
        relevance="high",
        referenced_regulations=[
            {**RESOLVER_REF, "reference_content": "حكم " * 4000}
        ],
    )
    out = ura.for_reference().referenced_regulations[0]
    assert out["النظام"] == "نظام المحاكم التجارية"
    assert out["الرقم"] == "16"
    for key in (
        "regulation_id",
        "target_chunk_ids",
        "confidence",
        "bm25_score",
        "vector_score",
        "combined_score",
        "match_method",
    ):
        assert key not in out, key
    from agents.deep_search_v4.ura.schema import CROSS_REF_REFERENCE_FREE_CHARS

    assert len(out["reference_content"]) <= CROSS_REF_REFERENCE_FREE_CHARS + 2


# ---------------------------------------------------------------------------
# E. AggregatorItem / CaseURAResult projections
# ---------------------------------------------------------------------------


def test_aggregator_item_court_fields_default_to_empty_strings():
    item = AggregatorItem(ref_id="case:x", n=1, domain="cases", relevance="medium")
    assert item.court == ""
    assert item.court_level == ""


def test_for_aggregator_maps_none_court_fields_to_empty_strings():
    ura = CaseURAResult(
        ref_id="case:case_1",
        source_type="case",
        relevance="high",
        case_content=SUMMARY_MD,
        court=None,
        court_level=None,
    )
    item = ura.for_aggregator(n=3)
    assert item.court == ""
    assert item.court_level == ""
    assert item.n == 3
    assert "None" not in render_aggregator_content(item)


def test_for_reference_is_unchanged_by_wave_4():
    """Citations must keep db_id=case_ref, db_uuid=cases.id, details_url."""
    reranked, _ = assemble_one(make_case_row(court_level="supreme"))
    assert reranked.db_id == "case_00042"
    assert reranked.db_uuid == "11111111-1111-1111-1111-111111111111"

    ura = CaseURAResult(
        ref_id=f"case:{reranked.db_id}",
        source_type="case",
        relevance=reranked.relevance,
        case_number=reranked.case_number,
        case_content=reranked.content,
        judgment_number=reranked.judgment_number,
        court=reranked.court,
        city=reranked.city,
        court_level=reranked.court_level,
        details_url="https://laws.example.sa/cases/42",
        entity_name="وزارة العدل",
        referenced_regulations=reranked.referenced_regulations,
    )
    view = ura.for_reference()
    assert view.ref_id == "case:case_00042"
    assert view.details_url == "https://laws.example.sa/cases/42"
    assert view.case_number == "1234/5"
    assert view.judgment_number == "9876"
    assert view.court == "المحكمة التجارية"
    assert view.city == "الرياض"
    assert view.entity_name == "وزارة العدل"
    # The reference view carries no court_level / content — unchanged contract.
    assert not hasattr(view, "court_level")
    assert not hasattr(view, "case_content")


# ---------------------------------------------------------------------------
# E2. Case إحالات reach the citation panel
#
# A ruling's referenced_regulations are projected onto the SAME ``CrossRef``
# shape the reg domain uses, so the panel renders one list for both domains.
# ---------------------------------------------------------------------------


def _case_reference(**ura_kwargs):
    from agents.deep_search_v4.aggregator.preprocessor import _reference_from_ura

    ura = CaseURAResult(
        ref_id="case:case_00042",
        source_type="case",
        relevance="high",
        **ura_kwargs,
    )
    return _reference_from_ura(1, ura)


def test_case_reference_carries_its_citations_as_cross_refs():
    ref = _case_reference(referenced_regulations=[RESOLVER_REF])
    assert ref.domain == "cases"
    assert len(ref.cross_refs) == 1
    cr = ref.cross_refs[0]
    assert cr.target_reg_title == "نظام المحاكم التجارية"
    assert cr.target_number == 16
    assert cr.target_type == "madda"
    assert "تختص المحكمة بالنظر في الآتي." in cr.content


def test_case_cross_refs_carry_no_resolver_telemetry():
    """The panel is a client surface — internal ids must not survive the hop."""
    ref = _case_reference(referenced_regulations=[RESOLVER_REF])
    blob = ref.model_dump_json()
    for leaked in ("17642_reg_003", "combined_auto", "0.9016", "0.7539"):
        assert leaked not in blob, leaked


def test_case_cross_ref_bodies_are_cut_to_the_public_window():
    """`for_reference()` gates the body BEFORE the CrossRef projection sees it."""
    from agents.deep_search_v4.ura.schema import CROSS_REF_REFERENCE_FREE_CHARS

    ref = _case_reference(
        referenced_regulations=[{**RESOLVER_REF, "reference_content": "حكم " * 4000}]
    )
    assert len(ref.cross_refs[0].content) <= CROSS_REF_REFERENCE_FREE_CHARS + 2


def test_non_numeric_article_labels_survive_in_the_title():
    """«الثانية عشرة» is a real مادة label — an int() failure must not drop it."""
    ref = _case_reference(
        referenced_regulations=[
            {"النظام": "نظام العمل", "الرقم": "الثانية عشرة"},
        ]
    )
    cr = ref.cross_refs[0]
    assert cr.target_number is None
    assert "نظام العمل" in cr.target_reg_title
    assert "الثانية عشرة" in cr.target_reg_title


def test_unlabelled_entries_are_dropped_not_rendered_blank():
    ref = _case_reference(
        referenced_regulations=[{"reference_content": "نص بلا عنوان"}, RESOLVER_REF]
    )
    assert len(ref.cross_refs) == 1
    assert ref.cross_refs[0].target_reg_title == "نظام المحاكم التجارية"


def test_a_case_with_no_citations_has_an_empty_cross_ref_list():
    assert _case_reference().cross_refs == []


# ---------------------------------------------------------------------------
# F. assemble_kept_cases bookkeeping (unchanged behaviour, guarded)
# ---------------------------------------------------------------------------


def test_out_of_range_position_is_skipped():
    row = make_case_row()
    supabase = _StubSupabase([row])
    results = asyncio.run(
        assemble_kept_cases(
            supabase,
            kept_decisions=[{"position": 7, "relevance": "high", "reasoning": ""}],
            fused_bucket=[fused(row["id"])],
        )
    )
    assert results == []


def test_missing_db_row_is_skipped():
    row = make_case_row()
    supabase = _StubSupabase([])  # DB returns nothing
    results = asyncio.run(
        assemble_kept_cases(
            supabase,
            kept_decisions=[{"position": 1, "relevance": "high", "reasoning": ""}],
            fused_bucket=[fused(row["id"])],
        )
    )
    assert results == []


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
