"""Strip the ingestion pipeline's internal appendix from ``cases.summary``.

The judgment-summary pipeline appended a trailing section to 16,505 of the
30,531 ``cases.summary`` rows (verified live 2026-08-07)::

    ## المراجع النظامية المحلولة
    - **نظام المحاكم التجارية** — المادة 16 → `17642_reg_003`
      (chunks: 17642_reg_003_article_16) [confidence: 0.9016]

That block is resolver telemetry — internal corpus ids, chunk ids and match
scores — not legal content. It must never reach a user surface (the case
source-view popup) nor an LLM prompt (the aggregator synthesis payload, whose
model happily restates the ids into the visible answer, which is exactly how
the leak was spotted).

The DB rows are pipeline-owned and may be re-ingested with the block intact,
so the durable fix is this render-time strip, applied at every point where
``cases.summary`` (or a URA ``case_content`` derived from it before this fix
existed) is served: ``case_search/unfold_ura._resolve_summary`` (publish
time), ``aggregator/preprocessor.render_aggregator_content`` (covers old
persisted artifacts), and ``source_viewer._build_case_view`` (the popup).

Live shape is uniform — always a ``## ``-level heading and always the LAST
section — but the stripper still drops heading→next-heading (any ``#`` level)
so a re-ingest that reorders sections cannot resurrect the leak.
"""
from __future__ import annotations

import re

_RESOLVED_REFS_PHRASE = "المراجع النظامية المحلولة"

# The appendix heading: 1–6 #'s, the phrase, optional trailing colon.
_RESOLVED_REFS_HEADING_RE = re.compile(
    r"^[ \t]{0,3}#{1,6}[ \t]*" + _RESOLVED_REFS_PHRASE + r"[ \t]*:?[ \t]*$",
    re.MULTILINE,
)

# Any markdown heading — the strip stops here so real sections survive.
_ANY_HEADING_RE = re.compile(r"^[ \t]{0,3}#{1,6}[ \t]+\S", re.MULTILINE)


def strip_resolved_refs_section(text: str) -> str:
    """Remove every «المراجع النظامية المحلولة» section from ``text``.

    Drops from the appendix heading up to (not including) the next markdown
    heading, or to end-of-text — in practice the block is always the tail.
    Text without the phrase is returned unchanged (cheap substring guard);
    the phrase appearing in prose without its heading is left alone.
    """
    if not text or _RESOLVED_REFS_PHRASE not in text:
        return text
    out = text
    while True:
        m = _RESOLVED_REFS_HEADING_RE.search(out)
        if not m:
            break
        nxt = _ANY_HEADING_RE.search(out, m.end())
        out = out[: m.start()] + out[nxt.start():] if nxt else out[: m.start()]
    return out.rstrip()


__all__ = ["strip_resolved_refs_section"]
