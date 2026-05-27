"""Dump per-sector representative regulation titles for the sector_picker prompt.

Queries ``regulations_v2`` once per canonical sector, picks the top-N titles by
chunk-count weight (proxy: count of ``chunks_v2`` rows whose ``regulation_id``
matches), and writes the result to
``agents/deep_search_v4/sector_picker/sector_examples.py`` as
``SECTOR_EXAMPLES: dict[str, list[str]]``.

Re-run when the corpus changes materially. Output is checked into git so the
sector_picker prompt is reproducible without a Supabase round-trip at import
time.

Run from repo root:

    python scripts/build_sector_examples.py
"""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
except Exception:
    pass

from dotenv import load_dotenv
load_dotenv()

from agents.deep_search_v4.shared.sector_vocab.regulations import VALID_SECTORS
from shared.db.client import get_supabase_client

# How many representative titles to keep per sector. Tuned so the rendered
# prompt stays under ~6k tokens — 6 titles × 38 sectors × ~10 tokens = ~2.3k
# tokens for the examples alone.
TOP_N_PER_SECTOR = 6

OUT_PATH = _ROOT / "agents" / "deep_search_v4" / "sector_picker" / "sector_examples.py"

# Page size for the chunks_v2 scan. Supabase's REST API caps responses at 1000
# rows regardless of the requested range, so any value above 1000 effectively
# truncates. Keep at 1000 and page explicitly.
CHUNK_SCAN_PAGE = 1000


def _build_chunk_counts(supabase) -> dict[str, int]:
    """Build ``{regulation_id: chunk_count}`` by scanning ``chunks_v2`` once.

    Supabase has no GROUP BY on the REST API, but it does support paginated
    range queries. We page through ``chunks_v2`` selecting only
    ``regulation_id`` and tally in Python. One pass, ~1 minute for the live
    corpus.
    """
    counts: dict[str, int] = {}
    offset = 0
    page = 0
    while True:
        result = (
            supabase.table("chunks_v2")
            .select("regulation_id")
            .range(offset, offset + CHUNK_SCAN_PAGE - 1)
            .execute()
        )
        rows = result.data or []
        if not rows:
            break
        for row in rows:
            rid = row.get("regulation_id")
            if rid:
                counts[rid] = counts.get(rid, 0) + 1
        page += 1
        if page % 10 == 0:
            print(f"  scanned chunks: {offset + len(rows)} (unique regs so far: {len(counts)})")
        if len(rows) < CHUNK_SCAN_PAGE:
            break
        offset += CHUNK_SCAN_PAGE
    print(f"  done: {offset + len(rows)} chunks, {len(counts)} regulations")
    return counts


def _title_kind_rank(title: str) -> int:
    """Lower number = more recognizable headline law.

    Saudi legal corpus shape: ``نظام X`` is the headline statute (code-level);
    ``اللائحة التنفيذية`` / ``لائحة`` / ``قواعد`` are implementing regulations;
    ``دليل`` are guides. The picker LLM identifies a sector best from headline
    statutes, so we surface those first.
    """
    t = title.lstrip()
    if t.startswith("نظام ") or t.startswith("النظام "):
        return 0
    if t.startswith("اللائحة التنفيذية"):
        return 1
    if t.startswith("لائحة ") or t.startswith("اللائحة "):
        return 2
    if t.startswith("قواعد ") or t.startswith("القواعد "):
        return 3
    if t.startswith("دليل ") or t.startswith("الدليل "):
        return 5  # guides last
    return 4


def _fetch_titles_for_sector(
    supabase, sector: str, chunk_counts: dict[str, int],
) -> list[str]:
    """Top-N regulation titles for one sector, biased toward headline laws.

    Rank key: ``(_title_kind_rank, -chunk_count, title)``. Headline statutes
    (``نظام …``) come first, then implementing regulations, then guides; within
    each kind, more-chunked regulations rank higher. Deterministic and
    independent of insertion order.
    """
    result = (
        supabase.table("regulations_v2")
        .select("id, title")
        .contains("sectors", [sector])
        .execute()
    )
    rows = result.data or []

    candidates = [
        (
            (row.get("title") or "").strip(),
            chunk_counts.get(row.get("id"), 0),
        )
        for row in rows
        if (row.get("title") or "").strip()
    ]
    candidates.sort(key=lambda t: (_title_kind_rank(t[0]), -t[1], t[0]))

    titles: list[str] = []
    seen: set[str] = set()
    for title, _ in candidates:
        if title in seen:
            continue
        seen.add(title)
        titles.append(title)
        if len(titles) >= TOP_N_PER_SECTOR:
            break
    return titles


def main() -> None:
    supabase = get_supabase_client()
    print("Scanning chunks_v2 to build per-regulation chunk counts...")
    chunk_counts = _build_chunk_counts(supabase)

    by_sector: dict[str, list[str]] = {}
    for i, sector in enumerate(VALID_SECTORS, start=1):
        titles = _fetch_titles_for_sector(supabase, sector, chunk_counts)
        by_sector[sector] = titles
        print(f"[{i:2d}/{len(VALID_SECTORS)}] {sector}: {len(titles)} titles")

    # Render the module.
    lines: list[str] = [
        '"""Per-sector representative regulation titles for the sector_picker prompt.',
        "",
        "Generated by ``scripts/build_sector_examples.py``. Do not hand-edit.",
        "",
        f"Top-{TOP_N_PER_SECTOR} titles per canonical sector, ranked by ``chunk_count`` desc.",
        "Used to give the sector_picker LLM a concrete sense of what each sector",
        "contains, instead of a flat list of 38 names.",
        '"""',
        "from __future__ import annotations",
        "",
        "SECTOR_EXAMPLES: dict[str, list[str]] = {",
    ]
    for sector, titles in by_sector.items():
        lines.append(f"    {sector!r}: [")
        for t in titles:
            lines.append(f"        {t!r},")
        lines.append("    ],")
    lines.append("}")
    lines.append("")
    lines.append('__all__ = ["SECTOR_EXAMPLES"]')
    lines.append("")

    body = "\n".join(lines)
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(body, encoding="utf-8")
    print(f"\nWrote {OUT_PATH.relative_to(_ROOT)}")


if __name__ == "__main__":
    main()
