"""What an agent reads of a long attachment — sections, caps, trimming.

Plan: ``.claude/plans/attachment_segmentation.md`` §4.6–§4.7 (owner decisions
D11–D16, 2026-11-11).

Pure functions — no DB, no LLM, no masking. Callers load the stored sections
(``agents.memory.attachment_segmenter``), encode them when the recipient reads
masked text (وضع السرية), and hand them here.

Who keeps what (by the segmenter's ``value``):

- deep search (case expander, reg expander, aggregator) — ``core`` only (D11);
- writer — ``metadata`` + ``core`` + ``supporting`` (D12; ``metadata`` until the
  metadata agent exists);
- ``boilerplate`` and ``repeat`` are never delivered and never named.

Caps (D14, D16): 100k per document, 300k per recipient. Over a cap every
delivered section keeps the same share of its length, cut from its end back to
a line break («اختُصر…» note). A document with no usable map (segmenter failed
or late) is delivered as its full text, treated as ONE section for the caps.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any

from shared.config import get_settings

#: Attachments longer than this are segmented (D9); shorter ones travel whole.
SEGMENT_MIN_CHARS = 5_000
#: Delivered text per document (D14).
PER_DOC_CAP = 100_000
#: Delivered text per recipient (D14) — summaries and headings do not count,
#: the same accounting as the old 30k cap.
TOTAL_CAP = 300_000

DEEP_SEARCH_VALUES: frozenset[str] = frozenset({"core"})
WRITER_VALUES: frozenset[str] = frozenset({"metadata", "core", "supporting"})
#: Left out without a marker — nothing a reader would miss.
SILENT_VALUES: frozenset[str] = frozenset({"boilerplate", "repeat"})

TRIM_NOTE = "[…] اختُصر هذا المقطع لضيق المساحة"

# ── Pinned official articles — .claude/plans/completion_search.md §4.7 ─────────
#: One pinned article is cut at this many characters (owner decision C23).
PIN_ARTICLE_CAP = 10_000
#: Safety net: pinned text per document. Past it every pinned article in that
#: document keeps the same share. Pinned text never counts toward the caps above.
PIN_DOC_NET = 30_000
#: The line over a pinned article. It says where the text comes from, because the
#: reader must not take it for the document's own words.
PIN_TAG = "من المكتبة النظامية، لا من المستند — {title}"
PIN_CUT_NOTE = "[…] اختُصر نص هذه المادة"
#: Over the articles of a document that has no sections to hang them under.
LOOSE_PINS_HEADING = "〔نصوص نظامية من المكتبة أشار إليها هذا المستند — ليست جزءًا منه〕"
#: ``Section.value`` of the carrier that brings such a document's articles through
#: the same plumbing as real sections. No recipient keeps it and it names nothing.
PINS_ONLY_VALUE = "pins_only"


@dataclass(frozen=True)
class SectionPin:
    """One official article, as it is printed under a section. Public text — never encoded."""

    article_id: str
    title: str          # «المادة 77 من نظام العمل»
    text: str           # already cut to the limits
LEFT_OUT_PREFIX = "[…] لم يُعرض هنا: "
LEFT_OUT_MORE = "وغيرها"
#: How many section descriptions one left-out marker names before «وغيرها».
MAX_LEFT_OUT_NAMES = 6


@dataclass(frozen=True)
class Section:
    """One stored section of an attachment, in document order."""

    seq: int
    value: str
    what: str
    text: str
    label: str = ""
    #: seg-v5.2+: who the section belongs to (the document's role words) and the
    #: party that filed or relies on it — shown in the section's tag. Empty on
    #: older maps.
    owner: str = ""
    brought_by: str = ""
    #: Where the section sits in the attachment's text — the link to a citation's
    #: position. ``-1`` when the row carries none.
    char_start: int = -1
    char_end: int = -1
    #: Official articles the section cites (completion_search.md §4.7). Empty unless
    #: ``ATTACHMENT_PIN_DELIVERY_ENABLED`` — the rendered text is then byte-identical.
    pins: tuple[SectionPin, ...] = ()

    def tag(self) -> str:
        """``what — owner، قدّمه brought_by`` (each part only when present)."""
        what = " ".join(str(self.what or "").split())
        owner = " ".join(str(self.owner or "").split())
        by = " ".join(str(self.brought_by or "").split())
        who = f"{owner}، قدّمه {by}" if owner and by else owner or (f"قدّمه {by}" if by else "")
        return f"{what} — {who}" if what and who else what or who


# ---------------------------------------------------------------------------
# Gate
# ---------------------------------------------------------------------------


def delivery_enabled(user_id: str | None) -> bool:
    """``ATTACHMENT_SEGMENT_DELIVERY_ENABLED`` and (no allowlist, or the user is
    on ``ATTACHMENT_SEGMENT_DELIVERY_USERS``). Never raises; off on error."""
    try:
        settings = get_settings()
        if not getattr(settings, "ATTACHMENT_SEGMENT_DELIVERY_ENABLED", False):
            return False
        raw = getattr(settings, "ATTACHMENT_SEGMENT_DELIVERY_USERS", None) or ""
        allowed = {u.strip() for u in str(raw).split(",") if u.strip()}
        return not allowed or str(user_id or "") in allowed
    except Exception:  # noqa: BLE001 — off is today's behaviour
        return False


# ---------------------------------------------------------------------------
# Trimming
# ---------------------------------------------------------------------------


def keep_ratio(chars: int, cap: int) -> float:
    """Share of ``chars`` that fits ``cap`` (1.0 when it already fits)."""
    if chars <= 0 or chars <= cap:
        return 1.0
    return max(cap, 0) / chars


def trim_text(text: str, ratio: float) -> str:
    """The first ``ratio`` of ``text``, cut back to the last line break (or, failing
    that, the last space) inside that length — never mid-word unless the span has
    no break at all in its second half."""
    if ratio >= 1.0:
        return text
    limit = int(len(text) * ratio)
    if limit <= 0:
        return ""
    cut = text[:limit]
    floor = limit // 2
    newline = cut.rfind("\n")
    if newline >= floor:
        cut = cut[:newline]
    else:
        space = cut.rfind(" ")
        if space >= floor:
            cut = cut[:space]
    return cut.rstrip()


def proportional_ratios(
    variable_chars: Mapping[str, int],
    fixed_chars: int,
    *,
    per_doc_cap: int = PER_DOC_CAP,
    total_cap: int = TOTAL_CAP,
) -> tuple[dict[str, float], float]:
    """Keep-ratio per document for the trimmable text, and the recipient-wide factor.

    ``variable_chars`` — per document, the delivered text that may be trimmed (its
    kept sections, or its whole text when it has no map). ``fixed_chars`` —
    delivered text that is never trimmed (whole short documents).

    Per document first (``per_doc_cap``), then every document by the same factor so
    the total fits ``total_cap - fixed_chars``. Example: nothing fixed, 400k
    trimmable → every section keeps 75%.
    """
    per_doc = {k: keep_ratio(v, per_doc_cap) for k, v in variable_chars.items()}
    after_doc = sum(v * per_doc[k] for k, v in variable_chars.items())
    room = total_cap - fixed_chars
    factor = keep_ratio(int(after_doc), room) if after_doc else 1.0
    if room <= 0:
        factor = 0.0
    return {k: per_doc[k] * factor for k in variable_chars}, factor


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def kept_chars(sections: Iterable[Section], keep: frozenset[str]) -> int:
    """Characters of the sections a recipient keeps."""
    return sum(len(s.text) for s in sections if s.value in keep)


def has_kept(sections: Iterable[Section], keep: frozenset[str]) -> bool:
    return any(s.value in keep and s.text.strip() for s in sections)


def _left_out_marker(names: Sequence[str]) -> str:
    shown = list(names[:MAX_LEFT_OUT_NAMES])
    if len(names) > MAX_LEFT_OUT_NAMES:
        shown.append(LEFT_OUT_MORE)
    return LEFT_OUT_PREFIX + "، ".join(shown)


def render_sections(
    sections: Sequence[Section],
    keep: frozenset[str],
    *,
    ratio: float = 1.0,
    file_wi: int | None = None,
) -> tuple[str, bool]:
    """The kept sections in document order, each under a ``〔what〕`` tag.

    A run of left-out sections between kept ones becomes ONE marker naming them by
    their descriptions; ``boilerplate`` / ``repeat`` are left out silently. With
    ``ratio < 1`` every kept section keeps that share of its length (cut from its
    end) and ends with :data:`TRIM_NOTE`. Returns ``(text, trimmed)``.

    ``file_wi`` (the aggregator's file refs, aggregator_file_refs.md §4.2, D12): the
    document's WI number. Each kept section's tag then opens with its ready file
    tag, ``〔F3·S7 · what — owner〕`` — ``S7`` is the ``seq`` the «الخيوط» squares
    show — so the aggregator copies it as it is. Pinned articles get no tag.
    ``None`` → the text is byte-identical to before.
    """
    parts: list[str] = []
    left_out: list[str] = []
    trimmed = False

    def flush() -> None:
        if left_out:
            parts.append(_left_out_marker(left_out))
            left_out.clear()

    for section in sorted(sections, key=lambda s: s.seq):
        what = " ".join(str(section.what or "").split())
        if section.value in keep:
            text = section.text.strip()
            if not text:
                continue
            flush()
            body = trim_text(text, ratio)
            if len(body) < len(text):
                trimmed = True
                body = f"{body}\n{TRIM_NOTE}" if body else TRIM_NOTE
            tag = section.tag()
            if file_wi is not None:
                from shared.workspace.file_refs import format_file_tag

                ready = format_file_tag(file_wi, section.seq)
                tag = f"{ready} · {tag}" if tag else ready
            parts.append(f"〔{tag}〕\n{body}" if tag else body)
            # The official articles this section cites, right under it (C19, C22).
            parts.extend(_render_pin(pin) for pin in section.pins)
        elif section.value not in SILENT_VALUES and what and what not in left_out:
            left_out.append(what)
    # A trailing run names what follows the last kept section too.
    flush()
    return "\n\n".join(parts), trimmed


FALLBACK_CUT_NOTE = "[انتهى المعروض: {shown} حرف من أصل {total} — بقية الوثيقة لم تُعرض]"


# ---------------------------------------------------------------------------
# Pinned official articles
# ---------------------------------------------------------------------------


def _render_pin(pin: SectionPin) -> str:
    return f"〔{PIN_TAG.format(title=pin.title)}〕\n{pin.text}"


def _cut_pin(text: str, limit: int) -> str:
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    cut = trim_text(text, limit / len(text))
    return f"{cut}\n{PIN_CUT_NOTE}" if cut else PIN_CUT_NOTE


def attach_pins(
    sections: Sequence[Section],
    pins: Iterable[Any],
    *,
    article_cap: int = PIN_ARTICLE_CAP,
    doc_net: int = PIN_DOC_NET,
) -> list[Section]:
    """Hang each pinned article under the section that cites it.

    ``pins`` — objects with ``char_start``, ``article_id``, ``title``, ``text`` (the
    pinned citations of ONE document, any order). A citation belongs to the section
    whose ``char_start <= position < char_end``. An article cited several times in one
    section is printed once there; cited in several sections, it is printed in full
    under each (owner decision C22 — a section may travel alone).

    A document with no sections gets ONE carrier section (``PINS_ONLY_VALUE``) holding
    each article once; ``render_loose_pins`` prints it after the document's text.

    Size (C23): each article is cut at ``article_cap``; if the document's pinned text
    still passes ``doc_net``, every article keeps the same share. The sections' own
    text is untouched, so the 100k / 300k caps are computed exactly as before.
    """
    pins = sorted(pins, key=lambda p: int(getattr(p, "char_start", 0) or 0))
    ordered = sorted(sections, key=lambda s: s.seq)
    if not pins:
        return list(ordered)

    placed: dict[int, list[tuple[str, str, str]]] = {}        # index in ``ordered`` → (id, title, text)
    if not ordered:
        seen: set[str] = set()
        for p in pins:
            if p.article_id not in seen:
                seen.add(p.article_id)
                placed.setdefault(-1, []).append((p.article_id, p.title, _cut_pin(p.text, article_cap)))
    else:
        for p in pins:
            pos = int(p.char_start)
            for i, s in enumerate(ordered):
                if s.char_start <= pos < s.char_end:
                    bucket = placed.setdefault(i, [])
                    if all(a != p.article_id for a, _t, _x in bucket):
                        bucket.append((p.article_id, p.title, _cut_pin(p.text, article_cap)))
                    break
    if not placed:
        return list(ordered)

    total = sum(len(text) for bucket in placed.values() for _a, _t, text in bucket)
    if total > doc_net > 0:
        ratio = doc_net / total
        for bucket in placed.values():
            for k, (article_id, title, text) in enumerate(bucket):
                cut = trim_text(text.removesuffix(f"\n{PIN_CUT_NOTE}"), ratio)
                bucket[k] = (article_id, title, f"{cut}\n{PIN_CUT_NOTE}" if cut else PIN_CUT_NOTE)

    def as_pins(bucket: list[tuple[str, str, str]]) -> tuple[SectionPin, ...]:
        return tuple(SectionPin(a, t, x) for a, t, x in bucket)

    if not ordered:
        return [Section(seq=0, value=PINS_ONLY_VALUE, what="", text="", pins=as_pins(placed[-1]))]
    return [replace(s, pins=as_pins(placed[i])) if i in placed else s for i, s in enumerate(ordered)]


def render_loose_pins(sections: Iterable[Section]) -> str:
    """The articles of a document that has no sections, as one block; ``""`` when none."""
    pins = [pin for s in sections if s.value == PINS_ONLY_VALUE for pin in s.pins]
    if not pins:
        return ""
    return "\n\n".join([LOOSE_PINS_HEADING, *(_render_pin(pin) for pin in pins)])


def with_loose_pins(text: str, sections: Iterable[Section] | None) -> str:
    """``text`` followed by the document's loose articles (unchanged when there are none)."""
    block = render_loose_pins(sections or ())
    return f"{text}\n\n{block}" if block else text


def writer_bodies(
    contents: Mapping[str, str],
    sections_by_id: Mapping[str, Sequence[Section]],
    *,
    per_doc_cap: int = PER_DOC_CAP,
    total_cap: int = TOTAL_CAP,
) -> tuple[dict[str, str], dict[str, Any]]:
    """The writer's text for each attachment (plan §4.7, D12 + D16).

    ``contents`` — real ``content_md`` per attachment item_id. Returns
    ``(bodies, stats)``:

    - with ``metadata`` / ``core`` / ``supporting`` sections → those sections,
      tagged, **uncapped** (D12) — a long file, or a small one segmented in a
      bundle with the other small files of its message;
    - no sections and ``≤ SEGMENT_MIN_CHARS`` → the content as it is;
    - no usable sections and longer → the full text within the caps: each cut at
      ``per_doc_cap``, and the fallback texts together trimmed proportionally to
      ``total_cap`` (D16). Segmented documents never count toward that total.
    """
    bodies: dict[str, str] = {}
    fallback_chars: dict[str, int] = {}
    segmented: list[str] = []
    for item_id, content in contents.items():
        content = content or ""
        sections = sections_by_id.get(item_id) or ()
        if has_kept(sections, WRITER_VALUES):
            bodies[item_id], _ = render_sections(sections, WRITER_VALUES)
            segmented.append(item_id)
        elif len(content.strip()) <= SEGMENT_MIN_CHARS:
            bodies[item_id] = with_loose_pins(content, sections)
        else:
            fallback_chars[item_id] = len(content)

    ratios, factor = proportional_ratios(
        fallback_chars, 0, per_doc_cap=per_doc_cap, total_cap=total_cap
    )
    trimmed: list[str] = []
    for item_id, total in fallback_chars.items():
        content = contents[item_id] or ""
        text = trim_text(content, ratios[item_id])
        if len(text) < len(content):
            trimmed.append(item_id)
            text = f"{text}\n{FALLBACK_CUT_NOTE.format(shown=len(text), total=total)}"
        bodies[item_id] = with_loose_pins(text, sections_by_id.get(item_id))
    stats = {
        "segmented": segmented,
        "fallback": list(fallback_chars),
        "trimmed": trimmed,
        "fallback_factor": round(factor, 4),
    }
    return bodies, stats


def sections_from_rows(rows: Iterable[Mapping[str, Any] | Any]) -> list[Section]:
    """Build :class:`Section` objects from stored rows (dicts or objects with
    ``seq`` / ``value`` / ``what`` / ``content_md`` | ``text`` / ``label``)."""
    out: list[Section] = []
    for row in rows:
        get = row.get if isinstance(row, Mapping) else lambda k, d=None, r=row: getattr(r, k, d)
        text = get("text", None)
        if text is None:
            text = get("content_md", "")
        out.append(
            Section(
                seq=int(get("seq", 0) or 0),
                value=str(get("value", "") or ""),
                what=str(get("what", "") or ""),
                text=str(text or ""),
                label=str(get("label", "") or ""),
                owner=str(get("owner", "") or ""),
                brought_by=str(get("brought_by", "") or ""),
                char_start=_position(get("char_start", None)),
                char_end=_position(get("char_end", None)),
            )
        )
    return sorted(out, key=lambda s: s.seq)


def _position(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return -1


__all__ = [
    "DEEP_SEARCH_VALUES",
    "LEFT_OUT_PREFIX",
    "LOOSE_PINS_HEADING",
    "PER_DOC_CAP",
    "PINS_ONLY_VALUE",
    "PIN_ARTICLE_CAP",
    "PIN_CUT_NOTE",
    "PIN_DOC_NET",
    "PIN_TAG",
    "SEGMENT_MIN_CHARS",
    "SILENT_VALUES",
    "TOTAL_CAP",
    "TRIM_NOTE",
    "WRITER_VALUES",
    "Section",
    "SectionPin",
    "attach_pins",
    "delivery_enabled",
    "has_kept",
    "keep_ratio",
    "kept_chars",
    "proportional_ratios",
    "render_loose_pins",
    "render_sections",
    "sections_from_rows",
    "trim_text",
    "with_loose_pins",
    "writer_bodies",
]
