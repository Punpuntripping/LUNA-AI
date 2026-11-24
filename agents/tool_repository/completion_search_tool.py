"""``complete_search`` / ``keep_results`` — the planners' door to the Completion Search helper.

Plan: ``.claude/plans/completion_search.md`` §4.4 (owner decisions C1–C2, C8–C10, C13).

- ``complete_search(request)`` — the planner sends plain words («نظام المعاملات المدنية
  المادة 56 والمادة 234», a ruling's case number, a circular …). High-confidence
  results are pinned at once; medium ones come back with a tag and are pinned only if
  the planner accepts them with ``keep_results``. No search fee. Never raises: any
  failure returns one plain Arabic line.
- A pinned text does three things: it goes on the conversation's «النصوص المثبّتة» card
  (``shared/workspace/pinned_texts.py``), it is announced to the chat, and its full
  wording travels to the agents that work after the planner.

**What survives a pause.** Every pinned block the planner reads carries a tag
(``[P-1a2b3c4d]``, derived from the text's identity). The tags stay in the run's
message history, so ``collect_turn_pins`` can rebuild the turn's pins from the history
after an ``ask_user`` pause — on fresh deps, in another process — reading the texts
back from the card. This is what was missing before: an article fetched around a
pause never reached the search agents.

**وضع السرية.** The planner reads encoded text, so a long case number in its request
is a fake. The helper's model is given the request as it is (it never sees the real
number); ``MaskedLibrary`` decodes the number only for the database lookup and encodes
what comes back about that ruling. The card stores the real text.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import re
from dataclasses import replace
from typing import Any, Callable, Iterable, Mapping

from pydantic_ai import Agent, RunContext

logger = logging.getLogger(__name__)

#: The longest the planner waits for one request (the helper's own model answers in
#: about 7 seconds on average, 16 at worst in the Phase 1 evaluation).
TOOL_TIMEOUT_S = 90.0
#: Ledger family per caller (``llm_calls.agent_family``).
FAMILY_BY_CALLER = {"deep_planner": "deep_search", "writer_planner": "writing"}

PIN_TAG_RE = re.compile(r"\[P-([0-9a-f]{8})\]")
_KEEP_ID_RE = re.compile(r"^[PK]-([0-9a-f]{8})$", re.I)

FAILED_AR = "تعذّر إتمام البحث عن النصوص المطلوبة الآن. أكمل العمل بدونها."
MEDIUM_FOOTER_AR = (
    "النتائج الموسومة [K-…] ثقتها متوسطة ولم تُثبَّت. إن كان النص هو المقصود فعلًا فثبّته: "
    "keep_results([\"K-…\"]). وإن لم يكن فاتركه."
)
KEPT_AR = "ثُبّت: {title} [P-{sid}]"
UNKNOWN_KEEP_AR = (
    "الرمز {raw} غير معروف في هذه الجولة. اطلب النص مجددًا بـ complete_search ثم ثبّته."
)
NOTHING_KEPT_AR = "لم يُذكر أي رمز لتثبيته."
BLOCK_MARKER_AR = "> 📌 نصوص رسمية مثبّتة لهذا الطلب (بنصها الكامل)"

#: Source types whose card entry holds the full text. The others hold a lead only and
#: are opened again from the library when a pin has to be rebuilt after a pause.
_FULL_ON_CARD = frozenset({"article", "principle"})
_CARD_EVENT_TYPES = frozenset({"workspace_item_created", "workspace_item_updated", "referenced_existing_item"})


def short_id(key: str) -> str:
    """A stable 8-character id for a pinned text, derived from its identity."""
    return hashlib.sha1(str(key).encode("utf-8")).hexdigest()[:8]


# --------------------------------------------------------------------------- #
# وضع السرية
# --------------------------------------------------------------------------- #


class MaskedLibrary:
    """A ``Library`` for a masked turn: the real case number exists only inside the lookup.

    ``decode`` turns the planner's fake back into the real number for the database;
    ``encode`` puts the fake back into anything that names that ruling before a model
    reads it. Laws, articles, circulars and services pass straight through — their
    names and numbers are short and never masked.
    """

    def __init__(self, inner: Any, *, encode: Callable[[str], str], decode: Callable[[str], str]) -> None:
        self._inner, self._encode, self._decode = inner, encode, decode

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    async def cases(self, case_number: str, court: str = "", year: str = ""):
        found = await self._inner.cases(self._decode(case_number), court, year)
        return [replace(c, title=self._encode(c.title)) for c in found]

    async def open_text(self, kind: str, object_id: str, title: str) -> str:
        text = await self._inner.open_text(kind, object_id, self._decode(title) if kind == "case" else title)
        return self._encode(text) if kind == "case" else text


def _masking() -> tuple[Callable[[str], str], Callable[[str], str]] | None:
    """``(encode, decode)`` of the turn's codec, or ``None`` outside a masked turn."""
    try:
        from backend.app.services.masking_service import active_codec, decode_text, encode_active

        codec = active_codec()
        if codec is None:
            return None
        return encode_active, (lambda text: decode_text(codec, text, emit=False))
    except Exception:  # noqa: BLE001 — masking unavailable = nothing to decode
        return None


# --------------------------------------------------------------------------- #
# The turn's pins
# --------------------------------------------------------------------------- #


def _slot(deps: Any, name: str) -> dict:
    slot = getattr(deps, name, None)
    if not isinstance(slot, dict):
        slot = {}
        try:
            setattr(deps, name, slot)
        except Exception:  # noqa: BLE001 — minimal deps: the pin still reaches the card
            pass
    return slot


def source_text_of(deps: Any, prompt: Any = None) -> str | None:
    """Everything the planner itself was given this turn — the user's messages, the
    attached documents, the earlier work. A law name counts as "written by the user"
    only when it stands in here; a name the planner completed on its own does not, and
    its result is offered as medium instead of being pinned. ``None`` when the deps
    carry no context (the request's own text is then the reference)."""
    cached = getattr(deps, "_cs_source_text", None)
    if isinstance(cached, str):
        return cached or None
    parts: list[str] = [str(prompt)] if isinstance(prompt, str) and prompt else []
    for name in ("intent", "case_brief", "compaction_summary_md"):
        parts.append(str(getattr(deps, name, "") or ""))
    for message in getattr(deps, "recent_messages", None) or []:
        parts.append(str(getattr(message, "content", "") or ""))
    for group in ("attached_items", "prior_searches", "prior_artifacts"):
        for item in getattr(deps, group, None) or []:
            for name in ("title", "describe_query", "summary", "content_md"):
                parts.append(str(getattr(item, name, "") or ""))
    text = "\n".join(p for p in parts if p)
    try:
        deps._cs_source_text = text
    except Exception:  # noqa: BLE001 — minimal deps
        pass
    return text or None


def _record(pin: Any) -> dict:
    return {
        "key": pin.key, "short_id": short_id(pin.key), "source_type": pin.source_type,
        "title": pin.title, "text": pin.text, "confidence": pin.confidence, "note": getattr(pin, "note", "") or "",
    }


def _announce(deps: Any, event: Mapping[str, Any] | None) -> None:
    """Card events go on ``deps._events`` — the list the turn hands to the chat relay.
    (The live ``emit_sse`` sink drops everything that is not a progress line.)"""
    events = getattr(deps, "_events", None)
    if event and isinstance(events, list):
        events.append(dict(event))


def card_events(deps: Any) -> list[dict]:
    """The card events this deps collected — for a caller that does not forward
    ``deps._events`` as a whole."""
    return [e for e in (getattr(deps, "_events", None) or []) if e.get("type") in _CARD_EVENT_TYPES]


async def _pin(deps: Any, pins: list[Any], *, caller: str, decode: Callable[[str], str] | None) -> None:
    """Pin texts for this turn: remember them for the agents that follow, write the card."""
    if not pins:
        return
    turn = _slot(deps, "_turn_pins")
    for pin in pins:
        turn.setdefault(pin.key, _record(pin))
    user_id = str(getattr(deps, "user_id", "") or "")
    conversation_id = str(getattr(deps, "conversation_id", "") or "")
    if not (user_id and conversation_id):
        return
    try:
        from shared.workspace.pinned_texts import add_agent_pins

        stored = pins
        if decode is not None:
            # Store real (the table never holds fakes): only a ruling's text can carry one.
            stored = [
                p.model_copy(update={"title": decode(p.title), "text": decode(p.text)})
                if p.source_type == "case" else p
                for p in pins
            ]
        write = await asyncio.to_thread(
            add_agent_pins, deps.supabase, user_id=user_id, conversation_id=conversation_id,
            pins=stored, by=caller,
        )
        if write is not None:
            _announce(deps, write.event())
    except Exception:  # noqa: BLE001 — the card is the user's view; the search goes on without it
        logger.warning("completion_search: card write failed", exc_info=True)


def _history_tags(messages: Iterable[Any]) -> list[str]:
    """Pin tags found in what the tools returned during this run, in order."""
    out: list[str] = []
    for message in messages or []:
        for part in getattr(message, "parts", None) or []:
            if type(part).__name__ != "ToolReturnPart":
                continue
            if getattr(part, "tool_name", "") not in ("complete_search", "keep_results"):
                continue
            for sid in PIN_TAG_RE.findall(str(getattr(part, "content", "") or "")):
                if sid not in out:
                    out.append(sid)
    return out


def _card_entries(supabase: Any, user_id: str, conversation_id: str) -> list[dict]:
    from shared.workspace.pinned_texts import KIND

    rows = (
        supabase.table("workspace_items").select("metadata")
        .eq("conversation_id", conversation_id).eq("user_id", user_id).eq("kind", KIND)
        .is_("deleted_at", "null").limit(1).execute().data or []
    )
    meta = rows[0].get("metadata") if rows else None
    return list((meta or {}).get("entries") or []) if isinstance(meta, Mapping) else []


async def collect_turn_pins(deps: Any, messages: Iterable[Any] | None = None, *, library: Any = None) -> list[dict]:
    """Every text pinned in this run, in the order it was pinned.

    Pins made on this deps are already in memory. Pins made before a pause are
    recovered from the run's message history (their tags) and read back from the
    conversation's card; a ruling, a circular or a service — which the card keeps as a
    lead only — is opened again from the library. Never raises.
    """
    turn = _slot(deps, "_turn_pins")
    by_sid = {rec["short_id"]: rec for rec in turn.values()}
    wanted = [sid for sid in _history_tags(messages or []) if sid not in by_sid]
    if wanted:
        try:
            user_id = str(getattr(deps, "user_id", "") or "")
            conversation_id = str(getattr(deps, "conversation_id", "") or "")
            entries = await asyncio.to_thread(_card_entries, deps.supabase, user_id, conversation_id)
            for entry in entries:
                sid = short_id(entry.get("key") or "")
                if sid not in wanted:
                    continue
                source_type = str(entry.get("source_type") or "")
                text = str(entry.get("body_md") or "")
                if source_type not in _FULL_ON_CARD:
                    text = await _reopen(deps, library, source_type, entry) or text
                record = {
                    "key": entry.get("key"), "short_id": sid, "source_type": source_type,
                    "title": str(entry.get("title") or ""), "text": text,
                    "confidence": str(entry.get("confidence") or "high"), "note": "",
                }
                masking = _masking()
                if masking and source_type == "case":      # the agents after the planner read encoded text
                    record["title"], record["text"] = masking[0](record["title"]), masking[0](record["text"])
                by_sid[sid] = record
                turn.setdefault(record["key"], record)
        except Exception:  # noqa: BLE001 — a pin that cannot be rebuilt is left out, the turn goes on
            logger.warning("completion_search: could not rebuild pins from the history", exc_info=True)
    ordered = [by_sid[sid] for sid in _history_tags(messages or []) if sid in by_sid]
    ordered += [rec for rec in turn.values() if rec not in ordered]
    return ordered


async def _reopen(deps: Any, library: Any, source_type: str, entry: Mapping[str, Any]) -> str:
    ids = entry.get("ids") or {}
    object_id = ids.get(f"{source_type}_id") or ""
    if not object_id:
        return ""
    if library is None:
        from agents.completion_search import SupabaseLibrary

        library = SupabaseLibrary(deps.supabase)
    return await library.open_text(source_type, str(object_id), str(entry.get("title") or ""))


def render_pins_block(pins: Iterable[Mapping[str, Any]]) -> str:
    """The ``statute_articles`` block body: every pinned text of the turn, in full."""
    def heading(p: Mapping[str, Any]) -> str:
        title = str(p.get("title") or "")
        # The heading the search agents' prompts name for an article: «## نص المادة … من …».
        return f"## نص {title}" if p.get("source_type") == "article" else f"## {title}"

    sections = "\n\n".join(
        f"{heading(p)}\n\n{str(p.get('text') or '').strip()}" for p in pins if p.get("text")
    )
    return f"{BLOCK_MARKER_AR}\n\n{sections}" if sections else ""


# --------------------------------------------------------------------------- #
# The tools
# --------------------------------------------------------------------------- #


def register_completion_search(agent: Agent, caller: str, *, library_factory: Callable[[Any], Any] | None = None) -> None:
    """Register ``complete_search`` and ``keep_results`` on a planner agent.

    The agent's deps must expose ``.supabase`` and should expose ``.user_id`` /
    ``.conversation_id`` (the card) and ``._events`` (the announcement).
    ``library_factory(deps)`` replaces the real library in tests.
    """
    family = FAMILY_BY_CALLER.get(caller)

    def _library(deps: Any) -> tuple[Any, Callable[[str], str] | None]:
        if library_factory is not None:
            library = library_factory(deps)
        else:
            from agents.completion_search import SupabaseLibrary

            library = SupabaseLibrary(deps.supabase)
        masking = _masking()
        if masking is None:
            return library, None
        return MaskedLibrary(library, encode=masking[0], decode=masking[1]), masking[1]

    @agent.tool
    async def complete_search(ctx: RunContext[Any], request: str) -> str:
        """Fetch the official wording of NAMED legal texts: articles by number, a phrase in a law, a ruling by its case number, a judicial principle, a circular, a government service.

        Write the request in plain words, exactly as the names and numbers appear in
        front of you — one request may ask for several things:
        «نظام المعاملات المدنية المادة 56 والمادة 234» ·
        «تعريف صاحب العمل في نظام العمل» · «الحكم في القضية رقم 4455 من المحكمة التجارية».

        Every result comes with a confidence level. A high-confidence text is pinned at
        once and marked [P-…]: it is saved on the conversation's card and travels in
        full to the agents that work after you. A medium-confidence text is marked
        [K-…] and is NOT pinned until you accept it with `keep_results`. A low one is
        only a list of possible titles.

        What you read here may be shortened; what is pinned is always the full text.
        This does not replace searching — it supplies exact wording only.

        Args:
            request: what to find, in plain words. Keep names and numbers exactly as given.
        """
        deps = ctx.deps
        try:
            from agents.completion_search import render_for_caller, run_completion_search
            from agents.tool_repository.fetch_article import _cap_for_planner

            library, decode = _library(deps)
            result = await asyncio.wait_for(
                run_completion_search(
                    request, library=library, caller=caller, agent_family=family,
                    conversation_id=getattr(deps, "conversation_id", None),
                    case_id=getattr(deps, "case_id", None), user_id=getattr(deps, "user_id", None),
                    source_text=source_text_of(deps, getattr(ctx, "prompt", None)),
                ),
                timeout=TOOL_TIMEOUT_S,
            )
            if result.error:
                return FAILED_AR
            high = result.pins("high")
            medium = result.pins("medium")
            await _pin(deps, high, caller=caller, decode=decode)
            pending = _slot(deps, "_pending_pins")
            tags: dict[str, str] = {p.key: f"P-{short_id(p.key)}" for p in high}
            for pin in medium:
                if pin.key not in tags:
                    pending[short_id(pin.key)] = pin
                    tags[pin.key] = f"K-{short_id(pin.key)}"
            text = render_for_caller(result, cap_text=_cap_for_planner, ids=tags)
            if any(tag.startswith("K-") for tag in tags.values()):
                text = f"{text}\n\n{MEDIUM_FOOTER_AR}"
            logger.info(
                "complete_search[%s]: searches=%d high=%d medium=%d model=%s",
                caller, len(result.searches), len(high), len(medium), result.model_used,
            )
            return text
        except Exception:  # noqa: BLE001 — a lookup must never break the planner's turn
            logger.warning("complete_search failed", exc_info=True)
            return FAILED_AR

    @agent.tool
    async def keep_results(ctx: RunContext[Any], result_ids: list[str]) -> str:
        """Pin medium-confidence texts that `complete_search` returned, after you checked they are the ones meant.

        Pass the tags exactly as printed, e.g. ["K-1a2b3c4d"]. A kept text is saved on the
        conversation's card and travels in full to the agents that work after you, like a
        high-confidence one. Keep a text only when its title is clearly the law, ruling
        or document that was asked for.

        Args:
            result_ids: the [K-…] tags to pin.
        """
        deps = ctx.deps
        try:
            pending = _slot(deps, "_pending_pins")
            turn = _slot(deps, "_turn_pins")
            lines: list[str] = []
            keep: list[Any] = []
            for raw in result_ids or []:
                m = _KEEP_ID_RE.match(str(raw).strip().strip("[]"))
                sid = m.group(1).lower() if m else ""
                pin = pending.pop(sid, None) if sid else None
                if pin is not None:
                    keep.append(pin)
                    lines.append(KEPT_AR.format(title=pin.title, sid=sid))
                    continue
                done = next((rec for rec in turn.values() if rec["short_id"] == sid), None) if sid else None
                lines.append(KEPT_AR.format(title=done["title"], sid=sid) if done else UNKNOWN_KEEP_AR.format(raw=raw))
            if keep:
                _library_unused, decode = _library(deps)
                await _pin(deps, keep, caller=caller, decode=decode)
            return "\n".join(lines) if lines else NOTHING_KEPT_AR
        except Exception:  # noqa: BLE001
            logger.warning("keep_results failed", exc_info=True)
            return FAILED_AR


__all__ = [
    "BLOCK_MARKER_AR",
    "FAILED_AR",
    "MaskedLibrary",
    "PIN_TAG_RE",
    "TOOL_TIMEOUT_S",
    "card_events",
    "collect_turn_pins",
    "register_completion_search",
    "render_pins_block",
    "short_id",
    "source_text_of",
]
