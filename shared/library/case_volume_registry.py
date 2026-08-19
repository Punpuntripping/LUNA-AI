"""Bound-volume provenance for the rulings parsed out of published PDF مجلدات.

GENERATED — do not hand-edit. Regenerate with::

    python scripts/build_case_volume_registry.py

``cases.source`` records which volume a ruling was parsed from and at which pages, but
never where that volume lives on the publisher's site. This module supplies the missing
half, recovered once from the ingestion scraper logs.

Two key shapes, because the two parsers keyed their volumes differently:

* **ديوان المظالم** — ``"<group>/<volume>"``, matching ``source['volume']`` verbatim
  (e.g. ``"الأحكام_الإدارية_1440هـ/Volume_2"``).
* **لجان الزكاة والضريبة · لجان التأمين** — the volume's sha1, matching
  ``source['source_volume']`` (e.g. ``"b7d5e9e7b27586bb"``).

Look them up through :func:`volume_source` rather than indexing directly — it takes a
``cases.source`` dict and knows which key shape that row uses.

``collection`` is the publisher's own title for the bound set — NOT the group folder name,
which is unreliable (``الأحكام_التجارية_1428هـ`` holds that year's إدارية, تجارية and
جزائية volumes alike). ``pdf`` is ``""`` for the volumes whose direct file URL the scrape
never resolved; ``landing`` is always present and is the honest fallback.

⚠ These URLs are the CROSSWALK, and D-CROSSWALK
(``.claude/plans/access_tiers_gating_DECISIONS.md``) puts them behind the unlock — one
volume PDF holds every ruling in the set, gated ones included. Only ``collection`` and the
page range may render on an anonymous page. Callers:
``shared.library.case_sources.judgment_provenance`` (free) and
``library_service.official_sources_for_item`` (metered).
"""

from __future__ import annotations

from typing import Any, Optional, TypedDict


class VolumeSource(TypedDict):
    """Where a bound volume was published. ``pdf`` may be ``""``; ``landing`` is set."""

    collection: str
    landing: str
    pdf: str


CASE_VOLUME_SOURCES: dict[str, VolumeSource] = {
    '10d58ebf5714c5a6': {
        "collection": 'مدونة القرارات والمبادئ الزكوية والضريبية 2020م-2021م',
        "landing": 'https://gstc.gov.sa/ar/Decisions/Pages/decisions.aspx?year=%D9%85%D8%AF%D9%88%D9%86%D8%A9%20%D8%A7%D9%84%D9%82%D8%B1%D8%A7%D8%B1%D8%A7%D8%AA%20%D9%88%D8%A7%D9%84%D9%85%D8%A8%D8%A7%D8%AF%D8%A6%20%D8%A7%D9%84%D8%B2%D9%83%D9%88%D9%8A%D8%A9%20%D9%88%D8%A7%D9%84%D8%B6%D8%B1%D9%8A%D8%A8%D9%8A%D8%A9%202020%D9%85-2021%D9%85',
        "pdf": 'https://gstc.gov.sa/ar/Decisions/Documents/مدونة القرارات والمبادئ الزكوية والضريبية 2020م-2021م/2.pdf',
    },
    '2222fe56a7a028a9': {
        "collection": 'مدونة القرارات والمبادئ الزكوية والضريبية 1434هـ - 1439هـ',
        "landing": 'https://gstc.gov.sa/ar/Decisions/Pages/decisions.aspx?year=%D9%85%D8%AF%D9%88%D9%86%D8%A9%20%D8%A7%D9%84%D9%82%D8%B1%D8%A7%D8%B1%D8%A7%D8%AA%20%D9%88%D8%A7%D9%84%D9%85%D8%A8%D8%A7%D8%AF%D8%A6%20%D8%A7%D9%84%D8%B2%D9%83%D9%88%D9%8A%D8%A9%20%D9%88%D8%A7%D9%84%D8%B6%D8%B1%D9%8A%D8%A8%D9%8A%D8%A9%201434%D9%87%D9%80%20-%201439%D9%87%D9%80',
        "pdf": 'https://gstc.gov.sa/ar/Decisions/Documents/مدونة القرارات والمبادئ الزكوية والضريبية 1435هـ - 1439هـ/4.pdf',
    },
    '317b054238fe3efb': {
        "collection": '',
        "landing": 'https://www.idc.gov.sa/ar-sa/Pages/Library.aspx?tab=tabpane_0',
        "pdf": 'https://www.idc.gov.sa/ar-sa/DocLib1/%D9%85%D8%AF%D9%88%D9%86%D8%A9%20%D8%A7%D9%84%D8%B3%D9%88%D8%A7%D8%A8%D9%82%20%D8%A7%D9%84%D9%82%D8%B6%D8%A7%D8%A6%D9%8A%D8%A9%20%D8%A7%D9%84%D8%AA%D8%A3%D9%85%D9%8A%D9%86%D9%8A%D8%A9.pdf',
    },
    '3277e7138506870d': {
        "collection": 'مدونة القرارات والمبادئ الزكوية والضريبية 1434هـ - 1439هـ',
        "landing": 'https://gstc.gov.sa/ar/Decisions/Pages/decisions.aspx?year=%D9%85%D8%AF%D9%88%D9%86%D8%A9%20%D8%A7%D9%84%D9%82%D8%B1%D8%A7%D8%B1%D8%A7%D8%AA%20%D9%88%D8%A7%D9%84%D9%85%D8%A8%D8%A7%D8%AF%D8%A6%20%D8%A7%D9%84%D8%B2%D9%83%D9%88%D9%8A%D8%A9%20%D9%88%D8%A7%D9%84%D8%B6%D8%B1%D9%8A%D8%A8%D9%8A%D8%A9%201434%D9%87%D9%80%20-%201439%D9%87%D9%80',
        "pdf": 'https://gstc.gov.sa/ar/Decisions/Documents/مدونة القرارات والمبادئ الزكوية والضريبية 1435هـ - 1439هـ/2.pdf',
    },
    '4f88af69f1d833ef': {
        "collection": 'مدونة القرارات والمبادئ الزكوية والضريبية 1434هـ - 1439هـ',
        "landing": 'https://gstc.gov.sa/ar/Decisions/Pages/decisions.aspx?year=%D9%85%D8%AF%D9%88%D9%86%D8%A9%20%D8%A7%D9%84%D9%82%D8%B1%D8%A7%D8%B1%D8%A7%D8%AA%20%D9%88%D8%A7%D9%84%D9%85%D8%A8%D8%A7%D8%AF%D8%A6%20%D8%A7%D9%84%D8%B2%D9%83%D9%88%D9%8A%D8%A9%20%D9%88%D8%A7%D9%84%D8%B6%D8%B1%D9%8A%D8%A8%D9%8A%D8%A9%201434%D9%87%D9%80%20-%201439%D9%87%D9%80',
        "pdf": 'https://gstc.gov.sa/ar/Decisions/Documents/مدونة القرارات والمبادئ الزكوية والضريبية 1435هـ - 1439هـ/3.pdf',
    },
    '53e831741b70b43a': {
        "collection": 'مدونة القرارات والمبادئ الزكوية والضريبية 2020م-2021م',
        "landing": 'https://gstc.gov.sa/ar/Decisions/Pages/decisions.aspx?year=%D9%85%D8%AF%D9%88%D9%86%D8%A9%20%D8%A7%D9%84%D9%82%D8%B1%D8%A7%D8%B1%D8%A7%D8%AA%20%D9%88%D8%A7%D9%84%D9%85%D8%A8%D8%A7%D8%AF%D8%A6%20%D8%A7%D9%84%D8%B2%D9%83%D9%88%D9%8A%D8%A9%20%D9%88%D8%A7%D9%84%D8%B6%D8%B1%D9%8A%D8%A8%D9%8A%D8%A9%202020%D9%85-2021%D9%85',
        "pdf": 'https://gstc.gov.sa/ar/Decisions/Documents/مدونة القرارات والمبادئ الزكوية والضريبية 2020م-2021م/1.pdf',
    },
    '9a9dac73529d2160': {
        "collection": 'مدونة القرارات والمبادئ الزكوية والضريبية 1434هـ - 1439هـ',
        "landing": 'https://gstc.gov.sa/ar/Decisions/Pages/decisions.aspx?year=%D9%85%D8%AF%D9%88%D9%86%D8%A9%20%D8%A7%D9%84%D9%82%D8%B1%D8%A7%D8%B1%D8%A7%D8%AA%20%D9%88%D8%A7%D9%84%D9%85%D8%A8%D8%A7%D8%AF%D8%A6%20%D8%A7%D9%84%D8%B2%D9%83%D9%88%D9%8A%D8%A9%20%D9%88%D8%A7%D9%84%D8%B6%D8%B1%D9%8A%D8%A8%D9%8A%D8%A9%201434%D9%87%D9%80%20-%201439%D9%87%D9%80',
        "pdf": 'https://gstc.gov.sa/ar/Decisions/Documents/مدونة القرارات والمبادئ الزكوية والضريبية 1435هـ - 1439هـ/5.pdf',
    },
    'b7377c04751bbef9': {
        "collection": '',
        "landing": 'https://www.idc.gov.sa/ar-sa/Pages/Library.aspx?tab=tabpane_0',
        "pdf": 'https://www.idc.gov.sa/ar-sa/DocLib1/%D9%85%D8%AF%D9%88%D9%86%D8%A9%20%D8%A7%D9%84%D9%85%D8%A8%D8%A7%D8%AF%D8%A6%20%D8%A7%D9%84%D9%82%D8%B6%D8%A7%D8%A6%D9%8A%D8%A9%20%D8%A7%D9%84%D8%AA%D8%A3%D9%85%D9%8A%D9%86%D9%8A%D8%A9.pdf',
    },
    'b7d5e9e7b27586bb': {
        "collection": 'مدونة القرارات والمبادئ الزكوية والضريبية 1434هـ - 1439هـ',
        "landing": 'https://gstc.gov.sa/ar/Decisions/Pages/decisions.aspx?year=%D9%85%D8%AF%D9%88%D9%86%D8%A9%20%D8%A7%D9%84%D9%82%D8%B1%D8%A7%D8%B1%D8%A7%D8%AA%20%D9%88%D8%A7%D9%84%D9%85%D8%A8%D8%A7%D8%AF%D8%A6%20%D8%A7%D9%84%D8%B2%D9%83%D9%88%D9%8A%D8%A9%20%D9%88%D8%A7%D9%84%D8%B6%D8%B1%D9%8A%D8%A8%D9%8A%D8%A9%201434%D9%87%D9%80%20-%201439%D9%87%D9%80',
        "pdf": 'https://gstc.gov.sa/ar/Decisions/Documents/مدونة القرارات والمبادئ الزكوية والضريبية 1435هـ - 1439هـ/6.pdf',
    },
    'f406caf97fa70afb': {
        "collection": 'مدونة القرارات والمبادئ الزكوية والضريبية 1434هـ - 1439هـ',
        "landing": 'https://gstc.gov.sa/ar/Decisions/Pages/decisions.aspx?year=%D9%85%D8%AF%D9%88%D9%86%D8%A9%20%D8%A7%D9%84%D9%82%D8%B1%D8%A7%D8%B1%D8%A7%D8%AA%20%D9%88%D8%A7%D9%84%D9%85%D8%A8%D8%A7%D8%AF%D8%A6%20%D8%A7%D9%84%D8%B2%D9%83%D9%88%D9%8A%D8%A9%20%D9%88%D8%A7%D9%84%D8%B6%D8%B1%D9%8A%D8%A8%D9%8A%D8%A9%201434%D9%87%D9%80%20-%201439%D9%87%D9%80',
        "pdf": 'https://gstc.gov.sa/ar/Decisions/Documents/مدونة القرارات والمبادئ الزكوية والضريبية 1435هـ - 1439هـ/1.pdf',
    },
    'الأحكام_الإدارية_1402-1426هـ/المجلد الأول': {
        "collection": 'مجموعة الأحكام والمبادئ الإدارية للأعوام 1402-1426 هـ',
        "landing": 'https://www.bog.gov.sa/ScientificContent/JudicialBlogs/AA1402-1426/Pages/default.aspx',
        "pdf": 'https://www.bog.gov.sa/ScientificContent/JudicialBlogs/AA1402-1426/Documents/المجموعة كاملة (PDF)/%D8%A7%D9%84%D9%85%D8%AC%D9%84%D8%AF%20%D8%A7%D9%84%D8%A3%D9%88%D9%84.pdf',
    },
    'الأحكام_الإدارية_1402-1426هـ/المجلد التاسع': {
        "collection": 'مجموعة الأحكام والمبادئ الإدارية للأعوام 1402-1426 هـ',
        "landing": 'https://www.bog.gov.sa/ScientificContent/JudicialBlogs/AA1402-1426/Pages/default.aspx',
        "pdf": 'https://www.bog.gov.sa/ScientificContent/JudicialBlogs/AA1402-1426/Documents/المجموعة كاملة (PDF)/%D8%A7%D9%84%D9%85%D8%AC%D9%84%D8%AF%20%D8%A7%D9%84%D8%AA%D8%A7%D8%B3%D8%B9.pdf',
    },
    'الأحكام_الإدارية_1402-1426هـ/المجلد التاسع عشر': {
        "collection": 'مجموعة الأحكام والمبادئ الإدارية للأعوام 1402-1426 هـ',
        "landing": 'https://www.bog.gov.sa/ScientificContent/JudicialBlogs/AA1402-1426/Pages/default.aspx',
        "pdf": 'https://www.bog.gov.sa/ScientificContent/JudicialBlogs/AA1402-1426/Documents/المجموعة كاملة (PDF)/%D8%A7%D9%84%D9%85%D8%AC%D9%84%D8%AF%20%D8%A7%D9%84%D8%AA%D8%A7%D8%B3%D8%B9%20%D8%B9%D8%B4%D8%B1.pdf',
    },
    'الأحكام_الإدارية_1402-1426هـ/المجلد الثالث': {
        "collection": 'مجموعة الأحكام والمبادئ الإدارية للأعوام 1402-1426 هـ',
        "landing": 'https://www.bog.gov.sa/ScientificContent/JudicialBlogs/AA1402-1426/Pages/default.aspx',
        "pdf": '',
    },
    'الأحكام_الإدارية_1402-1426هـ/المجلد الثالث عشر': {
        "collection": 'مجموعة الأحكام والمبادئ الإدارية للأعوام 1402-1426 هـ',
        "landing": 'https://www.bog.gov.sa/ScientificContent/JudicialBlogs/AA1402-1426/Pages/default.aspx',
        "pdf": 'https://www.bog.gov.sa/ScientificContent/JudicialBlogs/AA1402-1426/Documents/المجموعة كاملة (PDF)/%D8%A7%D9%84%D9%85%D8%AC%D9%84%D8%AF%20%D8%A7%D9%84%D8%AB%D8%A7%D9%84%D8%AB%20%D8%B9%D8%B4%D8%B1.pdf',
    },
    'الأحكام_الإدارية_1402-1426هـ/المجلد الثامن': {
        "collection": 'مجموعة الأحكام والمبادئ الإدارية للأعوام 1402-1426 هـ',
        "landing": 'https://www.bog.gov.sa/ScientificContent/JudicialBlogs/AA1402-1426/Pages/default.aspx',
        "pdf": 'https://www.bog.gov.sa/ScientificContent/JudicialBlogs/AA1402-1426/Documents/المجموعة كاملة (PDF)/%D8%A7%D9%84%D9%85%D8%AC%D9%84%D8%AF%20%D8%A7%D9%84%D8%AB%D8%A7%D9%85%D9%86.pdf',
    },
    'الأحكام_الإدارية_1402-1426هـ/المجلد الثامن عشر': {
        "collection": 'مجموعة الأحكام والمبادئ الإدارية للأعوام 1402-1426 هـ',
        "landing": 'https://www.bog.gov.sa/ScientificContent/JudicialBlogs/AA1402-1426/Pages/default.aspx',
        "pdf": 'https://www.bog.gov.sa/ScientificContent/JudicialBlogs/AA1402-1426/Documents/المجموعة كاملة (PDF)/%D8%A7%D9%84%D9%85%D8%AC%D9%84%D8%AF%20%D8%A7%D9%84%D8%AB%D8%A7%D9%85%D9%86%20%D8%B9%D8%B4%D8%B1.pdf',
    },
    'الأحكام_الإدارية_1402-1426هـ/المجلد الثاني عشر': {
        "collection": 'مجموعة الأحكام والمبادئ الإدارية للأعوام 1402-1426 هـ',
        "landing": 'https://www.bog.gov.sa/ScientificContent/JudicialBlogs/AA1402-1426/Pages/default.aspx',
        "pdf": 'https://www.bog.gov.sa/ScientificContent/JudicialBlogs/AA1402-1426/Documents/المجموعة كاملة (PDF)/%D8%A7%D9%84%D9%85%D8%AC%D9%84%D8%AF%20%D8%A7%D9%84%D8%AB%D8%A7%D9%86%D9%8A%20%D8%B9%D8%B4%D8%B1.pdf',
    },
    'الأحكام_الإدارية_1402-1426هـ/المجلد الحادي عشر': {
        "collection": 'مجموعة الأحكام والمبادئ الإدارية للأعوام 1402-1426 هـ',
        "landing": 'https://www.bog.gov.sa/ScientificContent/JudicialBlogs/AA1402-1426/Pages/default.aspx',
        "pdf": 'https://www.bog.gov.sa/ScientificContent/JudicialBlogs/AA1402-1426/Documents/المجموعة كاملة (PDF)/%D8%A7%D9%84%D9%85%D8%AC%D9%84%D8%AF%20%D8%A7%D9%84%D8%AD%D8%A7%D8%AF%D9%8A%20%D8%B9%D8%B4%D8%B1.pdf',
    },
    'الأحكام_الإدارية_1402-1426هـ/المجلد الخامس': {
        "collection": 'مجموعة الأحكام والمبادئ الإدارية للأعوام 1402-1426 هـ',
        "landing": 'https://www.bog.gov.sa/ScientificContent/JudicialBlogs/AA1402-1426/Pages/default.aspx',
        "pdf": 'https://www.bog.gov.sa/ScientificContent/JudicialBlogs/AA1402-1426/Documents/المجموعة كاملة (PDF)/%D8%A7%D9%84%D9%85%D8%AC%D9%84%D8%AF%20%D8%A7%D9%84%D8%AE%D8%A7%D9%85%D8%B3.pdf',
    },
    'الأحكام_الإدارية_1402-1426هـ/المجلد الخامس عشر': {
        "collection": 'مجموعة الأحكام والمبادئ الإدارية للأعوام 1402-1426 هـ',
        "landing": 'https://www.bog.gov.sa/ScientificContent/JudicialBlogs/AA1402-1426/Pages/default.aspx',
        "pdf": 'https://www.bog.gov.sa/ScientificContent/JudicialBlogs/AA1402-1426/Documents/المجموعة كاملة (PDF)/%D8%A7%D9%84%D9%85%D8%AC%D9%84%D8%AF%20%D8%A7%D9%84%D8%AE%D8%A7%D9%85%D8%B3%20%D8%B9%D8%B4%D8%B1.pdf',
    },
    'الأحكام_الإدارية_1402-1426هـ/المجلد الرابع': {
        "collection": 'مجموعة الأحكام والمبادئ الإدارية للأعوام 1402-1426 هـ',
        "landing": 'https://www.bog.gov.sa/ScientificContent/JudicialBlogs/AA1402-1426/Pages/default.aspx',
        "pdf": 'https://www.bog.gov.sa/ScientificContent/JudicialBlogs/AA1402-1426/Documents/المجموعة كاملة (PDF)/%D8%A7%D9%84%D9%85%D8%AC%D9%84%D8%AF%20%D8%A7%D9%84%D8%B1%D8%A7%D8%A8%D8%B9.pdf',
    },
    'الأحكام_الإدارية_1402-1426هـ/المجلد الرابع عشر': {
        "collection": 'مجموعة الأحكام والمبادئ الإدارية للأعوام 1402-1426 هـ',
        "landing": 'https://www.bog.gov.sa/ScientificContent/JudicialBlogs/AA1402-1426/Pages/default.aspx',
        "pdf": 'https://www.bog.gov.sa/ScientificContent/JudicialBlogs/AA1402-1426/Documents/المجموعة كاملة (PDF)/%D8%A7%D9%84%D9%85%D8%AC%D9%84%D8%AF%20%D8%A7%D9%84%D8%B1%D8%A7%D8%A8%D8%B9%20%D8%B9%D8%B4%D8%B1.pdf',
    },
    'الأحكام_الإدارية_1402-1426هـ/المجلد السابع': {
        "collection": 'مجموعة الأحكام والمبادئ الإدارية للأعوام 1402-1426 هـ',
        "landing": 'https://www.bog.gov.sa/ScientificContent/JudicialBlogs/AA1402-1426/Pages/default.aspx',
        "pdf": 'https://www.bog.gov.sa/ScientificContent/JudicialBlogs/AA1402-1426/Documents/المجموعة كاملة (PDF)/%D8%A7%D9%84%D9%85%D8%AC%D9%84%D8%AF%20%D8%A7%D9%84%D8%B3%D8%A7%D8%A8%D8%B9.pdf',
    },
    'الأحكام_الإدارية_1402-1426هـ/المجلد السابع عشر': {
        "collection": 'مجموعة الأحكام والمبادئ الإدارية للأعوام 1402-1426 هـ',
        "landing": 'https://www.bog.gov.sa/ScientificContent/JudicialBlogs/AA1402-1426/Pages/default.aspx',
        "pdf": 'https://www.bog.gov.sa/ScientificContent/JudicialBlogs/AA1402-1426/Documents/المجموعة كاملة (PDF)/%D8%A7%D9%84%D9%85%D8%AC%D9%84%D8%AF%20%D8%A7%D9%84%D8%B3%D8%A7%D8%A8%D8%B9%20%D8%B9%D8%B4%D8%B1.pdf',
    },
    'الأحكام_الإدارية_1402-1426هـ/المجلد السادس': {
        "collection": 'مجموعة الأحكام والمبادئ الإدارية للأعوام 1402-1426 هـ',
        "landing": 'https://www.bog.gov.sa/ScientificContent/JudicialBlogs/AA1402-1426/Pages/default.aspx',
        "pdf": 'https://www.bog.gov.sa/ScientificContent/JudicialBlogs/AA1402-1426/Documents/المجموعة كاملة (PDF)/%D8%A7%D9%84%D9%85%D8%AC%D9%84%D8%AF%20%D8%A7%D9%84%D8%B3%D8%A7%D8%AF%D8%B3.pdf',
    },
    'الأحكام_الإدارية_1402-1426هـ/المجلد السادس عشر': {
        "collection": 'مجموعة الأحكام والمبادئ الإدارية للأعوام 1402-1426 هـ',
        "landing": 'https://www.bog.gov.sa/ScientificContent/JudicialBlogs/AA1402-1426/Pages/default.aspx',
        "pdf": 'https://www.bog.gov.sa/ScientificContent/JudicialBlogs/AA1402-1426/Documents/المجموعة كاملة (PDF)/%D8%A7%D9%84%D9%85%D8%AC%D9%84%D8%AF%20%D8%A7%D9%84%D8%B3%D8%A7%D8%AF%D8%B3%20%D8%B9%D8%B4%D8%B1.pdf',
    },
    'الأحكام_الإدارية_1402-1426هـ/المجلد العاشر': {
        "collection": 'مجموعة الأحكام والمبادئ الإدارية للأعوام 1402-1426 هـ',
        "landing": 'https://www.bog.gov.sa/ScientificContent/JudicialBlogs/AA1402-1426/Pages/default.aspx',
        "pdf": 'https://www.bog.gov.sa/ScientificContent/JudicialBlogs/AA1402-1426/Documents/المجموعة كاملة (PDF)/%D8%A7%D9%84%D9%85%D8%AC%D9%84%D8%AF%20%D8%A7%D9%84%D8%B9%D8%A7%D8%B4%D8%B1.pdf',
    },
    'الأحكام_الإدارية_1402-1426هـ/المجلد العشرون (الفهارس)': {
        "collection": 'مجموعة الأحكام والمبادئ الإدارية للأعوام 1402-1426 هـ',
        "landing": 'https://www.bog.gov.sa/ScientificContent/JudicialBlogs/AA1402-1426/Pages/default.aspx',
        "pdf": 'https://www.bog.gov.sa/ScientificContent/JudicialBlogs/AA1402-1426/Documents/المجموعة كاملة (PDF)/%D8%A7%D9%84%D9%85%D8%AC%D9%84%D8%AF%20%D8%A7%D9%84%D8%B9%D8%B4%D8%B1%D9%88%D9%86%20%28%D8%A7%D9%84%D9%81%D9%87%D8%A7%D8%B1%D8%B3%29.pdf',
    },
    'الأحكام_الإدارية_1440هـ/Volume_1': {
        "collection": 'مجموعة الأحكام الإدارية لعام 1440 هـ',
        "landing": 'https://www.bog.gov.sa/ScientificContent/JudicialBlogs/1440/Pages/default.aspx',
        "pdf": 'https://www.bog.gov.sa/ScientificContent/JudicialBlogs/1440/Documents/%D8%A7%D9%84%D9%85%D8%AC%D9%85%D9%88%D8%B9%D8%A9%20%D9%83%D8%A7%D9%85%D9%84%D8%A9%20(PDF)/Volume_1.pdf',
    },
    'الأحكام_الإدارية_1440هـ/Volume_2': {
        "collection": 'مجموعة الأحكام الإدارية لعام 1440 هـ',
        "landing": 'https://www.bog.gov.sa/ScientificContent/JudicialBlogs/1440/Pages/default.aspx',
        "pdf": 'https://www.bog.gov.sa/ScientificContent/JudicialBlogs/1440/Documents/%D8%A7%D9%84%D9%85%D8%AC%D9%85%D9%88%D8%B9%D8%A9%20%D9%83%D8%A7%D9%85%D9%84%D8%A9%20(PDF)/Volume_2.pdf',
    },
    'الأحكام_الإدارية_1440هـ/Volume_3': {
        "collection": 'مجموعة الأحكام الإدارية لعام 1440 هـ',
        "landing": 'https://www.bog.gov.sa/ScientificContent/JudicialBlogs/1440/Pages/default.aspx',
        "pdf": 'https://www.bog.gov.sa/ScientificContent/JudicialBlogs/1440/Documents/%D8%A7%D9%84%D9%85%D8%AC%D9%85%D9%88%D8%B9%D8%A9%20%D9%83%D8%A7%D9%85%D9%84%D8%A9%20(PDF)/Volume_3.pdf',
    },
    'الأحكام_الإدارية_1440هـ/Volume_4': {
        "collection": 'مجموعة الأحكام الإدارية لعام 1440 هـ',
        "landing": 'https://www.bog.gov.sa/ScientificContent/JudicialBlogs/1440/Pages/default.aspx',
        "pdf": 'https://www.bog.gov.sa/ScientificContent/JudicialBlogs/1440/Documents/%D8%A7%D9%84%D9%85%D8%AC%D9%85%D9%88%D8%B9%D8%A9%20%D9%83%D8%A7%D9%85%D9%84%D8%A9%20(PDF)/Volume_4.pdf',
    },
    'الأحكام_الإدارية_1440هـ/Volume_6': {
        "collection": 'مجموعة الأحكام الإدارية لعام 1440 هـ',
        "landing": 'https://www.bog.gov.sa/ScientificContent/JudicialBlogs/1440/Pages/default.aspx',
        "pdf": 'https://www.bog.gov.sa/ScientificContent/JudicialBlogs/1440/Documents/%D8%A7%D9%84%D9%85%D8%AC%D9%85%D9%88%D8%B9%D8%A9%20%D9%83%D8%A7%D9%85%D9%84%D8%A9%20(PDF)/Volume_6.pdf',
    },
    'الأحكام_الإدارية_1440هـ/Volume_7': {
        "collection": 'مجموعة الأحكام الإدارية لعام 1440 هـ',
        "landing": 'https://www.bog.gov.sa/ScientificContent/JudicialBlogs/1440/Pages/default.aspx',
        "pdf": 'https://www.bog.gov.sa/ScientificContent/JudicialBlogs/1440/Documents/%D8%A7%D9%84%D9%85%D8%AC%D9%85%D9%88%D8%B9%D8%A9%20%D9%83%D8%A7%D9%85%D9%84%D8%A9%20(PDF)/Volume_7.pdf',
    },
    'الأحكام_الإدارية_1441هـ/Volume_1': {
        "collection": 'مجموعة الأحكام الإدارية لعام 1441 هـ',
        "landing": 'https://www.bog.gov.sa/ScientificContent/JudicialBlogs/1441/Pages/default.aspx',
        "pdf": 'https://www.bog.gov.sa/ScientificContent/JudicialBlogs/1441/Documents/%D8%A7%D9%84%D9%85%D8%AC%D9%85%D9%88%D8%B9%D8%A9%20%D9%83%D8%A7%D9%85%D9%84%D8%A9%20(PDF)/Volume_1.pdf',
    },
    'الأحكام_الإدارية_1441هـ/Volume_2': {
        "collection": 'مجموعة الأحكام الإدارية لعام 1441 هـ',
        "landing": 'https://www.bog.gov.sa/ScientificContent/JudicialBlogs/1441/Pages/default.aspx',
        "pdf": 'https://www.bog.gov.sa/ScientificContent/JudicialBlogs/1441/Documents/%D8%A7%D9%84%D9%85%D8%AC%D9%85%D9%88%D8%B9%D8%A9%20%D9%83%D8%A7%D9%85%D9%84%D8%A9%20(PDF)/Volume_2.pdf',
    },
    'الأحكام_الإدارية_1441هـ/Volume_3': {
        "collection": 'مجموعة الأحكام الإدارية لعام 1441 هـ',
        "landing": 'https://www.bog.gov.sa/ScientificContent/JudicialBlogs/1441/Pages/default.aspx',
        "pdf": 'https://www.bog.gov.sa/ScientificContent/JudicialBlogs/1441/Documents/%D8%A7%D9%84%D9%85%D8%AC%D9%85%D9%88%D8%B9%D8%A9%20%D9%83%D8%A7%D9%85%D9%84%D8%A9%20(PDF)/Volume_3.pdf',
    },
    'الأحكام_الإدارية_1441هـ/Volume_4': {
        "collection": 'مجموعة الأحكام الإدارية لعام 1441 هـ',
        "landing": 'https://www.bog.gov.sa/ScientificContent/JudicialBlogs/1441/Pages/default.aspx',
        "pdf": 'https://www.bog.gov.sa/ScientificContent/JudicialBlogs/1441/Documents/%D8%A7%D9%84%D9%85%D8%AC%D9%85%D9%88%D8%B9%D8%A9%20%D9%83%D8%A7%D9%85%D9%84%D8%A9%20(PDF)/Volume_4.pdf',
    },
    'الأحكام_الإدارية_1442هـ/Volume_1': {
        "collection": 'مجموعة الأحكام الإدارية لعام 1442 هـ',
        "landing": 'https://www.bog.gov.sa/ScientificContent/JudicialBlogs/1442/Pages/default.aspx',
        "pdf": 'https://www.bog.gov.sa/ScientificContent/JudicialBlogs/1442/Documents/%D8%A7%D9%84%D9%85%D8%AC%D9%85%D9%88%D8%B9%D8%A9%20%D9%83%D8%A7%D9%85%D9%84%D8%A9%20(PDF)/Volume_1.pdf',
    },
    'الأحكام_الإدارية_1442هـ/Volume_2': {
        "collection": 'مجموعة الأحكام الإدارية لعام 1442 هـ',
        "landing": 'https://www.bog.gov.sa/ScientificContent/JudicialBlogs/1442/Pages/default.aspx',
        "pdf": 'https://www.bog.gov.sa/ScientificContent/JudicialBlogs/1442/Documents/%D8%A7%D9%84%D9%85%D8%AC%D9%85%D9%88%D8%B9%D8%A9%20%D9%83%D8%A7%D9%85%D9%84%D8%A9%20(PDF)/Volume_2.pdf',
    },
    'الأحكام_الإدارية_1442هـ/Volume_3': {
        "collection": 'مجموعة الأحكام الإدارية لعام 1442 هـ',
        "landing": 'https://www.bog.gov.sa/ScientificContent/JudicialBlogs/1442/Pages/default.aspx',
        "pdf": 'https://www.bog.gov.sa/ScientificContent/JudicialBlogs/1442/Documents/%D8%A7%D9%84%D9%85%D8%AC%D9%85%D9%88%D8%B9%D8%A9%20%D9%83%D8%A7%D9%85%D9%84%D8%A9%20(PDF)/Volume_3.pdf',
    },
    'الأحكام_الإدارية_1442هـ/Volume_4': {
        "collection": 'مجموعة الأحكام الإدارية لعام 1442 هـ',
        "landing": 'https://www.bog.gov.sa/ScientificContent/JudicialBlogs/1442/Pages/default.aspx',
        "pdf": 'https://www.bog.gov.sa/ScientificContent/JudicialBlogs/1442/Documents/%D8%A7%D9%84%D9%85%D8%AC%D9%85%D9%88%D8%B9%D8%A9%20%D9%83%D8%A7%D9%85%D9%84%D8%A9%20(PDF)/Volume_4.pdf',
    },
    'الأحكام_الإدارية_1443هـ/Volume_1': {
        "collection": 'مجموعة الأحكام الإدارية لعام 1443 هـ',
        "landing": 'https://www.bog.gov.sa/ScientificContent/JudicialBlogs/1443/Pages/default.aspx',
        "pdf": 'https://www.bog.gov.sa/ScientificContent/JudicialBlogs/1443/Documents/%D8%A7%D9%84%D9%85%D8%AC%D9%85%D9%88%D8%B9%D8%A9%20%D9%83%D8%A7%D9%85%D9%84%D8%A9%20(PDF)/Volume_1.pdf',
    },
    'الأحكام_الإدارية_1443هـ/Volume_2': {
        "collection": 'مجموعة الأحكام الإدارية لعام 1443 هـ',
        "landing": 'https://www.bog.gov.sa/ScientificContent/JudicialBlogs/1443/Pages/default.aspx',
        "pdf": 'https://www.bog.gov.sa/ScientificContent/JudicialBlogs/1443/Documents/%D8%A7%D9%84%D9%85%D8%AC%D9%85%D9%88%D8%B9%D8%A9%20%D9%83%D8%A7%D9%85%D9%84%D8%A9%20(PDF)/Volume_2.pdf',
    },
    'الأحكام_الإدارية_1443هـ/Volume_3': {
        "collection": 'مجموعة الأحكام الإدارية لعام 1443 هـ',
        "landing": 'https://www.bog.gov.sa/ScientificContent/JudicialBlogs/1443/Pages/default.aspx',
        "pdf": 'https://www.bog.gov.sa/ScientificContent/JudicialBlogs/1443/Documents/%D8%A7%D9%84%D9%85%D8%AC%D9%85%D9%88%D8%B9%D8%A9%20%D9%83%D8%A7%D9%85%D9%84%D8%A9%20(PDF)/Volume_3.pdf',
    },
    'الأحكام_الإدارية_1443هـ/Volume_4': {
        "collection": 'مجموعة الأحكام الإدارية لعام 1443 هـ',
        "landing": 'https://www.bog.gov.sa/ScientificContent/JudicialBlogs/1443/Pages/default.aspx',
        "pdf": 'https://www.bog.gov.sa/ScientificContent/JudicialBlogs/1443/Documents/%D8%A7%D9%84%D9%85%D8%AC%D9%85%D9%88%D8%B9%D8%A9%20%D9%83%D8%A7%D9%85%D9%84%D8%A9%20(PDF)/Volume_4.pdf',
    },
    'الأحكام_الإدارية_1444هـ/Volume_1': {
        "collection": 'مجموعة الأحكام الإدارية لعام 1444 هـ',
        "landing": 'https://www.bog.gov.sa/ScientificContent/JudicialBlogs/1444/Pages/default.aspx',
        "pdf": 'https://www.bog.gov.sa/ScientificContent/JudicialBlogs/1444/Documents/%D8%A7%D9%84%D9%85%D8%AC%D9%85%D9%88%D8%B9%D8%A9%20%D8%A7%D9%84%D9%83%D8%A7%D9%85%D9%84%D8%A9%20(PDF)/Volume_1.pdf',
    },
    'الأحكام_الإدارية_1444هـ/Volume_2': {
        "collection": 'مجموعة الأحكام الإدارية لعام 1444 هـ',
        "landing": 'https://www.bog.gov.sa/ScientificContent/JudicialBlogs/1444/Pages/default.aspx',
        "pdf": 'https://www.bog.gov.sa/ScientificContent/JudicialBlogs/1444/Documents/%D8%A7%D9%84%D9%85%D8%AC%D9%85%D9%88%D8%B9%D8%A9%20%D8%A7%D9%84%D9%83%D8%A7%D9%85%D9%84%D8%A9%20(PDF)/Volume_2.pdf',
    },
    'الأحكام_التجارية_1424-1427هـ/المجلد الأول': {
        "collection": 'مجموعة الأحكام والمبادئ التجارية للأعوام 1424 -1427 هـ',
        "landing": 'https://www.bog.gov.sa/ScientificContent/JudicialBlogs/1424-1427/Pages/default.aspx',
        "pdf": 'https://www.bog.gov.sa/ScientificContent/JudicialBlogs/1424-1427/Documents/المجموعة كاملة (PDF)/%D8%A7%D9%84%D9%85%D8%AC%D9%84%D8%AF%20%D8%A7%D9%84%D8%A3%D9%88%D9%84.pdf',
    },
    'الأحكام_التجارية_1424-1427هـ/المجلد الثالث': {
        "collection": 'مجموعة الأحكام والمبادئ التجارية للأعوام 1424 -1427 هـ',
        "landing": 'https://www.bog.gov.sa/ScientificContent/JudicialBlogs/1424-1427/Pages/default.aspx',
        "pdf": 'https://www.bog.gov.sa/ScientificContent/JudicialBlogs/1424-1427/Documents/المجموعة كاملة (PDF)/%D8%A7%D9%84%D9%85%D8%AC%D9%84%D8%AF%20%D8%A7%D9%84%D8%AB%D8%A7%D9%84%D8%AB.pdf',
    },
    'الأحكام_التجارية_1424-1427هـ/المجلد الثاني': {
        "collection": 'مجموعة الأحكام والمبادئ التجارية للأعوام 1424 -1427 هـ',
        "landing": 'https://www.bog.gov.sa/ScientificContent/JudicialBlogs/1424-1427/Pages/default.aspx',
        "pdf": 'https://www.bog.gov.sa/ScientificContent/JudicialBlogs/1424-1427/Documents/المجموعة كاملة (PDF)/%D8%A7%D9%84%D9%85%D8%AC%D9%84%D8%AF%20%D8%A7%D9%84%D8%AB%D8%A7%D9%86%D9%8A.pdf',
    },
    'الأحكام_التجارية_1424-1427هـ/المجلد الخامس': {
        "collection": 'مجموعة الأحكام والمبادئ التجارية للأعوام 1424 -1427 هـ',
        "landing": 'https://www.bog.gov.sa/ScientificContent/JudicialBlogs/1424-1427/Pages/default.aspx',
        "pdf": 'https://www.bog.gov.sa/ScientificContent/JudicialBlogs/1424-1427/Documents/المجموعة كاملة (PDF)/%D8%A7%D9%84%D9%85%D8%AC%D9%84%D8%AF%20%D8%A7%D9%84%D8%AE%D8%A7%D9%85%D8%B3.pdf',
    },
    'الأحكام_التجارية_1424-1427هـ/المجلد الرابع': {
        "collection": 'مجموعة الأحكام والمبادئ التجارية للأعوام 1424 -1427 هـ',
        "landing": 'https://www.bog.gov.sa/ScientificContent/JudicialBlogs/1424-1427/Pages/default.aspx',
        "pdf": 'https://www.bog.gov.sa/ScientificContent/JudicialBlogs/1424-1427/Documents/المجموعة كاملة (PDF)/%D8%A7%D9%84%D9%85%D8%AC%D9%84%D8%AF%20%D8%A7%D9%84%D8%B1%D8%A7%D8%A8%D8%B9.pdf',
    },
    'الأحكام_التجارية_1428هـ/مجموعة الاحكام الادارية - الجزء 1': {
        "collection": 'مجموعة الأحكام والمبادئ لعام 1428 هـ',
        "landing": 'https://www.bog.gov.sa/ScientificContent/JudicialBlogs/Blog1428/Pages/default.aspx',
        "pdf": '',
    },
    'الأحكام_التجارية_1428هـ/مجموعة الاحكام الادارية - الجزء 2': {
        "collection": 'مجموعة الأحكام والمبادئ لعام 1428 هـ',
        "landing": 'https://www.bog.gov.sa/ScientificContent/JudicialBlogs/Blog1428/Pages/default.aspx',
        "pdf": 'https://www.bog.gov.sa/ScientificContent/JudicialBlogs/Blog1428/Documents/المجموعة كاملة (PDF)/%D9%85%D8%AC%D9%85%D9%88%D8%B9%D8%A9%20%D8%A7%D9%84%D8%A7%D8%AD%D9%83%D8%A7%D9%85%20%D8%A7%D9%84%D8%A7%D8%AF%D8%A7%D8%B1%D9%8A%D8%A9%20-%20%D8%A7%D9%84%D8%AC%D8%B2%D8%A1%202.pdf',
    },
    'الأحكام_التجارية_1428هـ/مجموعة الاحكام الادارية - الجزء 3': {
        "collection": 'مجموعة الأحكام والمبادئ لعام 1428 هـ',
        "landing": 'https://www.bog.gov.sa/ScientificContent/JudicialBlogs/Blog1428/Pages/default.aspx',
        "pdf": 'https://www.bog.gov.sa/ScientificContent/JudicialBlogs/Blog1428/Documents/المجموعة كاملة (PDF)/%D9%85%D8%AC%D9%85%D9%88%D8%B9%D8%A9%20%D8%A7%D9%84%D8%A7%D8%AD%D9%83%D8%A7%D9%85%20%D8%A7%D9%84%D8%A7%D8%AF%D8%A7%D8%B1%D9%8A%D8%A9%20-%20%D8%A7%D9%84%D8%AC%D8%B2%D8%A1%203.pdf',
    },
    'الأحكام_التجارية_1428هـ/مجموعة الاحكام الادارية - الجزء 4': {
        "collection": 'مجموعة الأحكام والمبادئ لعام 1428 هـ',
        "landing": 'https://www.bog.gov.sa/ScientificContent/JudicialBlogs/Blog1428/Pages/default.aspx',
        "pdf": 'https://www.bog.gov.sa/ScientificContent/JudicialBlogs/Blog1428/Documents/المجموعة كاملة (PDF)/%D9%85%D8%AC%D9%85%D9%88%D8%B9%D8%A9%20%D8%A7%D9%84%D8%A7%D8%AD%D9%83%D8%A7%D9%85%20%D8%A7%D9%84%D8%A7%D8%AF%D8%A7%D8%B1%D9%8A%D8%A9%20-%20%D8%A7%D9%84%D8%AC%D8%B2%D8%A1%204.pdf',
    },
    'الأحكام_التجارية_1428هـ/مجموعة الاحكام الادارية - الجزء 5': {
        "collection": 'مجموعة الأحكام والمبادئ لعام 1428 هـ',
        "landing": 'https://www.bog.gov.sa/ScientificContent/JudicialBlogs/Blog1428/Pages/default.aspx',
        "pdf": 'https://www.bog.gov.sa/ScientificContent/JudicialBlogs/Blog1428/Documents/المجموعة كاملة (PDF)/%D9%85%D8%AC%D9%85%D9%88%D8%B9%D8%A9%20%D8%A7%D9%84%D8%A7%D8%AD%D9%83%D8%A7%D9%85%20%D8%A7%D9%84%D8%A7%D8%AF%D8%A7%D8%B1%D9%8A%D8%A9%20-%20%D8%A7%D9%84%D8%AC%D8%B2%D8%A1%205.pdf',
    },
    'الأحكام_التجارية_1428هـ/مجموعة الاحكام الادارية - الجزء 6': {
        "collection": 'مجموعة الأحكام والمبادئ لعام 1428 هـ',
        "landing": 'https://www.bog.gov.sa/ScientificContent/JudicialBlogs/Blog1428/Pages/default.aspx',
        "pdf": 'https://www.bog.gov.sa/ScientificContent/JudicialBlogs/Blog1428/Documents/المجموعة كاملة (PDF)/%D9%85%D8%AC%D9%85%D9%88%D8%B9%D8%A9%20%D8%A7%D9%84%D8%A7%D8%AD%D9%83%D8%A7%D9%85%20%D8%A7%D9%84%D8%A7%D8%AF%D8%A7%D8%B1%D9%8A%D8%A9%20-%20%D8%A7%D9%84%D8%AC%D8%B2%D8%A1%206.pdf',
    },
    'الأحكام_التجارية_1428هـ/مجموعة الاحكام التجارية - الجزء 1': {
        "collection": 'مجموعة الأحكام والمبادئ لعام 1428 هـ',
        "landing": 'https://www.bog.gov.sa/ScientificContent/JudicialBlogs/Blog1428/Pages/default.aspx',
        "pdf": 'https://www.bog.gov.sa/ScientificContent/JudicialBlogs/Blog1428/Documents/المجموعة كاملة (PDF)/%D9%85%D8%AC%D9%85%D9%88%D8%B9%D8%A9%20%D8%A7%D9%84%D8%A7%D8%AD%D9%83%D8%A7%D9%85%20%D8%A7%D9%84%D8%AA%D8%AC%D8%A7%D8%B1%D9%8A%D8%A9%20-%20%D8%A7%D9%84%D8%AC%D8%B2%D8%A1%201.pdf',
    },
    'الأحكام_التجارية_1428هـ/مجموعة الاحكام التجارية - الجزء 2': {
        "collection": 'مجموعة الأحكام والمبادئ لعام 1428 هـ',
        "landing": 'https://www.bog.gov.sa/ScientificContent/JudicialBlogs/Blog1428/Pages/default.aspx',
        "pdf": 'https://www.bog.gov.sa/ScientificContent/JudicialBlogs/Blog1428/Documents/المجموعة كاملة (PDF)/%D9%85%D8%AC%D9%85%D9%88%D8%B9%D8%A9%20%D8%A7%D9%84%D8%A7%D8%AD%D9%83%D8%A7%D9%85%20%D8%A7%D9%84%D8%AA%D8%AC%D8%A7%D8%B1%D9%8A%D8%A9%20-%20%D8%A7%D9%84%D8%AC%D8%B2%D8%A1%202.pdf',
    },
    'الأحكام_التجارية_1428هـ/مجموعة الاحكام الجزائية - الجزء 1': {
        "collection": 'مجموعة الأحكام والمبادئ لعام 1428 هـ',
        "landing": 'https://www.bog.gov.sa/ScientificContent/JudicialBlogs/Blog1428/Pages/default.aspx',
        "pdf": '',
    },
    'الأحكام_التجارية_1428هـ/مجموعة الاحكام الجزائية - الجزء 2': {
        "collection": 'مجموعة الأحكام والمبادئ لعام 1428 هـ',
        "landing": 'https://www.bog.gov.sa/ScientificContent/JudicialBlogs/Blog1428/Pages/default.aspx',
        "pdf": 'https://www.bog.gov.sa/ScientificContent/JudicialBlogs/Blog1428/Documents/المجموعة كاملة (PDF)/%D9%85%D8%AC%D9%85%D9%88%D8%B9%D8%A9%20%D8%A7%D9%84%D8%A7%D8%AD%D9%83%D8%A7%D9%85%20%D8%A7%D9%84%D8%AC%D8%B2%D8%A7%D8%A6%D9%8A%D8%A9%20-%20%D8%A7%D9%84%D8%AC%D8%B2%D8%A1%202.pdf',
    },
    'الأحكام_التجارية_1429هـ/المجلد الأول-إداري': {
        "collection": 'مجموعة الأحكام والمبادئ لعام 1429 هـ',
        "landing": 'https://www.bog.gov.sa/ScientificContent/JudicialBlogs/1429/Pages/default1.aspx',
        "pdf": 'https://www.bog.gov.sa/ScientificContent/JudicialBlogs/1429/Documents/المجموعة كاملة (PDF)/%D8%A7%D9%84%D9%85%D8%AC%D9%84%D8%AF%20%D8%A7%D9%84%D8%A3%D9%88%D9%84-%D8%A5%D8%AF%D8%A7%D8%B1%D9%8A.pdf',
    },
    'الأحكام_التجارية_1429هـ/المجلد الاول-تجاري': {
        "collection": 'مجموعة الأحكام والمبادئ لعام 1429 هـ',
        "landing": 'https://www.bog.gov.sa/ScientificContent/JudicialBlogs/1429/Pages/default1.aspx',
        "pdf": 'https://www.bog.gov.sa/ScientificContent/JudicialBlogs/1429/Documents/المجموعة كاملة (PDF)/%D8%A7%D9%84%D9%85%D8%AC%D9%84%D8%AF%20%D8%A7%D9%84%D8%A7%D9%88%D9%84-%D8%AA%D8%AC%D8%A7%D8%B1%D9%8A.pdf',
    },
    'الأحكام_التجارية_1429هـ/المجلد الاول-جزائي': {
        "collection": 'مجموعة الأحكام والمبادئ لعام 1429 هـ',
        "landing": 'https://www.bog.gov.sa/ScientificContent/JudicialBlogs/1429/Pages/default1.aspx',
        "pdf": 'https://www.bog.gov.sa/ScientificContent/JudicialBlogs/1429/Documents/المجموعة كاملة (PDF)/%D8%A7%D9%84%D9%85%D8%AC%D9%84%D8%AF%20%D8%A7%D9%84%D8%A7%D9%88%D9%84-%D8%AC%D8%B2%D8%A7%D8%A6%D9%8A.pdf',
    },
    'الأحكام_التجارية_1429هـ/المجلد الثالث-إداري': {
        "collection": 'مجموعة الأحكام والمبادئ لعام 1429 هـ',
        "landing": 'https://www.bog.gov.sa/ScientificContent/JudicialBlogs/1429/Pages/default1.aspx',
        "pdf": 'https://www.bog.gov.sa/ScientificContent/JudicialBlogs/1429/Documents/المجموعة كاملة (PDF)/%D8%A7%D9%84%D9%85%D8%AC%D9%84%D8%AF%20%D8%A7%D9%84%D8%AB%D8%A7%D9%84%D8%AB-%D8%A5%D8%AF%D8%A7%D8%B1%D9%8A.pdf',
    },
    'الأحكام_التجارية_1429هـ/المجلد الثاني-إداري': {
        "collection": 'مجموعة الأحكام والمبادئ لعام 1429 هـ',
        "landing": 'https://www.bog.gov.sa/ScientificContent/JudicialBlogs/1429/Pages/default1.aspx',
        "pdf": 'https://www.bog.gov.sa/ScientificContent/JudicialBlogs/1429/Documents/المجموعة كاملة (PDF)/%D8%A7%D9%84%D9%85%D8%AC%D9%84%D8%AF%20%D8%A7%D9%84%D8%AB%D8%A7%D9%86%D9%8A-%D8%A5%D8%AF%D8%A7%D8%B1%D9%8A.pdf',
    },
    'الأحكام_التجارية_1429هـ/المجلد الثاني-تجاري': {
        "collection": 'مجموعة الأحكام والمبادئ لعام 1429 هـ',
        "landing": 'https://www.bog.gov.sa/ScientificContent/JudicialBlogs/1429/Pages/default1.aspx',
        "pdf": '',
    },
    'الأحكام_التجارية_1429هـ/المجلد الثاني-جزائي': {
        "collection": 'مجموعة الأحكام والمبادئ لعام 1429 هـ',
        "landing": 'https://www.bog.gov.sa/ScientificContent/JudicialBlogs/1429/Pages/default1.aspx',
        "pdf": 'https://www.bog.gov.sa/ScientificContent/JudicialBlogs/1429/Documents/المجموعة كاملة (PDF)/%D8%A7%D9%84%D9%85%D8%AC%D9%84%D8%AF%20%D8%A7%D9%84%D8%AB%D8%A7%D9%86%D9%8A-%D8%AC%D8%B2%D8%A7%D8%A6%D9%8A.pdf',
    },
    'الأحكام_التجارية_1429هـ/المجلد الخامس-إداري': {
        "collection": 'مجموعة الأحكام والمبادئ لعام 1429 هـ',
        "landing": 'https://www.bog.gov.sa/ScientificContent/JudicialBlogs/1429/Pages/default1.aspx',
        "pdf": 'https://www.bog.gov.sa/ScientificContent/JudicialBlogs/1429/Documents/المجموعة كاملة (PDF)/%D8%A7%D9%84%D9%85%D8%AC%D9%84%D8%AF%20%D8%A7%D9%84%D8%AE%D8%A7%D9%85%D8%B3-%D8%A5%D8%AF%D8%A7%D8%B1%D9%8A.pdf',
    },
    'الأحكام_التجارية_1429هـ/المجلد الرابع-إداري': {
        "collection": 'مجموعة الأحكام والمبادئ لعام 1429 هـ',
        "landing": 'https://www.bog.gov.sa/ScientificContent/JudicialBlogs/1429/Pages/default1.aspx',
        "pdf": 'https://www.bog.gov.sa/ScientificContent/JudicialBlogs/1429/Documents/المجموعة كاملة (PDF)/%D8%A7%D9%84%D9%85%D8%AC%D9%84%D8%AF%20%D8%A7%D9%84%D8%B1%D8%A7%D8%A8%D8%B9-%D8%A5%D8%AF%D8%A7%D8%B1%D9%8A.pdf',
    },
    'الأحكام_التجارية_1429هـ/المجلد السادس-إداري': {
        "collection": 'مجموعة الأحكام والمبادئ لعام 1429 هـ',
        "landing": 'https://www.bog.gov.sa/ScientificContent/JudicialBlogs/1429/Pages/default1.aspx',
        "pdf": 'https://www.bog.gov.sa/ScientificContent/JudicialBlogs/1429/Documents/المجموعة كاملة (PDF)/%D8%A7%D9%84%D9%85%D8%AC%D9%84%D8%AF%20%D8%A7%D9%84%D8%B3%D8%A7%D8%AF%D8%B3-%D8%A5%D8%AF%D8%A7%D8%B1%D9%8A.pdf',
    },
    'الأحكام_التجارية_1430هـ/الاداري 1430 الجزء الأول': {
        "collection": 'مجموعة الأحكام والمبادئ لعام 1430 هـ',
        "landing": 'https://www.bog.gov.sa/ScientificContent/JudicialBlogs/1430/Pages/default.aspx',
        "pdf": 'https://www.bog.gov.sa/ScientificContent/JudicialBlogs/1430/Documents/المجموعة كاملة (PDF)/%D8%A7%D9%84%D8%A7%D8%AF%D8%A7%D8%B1%D9%8A%201430%20%D8%A7%D9%84%D8%AC%D8%B2%D8%A1%20%D8%A7%D9%84%D8%A3%D9%88%D9%84.pdf',
    },
    'الأحكام_التجارية_1430هـ/الاداري 1430 الجزء الثالث': {
        "collection": 'مجموعة الأحكام والمبادئ لعام 1430 هـ',
        "landing": 'https://www.bog.gov.sa/ScientificContent/JudicialBlogs/1430/Pages/default.aspx',
        "pdf": 'https://www.bog.gov.sa/ScientificContent/JudicialBlogs/1430/Documents/المجموعة كاملة (PDF)/%D8%A7%D9%84%D8%A7%D8%AF%D8%A7%D8%B1%D9%8A%201430%20%D8%A7%D9%84%D8%AC%D8%B2%D8%A1%20%D8%A7%D9%84%D8%AB%D8%A7%D9%84%D8%AB.pdf',
    },
    'الأحكام_التجارية_1430هـ/الاداري 1430 الجزء الثاني': {
        "collection": 'مجموعة الأحكام والمبادئ لعام 1430 هـ',
        "landing": 'https://www.bog.gov.sa/ScientificContent/JudicialBlogs/1430/Pages/default.aspx',
        "pdf": 'https://www.bog.gov.sa/ScientificContent/JudicialBlogs/1430/Documents/المجموعة كاملة (PDF)/%D8%A7%D9%84%D8%A7%D8%AF%D8%A7%D8%B1%D9%8A%201430%20%D8%A7%D9%84%D8%AC%D8%B2%D8%A1%20%D8%A7%D9%84%D8%AB%D8%A7%D9%86%D9%8A.pdf',
    },
    'الأحكام_التجارية_1430هـ/الاداري 1430 الجزء الخامس': {
        "collection": 'مجموعة الأحكام والمبادئ لعام 1430 هـ',
        "landing": 'https://www.bog.gov.sa/ScientificContent/JudicialBlogs/1430/Pages/default.aspx',
        "pdf": 'https://www.bog.gov.sa/ScientificContent/JudicialBlogs/1430/Documents/المجموعة كاملة (PDF)/%D8%A7%D9%84%D8%A7%D8%AF%D8%A7%D8%B1%D9%8A%201430%20%D8%A7%D9%84%D8%AC%D8%B2%D8%A1%20%D8%A7%D9%84%D8%AE%D8%A7%D9%85%D8%B3.pdf',
    },
    'الأحكام_التجارية_1430هـ/الاداري 1430 الجزء الرابع': {
        "collection": 'مجموعة الأحكام والمبادئ لعام 1430 هـ',
        "landing": 'https://www.bog.gov.sa/ScientificContent/JudicialBlogs/1430/Pages/default.aspx',
        "pdf": 'https://www.bog.gov.sa/ScientificContent/JudicialBlogs/1430/Documents/المجموعة كاملة (PDF)/%D8%A7%D9%84%D8%A7%D8%AF%D8%A7%D8%B1%D9%8A%201430%20%D8%A7%D9%84%D8%AC%D8%B2%D8%A1%20%D8%A7%D9%84%D8%B1%D8%A7%D8%A8%D8%B9.pdf',
    },
    'الأحكام_التجارية_1430هـ/الاداري 1430 الجزء السادس': {
        "collection": 'مجموعة الأحكام والمبادئ لعام 1430 هـ',
        "landing": 'https://www.bog.gov.sa/ScientificContent/JudicialBlogs/1430/Pages/default.aspx',
        "pdf": 'https://www.bog.gov.sa/ScientificContent/JudicialBlogs/1430/Documents/المجموعة كاملة (PDF)/%D8%A7%D9%84%D8%A7%D8%AF%D8%A7%D8%B1%D9%8A%201430%20%D8%A7%D9%84%D8%AC%D8%B2%D8%A1%20%D8%A7%D9%84%D8%B3%D8%A7%D8%AF%D8%B3.pdf',
    },
    'الأحكام_التجارية_1430هـ/التجاري 1430 الجزء الأول': {
        "collection": 'مجموعة الأحكام والمبادئ لعام 1430 هـ',
        "landing": 'https://www.bog.gov.sa/ScientificContent/JudicialBlogs/1430/Pages/default.aspx',
        "pdf": 'https://www.bog.gov.sa/ScientificContent/JudicialBlogs/1430/Documents/المجموعة كاملة (PDF)/%D8%A7%D9%84%D8%AA%D8%AC%D8%A7%D8%B1%D9%8A%201430%20%D8%A7%D9%84%D8%AC%D8%B2%D8%A1%20%D8%A7%D9%84%D8%A3%D9%88%D9%84.pdf',
    },
    'الأحكام_التجارية_1430هـ/التجاري 1430 الجزء الثالث': {
        "collection": 'مجموعة الأحكام والمبادئ لعام 1430 هـ',
        "landing": 'https://www.bog.gov.sa/ScientificContent/JudicialBlogs/1430/Pages/default.aspx',
        "pdf": '',
    },
    'الأحكام_التجارية_1430هـ/التجاري 1430 الجزء الثاني': {
        "collection": 'مجموعة الأحكام والمبادئ لعام 1430 هـ',
        "landing": 'https://www.bog.gov.sa/ScientificContent/JudicialBlogs/1430/Pages/default.aspx',
        "pdf": 'https://www.bog.gov.sa/ScientificContent/JudicialBlogs/1430/Documents/المجموعة كاملة (PDF)/%D8%A7%D9%84%D8%AA%D8%AC%D8%A7%D8%B1%D9%8A%201430%20%D8%A7%D9%84%D8%AC%D8%B2%D8%A1%20%D8%A7%D9%84%D8%AB%D8%A7%D9%86%D9%8A.pdf',
    },
    'الأحكام_التجارية_1430هـ/الجزائي 1430 الجزء الأول': {
        "collection": 'مجموعة الأحكام والمبادئ لعام 1430 هـ',
        "landing": 'https://www.bog.gov.sa/ScientificContent/JudicialBlogs/1430/Pages/default.aspx',
        "pdf": '',
    },
    'الأحكام_التجارية_1430هـ/الجزائي 1430 الجزء الثاني': {
        "collection": 'مجموعة الأحكام والمبادئ لعام 1430 هـ',
        "landing": 'https://www.bog.gov.sa/ScientificContent/JudicialBlogs/1430/Pages/default.aspx',
        "pdf": 'https://www.bog.gov.sa/ScientificContent/JudicialBlogs/1430/Documents/المجموعة كاملة (PDF)/%D8%A7%D9%84%D8%AC%D8%B2%D8%A7%D8%A6%D9%8A%201430%20%D8%A7%D9%84%D8%AC%D8%B2%D8%A1%20%D8%A7%D9%84%D8%AB%D8%A7%D9%86%D9%8A.pdf',
    },
    'الأحكام_التجارية_1431هـ/الاداري 1431 الجزء الأول': {
        "collection": 'مجموعة الأحكام والمبادئ لعام 1431 هـ',
        "landing": 'https://www.bog.gov.sa/ScientificContent/JudicialBlogs/1431/Pages/default.aspx',
        "pdf": 'https://www.bog.gov.sa/ScientificContent/JudicialBlogs/1431/Documents/المجموعة كاملة (PDF)/%D8%A7%D9%84%D8%A7%D8%AF%D8%A7%D8%B1%D9%8A%201431%20%D8%A7%D9%84%D8%AC%D8%B2%D8%A1%20%D8%A7%D9%84%D8%A3%D9%88%D9%84.pdf',
    },
    'الأحكام_التجارية_1431هـ/الاداري 1431 الجزء الثالث': {
        "collection": 'مجموعة الأحكام والمبادئ لعام 1431 هـ',
        "landing": 'https://www.bog.gov.sa/ScientificContent/JudicialBlogs/1431/Pages/default.aspx',
        "pdf": 'https://www.bog.gov.sa/ScientificContent/JudicialBlogs/1431/Documents/المجموعة كاملة (PDF)/%D8%A7%D9%84%D8%A7%D8%AF%D8%A7%D8%B1%D9%8A%201431%20%D8%A7%D9%84%D8%AC%D8%B2%D8%A1%20%D8%A7%D9%84%D8%AB%D8%A7%D9%84%D8%AB.pdf',
    },
    'الأحكام_التجارية_1431هـ/الاداري 1431 الجزء الثاني': {
        "collection": 'مجموعة الأحكام والمبادئ لعام 1431 هـ',
        "landing": 'https://www.bog.gov.sa/ScientificContent/JudicialBlogs/1431/Pages/default.aspx',
        "pdf": 'https://www.bog.gov.sa/ScientificContent/JudicialBlogs/1431/Documents/المجموعة كاملة (PDF)/%D8%A7%D9%84%D8%A7%D8%AF%D8%A7%D8%B1%D9%8A%201431%20%D8%A7%D9%84%D8%AC%D8%B2%D8%A1%20%D8%A7%D9%84%D8%AB%D8%A7%D9%86%D9%8A.pdf',
    },
    'الأحكام_التجارية_1431هـ/الاداري 1431 الجزء الخامس': {
        "collection": 'مجموعة الأحكام والمبادئ لعام 1431 هـ',
        "landing": 'https://www.bog.gov.sa/ScientificContent/JudicialBlogs/1431/Pages/default.aspx',
        "pdf": '',
    },
    'الأحكام_التجارية_1431هـ/الاداري 1431 الجزء الرابع': {
        "collection": 'مجموعة الأحكام والمبادئ لعام 1431 هـ',
        "landing": 'https://www.bog.gov.sa/ScientificContent/JudicialBlogs/1431/Pages/default.aspx',
        "pdf": 'https://www.bog.gov.sa/ScientificContent/JudicialBlogs/1431/Documents/المجموعة كاملة (PDF)/%D8%A7%D9%84%D8%A7%D8%AF%D8%A7%D8%B1%D9%8A%201431%20%D8%A7%D9%84%D8%AC%D8%B2%D8%A1%20%D8%A7%D9%84%D8%B1%D8%A7%D8%A8%D8%B9.pdf',
    },
    'الأحكام_التجارية_1431هـ/الاداري 1431 الجزء السادس': {
        "collection": 'مجموعة الأحكام والمبادئ لعام 1431 هـ',
        "landing": 'https://www.bog.gov.sa/ScientificContent/JudicialBlogs/1431/Pages/default.aspx',
        "pdf": 'https://www.bog.gov.sa/ScientificContent/JudicialBlogs/1431/Documents/المجموعة كاملة (PDF)/%D8%A7%D9%84%D8%A7%D8%AF%D8%A7%D8%B1%D9%8A%201431%20%D8%A7%D9%84%D8%AC%D8%B2%D8%A1%20%D8%A7%D9%84%D8%B3%D8%A7%D8%AF%D8%B3.pdf',
    },
    'الأحكام_التجارية_1431هـ/التجاري 1431 الجزء الأول': {
        "collection": 'مجموعة الأحكام والمبادئ لعام 1431 هـ',
        "landing": 'https://www.bog.gov.sa/ScientificContent/JudicialBlogs/1431/Pages/default.aspx',
        "pdf": 'https://www.bog.gov.sa/ScientificContent/JudicialBlogs/1431/Documents/المجموعة كاملة (PDF)/%D8%A7%D9%84%D8%AA%D8%AC%D8%A7%D8%B1%D9%8A%201431%20%D8%A7%D9%84%D8%AC%D8%B2%D8%A1%20%D8%A7%D9%84%D8%A3%D9%88%D9%84.pdf',
    },
    'الأحكام_التجارية_1431هـ/التجاري 1431 الجزء الثالث': {
        "collection": 'مجموعة الأحكام والمبادئ لعام 1431 هـ',
        "landing": 'https://www.bog.gov.sa/ScientificContent/JudicialBlogs/1431/Pages/default.aspx',
        "pdf": 'https://www.bog.gov.sa/ScientificContent/JudicialBlogs/1431/Documents/المجموعة كاملة (PDF)/%D8%A7%D9%84%D8%AA%D8%AC%D8%A7%D8%B1%D9%8A%201431%20%D8%A7%D9%84%D8%AC%D8%B2%D8%A1%20%D8%A7%D9%84%D8%AB%D8%A7%D9%84%D8%AB.pdf',
    },
    'الأحكام_التجارية_1431هـ/التجاري 1431 الجزء الثاني': {
        "collection": 'مجموعة الأحكام والمبادئ لعام 1431 هـ',
        "landing": 'https://www.bog.gov.sa/ScientificContent/JudicialBlogs/1431/Pages/default.aspx',
        "pdf": 'https://www.bog.gov.sa/ScientificContent/JudicialBlogs/1431/Documents/المجموعة كاملة (PDF)/%D8%A7%D9%84%D8%AA%D8%AC%D8%A7%D8%B1%D9%8A%201431%20%D8%A7%D9%84%D8%AC%D8%B2%D8%A1%20%D8%A7%D9%84%D8%AB%D8%A7%D9%86%D9%8A.pdf',
    },
    'الأحكام_التجارية_1431هـ/التجاري 1431 الجزء الرابع': {
        "collection": 'مجموعة الأحكام والمبادئ لعام 1431 هـ',
        "landing": 'https://www.bog.gov.sa/ScientificContent/JudicialBlogs/1431/Pages/default.aspx',
        "pdf": 'https://www.bog.gov.sa/ScientificContent/JudicialBlogs/1431/Documents/المجموعة كاملة (PDF)/%D8%A7%D9%84%D8%AA%D8%AC%D8%A7%D8%B1%D9%8A%201431%20%D8%A7%D9%84%D8%AC%D8%B2%D8%A1%20%D8%A7%D9%84%D8%B1%D8%A7%D8%A8%D8%B9.pdf',
    },
    'الأحكام_التجارية_1431هـ/الجزائي 1431 الجزء الأول': {
        "collection": 'مجموعة الأحكام والمبادئ لعام 1431 هـ',
        "landing": 'https://www.bog.gov.sa/ScientificContent/JudicialBlogs/1431/Pages/default.aspx',
        "pdf": 'https://www.bog.gov.sa/ScientificContent/JudicialBlogs/1431/Documents/المجموعة كاملة (PDF)/%D8%A7%D9%84%D8%AC%D8%B2%D8%A7%D8%A6%D9%8A%201431%20%D8%A7%D9%84%D8%AC%D8%B2%D8%A1%20%D8%A7%D9%84%D8%A3%D9%88%D9%84.pdf',
    },
    'الأحكام_التجارية_1431هـ/الجزائي1431 الجزء الثاني': {
        "collection": 'مجموعة الأحكام والمبادئ لعام 1431 هـ',
        "landing": 'https://www.bog.gov.sa/ScientificContent/JudicialBlogs/1431/Pages/default.aspx',
        "pdf": 'https://www.bog.gov.sa/ScientificContent/JudicialBlogs/1431/Documents/المجموعة كاملة (PDF)/%D8%A7%D9%84%D8%AC%D8%B2%D8%A7%D8%A6%D9%8A1431%20%D8%A7%D9%84%D8%AC%D8%B2%D8%A1%20%D8%A7%D9%84%D8%AB%D8%A7%D9%86%D9%8A.pdf',
    },
    'الأحكام_الجزائية_1402-1427هـ/المجلد الاول': {
        "collection": 'مجموعة الأحكام والمبادئ الجزائية للأعوام 1402هـ-1427هـ',
        "landing": 'https://www.bog.gov.sa/ScientificContent/JudicialBlogs/1402-1427/Pages/default.aspx',
        "pdf": 'https://www.bog.gov.sa/ScientificContent/JudicialBlogs/1402-1427/Documents/المجموعة كاملة (PDF)/%D8%A7%D9%84%D9%85%D8%AC%D9%84%D8%AF%20%D8%A7%D9%84%D8%A7%D9%88%D9%84.pdf',
    },
    'الأحكام_الجزائية_1402-1427هـ/المجلد الثالث': {
        "collection": 'مجموعة الأحكام والمبادئ الجزائية للأعوام 1402هـ-1427هـ',
        "landing": 'https://www.bog.gov.sa/ScientificContent/JudicialBlogs/1402-1427/Pages/default.aspx',
        "pdf": 'https://www.bog.gov.sa/ScientificContent/JudicialBlogs/1402-1427/Documents/المجموعة كاملة (PDF)/%D8%A7%D9%84%D9%85%D8%AC%D9%84%D8%AF%20%D8%A7%D9%84%D8%AB%D8%A7%D9%84%D8%AB.pdf',
    },
    'الأحكام_الجزائية_1402-1427هـ/المجلد الثاني': {
        "collection": 'مجموعة الأحكام والمبادئ الجزائية للأعوام 1402هـ-1427هـ',
        "landing": 'https://www.bog.gov.sa/ScientificContent/JudicialBlogs/1402-1427/Pages/default.aspx',
        "pdf": '',
    },
    'الأحكام_الجزائية_1402-1427هـ/المجلد الرابع': {
        "collection": 'مجموعة الأحكام والمبادئ الجزائية للأعوام 1402هـ-1427هـ',
        "landing": 'https://www.bog.gov.sa/ScientificContent/JudicialBlogs/1402-1427/Pages/default.aspx',
        "pdf": 'https://www.bog.gov.sa/ScientificContent/JudicialBlogs/1402-1427/Documents/المجموعة كاملة (PDF)/%D8%A7%D9%84%D9%85%D8%AC%D9%84%D8%AF%20%D8%A7%D9%84%D8%B1%D8%A7%D8%A8%D8%B9.pdf',
    },
    'السوابق_القضائية_الإدارية_1402-1436هـ/السوابق القضائية كاملة': {
        "collection": 'السوابق القضائية لأحكام ديوان المظالم الإدارية للأعوام 1402 - 1436 هـ',
        "landing": 'https://www.bog.gov.sa/ScientificContent/JudicialBlogs/A1402-1436/Pages/default.aspx',
        "pdf": 'https://www.bog.gov.sa/ScientificContent/JudicialBlogs/A1402-1436/Documents/السوابق القضائية كاملة (PDF)/%D8%A7%D9%84%D8%B3%D9%88%D8%A7%D8%A8%D9%82%20%D8%A7%D9%84%D9%82%D8%B6%D8%A7%D8%A6%D9%8A%D8%A9%20%D9%83%D8%A7%D9%85%D9%84%D8%A9.pdf',
    },
}


def volume_source(source: Any) -> Optional[VolumeSource]:
    """Resolve a ``cases.source`` dict to its published volume, or ``None``.

    Tries the sha1 key (``source_volume``) before the path key (``volume``): a row carries
    one or the other, never both, and the ديوان المظالم rows set ``source_volume`` to an
    int INDEX rather than a sha, so checking it first costs nothing and reading it as a
    registry key would be wrong. Non-dict input, an unknown volume, and a row with no
    volume at all all return ``None`` — a caller never has to pre-check the shape.
    """
    if not isinstance(source, dict):
        return None
    sha = source.get("source_volume")
    if isinstance(sha, str) and sha in CASE_VOLUME_SOURCES:
        return CASE_VOLUME_SOURCES[sha]
    volume = source.get("volume")
    if isinstance(volume, str) and volume in CASE_VOLUME_SOURCES:
        return CASE_VOLUME_SOURCES[volume]
    return None
