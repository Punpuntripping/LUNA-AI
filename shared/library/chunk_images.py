"""Chunk images — the figure instead of the filename, for BOTH views.

A regulation chunk is Arabic markdown, and 1,839 of them carry image markup
pointing at a file no app can reach::

    ![img-1.jpeg](images/page_005_img_001.jpeg)

Nothing in this repository has ever looked at it, so all three consumers ship it
today: the library prints the literal string as body text, مراجع emits a
relative ``<img>`` that 404s inside a cited statute, and the aggregator reads a
*filename* where a diagram belongs. Since 2026-08-29 the pixels and — more
importantly — the **words** live in the database beside the chunk::

    chunks_v2.content     the corpus, byte-identical, NEVER written. What BM25,
                          ``search_topics``, the embedder and every reranker
                          read, image markup and all (plan D2).
    chunk_images          one row per figure: ``source_basename`` (the
                          substitution key), ``title``, ``description``,
                          ``transcribed_text``, ``storage_path`` and ``meta``.

This module turns those rows into the two derived strings, and it is the ONE
implementation all four surfaces call — the library body, the مادة body, the
مراجع popup and the aggregator — so they cannot drift:

    the DISPLAY body   every span replaced by the figure's PIXELS
                       (:func:`place_images`, over ``chunk_tables.split_body``)
    the AGENT body     every span replaced by the figure's WORDS
                       (:func:`render_for_agent`)

That second half is the whole difference from ``chunk_tables``. A table was
flattened into prose before ingestion, so ``content`` already held the law and
the agent needed nothing. A figure was flattened into **nothing but its own
path**, so repairing the reader's view is only half the job.

Measured on prod (``dwgghvxogtwyaxmbgjod``) 2026-08-29:

    chunk_images rows                       5,347
    uploaded_at IS NULL                         0
    cited / orphan                      3,677 / 1,670
    chunks carrying a figure            1,598 / 48,429 (3.3%)
    jpeg / png                          4,772 / **575**
    chunks with a span and NO row         **656** (154 of them published)
    cited spans whole-line / **inline**  3,630 / **47**
    span length mean / p50 / max         41 / 44 / 68
    title 4–77 · description 98–2,008 · transcribed_text p50 31, max 4,854
    agent-body inflation, uncapped      +2,114 chars mean, **+28,318** worst

Eight rules are load-bearing here, and each one closes a measured trap:

1. **An unresolved span emits NOTHING** (D3). For tables this was defensive —
   0 of 24,511 tokens were unresolvable. Here it *fires*: 656 chunks carry
   markup with no row at all, 298 of those spans on published pages, because
   the vision pass judged those figures decorative or they sit in front matter.
   Deleting them IS the fix, not a fallback. The literal ``](images/`` may not
   survive in anything this module emits, on any path.
2. **Resolve by ``source_basename`` and replace the SPAN, never the line**
   (D4). 47 of the 3,677 cited spans sit inline inside a prose sentence; a
   whole-line rule silently drops every one and leaves the sentence looking
   finished. A span replace is identical on the other 3,630, so it is strictly
   safer and never worse.
3. **Orphans append after the last segment, ordered by ``n``** (D5). 1,670
   figures on 484 chunks have no markup at all — recovered from the source PDF
   and placed by line provenance. The two populations are disjoint by corpus
   invariant, so a cited figure can never also append.
4. **``uploaded_at IS NULL`` ⇒ unresolved** (D6). 0 rows today. A URL for
   absent bytes is a 404 inside a statute, which is the exact thing this module
   exists to stop, so it is checked in code and not asserted in a comment.
5. **Build the URL from ``storage_path``, never ``image_ref + ".jpeg"``** (D7).
   **575 of 5,347 are PNG.** ``storage_path`` already carries the right
   extension, and it is percent-encoded on the way out because four regulation
   refs contain Arabic.
6. **«الصورة {N}» is a RENDER-ORDER counter minted here** (D8). Never
   ``meta->>'n'`` — 120 of 418 regulations have gaps in it, worst case 383, so
   a reader would see «الصورة 402» and conclude 401 figures were lost. Never
   ``meta->>'n_in_chunk'`` either — it restarts every chunk. A repeated
   ``image_ref`` re-uses the number it got first (26 basenames appear twice in
   one chunk, genuinely the same figure cited twice).
7. **The gate charges a figure for what it puts in front of a reader, and every
   removed span is charged whether or not it renders** (D10). Every segment
   carries ``weight`` and ``span_len`` so the backend's budget arithmetic can
   never come out looser than the string cut it replaces.
8. **The agent body is CAPPED per chunk** (D13). Substituting the figure text
   inflates a chunk by 69% on average and by 28,318 chars at worst; past
   :data:`AGENT_FIGURE_BUDGET` the remaining figures collapse to their captions
   and a «(+N صورة أخرى لم تُدرج)» line, so the model is TOLD what it is not
   being shown rather than silently shorted.

This module is PURE: no database, no I/O, no config lookup, no gate and no
truncation. ``base_url`` is passed in (the caller reads
``get_settings().SUPABASE_URL``) so a restore into another project finds its own
images without a code change. Weighing a figure against the free-char budget
belongs to ``backend/app/services/library_service.py`` and stays there — which
is why every image segment carries its own ``weight`` and ``span_len``.

Plan: ``.claude/plans/chunk_image_rendering.md`` §2.
Corpus-side contract: ``agentic_for_ministry`` →
``ingestion/chunk_images/REFERENCE.md`` §3–§6.
"""
from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

from shared.library.chunk_tables import Segment

__all__ = [
    "AGENT_FIGURE_BUDGET",
    "IMAGE_SPAN",
    "IMAGE_TOKEN",
    "ChunkImage",
    "images_by_chunk",
    "place_images",
    "render_for_agent",
    "image_weight",
]


#: The CORPUS contract (REFERENCE.md §3.1), and the reason this walk cannot join
#: ``chunk_tables``': it matches a span **anywhere on a line**, because 47 live
#: spans sit inside a prose sentence. The capture group is ``source_basename``.
#: Do not anchor it to a whole line and do not merge it with
#: :data:`IMAGE_TOKEN` — they are different regexes for different jobs.
IMAGE_SPAN = re.compile(r"!\[[^\]]*\]\(images/([^)]+)\)")

#: OURS (D14): the whole-line stand-in the server projects onto the wire so a
#: rendered figure can travel as one short token inside ``text`` with its
#: payload beside it in ``images``. ``IMG_{N}`` is **minted by the server and
#: never derived from ``image_ref``** — ``image_ref`` is ``{reg_ref}_img_{n}``
#: and four regulations carry Arabic in their ref
#: (``17645_reg_الانظمة_002_chunk_001``), so a token built from it could not use
#: the ASCII anchor that makes this pattern safe. ``IMG_{N}`` *is* the caption
#: number, so the token and the label cannot disagree. Verified corpus-wide:
#: **0 chunks contain a whole-line ``IMG_\d+``** (8 contain one inline, which
#: the line anchor never matches).
#:
#: This module neither emits nor consumes the token — projection is the
#: backend's job. It lives here so there is exactly one spelling of it.
IMAGE_TOKEN = re.compile(r"^[ \t]*(IMG_\d+)[ \t]*$", re.M)

#: D13's per-chunk ceiling for :func:`render_for_agent`, in characters.
#: Measured over all 1,598 figure-bearing chunks: uncapped substitution adds a
#: mean of 2,114 chars to a chunk whose mean ``content`` is 3,076 (+69%), and on
#: 452 chunks (28%) the figure text is longer than the statute text. Per-image
#: trims (description 400, transcription 1,200) save only 10%; this ceiling does
#: the work, touching 177 chunks (11%) and bringing the mean add to 1,584.
AGENT_FIGURE_BUDGET = 4000

#: The public Storage bucket. Public and unsigned by design (REFERENCE.md §5);
#: the CSP already allows ``https://*.supabase.co`` for ``img-src``.
_BUCKET = "regulation-images"

#: The caption, split so :func:`image_weight` and the renderer cannot disagree
#: about what it costs. «الصورة {n}: {title}» — Latin digits, because the number
#: is app chrome (``project_latin_numerals_policy``), and the title verbatim,
#: because it is corpus text (the carve-out).
_CAPTION_PREFIX = "الصورة "
_CAPTION_SEP = ": "

#: The agent's labelled blockquote (§2.3), the exact shape
#: ``agents/simple_search/unfold.render_service_guide`` already uses for guide
#: screenshots. Labelled so the model never mistakes a description for the
#: statute's own prose; blockquoted so a multi-line description cannot break the
#: markdown around it.
_AGENT_FIGURE_MARK = "> 🖼 **"
_AGENT_TRANSCRIPT_LABEL = "> **نص الصورة:** "
_AGENT_ORPHAN_LABEL = "**صور مرفقة بهذا المقطع:**"


@dataclass(frozen=True)
class ChunkImage:
    """One placed figure, ready to render. Built ONLY by :func:`images_by_chunk`.

    Nothing downstream may construct one from a raw row: the URL rule (D7), the
    ``uploaded_at`` check (D6) and the ``contains_text`` gate on
    ``transcribed_text`` all live in that constructor, and a hand-built instance
    would quietly skip them.
    """

    #: Canonical id, ``{reg_ref}_img_{n}``. UNIQUE corpus-wide. It is the React
    #: key, and it is what the render counter keys on so a figure cited twice in
    #: one chunk gets one number, not two (D8).
    image_ref: str
    #: The substitution key for a cited figure — the basename inside
    #: ``![…](images/NAME)``. ``""`` for an orphan, which has no markup to match.
    source_basename: str
    #: 4–77 chars (mean 31). THE CAPTION. This is what ``service_guide_images``
    #: never had, and it is why this wing gets a ``<figcaption>`` where
    #: ``GuideBody`` deliberately has none (D9).
    title: str
    #: 98–2,008 chars of Arabic. The ``alt`` text — what a screen reader and a
    #: crawler get — and half of what the model gets. Never the filename.
    description: str
    #: The figure's OWN text, verbatim: where a spec table's numbers live, and
    #: the answer to "what does the diagram say". **Blank unless the row's
    #: ``contains_text`` was true** — see :func:`images_by_chunk`. Never printed
    #: to the reader in v1 (D9), but it IS charged to the gate (D10) and it IS
    #: given to the model (§2.3).
    transcribed_text: str
    #: Public Storage URL, built from ``storage_path`` and percent-encoded (D7).
    #: Never ``image_ref + ".jpeg"`` — 575 of 5,347 rows are PNG.
    url: str
    #: Intrinsic dimensions, from ``meta``. ``0`` when the row carries none —
    #: the renderer must then omit the attribute rather than emit ``width="0"``.
    #: They reserve the box before the bytes land: one chunk carries 31 figures
    #: and the widest is 12,250px, so without them a section reflows per image.
    width: int
    height: int
    #: ``"cited"`` | ``"orphan"``. Decides the render path, and it is the ONLY
    #: thing that does. An unrecognised value falls to ``"cited"`` — see
    #: :func:`images_by_chunk` for why that is the fail-soft direction.
    origin: str
    #: ``meta->>'n'``: the index within the REGULATION. **Ordering only, and
    #: only for orphans.** Never a label — 120 of 418 regulations have gaps in
    #: it, worst case a gap of 383 (D8).
    n: int


# --- construction -------------------------------------------------------------


def _meta_of(row: Mapping[str, Any]) -> Mapping[str, Any]:
    meta = row.get("meta")
    return meta if isinstance(meta, Mapping) else {}


def _pick(row: Mapping[str, Any], meta: Mapping[str, Any], key: str) -> Any:
    """Read ``key`` from the row, then from ``meta``. Both spellings are live.

    The plan's batched read (§3.2) selects ``meta`` whole, so ``origin``, ``n``,
    ``width`` and ``height`` arrive nested. REFERENCE.md §7's query aliases them
    to the top level (``meta->>'origin' as origin``). A consumer that used
    either one must not silently get zeros, so both are accepted and the
    top-level alias — the more explicit of the two — wins.
    """
    value = row.get(key)
    if value is None:
        value = meta.get(key)
    return value


def _as_int(value: Any) -> int:
    """``int(value)`` or ``0``. ``meta`` is jsonb, so a number may arrive as a str."""
    if isinstance(value, bool):
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _text(value: Any) -> str:
    return str(value).strip() if isinstance(value, str) else ""


def _storage_prefix(base_url: Any) -> str:
    """``{base_url}/storage/v1/object/public/regulation-images``, or ``""``.

    ``""`` when the caller handed over nothing usable. That is deliberate and it
    is checked by :func:`images_by_chunk`, which then resolves **no** figures:
    an empty base would build a RELATIVE URL against the app origin, which is
    precisely the broken-image bug this module exists to delete (D6's reasoning,
    one level up). The fail-soft direction here is always *prose without its
    figures*, never a dead ``<img>``.
    """
    base = _text(base_url).rstrip("/")
    if not base:
        return ""
    return f"{base}/storage/v1/object/public/{_BUCKET}"


def images_by_chunk(
    rows: Iterable[Mapping[str, Any]],
    *,
    base_url: str,
) -> dict[str, list[ChunkImage]]:
    """Raw ``chunk_images`` rows -> ``{chunk_id: [ChunkImage]}``.

    THE constructor. Every rule that decides whether a figure exists at all
    lives here, so a consumer holding a :class:`ChunkImage` may assume it is
    renderable and nothing downstream re-checks:

    * **``uploaded_at`` falsy ⇒ the row is dropped** (D6). 0 rows today; a URL
      for absent bytes is a 404 inside a statute.
    * **No ``storage_path``, no ``image_ref`` or no ``chunk_id`` ⇒ dropped.**
      Each is a key something downstream needs, and a row missing one is
      indistinguishable from a row that was never ingested — which is the one
      well-tested path (the span is deleted, D3).
    * **``base_url`` blank ⇒ nothing resolves at all.** See
      :func:`_storage_prefix`.
    * **``transcribed_text`` survives only when ``contains_text``** (§2.3), and
      that is enforced here because :class:`ChunkImage` deliberately carries no
      ``contains_text`` field: the flag has exactly one consequence, so it is
      applied once rather than re-checked at three call sites. A row that does
      not carry the column at all is trusted (the caller forgot it from the
      select list; blanking 4,156 rows' worth of the answer would be the worse
      failure), a row that carries it false is honoured.

    The URL is percent-encoded with ``safe="/"`` — ``storage_path`` is
    ``{regulation_ref}/{image_ref}.{ext}`` and four regulation refs contain
    Arabic, so a raw path would be a URL only a browser could guess at. Same
    call ``library_service`` already makes for the guides bucket.

    Each list is sorted by ``n`` (stable, so equal ``n`` keeps row order). That
    is REFERENCE.md §7's own ordering and it is what D5's orphan block needs;
    cited figures are resolved by basename and never by position, so the sort
    cannot affect them.
    """
    prefix = _storage_prefix(base_url)
    if not prefix:
        return {}

    out: dict[str, list[ChunkImage]] = {}
    for row in rows or ():
        if not isinstance(row, Mapping):
            continue
        chunk_id = _text(row.get("chunk_id"))
        image_ref = _text(row.get("image_ref"))
        storage_path = _text(row.get("storage_path")).lstrip("/")
        if not chunk_id or not image_ref or not storage_path:
            continue
        if not row.get("uploaded_at"):  # D6 — bytes absent, treat as unresolved
            continue

        meta = _meta_of(row)
        contains_text = row.get("contains_text", True)
        if contains_text is None:
            contains_text = True

        out.setdefault(chunk_id, []).append(
            ChunkImage(
                image_ref=image_ref,
                source_basename=_text(row.get("source_basename")),
                title=_text(row.get("title")),
                description=_text(row.get("description")),
                transcribed_text=(
                    _text(row.get("transcribed_text")) if contains_text else ""
                ),
                url=f"{prefix}/{quote(storage_path, safe='/')}",
                width=_as_int(_pick(row, meta, "width")),
                height=_as_int(_pick(row, meta, "height")),
                # Anything that is not exactly "orphan" is treated as cited, so
                # an unknown origin resolves its span if it has a basename and
                # renders nowhere if it does not. The other default would append
                # a figure to the end of a chunk on a position nobody predicted
                # — a placement claim the data does not support (D5/D16).
                origin="orphan" if _pick(row, meta, "origin") == "orphan" else "cited",
                n=_as_int(_pick(row, meta, "n")),
            )
        )

    for images in out.values():
        images.sort(key=lambda image: image.n)
    return out


# --- the caption and what it costs --------------------------------------------


def _caption(image: ChunkImage, n: int) -> str:
    """«الصورة {n}: {title}» — Latin digits (ours), title verbatim (the corpus')."""
    title = image.title
    if not title:
        return f"{_CAPTION_PREFIX}{n}"
    return f"{_CAPTION_PREFIX}{n}{_CAPTION_SEP}{title}"


def image_weight(image: ChunkImage) -> int:
    """What one figure costs a free-char budget, exclusive of the span it replaced.

    D10, and it is the load-bearing half of this module's contract with the
    gate. Today the gate charges ``len("![img-1.jpeg](images/page_005_img_001.jpeg)")``
    — mean 41 chars — and renders a broken image. Tomorrow it renders a diagram
    that may carry a full specification table in pixels, so::

        weight   = max(span_len, image_weight(image))   # D10, on the segment
        span_len = len(span)                            # charged even when nothing renders

    Two things are counted and one is not:

    * ``len(caption)`` — the only text that reaches the DOM, and it is small
      («الصورة 12: » plus a 31-char mean title).
    * ``len(transcribed_text)`` — the law the reader's *eye* gets: p50 31 (a
      photo with a word on it), p95 755, max 4,854 (a fines table photographed
      whole). Charging it is what stops an anonymous crawler collecting a
      نظام's entire spec schedule as JPEGs against a 600-char budget. Blank
      unless ``contains_text``, so a figure with no readable text is charged
      only for its caption.
    * The render number's DIGITS are not counted. They must not be: the same
      figure is «الصورة 7» on the document and «الصورة 1» on its own مادة page
      (D17), and a weight that moved with the counter would make one figure cost
      two different amounts on two surfaces. 1–3 characters, well inside the
      noise of a 600-char budget.

    ``max(span_len, …)`` and not this number alone is what makes the budget
    invariant exact by construction rather than by measurement: **every
    character ``content`` spent on a span is still spent, plus whatever the
    figure adds on top.**
    """
    title = image.title or ""
    caption = len(_CAPTION_PREFIX) + (len(_CAPTION_SEP) + len(title) if title else 0)
    return caption + len(image.transcribed_text or "")


def _image_segment(image: ChunkImage, n: int, span_len: int) -> Segment:
    """The image segment. THE contract the backend, the wire and مراجع consume.

    Nine keys, and the three that are missing are missing on purpose:
    ``transcribed_text`` (not printed to a reader in v1 — D9 — though it is
    charged through ``weight``), ``origin`` (the render path is already decided
    by the time a segment exists) and ``source_basename`` (its span is gone).
    """
    return {
        "kind": "image",
        "image_ref": image.image_ref,
        "n": n,
        "title": image.title,
        "description": image.description,
        "url": image.url,
        "width": image.width,
        "height": image.height,
        "weight": max(span_len, image_weight(image)),
        "span_len": span_len,
    }


# --- the walk -----------------------------------------------------------------


def _partition(
    images: Iterable[ChunkImage],
) -> tuple[dict[str, ChunkImage], list[ChunkImage]]:
    """-> (``{source_basename: cited image}``, orphans in ``n`` order).

    A cited row with no basename is in NEITHER: it has nothing to match and no
    position anyone predicted, so it renders nowhere. That is the fail-soft
    direction (prose without a figure), and it cannot happen in the corpus —
    every one of the 3,677 cited rows has its basename present in live
    ``content``.

    First basename wins. ``(regulation_ref, source_basename)`` is unique, so a
    collision is a caller mixing two regulations' rows into one chunk's list.
    """
    by_name: dict[str, ChunkImage] = {}
    orphans: list[ChunkImage] = []
    for image in images or ():
        if image.origin == "orphan":
            orphans.append(image)
            continue
        if image.source_basename and image.source_basename not in by_name:
            by_name[image.source_basename] = image
    orphans.sort(key=lambda image: image.n)  # D5, and stable on ties
    return by_name, orphans


def _rejoin(left: str, right: str) -> str:
    """Close the hole a DELETED span left, without inventing a paragraph break.

    D3 says an unresolved span emits nothing — "nothing" includes the whitespace
    it orphaned. 3,630 of 3,677 cited spans sit alone on their own line, so the
    naive ``left + right`` leaves the line's own newline behind and the
    paragraph break doubles up exactly where the figure used to be; the 47
    inline ones leave two spaces mid-sentence instead of one. This is the
    span-level equivalent of ``chunk_tables._walk``'s blank swallow.

    The rule: whatever whitespace touched the span from either side collapses to
    the *structural* separation of the two sides, minus the line the span
    occupied when it had one to itself.

        "أ\\n\\nSPAN\\n\\nب"  -> "أ\\n\\nب"    one paragraph break, not two
        "أ\\nSPAN\\nب"      -> "أ\\nب"      one line break, not two
        "أ SPAN ب"        -> "أ ب"        one space, not two
        "أSPANب"          -> "أب"         no space invented
    """
    stripped_left = left.rstrip()
    tail = left[len(stripped_left) :]
    stripped_right = right.lstrip()
    head = right[: len(right) - len(stripped_right)]

    newlines_before = tail.count("\n")
    newlines_after = head.count("\n")
    if newlines_before and newlines_after:
        # The span had a line to itself; that line goes with it.
        count = newlines_before + newlines_after - 1
    else:
        count = newlines_before + newlines_after

    if count >= 2:
        gap = "\n\n"
    elif count == 1:
        gap = "\n"
    elif tail or head:
        gap = " "
    else:
        gap = ""
    return f"{stripped_left}{gap}{stripped_right}"


def _split_spans(
    text: str,
    by_name: Mapping[str, ChunkImage],
) -> list[tuple[str, Any]]:
    """Text -> alternating ``("text", str)`` / ``("image", (ChunkImage, span_len))``.

    The one span walk both public renderers share, so the display view and the
    agent view cannot disagree about where a figure sits or which spans died.

    * A span with no entry in ``by_name`` is REMOVED with its whitespace (D3,
      via :func:`_rejoin`) and contributes no element at all.
    * A resolved span closes the current text run and opens a new one, because a
      figure is a BLOCK: splicing a three-line blockquote or a ``<figure>`` into
      the middle of a sentence is broken markup in either view. The 47 inline
      spans therefore break their sentence in two on both surfaces — identically,
      which is the point.
    * ``span_len`` rides out with each image because the gate charges it (D10)
      and the match is the only place it is knowable.
    * The whitespace that touched a RESOLVED span is absorbed by the block, on
      both sides. It was layout around an inline token; it is now the gap
      between a paragraph and a figure, and the app's own styling owns that.
      Leaving it in place would put a stray leading space on 47 sentences and,
      where the gap is wide enough, hand a markdown renderer an indented code
      block.

    Blank-line trimming at the ENDS of a run is still the caller's, because the
    two callers trim differently.
    """
    elements: list[tuple[str, Any]] = []
    run = ""
    dropped = False
    after_image = False
    position = 0

    for match in IMAGE_SPAN.finditer(text):
        left = text[position : match.start()]
        position = match.end()
        if after_image:
            left = left.lstrip()
            after_image = False
        run = _rejoin(run, left) if dropped else run + left
        dropped = False

        image = by_name.get(match.group(1))
        if image is None:
            dropped = True  # D3 — and the gap closes on the NEXT append
            continue

        elements.append(("text", run.rstrip()))
        run = ""
        after_image = True
        elements.append(("image", (image, match.end() - match.start())))

    tail = text[position:]
    if after_image:
        tail = tail.lstrip()
    run = _rejoin(run, tail) if dropped else run + tail
    elements.append(("text", run))
    return elements


def _trim_blank_edges(text: str) -> str:
    """Drop blank lines at both ends. ``""`` if all blank.

    Mirrors ``chunk_tables._join`` exactly — whole blank lines only, the lines
    themselves untouched — so a text segment coming out of this module has the
    same shape as one coming out of ``split_body``.
    """
    lines = text.split("\n")
    start, end = 0, len(lines)
    while start < end and not lines[start].strip():
        start += 1
    while end > start and not lines[end - 1].strip():
        end -= 1
    if start >= end:
        return ""
    return "\n".join(lines[start:end])


class _Counter:
    """The render-order counter (D8), and the memory that makes repeats agree.

    Minted here and nowhere else. A repeated ``image_ref`` re-uses the number it
    got the first time — 26 basenames appear more than once in a single chunk,
    genuinely the same figure cited twice, and it must not become «الصورة 3» and
    «الصورة 7».
    """

    def __init__(self, start: int) -> None:
        self.next_index = max(1, int(start))
        self._seen: dict[str, int] = {}

    def number(self, image_ref: str) -> int:
        existing = self._seen.get(image_ref)
        if existing is not None:
            return existing
        assigned = self.next_index
        self._seen[image_ref] = assigned
        self.next_index += 1
        return assigned


def place_images(
    segments: Sequence[Segment],
    images: Iterable[ChunkImage],
    *,
    start_index: int = 1,
) -> tuple[list[Segment], int]:
    """Splice figures into ``chunk_tables.split_body`` output. -> (segments, next_index).

    A **second pass**, deliberately, and not a branch inside ``split_body``:
    that walk is line-based and turns whole-line ``TBL_…`` tokens into table
    segments, while an image span is not a line — 47 of them live inside a
    sentence — so joining the two walks would mean teaching the line walk a
    second, inline grammar. Running afterwards over the text segments only costs
    one extra pass and keeps each grammar in one place.

    The composition with tables is SAFE and was measured, not assumed: of the
    799 cited rows on chunks that also carry ``content_display``, all 799 keep
    their ``source_basename`` in it — **0 basenames were ever swallowed into a
    ``TBL_`` token** (§9.3). 433 chunks carry both.

    Order of operations:

    1. Every non-text segment passes through untouched, by identity. A text
       segment with no span passes through untouched too, so the 96.7% of chunks
       that carry no figure get byte-identical output and no allocation.
    2. Inside a text segment, spans are lifted out in place: unresolved ones
       vanish with their whitespace (D3), resolved ones become image segments
       between the surrounding prose runs (D4).
    3. Orphans append after the LAST segment, in ``n`` order (D5). They can
       never double-show with step 2: the corpus invariant is that an orphan's
       basename matches nothing in its chunk's ``content``, which is *why* it is
       an orphan.
    4. Each emitted figure takes the next number from the counter unless its
       ``image_ref`` already has one in this scope (D8).

    ``start_index``/``next_index`` are how the caller threads ONE counter across
    a document's sections in reading order (D8) — and the reading order is a
    precondition, not a nicety: a counter threaded through an unordered loop
    numbers figures in whatever order the rows arrived. On a مادة page the scope
    is the page, so the caller simply starts at 1 again (D17).

    Guarantees, each of which has a test:

    * The literal ``](images/`` cannot appear in any emitted ``text``.
    * A text segment is never empty and never holds a span.
    * A dropped span leaves no blank-line artifact where it stood.
    """
    counter = _Counter(start_index)
    by_name, orphans = _partition(images)
    placed: list[Segment] = []

    for segment in segments or ():
        if not isinstance(segment, Mapping) or segment.get("kind") != "text":
            placed.append(segment)
            continue
        text = segment.get("text")
        if not isinstance(text, str) or not IMAGE_SPAN.search(text):
            placed.append(segment)  # the 96.7% case: untouched, by identity
            continue

        for kind, payload in _split_spans(text, by_name):
            if kind == "text":
                run = _trim_blank_edges(payload)
                if run:
                    placed.append({"kind": "text", "text": run})
                continue
            image, span_len = payload
            placed.append(_image_segment(image, counter.number(image.image_ref), span_len))

    for image in orphans:
        # span_len 0: an orphan removed no markup, so it cost the budget nothing
        # before this feature and charges only what it now renders (D10).
        placed.append(_image_segment(image, counter.number(image.image_ref), 0))

    return placed, counter.next_index


# --- the agent body -----------------------------------------------------------


def _collapse(text: str) -> str:
    """Whitespace-collapsed to one line.

    Not cosmetic: a blank line inside a description or a transcription would
    TERMINATE the blockquote, and the continuation would then read as the
    statute's own prose — the exact confusion the «🖼» label exists to prevent.
    Same call ``render_service_guide`` makes on a guide screenshot's description.
    """
    return " ".join((text or "").split())


def _agent_caption_line(image: ChunkImage, n: int) -> str:
    return f"{_AGENT_FIGURE_MARK}{_caption(image, n)}**"


def _agent_block(image: ChunkImage, n: int) -> str:
    """The labelled blockquote (§2.3)::

        > 🖼 **الصورة 3: مخطط تدفق إجراءات الترخيص**
        > {description}
        > **نص الصورة:** {transcribed_text}

    ``transcribed_text`` appears only when the row's ``contains_text`` was true
    (4,156 of 5,347) — :func:`images_by_chunk` has already blanked it otherwise —
    and is never fabricated for the rest.

    **Nothing about the image itself travels.** No URL, no bucket path, no
    ``image_ref``, no bytes. The model cannot open an image, so shipping any of
    it is pure cost.
    """
    lines = [_agent_caption_line(image, n)]
    description = _collapse(image.description)
    if description:
        lines.append(f"> {description}")
    transcription = _collapse(image.transcribed_text)
    if transcription:
        lines.append(f"{_AGENT_TRANSCRIPT_LABEL}{transcription}")
    return "\n".join(lines)


def render_for_agent(
    content: str,
    images: Iterable[ChunkImage],
    *,
    max_chars: int = AGENT_FIGURE_BUDGET,
) -> str:
    """``content`` with every span replaced by the figure's WORDS. The agent body.

    The half ``chunk_tables`` never needed. A table was flattened into prose
    before ingestion, so ``content`` already carried its law; a figure was
    flattened into its own file path, so the aggregator has been reading
    ``page_005_img_001.jpeg`` where a diagram belongs — 61 of 3,800 regulation
    citations (1.6%), bounded by ``chunks_v2.has_images`` so the extra read is
    not issued on the other 98.4% of turns (D12).

    ⚠ **This is not ``content`` and must never be indexed, embedded or
    reranked.** ``content`` stays what BM25, ``search_topics``, the embedder and
    every reranker see (D2). This string rides BESIDE it — as
    ``RegURAResult.chunk_agent_content``, never over ``chunk_content`` — so
    مراجع's «نسخ المحتوى» and every consumer that ignores the new field keep
    exactly today's bytes.

    Four rules:

    * **A chunk with nothing to do is returned byte-identical.** No rows and no
      spans ⇒ ``content`` itself, not a normalised copy of it. That is 96.7% of
      the corpus and it must not pay for this feature.
    * **An unresolved span still emits nothing** (D3). 656 chunks carry markup
      with no row; here the reader is a model, which would do something worse
      than print a filename — it would try to interpret it.
    * **Orphans append at the end**, under one «صور مرفقة بهذا المقطع:» line,
      because their position in the prose is ``predicted`` and presenting a
      guessed position as a certain one is a claim we cannot make to a model
      that will cite it (§2.3).
    * **The per-chunk ceiling is enforced here** (D13). Figures render in full
      until ``max_chars`` of figure text is spent; the first one that does not
      fit closes the full-text channel for the rest of the chunk, and every
      figure after it collapses to its caption line. A closing
      «(+{k} صورة أخرى لم تُدرج)» says how many. The monotone rule is the same
      one the reader's gate uses (D11) and it is what keeps the sequence
      honest — figures fill until the budget runs out and then stop, rather than
      appearing around the holes.

    The ceiling governs the DESCRIPTIONS and TRANSCRIPTIONS, which is where the
    2,114-char mean inflation lives. The caption lines that ride on top are ~45
    chars each and are the whole point of the degradation: the model is told
    what it is not being shown rather than silently shorted, so charging them
    against the budget could suppress the notice itself.

    A figure cited twice in one chunk (26 basenames) is DESCRIBED once. The
    second occurrence emits its caption line — same number (D8), so the model
    can see it is the same figure — and is neither charged again nor counted in
    the tally, because nothing was withheld: its words are verbatim above. The
    display path renders both in full instead, because a reader looking at the
    second citation cannot scroll a prompt.
    """
    if not isinstance(content, str):
        return ""
    by_name, orphans = _partition(images)
    if not by_name and not orphans and not IMAGE_SPAN.search(content):
        return content  # the 96.7% case, byte-identical

    counter = _Counter(1)  # the agent's scope is the CHUNK
    budget = max(0, int(max_chars))
    spent = 0
    figures_open = True
    described: set[str] = set()
    # Distinct figures, not occurrences: a figure withheld and then cited again
    # was withheld ONCE, and «(+2 صورة أخرى)» for one missing diagram is a lie
    # in the one direction the model cannot check.
    withheld: set[str] = set()
    blocks: list[str] = []

    def figure(image: ChunkImage) -> str:
        nonlocal spent, figures_open
        n = counter.number(image.image_ref)
        if image.image_ref in described:
            return _agent_caption_line(image, n)  # already described, in full, above
        if figures_open:
            block = _agent_block(image, n)
            if spent + len(block) <= budget:
                spent += len(block)
                described.add(image.image_ref)
                return block
            figures_open = False  # D11's monotone rule, for the model
        withheld.add(image.image_ref)
        return _agent_caption_line(image, n)

    for kind, payload in _split_spans(content, by_name):
        if kind == "text":
            run = _trim_blank_edges(payload)
            if run:
                blocks.append(run)
            continue
        image, _span_len = payload
        blocks.append(figure(image))

    if orphans:
        blocks.append(_AGENT_ORPHAN_LABEL)
        blocks.extend(figure(image) for image in orphans)

    if withheld:
        blocks.append(f"(+{len(withheld)} صورة أخرى لم تُدرج)")

    return "\n\n".join(blocks)
