"""Strip the ingestion pipeline's internal sections from ``cases.summary``.

The judgment-summary pipeline leaves TWO kinds of machine-written section in
``cases.summary``. Neither is legal content; both must be dropped at render.

**1. The resolver appendix** — 16,505 of the 30,531 rows (verified live
2026-08-07)::

    ## المراجع النظامية المحلولة
    - **نظام المحاكم التجارية** — المادة 16 → `17642_reg_003`
      (chunks: 17642_reg_003_article_16) [confidence: 0.9016]

Resolver telemetry — internal corpus ids, chunk ids and match scores.

**2. The classifier's crash dump** — 252 rows (verified live 2026-08-10)::

    ## منطوق الاستئناف
    تأييد حكم المحكمة التجارية بالرياض …

    ## classification_error
    ConnectError: [Errno 11001] getaddrinfo failed

A Python traceback line the classification step wrote into the document when it
failed (``ConnectError: … getaddrinfo failed`` on most, ``JSONDecodeError`` on
43). It is English, it is a stack-trace fragment, and it says nothing about the
ruling.

Neither may reach a user surface (the case source-view popup) nor an LLM prompt
(the aggregator synthesis payload, whose model happily restates ids — and, on
those 252 rulings, restates the error — into the visible answer; that is exactly
how the first leak was spotted).

THE PATTERN LIST IS CLOSED, NOT A GUESS
---------------------------------------
A scan of all 30,531 summaries for Latin-alphabet section headings found
``classification_error`` **and nothing else**. And the leak is ``summary``-only:
0 rows carry either section in ``short_summary`` / ``content`` / ``facts`` /
``ruling``, which is why the public ``/judgments`` pages (body = ``cases.content``,
lead = ``short_summary``) were never affected. So this module strips two known
sections rather than guessing at a family of them — if a third ever appears, it
is new pipeline behaviour and belongs here explicitly.

The DB rows are pipeline-owned and may be re-ingested with the blocks intact, so
the durable fix is this render-time strip, applied at every point where
``cases.summary`` (or a URA ``case_content`` derived from it before this fix
existed) is served: ``case_search/unfold_ura._resolve_summary`` (publish time),
``aggregator/preprocessor.render_aggregator_content`` (covers old persisted
artifacts), ``source_viewer._build_case_view`` (the popup) and
``tool_repository/unfold_workspace_item`` (the LLM tool result). Any NEW consumer
of ``cases.summary`` must run this too — which is why the function is named after
what it does (drop pipeline sections) rather than after one of its patterns.

Live shape is uniform for both — always a ``## ``-level heading and always in the
tail — but the stripper still drops heading→next-heading (any ``#`` level) so a
re-ingest that reorders sections cannot resurrect either leak.
"""
from __future__ import annotations

import re

_RESOLVED_REFS_PHRASE = "المراجع النظامية المحلولة"

# The appendix heading: 1–6 #'s, the phrase, optional trailing colon.
_RESOLVED_REFS_HEADING_RE = re.compile(
    r"^[ \t]{0,3}#{1,6}[ \t]*" + _RESOLVED_REFS_PHRASE + r"[ \t]*:?[ \t]*$",
    re.MULTILINE,
)

_ERROR_PHRASE = "classification_error"

# The classifier crash heading. Case-insensitive because the token is
# machine-written English and nothing guarantees its casing across re-ingests.
_ERROR_HEADING_RE = re.compile(
    r"^[ \t]{0,3}#{1,6}[ \t]*" + _ERROR_PHRASE + r"[ \t]*:?[ \t]*$",
    re.MULTILINE | re.IGNORECASE,
)

# Any markdown heading — the strip stops here so real sections survive.
_ANY_HEADING_RE = re.compile(r"^[ \t]{0,3}#{1,6}[ \t]+\S", re.MULTILINE)


def _drop_sections(text: str, heading_re: re.Pattern[str]) -> str:
    """Drop every ``heading_re`` section: heading → next heading, or to EOF.

    Returns the SAME object when nothing matched, so the caller can tell a real
    strip from a no-op without comparing strings.
    """
    out = text
    while True:
        m = heading_re.search(out)
        if not m:
            break
        nxt = _ANY_HEADING_RE.search(out, m.end())
        out = out[: m.start()] + out[nxt.start():] if nxt else out[: m.start()]
    return out


def strip_pipeline_sections(text: str) -> str:
    """Remove the pipeline's internal sections from ``text``.

    Drops every «المراجع النظامية المحلولة» section and every
    ``classification_error`` section, each from its heading up to (not including)
    the next markdown heading of any level, or to end-of-text.

    Text carrying neither phrase is returned unchanged (cheap substring guard);
    a phrase appearing in prose without its own heading is left alone.

    Order between the two passes does not matter: ``_ANY_HEADING_RE`` stops one
    strip at the other's heading, and the second pass then removes it.
    """
    if not text:
        return text
    has_refs = _RESOLVED_REFS_PHRASE in text
    has_error = _ERROR_PHRASE in text.lower()
    if not (has_refs or has_error):
        return text

    out = text
    if has_refs:
        out = _drop_sections(out, _RESOLVED_REFS_HEADING_RE)
    if has_error:
        out = _drop_sections(out, _ERROR_HEADING_RE)
    return out.rstrip()


__all__ = ["strip_pipeline_sections"]
