"""Official judicial principles («مبدأ قضائي») through the URA → aggregator path.

Plan ``.claude/plans/official_principles_refs.md`` §3.4–§3.6. Pins:

- both adapters route a kept principle to ONE shell under ONE ref_id
  (``principle:<official_principles.id>``) — D1;
- the merger collapses a principle both executors kept into one result
  (sub-query union across phases, relevance lift, reasoning join) and ranks
  principles between regulations and cases;
- ``enrich_ura`` hydrates both kinds of shell into the same object and drops a
  principle with no rule text;
- the Article 40 pin: present for 18329, absent for anything else, fail-soft;
- the aggregator view (D5), the reference card, the source view, and the
  persisted-URA JSON round trip with both new domains.

The two rows below are ``official_principles`` nos. 1 and 2, copied from the
live table (2026-11-07) — never retyped.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from agents.deep_search_v4.aggregator.models import AggregatorInput, Reference
from agents.deep_search_v4.aggregator.preprocessor import (
    _reference_from_ura,
    attach_source_views,
    build_snippet,
    preprocess_references,
    render_aggregator_content,
)
from agents.deep_search_v4.aggregator.prompts import build_aggregator_user_message
from agents.deep_search_v4.shared.models import RerankerQueryResult as SharedRQR
from agents.deep_search_v4.source_viewer import (
    ArticleFullSourceView,
    PrincipleSourceView,
    build_source_view,
)
from agents.deep_search_v4.ura import enrich, pinned, principles
from agents.deep_search_v4.ura.case_adapter import case_to_rqr
from agents.deep_search_v4.ura.merger import DOMAIN_RANK, build_ura_from_phases
from agents.deep_search_v4.ura.reg_adapter import reg_compliance_to_rqr
from agents.deep_search_v4.ura.schema import (
    ArticleURAResult,
    CaseURAResult,
    PrincipleURAResult,
    RegURAResult,
    UnifiedRetrievalArtifact,
)

# --------------------------------------------------------------------------- #
# Fixtures — live rows (official_principles nos. 1 and 2, entity 18329)
# --------------------------------------------------------------------------- #

P1_ID = "b285660c-eefb-5146-a068-71f08cb8737e"
P2_ID = "64052e1b-17d9-511d-8508-ed401e624d1a"
SOURCE_URL = "https://drive.google.com/file/d/19wu0BYdazRzTB0xjmCkk0cgNu2O5L8qG/view"

ROW_P1 = {
    "id": P1_ID,
    "principle_ref": "18329_principles_0001",
    "entity_ref": "18329",
    "principle_no": 1,
    "chapter_no": 1,
    "chapter_title": "الأصول والقواعد",
    "text": (
        "تعريف المبدأ القضائي هو: القاعدة القضائية العامة الموضوعية والإجرائية "
        "التي تقررها المحكمة العليا"
    ),
    "citations": [
        {
            "body": "المحكمة العليا بهيئتها العامة",
            "printed": "(ك ع ع): (٢/أ)، (٢٩/٨/١٤٣٤)",
            "body_code": "ك ع ع",
            "date_hijri": "29/8/1434",
            "date_valid": True,
            "year_hijri": 1434,
            "decision_no": "2/أ",
        }
    ],
    "issuing_bodies": ["ك ع ع"],
    "issuing_body_names": ["المحكمة العليا بهيئتها العامة"],
    "source": {"pages": [33, 33], "source_url": SOURCE_URL},
}

ROW_P2 = {
    "id": P2_ID,
    "principle_ref": "18329_principles_0002",
    "entity_ref": "18329",
    "principle_no": 2,
    "chapter_no": 1,
    "chapter_title": "الأصول والقواعد",
    "text": "الأصل براءة الذمة حتى يثبت ما يوجب الإدانة.",
    "citations": [
        {
            "body": "الهيئة القضائية العليا",
            "printed": "(هـ ق ع): (٢٥٤)، (١٣/١٠/١٣٩١)",
            "body_code": "هـ ق ع",
            "date_hijri": "13/10/1391",
            "date_valid": True,
            "year_hijri": 1391,
            "decision_no": "254",
        }
    ],
    "issuing_bodies": ["هـ ق ع"],
    "issuing_body_names": ["الهيئة القضائية العليا"],
    "source": {"pages": [33, 33], "source_url": SOURCE_URL},
}

# Article 40 of the objections regulation (articles_v2 / regulations_v2 rows,
# body shortened). The pin must read these, never a hardcoded text.
ART40_ROW = {
    "id": pinned.ARTICLE_40_ID,
    "regulation_id": "b61a5150-e9a1-47a5-acd3-1a02f1ba084d",
    "article_number": "40",
    "content": (
        "##### المادة الأربعون\n\nإذا كان محل الاعتراض مخالفة الحكم لمبدأ قضائي "
        "صادر من المحكمة العليا، أو أخذت به إحدى دوائر المحكمة العليا في قضايا سابقة"
    ),
}
ART40_REG_ROW = {
    "id": "b61a5150-e9a1-47a5-acd3-1a02f1ba084d",
    "clean_title": None,
    "title": "اللائحة التنفيذية لطرق الاعتراض على الأحكام",
    "doc_type_raw": "لائحة تنفيذية",
    "landing_url": "https://laws.moj.gov.sa/ar/legislation/7uUvC6c0BoKLJuwB021doA",
}


# --------------------------------------------------------------------------- #
# A stub sync supabase client: table → rows, filtered on eq / in_
# --------------------------------------------------------------------------- #


class _Resp:
    def __init__(self, data):
        self.data = data


class _Query:
    def __init__(self, client, table):
        self._client = client
        self._table = table
        self._filters: list[tuple[str, str, object]] = []
        self._single = False

    def select(self, *_a, **_kw):
        return self

    def eq(self, col, val):
        self._filters.append(("eq", col, val))
        return self

    def in_(self, col, vals):
        self._filters.append(("in", col, list(vals)))
        return self

    def limit(self, *_a):
        return self

    def maybe_single(self):
        self._single = True
        return self

    def execute(self):
        self._client.calls.append(self._table)
        if self._table in self._client.fail_tables:
            raise RuntimeError(f"boom: {self._table}")
        rows = list(self._client.tables.get(self._table, []))
        for kind, col, val in self._filters:
            if kind == "eq":
                rows = [r for r in rows if r.get(col) == val]
            else:
                rows = [r for r in rows if r.get(col) in val]
        if self._single:
            return _Resp(rows[0] if rows else None)
        return _Resp(rows)


class StubSupabase:
    def __init__(self, tables: dict[str, list[dict]] | None = None, fail_tables=()):
        self.tables = tables or {}
        self.fail_tables = set(fail_tables)
        self.calls: list[str] = []

    def table(self, name):
        return _Query(self, name)


def _db() -> StubSupabase:
    return StubSupabase(
        {
            "official_principles": [ROW_P1, ROW_P2],
            "articles_v2": [ART40_ROW],
            "regulations_v2": [ART40_REG_ROW],
        }
    )


# --------------------------------------------------------------------------- #
# Executor outputs (duck-typed: B/C's model fields are read with getattr)
# --------------------------------------------------------------------------- #


def _reg_rqr(results, query="الوعد الملزم", dropped=None):
    return SimpleNamespace(
        query=query, rationale="", sufficient=True, results=results,
        dropped_count=len(dropped or []), summary_note="", unfold_rounds=0,
        total_unfolds=0, dropped_results=dropped or [],
    )


def _reg_principle(row=ROW_P2, relevance="medium", reasoning="من جهة الأنظمة"):
    return SimpleNamespace(
        source_type="official_principle", title="المبدأ رقم 2 — الأصول والقواعد",
        content=row["text"], relevance=relevance, reasoning=reasoning,
        db_id=row["id"], rrf=0.03,
        source_row={**row, "source_type": "official_principle", "_mode": "flat"},
    )


def _case_principle(pid=P2_ID, relevance="high", reasoning="من جهة الأحكام"):
    return SimpleNamespace(
        candidate_kind="official_principle", principle_id=pid, db_id=pid,
        db_uuid="", source_type="official_principle", title="مبدأ قضائي",
        content=ROW_P2["text"], score=0.71, relevance=relevance,
        reasoning=reasoning,
    )


def _case_ruling(case_ref="REF-1"):
    return SimpleNamespace(
        source_type="case", title="حكم", content="## الملخص\nنزاع", court="محكمة",
        city=None, court_level="appeal", case_number="1", judgment_number=None,
        date_hijri=None, year_gregorian=2020, legal_domains=[],
        referenced_regulations=[], appeal_result=None, score=0.5,
        relevance="medium", reasoning="", db_id=case_ref, db_uuid="uuid-1",
    )


# --------------------------------------------------------------------------- #
# principles.py — the row builder
# --------------------------------------------------------------------------- #


def test_principle_from_row_reads_citations_never_content():
    p = principles.principle_from_row(ROW_P2, relevance="high", reasoning="ر", rrf=0.5)
    assert p.ref_id == f"principle:{P2_ID}"
    assert p.domain == "principles"
    assert p.source_type == "official_principle"
    assert p.principle_no == 2
    assert p.entity_ref == "18329"
    assert p.decisions == [
        {"body": "الهيئة القضائية العليا", "decision_no": "254", "date_hijri": "13/10/1391"}
    ]
    assert p.source_url == SOURCE_URL
    assert p.pages == [33]  # [33, 33] deduped
    # Never the printed Arabic-Indic form.
    assert "١" not in str(p.model_dump())


def test_decision_body_falls_back_to_issuing_body_names_and_digits_go_latin():
    row = {
        **ROW_P1,
        "citations": [
            {"body": "", "body_code": "ك ع ع", "decision_no": "٢/أ", "date_hijri": "٢٩/٨/١٤٣٤"},
            {"body": "", "body_code": "", "decision_no": "", "date_hijri": ""},  # nothing to look up
        ],
    }
    p = principles.principle_from_row(row)
    assert p.decisions == [
        {"body": "المحكمة العليا بهيئتها العامة", "decision_no": "2/أ", "date_hijri": "29/8/1434"}
    ]


def test_titles_and_decision_lines_degrade_part_by_part():
    assert principles.principle_title(2, "الأصول والقواعد") == "المبدأ رقم 2 — الأصول والقواعد"
    assert principles.principle_title(2, "") == "المبدأ رقم 2"
    assert principles.principle_title(None, "") == "مبدأ قضائي"
    two = [
        {"body": "أ", "decision_no": "162/5", "date_hijri": "24/3/1415"},
        {"body": "ب", "decision_no": "", "date_hijri": "1/1/1400"},
    ]
    assert (
        principles.principle_decision_line(two)
        == "القرار 162/5 بتاريخ 24/3/1415هـ · قرار بتاريخ 1/1/1400هـ"
    )
    assert principles.principle_decision_line([]) == ""
    # A corpus with no citations still names its body.
    bare = principles.principle_from_row(
        {"id": "x", "text": "ق", "issuing_body_names": ["المحكمة الإدارية العليا"]}
    )
    assert bare.body_names() == ["المحكمة الإدارية العليا"]


# --------------------------------------------------------------------------- #
# Adapters — both sides land on ONE ref_id
# --------------------------------------------------------------------------- #


def test_reg_adapter_routes_official_principle_to_a_principle_shell():
    dropped = [{"db_id": P1_ID, "title": "م", "source_type": "official_principle"}]
    [rqr] = reg_compliance_to_rqr([_reg_rqr([_reg_principle()], dropped=dropped)])
    [res] = rqr.results
    assert isinstance(res, PrincipleURAResult)
    assert res.ref_id == f"principle:{P2_ID}"
    assert res.text == ROW_P2["text"]
    assert res.decisions[0]["decision_no"] == "254"  # complete from source_row
    assert rqr.kept_forensic == [
        {
            "source_table": "official_principles", "ref_id": P2_ID,
            "title": "المبدأ رقم 2 — الأصول والقواعد", "relevance": "medium",
            "source_type": "official_principle", "reasoning": "من جهة الأنظمة",
        }
    ]
    assert rqr.dropped_forensic[0]["source_table"] == "official_principles"


def test_case_adapter_routes_principle_candidates_to_the_same_ref_id():
    case_rqr = SimpleNamespace(
        query="حجية", rationale="", sufficient=True,
        results=[_case_principle(), _case_ruling()], dropped_count=0,
        summary_note="", unfold_rounds=0, total_unfolds=0, dropped_results=[],
    )
    [rqr] = case_to_rqr([case_rqr])
    principle, ruling = rqr.results
    assert isinstance(principle, PrincipleURAResult)
    assert principle.ref_id == f"principle:{P2_ID}"
    assert principle.text == ROW_P2["text"]
    assert principle.rrf_max == pytest.approx(0.71)
    assert isinstance(ruling, CaseURAResult) and ruling.ref_id == "case:REF-1"
    assert [f["source_table"] for f in rqr.kept_forensic] == ["official_principles", "cases"]
    assert rqr.kept_forensic[0]["ref_id"] == P2_ID


def test_case_adapter_without_candidate_kind_is_still_a_case():
    ruling = _case_ruling()
    assert not hasattr(ruling, "candidate_kind")
    [rqr] = case_to_rqr([SimpleNamespace(
        query="q", rationale="", sufficient=True, results=[ruling], dropped_count=0,
        summary_note="", unfold_rounds=0, total_unfolds=0, dropped_results=[],
    )])
    assert isinstance(rqr.results[0], CaseURAResult)


# --------------------------------------------------------------------------- #
# Merger — dedup across executors, rank
# --------------------------------------------------------------------------- #


def _phases():
    reg = reg_compliance_to_rqr([_reg_rqr([_reg_principle(relevance="medium")])])
    case = case_to_rqr([SimpleNamespace(
        query="حجية", rationale="", sufficient=True,
        results=[_case_principle(relevance="high"), _case_ruling()],
        dropped_count=0, summary_note="", unfold_rounds=0, total_unfolds=0,
        dropped_results=[],
    )])
    return reg, case


def test_merger_collapses_a_double_find_into_one_result():
    reg, case = _phases()
    ura = build_ura_from_phases(reg, case, original_query="q")
    found = [r for r in ura.high_results + ura.medium_results if r.domain == "principles"]
    assert len(found) == 1
    [p] = found
    assert p.appears_in_sub_queries == [0, 1]       # reg sq 0 + case sq 1
    assert p.relevance == "high"                     # lifted from the case side
    assert p.reasoning == "من جهة الأنظمة؛ من جهة الأحكام"
    assert p.rrf_max == pytest.approx(0.71)          # max of 0.03 / 0.71
    assert p in ura.high_results


def test_domain_rank_puts_principles_between_statute_and_rulings():
    assert DOMAIN_RANK["regulations"] > DOMAIN_RANK["principles"] > DOMAIN_RANK["cases"]
    assert DOMAIN_RANK["cases"] > DOMAIN_RANK["circulars"] > DOMAIN_RANK["compliance"]
    reg_chunk = RegURAResult(ref_id="reg:c1", source_type="chunk", relevance="medium")
    reg_rqr = SharedRQR(
        query="q", rationale="", sufficient=True, domain="regulations",
        results=[principles.principle_from_row(ROW_P2), reg_chunk],
    )
    case_rqr = SharedRQR(
        query="q2", rationale="", sufficient=True, domain="cases",
        results=[CaseURAResult(ref_id="case:x", source_type="case", relevance="medium", rrf_max=9.0)],
    )
    ura = build_ura_from_phases([reg_rqr], [case_rqr], original_query="q")
    assert [r.domain for r in ura.medium_results] == ["regulations", "principles", "cases"]


# --------------------------------------------------------------------------- #
# Enrich — hydrate makes both shells identical; empty principles drop
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_enrich_hydrates_a_case_side_shell_to_the_reg_side_one():
    case_shell = principles.principle_from_row({"id": P2_ID, "text": "نص مختصر"},
                                               relevance="high", reasoning="ر")
    reg_shell = principles.principle_from_row(ROW_P2, relevance="high", reasoning="ر")
    assert case_shell.entity_ref == "" and case_shell.decisions == []

    ura = UnifiedRetrievalArtifact(high_results=[case_shell])
    db = _db()
    await enrich.enrich_ura(ura, db)

    [hydrated] = ura.high_results
    assert hydrated.model_dump() == reg_shell.model_dump()
    assert db.calls == ["official_principles"]  # ONE batched read


@pytest.mark.asyncio
async def test_enrich_keeps_the_adapter_view_on_a_failed_read_and_drops_empty_principles():
    with_text = principles.principle_from_row({"id": P2_ID, "text": "نص"})
    empty = principles.principle_from_row({"id": "gone"})
    ura = UnifiedRetrievalArtifact(medium_results=[with_text, empty])
    await enrich.enrich_ura(ura, StubSupabase(fail_tables={"official_principles"}))
    assert [r.ref_id for r in ura.medium_results] == [f"principle:{P2_ID}"]
    assert ura.medium_results[0].text == "نص"


# --------------------------------------------------------------------------- #
# Article 40 pin
# --------------------------------------------------------------------------- #


def _ura_with(*results) -> UnifiedRetrievalArtifact:
    highs = [r for r in results if r.relevance == "high"]
    meds = [r for r in results if r.relevance != "high"]
    return UnifiedRetrievalArtifact(high_results=highs, medium_results=meds)


@pytest.mark.asyncio
async def test_pin_appends_article_40_at_the_end_of_medium():
    p1 = principles.principle_from_row(ROW_P1, relevance="high")
    p1.appears_in_sub_queries = [0, 2]
    p2 = principles.principle_from_row(ROW_P2, relevance="medium")
    p2.appears_in_sub_queries = [3]
    chunk = RegURAResult(ref_id="reg:c1", source_type="chunk", relevance="medium",
                         chunk_content="نص")
    ura = _ura_with(p1, p2, chunk)

    assert await pinned.pin_article_40(_db(), ura) is True

    art = ura.medium_results[-1]
    assert isinstance(art, ArticleURAResult)
    assert art.ref_id == f"article:{pinned.ARTICLE_40_ID}"
    assert art.relevance == "medium"
    assert art.appears_in_sub_queries == [0, 2, 3]
    assert art.content == ART40_ROW["content"]          # read, never hardcoded
    assert art.reg_title == ART40_REG_ROW["title"]       # clean_title is NULL
    assert art.article_number == "40"
    assert art.doc_type == "لائحة تنفيذية"
    assert art.landing_url == ART40_REG_ROW["landing_url"]
    # Never twice.
    assert await pinned.pin_article_40(_db(), ura) is False
    assert sum(isinstance(r, ArticleURAResult) for r in ura.medium_results) == 1


@pytest.mark.asyncio
async def test_no_pin_without_a_18329_principle():
    other = principles.principle_from_row({**ROW_P2, "entity_ref": "17486"})
    for ura in (_ura_with(other), _ura_with(), UnifiedRetrievalArtifact()):
        db = _db()
        assert await pinned.pin_article_40(db, ura) is False
        assert db.calls == []
        assert not any(isinstance(r, ArticleURAResult) for r in ura.medium_results)


@pytest.mark.asyncio
async def test_pin_is_fail_soft():
    ura = _ura_with(principles.principle_from_row(ROW_P2))
    before = ura.model_dump()
    assert await pinned.pin_article_40(StubSupabase(fail_tables={"articles_v2"}), ura) is False
    assert await pinned.pin_article_40(StubSupabase({}), ura) is False  # row gone
    assert ura.model_dump() == before
    assert await pinned.pin_article_40(None, ura) is False  # no client at all


# --------------------------------------------------------------------------- #
# Aggregator view, reference card, snippet, source view
# --------------------------------------------------------------------------- #


def test_aggregator_view_renders_the_full_principle():
    p = principles.principle_from_row(ROW_P2)
    text = render_aggregator_content(p.for_aggregator(n=3))
    assert text == (
        "مبدأ قضائي رسمي — المبدأ رقم 2 (الباب: الأصول والقواعد)\n\n"
        "الأصل براءة الذمة حتى يثبت ما يوجب الإدانة.\n\n"
        "القرار: 254 بتاريخ 13/10/1391هـ — الهيئة القضائية العليا"
    )
    # A corpus without number / chapter / citations still renders.
    bare = principles.principle_from_row(
        {"id": "x", "text": "ق", "issuing_body_names": ["المحكمة الإدارية العليا"]}
    )
    assert render_aggregator_content(bare.for_aggregator()) == (
        "مبدأ قضائي رسمي\n\nق\n\nالجهة المصدرة: المحكمة الإدارية العليا"
    )


def test_principle_reference_card():
    p = principles.principle_from_row(ROW_P1, relevance="high")
    ref = _reference_from_ura(4, p)
    assert ref.n == 4
    assert ref.domain == "principles"
    assert ref.source_type == "official_principle"
    assert ref.ref_id == f"principle:{P1_ID}"
    assert ref.title == "المبدأ رقم 1 — الأصول والقواعد"
    assert ref.regulation_title == ref.entity_name == "المحكمة العليا بهيئتها العامة"
    assert ref.url == SOURCE_URL
    assert ref.decision_line == "القرار 2/أ بتاريخ 29/8/1434هـ"
    assert ref.snippet == build_snippet(p) == ROW_P1["text"]
    assert ref.render_label() == (
        "المبدأ رقم 1 — الأصول والقواعد — المحكمة العليا بهيئتها العامة"
    )
    # Several bodies: distinct, joined «، ».
    two = principles.principle_from_row({
        **ROW_P2,
        "citations": ROW_P2["citations"] + ROW_P1["citations"] + ROW_P2["citations"],
    })
    assert _reference_from_ura(1, two).entity_name == (
        "الهيئة القضائية العليا، المحكمة العليا بهيئتها العامة"
    )


def _pinned_article() -> ArticleURAResult:
    return pinned.fetch_article_result(_db(), pinned.ARTICLE_40_ID)


def test_article_reference_matches_the_existing_articles_wire_shape():
    art = _pinned_article()
    ref = _reference_from_ura(9, art)
    assert ref.domain == "articles"
    assert ref.source_type == "article_full"
    assert ref.ref_id == f"article:{pinned.ARTICLE_40_ID}"
    assert ref.title == "المادة 40 من اللائحة التنفيذية لطرق الاعتراض على الأحكام"
    assert ref.article_num == "40"
    assert ref.regulation_title == ART40_REG_ROW["title"]
    assert ref.landing_url == ART40_REG_ROW["landing_url"]
    assert ref.doc_type == "لائحة تنفيذية"
    assert render_aggregator_content(art.for_aggregator()).startswith(
        "المادة 40 من اللائحة التنفيذية لطرق الاعتراض على الأحكام\n\n##### المادة الأربعون"
    )

    # Field-for-field what the backend rebuilds a persisted ``articles`` ref as
    # (snippet aside: the backend stamps it separately).
    refs_service = pytest.importorskip("backend.app.services.references_service")
    shell = refs_service.ArticleRefShell(
        ref_id=art.ref_id, relevance="medium", article_id=art.article_id,
        article_number=art.article_number, content=art.content,
        regulation_id=art.regulation_id, regulation_title=art.reg_title,
        landing_url=art.landing_url, doc_type=art.doc_type,
    )
    rebuilt = refs_service._reference_from_article_shell(9, shell)
    assert ref.model_dump(exclude={"snippet"}) == rebuilt.model_dump(exclude={"snippet"})

    # And simple_search's live card for the same مادة — identical bar the title
    # (simple_search prints «المادة 40»; the panel rebuild prints the full one).
    ss_runner = pytest.importorskip("agents.simple_search.runner")
    ss_models = pytest.importorskip("agents.simple_search.models")
    obj = ss_models.ResolvedObject(
        level="article", article_id=art.article_id, regulation_id=art.regulation_id,
        article_number="40", title="المادة 40", subtitle=art.reg_title,
        doc_type=art.doc_type, source_url=art.landing_url,
    )
    group = ss_runner._Group(key="k", level="article", objects=[obj])
    [ss_ref] = ss_runner.build_references(
        group, [ss_models.UnfoldResult(level="article", text=art.content)]
    )
    skip = {"title", "snippet", "relevance", "n"}
    assert ref.model_dump(exclude=skip) == ss_ref.model_dump(exclude=skip)


@pytest.mark.asyncio
async def test_source_views_for_both_new_domains():
    p = principles.principle_from_row(ROW_P1)
    view = await build_source_view(StubSupabase(), p)
    assert isinstance(view, PrincipleSourceView)
    assert view.model_dump() == {
        "source_type": "official_principle",
        "title": "المبدأ رقم 1 — الأصول والقواعد",
        "chapter": "الأصول والقواعد",
        "text": ROW_P1["text"],
        "decisions": [
            {"body": "المحكمة العليا بهيئتها العامة", "decision_no": "2/أ",
             "date_hijri": "29/8/1434"}
        ],
        "pages": [33],
        "url": SOURCE_URL,
    }
    # A bare persisted shell is hydrated by id.
    bare = PrincipleURAResult(ref_id=f"principle:{P2_ID}", relevance="medium",
                              principle_id=P2_ID)
    view = await build_source_view(_db(), bare)
    assert view.text == ROW_P2["text"] and view.decisions[0]["decision_no"] == "254"

    art = _pinned_article()
    view = await build_source_view(_db(), art)
    assert isinstance(view, ArticleFullSourceView)
    assert view.title == "المادة 40 من اللائحة التنفيذية لطرق الاعتراض على الأحكام"
    assert view.content.startswith("##### المادة الأربعون")
    # The re-read misses → the shell's own body backs the popup.
    view = await build_source_view(StubSupabase(), art)
    assert isinstance(view, ArticleFullSourceView) and "المادة الأربعون" in view.content


@pytest.mark.asyncio
async def test_preprocess_and_attach_source_views_end_to_end():
    p = principles.principle_from_row(ROW_P2, relevance="high")
    p.appears_in_sub_queries = [0]
    ura = _ura_with(p)
    await pinned.pin_article_40(_db(), ura)
    agg_input = AggregatorInput.from_ura(ura)

    refs, ref_to_sq = preprocess_references(agg_input)
    assert [(r.n, r.domain) for r in refs] == [(1, "principles"), (2, "articles")]
    assert ref_to_sq == {1: [0], 2: [0]}

    await attach_source_views(_db(), refs, [p, ura.medium_results[-1]])
    assert isinstance(refs[0].source_view, PrincipleSourceView)
    assert isinstance(refs[1].source_view, ArticleFullSourceView)

    message = build_aggregator_user_message(agg_input, refs)
    assert "<type>official_principle</type>" in message
    assert "القرار: 254 بتاريخ 13/10/1391هـ — الهيئة القضائية العليا" in message
    assert "<type>article_full</type>" in message
    assert "<article_num>40</article_num>" in message

    # The wire round-trips (references_json → Reference) with the new members.
    for ref in refs:
        assert Reference.model_validate(ref.model_dump()) == ref


# --------------------------------------------------------------------------- #
# Persisted URA (retrieval_artifacts.ura_json) reloads both new domains
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_ura_json_round_trip_with_principles_and_articles():
    ura = _ura_with(principles.principle_from_row(ROW_P1, relevance="high"),
                    principles.principle_from_row(ROW_P2))
    await pinned.pin_article_40(_db(), ura)

    reloaded = UnifiedRetrievalArtifact.model_validate(ura.model_dump())
    assert reloaded == ura
    assert [type(r).__name__ for r in reloaded.high_results + reloaded.medium_results] == [
        "PrincipleURAResult", "PrincipleURAResult", "ArticleURAResult",
    ]
    assert UnifiedRetrievalArtifact.model_validate_json(ura.model_dump_json()) == ura


# --------------------------------------------------------------------------- #
# Orchestrator status line follows the flag
# --------------------------------------------------------------------------- #


def test_reg_phase_text_names_principles_only_when_enabled(monkeypatch):
    from agents.deep_search_v4 import orchestrator

    monkeypatch.setattr(
        orchestrator, "get_settings",
        lambda: SimpleNamespace(OFFICIAL_PRINCIPLES_ENABLED=True),
    )
    assert orchestrator._reg_phase_text() == (
        "اكتمل البحث في الأنظمة والتعاميم والخدمات والمبادئ القضائية"
    )
    monkeypatch.setattr(orchestrator, "get_settings", lambda: SimpleNamespace())
    assert orchestrator._reg_phase_text() == "اكتمل البحث في الأنظمة والتعاميم والخدمات"


def test_flag_defaults_off():
    from shared.config import Settings

    assert Settings.model_fields["OFFICIAL_PRINCIPLES_ENABLED"].default is False
