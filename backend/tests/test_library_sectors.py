"""Guard tests for the sector slug map (`library_sectors.md` §6, D4/D5, T2).

`shared/library/sectors.py` deliberately DEGRADES on drift rather than raising at
import time (a sector added on the agents side must not crash the backend's
boot). That makes these tests the real enforcement point: they must fail in CI
the moment ``VALID_SECTORS`` and the slug map disagree.
"""
from __future__ import annotations

import re

import pytest

from agents.deep_search_v4.shared.sector_vocab.unified import VALID_SECTORS
from shared.library import sectors as mod

_SLUG_RE = re.compile(r"\A[a-z0-9]+(-[a-z0-9]+)*\Z")


def test_every_valid_sector_has_a_slug():
    """The map covers the owning vocabulary EXACTLY — no gaps, no strays.

    A gap means that sector silently has no public page; a stray means a slug
    that matches zero corpus rows. Both are invisible at runtime by design.
    """
    missing = [name for name in VALID_SECTORS if name not in mod.SECTOR_SLUGS]
    extra = [name for name in mod.SECTOR_SLUGS if name not in set(VALID_SECTORS)]
    assert not missing, f"sectors with no slug: {missing}"
    assert not extra, f"slugged names absent from VALID_SECTORS: {extra}"
    assert len(mod.SECTOR_SLUGS) == 38


def test_slugs_are_unique_and_url_safe():
    """38 distinct lowercase kebab-case slugs — a collision would alias two
    sectors onto one URL and hand the loser a page of the winner's items."""
    slugs = list(mod.SECTOR_SLUGS.values())
    assert len(set(slugs)) == len(slugs), "duplicate slug"
    assert len(mod.SLUG_TO_SECTOR) == len(slugs)
    bad = [s for s in slugs if not _SLUG_RE.match(s)]
    assert not bad, f"not url-safe kebab-case: {bad}"


def test_slugs_are_latin_only():
    """D4/D5: structural segments are Latin translations, never Arabic or a
    transliteration. An Arabic character here means the rule was broken."""
    assert all(s.isascii() for s in mod.SECTOR_SLUGS.values())


def test_browse_order_is_by_volume_not_alphabetical():
    """§3/§7.2: insertion order IS the browse order. Alphabetical would bury the
    20k-item sector under the 753-item one."""
    assert mod.SECTOR_ORDER[0] == "المعاملات التجارية"
    assert mod.SECTOR_ORDER[-1] == "حقوق الإنسان"
    assert mod.SECTOR_ORDER != sorted(mod.SECTOR_ORDER)
    assert mod.SECTOR_ORDER == list(mod.SECTOR_SLUGS)


@pytest.mark.parametrize("reserved", ["mine", "page"])
def test_reserved_segments_never_resolve_as_a_sector(reserved):
    """T2: `/library/mine` is the authed shelf and `/library/page/{n}` is the
    unfiltered paginator. If either ever resolved as a sector the shelf route
    could be shadowed — a per-user surface rendered for anonymous visitors."""
    assert mod.sector_for_slug(reserved) is None
    assert reserved not in mod.SECTOR_SLUG_VOCAB


def test_unknown_slug_resolves_to_none():
    """§12.7: an invalid slug must be answerable without a DB round-trip."""
    assert mod.sector_for_slug("zzz") is None
    assert mod.sector_for_slug("") is None
    assert mod.sector_for_slug(None) is None


def test_slug_lookup_round_trips_both_ways():
    for name, slug in mod.SECTOR_SLUGS.items():
        assert mod.sector_for_slug(slug) == name
        assert mod.slug_for_sector(name) == slug


def test_lookup_is_case_and_whitespace_tolerant():
    """Slugs arrive from a URL path segment; names arrive from corpus arrays."""
    assert mod.sector_for_slug("  Labor-Employment ") == "العمل والتوظيف"
    assert mod.slug_for_sector(" العمل والتوظيف ") == "labor-employment"


def test_unrecognised_sector_name_is_not_linkable():
    """D11: a pill whose value is not in the vocabulary renders as plain text,
    never as a link to a 404."""
    assert mod.slug_for_sector("قطاع لا وجود له") is None
