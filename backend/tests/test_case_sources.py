"""``shared.library.case_sources`` — where a ruling came from.

The load-bearing assertion in here is the GATE SPLIT: `citation` may render on an
anonymous page and `official_sources` may not, so no test may ever find a URL in
`citation`. D-CROSSWALK (`.claude/plans/access_tiers_gating_DECISIONS.md`) is what that
protects, and a bound-volume PDF is the strongest case for it in the corpus — one file is
~50 full rulings, every one of them gated.

The `source` payloads below are VERBATIM shapes from the live corpus (2026-08-19), trimmed
to the keys this module reads. Inventing them would test the parser against itself.
"""
from __future__ import annotations

import pytest

from shared.library.case_sources import entity_name, judgment_provenance
from shared.library.case_volume_registry import CASE_VOLUME_SOURCES, volume_source

# --- real corpus shapes ----------------------------------------------------------------

MOJ = {
    "details_url": "https://laws.moj.gov.sa/ar/JudicialDecisionsList/2/SouXoRiQdjYyCbDx",
    "source": {},
    "entities": {"entity_name": "وزارة العدل"},
}

# ديوان المظالم — «<group>/<volume>» key, post-1438 «Volume_N» filename
BOG_VOLUME_N = {
    "details_url": None,
    "source": {
        "group": "الأحكام_الإدارية_1440هـ",
        "pages": {"end": 259, "start": 253},
        "parser": "parse_volumes",
        "volume": "الأحكام_الإدارية_1440هـ/Volume_2",
        "entity_ref": "17486",
        "source_volume": 2,
        "case_number_admin": "1880/5/ق",
    },
    "entities": {"entity_name": "ديوان المظالم"},
}

# ديوان المظالم — pre-1438, Arabic volume name
BOG_ARABIC = {
    "details_url": None,
    "source": {
        "group": "الأحكام_الجزائية_1402-1427هـ",
        "pages": {"end": 553, "start": 539},
        "volume": "الأحكام_الجزائية_1402-1427هـ/المجلد الثالث",
        "entity_ref": "17486",
        "source_volume": 0,
    },
    "entities": {"entity_name": "ديوان المظالم"},
}

# لجان الزكاة والضريبة — a قرار published as its OWN pdf
GSTC_DECISION = {
    "details_url": None,
    "source": {
        "kind": "decision_pdf",
        "year": "1438هـ",
        "pdf_url": "https://gstc.gov.sa/ar/Decisions/Documents/1438هـ/جدة/قرار رقم 15.pdf",
        "committee": "اللجنة الابتدائية الأولى",
        "entity_ref": "40046",
        "page_count": 10,
        "source_url": "https://gstc.gov.sa/ar/Decisions/Pages/decisions.aspx?year=1438",
    },
    "entities": {"entity_name": "الأمانة العامة للجان الزكوية والضريبية والجمركية"},
}

# لجان الزكاة والضريبة — a bound مدونة, sha1 key
GSTC_VOLUME = {
    "details_url": None,
    "source": {
        "kind": "volume",
        "pages": {"end": 969, "start": 961},
        "entity_ref": "40046",
        "volume_title": "مدونة القرارات عام  1439هـ",
        "source_volume": "b7d5e9e7b27586bb",
    },
    "entities": {"entity_name": "الأمانة العامة للجان الزكوية والضريبية والجمركية"},
}


def _labels(prov) -> dict[str, str]:
    return {row["label"]: row["value"] for row in prov.citation}


def _hrefs(prov) -> list[str]:
    return [link["href"] for link in prov.official_sources]


# --- the gate split --------------------------------------------------------------------


@pytest.mark.parametrize(
    "row", [MOJ, BOG_VOLUME_N, BOG_ARABIC, GSTC_DECISION, GSTC_VOLUME]
)
def test_the_citation_half_NEVER_carries_a_url(row) -> None:
    """The one rule this module exists to keep.

    `citation` renders on the anonymous /judgments page; `official_sources` rides the
    unlock. A URL that leaks into `citation` publishes the crosswalk to every crawler —
    and for a bound volume, a link to ~50 gated rulings.
    """
    prov = judgment_provenance(row, entity_name(row))
    for value in _labels(prov).values():
        assert "http" not in value, value
        assert "www." not in value, value


def test_the_links_half_is_where_every_url_lives() -> None:
    prov = judgment_provenance(BOG_VOLUME_N, "ديوان المظالم")
    assert prov.official_sources, "a volume ruling must reach its publisher"
    assert all(h.startswith("https://") for h in _hrefs(prov))


# --- shape 1: وزارة العدل --------------------------------------------------------------


def test_a_moj_ruling_adds_no_citation_rows() -> None:
    """Its source is a link, not a location — the card already names court and number."""
    prov = judgment_provenance(MOJ, "وزارة العدل")
    assert prov.citation == []
    assert prov.official_sources == [
        {"title": "مصدر الحكم — وزارة العدل", "href": MOJ["details_url"]}
    ]


def test_an_unknown_entity_still_produces_an_honest_link_title() -> None:
    """797 rows have a null ``entity_id``. The link must not guess a publisher."""
    prov = judgment_provenance({**MOJ, "entities": None}, "")
    assert prov.official_sources == [
        {"title": "مصدر الحكم الرسمي", "href": MOJ["details_url"]}
    ]


# --- shape 2: a standalone قرار PDF ----------------------------------------------------


def test_a_single_decision_pdf_is_JUST_the_link() -> None:
    """«if parsed from 1 pdf, the link is enough» — the whole file is this one decision,
    so a page range would describe the document rather than locate anything in it. The
    committee is not repeated either: ``cases.court`` already reads «هيئة الزكاة
    والضريبة — الدائرة الابتدائية الأولى…»."""
    prov = judgment_provenance(GSTC_DECISION, "الأمانة العامة للجان الزكوية والضريبية والجمركية")
    assert prov.citation == []
    assert [link["title"] for link in prov.official_sources] == [
        "ملف القرار (PDF)",
        "صفحة القرارات — الأمانة العامة للجان الزكوية والضريبية والجمركية",
    ]
    assert prov.official_sources[0]["href"] == GSTC_DECISION["source"]["pdf_url"]


# --- shape 3: a bound مجلد -------------------------------------------------------------


def test_a_volume_ruling_names_its_volume_and_pages() -> None:
    prov = judgment_provenance(BOG_VOLUME_N, "ديوان المظالم")
    assert _labels(prov) == {
        # «Volume_2» is the publisher's FILENAME. A lawyer reads «المجلد 2».
        "المجلد": "المجلد 2 — مجموعة الأحكام الإدارية لعام 1440 هـ",
        "الصفحات": "253–259",
    }


def test_an_arabic_named_volume_is_used_verbatim() -> None:
    prov = judgment_provenance(BOG_ARABIC, "ديوان المظالم")
    assert _labels(prov)["المجلد"] == (
        "المجلد الثالث — مجموعة الأحكام والمبادئ الجزائية للأعوام 1402هـ-1427هـ"
    )


def test_a_volume_ruling_offers_the_pdf_AND_the_collection_page() -> None:
    """Both, in that order: the PDF is the document, the collection page is the proof of
    who published it — and it is the ONLY link the 8 volumes with no resolved file URL
    can offer, so it is never conditional on the PDF."""
    prov = judgment_provenance(BOG_VOLUME_N, "ديوان المظالم")
    assert [link["title"] for link in prov.official_sources] == [
        "ملف المجلد (PDF)",
        "صفحة المجموعة — ديوان المظالم",
    ]


def test_a_volume_whose_pdf_url_was_never_resolved_still_reaches_its_publisher() -> None:
    """8 of 102 volumes (355 rulings) have no direct file URL. They degrade to the
    collection page rather than to nothing."""
    key = "الأحكام_الجزائية_1402-1427هـ/المجلد الثاني"
    assert CASE_VOLUME_SOURCES[key]["pdf"] == "", "fixture assumes this volume has no pdf"
    prov = judgment_provenance(
        {**BOG_ARABIC, "source": {**BOG_ARABIC["source"], "volume": key}},
        "ديوان المظالم",
    )
    assert [link["title"] for link in prov.official_sources] == [
        "صفحة المجموعة — ديوان المظالم"
    ]


def test_a_gstc_volume_keys_on_its_sha_not_its_path() -> None:
    """The two parsers keyed volumes differently and both shapes must resolve."""
    prov = judgment_provenance(GSTC_VOLUME, "الأمانة العامة للجان الزكوية والضريبية والجمركية")
    assert _labels(prov)["المجلد"] == (
        "مدونة القرارات عام  1439هـ — مدونة القرارات والمبادئ الزكوية والضريبية "
        "1434هـ - 1439هـ"
    )
    assert _labels(prov)["الصفحات"] == "961–969"


def test_a_bog_int_source_volume_is_not_mistaken_for_a_registry_key() -> None:
    """ديوان المظالم rows set ``source_volume`` to an int INDEX (0, 2, …), not a sha.
    Reading it as a key would resolve the wrong volume — or, worse, silently resolve
    nothing while the path key sat right there."""
    assert BOG_VOLUME_N["source"]["source_volume"] == 2
    assert volume_source(BOG_VOLUME_N["source"]) is CASE_VOLUME_SOURCES[
        "الأحكام_الإدارية_1440هـ/Volume_2"
    ]


# --- degradation -----------------------------------------------------------------------


@pytest.mark.parametrize(
    "source",
    [
        None,
        {},
        "not-a-dict",
        {"volume": "a/volume/that/was/never/published"},
        {"volume": "الأحكام_الإدارية_1440هـ/Volume_2", "pages": {"start": 0, "end": 0}},
        {"volume": "الأحكام_الإدارية_1440هـ/Volume_2", "pages": "corrupt"},
    ],
)
def test_a_malformed_source_never_raises(source) -> None:
    """This runs inside ISR page renders and inside a PAID reveal. An attribution row is
    not worth a 500 on either."""
    prov = judgment_provenance({"source": source, "details_url": None}, "")
    assert isinstance(prov.citation, list)
    assert isinstance(prov.official_sources, list)


def test_a_zero_page_range_is_dropped_not_printed() -> None:
    """A parser that failed writes 0. «الصفحات: 0–0» is worse than no row."""
    row = {
        "details_url": None,
        "source": {
            "volume": "الأحكام_الإدارية_1440هـ/Volume_2",
            "pages": {"start": 0, "end": 0},
        },
    }
    assert "الصفحات" not in _labels(judgment_provenance(row, ""))


def test_a_single_page_ruling_prints_one_number() -> None:
    row = {
        "details_url": None,
        "source": {
            "volume": "الأحكام_الإدارية_1440هـ/Volume_2",
            "pages": {"start": 253, "end": 253},
        },
    }
    assert _labels(judgment_provenance(row, ""))["الصفحات"] == "253"


def test_an_empty_provenance_is_falsy() -> None:
    """So a caller can write ``if prov:`` instead of checking both lists."""
    assert not judgment_provenance({"source": {}, "details_url": None}, "")
    assert judgment_provenance(MOJ, "وزارة العدل")


# --- the entity embed ------------------------------------------------------------------


@pytest.mark.parametrize(
    "embed,expected",
    [
        ({"entity_name": "ديوان المظالم"}, "ديوان المظالم"),
        ([{"entity_name": "ديوان المظالم"}], "ديوان المظالم"),
        (None, ""),
        ([], ""),
        ({}, ""),
        ({"entity_name": None}, ""),
    ],
)
def test_the_entity_embed_unwraps_every_shape_postgrest_returns(embed, expected) -> None:
    assert entity_name({"entities": embed}) == expected


# --- the registry itself ---------------------------------------------------------------


def test_no_registry_title_carries_arabic_indic_digits() -> None:
    """The app's chrome is Latin-digit only; the carve-out is corpus BODY text, and a
    publication title composed into a metadata grid is not that. bog titles them
    «لعام ١٤٤٠ هـ»."""
    for key, vol in CASE_VOLUME_SOURCES.items():
        assert not set(vol["collection"]) & set("٠١٢٣٤٥٦٧٨٩"), key


def test_every_registry_volume_can_reach_its_publisher() -> None:
    """``pdf`` is allowed to be empty; ``landing`` is not — it is the fallback."""
    for key, vol in CASE_VOLUME_SOURCES.items():
        assert vol["landing"].startswith("https://"), key
        assert vol["pdf"] == "" or vol["pdf"].startswith("https://"), key
