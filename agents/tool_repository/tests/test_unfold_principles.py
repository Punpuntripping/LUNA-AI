"""unfold_workspace_item — the «مبدأ قضائي» line (official_principles_refs.md §3.6).

The router names a prior item's sources by what the المراجع card printed, so a
principle's manifest line is the card's own strings: the chip «مبدأ قضائي», the
title «المبدأ رقم N — {الباب}», the issuing body and decision line, then the rule
capped exactly as the card's snippet is. Before this, a ``principles`` ref fell
into the unknown-domain branch and its ``[n]`` vanished from the manifest.

Self-contained: a small filter-honouring Supabase fake lives below (same
semantics as test_unfold_preview_lock's). The row is ``official_principles``
no. 2 (entity 18329), copied from the live table on 2026-11-07.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from agents.tool_repository import unfold_workspace_item as uwi


class _Query:
    def __init__(self, rows: list[dict], log: list[tuple[str, list]], name: str) -> None:
        self._rows = list(rows)
        self._filters: list[Any] = []
        self._log, self._name = log, name

    def select(self, _cols: str) -> "_Query":
        return self

    def eq(self, col: str, val: Any) -> "_Query":
        self._filters.append(lambda r: r.get(col) == val)
        return self

    def in_(self, col: str, vals: list[Any]) -> "_Query":
        self._log.append((self._name, list(vals)))
        wanted = set(vals)
        self._filters.append(lambda r: r.get(col) in wanted)
        return self

    def order(self, col: str, desc: bool = False) -> "_Query":
        return self

    def execute(self) -> SimpleNamespace:
        return SimpleNamespace(data=[r for r in self._rows if all(f(r) for f in self._filters)])


def _supabase(tables: dict[str, list[dict]]) -> SimpleNamespace:
    log: list[tuple[str, list]] = []
    return SimpleNamespace(
        table=lambda name: _Query(tables.get(name, []), log, name), log=log,
    )


WI = "11111111-1111-1111-1111-111111111111"
P2_ID = "64052e1b-17d9-511d-8508-ed401e624d1a"
P_GONE = "9a9a9a9a-0000-4000-8000-000000000000"

ROW_P2 = {
    "id": P2_ID,
    "principle_ref": "18329_principles_0002",
    "entity_ref": "18329",
    "principle_no": 2,
    "chapter_no": 1,
    "chapter_title": "الأصول والقواعد",
    "text": "الأصل براءة الذمة حتى يثبت ما يوجب الإدانة.",
    "content": "(هـ ق ع): (٢٥٤)، (١٣/١٠/١٣٩١)",
    "citations": [
        {
            "body": "الهيئة القضائية العليا",
            "body_code": "هـ ق ع",
            "date_hijri": "13/10/1391",
            "decision_no": "254",
        }
    ],
    "issuing_bodies": ["هـ ق ع"],
    "issuing_body_names": ["الهيئة القضائية العليا"],
    "source": {
        "pages": [33, 33],
        "source_url": "https://drive.google.com/file/d/19wu0BYdazRzTB0xjmCkk0cgNu2O5L8qG/view",
    },
}

EXPECTED_P2 = (
    "مبدأ قضائي: المبدأ رقم 2 — الأصول والقواعد "
    "(الهيئة القضائية العليا، القرار 254 بتاريخ 13/10/1391هـ) — "
    "الأصل براءة الذمة حتى يثبت ما يوجب الإدانة."
)


def _ref(n: int, ref_id: str, item_id: Any = None) -> dict:
    return {"wi_id": WI, "n": n, "ref_id": ref_id, "domain": "principles",
            "item_id": item_id, "used": True}


def test_a_principle_line_is_the_card_chip_title_decision_and_rule() -> None:
    supabase = _supabase({
        "workspace_item_references": [_ref(1, f"principle:{P2_ID}", P2_ID)],
        "official_principles": [ROW_P2],
    })
    lines = uwi.resolve_used_sources(supabase, WI)
    assert [(ln.n, ln.domain, ln.text) for ln in lines] == [(1, "principles", EXPECTED_P2)]
    # Decisions from ``citations`` (Latin digits) — never the stored content.
    assert "٢" not in lines[0].text and "هـ ق ع" not in lines[0].text


def test_a_principle_keyed_by_ref_id_alone_still_resolves() -> None:
    supabase = _supabase({
        "workspace_item_references": [_ref(4, f"principle:{P2_ID}")],
        "official_principles": [ROW_P2],
    })
    assert [ln.text for ln in uwi.resolve_used_sources(supabase, WI)] == [EXPECTED_P2]


def test_a_vanished_or_malformed_principle_keeps_its_n_as_a_stub() -> None:
    supabase = _supabase({
        "workspace_item_references": [
            _ref(1, f"principle:{P_GONE}", P_GONE),
            _ref(2, "principle:not-a-uuid"),
            _ref(3, f"principle:{P2_ID}", P2_ID),
        ],
        "official_principles": [ROW_P2],
    })
    lines = uwi.resolve_used_sources(supabase, WI)
    assert [(ln.n, ln.text) for ln in lines] == [
        (1, uwi._FALLBACK_LINE), (2, uwi._FALLBACK_LINE), (3, EXPECTED_P2),
    ]
    # ONE batched read, and the malformed id never reaches the uuid column.
    assert supabase.log == [("official_principles", sorted([P_GONE, P2_ID]))]


def test_the_rule_is_capped_exactly_like_the_card_snippet() -> None:
    long_rule = "قاعدة " * 200
    supabase = _supabase({
        "workspace_item_references": [_ref(1, f"principle:{P2_ID}", P2_ID)],
        "official_principles": [{**ROW_P2, "text": long_rule}],
    })
    (line,) = uwi.resolve_used_sources(supabase, WI, snippet_max_chars=120)
    rule = line.text.split(") — ", 1)[1]
    assert len(rule) <= 125  # the cap + the snippet's own ellipsis
    assert rule.startswith("قاعدة قاعدة")


def test_a_principle_with_no_number_chapter_or_citation_still_reads_as_one() -> None:
    """17486 (المحكمة الإدارية العليا) is extracted but unclassified: nothing
    may assume a number, a chapter or a citation exists."""
    bare = {"id": P2_ID, "text": "نص المبدأ."}
    supabase = _supabase({
        "workspace_item_references": [_ref(1, f"principle:{P2_ID}", P2_ID)],
        "official_principles": [bare],
    })
    (line,) = uwi.resolve_used_sources(supabase, WI)
    assert line.text == "مبدأ قضائي — نص المبدأ."


def test_the_manifest_renders_a_principle_in_n_order() -> None:
    supabase = _supabase({
        "workspace_item_references": [_ref(2, f"principle:{P2_ID}", P2_ID)],
        "official_principles": [ROW_P2],
    })
    md = uwi.render_unfold_md("نص [2].", uwi.resolve_used_sources(supabase, WI))
    assert md.endswith(f"{uwi._MANIFEST_HEADER}\n[2] {EXPECTED_P2}")
