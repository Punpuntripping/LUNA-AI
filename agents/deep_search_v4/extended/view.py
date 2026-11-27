"""The extended planner's view of a stored angle map, and each document's marked text.

:func:`build_planner_view` reads, with the production loaders: the map (already loaded
by eligibility — ``store.load_angle_map_for_item`` shape), its members' rows
(``attachment_segmenter.runner._load_items`` — the ``shared.files`` overlay, so a file
attached from «الملفات» reads its file's text), their segment maps
(``load_segment_maps``) and the section index (``labels.section_index``). Each angle's
sections are labelled by code relative to the angle's side (``angle_viewpoint`` /
``relative_label``) — the same rule the angle-map tests pin (plan §4.2).

Each document's ``marked_text`` is the WHOLE document, every paragraph in order, with a
code-written marker line at each section start — 〔D1·S7 · owner، قدّمه … · جوهري〕
(D17; ``run_full_trace.py`` ``marked()``). Paragraphs are rebuilt the way the angle
mapper rebuilds them (``paragraphize(content_md, doc_type)``); when they no longer fall
on the sections' offsets (a changed ``doc_type``), the sections' character ranges
are used instead — the same text, cut at the stored offsets.

:func:`render_planner_input` is the planner's input as tested on 2026-11-23 (D28,
``run_budget_planner.py`` ``planner_input``): the conversation before the message, the
budget block, the documents (id, type, title), the angles, the message — HTML-escaped.
No summary, no client side, no gaps, no section list, no file text (Q14).

Sync. :func:`build_planner_view` raises on a failed read (the caller falls back to
today's path).
"""
from __future__ import annotations

import html
from typing import Any, Mapping, Sequence

from .budget import budget_block
from .models import AngleCard, BudgetMode, DocView, PlannerView, SectionView

E = html.escape

#: Section value → the Arabic word on its marker line (run_full_trace.py ``VALUE_AR``).
VALUE_AR: dict[str, str] = {
    "core": "جوهري",
    "supporting": "مساند",
    "metadata": "بيانات",
    "boilerplate": "قالب",
    "repeat": "مكرر",
}

#: The conversation before the message: the last N messages, each cut at M characters
#: (as tested 2026-11-23).
RECENT_MESSAGES_MAX = 6
RECENT_MESSAGE_MAX_CHARS = 4_000
_CUT_MARK = " …[مقتطع]"


# --------------------------------------------------------------------------- helpers


def owner_of(section: Mapping[str, Any]) -> str:
    """``owner، قدّمه brought_by`` / ``owner`` / ``-`` (run_full_trace.py ``owner_of``)."""
    owner = str(section.get("owner") or "") or "-"
    brought_by = str(section.get("brought_by") or "")
    return f"{owner}، قدّمه {brought_by}" if brought_by else owner


def display_ref(ref: str) -> str:
    """``D1.S7`` → ``D1·S7`` (how markers, briefs and the merger show a ref)."""
    from agents.memory.angle_mapper.refs import display_ref as _display

    return _display(ref)


def marker_line(section: Mapping[str, Any]) -> str:
    """〔D1·S7 · owner، قدّمه … · جوهري〕."""
    value = str(section.get("value") or "")
    return f"〔{display_ref(str(section.get('ref') or ''))} · {owner_of(section)} · {VALUE_AR.get(value, value)}〕"


def _paragraphs_fit(paragraphs: Sequence[Any], sections: Sequence[Mapping[str, Any]]) -> bool:
    n = len(paragraphs)
    for s in sections:
        try:
            a, b = int(s.get("from_p")), int(s.get("to_p"))
        except (TypeError, ValueError):
            return False
        if not (1 <= a <= b <= n):
            return False
        if paragraphs[a - 1].char_start != s.get("char_start") or paragraphs[b - 1].char_end != s.get("char_end"):
            return False
    return True


def marked_text(content_md: str, sections: Sequence[Mapping[str, Any]], *, doc_type: str = "") -> str:
    """The whole document with a marker line before each section (D17).

    ``sections`` — the document's section dicts (``labels.section_index`` values:
    ``ref``, ``seq``, ``from_p``, ``to_p``, ``char_start``, ``char_end``, ``value``,
    ``owner``, ``brought_by``). Every paragraph is kept, in order — one outside every
    section stays, unmarked. ``""`` when the document has no sections."""
    from agents.memory.attachment_segmenter.paragraphs import paragraphize

    text = content_md or ""
    secs = sorted(sections, key=lambda s: int(s.get("seq") or 0))
    if not secs or not text:
        return ""
    paragraphs = paragraphize(text, doc_type=doc_type or None)
    blocks: list[str] = []
    if _paragraphs_fit(paragraphs, secs):
        starts: dict[int, list[Mapping[str, Any]]] = {}
        for s in secs:
            starts.setdefault(int(s["from_p"]), []).append(s)
        for p in paragraphs:
            for s in starts.get(p.n, ()):
                blocks.append(marker_line(s))
            blocks.append(p.text)
        return "\n\n".join(blocks)
    # Paragraphs moved (another doc_type): cut at the sections' stored offsets.
    by_start = sorted(secs, key=lambda s: int(s.get("char_start") or 0))
    pos = 0
    for s in by_start:
        start = max(int(s.get("char_start") or 0), pos)
        end = max(int(s.get("char_end") or 0), start)
        gap = text[pos:start].strip()
        if gap:
            blocks.append(gap)
        blocks.append(marker_line(s))
        body = text[start:end].strip()
        if body:
            blocks.append(body)
        pos = max(pos, end)
    tail = text[pos:].strip()
    if tail:
        blocks.append(tail)
    return "\n\n".join(blocks)


# --------------------------------------------------------------------------- the view


def build_planner_view(supabase: Any, angle_map: dict, *, user_id: str) -> PlannerView:
    """The planner's view of ``angle_map`` (``store.load_angle_map_for_item`` shape:
    ``{"map", "items", "angles"}``) for ``user_id``.

    Documents D1… in the map's ``doc_index`` order; angles A1… in ``seq`` order, each
    section labelled الموكّل / الخصم / محايد relative to the angle's side
    (unresolvable → «?»). Raises on a failed read, on a map of another user, and on a
    member that is gone or not the user's (the map is then not this turn's)."""
    from agents.memory.angle_mapper.labels import LABEL_AR, angle_viewpoint, relative_label, section_index
    from agents.memory.angle_mapper.refs import canonical_ref
    from agents.memory.attachment_segmenter.runner import _load_items
    from agents.memory.attachment_segmenter.store import load_segment_maps
    from shared.attachments.overlay import corrections_for_rows
    from shared.attachments.text_corrections import apply_text_corrections

    row = dict((angle_map or {}).get("map") or {})
    items = sorted((angle_map or {}).get("items") or [], key=lambda i: int(i.get("doc_index") or 0))
    angles = sorted((angle_map or {}).get("angles") or [], key=lambda a: int(a.get("seq") or 0))
    if not row or not items:
        raise ValueError("extended_search: empty angle map")
    if str(row.get("user_id") or "") != str(user_id or ""):
        raise ValueError("extended_search: angle map of another user")

    ids = [str(i.get("item_id") or "") for i in items]
    rows = _load_items(supabase, ids)
    for item_id in ids:
        r = rows.get(item_id)
        if not r or r.get("deleted_at") or str(r.get("user_id") or "") != str(user_id):
            raise ValueError("extended_search: an angle-map member is gone")
    lengths = {i: len(str(rows[i].get("content_md") or "")) for i in ids}
    smaps = load_segment_maps(supabase, ids, user_id=str(user_id), content_chars=lengths)
    index = section_index(items, smaps)
    client = dict(row.get("client") or {})
    # The user's text fixes (attachment_agent.md §3.3), applied AFTER marking: the
    # marking compares paragraph offsets with the sections' stored offsets
    # (``_paragraphs_fit``), so it runs on the REAL text; ``content_chars`` too.
    fixes = corrections_for_rows(
        supabase, [rows[i] for i in ids], user_id=str(user_id), kinds=None,
    )

    docs: list[DocView] = []
    for it in items:
        item_id = str(it.get("item_id") or "")
        r = rows[item_id]
        doc = int(it.get("doc_index") or 0)
        metadata = r.get("metadata") or {}
        doc_type = str(metadata.get("doc_type") or "") if isinstance(metadata, Mapping) else ""
        content = str(r.get("content_md") or "")
        secs = [s for s in index.values() if s.get("doc") == doc]
        docs.append(DocView(
            id=f"D{doc}",
            item_id=item_id,
            title=str(r.get("title") or ""),
            doc_type=doc_type,
            summary=str(r.get("summary") or ""),
            content_chars=len(content),
            marked_text=apply_text_corrections(
                marked_text(content, secs, doc_type=doc_type), fixes.get(item_id),
            ),
        ))

    cards: list[AngleCard] = []
    for a in angles:
        sections: list[SectionView] = []
        for ref in a.get("sections") or []:
            canon = canonical_ref(ref)
            sec = index.get(canon)
            if sec is None:
                sections.append(SectionView(ref=canon, label="?", owner="", what=""))
                continue
            me, party = angle_viewpoint(a, client, canon)
            sections.append(SectionView(
                ref=canon,
                label=LABEL_AR[relative_label(sec, me, party)],
                owner=owner_of(sec),
                what=str(sec.get("what") or ""),
            ))
        cards.append(AngleCard(
            id=f"A{int(a.get('seq') or 0)}",
            kind=str(a.get("kind") or ""),
            strength=str(a.get("strength") or ""),
            for_side=str(a.get("for_side") or ""),
            for_party=str(a.get("for_party") or ""),
            title=str(a.get("title") or ""),
            argument=str(a.get("argument") or ""),
            legal_question=str(a.get("legal_question") or ""),
            weakness=str(a.get("weakness") or ""),
            ocr_suspect=bool(a.get("ocr_suspect")),
            sections=sections,
        ))

    return PlannerView(
        map_id=str(row.get("map_id") or ""),
        message_id=str(row["message_id"]) if row.get("message_id") else None,
        documents=docs,
        angles=cards,
        map_client=client,
        section_refs=frozenset(index),
    )


# --------------------------------------------------------------------------- the planner's input (D28)


def _role_content(message: Any) -> tuple[str, str]:
    if isinstance(message, Mapping):
        return str(message.get("role") or "user"), str(message.get("content") or "")
    return str(getattr(message, "role", "") or "user"), str(getattr(message, "content", "") or "")


def recent_pairs(recent_messages: Sequence[Any] | None, *, query: str = "") -> list[tuple[str, str]]:
    """``(role, content)`` of the conversation BEFORE the message: the last
    ``RECENT_MESSAGES_MAX``, each cut at ``RECENT_MESSAGE_MAX_CHARS``. A trailing user
    message that holds ``query`` itself (the turn's own row, saved before the AI call)
    is dropped — the message travels in ``<query>``."""
    pairs = [_role_content(m) for m in recent_messages or []]
    pairs = [(role, content) for role, content in pairs if content.strip()]
    q = (query or "").strip()
    if pairs and q and pairs[-1][0] == "user" and q in pairs[-1][1]:
        pairs.pop()
    out = []
    for role, content in pairs[-RECENT_MESSAGES_MAX:]:
        if len(content) > RECENT_MESSAGE_MAX_CHARS:
            content = content[:RECENT_MESSAGE_MAX_CHARS] + _CUT_MARK
        out.append((role, content))
    return out


def render_planner_input(
    view: PlannerView, *, query: str, recent_messages: Sequence[Any] | None, mode: BudgetMode,
) -> str:
    """The planner's user message (D28, as tested 2026-11-23). ``query`` travels word
    for word; ``recent_messages`` — ``ChatMessageSnapshot`` objects or ``{"role",
    "content"}`` dicts, oldest first."""
    rec = "\n".join(
        f'  <message role="{E(role)}">{E(content)}</message>'
        for role, content in recent_pairs(recent_messages, query=query)
    ) or "  (none)"
    lines = [f"<recent_messages>\n{rec}\n</recent_messages>", "", budget_block(mode), ""]
    lines += _documents_and_angles(view)
    lines += ["", f"<query>{E(query or '')}</query>"]
    return "\n".join(lines)


def _documents_and_angles(view: PlannerView, wi_of: Mapping[str, str] | None = None) -> list[str]:
    """The ``<documents>`` and ``<angles>`` blocks (as tested 2026-11-23). ``wi_of`` —
    item_id → "WI-n": when given, each document also names its alias (the one planner
    reads the same files in ``<attached_items>``)."""
    lines = ["<documents>"]
    for d in view.documents:
        wi = (wi_of or {}).get(str(d.item_id))
        wi_attr = f' wi="{E(wi)}"' if wi else ""
        lines.append(f'  <document id="{d.id}"{wi_attr} doc_type="{E(d.doc_type)}">{E(d.title)}</document>')
    lines += ["</documents>", "", "<angles>"]
    for a in view.angles:
        secs = "؛ ".join(f"{s.ref} ({s.label}: {E(s.owner)} — {E(s.what)})" for s in a.sections)
        lines += [
            f'  <angle id="{a.id}" kind="{E(a.kind)}" strength="{E(a.strength)}" '
            f'for_side="{E(a.for_side)}" for="{E(a.for_party)}">',
            f"    <title>{E(a.title)}</title>",
            f"    <argument>{E(a.argument)}</argument>",
            f"    <legal_question>{E(a.legal_question)}</legal_question>",
            f"    <weakness>{E(a.weakness)}</weakness>",
            f"    <sections>{secs}</sections>",
            "  </angle>",
        ]
    lines.append("</angles>")
    return lines


def render_extended_block(
    view: PlannerView, mode: BudgetMode, *, wi_of: Mapping[str, str] | None = None,
) -> str:
    """The one planner's «بحث شامل» data (.claude/plans/one_planner.md): the budget
    block, the documents (with their WI aliases) and the angles — the blocks the
    extended planner reads, minus the conversation and the message (the one planner
    has those in its own blocks)."""
    return "\n".join([budget_block(mode), "", *_documents_and_angles(view, wi_of)])


__all__ = [
    "RECENT_MESSAGES_MAX",
    "RECENT_MESSAGE_MAX_CHARS",
    "VALUE_AR",
    "build_planner_view",
    "display_ref",
    "marked_text",
    "marker_line",
    "owner_of",
    "recent_pairs",
    "render_extended_block",
    "render_planner_input",
]
