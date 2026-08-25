"""Chunk tables — the ONE walk that turns a display body into renderable parts.

Every table in the regulation corpus was OCR'd and then converted to **prose**
before ingestion, because prose is what BM25 indexes and what the model reads.
The original ``<table>`` markup survived on disk and, since 2026-08-24, lives in
the database beside it:

    chunks_v2.content          the AGENT view — prose conversion intact
    chunks_v2.content_display  the USER view — same text, each confidently
                               resolved table collapsed to a whole-line
                               ``TBL_…`` token. NULL when there is nothing to
                               swap.
    chunk_tables_v2            one row per token: ``table_html``, ``table_md``
                               (the prose it replaced) and provenance.

Rendering is therefore a walk: swap each token line for its table. This module
is that walk, and nothing else — it is the single implementation the public
library and the مراجع popup both call, so the two surfaces cannot drift.

Measured on prod (``dwgghvxogtwyaxmbgjod``) 2026-08-24:

    chunk_tables_v2 rows                24,511
    chunks carrying content_display      8,855 / 48,390 (18.3%)
    tokens found in content_display      24,511 — **0 unresolvable**
    merged cells (rowspan | colspan)      8,363 (34.1%)
    <br>                                  8,641
    <th> / <thead>                       16,370 / 0
    table_md empty                            0 (min length 27 chars)
    mean / max table_html                 1,182 / 12,653 chars
    <script> / <iframe> / javascript:         0 / 0 / 0
    <img> 252 · <a> 42 · style= 55 · form controls 4

The corpus being provably clean today is what makes the allowlist free: it costs
nothing in fidelity and buys the guarantee that the frontend's
``dangerouslySetInnerHTML`` is trusted **by construction** rather than by
inspection of a snapshot.

Five rules are load-bearing here, and each one closes a specific trap:

1. **``content_display`` NULL ⇒ read ``content``.** 82% of chunks carry no
   display body at all; that is the normal case, not an error.
   (:func:`display_body` is the one place this rule lives.)
2. **An unresolved token emits NOTHING.** A raw ``TBL_17261_reg_501_chunk_003_1``
   on a page — or worse, in a generated answer — is the single failure mode this
   whole design exists to prevent. Corpus-wide it is currently unreachable
   (0 of 24,511), but the local corpus runs ahead of the DB and re-ingests will
   recur, so the renderer is safe by construction instead of by luck.
3. **Resolve by ``table_ref`` read off the row.** Never derive the token from
   ``chunk_ref`` and never key on ``position``. ``position`` is document order,
   not render order, and a chunk can hold tables that were skipped as unsure.
   The token stem is a *sanitized* ``chunk_ref`` with a hash appended whenever
   sanitizing changed anything — four regulations carry Arabic in their ref, one
   a space (``17645_reg_الانظمة_002_chunk_001``). Deriving the token is right
   99.96% of the time, which is the worst possible hit rate.
4. **``md`` is the alt text.** It is the prose the table was flattened into —
   real Arabic sentences, never a filename, never empty (measured min 27 chars).
   It is what makes a chunk usable with no table rendering at all, it is the
   copy string (a user pasting a source into a memo must get prose, not markup),
   and it is what the gate charges a token against.
5. **Sanitize by RE-SERIALIZING through an allowlist**, never by stripping with
   a regex. A regex that strips ``<script>`` can be defeated by markup a regex
   cannot parse; a re-serializer emits only tags it decided to emit, so the
   output is a function of the allowlist and not of the input's shape.

This module is PURE: no database, no I/O, no gate and no truncation. Weighing a
table against the free-char budget (``len(md)``, atomic) belongs to
``backend/app/services/library_service.py`` and stays there — which is why every
table segment carries its ``md``.

Never embed, index or prompt on the body this module renders. It has table
content *removed*; indexing it silently drops tables from search. ``content``
remains what the aggregator, BM25, ``search_topics`` and every reranker see.

Plan: ``.claude/plans/chunk_table_rendering.md`` §2.
Corpus-side contract: ``agentic_for_ministry`` →
``ingestion/regulation_v2/CHUNK_TABLES_REFERENCE.md`` §3–§4.
"""
from __future__ import annotations

import re
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from html import escape, unescape
from html.parser import HTMLParser
from typing import Any

__all__ = [
    "TABLE_PLACEHOLDER",
    "ChunkTable",
    "Segment",
    "display_body",
    "sanitize_table_html",
    "tables_by_ref",
    "split_body",
    "render_text_only",
    "visible_text",
    "table_weight",
]


#: The one regex. A whole line, nothing else on it; the capture group is the
#: ``table_ref`` to look up. ``[A-Za-z0-9_]+`` and not something looser is what
#: makes BOTH token shapes match — the plain ``TBL_{chunk_ref}_{n}`` and the
#: hash-suffixed form produced for a ref that needed sanitizing. Do not relax
#: it, and do not write a second copy of it anywhere.
TABLE_PLACEHOLDER = re.compile(r"^[ \t]*(TBL_[A-Za-z0-9_]+)[ \t]*$", re.M)

#: Measuring only — see :func:`visible_text`. These run over ALREADY-SANITIZED
#: markup, so there is no tag-stripping-as-security here; the allowlist
#: re-serializer is what makes the markup safe, and a regex never is.
_TAG_RUN = re.compile(r"<[^>]*>")
_WS_RUN = re.compile(r"\s+")


# --- the allowlist (plan §2.2) ------------------------------------------------

#: Emitted verbatim (minus every attribute but the two below).
#:
#: ``ul``/``ol``/``li``/``dl``/``dt``/``dd`` are here because a bulleted list
#: inside a ``<td>`` is real structure, not decoration — 226 corpus tables carry
#: one, and flattening them to a run-on line is the same lossy move this whole
#: module exists to undo.
_ALLOWED_ELEMENTS = frozenset(
    {
        "table", "thead", "tbody", "tfoot", "tr", "th", "td", "caption",
        "br", "b", "strong", "i", "em", "sup", "sub",
        "ul", "ol", "li", "dl", "dt", "dd",
    }
)

#: Tag dropped, TEXT kept. ``a`` (42 rows) loses its href — a statute's grid has
#: no business carrying an outbound link, and unwrapping keeps the label. ``p``,
#: ``span`` and ``div`` are pure presentation inside a cell; a heading inside a
#: cell is a font choice, not an outline, so ``h1``-``h6`` unwrap too.
#:
#: ⚠ EVERY presentational wrapper the corpus uses must be listed HERE and not
#: merely left off ``_ALLOWED_ELEMENTS`` — an unlisted element is dropped WITH
#: ITS CONTENT. Full-corpus validation on 2026-08-25 found that gap destroying
#: **177,060 characters of statutory text across 325 tables**, 24 of which
#: rendered as a visible blank grid: ``<ul>`` alone ate 149,116 chars over 218
#: tables, ``div`` 13,725 over 43, ``ol`` 7,568 over 17. Dropping-with-content
#: is correct for ``<img>``/``<script>`` and catastrophic for a layout wrapper.
#: Adding a tag to this set is cheap; leaving one out is silent data loss.
_UNWRAPPED_ELEMENTS = frozenset(
    {
        "a", "p", "span", "div", "u", "font", "center", "small", "big",
        "h1", "h2", "h3", "h4", "h5", "h6", "section", "article", "label",
        "abbr", "code", "pre", "blockquote", "figure", "figcaption",
    }
)

#: The only attributes that survive, and only with an int value in 1..100.
#: ``style`` (55 rows), ``class``, ``id``, ``width``, ``align`` all go: the app's
#: own styling owns presentation.
_ALLOWED_ATTRS = frozenset({"rowspan", "colspan"})

#: A merged cell spanning more than this is a parse artifact, not a statute.
_MAX_SPAN = 100

#: HTML void elements. Needed by the drop logic: a dropped element that can
#: never be closed must NOT open a suppression scope, or one stray ``<img>``
#: would swallow the rest of the table.
_VOID_ELEMENTS = frozenset(
    {
        "area", "base", "br", "col", "embed", "hr", "img", "input", "link",
        "meta", "param", "source", "track", "wbr",
    }
)

#: Cells are the proof-of-life test: a fragment that kept neither is not a table.
_CELL_ELEMENTS = frozenset({"td", "th"})

#: Table structure. Meeting one of these while inside a dropped scope proves the
#: scope should already have closed — see ``handle_starttag``.
_STRUCTURE_ELEMENTS = frozenset(
    {"table", "thead", "tbody", "tfoot", "tr", "th", "td", "caption"}
)


@dataclass(frozen=True)
class ChunkTable:
    """One placed table, ready to render.

    ``html`` is **already sanitized** — :func:`tables_by_ref` is the only
    constructor a consumer should use, and it runs :func:`sanitize_table_html`
    before the dataclass exists. Nothing downstream re-sanitizes, and nothing
    downstream may hand raw corpus HTML to this type.
    """

    table_ref: str
    #: Sanitized ``<table>…</table>`` fragment. Never ``""`` for a table that
    #: reached a consumer — an empty sanitize result is dropped upstream so the
    #: token behaves exactly like an unresolved one (rule 2).
    html: str
    #: The prose the table was flattened into: alt text, copy text, gate weight.
    md: str


#: A segment is a plain dict, so it crosses the wire and the ISR bake without a
#: serializer. Exactly two shapes::
#:
#:     {"kind": "text",  "text": str}
#:     {"kind": "table", "ref": str, "html": str, "md": str}
Segment = dict[str, Any]


def display_body(chunk: Mapping[str, Any]) -> str:
    """``content_display`` or ``content``. THE body-choosing rule, in one place.

    A NULL or empty ``content_display`` is the **normal** case — 39,535 of
    48,390 chunks (82%) have no table to swap — not an error and not a signal of
    a failed read. Every display-side caller goes through here so the coalesce
    cannot be spelled four slightly different ways at four call sites.

    Whitespace-only is treated as empty and falls back to ``content`` too: the
    fail-soft direction for this module is always *prose intact*, never a blank
    section. A missing key yields ``""`` rather than raising, because a select
    list that forgot the column must degrade, not 500.

    Never pass the result to an embedder, an index or a prompt — see the module
    docstring.
    """
    display = chunk.get("content_display")
    if isinstance(display, str) and display.strip():
        return display
    content = chunk.get("content")
    return content if isinstance(content, str) else ""


class _TableSanitizer(HTMLParser):
    """Allowlist re-serializer: parses, then REBUILDS from the allowlist.

    The output is a function of :data:`_ALLOWED_ELEMENTS` and nothing else, so
    malformed input cannot smuggle a tag through — the parser may misread the
    input, but every byte of the output was chosen here. That is the whole
    argument for the frontend trusting ``dangerouslySetInnerHTML`` (plan D6).
    """

    def __init__(self) -> None:
        # convert_charrefs=True resolves entities into text, which we then
        # re-escape. That is what neutralises "&lt;script&gt;" smuggling: it
        # arrives as data, and data is always escaped on the way out.
        super().__init__(convert_charrefs=True)
        self._out: list[str] = []
        self._open: list[str] = []       # emitted, still-unclosed tags
        self._suppress: list[str] = []   # inside a dropped subtree
        self.has_cell = False

    # -- emission helpers --

    def _attrs(self, attrs: list[tuple[str, str | None]]) -> str:
        parts: list[str] = []
        seen: set[str] = set()
        for raw_name, raw_value in attrs:
            name = (raw_name or "").lower()
            if name not in _ALLOWED_ATTRS or name in seen:
                continue
            value = _span_value(raw_value)
            if value is None:
                continue
            seen.add(name)
            parts.append(f' {name}="{value}"')
        return "".join(parts)

    # -- parser callbacks --

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if self._suppress:
            # A dropped wrapper that is never closed would otherwise swallow
            # every remaining cell — 7 corpus tables do exactly that. Table
            # STRUCTURE cannot legitimately live inside a dropped inline
            # wrapper, so meeting one proves the scope was already over and
            # ends it. Bounded blast radius: worst case we keep a cell we would
            # have eaten, never the reverse.
            if tag in _STRUCTURE_ELEMENTS:
                self._suppress.clear()
            else:
                # Track nesting so a "</div>" inside a dropped "<div>" does not
                # end suppression early. Void tags never close, never push them.
                if tag not in _VOID_ELEMENTS:
                    self._suppress.append(tag)
                return
        if tag in _ALLOWED_ELEMENTS:
            if tag in _VOID_ELEMENTS:      # "br" is the only allowed void tag
                self._out.append(f"<{tag}>")
                return
            self._out.append(f"<{tag}{self._attrs(attrs)}>")
            self._open.append(tag)
            if tag in _CELL_ELEMENTS:
                self.has_cell = True
            return
        if tag in _UNWRAPPED_ELEMENTS:
            return                          # tag gone, text keeps flowing
        # Unrecognised: dropped WITH its content. "<img>" goes because the CSP
        # would block these hosts anyway and paint a broken-image icon inside a
        # statute; script/iframe/form controls go because they are not law.
        # HTMLParser puts script and style into CDATA mode itself, so their
        # bodies arrive as one suppressed data chunk.
        if tag not in _VOID_ELEMENTS:
            self._suppress.append(tag)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        # A self-closing "<foo/>" must NOT open a suppression scope it will
        # never close — which is exactly what the inherited start-then-end
        # default would do, silently swallowing the rest of the table.
        tag = tag.lower()
        if self._suppress:
            return
        if tag not in _ALLOWED_ELEMENTS:
            return
        if tag in _VOID_ELEMENTS:
            self._out.append(f"<{tag}>")
            return
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if self._suppress:
            if tag in self._suppress:
                while self._suppress:
                    if self._suppress.pop() == tag:
                        break
            return
        if tag in _VOID_ELEMENTS or tag not in _ALLOWED_ELEMENTS:
            return
        if tag not in self._open:
            return                          # stray close tag: drop it
        # Unwind, closing anything left open inside, so
        # "<table><tr><td>x</table>" comes back out balanced.
        while self._open:
            popped = self._open.pop()
            self._out.append(f"</{popped}>")
            if popped == tag:
                break

    def handle_data(self, data: str) -> None:
        if self._suppress or not data:
            return
        self._out.append(escape(data, quote=False))

    def handle_entityref(self, name: str) -> None:  # pragma: no cover - charrefs on
        self.handle_data(unescape(f"&{name};"))

    def handle_charref(self, name: str) -> None:  # pragma: no cover - charrefs on
        self.handle_data(unescape(f"&#{name};"))

    # Comments, doctypes and processing instructions carry no law and are
    # dropped in silence — the pipeline's own "converted table" markers with
    # them.
    def handle_comment(self, data: str) -> None:
        return

    def handle_decl(self, decl: str) -> None:
        return

    def unknown_decl(self, data: str) -> None:
        return

    def handle_pi(self, data: str) -> None:
        return

    def result(self) -> str:
        while self._open:
            self._out.append(f"</{self._open.pop()}>")
        return "".join(self._out).strip()


def _span_value(raw: str | None) -> str | None:
    """``rowspan``/``colspan`` value, normalised, or ``None`` to drop it.

    Only an int in ``1..100`` survives. ``rowspan="0"`` means "to the end of the
    section" in the HTML spec and is an OCR artifact here; ``999`` is a parse
    error that would stretch one cell over the whole page; ``abc`` and a bare
    valueless attribute are noise. Emitting ``str(int(...))`` also normalises
    Arabic-Indic digits inside the attribute — presentation metadata, not body
    text, so the Latin-numerals carve-out for corpus text is untouched.
    """
    if raw is None:
        return None
    text = raw.strip()
    if not text:
        return None
    try:
        value = int(text)
    except (TypeError, ValueError):
        return None
    if not 1 <= value <= _MAX_SPAN:
        return None
    return str(value)


def sanitize_table_html(raw: str) -> str:
    """Allowlist re-serializer. Returns ``""`` if no ``<td>``/``<th>`` survived.

    Parse and REBUILD — never regex-strip (plan D6). Stdlib ``html.parser``
    only; no new dependency is permitted in either repo.

    Kept: ``table thead tbody tfoot tr th td caption br b strong i em sup sub``,
    with ``rowspan``/``colspan`` as the only attributes and only for an int in
    1..100. Unwrapped (tag dropped, text kept): ``a p span``. Everything else —
    ``img``, ``script``, ``iframe``, form controls, anything unrecognised — is
    dropped **with its content**, as are all other attributes (``style``,
    ``class``, ``id``, ``width``, ``align``) and every comment.

    The empty return is what rule 2 turns into "emit nothing": a fragment that
    kept no cell is not a table, and :func:`tables_by_ref` drops it so its token
    behaves exactly like one that was never ingested. A parse that blows up
    returns ``""`` for the same reason — a missing grid is a degradation, a
    half-parsed one is a hazard.
    """
    if not isinstance(raw, str) or not raw:
        return ""
    parser = _TableSanitizer()
    try:
        parser.feed(raw)
        parser.close()
    except Exception:
        return ""
    if not parser.has_cell:
        return ""
    out = parser.result()
    # A GRID WITH NOTHING IN IT IS NOT A TABLE. `has_cell` alone passes on a
    # corpse: full-corpus validation found 24 tables whose cells survived but
    # whose text had been eaten, so the token resolved and the reader got an
    # empty ruled box where a statute belonged — rule 2's "emit nothing" never
    # fired because, structurally, something had. Test the TEXT, not the tags.
    if not visible_text(out):
        return ""
    return out


def tables_by_ref(rows: Iterable[Mapping[str, Any]]) -> dict[str, ChunkTable]:
    """Raw ``chunk_tables_v2`` rows -> ``{table_ref: ChunkTable}``, sanitized.

    Keyed on ``table_ref`` **as read off the row** (rule 3). ``table_ref`` is
    UNIQUE corpus-wide, so one dict is safe for a whole regulation's worth of
    rows — which is what the single batched read per document produces.

    A row whose HTML sanitizes to ``""`` is **omitted**, not stored empty. That
    is deliberate: an omitted ref is indistinguishable from a ref that was never
    ingested, so both take the one well-tested path (the token is dropped) and
    there is no second "resolved but blank" state for a consumer to forget.

    Rows with no ``table_ref`` are skipped. ``table_md`` is never empty in the
    corpus (min 27 chars); a row that somehow has none still renders its grid,
    it just contributes nothing to the text-only channel.
    """
    out: dict[str, ChunkTable] = {}
    for row in rows or ():
        ref = row.get("table_ref")
        if not isinstance(ref, str) or not ref.strip():
            continue
        html = sanitize_table_html(row.get("table_html") or "")
        if not html:
            continue
        md = row.get("table_md")
        md = md.strip() if isinstance(md, str) else ""
        out[ref.strip()] = ChunkTable(table_ref=ref.strip(), html=html, md=md)
    return out


def _walk(body: str, tables: Mapping[str, ChunkTable]) -> Iterator[tuple[str, Any]]:
    """The single line walk both public renderers share.

    Yields ``("text", line)`` for every surviving prose line and
    ``("table", ChunkTable)`` for every resolved token, having already removed
    unresolved tokens *and* the blank line each one would have orphaned.

    Two details that are not decoration:

    * A token line is matched with :data:`TABLE_PLACEHOLDER` itself, so this
      walk and the corpus contract cannot disagree about what a token is.
    * A trailing carriage return is stripped before matching. A CRLF body would
      otherwise fail the trailing anchor and ship the literal token as prose —
      the exact leak rule 2 exists to stop. Being lenient here can only remove
      tokens, never invent them.
    """
    if not body:
        return
    swallow_blanks = False
    # "Nothing emitted yet" counts as ending blank, so a dropped token on the
    # first line does not leave a blank one behind it either.
    trailing_blank = True
    for raw_line in body.split("\n"):
        line = raw_line[:-1] if raw_line.endswith("\r") else raw_line
        match = TABLE_PLACEHOLDER.match(line)
        if match is not None:
            table = tables.get(match.group(1))
            if table is None:
                # Rule 2: the line vanishes. If the prose already ended on a
                # blank line, swallow the blank run that followed the token too
                # or the paragraph break doubles up where the table used to be.
                swallow_blanks = trailing_blank
                continue
            yield ("table", table)
            swallow_blanks = False
            trailing_blank = False
            continue
        if swallow_blanks:
            if not line.strip():
                continue
            swallow_blanks = False
        yield ("text", line)
        trailing_blank = not line.strip()


def _join(lines: list[str]) -> str:
    """Join prose lines, trimming blank lines at both ends. ``""`` if all blank.

    Only whole blank lines go; the lines themselves are untouched, so leading
    indentation inside a run of prose survives.
    """
    start, end = 0, len(lines)
    while start < end and not lines[start].strip():
        start += 1
    while end > start and not lines[end - 1].strip():
        end -= 1
    if start >= end:
        return ""
    return "\n".join(lines[start:end])


def visible_text(html: str) -> str:
    """The reader-visible text of a table fragment — tags gone, spacing collapsed.

    Not a rendering: a measuring tape. It answers "how much law does this grid
    put on the page", which is what an exposure budget needs and what neither
    ``len(html)`` (counts markup) nor ``len(md)`` (counts a DIFFERENT rendering
    of the same table) actually reports.
    """
    if not html:
        return ""
    return _WS_RUN.sub(" ", _TAG_RUN.sub(" ", html)).strip()


def table_weight(table: ChunkTable) -> int:
    """What a table costs a free-char budget: ``max(len(md), len(visible))``.

    The plan's D8 charged a table at ``len(table_md)`` alone, on the reasoning
    that ``md`` is byte-identical to the prose the token replaced — so the
    budget would buy exactly the law it bought before the feature existed.
    That reasoning is right for the corpus as a whole and WRONG at the tail,
    and the tail is the half that leaks. Measured over all 24,511 rows on
    2026-08-25:

    * mean ``md`` 878 chars vs mean visible text 786 — so ``md`` usually charges
      MORE than the grid renders, and the gate is already the conservative one;
    * but **548 tables (2.2%) render >500 chars more than ``md`` charges**, 33 of
      them >2,000 more, worst case **3,382**;
    * because **244 tables never got a prose conversion at all** — their ``md``
      is the ingestion error placeholder «[خطأ في التحويل - انتهت المهلة]», 31
      chars standing in for a full penalty schedule. `17405_reg_603_chunk_019`
      is one: two 3.2 KB violation-fine grids whose entire prose form is that
      sentence, twice.

    Charging ``len(md)`` there would serve a complete fines table to an
    anonymous crawler for 31 characters of a 600-character budget. Taking the
    max costs nothing on the 97.8% where ``md`` already dominates, and closes
    that hole exactly.

    Note what this also reveals: for those 244, ``content`` — the AGENT view —
    holds the error string too. The model has been reading «conversion failed»
    where a fines table belongs. Restoring the grid to the reader does not fix
    that; it is logged in the plan's §8 as a corpus-side follow-up.
    """
    return max(len(table.md or ""), len(visible_text(table.html or "")))


def split_body(body: str, tables: Mapping[str, ChunkTable]) -> list[Segment]:
    """Body -> alternating prose / table segments. Unresolved tokens DROPPED.

    Both consumers — the library section and the مراجع popup — build their
    payload from these segments, so nobody re-implements the regex and the two
    surfaces cannot drift.

    Guarantees, each of which has a test:

    * A text segment **never** contains a token line, and is never empty.
    * A token with no entry in ``tables`` produces no segment and no trace: its
      line is gone and no blank-line artifact marks where it stood.
    * The literal string ``TBL_`` cannot appear in any emitted ``text``.

    ``md`` rides on every table segment because the copy button pastes it, and
    ``weight`` because the gate charges by it — this module does neither, it
    just refuses to throw either value away. See :func:`table_weight` for why
    the two are not the same number.
    """
    segments: list[Segment] = []
    buffer: list[str] = []

    def flush() -> None:
        text = _join(buffer)
        buffer.clear()
        if text:
            segments.append({"kind": "text", "text": text})

    for kind, payload in _walk(body, tables):
        if kind == "text":
            buffer.append(payload)
            continue
        flush()
        segments.append(
            {
                "kind": "table",
                "ref": payload.table_ref,
                "html": payload.html,
                "md": payload.md,
                "weight": table_weight(payload),
            }
        )
    flush()
    return segments


def render_text_only(body: str, tables: Mapping[str, ChunkTable]) -> str:
    """Tokens -> ``table_md``. The copy string, and every text-only channel.

    Substituting the prose back in place restores the text to what ``content``
    says, which is why it always reads correctly: ``table_md`` is literally the
    block the table was flattened into, so the two views carry the same law.
    That equivalence is the strongest check available on this module and it has
    a test (``test_render_text_only_reproduces_the_prose``).

    Use this wherever HTML cannot go — «نسخ المحتوى», a plain-text export, a
    summary. An unresolved token still emits nothing (rule 2): a text channel is
    exactly where a raw ``TBL_…`` would be most likely to end up pasted into a
    memo.
    """
    lines: list[str] = []
    swallow_blanks = False
    for kind, payload in _walk(body, tables):
        if kind == "table":
            md = payload.md.strip()
            if md:
                lines.extend(md.split("\n"))
                swallow_blanks = False
            else:
                # A table with no prose (never in the corpus — min 27 chars)
                # contributes nothing here, so it has to leave no scar either:
                # same blank-line swallow the dropped-token path uses.
                swallow_blanks = not lines or not lines[-1].strip()
            continue
        if swallow_blanks:
            if not payload.strip():
                continue
            swallow_blanks = False
        lines.append(payload)
    return _join(lines)
