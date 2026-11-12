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
from dataclasses import dataclass
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
) -> tuple[str, bool]:
    """The kept sections in document order, each under a ``〔what〕`` tag.

    A run of left-out sections between kept ones becomes ONE marker naming them by
    their descriptions; ``boilerplate`` / ``repeat`` are left out silently. With
    ``ratio < 1`` every kept section keeps that share of its length (cut from its
    end) and ends with :data:`TRIM_NOTE`. Returns ``(text, trimmed)``.
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
            parts.append(f"〔{what}〕\n{body}" if what else body)
        elif section.value not in SILENT_VALUES and what and what not in left_out:
            left_out.append(what)
    # A trailing run names what follows the last kept section too.
    flush()
    return "\n\n".join(parts), trimmed


FALLBACK_CUT_NOTE = "[انتهى المعروض: {shown} حرف من أصل {total} — بقية الوثيقة لم تُعرض]"


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
            bodies[item_id] = content
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
        bodies[item_id] = text
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
            )
        )
    return sorted(out, key=lambda s: s.seq)


__all__ = [
    "DEEP_SEARCH_VALUES",
    "LEFT_OUT_PREFIX",
    "PER_DOC_CAP",
    "SEGMENT_MIN_CHARS",
    "SILENT_VALUES",
    "TOTAL_CAP",
    "TRIM_NOTE",
    "WRITER_VALUES",
    "Section",
    "delivery_enabled",
    "has_kept",
    "keep_ratio",
    "kept_chars",
    "proportional_ratios",
    "render_sections",
    "sections_from_rows",
    "trim_text",
    "writer_bodies",
]
