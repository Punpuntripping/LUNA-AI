"""Reranker-side renderer for case_search (sectioned pipeline).

What the reranker LLM grades, post-Wave-1 (`.claude/plans/case_topics_loop.md`
§6.1):

    ### [3]
    المحكمة: التجارية — الرياض (ابتدائي)
    الموضوعات المطابقة:
    - [اسانيد · المدعي · أساس الحكم] الاستناد إلى كشف حساب ومصادقة رصيد
    - [اسانيد · المدعى عليه · لم يُعتد به] الاستناد إلى أن المخلص اختار الناقل
    الملخص: - نزاع على استرداد جزء من عمولة سمسرة عقارية.

Why this replaced 10,000 chars of raw `cases.content`:

- Raw ruling text does not say **whether the court actually relied on** the
  matching argument. `case_topics.attrs` says it atomically —
  `موقف المحكمة` ∈ {أساس الحكم, قُبل, رُفض, لم يُعتد به, لم تُناقَش …} for a
  `basis` topic, `النوع` ∈ {موضوعي, شكلي} for a `principle`.
- 15 candidates × N sub-queries of full ruling text was the dominant token
  cost of the case executor. ~600–800 chars/candidate here is a ~15× cut.
- `attrs` is a SIGNAL, never a filter (D4): a `رُفض` / `لم يُعتد به` basis is
  exactly what a user asking "will this defence fail?" needs. The prompt owns
  that reading; this module only renders it faithfully.
- Court / city / level are context only (D5) — no ordering or boost here.

There is no DB round trip left in this module. The `search_case_topics` RPC
joins the case header onto every topic row, so `ChannelCandidate.row` is
already the header and `ChannelCandidate.topics` is already score-desc — the
old `fetch_case_headers` / `enrich_candidates` pair (and its `cases.content`
SELECT) is deleted.

Position N remains the only handle the reranker returns: `assemble_kept_cases`
(unfold_ura.py) maps position → case_id via bucket order and re-fetches every
keeper. Nothing is parsed back out of this markdown.
"""
from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

# `court_level` → Arabic label, from the CANONICAL vocabulary. All three values
# are mapped there, including `supreme` (125 rulings), which four independent
# two-branch ternaries used to collapse into `first_instance`. Do not re-derive
# a local map here — that is exactly how the bug propagated.
from agents.deep_search_v4.shared.court_levels import COURT_LEVEL_AR, court_level_ar

if TYPE_CHECKING:
    from .models import ChannelCandidate, FusedCandidate

logger = logging.getLogger(__name__)

# `case_topic_kind` → Arabic tag head. Mirrors the corpus vocabulary the
# reranker prompt teaches.
TOPIC_KIND_AR: dict[str, str] = {
    "basis": "اسانيد",
    "principle": "مبدأ",
    "fact": "وقائع",
    # `facts` is the agent-side channel spelling; tolerated so a mis-tagged
    # row still renders a sane label instead of leaking the raw key.
    "facts": "وقائع",
}

# Which `attrs` keys to append to the tag, per kind, in order. Missing keys are
# omitted — never rendered as `None`. `fact` carries `attrs == {}`.
TOPIC_ATTR_KEYS: dict[str, tuple[str, ...]] = {
    "basis": ("الطرف", "موقف المحكمة"),
    "principle": ("النوع",),
    "fact": (),
    "facts": (),
}

_NO_DATA = "(لا توجد بيانات لهذا الحكم)"

# Re-exported so importers of this module (and its tests) see the same map the
# rest of the pipeline uses.
__all__ = [
    "COURT_LEVEL_AR",
    "TOPIC_ATTR_KEYS",
    "TOPIC_KIND_AR",
    "format_bucket_for_reranker",
    "format_candidate_for_reranker",
]


# ─── Helpers ──────────────────────────────────────────────────────────────────


def _clean(value: Any) -> str:
    """Coerce a possibly-None DB value to a stripped string."""
    if value is None:
        return ""
    return str(value).strip()


def _coerce_attrs(attrs: Any) -> dict[str, Any]:
    """Normalise a topic's `attrs` to a dict.

    jsonb normally arrives as a dict, but a string (double-encoded jsonb, or a
    mock fixture) must not blow up the formatter.
    """
    if isinstance(attrs, dict):
        return attrs
    if isinstance(attrs, str) and attrs.strip():
        try:
            parsed = json.loads(attrs)
        except (json.JSONDecodeError, TypeError):
            return {}
        if isinstance(parsed, dict):
            return parsed
    return {}


def _topic_tag(topic: dict[str, Any], *, kind_hint: str = "") -> str:
    """Build the bracketed tag for one matched topic.

    `basis`     → `اسانيد · {الطرف} · {موقف المحكمة}`
    `principle` → `مبدأ · {النوع}`
    `fact`      → `وقائع`

    Missing `attrs` keys drop their segment. An unknown kind falls back to the
    raw kind string so a new enum value is visible rather than silently blank.
    """
    kind = _clean(topic.get("kind")) or _clean(kind_hint)
    head = TOPIC_KIND_AR.get(kind, kind)
    segments: list[str] = [head] if head else []

    attrs = _coerce_attrs(topic.get("attrs"))
    for key in TOPIC_ATTR_KEYS.get(kind, ()):
        val = _clean(attrs.get(key))
        if val:
            segments.append(val)

    return " · ".join(segments)


def _format_court_line(row: dict[str, Any]) -> str:
    """`المحكمة: {court} — {city} ({level})`, omitting whatever is missing."""
    court = _clean(row.get("court"))
    city = _clean(row.get("city"))
    # strict=True: this is a DISPLAY path — an unrecognised level must print
    # nothing rather than assert a false ابتدائي.
    level = court_level_ar(row.get("court_level"), strict=True)

    if not court and not city:
        # Level alone carries no identity — skip the line entirely rather than
        # emit `المحكمة:  (ابتدائي)`.
        return ""

    line = f"المحكمة: {court or city}"
    if court and city:
        line += f" — {city}"
    if level:
        line += f" ({level})"
    return line


def _format_topics_block(
    topics: list[dict[str, Any]],
    *,
    kind_hint: str = "",
) -> list[str]:
    """`الموضوعات المطابقة:` + one `- [tag] text` line per matched topic."""
    lines: list[str] = []
    for topic in topics or []:
        text = _clean(topic.get("text"))
        if not text:
            continue
        tag = _topic_tag(topic, kind_hint=kind_hint)
        lines.append(f"- [{tag}] {text}" if tag else f"- {text}")

    if not lines:
        return []
    return ["الموضوعات المطابقة:", *lines]


# ─── Formatters ───────────────────────────────────────────────────────────────


def format_candidate_for_reranker(
    cand: "FusedCandidate | ChannelCandidate",
    position: int,
) -> str:
    """Render one candidate: header + matched topics (+ short_summary).

    Shape (plan §6.1) — the reranker prompt is written against this exactly:

        ### [N]
        المحكمة: {court} — {city} ({level})
        الموضوعات المطابقة:
        - [{tag}] {topic text}
        الملخص: {short_summary}

    Every line is conditional. `short_summary` is NULL/empty on 964 cases →
    the `الملخص:` line is omitted with no placeholder (trap 6). A candidate
    with neither header nor topics renders a short marker so the block is
    never empty.
    """
    row = cand.row or {}
    # `topics` lives on ChannelCandidate; a FusedCandidate wrapper has none, so
    # fall back to the row (and then to empty) rather than raising.
    topics = list(getattr(cand, "topics", None) or row.get("topics") or [])

    lines: list[str] = [f"### [{position}]"]

    court_line = _format_court_line(row)
    if court_line:
        lines.append(court_line)

    kind_hint = _clean(getattr(cand, "channel", "")) or _clean(row.get("kind"))
    topic_lines = _format_topics_block(topics, kind_hint=kind_hint)
    lines.extend(topic_lines)

    short_summary = _clean(row.get("short_summary"))
    if short_summary:
        lines.append(f"الملخص: {short_summary}")

    if len(lines) == 1:
        lines.append(_NO_DATA)

    return "\n".join(lines) + "\n"


def format_bucket_for_reranker(
    candidates: list["FusedCandidate | ChannelCandidate"],
    *,
    bucket_label: str = "fused",
) -> tuple[str, int]:
    """Top-level rendering for a sectioned-retrieval bucket.

    Returns:
        (markdown, count)
    """
    if not candidates:
        return "لم يتم العثور على سوابق قضائية مطابقة للاستعلام.", 0

    lines: list[str] = [
        f"## نتائج البحث في السوابق القضائية ({bucket_label}) — {len(candidates)} نتيجة\n"
    ]
    for i, cand in enumerate(candidates, start=1):
        lines.append(format_candidate_for_reranker(cand, i))

    return "\n".join(lines), len(candidates)
