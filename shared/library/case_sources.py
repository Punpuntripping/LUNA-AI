"""Where a ruling came from — the ONE reader of ``cases.source``.

Two thirds of the أحكام corpus (20,671 of 30,531) is وزارة العدل and carries a
``details_url`` deep link. The other 9,860 were parsed out of PDFs, and until now showed no
source at all: a reader could not tell whether a ruling came from a قرار published on its
own or from page 253 of the second مجلد of a bound annual set.

``cases.source`` already recorded that — it just never reached a payload. This module turns
it into the two things a surface needs:

* :attr:`Provenance.citation` — label/value rows for «المعلومات الأساسية»: «المجلد» and
  «الصفحات». Bibliographic text, no URL.
* :attr:`Provenance.official_sources` — the links: the publisher's collection page and the
  volume/decision PDF.

**The split is a gating decision, not a layout one.** D-CROSSWALK
(``.claude/plans/access_tiers_gating_DECISIONS.md``) puts source URLs behind the item's
unlock, and a bound volume makes that rule stronger rather than weaker: one مجلد PDF holds
~50 full rulings, every one of them gated. So ``citation`` may render anonymously and
``official_sources`` may not. Callers must keep them apart — see
``library_service.get_judgment_doc`` (citation only) and
``library_service.official_sources_for_item`` (links only).

Three source shapes, distinguished by :func:`judgment_provenance`:

===================  ===========================  =====================================
shape                ``cases.source`` marker      what the reader gets
===================  ===========================  =====================================
وزارة العدل           (none — ``details_url``)     the ruling's own MoJ page
single قرار PDF      ``kind == 'decision_pdf'``   the decision's own PDF + listing page
bound مجلد           ``volume`` / ``volume_title`` مجلد + page range, PDF + collection
===================  ===========================  =====================================

A single-PDF ruling gets NO page range: the whole file is that one decision, so «الصفحات»
would be describing the document rather than locating anything in it. The link is enough.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Optional

from shared.library.case_volume_registry import volume_source

# «Volume_2» is the publisher's own filename, not a label to show a lawyer. Rendered
# «المجلد 2» — Latin digit, per the app-wide numerals rule.
_VOLUME_N = re.compile(r"^Volume[_ ]?(\d+)$", re.IGNORECASE)

# Underscores are the scraper's path separator, not the publisher's punctuation:
# «الأحكام_الإدارية_1440هـ» is one title with spaces in it.
_UNDERSCORES = re.compile(r"_+")


@dataclass(frozen=True)
class Provenance:
    """Where one ruling came from, split by what the gate allows.

    ``citation`` is free to render anonymously; ``official_sources`` rides the item's
    unlock. Both are empty for a ruling whose ``source`` says nothing useful, so a caller
    can append unconditionally.
    """

    citation: list[dict[str, str]] = field(default_factory=list)
    """``[{'label': 'المجلد', 'value': …}, …]`` — no URLs, safe on an anon page."""

    official_sources: list[dict[str, str]] = field(default_factory=list)
    """``[{'title': …, 'href': …}, …]`` — the crosswalk. Metered reveal ONLY."""

    def __bool__(self) -> bool:
        return bool(self.citation or self.official_sources)


def _clean(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def entity_name(row: dict[str, Any]) -> str:
    """Issuing body's Arabic name off an embedded ``entities(entity_name)``, or ``""``.

    Both callers select the entity as a PostgREST embed, so both have to unwrap the same
    three shapes: a nested dict, ``None`` for the 797 rows with a null ``entity_id``, and
    a list if the relation is ever read as to-many. One implementation, because a source
    link is not worth a TypeError inside a page render.
    """
    entity = row.get("entities")
    if isinstance(entity, list):
        entity = entity[0] if entity else None
    if not isinstance(entity, dict):
        return ""
    return _clean(entity.get("entity_name"))


def _is_http(url: str) -> bool:
    return url.startswith("http://") or url.startswith("https://")


def _volume_label(source: dict[str, Any]) -> str:
    """The volume's own name — «المجلد 2», «مدونة القرارات عام 1439هـ», «الجزء الأول».

    ``volume_title`` (gstc/idc) is the publisher's title and is used verbatim. ديوان
    المظالم rows have no title, only a ``"<group>/<volume>"`` path, so the tail of that
    path is the name; «Volume_N» is the raw filename and gets rewritten Arabic.
    """
    title = _clean(source.get("volume_title"))
    if title:
        return title

    volume = _clean(source.get("volume"))
    if not volume:
        return ""
    tail = volume.rsplit("/", 1)[-1]
    m = _VOLUME_N.match(tail)
    return f"المجلد {m.group(1)}" if m else _UNDERSCORES.sub(" ", tail)


def _page_range(source: dict[str, Any]) -> str:
    """``"253–259"``, or ``"253"`` when a ruling sits on a single page.

    An en-dash, not a hyphen: this is a range in RTL running text. Both ends must be
    positive ints — a parser that failed and wrote 0 or null must not render «0–0».
    """
    pages = source.get("pages")
    if not isinstance(pages, dict):
        return ""
    try:
        start = int(pages.get("start") or 0)
        end = int(pages.get("end") or 0)
    except (TypeError, ValueError):
        return ""
    if start <= 0:
        return ""
    if end <= 0 or end == start:
        return str(start)
    return f"{start}–{end}"


def judgment_provenance(
    row: dict[str, Any], entity_name: str = ""
) -> Provenance:
    """Read one ``cases`` row into its :class:`Provenance`.

    ``row`` needs ``source`` and ``details_url``; ``entity_name`` is the issuing body's
    Arabic name (``entities.entity_name``) and only ever titles a link — pass ``""`` and
    the links stay correct, just less specific.

    PURE: no I/O, no DB. Unknown/blank/malformed ``source`` yields an empty
    :class:`Provenance` rather than raising, because this runs inside page renders that
    must not 500 over an attribution row.
    """
    source = row.get("source")
    source = source if isinstance(source, dict) else {}

    citation: list[dict[str, str]] = []
    links: list[dict[str, str]] = []

    # ---- 1. a قرار published as its own PDF -------------------------------------
    # `pdf_url` is on the row itself; no registry lookup. Per the plan: one PDF, one
    # decision ⇒ the link is the whole answer, no page range.
    if _clean(source.get("kind")) == "decision_pdf":
        # No citation rows at all. `source.committee` is the obvious candidate and is
        # WRONG: `cases.court` already reads «هيئة الزكاة والضريبة — الدائرة الأولى
        # للفصل في مخالفات ومنازعات ضريبة القيمة المضافة في مدينة الرياض», so a «الدائرة»
        # row would print the same committee twice in one card. And a page range would be
        # describing the file, not locating anything inside it.
        pdf_url = _clean(source.get("pdf_url"))
        if _is_http(pdf_url):
            links.append({"title": "ملف القرار (PDF)", "href": pdf_url})
        listing = _clean(source.get("source_url"))
        if _is_http(listing):
            links.append(
                {
                    "title": f"صفحة القرارات — {entity_name}" if entity_name
                    else "صفحة القرارات الرسمية",
                    "href": listing,
                }
            )
        return Provenance(citation=citation, official_sources=links)

    # ---- 2. a ruling lifted out of a bound مجلد ----------------------------------
    volume = _volume_label(source)
    published = volume_source(source)
    if volume:
        collection = _clean(published.get("collection")) if published else ""
        # «المجلد 2 — مجموعة الأحكام الإدارية لعام 1440 هـ». Collapsed to one part when
        # the volume IS the publication (the مدونات, whose title already names the set).
        citation.append(
            {
                "label": "المجلد",
                "value": f"{volume} — {collection}" if collection else volume,
            }
        )
        pages = _page_range(source)
        if pages:
            citation.append({"label": "الصفحات", "value": pages})

    if published:
        pdf_url = published["pdf"]
        if _is_http(pdf_url):
            links.append({"title": "ملف المجلد (PDF)", "href": pdf_url})
        landing = published["landing"]
        if _is_http(landing):
            # Named for the ENTITY, not the collection: this is the exit that says who
            # published the ruling. It is also the only link the 8 volumes with no
            # resolved file URL can offer, so it is never conditional on `pdf`.
            links.append(
                {
                    "title": f"صفحة المجموعة — {entity_name}" if entity_name
                    else "صفحة المجموعة الرسمية",
                    "href": landing,
                }
            )

    # ---- 3. وزارة العدل — the ruling has its own page ----------------------------
    # Last, so a row that somehow carries both a volume and a details_url lists the
    # narrower link (its own page) after the broader one.
    details_url = _clean(row.get("details_url"))
    if _is_http(details_url):
        links.append(
            {
                "title": f"مصدر الحكم — {entity_name}" if entity_name
                else "مصدر الحكم الرسمي",
                "href": details_url,
            }
        )

    return Provenance(citation=citation, official_sources=links)
