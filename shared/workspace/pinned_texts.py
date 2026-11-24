"""«النصوص المثبّتة» — the ONE card per conversation that shows the user which official
texts were pinned.

Plan: ``.claude/plans/completion_search.md`` §4.5 (owner decisions C8, C21, C25).
It replaces the ``statute_package`` note (a new note on every search turn) with one
read-only workspace item of kind ``pinned_texts``:

- each text appears ONCE, with a «ورد في» line for every place that cited it — an
  attachment's own sentence, or the agent that asked for it;
- articles and judicial principles show their full official text; rulings, circulars
  and services show their title and a short lead (plan §8 question 1);
- the item is written by code only: nobody edits it (the API allows edits for
  ``note`` / ``agent_writing`` alone), it is outside the 15-item cap (the cap counts
  three other kinds) and no model summarises it (its summary is set here).

Two writers can reach the card at once — a planner's pin and a background attachment
run in the same turn. ``upsert_card`` merges with a compare-and-set on ``updated_at``
and retries; two writers that both find no card are settled by the unique index of
migration 230.

This module is the lower layer: it takes plain objects (anything with the attributes
it reads) and a service-role Supabase client, and imports nothing from ``agents`` or
``backend``.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping, Sequence

logger = logging.getLogger(__name__)

KIND = "pinned_texts"
SUBTYPE = "pinned_texts"
CREATED_BY = "agent"
#: The card's name on screen (owner decision C25).
TITLE_AR = "النصوص المثبّتة"
HEADER_AR = "> 📌 نصوص رسمية ثبّتها ريحان في هذه المحادثة — للقراءة فقط."
SUMMARY_LEAD_AR = "نصوص رسمية مثبّتة في هذه المحادثة (للقراءة فقط):"
LEAD_NOTE_AR = "(مقتطف — النص الكامل في المكتبة)"
BY_LABEL_AR = {
    "deep_planner": "أُضيف أثناء البحث",
    "writer_planner": "أُضيف أثناء إعداد الصياغة",
}
ATTACHMENT_LABEL_AR = "ورد في المرفق «{title}»: «{sentence}»"
ATTACHMENT_LABEL_NO_TITLE_AR = "ورد في مرفق: «{sentence}»"

#: Source types whose full official text is shown on the card. The rest show a lead.
FULL_TEXT_TYPES: frozenset[str] = frozenset({"article", "principle"})
LEAD_CHARS = 600
SENTENCE_CHARS = 300
SUMMARY_CHARS = 1_500
_ITEMS_TABLE = "workspace_items"
_MAX_ATTEMPTS = 4
_CONFIDENCE_RANK = {"high": 3, "medium": 2, "low": 1}


# --------------------------------------------------------------------------- #
# Entries — pure
# --------------------------------------------------------------------------- #


def _flat(text: Any, limit: int) -> str:
    flat = " ".join(str(text or "").split())
    return flat if len(flat) <= limit else flat[:limit].rstrip() + "…"


def body_for(source_type: str, text: str) -> str:
    """What the card prints under an entry's title."""
    text = (text or "").strip()
    if source_type in FULL_TEXT_TYPES or len(text) <= LEAD_CHARS:
        return text
    cut = text[:LEAD_CHARS]
    newline = cut.rfind("\n")
    cut = cut[:newline] if newline >= LEAD_CHARS // 2 else cut
    return f"{cut.rstrip()}…\n\n{LEAD_NOTE_AR}"


def make_entry(
    *,
    key: str,
    source_type: str,
    title: str,
    text: str,
    source_title: str = "",
    confidence: str = "high",
    ids: Mapping[str, Any] | None = None,
    cited_in: Sequence[Mapping[str, Any]] = (),
) -> dict:
    """One card entry. ``key`` is the text's identity: the same text is one entry."""
    return {
        "key": key,
        "source_type": source_type,
        "title": " ".join(str(title or "").split()),
        "source_title": " ".join(str(source_title or "").split()),
        "confidence": confidence if confidence in _CONFIDENCE_RANK else "high",
        "body_md": body_for(source_type, text),
        "ids": {k: str(v) for k, v in (ids or {}).items() if v},
        "cited_in": [dict(c) for c in cited_in],
    }


def entry_from_pin(pin: Any, *, by: str) -> dict:
    """An entry for a text an agent asked the helper for. ``pin`` is a
    ``completion_search.models.Pin`` (read by attribute)."""
    ids = {
        name: getattr(pin, name, "")
        for name in ("regulation_id", "article_id", "article_number", "circular_id", "service_id",
                     "case_id", "principle_no")
    }
    label = BY_LABEL_AR.get(by, BY_LABEL_AR["deep_planner"])
    return make_entry(
        key=str(pin.key), source_type=str(pin.source_type), title=str(pin.title), text=str(pin.text),
        source_title=str(getattr(pin, "source_title", "") or ""),
        confidence=str(getattr(pin, "confidence", "high") or "high"), ids=ids,
        cited_in=[{"id": f"agent:{by}", "by": by, "label": label}],
    )


def attachment_entries(
    *, attachment_id: str, attachment_title: str, content: str, pins: Iterable[Any]
) -> list[dict]:
    """Entries for what ONE attachment cites. ``pins`` are
    ``citation_pins.store.PinnedArticle`` objects; ``content`` is the attachment's own
    text, used only to quote the citing sentence beside the article — so a lawyer can
    see a number that was misread from a scan."""
    out: dict[str, dict] = {}
    title = " ".join(str(attachment_title or "").split())
    for pin in sorted(pins, key=lambda p: int(getattr(p, "char_start", 0) or 0)):
        sentence = _flat((content or "")[int(pin.sentence_start):int(pin.sentence_end)], SENTENCE_CHARS)
        template = ATTACHMENT_LABEL_AR if title else ATTACHMENT_LABEL_NO_TITLE_AR
        cited = {
            "id": f"attachment:{attachment_id}:{int(pin.char_start)}",
            "by": "attachment",
            "item_id": str(attachment_id),
            "label": template.format(title=title, sentence=sentence),
        }
        key = f"article:{pin.article_id}"
        if key in out:
            out[key]["cited_in"].append(cited)
            continue
        out[key] = make_entry(
            key=key, source_type="article", title=str(pin.title), text=str(pin.text),
            source_title=str(pin.regulation_title), confidence="high",
            ids={"regulation_id": pin.regulation_id, "article_id": pin.article_id,
                 "article_number": pin.article_number},
            cited_in=[cited],
        )
    return list(out.values())


def merge_entries(existing: Sequence[Mapping[str, Any]], new: Sequence[Mapping[str, Any]]) -> tuple[list[dict], bool]:
    """``existing`` with ``new`` merged in, and whether anything changed.

    The same text (same ``key``) stays one entry: its «ورد في» lines are added to (never
    duplicated), its wording is refreshed to the library's current text, and its
    confidence only ever goes up. New texts are appended in the order they arrive.
    """
    merged = [dict(e) for e in existing if isinstance(e, Mapping) and e.get("key")]
    index = {e["key"]: i for i, e in enumerate(merged)}
    changed = False
    for entry in new:
        key = entry.get("key")
        if not key:
            continue
        if key not in index:
            index[key] = len(merged)
            merged.append({**entry, "cited_in": [dict(c) for c in entry.get("cited_in") or []]})
            changed = True
            continue
        current = merged[index[key]]
        cited = [dict(c) for c in current.get("cited_in") or []]
        seen = {c.get("id") for c in cited}
        for c in entry.get("cited_in") or []:
            if c.get("id") not in seen:
                seen.add(c.get("id"))
                cited.append(dict(c))
                changed = True
        better = _CONFIDENCE_RANK.get(str(entry.get("confidence")), 0) > _CONFIDENCE_RANK.get(
            str(current.get("confidence")), 0)
        updated = {
            **current,
            "title": entry.get("title") or current.get("title", ""),
            "source_title": entry.get("source_title") or current.get("source_title", ""),
            "body_md": entry.get("body_md") or current.get("body_md", ""),
            "ids": {**(current.get("ids") or {}), **(entry.get("ids") or {})},
            "confidence": entry.get("confidence") if better else current.get("confidence"),
            "cited_in": cited,
        }
        if updated != current:
            changed = True
        merged[index[key]] = updated
    return merged, changed


def render_card(entries: Sequence[Mapping[str, Any]]) -> str:
    """The card's markdown: each text once, its «ورد في» lines under it."""
    blocks = [HEADER_AR]
    for entry in entries:
        lines = [f"## {entry.get('title', '')}", "", str(entry.get("body_md") or "").strip()]
        cited = [str(c.get("label") or "").strip() for c in entry.get("cited_in") or []]
        cited = [c for c in dict.fromkeys(cited) if c]
        if cited:
            lines += ["", *[f"> {c}" for c in cited]]
        blocks.append("\n".join(lines).strip())
    return "\n\n---\n\n".join(blocks) if len(blocks) > 1 else HEADER_AR


def card_summary(entries: Sequence[Mapping[str, Any]]) -> str:
    """What other agents read of the card: its titles, never the full texts."""
    titles = "؛ ".join(str(e.get("title") or "") for e in entries if e.get("title"))
    return _flat(f"{SUMMARY_LEAD_AR} {titles}", SUMMARY_CHARS)


# --------------------------------------------------------------------------- #
# The write
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class CardWrite:
    item_id: str
    created: bool          # this call created the card
    changed: bool          # this call changed it (False: everything was already there)
    total: int             # entries on the card now
    title: str = TITLE_AR

    def event(self) -> dict:
        """The event the chat relays so the card shows up, refreshes, or is pointed at.

        The three shapes already exist for other agent items, and each puts the card
        under the reply as the one green «افتح …» button: a new card is
        ``workspace_item_created``; a card this turn added to is
        ``workspace_item_updated``; a card that already held everything this turn
        pinned is ``referenced_existing_item``.
        """
        if self.created:
            return {
                "type": "workspace_item_created",
                "item_id": self.item_id,
                "kind": KIND,
                "subtype": SUBTYPE,
                "title": self.title,
                "created_by": CREATED_BY,
            }
        if self.changed:
            return {"type": "workspace_item_updated", "item_id": self.item_id}
        return {"type": "referenced_existing_item", "item_id": self.item_id}


def _rows(resp: Any) -> list[dict]:
    return list(getattr(resp, "data", None) or [])


def _find_card(supabase: Any, user_id: str, conversation_id: str) -> dict | None:
    rows = _rows(
        supabase.table(_ITEMS_TABLE).select("item_id,metadata,updated_at")
        .eq("conversation_id", conversation_id).eq("user_id", user_id).eq("kind", KIND)
        .is_("deleted_at", "null").limit(1).execute()
    )
    return rows[0] if rows else None


def _set_summary(supabase: Any, item_id: str, updated_at: Any, entries: Sequence[Mapping[str, Any]], content: str) -> None:
    """A second statement on purpose: a trigger clears ``summary`` whenever ``content_md``
    changes, and a card with no summary would be handed to the LLM summariser. Guarded
    by the stamp of OUR content write, so a newer merge's summary is never overwritten."""
    now = datetime.now(timezone.utc).isoformat()
    query = (
        supabase.table(_ITEMS_TABLE)
        .update({"summary": card_summary(entries), "summary_source_length": len(content), "summary_updated_at": now})
        .eq("item_id", item_id)
    )
    if updated_at:
        query = query.eq("updated_at", updated_at)
    query.execute()


def upsert_card(
    supabase: Any,
    *,
    user_id: str,
    conversation_id: str,
    entries: Sequence[Mapping[str, Any]],
    case_id: Any = None,
) -> CardWrite | None:
    """Merge ``entries`` into the conversation's card, creating it on the first pin.

    Returns ``None`` when there is nothing to write or no conversation to write to.
    Raises only when every attempt lost its race or the database refused the write —
    callers treat the card as best effort.
    """
    entries = [e for e in entries if e.get("key")]
    if not entries or not user_id or not conversation_id:
        return None
    last_error: Exception | None = None
    for _attempt in range(_MAX_ATTEMPTS):
        card = _find_card(supabase, str(user_id), str(conversation_id))
        if card is None:
            merged, _ = merge_entries([], entries)
            content = render_card(merged)
            now = datetime.now(timezone.utc).isoformat()
            payload = {
                "user_id": str(user_id), "conversation_id": str(conversation_id), "kind": KIND,
                "created_by": CREATED_BY, "title": TITLE_AR, "is_visible": True, "content_md": content,
                "metadata": {"subtype": SUBTYPE, "entries": merged},
                # set at insert: the summarise-on-insert trigger fires only for a NULL summary
                "summary": card_summary(merged), "summary_source_length": len(content), "summary_updated_at": now,
            }
            if case_id:
                payload["case_id"] = str(case_id)
            try:
                rows = _rows(supabase.table(_ITEMS_TABLE).insert(payload).execute())
            except Exception as exc:  # noqa: BLE001 — another writer created the card first (unique index, 230)
                last_error = exc
                continue
            if rows and rows[0].get("item_id"):
                return CardWrite(str(rows[0]["item_id"]), created=True, changed=True, total=len(merged))
            last_error = RuntimeError("insert returned no row")
            continue

        meta = card.get("metadata") if isinstance(card.get("metadata"), Mapping) else {}
        merged, changed = merge_entries(meta.get("entries") or [], entries)
        item_id = str(card["item_id"])
        if not changed:
            return CardWrite(item_id, created=False, changed=False, total=len(merged))
        content = render_card(merged)
        rows = _rows(
            supabase.table(_ITEMS_TABLE)
            .update({"content_md": content, "metadata": {**meta, "subtype": SUBTYPE, "entries": merged},
                     "updated_at": datetime.now(timezone.utc).isoformat()})
            .eq("item_id", item_id).eq("updated_at", card.get("updated_at")).execute()
        )
        if not rows:
            continue                      # someone else wrote the card since we read it — read again
        try:
            _set_summary(supabase, item_id, rows[0].get("updated_at"), merged, content)
        except Exception:  # noqa: BLE001 — the texts are saved; a missing summary is repaired on the next merge
            logger.warning("pinned_texts: summary write failed for %s", item_id, exc_info=True)
        return CardWrite(item_id, created=False, changed=True, total=len(merged))
    raise RuntimeError(f"pinned_texts: card write did not settle ({type(last_error).__name__ if last_error else 'contention'})")


def add_agent_pins(
    supabase: Any, *, user_id: str, conversation_id: str, pins: Iterable[Any], by: str, case_id: Any = None
) -> CardWrite | None:
    """Put the texts an agent pinned on the conversation's card."""
    return upsert_card(
        supabase, user_id=user_id, conversation_id=conversation_id, case_id=case_id,
        entries=[entry_from_pin(pin, by=by) for pin in pins],
    )


def add_attachment_pins(
    supabase: Any,
    *,
    user_id: str,
    conversation_id: str,
    attachment_id: str,
    attachment_title: str,
    content: str,
    pins: Iterable[Any],
    case_id: Any = None,
) -> int:
    """Put what an attachment cites on its conversation's card. Returns the entries on
    the card afterwards (0 when nothing was written)."""
    write = upsert_card(
        supabase, user_id=user_id, conversation_id=conversation_id, case_id=case_id,
        entries=attachment_entries(
            attachment_id=attachment_id, attachment_title=attachment_title, content=content, pins=pins,
        ),
    )
    return write.total if write else 0


__all__ = [
    "CREATED_BY",
    "CardWrite",
    "FULL_TEXT_TYPES",
    "HEADER_AR",
    "KIND",
    "SUBTYPE",
    "TITLE_AR",
    "add_agent_pins",
    "add_attachment_pins",
    "attachment_entries",
    "body_for",
    "card_summary",
    "entry_from_pin",
    "make_entry",
    "merge_entries",
    "render_card",
    "upsert_card",
]
