"""/judgments wing tests (.claude/plans/seo_public_library.md § Phase 5).

Covers the backend pieces the wing added:

    GET /public/library/judgments                (hub, 9 cards/page, newest first)
    GET /public/library/judgments/{slug}         (doc page — the section model)
    GET /library/full/judgment/{slug}            (AUTHED full reveal)
    library_service.list_judgments_hub / judgments_hub_total_pages /
                    get_judgment_doc / get_full_judgment /
                    _judgment_cited_regulations

No live DB. Supabase is a small in-memory PostgREST stand-in (``FakeSupabase``):
it holds real row dicts per table and actually APPLIES the filters, ordering,
range and count the service asks for — the alternative (a scripted result queue)
cannot catch the two bugs this wing is most exposed to, namely NULL-date ordering
and ``in.()`` chunk sizes.

The load-bearing assertions here are the GATE ones: a gated section's hidden
bytes must not appear anywhere in the serialized anon response.
"""
from __future__ import annotations

import json
from typing import Any, Optional
from urllib.parse import quote

import pytest

from backend.app.services import library_service as ls


# ---------------------------------------------------------------------------
# In-memory PostgREST stand-in
# ---------------------------------------------------------------------------


class _Result:
    def __init__(self, data: Any, count: Optional[int] = None) -> None:
        self.data = data
        self.count = count


class _Chain:
    """Applies the subset of PostgREST semantics this wing uses."""

    def __init__(self, fake: "FakeSupabase", table: str) -> None:
        self._fake = fake
        self._table = table
        self._filters: list[tuple[str, str, Any]] = []
        self._orders: list[tuple[str, bool, Optional[bool]]] = []
        self._range: Optional[tuple[int, int]] = None
        self._limit: Optional[int] = None
        self._count: Optional[str] = None
        self._negate = False

    # --- builders ---------------------------------------------------------
    def select(self, *_cols: Any, count: Optional[str] = None, **_k: Any) -> "_Chain":
        self._count = count
        return self

    def eq(self, col: str, val: Any) -> "_Chain":
        self._filters.append(("eq", col, val))
        return self

    def in_(self, col: str, vals: list[Any]) -> "_Chain":
        vals = list(vals)
        self._fake.in_calls.append((self._table, col, vals))
        self._filters.append(("in", col, vals))
        return self

    def ilike(self, col: str, pattern: str) -> "_Chain":
        self._filters.append(("ilike", col, pattern))
        return self

    def contains(self, col: str, vals: list[Any]) -> "_Chain":
        self._filters.append(("contains", col, list(vals)))
        return self

    def like(self, col: str, pattern: str) -> "_Chain":
        self._filters.append(("ilike", col, pattern))
        return self

    @property
    def not_(self) -> "_Chain":
        self._negate = True
        return self

    def is_(self, col: str, val: Any) -> "_Chain":
        self._filters.append(("not_is" if self._negate else "is", col, val))
        self._negate = False
        return self

    def order(
        self,
        col: str,
        *,
        desc: bool = False,
        nullsfirst: Optional[bool] = None,
        foreign_table: Optional[str] = None,
    ) -> "_Chain":
        self._orders.append((col, desc, nullsfirst))
        self._fake.orders.append((self._table, col, desc, nullsfirst))
        return self

    def range(self, start: int, end: int) -> "_Chain":
        self._range = (start, end)
        return self

    def limit(self, n: int) -> "_Chain":
        self._limit = n
        return self

    # --- execution --------------------------------------------------------
    def _matches(self, row: dict[str, Any]) -> bool:
        for op, col, val in self._filters:
            cell = row.get(col)
            if op == "eq":
                if cell is None or str(cell) != str(val):
                    return False
            elif op == "in":
                if cell is None or str(cell) not in {str(v) for v in val}:
                    return False
            elif op == "ilike":
                needle = str(val).strip("%").lower()
                if needle and needle not in str(cell or "").lower():
                    return False
            elif op == "contains":
                if not set(map(str, val)) <= set(map(str, cell or [])):
                    return False
            elif op == "is":
                if val == "null" and cell is not None:
                    return False
            elif op == "not_is":
                if val == "null" and cell is None:
                    return False
        return True

    def execute(self) -> _Result:
        rows = [dict(r) for r in self._fake.tables.get(self._table, []) if self._matches(r)]

        # Ordering: apply passes in reverse for multi-key stability, matching
        # Postgres NULL defaults (ASC → NULLS LAST, DESC → NULLS FIRST) unless
        # nullsfirst was passed explicitly.
        for col, desc, nullsfirst in reversed(self._orders):
            nf = desc if nullsfirst is None else nullsfirst
            non_null = [r for r in rows if r.get(col) is not None]
            nulls = [r for r in rows if r.get(col) is None]
            non_null.sort(key=lambda r: str(r.get(col)), reverse=desc)
            rows = (nulls + non_null) if nf else (non_null + nulls)

        count = len(rows) if self._count == "exact" else None
        if self._range is not None:
            start, end = self._range
            rows = rows[start : end + 1]
        if self._limit is not None and self._count != "exact":
            rows = rows[: self._limit]
        self._fake.selects.append((self._table, list(self._filters)))
        return _Result(rows, count)


class FakeSupabase:
    """Row-backed fake: seed tables, then the service queries them for real."""

    def __init__(self, **tables: list[dict[str, Any]]) -> None:
        self.tables: dict[str, list[dict[str, Any]]] = {
            k: list(v) for k, v in tables.items()
        }
        self.tables.setdefault(
            "seo_gate_defaults",
            [{"content_type": "judgment", "default_gate": "gated"}],
        )
        self.in_calls: list[tuple[str, str, list[Any]]] = []
        self.orders: list[tuple[str, str, bool, Optional[bool]]] = []
        self.selects: list[tuple[str, list]] = []

    def table(self, name: str) -> _Chain:
        return _Chain(self, name)

    def rpc(self, name: str, params: dict[str, Any]) -> "_RpcResult":
        """Stand-in for ``public.bm25_search()`` (migration 111).

        ⚠ THIS IS A SUBSTRING MATCH, NOT BM25, and it is not trying to be. What
        these tests own is the WIRING — that ``q`` narrows the wing, that the
        returned order survives the corpus fetch, that an empty match set yields
        an empty page. Ranking quality is a property of the SQL and belongs to
        Wave F calibration against a real query set, not to a Python fake that
        would only ever prove it agrees with itself.

        Matches the same text the real index holds for a judgment: the title
        derived from ``short_summary`` plus the always-free lead. Nothing gated —
        mirroring D3, so a test can never accidentally assert that gated text is
        searchable.
        """
        assert name == "bm25_search", name
        needle = (params.get("p_query") or "").strip()
        rows: list[dict[str, Any]] = []
        for case in self.tables.get("cases", []):
            haystack = " ".join(
                str(case.get(k) or "")
                for k in ("short_summary", "court", "city", "case_number")
            )
            if needle and needle in haystack:
                rows.append(
                    {
                        "corpus": "judgment",
                        "content_id": str(case.get("id")),
                        "slug": None,
                        "title": "",
                        "facets": {},
                        "score": float(len(rows) * -1),
                        "total_count": 0,
                    }
                )
        for r in rows:
            r["total_count"] = len(rows)
        return _RpcResult(rows)


class _RpcResult:
    """``.execute()`` shim so the fake matches the supabase-py rpc chain."""

    def __init__(self, data: list[dict[str, Any]]) -> None:
        self._data = data

    def execute(self) -> "_RpcResult":
        return self

    @property
    def data(self) -> list[dict[str, Any]]:
        return self._data


@pytest.fixture(autouse=True)
def _clear_module_caches():
    """The gate-defaults + published-ids caches are module-level TTL caches;
    a test's seeded policy must never leak into the next test."""
    ls._gate_defaults_cache["value"] = None
    ls._gate_defaults_cache["expires_at"] = 0.0
    ls._published_ids_cache.clear()
    yield
    ls._gate_defaults_cache["value"] = None
    ls._gate_defaults_cache["expires_at"] = 0.0
    ls._published_ids_cache.clear()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

CASE_ID = "11111111-1111-1111-1111-111111111111"
SLUG = "نزاع-تجاري-حول-عقد-توريد-fi-439318978"

# Every gated section carries a unique tail marker: if any of these strings shows
# up in the serialized anon payload, gated bytes leaked.
LONG = "ب" * 900


def _long(marker: str) -> str:
    return f"{marker} " + " ".join(["كلمة"] * 300) + f" {marker}-END"


# The page body is the REAL ruling text (``cases.content``), so the fixture is a
# realistic `case_md` document: a `# القضية رقم N` H1 + a `- **court**: …`
# front-matter block (both duplicate the metadata card and must be stripped), then
# the ruling under its OWN `##` headings. Each section carries a unique tail
# marker so a leak of gated bytes is detectable in the serialized payload.
JUDGMENT_CONTENT = "\n".join(
    [
        "# القضية رقم 439318978",
        "",
        "- **court**: التجارية",
        "- **city**: الدمام",
        "- **details_url**: https://laws.moj.gov.sa/ar/JudicialDecisionsList/1/abc",
        "",
        "## نص الحكم",
        "",
        "OPENING " + " ".join(["كلمة"] * 400) + " OPENING-END",
        "",
        "## تسبيب الحكم:",
        "",
        "REASONING " + " ".join(["كلمة"] * 400) + " REASONING-END",
        "",
        "## منطوق الحكم",
        "",
        "RULING " + " ".join(["كلمة"] * 400) + " RULING-END",
    ]
)

FULL_CASE = {
    "id": CASE_ID,
    "content": JUDGMENT_CONTENT,
    "case_ref": "17642_fi_439318978",
    "court": "التجارية",
    "court_level": "first_instance",
    "city": "الدمام",
    "case_number": "439318978",
    "judgment_number": "433741394",
    "date_hijri": "29 ذو القعدة 1443",
    "date_gregorian": "2022-06-29",
    "appeal_result": "تأييد",
    "legal_domains": ["الملكية الفكرية"],
    "short_summary": "- نزاع حول انتهاك علامة تجارية مسجلة.\n- المحكمة قضت بالمنع.",
    "summary": "ملخص مطول",
    "details_url": "https://laws.moj.gov.sa/ar/JudicialDecisionsList/1/abc",
    "referenced_regulations": [],
    "facts": _long("FACTS"),
    "claims": _long("CLAIMS"),
    "plaintiff_grounds": _long("PGROUNDS"),
    "defendant_response": _long("DRESPONSE"),
    "defendant_grounds": _long("DGROUNDS"),
    "reasoning": _long("REASONING"),
    "ruling": _long("RULING"),
    "objection_grounds": _long("OBJECTION"),
    "appellee_response": _long("APPELLEE"),
    "appeal_reasoning": _long("APREASONING"),
    "appeal_ruling": _long("APRULING"),
}

META_ROW = {
    "content_type": "judgment",
    "content_id": CASE_ID,
    "slug": SLUG,
    "seo_tier": None,
    "gate_override": None,
    # Migration 130 — "may a crawler have this", independent of the slug. The
    # fixture is indexable so the default payload exercises the cleared path;
    # `_fake(indexable=False)` covers the 7,000 that stay noindex.
    "indexable": True,
    "updated_at": "2026-07-25T00:00:00+00:00",
}


def _fake(
    case: Optional[dict] = None,
    *,
    gate_override: Optional[str] = None,
    indexable: bool = True,
    **extra,
):
    meta = dict(META_ROW)
    if gate_override:
        meta["gate_override"] = gate_override
    meta["indexable"] = indexable
    return FakeSupabase(
        cases=[case or FULL_CASE],
        seo_item_meta=[meta],
        **extra,
    )


# ---------------------------------------------------------------------------
# Route + wiring inventory
# ---------------------------------------------------------------------------


def test_judgment_routes_registered() -> None:
    from backend.app.main import create_app

    paths = {getattr(r, "path", "") for r in create_app().routes}
    assert "/api/v1/public/library/judgments" in paths
    assert "/api/v1/public/library/judgments/{slug}" in paths


def test_judgment_registered_for_authed_full_content() -> None:
    from backend.app.api import public_library

    assert "judgment" in public_library._FULL_CONTENT_TYPES


def test_judgments_sitemap_section_is_served() -> None:
    """The judgments sitemap section is registered (2026-08-11).

    Replaces ``test_judgments_sitemap_section_not_served_yet``. The PDPL gate did
    not go away — it moved from "the wing is unreachable" to "only cleared rows
    are listed", enforced by ``seo_item_meta.indexable`` (migration 130) and
    asserted by the two tests below.
    """
    from backend.app.api import public_library

    assert public_library._LIBRARY_SITEMAP_SECTIONS["judgments"] == (
        "judgment",
        "judgments",
    )


def test_sitemap_feed_lists_only_indexable_rows() -> None:
    """The feed filters on ``indexable``, not merely on having a slug.

    This is the half of the PDPL gate that lives in the query. Without the
    ``indexable`` predicate the section would enumerate all 10,000 published
    rulings — including the 1,665 the selector excluded for carrying an identity
    marker — which is exactly what the old "section unreachable" rule bought.
    """
    cleared = {**META_ROW, "content_id": "c-1", "slug": "حكم-مصرّح", "indexable": True}
    withheld = {**META_ROW, "content_id": "c-2", "slug": "حكم-محجوب", "indexable": False}
    fake = FakeSupabase(seo_item_meta=[cleared, withheld])

    urls, _pages = ls.sitemap_library_urls(
        fake, "https://x.test", "judgment", "judgments"
    )

    locs = [u["loc"] for u in urls]
    assert locs == [f"https://x.test/judgments/{quote('حكم-مصرّح', safe='')}"]
    assert all("محجوب" not in quote(loc, safe="") for loc in locs)


def test_sitemap_feed_skips_indexable_rows_that_lost_their_slug() -> None:
    """Both predicates bind. `indexable` alone is not a servable page.

    An unpublished row cannot be listed however its flag reads — the two
    questions are independent, so the feed has to ask both.
    """
    unpublished = {
        **META_ROW,
        "content_id": "c-3",
        "slug": None,
        "indexable": True,
    }
    fake = FakeSupabase(seo_item_meta=[unpublished])

    urls, _pages = ls.sitemap_library_urls(
        fake, "https://x.test", "judgment", "judgments"
    )

    assert urls == []


def test_doc_payload_carries_the_indexable_flag() -> None:
    """``indexable`` reaches the page, and is FALSE when the sidecar says so.

    The page's ``robots`` meta and the sitemap read this one flag; if it stopped
    riding the payload the page would silently fall back to its noindex default
    while the sitemap kept listing the URL.
    """
    assert ls.get_judgment_doc(_fake(), SLUG)["indexable"] is True
    assert ls.get_judgment_doc(_fake(indexable=False), SLUG)["indexable"] is False


# ---------------------------------------------------------------------------
# The section model
# ---------------------------------------------------------------------------


# is_free = "this section reached the reader whole". Under the shared document
# budget nothing in this 3-section, ~6k-char fixture does: the allowance is
# ~915 chars and is spent front-to-back, so s1 truncates and s2/s3 get nothing.
EXPECTED_SECTIONS = [
    ("s1", "نص الحكم", False),
    ("s2", "تسبيب الحكم", False),
    ("s3", "منطوق الحكم", False),
]


def test_body_is_the_ruling_text_not_the_summary_columns() -> None:
    """The page publishes ``content`` — the document — not the derived columns.

    Guards the regression this wing was corrected for: rendering the pipeline's
    per-stage summaries as the body published a paraphrase of a court ruling
    under that ruling's own URL.
    """
    case = {**FULL_CASE, "facts": "SUMMARY-FACTS", "reasoning": "SUMMARY-REASONING"}
    doc = ls.get_judgment_doc(_fake(case), SLUG)
    body = json.dumps(doc["sections"], ensure_ascii=False)
    assert "SUMMARY-FACTS" not in body
    assert "SUMMARY-REASONING" not in body
    assert "OPENING" in body  # the real ruling text is what shipped


def test_section_order_ids_titles_and_free_flags() -> None:
    """Sections come from the document's OWN headings, ids are positional."""
    doc = ls.get_judgment_doc(_fake(), SLUG)
    got = [(s["id"], s["title"], s["is_free"]) for s in doc["sections"]]
    assert got == EXPECTED_SECTIONS


def test_the_budget_is_one_document_allowance_not_one_per_section() -> None:
    """THE regression this wing was re-gated for.

    The old rule gave 1,200 chars to EVERY section and rendered the first one
    whole, so free bytes grew with however finely a ruling happened to be
    subdivided — measured at 42% of the corpus body, against a stated intent of
    10–15%. The allowance is now computed once from the document's own length.
    """
    doc = ls.get_judgment_doc(_fake(), SLUG)
    total = sum(len(s["text"]) for s in ls._parse_judgment_body(JUDGMENT_CONTENT))
    served = sum(len(s["text"]) for s in doc["sections"])

    assert served <= ls.free_budget(total, ls.JUDGMENT_BUDGET)
    # The old rule would have served all of s1 plus 1,200 of each of s2/s3.
    assert served < len(ls._parse_judgment_body(JUDGMENT_CONTENT)[0]["text"])


def test_the_budget_is_front_loaded_and_later_sections_render_as_bars() -> None:
    """The opening carries the search terms; the reasoning is what an unlock buys."""
    doc = ls.get_judgment_doc(_fake(), SLUG)
    assert doc["sections"][0]["text"].startswith("OPENING")
    assert len(doc["sections"][0]["text"]) > 0
    for tail in doc["sections"][1:]:
        assert tail["text"] == ""
        assert tail["is_truncated"] is True
        # An empty section must still size its placeholder bars, or the page
        # renders a titled void with no signal that anything was withheld.
        assert tail["hidden_placeholder_lines"] > 0


def test_frontmatter_block_is_stripped_from_the_body() -> None:
    """The `# القضية رقم` H1 + `- **court**: …` bullets duplicate the metadata
    card and the official-source link — they must not render twice."""
    doc = ls.get_judgment_doc(_fake(), SLUG)
    body = "\n".join(s["text"] for s in doc["sections"])
    assert "**court**" not in body
    assert "القضية رقم 439318978" not in body
    assert "details_url" not in body


def test_body_without_headings_renders_as_one_section() -> None:
    """The common shape: no `##` headings at all. Must stay renderable."""
    case = {**FULL_CASE, "content": "نص الحكم بلا أي عناوين فرعية على الإطلاق."}
    doc = ls.get_judgment_doc(_fake(case), SLUG)
    assert [s["id"] for s in doc["sections"]] == ["s1"]
    assert doc["sections"][0]["title"] == ""


def test_single_section_document_is_still_gated() -> None:
    """A one-section ruling — 60% of the corpus — must not ship whole.

    Most of the corpus is one section (no headings, or a single «## نص الحكم»).
    A per-section budget applied there would have shipped the entire judgment
    ungated while still reporting gate='gated' — a decorative gate.
    """
    body = "SOLE " + " ".join(["كلمة"] * 600) + " SOLE-END"
    for content in (body, "## نص الحكم\n\n" + body):
        doc = ls.get_judgment_doc(_fake({**FULL_CASE, "content": content}), SLUG)
        assert len(doc["sections"]) == 1
        sole = doc["sections"][0]
        assert sole["is_free"] is False
        assert sole["is_truncated"] is True
        assert len(sole["text"]) <= ls.free_budget(len(body), ls.JUDGMENT_BUDGET)
        assert "SOLE-END" not in json.dumps(doc, ensure_ascii=False)
        assert doc["hidden_section_count"] == 1
        assert doc["withheld_pct"] >= 50.0


def test_empty_content_yields_no_sections() -> None:
    for empty in (None, "", "   \n  "):
        doc = ls.get_judgment_doc(_fake({**FULL_CASE, "content": empty}), SLUG)
        assert doc["sections"] == []
        assert doc["hidden_section_count"] == 0


def test_heading_trailing_colon_is_dropped_from_the_title() -> None:
    doc = ls.get_judgment_doc(_fake(), SLUG)
    titles = [s["title"] for s in doc["sections"]]
    assert "تسبيب الحكم" in titles  # source heading is «## تسبيب الحكم:»
    assert not any(t.endswith(":") for t in titles)


# ---------------------------------------------------------------------------
# The exposure budget primitives (pure — no DB, no wing)
# ---------------------------------------------------------------------------


BUDGET = ls.GateBudget(ratio=0.15, floor=600, ceiling=2000)


@pytest.mark.parametrize(
    "total, expected",
    [
        (100, 600),  # floor wins on a tiny document
        (4000, 600),  # 15% = 600, exactly the floor
        (10_000, 1500),  # the ratio governs the middle of the range
        (100_000, 2000),  # ceiling caps a long statute's leak
    ],
)
def test_free_budget_is_a_clamped_fraction(total: int, expected: int) -> None:
    assert ls.free_budget(total, BUDGET) == expected


def test_gate_decision_cuts_deeper_rather_than_breach_the_withheld_floor() -> None:
    """Between "the ratio budget" and "too short to gate" sits a band where the
    floor would over-serve. There we serve less, not more."""
    total = 2000  # target 600; withholding 1400 clears both floors
    assert ls.gate_decision(total, "gated", BUDGET) == ("gated", 600)

    total = 1500  # target 600 → withholds 900 (>=800, >=50%): still fine
    assert ls.gate_decision(total, "gated", BUDGET) == ("gated", 600)

    # 1300: the 600 floor would withhold only 700 — under MIN_WITHHELD_CHARS —
    # and cutting to the deepest legal serve (500) falls under the floor, so the
    # document cannot be gated honestly at all.
    assert ls.gate_decision(1300, "gated", BUDGET) == ("open", 1300)


def test_gate_decision_never_touches_an_open_document() -> None:
    assert ls.gate_decision(50_000, "open", BUDGET) == ("open", 50_000)


def test_spend_budget_is_shared_not_per_section() -> None:
    texts = ["A" * 1000, "B" * 1000, "C" * 1000]
    cuts = ls.spend_budget_across_sections(texts, "gated", 900)
    served = sum(len(c["visible_text"]) for c in cuts)
    assert served <= 900
    # The old behaviour would have served 900 from EVERY section.
    assert served < 900 * 3
    assert cuts[0]["visible_text"].startswith("A")
    assert cuts[1]["visible_text"] == ""


def test_spend_budget_passes_an_open_document_through_whole() -> None:
    texts = ["A" * 5000, "B" * 5000]
    cuts = ls.spend_budget_across_sections(texts, "open", 600)
    assert [c["visible_text"] for c in cuts] == texts
    assert all(c["is_truncated"] is False for c in cuts)


# ---------------------------------------------------------------------------
# Gating
# ---------------------------------------------------------------------------


def test_gated_truncation_removes_bytes_from_the_payload() -> None:
    """The security invariant: hidden bytes are absent from the anon response."""
    doc = ls.get_judgment_doc(_fake(), SLUG)  # section default = 'gated'
    assert doc["gate_effective"] == "gated"

    body = json.dumps(doc, ensure_ascii=False)
    for marker in ("OPENING-END", "REASONING-END", "RULING-END"):
        assert marker not in body, f"gated bytes leaked: {marker}"

    reasoning = next(s for s in doc["sections"] if s["id"] == "s2")
    assert reasoning["is_truncated"] is True
    assert reasoning["hidden_placeholder_lines"] > 0


def test_gated_judgment_withholds_the_majority_of_the_ruling() -> None:
    """The invariant the wing lacked: «gated» must actually withhold something.

    `hidden_section_count` cannot express this — it counts sections and reads 0
    on precisely the documents giving everything away, which is how the old gate
    hid its own leak. Assert on BYTES.
    """
    doc = ls.get_judgment_doc(_fake(), SLUG)
    assert doc["gate_effective"] == "gated"
    assert doc["withheld_pct"] >= ls.MIN_WITHHELD_RATIO * 100
    assert doc["withheld_chars"] >= ls.MIN_WITHHELD_CHARS


def test_a_ruling_too_short_to_gate_honestly_is_marked_open() -> None:
    """No paywall over a document we are not withholding.

    `gate_decision` step 3: below the withheld floor the item ships whole,
    reports 'open', drops the CTA and publishes its official source — the same
    downgrade `effective_circular_gate` has always applied to short تعاميم.
    """
    body = "قصير " + " ".join(["كلمة"] * 100)  # ~600 chars, cannot clear the floor
    doc = ls.get_judgment_doc(_fake({**FULL_CASE, "content": body}), SLUG)

    assert doc["gate_effective"] == "open"
    assert doc["withheld_chars"] == 0
    assert doc["hidden_section_count"] == 0
    assert all(s["is_truncated"] is False for s in doc["sections"])
    assert doc["official_sources"], "an open ruling publishes its MOJ source link"


def test_gated_payload_survives_the_response_model_unchanged() -> None:
    """Serializing through the real Pydantic model must not resurrect the bytes
    (and proves the payload keys match the model the frontend is built on)."""
    from backend.app.api.public_library import JudgmentDocResponse

    doc = ls.get_judgment_doc(_fake(), SLUG)
    dumped = JudgmentDocResponse(**doc).model_dump_json()
    assert "REASONING-END" not in dumped
    assert "OPENING-END" not in dumped
    assert "OPENING" in dumped  # the front-loaded preview survives intact


def test_open_gate_ships_every_section_whole() -> None:
    doc = ls.get_judgment_doc(_fake(gate_override="open"), SLUG)
    assert doc["gate_effective"] == "open"
    assert doc["hidden_section_count"] == 0
    parsed = ls._parse_judgment_body(JUDGMENT_CONTENT)
    assert [s["text"] for s in doc["sections"]] == [s["text"] for s in parsed]
    assert all(sec["is_truncated"] is False for sec in doc["sections"])


def test_a_short_trailing_section_is_hidden_once_the_budget_is_spent() -> None:
    """Section length no longer decides — the remaining allowance does.

    Under the old per-section budget this trailing «قصير جدًا.» rendered free
    because it happened to be shorter than 1,200 chars, and that "short sections
    are free" behaviour is exactly what let short documents through whole. With
    one shared allowance, a long opening consumes it and the tail is bars.
    """
    content = "\n".join(
        [
            "## نص الحكم",
            "OPENING " + " ".join(["كلمة"] * 400) + " OPENING-END",
            "## منطوق الحكم",
            "قصير جدًا.",
        ]
    )
    doc = ls.get_judgment_doc(_fake({**FULL_CASE, "content": content}), SLUG)
    short = next(s for s in doc["sections"] if s["id"] == "s2")
    assert short["is_truncated"] is True
    assert short["text"] == ""
    assert doc["hidden_section_count"] == 2


def test_summary_md_is_always_free() -> None:
    doc = ls.get_judgment_doc(_fake(), SLUG)
    assert doc["summary_md"] == FULL_CASE["short_summary"]


# ---------------------------------------------------------------------------
# Doc payload shape
# ---------------------------------------------------------------------------


def test_doc_payload_keys_are_the_frontend_contract() -> None:
    doc = ls.get_judgment_doc(_fake(), SLUG)
    assert set(doc) == {
        "slug", "title", "subject", "court", "court_level", "court_level_label",
        "city", "case_number", "judgment_number", "date_hijri", "date_gregorian",
        "hijri_year", "appeal_result", "domains", "metadata", "summary_md",
        "has_summary", "sections", "cited_regulations", "cited_total",
        "official_sources", "gate_effective", "indexable", "hidden_section_count",
        "withheld_chars", "withheld_pct",
    }


def test_derived_naming_comes_from_the_shared_module() -> None:
    from shared.seo import judgment_naming

    doc = ls.get_judgment_doc(_fake(), SLUG)
    assert doc["subject"] == judgment_naming.judgment_subject(FULL_CASE)
    assert doc["title"] == judgment_naming.judgment_display_title(FULL_CASE)
    assert doc["court_level_label"] == "ابتدائي"
    assert doc["hijri_year"] == "1443"


def test_metadata_card_omits_missing_values() -> None:
    case = {
        **FULL_CASE,
        "city": None,
        "judgment_number": None,
        "appeal_result": None,
        "date_gregorian": None,
    }
    doc = ls.get_judgment_doc(_fake(case), SLUG)
    labels = [m["label"] for m in doc["metadata"]]
    assert labels == ["المحكمة", "الدرجة", "رقم القضية", "التاريخ الهجري"]
    assert all(m["value"] for m in doc["metadata"])


def test_a_GATED_judgment_withholds_its_official_source() -> None:
    """User decision 2026-07-28, REVERSING the plan's §1.2 «the official source
    URL is always shown, gated or not».

    The block is not a generic link — it is a deep link carrying the source
    system's own identifier, so publishing it across the GATED corpus hands out a
    slug → official-ID crosswalk. It is served ONLY from the authed reveal.

    Every judgment is gated by section policy, so this is the live branch for the
    whole wing. Layer A (a property of the item, not the caller), so the ISR
    payload stays cacheable.
    """
    doc = ls.get_judgment_doc(_fake(), SLUG)
    assert doc["official_sources"] == []
    assert FULL_CASE["details_url"] not in json.dumps(doc, ensure_ascii=False)


def test_an_OPEN_judgment_publishes_its_official_source() -> None:
    """Narrowed to gated-only on 2026-08-01: an open item ships whole — text AND
    source link — to anyone, crawlers included, and never reaches a reveal, so
    there is still exactly one renderer per page."""
    doc = ls.get_judgment_doc(_fake(gate_override="open"), SLUG)
    assert doc["gate_effective"] == "open", "fixture is not open — test proves nothing"
    assert doc["official_sources"] == [
        {"title": "مصدر الحكم — وزارة العدل", "href": FULL_CASE["details_url"]}
    ]


def test_the_moj_details_url_is_still_what_the_reveal_serves() -> None:
    """Withheld from the page, not deleted from the corpus: the authed reveal is
    where it comes from."""
    fake = _fake()
    fake.tables.setdefault("cases", [dict(FULL_CASE)])
    out = ls.official_sources_for_item(fake, "judgment", FULL_CASE["id"])
    assert out == [
        {"title": "مصدر الحكم — وزارة العدل", "href": FULL_CASE["details_url"]}
    ]


def test_non_url_details_url_is_not_surfaced_as_a_source() -> None:
    fake = _fake({**FULL_CASE, "details_url": "scraped"})
    fake.tables.setdefault("cases", [{**FULL_CASE, "details_url": "scraped"}])
    assert ls.official_sources_for_item(fake, "judgment", FULL_CASE["id"]) == []


def test_unknown_slug_returns_none() -> None:
    assert ls.get_judgment_doc(_fake(), "لا-يوجد") is None
    assert ls.get_judgment_doc(_fake(), "") is None
    assert ls.get_full_judgment(_fake(), "لا-يوجد") is None


def test_sidecar_pointing_at_a_missing_case_returns_none() -> None:
    fake = FakeSupabase(cases=[], seo_item_meta=[META_ROW])
    assert ls.get_judgment_doc(fake, SLUG) is None


# ---------------------------------------------------------------------------
# AUTHED full reveal
# ---------------------------------------------------------------------------


def test_full_judgment_returns_every_section_untruncated() -> None:
    full = ls.get_full_judgment(_fake(), SLUG)
    assert set(full) == {"sections", "summary_md"}
    assert [s["id"] for s in full["sections"]] == [i for i, _, _ in EXPECTED_SECTIONS]
    assert [s["title"] for s in full["sections"]] == [t for _, t, _ in EXPECTED_SECTIONS]
    for sec in full["sections"]:
        assert set(sec) == {"id", "title", "text"}
    # The bytes the anon page withheld are exactly what the authed reveal returns.
    assert full["sections"][1]["text"].endswith("REASONING-END")
    assert full["sections"][2]["text"].endswith("RULING-END")


def test_full_judgment_section_ids_match_the_anon_page() -> None:
    """The enhancer swaps section-for-section, so the ids must line up."""
    content = "\n".join(
        ["## أولًا", "نص أول مطول " * 40, "## ثانيًا", "نص ثانٍ مطول " * 40]
    )
    case = {**FULL_CASE, "content": content}
    anon = ls.get_judgment_doc(_fake(case), SLUG)
    full = ls.get_full_judgment(_fake(case), SLUG)
    assert [s["id"] for s in full["sections"]] == [s["id"] for s in anon["sections"]]
    assert [s["title"] for s in full["sections"]] == [
        s["title"] for s in anon["sections"]
    ]


def test_full_judgment_serializes_through_the_shared_full_model() -> None:
    from backend.app.api.public_library import LibraryFullResponse

    full = ls.get_full_judgment(_fake(), SLUG)
    model = LibraryFullResponse(content_type="judgment", key=SLUG, **full)
    assert model.sections is not None
    assert model.sections[1].text.endswith("REASONING-END")
    assert model.summary_md == FULL_CASE["summary"]


# ---------------------------------------------------------------------------
# «ملخص ريحان» — cases.summary, gated, on the SAME unlock as the ruling
# ---------------------------------------------------------------------------


def test_rayhan_summary_never_reaches_the_anon_payload() -> None:
    """The anon page publishes the BOOLEAN, never the summary.

    `summary_md` on the doc payload stays `short_summary` (the always-free
    ~250-char lead); `cases.summary` is gated content and its only path to a
    reader is the metered reveal.
    """
    doc = ls.get_judgment_doc(_fake(), SLUG)
    assert doc["has_summary"] is True
    assert doc["summary_md"] == FULL_CASE["short_summary"]
    assert FULL_CASE["summary"] not in json.dumps(doc, ensure_ascii=False)


def test_the_reveal_serves_the_rayhan_summary_alongside_the_ruling() -> None:
    """ONE response, both payloads — which is what makes it ONE unlock. The
    page's «ملخص ريحان» button and its «اعرض النص كاملاً» panel share this call,
    so a reader is never charged twice for one ruling."""
    full = ls.get_full_judgment(_fake(), SLUG)
    assert full["summary_md"] == FULL_CASE["summary"]
    assert full["sections"], "the same response must still carry the ruling"


def test_has_summary_is_false_when_the_ruling_has_none() -> None:
    """~18 of 30,531 rulings. The button renders on this flag, so it must be
    honest — an unlock is never spendable on nothing."""
    for empty in (None, "", "   "):
        case = {**FULL_CASE, "summary": empty}
        assert ls.get_judgment_doc(_fake(case), SLUG)["has_summary"] is False
        assert ls.get_full_judgment(_fake(case), SLUG)["summary_md"] is None


def test_rayhan_summary_strips_the_pipeline_sections() -> None:
    """16.5k rows end in a «المراجع النظامية المحلولة» appendix of internal
    corpus/chunk ids and match scores; 252 carry a `## classification_error`
    Python traceback. Neither may reach a reader — the strip is render-time
    because the table is pipeline-owned and a re-ingest restores both.
    """
    dirty = "\n".join(
        [
            "## الملخص",
            "- نزاع حول انتهاك علامة تجارية.",
            "",
            "## المراجع النظامية المحلولة",
            "- **نظام العلامات التجارية** — المادة 16 → `17642_reg_003`",
            "  (chunks: 17642_reg_003_article_16) [confidence: 0.9016]",
            "",
            "## classification_error",
            "ConnectError: [Errno 11001] getaddrinfo failed",
        ]
    )
    case = {**FULL_CASE, "summary": dirty}

    served = ls.get_full_judgment(_fake(case), SLUG)["summary_md"]
    assert served == "## الملخص\n- نزاع حول انتهاك علامة تجارية."
    assert "17642_reg_003" not in served
    assert "confidence" not in served
    assert "classification_error" not in served
    assert "getaddrinfo" not in served

    # And a summary that is NOTHING BUT the appendix counts as no summary at all.
    only_noise = {**FULL_CASE, "summary": "## classification_error\nJSONDecodeError"}
    assert ls.get_judgment_doc(_fake(only_noise), SLUG)["has_summary"] is False


# ---------------------------------------------------------------------------
# The cited-regulations mesh
# ---------------------------------------------------------------------------

REG_UUID = "22222222-2222-2222-2222-222222222222"
REG_ROW = {
    "id": REG_UUID,
    "reg_ref": "17642_reg_037",
    "clean_title": "نظام التحكيم",
    "title": "نظام التحكيم الخام",
}


def _ref(reg_id: Optional[str], no: Optional[str], name: str) -> dict[str, Any]:
    return {"النظام": name, "الرقم": no, "regulation_id": reg_id}


def _mesh_fake(refs: list[dict[str, Any]], *, meta_extra: list[dict] = ()):
    case = {**FULL_CASE, "referenced_regulations": refs}
    return FakeSupabase(
        cases=[case],
        seo_item_meta=[META_ROW, *meta_extra],
        regulations_v2=[REG_ROW],
    )


def test_mesh_resolves_regulation_and_article_slugs() -> None:
    fake = _mesh_fake(
        [_ref("17642_reg_037", "50", "نظام التحكيم")],
        meta_extra=[
            {"content_type": "regulation", "content_id": REG_UUID, "slug": "نظام-التحكيم"},
            {"content_type": "article", "content_id": f"{REG_UUID}#50", "slug": "المادة-50"},
        ],
    )
    doc = ls.get_judgment_doc(fake, SLUG)
    assert doc["cited_total"] == 1
    assert doc["cited_regulations"] == [
        {
            "title": "نظام التحكيم",
            "article_no": "50",
            "reg_slug": "نظام-التحكيم",
            "article_slug": "المادة-50",
        }
    ]


def test_mesh_dedupes_repeated_citations_preserving_order() -> None:
    refs = [
        _ref("17642_reg_037", "50", "نظام التحكيم"),
        _ref("17642_reg_037", "11", "نظام التحكيم"),
        _ref("17642_reg_037", "50", "نظام التحكيم"),  # dup
        _ref(None, "245", "لائحة نظام المحاكم التجارية"),
        _ref(None, "245", "لائحة نظام المحاكم التجارية"),  # dup
        _ref(None, "245", "نظام آخر بلا معرّف"),  # same article_no, different نظام
    ]
    items, total = ls._judgment_cited_regulations(_mesh_fake(refs), {"referenced_regulations": refs})
    assert total == 4
    assert [(i["title"], i["article_no"]) for i in items] == [
        ("نظام التحكيم", "50"),
        ("نظام التحكيم", "11"),
        ("لائحة نظام المحاكم التجارية", "245"),
        ("نظام آخر بلا معرّف", "245"),
    ]


def test_mesh_unresolved_reference_still_listed_without_links() -> None:
    refs = [_ref(None, "58", "اللائحة التنفيذية لنظام المحاكم التجارية")]
    doc = ls.get_judgment_doc(_mesh_fake(refs), SLUG)
    assert doc["cited_regulations"] == [
        {
            "title": "اللائحة التنفيذية لنظام المحاكم التجارية",
            "article_no": "58",
            "reg_slug": None,
            "article_slug": None,
        }
    ]


def test_mesh_unpublished_regulation_has_no_slugs() -> None:
    """Resolved to a corpus row, but nothing published → canonical title, no link."""
    doc = ls.get_judgment_doc(_mesh_fake([_ref("17642_reg_037", "50", "نظام تحكيم")]), SLUG)
    assert doc["cited_regulations"] == [
        {"title": "نظام التحكيم", "article_no": "50", "reg_slug": None, "article_slug": None}
    ]


def test_mesh_article_slug_requires_a_published_parent() -> None:
    """A مادة URL is nested under the reg slug — an orphan article slug is dropped."""
    fake = _mesh_fake(
        [_ref("17642_reg_037", "50", "نظام التحكيم")],
        meta_extra=[
            {"content_type": "article", "content_id": f"{REG_UUID}#50", "slug": "المادة-50"}
        ],
    )
    doc = ls.get_judgment_doc(fake, SLUG)
    assert doc["cited_regulations"][0]["reg_slug"] is None
    assert doc["cited_regulations"][0]["article_slug"] is None


def test_mesh_paragraph_citation_links_to_its_article() -> None:
    """«16/1» displays verbatim but resolves to المادة 16."""
    fake = _mesh_fake(
        [_ref("17642_reg_037", "50/1", "نظام التحكيم")],
        meta_extra=[
            {"content_type": "regulation", "content_id": REG_UUID, "slug": "نظام-التحكيم"},
            {"content_type": "article", "content_id": f"{REG_UUID}#50", "slug": "المادة-50"},
        ],
    )
    item = ls.get_judgment_doc(fake, SLUG)["cited_regulations"][0]
    assert item["article_no"] == "50/1"
    assert item["article_slug"] == "المادة-50"


def test_mesh_arabic_indic_article_number_resolves() -> None:
    assert ls._judgment_article_int("٥٠") == 50
    assert ls._judgment_article_int("16/1") == 16
    assert ls._judgment_article_int("") is None
    assert ls._judgment_article_int("مكرر") is None


def test_mesh_batches_and_chunks_every_lookup() -> None:
    """One query per lookup table, in.() chunked at 150 (URL-length trap)."""
    refs = [_ref(f"17642_reg_{i:03d}", str(i), f"نظام {i}") for i in range(200)]
    fake = _mesh_fake(refs)
    ls.get_judgment_doc(fake, SLUG)

    reg_chunks = [vals for tbl, col, vals in fake.in_calls if tbl == "regulations_v2"]
    assert len(reg_chunks) == 2  # 200 refs → 150 + 50, not 200 round-trips
    assert all(len(c) <= 150 for c in reg_chunks)
    assert sum(len(c) for c in reg_chunks) == 200


def test_mesh_handles_missing_and_malformed_reference_payloads() -> None:
    for payload in (None, "not json", [], ["scalar", None], {"nope": 1}):
        items, total = ls._judgment_cited_regulations(
            _mesh_fake([]), {"referenced_regulations": payload}
        )
        assert (items, total) == ([], 0)


def test_mesh_parses_jsonb_delivered_as_a_string() -> None:
    refs = [_ref("17642_reg_037", "50", "نظام التحكيم")]
    items, total = ls._judgment_cited_regulations(
        _mesh_fake(refs), {"referenced_regulations": json.dumps(refs, ensure_ascii=False)}
    )
    assert total == 1 and items[0]["article_no"] == "50"


def test_cited_free_limit_default_shows_the_whole_mesh() -> None:
    """The list is names + numbers only — gating it would gate our own crawl graph."""
    assert ls.JUDGMENT_CITED_FREE_LIMIT is None


def test_cited_free_limit_caps_items_but_not_the_total(monkeypatch) -> None:
    monkeypatch.setattr(ls, "JUDGMENT_CITED_FREE_LIMIT", 3)
    refs = [_ref(None, str(i), f"نظام {i}") for i in range(10)]
    items, total = ls._judgment_cited_regulations(
        _mesh_fake(refs), {"referenced_regulations": refs}
    )
    assert len(items) == 3
    assert total == 10


# ---------------------------------------------------------------------------
# Hub — ordering, filters, pagination
# ---------------------------------------------------------------------------


def _hub_case(n: int, date: Optional[str], **over) -> dict[str, Any]:
    return {
        "id": f"{n:08d}-0000-0000-0000-000000000000",
        "case_ref": f"17642_fi_{n}",
        "court": "التجارية",
        "court_level": "first_instance",
        "city": "الرياض",
        "case_number": str(n),
        "judgment_number": None,
        "date_hijri": "15 ربيع الأول 1445",
        "date_gregorian": date,
        "legal_domains": ["المعاملات التجارية"],
        "short_summary": f"- نزاع رقم {n} حول عقد إجارة.\n- قضت المحكمة برفض الدعوى.",
        "summary": None,
        "facts": "وقائع",
        "ruling": "منطوق",
        **over,
    }


def _hub_fake(cases: list[dict[str, Any]], *, published: Optional[list[str]] = None):
    """Corpus + sidecar + the PUBLISHED-ONLY VIEW the hub actually reads.

    ⚠ Seeding ``cases`` alone is no longer enough. Since migration 123 the hub
    paginates ``library_judgments_ranked`` — corpus ⋈ sidecar, ``slug is not
    null`` — so "published" is a property of the RELATION rather than a filter
    applied after pagination. That is the whole point: an unpublished row cannot
    be in the relation, so no page can come back short at any publish size. The
    view carries ``slug``, which is what retired the per-page ``_slug_map``
    round-trip; a fake that omits it makes every card slugless and every item is
    dropped.
    """
    ids = [c["id"] for c in cases] if published is None else published
    meta = [
        {
            "content_type": "judgment",
            "content_id": cid,
            "slug": f"حكم-{cid[:8]}",
            "seo_tier": None,
            "gate_override": None,
            "updated_at": "2026-07-25T00:00:00+00:00",
        }
        for cid in ids
    ]
    slug_by_id = {m["content_id"]: m["slug"] for m in meta}
    ranked = [
        dict(c, slug=slug_by_id[c["id"]]) for c in cases if c["id"] in slug_by_id
    ]
    return FakeSupabase(
        cases=cases, seo_item_meta=meta, library_judgments_ranked=ranked
    )


def test_hub_orders_newest_first_with_dateless_judgments_last() -> None:
    cases = [
        _hub_case(1, None),
        _hub_case(2, "2021-01-01"),
        _hub_case(3, None),
        _hub_case(4, "2024-05-05"),
        _hub_case(5, "2023-02-02"),
    ]
    out = ls.list_judgments_hub(_hub_fake(cases), page=1)
    assert [i["date_gregorian"] for i in out["items"]] == [
        "2024-05-05", "2023-02-02", "2021-01-01", None, None,
    ]


def test_hub_passes_nullslast_explicitly_on_the_ranked_view(monkeypatch) -> None:
    """Postgres puts NULLs FIRST on DESC by default — the hub must override it.

    Ordering is now ALWAYS the database's job: there is one path, a ``range``
    query over ``library_judgments_ranked``, so this is no longer a "legacy path"
    special case and needs no ``_published_ids`` monkeypatch to reach it.

    ⚠ This matters more after the publish ramp than before it. The ديوان المظالم,
    زكاة and تأمين feeds carry NO ``date_gregorian`` at all, so if NULLs sorted
    first they would occupy the entire front of the wing and bury every dated
    وزارة العدل judgment behind them.
    """
    cases = [_hub_case(i, None if i % 2 else f"20{20 + i}-01-01") for i in range(8)]
    fake = _hub_fake(cases)
    out = ls.list_judgments_hub(fake, page=1)

    date_orders = [
        o
        for o in fake.orders
        if o[0] == "library_judgments_ranked" and o[1] == "date_gregorian"
    ]
    assert date_orders and date_orders[0] == (
        "library_judgments_ranked", "date_gregorian", True, False,
    )
    # …and the resulting page really is newest-first with the dateless rows last.
    dates = [i["date_gregorian"] for i in out["items"]]
    assert dates == ["2026-01-01", "2024-01-01", "2022-01-01", "2020-01-01", None, None, None, None]


# ``test_hub_python_sort_key_matches_the_db_contract`` was DELETED here, not
# skipped: ``_judgment_hub_sort_key`` existed only to reproduce the DB ordering
# for the Python-sorted sample page, and that page is gone. Ordering now has one
# implementation (the query above), so there is no second one left to keep honest.


def test_hub_pagination_fills_every_page() -> None:
    cases = [_hub_case(i, f"2024-01-{i:02d}") for i in range(1, 13)]  # 12 published
    fake = _hub_fake(cases)
    p1 = ls.list_judgments_hub(fake, page=1)
    ls._published_ids_cache.clear()
    p2 = ls.list_judgments_hub(fake, page=2)
    assert (len(p1["items"]), p1["total_pages"]) == (9, 2)
    assert (len(p2["items"]), p2["page"]) == (3, 2)
    assert not {i["slug"] for i in p1["items"]} & {i["slug"] for i in p2["items"]}
    ls._published_ids_cache.clear()
    assert ls.judgments_hub_total_pages(fake) == 2


def test_hub_returns_only_published_rows() -> None:
    cases = [_hub_case(i, f"2024-01-{i:02d}") for i in range(1, 6)]
    fake = _hub_fake(cases, published=[cases[0]["id"], cases[3]["id"]])
    out = ls.list_judgments_hub(fake, page=1)
    assert len(out["items"]) == 2
    assert {i["date_gregorian"] for i in out["items"]} == {"2024-01-01", "2024-01-04"}


def test_hub_item_shape_and_snippet_strips_bullets() -> None:
    fake = _hub_fake([_hub_case(7, "2024-03-03")])
    item = ls.list_judgments_hub(fake, page=1)["items"][0]
    assert set(item) == {
        "slug", "title", "court", "court_level", "court_level_label", "city",
        "date_hijri", "date_gregorian", "domains", "snippet",
        # Added with the court sections: the raw `court` string stays for
        # display, `court_slug` is its bucket, so the pill can link to
        # /judgments/courts/{slug}. `None` for a raw value no bucket claims —
        # the pill degrades to plain text rather than a broken link.
        "court_slug",
    }
    assert item["court_level_label"] == "ابتدائي"
    assert item["court_slug"] == "المحكمة-التجارية"
    assert item["snippet"].startswith("نزاع رقم 7")
    assert "- " not in item["snippet"]
    assert "قضت المحكمة برفض الدعوى" in item["snippet"]


def test_hub_filters_court_level_domain_and_query() -> None:
    cases = [
        _hub_case(1, "2024-01-01", court_level="appeal", legal_domains=["العمل"]),
        _hub_case(2, "2024-01-02", court_level="first_instance", legal_domains=["العقار"]),
        _hub_case(3, "2024-01-03", court_level="appeal", legal_domains=["العقار"]),
    ]
    fake = _hub_fake(cases)
    by_level = ls.list_judgments_hub(fake, page=1, court_level="appeal")
    assert len(by_level["items"]) == 2
    ls._published_ids_cache.clear()
    by_domain = ls.list_judgments_hub(fake, page=1, domain="العقار")
    assert len(by_domain["items"]) == 2
    ls._published_ids_cache.clear()
    # ``q`` now resolves through bm25_search (Wave B) instead of an ilike on
    # short_summary. The wing's OWN filters still run on the corpus rows, which
    # is what this asserts alongside the narrowing.
    by_q = ls.list_judgments_hub(fake, page=1, q="نزاع رقم 3")
    assert len(by_q["items"]) == 1
    assert by_q["items"][0]["court_level"] == "appeal"
    # A search carries a result count; a browse listing does not (D8 keeps the
    # envelope otherwise identical, snippet included).
    assert by_q["total_count"] == 1
    assert by_q["total_count_is_exact"] is True
    assert by_q["items"][0]["snippet"]
    assert by_level["total_count"] is None


def test_hub_empty_result_still_returns_one_page() -> None:
    """A search that matches nothing is one EMPTY page, not zero pages — the
    paginator has to render something. ``total_count`` is 0 and exact: nothing
    was truncated, there simply was nothing."""
    fake = _hub_fake([_hub_case(1, "2024-01-01")])
    out = ls.list_judgments_hub(fake, page=1, q="لا-يوجد-شيء-كهذا")
    assert out == {
        "items": [],
        "page": 1,
        "total_pages": 1,
        "total_count": 0,
        "total_count_is_exact": True,
    }
    ls._published_ids_cache.clear()
    assert ls.judgments_hub_total_pages(fake, q="لا-يوجد-شيء-كهذا") == 1


def test_hub_items_serialize_through_the_response_model() -> None:
    from backend.app.api.public_library import JudgmentHubItem, JudgmentHubResponse

    fake = _hub_fake([_hub_case(1, "2024-01-01"), _hub_case(2, None)])
    data = ls.list_judgments_hub(fake, page=1)
    resp = JudgmentHubResponse(
        items=[JudgmentHubItem(**it) for it in data["items"]],
        page=data["page"],
        total_pages=data["total_pages"],
    )
    assert len(resp.items) == 2
    assert resp.items[1].date_gregorian is None
    assert resp.max_anon_page == ls.ANON_HUB_MAX_PAGE
