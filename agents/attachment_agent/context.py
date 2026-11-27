"""Load and render what the attachment agent reads (plan §2.2 "What code gives it").

Per file: title, type, summary, size, pages, and either the WHOLE text (a short
file) or the OUTLINE — one line per stored section, boilerplate / repeat runs
folded into one line. Then the angle map(s) «الخيوط»: the client line, the
threads with the sections they rest on (as the agent's own ``F1·S7`` refs), and
the gaps.

Attachment text lives in ``user_files`` since migration 223: rows are read the
way the router reads them (``files_store.with_file_id`` + ``overlay_files``).
The user's text fixes (``attachment_text_corrections``, plan §3.3) are applied to
the text and to the section texts BEFORE masking (``MaskedText`` keeps offsets in
the original text). Everything that reaches the model is encoded with the turn's
codec (وضع السرية); the caller persists the new fakes before the model runs.

Service-role client: the ``user_id`` / ``conversation_id`` filters are load-bearing.
"""
from __future__ import annotations

import html
import logging
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

from agents.memory.angle_mapper.refs import parse_ref
from agents.memory.angle_mapper.store import load_angle_map_for_item
from agents.memory.attachment_segmenter.store import load_pages, load_segment_maps
from agents.utils.history import neutralize_system_brackets
from backend.app.services.masking_service import active_codec
from shared.attachments.text_corrections import (
    apply_text_corrections,
    file_keys_for_items,
    load_text_corrections,
)
from shared.files import store as files_store

from .deps import FileDoc, SectionRow, ThreadMap
from .models import AttachmentAgentCall
from .text import Encoder, MaskedText

logger = logging.getLogger(__name__)

#: A file this short is given whole instead of an outline (plan §2.2: ≤ ~12k).
WHOLE_FILE_MAX_CHARS = 12_000
#: Whole texts in one turn message, all files together; past it a short file
#: gets its header only and is read with read_pages.
WHOLE_TOTAL_MAX_CHARS = 48_000
#: The outlines of all files together. Above it only core / metadata sections
#: keep their line; the rest is counted.
OUTLINE_MAX_CHARS = 60_000
#: Chat rows before the current message (same window as artifact_analyzer).
CONVERSATION_ROWS = 6
_MESSAGE_FETCH_BUFFER = 4
#: Long free text in a header or a thread line is cut to this.
_SUMMARY_MAX_CHARS = 2_000
_ARGUMENT_MAX_CHARS = 400

#: Folded into one line when two or more follow each other.
_SILENT_VALUES = frozenset({"boilerplate", "repeat"})
#: Kept line by line when the outline is over its cap.
_CAPPED_KEEP_VALUES = frozenset({"core", "metadata"})


@dataclass
class Loaded:
    files: list[FileDoc] = field(default_factory=list)
    #: Rendered (masked) blocks, one per angle map.
    threads: list[str] = field(default_factory=list)
    #: The stored angle maps, as loaded (real text).
    angle_maps: list[dict] = field(default_factory=list)
    #: The same maps as the edit tools resolve them (labels ``M1``… when several).
    maps: list[ThreadMap] = field(default_factory=list)
    #: Titles (masked) of picked attachments that have no text yet.
    without_text: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Masking
# ---------------------------------------------------------------------------


def turn_encoder() -> Encoder | None:
    """The active codec's encode, or ``None`` when nothing is masked this turn."""
    codec = active_codec()
    if codec is None or not getattr(codec, "enabled", True):
        return None
    return codec.encode


def _enc(encode: Encoder | None, text: Any) -> str:
    value = str(text or "")
    if encode is None or not value:
        return value
    try:
        return encode(value)
    except Exception:  # noqa: BLE001 — same rule as encode_active
        logger.debug("attachment_agent: encode failed", exc_info=True)
        return value


def _esc(value: Any) -> str:
    return html.escape(str(value or ""), quote=False)


def _title(text: str) -> str:
    """A file name is user input: it may not open a system line or close «…»."""
    return neutralize_system_brackets(text or "").replace("«", '"').replace("»", '"').strip()


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------


def load_attachment_rows(supabase: Any, call: AttachmentAgentCall) -> list[dict]:
    """The conversation's live attachments (the picked ones, or all), upload order."""
    columns = (
        "item_id, wi_seq, kind, title, summary, content_md, created_at, "
        "doc_type:metadata->>doc_type"
    )
    columns = files_store.with_file_id(columns, user_id=call.user_id, supabase=supabase)
    q = (
        supabase.table("workspace_items")
        .select(columns)
        .eq("conversation_id", call.conversation_id)
        .eq("user_id", call.user_id)
        .eq("kind", "attachment")
        .is_("deleted_at", "null")
    )
    picked = [str(i) for i in call.item_ids if i]
    if picked:
        q = q.in_("item_id", picked)
    rows = list(getattr(q.order("created_at", desc=False).execute(), "data", None) or [])
    rows = files_store.overlay_files(supabase, rows, user_id=call.user_id)
    return [r for r in rows if str(r.get("kind") or "") == "attachment" and r.get("item_id")]


def _section_rows(
    stored: Any, text_len: int, original: str, encode: Encoder | None,
    corrections: Sequence[Any] = (),
) -> list[SectionRow]:
    out: list[SectionRow] = []
    for seg in getattr(stored, "segments", None) or []:
        start = max(0, min(int(seg.char_start), text_len))
        end = max(start, min(int(seg.char_end), text_len))
        stored_text = seg.content_md or ""
        # Joined = the stored text is not the slice (both uncorrected); shown fixed.
        joined = bool(stored_text.strip()) and stored_text.strip() != original[start:end].strip()
        owner = (seg.owner or "").strip() or (
            seg.owner_class if seg.owner_class and seg.owner_class != "unknown" else ""
        )
        out.append(SectionRow(
            seq=int(seg.seq),
            label=_enc(encode, seg.label),
            value=str(seg.value or ""),
            what=_enc(encode, seg.what),
            owner=_enc(encode, owner),
            brought_by=_enc(encode, seg.brought_by),
            char_start=start,
            char_end=end,
            page_from=seg.page_from,
            page_to=seg.page_to,
            joined_text=_enc(encode, apply_text_corrections(stored_text, corrections)) if joined else "",
        ))
    return sorted(out, key=lambda s: (s.char_start, s.seq))


def _page_rows(rows: Iterable[Mapping[str, Any]], text_len: int) -> list[tuple[int, int]]:
    pages: list[tuple[int, int]] = []
    for r in rows or []:
        try:
            pages.append((int(r["page_no"]), max(0, min(int(r["char_start"]), text_len))))
        except (KeyError, TypeError, ValueError):
            continue
    return sorted(pages)


def conversation_item_ids(supabase: Any, call: AttachmentAgentCall) -> list[str]:
    """Every live attachment of the conversation (picked or not) — what a carried
    map's members are pointed at. ``[]`` on a failed read (never raises)."""
    try:
        return [
            str(r["item_id"]) for r in getattr(
                supabase.table("workspace_items").select("item_id")
                .eq("conversation_id", call.conversation_id).eq("user_id", call.user_id)
                .eq("kind", "attachment").is_("deleted_at", "null").execute(),
                "data", None,
            ) or [] if r.get("item_id")
        ]
    except Exception as exc:  # noqa: BLE001
        logger.warning("attachment_agent: conversation attachments not listed (%s)", type(exc).__name__)
        return []


def _carried_map(supabase: Any, item_id: str, here: Sequence[str]) -> dict | None:
    """A «الملفات» copy's carried map (``angle_mapper.library``, owner 2026-11-27) —
    the newest fresh map holding another item of its file, WHOLE — with its members
    pointed at this conversation's copies (``localize_angle_map``), exactly as
    ``extended/eligibility._best_fresh_map`` does. ``None`` when there is none."""
    from agents.memory.angle_mapper.library import load_carried_angle_map, localize_angle_map

    stored = load_carried_angle_map(supabase, item_id)
    if not stored or not stored.get("map"):
        return None
    return localize_angle_map(supabase, stored, list(here)) or stored


def _load_angle_maps(
    supabase: Any, item_ids: Sequence[str], call: AttachmentAgentCall,
) -> tuple[list[dict], set[str], list[str]]:
    """Every angle map holding one of the files, each once (a file is in at most
    one); a file with no map of its own falls back to its CARRIED map when
    ``LIBRARY_FILE_MAPS_*`` is on for the user (off: no carried read at all).
    Returns the maps, the ids of the carried ones, and this conversation's
    attachment ids (``[]`` when no carried map was looked for)."""
    from agents.utils.angle_map_flags import library_file_maps_enabled

    maps: list[dict] = []
    carried: set[str] = set()
    covered: set[str] = set()
    here: list[str] | None = None
    library_on: bool | None = None
    for item_id in item_ids:
        if item_id in covered:
            continue
        stored = load_angle_map_for_item(supabase, item_id)
        is_carried = False
        if not stored or not stored.get("map"):
            if library_on is None:
                library_on = library_file_maps_enabled(call.user_id)
            if not library_on:
                continue
            if here is None:
                here = list(dict.fromkeys([*item_ids, *conversation_item_ids(supabase, call)]))
            stored = _carried_map(supabase, item_id, here)
            if not stored:
                continue
            is_carried = True
        map_id = str(stored["map"].get("map_id"))
        members = {str(i.get("item_id")) for i in stored.get("items") or []}
        covered |= members or {item_id}
        if all(str(m["map"].get("map_id")) != map_id for m in maps):
            maps.append(stored)
            if is_carried:
                carried.add(map_id)
    return maps, carried, here or []


def load_context(supabase: Any, call: AttachmentAgentCall) -> Loaded:
    """Files, sections, pages and angle maps for one call — encoded. Raises on a
    failed workspace read (the runner turns it into ``outcome="error"``); the
    segment / page / angle-map reads degrade to "none" on their own."""
    encode = turn_encoder()
    rows = load_attachment_rows(supabase, call)
    loaded = Loaded()
    with_text = []
    for row in rows:
        if (row.get("content_md") or "").strip():
            with_text.append(row)
        else:
            loaded.without_text.append(_title(_enc(encode, row.get("title"))))
    if not with_text:
        return loaded

    ids = [str(r["item_id"]) for r in with_text]
    seg_maps = load_segment_maps(
        supabase, ids, user_id=call.user_id,
        content_chars={str(r["item_id"]): len(r.get("content_md") or "") for r in with_text},
    )
    angle_maps, carried, here = _load_angle_maps(supabase, ids, call)
    in_maps = {str(i.get("item_id")) for m in angle_maps for i in m.get("items") or []}
    # = text_corrections.corrections_for_items, keeping the file ids (the edit tools
    # store fixes and promotions on them). Neither call raises.
    file_keys = file_keys_for_items(supabase, ids, user_id=call.user_id)
    corrections = load_text_corrections(supabase, set(file_keys.values()), user_id=call.user_id)

    for n, row in enumerate(with_text, start=1):
        item_id = str(row["item_id"])
        original = row.get("content_md") or ""
        stored = seg_maps.get(item_id)
        wi_seq = row.get("wi_seq")
        file_id = file_keys.get(item_id, item_id)
        fixes = list(corrections.get(file_id) or [])
        loaded.files.append(FileDoc(
            label=f"F{n}",
            item_id=item_id,
            wi_seq=int(wi_seq) if wi_seq is not None else None,
            title=_title(_enc(encode, row.get("title"))) or "(بلا عنوان)",
            doc_type=str(row.get("doc_type") or ""),
            summary=_enc(encode, row.get("summary")),
            view=MaskedText(original, encode, fixes),
            sections=_section_rows(stored, len(original), original, encode, fixes) if stored else [],
            pages=_page_rows(load_pages(supabase, item_id), len(original)),
            has_segment_map=stored is not None,
            has_angle_map=item_id in in_maps,
            file_id=file_id,
            corrections=fixes,
        ))

    by_item = {f.item_id: f for f in loaded.files}
    loaded.angle_maps = angle_maps
    several = len(angle_maps) > 1
    loaded.maps = [
        ThreadMap(
            label=f"M{n}" if several else "",
            map_id=str((m.get("map") or {}).get("map_id") or ""),
            stored=m,
            doc_of_item={
                str(i.get("item_id")): int(i.get("doc_index") or 0) for i in m.get("items") or []
            },
            carried=str((m.get("map") or {}).get("map_id") or "") in carried,
        )
        for n, m in enumerate(angle_maps, start=1)
    ]
    loaded.threads = [
        render_threads(m, by_item, encode, label=t.label, here=set(here) if t.carried else None)
        for m, t in zip(angle_maps, loaded.maps)
    ]
    return loaded


def load_recent_messages(
    supabase: Any, call: AttachmentAgentCall, n: int = CONVERSATION_ROWS,
) -> list[dict]:
    """The last ``n`` chat rows before the current message, encoded, oldest first.

    A minimal copy of the orchestrator's ``_load_recent_messages`` (importing the
    orchestrator here would be heavy and circular). Never raises: no history is a
    valid input.
    """
    try:
        rows = list(getattr(
            supabase.table("messages")
            .select("message_id, role, content, created_at")
            .eq("conversation_id", call.conversation_id)
            .order("created_at", desc=True)
            .limit(n + _MESSAGE_FETCH_BUFFER)
            .execute(),
            "data", None,
        ) or [])
    except Exception as exc:  # noqa: BLE001
        logger.warning("attachment_agent: recent messages failed (%s)", type(exc).__name__)
        return []
    rows.reverse()
    if call.message_id:
        current = next(
            (i for i, r in enumerate(rows) if str(r.get("message_id")) == str(call.message_id)), None,
        )
        if current is not None:
            rows = rows[:current]
    encode = turn_encoder()
    out = [
        {
            "role": r.get("role") if r.get("role") in ("user", "assistant") else "user",
            "content": _enc(encode, neutralize_system_brackets(r.get("content") or "")),
        }
        for r in rows if (r.get("content") or "").strip()
    ]
    return conversation_rows(out, call.user_message, n)


def recent_messages(supabase: Any, call: AttachmentAgentCall) -> list[dict]:
    """The router's rows when it passed them (encoded again: idempotent on fakes),
    else the last rows loaded here."""
    if call.recent_messages:
        encode = turn_encoder()
        rows = [
            {"role": _get(r, "role"), "content": _enc(encode, _get(r, "content"))}
            for r in call.recent_messages
        ]
        return conversation_rows(rows, call.user_message)
    return load_recent_messages(supabase, call)


def conversation_rows(rows: Sequence[Any], user_message: str, n: int = CONVERSATION_ROWS) -> list[dict]:
    """Rows as ``{role, content}``, without the current message when it ends them."""
    out = [
        {"role": str(_get(r, "role") or "user"), "content": str(_get(r, "content") or "")}
        for r in rows
    ]
    if out and out[-1]["role"] == "user" and out[-1]["content"].strip() == (user_message or "").strip():
        out = out[:-1]
    return out[-n:]


def _get(row: Any, key: str) -> Any:
    return row.get(key) if isinstance(row, Mapping) else getattr(row, key, None)


# ---------------------------------------------------------------------------
# Rendering — the outline
# ---------------------------------------------------------------------------


def _pages_text(first: int | None, last: int | None) -> str:
    if first is None and last is None:
        return ""
    first = first if first is not None else last
    last = last if last is not None else first
    return f"p {first}" if first == last else f"p {first}–{last}"


def _span_pages(sections: Sequence[SectionRow]) -> str:
    firsts = [s.page_from for s in sections if s.page_from is not None]
    lasts = [s.page_to if s.page_to is not None else s.page_from for s in sections]
    lasts = [p for p in lasts if p is not None]
    return _pages_text(min(firsts) if firsts else None, max(lasts) if lasts else None)


def section_line(f: FileDoc, s: SectionRow) -> str:
    """``F1·S7 · label · value · what · owner · pages``."""
    parts = [f.ref(s), s.label, s.value]
    if s.what:
        parts.append(s.what)
    if s.owner:
        parts.append(s.owner + (f" (قدّمه: {s.brought_by})" if s.brought_by else ""))
    pages = _pages_text(s.page_from, s.page_to)
    if pages:
        parts.append(pages)
    return " · ".join(p for p in parts if p)


def _run_line(f: FileDoc, run: Sequence[SectionRow], *, listed: bool) -> str:
    """One line for a run of sections: ``F1·S12–S15 · 4 sections: boilerplate ×3, repeat ×1 · p 7–9``."""
    counts: dict[str, int] = {}
    for s in run:
        counts[s.value] = counts.get(s.value, 0) + 1
    kinds = ", ".join(f"{v} ×{c}" for v, c in counts.items())
    ref = f.ref(run[0]) if len(run) == 1 else f"{f.ref(run[0])}–S{run[-1].seq}"
    word = "section" if len(run) == 1 else "sections"
    what = f"{len(run)} {word}: {kinds}" if listed else f"{len(run)} {word} not listed: {kinds}"
    return " · ".join(p for p in (ref, what, _span_pages(run)) if p)


def outline_lines(f: FileDoc, *, capped: bool = False) -> list[str]:
    """One line per section. Not capped: boilerplate / repeat runs of 2+ fold
    into one line. Capped: only core / metadata keep a line; every other run is
    counted."""
    lines: list[str] = []
    run: list[SectionRow] = []

    def flush() -> None:
        if not run:
            return
        if not capped and len(run) == 1:
            lines.append(section_line(f, run[0]))
        else:
            lines.append(_run_line(f, run, listed=not capped))
        run.clear()

    for s in f.sections:
        hidden = (s.value not in _CAPPED_KEEP_VALUES) if capped else (s.value in _SILENT_VALUES)
        if hidden:
            run.append(s)
            continue
        flush()
        lines.append(section_line(f, s))
    flush()
    return lines


def _size_text(f: FileDoc) -> str:
    size = f"{f.chars:,} chars"
    if f.pages:
        return f"{size} · {len(f.pages)} pages"
    return f"{size} · no page numbers ({len(f.parts)} parts for read_pages)"


def file_header(f: FileDoc, mode: str) -> str:
    parts = [f"## {f.label} — «{f.title}»"]
    if f.wi:
        parts.append(f.wi)
    if f.doc_type:
        parts.append(f.doc_type)
    parts.append(_size_text(f))
    parts.append(mode)
    return " · ".join(parts)


def plan_files(files: Sequence[FileDoc]) -> dict[str, str]:
    """``{label: "whole" | "outline" | "none"}`` — how each file is shown."""
    modes: dict[str, str] = {}
    whole_left = WHOLE_TOTAL_MAX_CHARS
    for f in files:
        if f.chars <= WHOLE_FILE_MAX_CHARS and f.chars <= whole_left:
            modes[f.label] = "whole"
            whole_left -= f.chars
        elif f.sections:
            modes[f.label] = "outline"
        else:
            modes[f.label] = "none"
    return modes


def render_files(files: Sequence[FileDoc]) -> str:
    """The <files> body. Marks the short files' whole text as seen."""
    modes = plan_files(files)
    # A whole short file keeps its outline when it has one: the threads name its
    # sections (F1·S2), and the outline says which part of the text that is.
    outlines = {f.label: outline_lines(f) for f in files if f.sections and modes[f.label] != "none"}
    if sum(len(line) + 1 for lines in outlines.values() for line in lines) > OUTLINE_MAX_CHARS:
        outlines = {label: outline_lines(next(f for f in files if f.label == label), capped=True)
                    for label in outlines}
    budget = OUTLINE_MAX_CHARS
    blocks: list[str] = []
    for f in files:
        mode = modes[f.label]
        if mode == "whole":
            header = file_header(
                f, "whole text below" + (f" + outline of {len(f.sections)} sections" if f.sections else ""),
            )
        elif mode == "outline":
            header = file_header(f, f"outline of {len(f.sections)} sections")
        else:
            header = file_header(f, "no outline: read it with find and read_pages")
        block = [_esc(header)]
        if f.summary:
            block.append(f"summary: {_esc(f.summary[:_SUMMARY_MAX_CHARS])}")
        if mode == "whole":
            f.given_whole = True
            f.mark_seen(0, len(f.view.text))
            block.append(f'<text file="{f.label}">\n{_esc(f.view.text)}\n</text>')
        if f.label in outlines:
            kept: list[str] = []
            for line in outlines[f.label]:
                if budget - len(line) - 1 < 0:
                    kept.append(f"[outline cut here — {len(outlines[f.label]) - len(kept)} more lines; use find]")
                    break
                budget -= len(line) + 1
                kept.append(line)
            block.append("<outline>\n" + _esc("\n".join(kept)) + "\n</outline>")
        blocks.append("\n".join(block))
    return "\n\n".join(blocks)


# ---------------------------------------------------------------------------
# Rendering — the angle map «الخيوط»
# ---------------------------------------------------------------------------


def _cut(text: str, limit: int) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[:limit].rstrip() + "…"


def render_threads(
    stored: Mapping[str, Any], by_item: Mapping[str, FileDoc], encode: Encoder | None, *, label: str = "",
    here: set[str] | None = None,
) -> str:
    """One angle map as the agent reads it. Section refs become the agent's own
    ``F1·S7``; a ref into a file not given here is counted, not shown. Threads are
    ``A{seq}`` and gaps ``G{n}`` (1-based, the order shown) — prefixed ``M1·`` when
    several maps are shown (``label``), so an edit names exactly one.

    ``here`` — for a map CARRIED from another conversation: this conversation's
    attachment ids. Its other documents are then counted as "not in this
    conversation" (apart from this conversation's files not given in this call),
    and the head says the map is shared, so a change shows there too."""
    pre = f"{label}·" if label else ""
    amap = stored.get("map") or {}
    doc_label: dict[int, str] = {}
    away: set[int] = set()        # documents of a carried map that are not in this conversation
    outside = 0
    for it in stored.get("items") or []:
        f = by_item.get(str(it.get("item_id")))
        doc = int(it.get("doc_index") or 0)
        if f is not None:
            doc_label[doc] = f.label
        elif here is not None and str(it.get("item_id")) not in here:
            away.add(doc)
        else:
            outside += 1

    def refs(values: Iterable[Any]) -> str:
        shown: list[str] = []
        other = elsewhere = 0
        for value in values or []:
            parsed = parse_ref(value)
            label = doc_label.get(parsed[0]) if parsed else None
            if label is not None:
                shown.append(f"{label}·S{parsed[1]}")
            elif parsed and parsed[0] in away:
                elsewhere += 1
            else:
                other += 1
        text = ", ".join(dict.fromkeys(shown))
        if other:
            text = (text + " " if text else "") + f"(+{other} in files not given here)"
        if elsewhere:
            text = (text + " " if text else "") + f"(+{elsewhere} in documents not in this conversation)"
        return text or "—"

    def e(text: Any, limit: int = _ARGUMENT_MAX_CHARS) -> str:
        return _cut(_enc(encode, text), limit)

    files = ", ".join(sorted(set(doc_label.values()), key=lambda x: int(x[1:])))
    head = f"## {label + ' — ' if label else ''}Threads «الخيوط» — analysis of {files or 'these files'}"
    if outside:
        head += f" (+{outside} file{'s' if outside != 1 else ''} not given here)"
    if here is not None:
        head += (
            " — carried from another conversation with a «الملفات» file"
            + (f", with {len(away)} document{'s' if len(away) != 1 else ''} not in this conversation"
               if away else "")
            + "; a change here shows there too"
        )
    lines = [head]

    client = amap.get("client") or {}
    if client.get("party") or client.get("side"):
        line = f"Client: {client.get('side') or 'unknown'} «{e(client.get('party'), 200)}»"
        if client.get("basis"):
            line += f" — {e(client.get('basis'), 300)}"
        lines.append(line)

    angles = stored.get("angles") or []
    if not angles:
        lines.append("No threads.")
    for a in angles:
        line = f"{pre}A{a.get('seq')} · «{e(a.get('title'), 200)}»"
        if a.get("for_party") or a.get("for_side"):
            line += f" · for: {a.get('for_side') or 'unknown'} «{e(a.get('for_party'), 120)}»"
        if a.get("strength"):
            line += f" · strength: {e(a.get('strength'), 120)}"
        line += f" · sections: {refs(a.get('sections'))}"
        lines.append(line)
        if a.get("argument"):
            lines.append(f"   argument: {e(a.get('argument'))}")
        if a.get("weakness"):
            lines.append(f"   weakness: {e(a.get('weakness'))}")

    gaps = [g for g in amap.get("gaps") or [] if isinstance(g, Mapping)]
    if gaps:
        lines.append("Gaps:")
        for n, gap in enumerate(gaps, start=1):
            lines.append(
                f"{pre}G{n} · {gap.get('kind') or 'gap'}: {e(gap.get('issue'))} · sections: {refs(gap.get('sections'))}"
            )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# The turn message
# ---------------------------------------------------------------------------


def render_turn_message(call: AttachmentAgentCall, loaded: Loaded, recent: Sequence[Any]) -> str:
    conversation = "\n".join(
        f'<message role="{_esc(_get(m, "role"))}">{_esc(_get(m, "content"))}</message>'
        for m in recent
    )
    parts = [
        # Already masked by the router; encoding again is a no-op on fakes and
        # masks anything a caller passed unmasked.
        f"<current_message>{_esc(_enc(turn_encoder(), call.user_message))}</current_message>",
        f"<conversation>\n{conversation}\n</conversation>",
        f"<files>\n{render_files(loaded.files)}\n</files>",
    ]
    if loaded.without_text:
        names = "، ".join(f"«{_esc(t)}»" for t in loaded.without_text)
        parts.append(f"<files_without_text>{names} — no text extracted yet; cannot be read.</files_without_text>")
    if loaded.threads:
        parts.append("<threads>\n" + "\n\n".join(_esc(t) for t in loaded.threads) + "\n</threads>")
    else:
        parts.append("<threads>none — these files have no analysis «الخيوط».</threads>")
    return "\n\n".join(parts)


__all__ = [
    "CONVERSATION_ROWS",
    "Loaded",
    "OUTLINE_MAX_CHARS",
    "WHOLE_FILE_MAX_CHARS",
    "WHOLE_TOTAL_MAX_CHARS",
    "conversation_rows",
    "file_header",
    "load_attachment_rows",
    "load_context",
    "load_recent_messages",
    "outline_lines",
    "plan_files",
    "recent_messages",
    "render_files",
    "render_threads",
    "render_turn_message",
    "section_line",
    "turn_encoder",
]
