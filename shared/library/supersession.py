"""Supersession — what a replaced regulation still means during its transition.

``reg_status`` answers one question: did the corpus record a repeal? A law
replaced by another needs more than that bit. The old text can stay applicable
for a transition period, parts of it can outlive that period with no end date,
and the reranker is built to drop anything flagged «ملغي». So the old law's
status comes from stored dates here, not from ``status_class`` alone.

The data lives on the old row (``superseded_by`` + ``superseded_dates``) and the
new row (``transition_period_note``, ``effective_date``), migration 199. Nothing
in this module names a law, a ref or a date: the next replaced law is a data
change only (``scripts/set_supersession.py``), and a نظام and a لائحة follow the
same rules because every rule keys on a regulations row.

A :class:`Pair` is ONE OLD ROW plus its whole ``superseded_by`` list. So a split
is one pair with several successors, and a merge is several pairs sharing a new
row, which is why ``for_regulation`` returns a tuple.

Fail open, and silent by default: a load failure means an empty map, an empty
map means every function below returns today's answer, and a regulation with no
pair gets byte-identical prompts and costs no extra tokens.

    phase                       reranker, old chunk             aggregator
    upcoming                    no line                         <transitions>
    period_1                    no line                         <transitions>
    period_2, or after+pending  no line on surviving chunks     <transitions>
                                or if the sub-query names the
                                old version, else repeal line
    after, nothing pending      repeal line                     <status>
"""
from __future__ import annotations

import logging
import re
import threading
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable, Mapping

from shared.library.reg_status import (
    REPEALED_STATUS_CLASS,
    is_repealed,
    status_line,
)

__all__ = [
    "UPCOMING",
    "PERIOD_1",
    "PERIOD_2",
    "AFTER",
    "VERSION_OLD",
    "VERSION_NEW",
    "Successor",
    "Pair",
    "Transition",
    "AggregatorStatus",
    "SupersessionMap",
    "load_supersessions",
    "reset_cache",
    "riyadh_today",
    "phase",
    "version_choice",
    "version_title",
    "strip_version_marker",
    "names_old_version",
    "old_chunk_eligible",
    "takes_extra_slot",
    "reranker_status",
    "reranker_name",
    "aggregator_status",
    "phase_instruction",
    "both_versions_instruction",
    "chunk_provisions",
    "group_by_note",
    "responder_line",
    "PROVISIONS_HEADING",
    "RESPONDER_VERSION_RULE",
]

logger = logging.getLogger(__name__)

UPCOMING = "upcoming"
PERIOD_1 = "period_1"
PERIOD_2 = "period_2"
AFTER = "after"

VERSION_OLD = "old"
VERSION_NEW = "new"
_VERSION_MARKERS = {VERSION_OLD: "(القديم)", VERSION_NEW: "(الجديد)"}
PROVISIONS_HEADING = "الأحكام الانتقالية"

#: How long a loaded map is trusted. Also the rollback time: clearing
#: ``superseded_by`` on a row takes effect within this window, with no deploy.
_CACHE_TTL_SECONDS = 300.0
#: A failed load is retried sooner than the TTL, so a blip doesn't switch
#: transitions off for 5 minutes, but not on every chunk during an outage.
_RETRY_AFTER_FAILURE_SECONDS = 30.0

#: Saudi Arabia has no DST. A fixed offset, not ``ZoneInfo``, because a slim
#: image without tzdata would otherwise fail at the first call.
_RIYADH = timezone(timedelta(hours=3))

_OLD_COLUMNS = "id, reg_ref, clean_title, title, superseded_by, superseded_dates"
_SUCCESSOR_COLUMNS = "id, reg_ref, transition_period_note"


# -- Data ---------------------------------------------------------------------


@dataclass(frozen=True)
class Successor:
    """One ``superseded_by`` entry, plus what the loader read from its row."""

    title: str
    reg_ref: str | None = None
    part: str | None = None
    pending: bool = False
    chunk_refs: tuple[str, ...] = ()
    reg_id: str | None = None
    note: str = ""


@dataclass(frozen=True)
class Pair:
    """One old regulation and everything that replaces it."""

    old_id: str
    old_ref: str
    old_title: str
    superseded_on: date
    period_1_end: date
    period_2_end: date
    successors: tuple[Successor, ...]

    @property
    def replacements(self) -> tuple[Successor, ...]:
        """The successors actually in place: the entries not marked pending."""
        return tuple(s for s in self.successors if not s.pending)

    @property
    def pending_parts(self) -> tuple[Successor, ...]:
        """The parts of the old law still in force."""
        return tuple(s for s in self.successors if s.pending)

    @property
    def new_title(self) -> str:
        """Every replacement's title; one title except for a split."""
        return " و".join(s.title for s in self.replacements)

    @property
    def surviving_chunks(self) -> frozenset[str]:
        """Chunks of the old row holding a part still in force."""
        return frozenset(c for s in self.pending_parts for c in s.chunk_refs)

    @property
    def note(self) -> str:
        """The replacements' distinct notes; one note except for a split."""
        notes = dict.fromkeys(s.note for s in self.replacements if s.note)
        return "\n\n".join(notes)


@dataclass(frozen=True)
class Transition:
    """One entry for the aggregator's ``<transitions>`` block."""

    phase: str
    old_title: str
    new_title: str
    in_force_on: str
    instruction: str
    note: str
    new_titles: tuple[str, ...] = ()


@dataclass(frozen=True)
class AggregatorStatus:
    """What the aggregator gets for one reference.

    ``status`` is the ``<status>`` text, ``""`` for no element. ``version`` is
    ``old`` / ``new`` for ``<version>``, set only while a block is needed, and
    ``transitions`` is empty when no ``<transitions>`` block is needed.
    """

    status: str = ""
    version: str = ""
    transitions: tuple[Transition, ...] = ()


class SupersessionMap:
    """Every pair, findable from either side by uuid or by ``reg_ref``."""

    def __init__(self, pairs: Iterable[Pair] = ()) -> None:
        pairs = tuple(pairs)
        self._old: dict[str, Pair] = {}
        self._by_reg: dict[str, tuple[Pair, ...]] = {}
        # Old sides first, so a row in a chain lists the pair that retires it
        # before the pair that created it.
        for pair in pairs:
            for key in (pair.old_id, pair.old_ref):
                self._old[key] = pair
                self._add(key, pair)
        # A pending successor is NOT a new side: the part hasn't moved, and
        # indexing it would put a block on every citation of that other law.
        for pair in pairs:
            for successor in pair.replacements:
                for key in (successor.reg_id, successor.reg_ref):
                    if key:
                        self._add(key, pair)

    def _add(self, key: str, pair: Pair) -> None:
        current = self._by_reg.get(key, ())
        if pair not in current:
            self._by_reg[key] = (*current, pair)

    def for_regulation(self, reg_id: Any) -> tuple[Pair, ...]:
        """Every pair this row belongs to; empty for a regulation with none."""
        return self._by_reg.get(str(reg_id), ()) if reg_id else ()

    def old_side(self, reg_id: Any) -> Pair | None:
        """The pair that retires this row, or ``None`` if nothing does."""
        return self._old.get(str(reg_id)) if reg_id else None

    def transition_note(self, reg_id: Any) -> str:
        """The note for a row: its own if new, its successor's if old."""
        key = str(reg_id)
        for pair in self.for_regulation(reg_id):
            if key in (pair.old_id, pair.old_ref):
                return pair.note
            for successor in pair.replacements:
                if key in (successor.reg_id, successor.reg_ref):
                    return successor.note
        return ""


# -- Loading ------------------------------------------------------------------


def _build_pair(row: Mapping[str, Any], successor_rows: Mapping[str, Any]) -> Pair:
    successors = []
    for entry in row["superseded_by"]:
        ref = entry.get("reg_ref")
        live = successor_rows.get(ref) or {}
        successors.append(
            Successor(
                title=entry.get("title") or "",
                reg_ref=ref,
                part=entry.get("part"),
                pending=bool(entry.get("pending")),
                chunk_refs=tuple(entry.get("chunk_refs") or ()),
                reg_id=live.get("id"),
                note=live.get("transition_period_note") or "",
            )
        )
    dates = row["superseded_dates"]
    return Pair(
        old_id=str(row["id"]),
        old_ref=row["reg_ref"],
        old_title=row.get("clean_title") or row.get("title") or "",
        superseded_on=date.fromisoformat(dates["superseded_on"]),
        period_1_end=date.fromisoformat(dates["period_1_end"]),
        period_2_end=date.fromisoformat(dates["period_2_end"]),
        successors=tuple(successors),
    )


def _fetch(supabase: Any) -> SupersessionMap:
    resp = (
        supabase.table("regulations_v2")
        .select(_OLD_COLUMNS)
        .not_.is_("superseded_by", "null")
        .execute()
    )
    # An empty array is what's left once a law's last pending entry is removed
    # by hand: nothing to transition, so it behaves as a plain regulation.
    rows = [r for r in (resp.data or []) if r.get("superseded_by")]
    refs = sorted({e["reg_ref"] for r in rows for e in r["superseded_by"] if e.get("reg_ref")})
    successor_rows: dict[str, Any] = {}
    if refs:
        live = (
            supabase.table("regulations_v2")
            .select(_SUCCESSOR_COLUMNS)
            .in_("reg_ref", refs)
            .execute()
        )
        successor_rows = {r["reg_ref"]: r for r in (live.data or [])}
    return SupersessionMap(_build_pair(r, successor_rows) for r in rows)


_clock = time.monotonic
_lock = threading.Lock()
_cached = SupersessionMap()
_expires_at = 0.0


def load_supersessions(supabase: Any) -> SupersessionMap:
    """The current map: cached 5 minutes, last good on error, empty if none.

    Never raises. Safe to call per chunk: only an expired cache touches the DB,
    and the lock keeps a parallel batch from loading it once per thread.
    """
    global _cached, _expires_at
    if _clock() < _expires_at:
        return _cached
    with _lock:
        if _clock() < _expires_at:
            return _cached
        try:
            _cached = _fetch(supabase)
            _expires_at = _clock() + _CACHE_TTL_SECONDS
        except Exception as e:  # noqa: BLE001 — fail open, by principle
            logger.warning("load_supersessions failed, keeping last good map: %s", e)
            _expires_at = _clock() + _RETRY_AFTER_FAILURE_SECONDS
        return _cached


def reset_cache() -> None:
    """Forget the cached map. For tests."""
    global _cached, _expires_at
    with _lock:
        _cached = SupersessionMap()
        _expires_at = 0.0


# -- Phase --------------------------------------------------------------------


def riyadh_today() -> date:
    """Today's date in Saudi Arabia. Dates in the law are local dates."""
    return datetime.now(_RIYADH).date()


def phase(pair: Pair, today: date) -> str:
    """``upcoming`` / ``period_1`` / ``period_2`` / ``after``. End dates inclusive."""
    if today < pair.superseded_on:
        return UPCOMING
    if today <= pair.period_1_end:
        return PERIOD_1
    if today <= pair.period_2_end:
        return PERIOD_2
    return AFTER


# -- Version markers ----------------------------------------------------------

_ARABIC_LETTER = "ء-ي"
# The definite article with its attached particles: «القديم», «والقديم»,
# «بالقديم», «للقديم». Whole words only, so «قديمة» alone never matches.
_ARTICLE = "(?:[وف]?(?:[بك]?ال|لل))"
_OLD_WORD = rf"(?<![{_ARABIC_LETTER}]){_ARTICLE}قديمة?(?![{_ARABIC_LETTER}])"
_NEW_WORD = rf"(?<![{_ARABIC_LETTER}]){_ARTICLE}جديدة?(?![{_ARABIC_LETTER}])"
_OLD_MARKER = re.compile(_OLD_WORD)
_NEW_MARKER = re.compile(_NEW_WORD)
# The marker with whatever wraps it: « (القديم)», « - الجديد», « القديم».
_MARKER_WITH_WRAPPING = re.compile(
    rf"[\s\-–—]*[(\[]?\s*(?:{_OLD_WORD}|{_NEW_WORD})\s*[)\]]?"
)


def names_old_version(text: str | None) -> bool:
    """True when the text carries an explicit old-version marker.

    Unlike :func:`version_choice` this stays true for a comparison («الفرق بين
    القديم والجديد»), which does name the old version.
    """
    return _OLD_MARKER.search(text or "") is not None


def version_choice(text: str | None) -> str | None:
    """``old`` / ``new`` from an explicit marker; ``None`` for none or both."""
    has_old = names_old_version(text)
    has_new = _NEW_MARKER.search(text or "") is not None
    if has_old == has_new:
        return None
    return VERSION_OLD if has_old else VERSION_NEW


def strip_version_marker(text: str | None) -> str:
    """The text with any old/new marker removed, for resolving a base title.

    ⚠ Some real titles END in one of these words and are not versions at all
    («… الأصناف النباتية الجديدة»). Only trust the stripped title if it resolves
    to a row with a pair; otherwise keep the original text.
    """
    # A space, not "": the pattern eats the leading whitespace, so a mid-title
    # marker would otherwise fuse the two words around it.
    return " ".join(_MARKER_WITH_WRAPPING.sub(" ", text or "").split())


# -- Reranker -----------------------------------------------------------------


def _row_key(reg_row: Mapping[str, Any]) -> Any:
    return reg_row.get("id") or reg_row.get("reg_ref")


def old_chunk_eligible(
    supersessions: SupersessionMap,
    reg_row: Mapping[str, Any],
    chunk_ref: str | None,
    sub_query: str | None,
    today: date,
) -> bool:
    """True when this chunk is on an old row and must reach the reranker clean.

    The live decider scores against a frozen rubric and can't be told "repealed,
    except…", so the exception is decided here. ``False`` for a no-pair row and
    for a new row.
    """
    pair = supersessions.old_side(_row_key(reg_row))
    if pair is None:
        return False
    current = phase(pair, today)
    if current in (UPCOMING, PERIOD_1):
        return True
    if current == AFTER and not pair.pending_parts:
        return False
    return chunk_ref in pair.surviving_chunks or names_old_version(sub_query)


def takes_extra_slot(
    supersessions: SupersessionMap,
    reg_row: Mapping[str, Any],
    chunk_ref: str | None,
    sub_query: str | None,
    today: date,
) -> bool:
    """True when an eligible old chunk is capped apart, in the extra slots.

    Not in ``upcoming``, where the old law is simply the law and must compete on
    equal terms, and not when the sub-query names the old version, which wants it.
    """
    pair = supersessions.old_side(_row_key(reg_row))
    return (
        pair is not None
        and phase(pair, today) != UPCOMING
        and not names_old_version(sub_query)
        and old_chunk_eligible(supersessions, reg_row, chunk_ref, sub_query, today)
    )


def reranker_status(
    supersessions: SupersessionMap,
    reg_row: Mapping[str, Any],
    chunk_ref: str | None,
    sub_query: str | None,
    today: date,
) -> str:
    """The status line the reranker sees: ``""`` or the standard repeal line."""
    status_class = reg_row.get("status_class")
    status_raw = reg_row.get("status_raw")
    if supersessions.old_side(_row_key(reg_row)) is None:
        return status_line(status_class, status_raw)
    if old_chunk_eligible(supersessions, reg_row, chunk_ref, sub_query, today):
        return ""
    # By the stored dates this row is repealed, even if nobody has flipped its
    # status_class yet. Its raw status is only worth quoting if it agrees.
    raw = status_raw if is_repealed(status_class) else None
    return status_line(REPEALED_STATUS_CLASS, raw)


def reranker_name(
    supersessions: SupersessionMap,
    reg_row: Mapping[str, Any],
    chunk_ref: str | None,
    sub_query: str | None,
    today: date,
) -> str:
    """The regulation name to SHOW the reranker for this chunk.

    An eligible old chunk shows its first replacement's title, because a name
    carrying «(القديم)» could be marked down even with no status line. Display
    only: never store this as the chunk's real title.
    """
    real_name = reg_row.get("clean_title") or reg_row.get("title") or ""
    if not old_chunk_eligible(supersessions, reg_row, chunk_ref, sub_query, today):
        return real_name
    pair = supersessions.old_side(_row_key(reg_row))
    return pair.replacements[0].title if pair.replacements else real_name


# -- Aggregator ---------------------------------------------------------------

_ALL_PHASES = (
    "Cite the `<provisions>` in prose, by the number they carry and their "
    "instrument if named (e.g. «البند (…) من …» or «المادة (…) من …»), never with "
    "`[n]`: `[n]` opens a chunk that does not contain them. If they do not "
    "address a point (for example matters already pending on the effective "
    "date), say so rather than guess. If no reference from the new law covers "
    "a point, say so in the gaps section and do not state what the new law "
    "provides."
)
_NAME_THE_LAW = (
    "Say which law the analysis is under in the opening sentences: the chat "
    "reply is written from excerpts of the synthesis, always including its opening."
)
_PHASE_INSTRUCTIONS = {
    UPCOMING: (
        "The new law is published but not yet in force. Answer under the old "
        "law and mention the new one as coming, with its date."
    ),
    PERIOD_1: (
        "Decide which law applies from the user's facts and the `<provisions>` on "
        "the references. If that settles it, answer under that law. Otherwise answer under "
        "the new law, then add a short section headed «إن كان النظام القديم هو المنطبق» "
        "(or the same wording for an old لائحة) saying what the old text provides on the "
        "question, cited with `[n]`, or that the references add nothing different. This "
        "section is required even if the question is abstract and the new law answers it "
        "fully. Naming the law in the opening, reciting provisions or citing old text in "
        f"passing does not replace it. {_NAME_THE_LAW}"
    ),
    PERIOD_2: (
        "State that the analysis is under the new law. Use the old law only for "
        "parts the `<provisions>` keep in force, or when the user says the "
        f"matter is old. {_NAME_THE_LAW}"
    ),
    # Reached only by an old chunk in a part still in force. The new law is plain
    # law by now, so nothing asks the answer to announce which law it is under.
    AFTER: (
        "This old text was replaced, but the `<provisions>` keep this part of it in "
        "force. Rely on it only within that part, and say that it remains in force."
    ),
}


def phase_instruction(current_phase: str) -> str:
    """The instruction text for a ``<transitions>`` entry in this phase."""
    return f"{_PHASE_INSTRUCTIONS[current_phase]} {_ALL_PHASES}"


#: Shown only when both versions of a law reach the aggregator, which is when an
#: old rule can be mistaken for the current one or a number for the wrong article.
_BOTH_VERSIONS = (
    "Both versions of this law are in your material, and they are separate texts. "
    "Name the version of every rule you state. While the analysis is under the new "
    "law, take each point from the new version. Present an old-version rule as "
    "current only if the `<provisions>` keep that part in force; otherwise describe "
    "it in the past tense as the old law's rule. Never carry a penalty, amount, "
    "period or remedy from one version to the other, even where the rules look alike. "
    "If no reference from the new version covers a point, say that the references do "
    "not show it. Do not suggest that the new version is silent on it or differs from "
    "the old one, or that the old rule continues. If the question uses a term or "
    "classification that only old-version references use, say in the opening sentences "
    "that it comes from the old version and answer in the new version's own terms; "
    "never present it as the new law's. An article number can mean different things in "
    "the two versions: if the question names a number, say in the opening sentences "
    "what it is in each version and which one the question means."
)
#: The static rule says context blocks are no basis for an answer, which would
#: leave an article fetched by number unusable and report it as missing.
_PINNED_ARTICLES = (
    "The `statute_articles` block is the law's own text, fetched by article number, "
    "so the general rule about context blocks does not apply to it: rely on it, cite "
    "it in prose by article number and version, never with `[n]`, and never report "
    "it as missing."
)


def both_versions_instruction(*, pinned_articles: bool) -> str:
    """The ``<both_versions>`` text; ``pinned_articles`` when a fetched block exists."""
    return f"{_BOTH_VERSIONS} {_PINNED_ARTICLES}" if pinned_articles else _BOTH_VERSIONS


def _format_date(day: date) -> str:
    # Slashes, not ISO: in Arabic text a hyphenated date renders back to front,
    # while a slash keeps the run in one piece (same shape as the corpus's هـ dates).
    return f"{day.year}/{day.month:02d}/{day.day:02d}م"


def version_title(title: str, version: str) -> str:
    """«{title} (القديم)» / «{title} (الجديد)», unless the title already says so.

    A replacement usually keeps the old name, so unmarked the two sides of a pair
    read as one law. Also the form a lookup re-call passes back verbatim.
    """
    if version_choice(title) == version:
        return title
    return f"{title} {_VERSION_MARKERS[version]}"


def _transition(pair: Pair, today: date) -> Transition:
    current = phase(pair, today)
    return Transition(
        phase=current,
        old_title=version_title(pair.old_title, VERSION_OLD),
        new_title=pair.new_title,
        in_force_on=_format_date(pair.superseded_on),
        instruction=phase_instruction(current),
        note=pair.note,
        new_titles=tuple(s.title for s in pair.replacements),
    )


def aggregator_status(
    supersessions: SupersessionMap,
    reg_row: Mapping[str, Any],
    today: date,
) -> AggregatorStatus:
    """The ``<status>``, ``<version>`` and ``<transitions>`` data for a reference.

    A row in a live transition gets no ``<status>``, so the static repeal rule
    in the aggregator prompt never fires for it. No pair gives today's line.
    """
    key = _row_key(reg_row)
    old_pair = supersessions.old_side(key)
    # After the second period a new law is plain law. A pending part has no end
    # date, so a block on the new side would otherwise ride on it for good.
    active = tuple(
        p for p in supersessions.for_regulation(key)
        if phase(p, today) != AFTER or (p == old_pair and p.pending_parts)
    )
    if active:
        return AggregatorStatus(
            version=VERSION_OLD if old_pair in active else VERSION_NEW,
            transitions=tuple(_transition(p, today) for p in active),
        )
    if old_pair is not None and old_pair.new_title:
        return AggregatorStatus(status=f"ملغي — حلّ محله {old_pair.new_title}")
    return AggregatorStatus(
        status=status_line(reg_row.get("status_class"), reg_row.get("status_raw"))
    )


def chunk_provisions(transitions: Iterable[Transition]) -> str:
    """The provisions a chunk of this row carries, word for word; ``""`` for none.

    The one rule for every agent that reads a chunk, so the text travels with the
    chunk and no agent reads it without them. Absent in ``upcoming``, when the old
    law simply applies. A row in several pairs joins their distinct notes.
    """
    notes = dict.fromkeys(t.note for t in transitions if t.note and t.phase != UPCOMING)
    return "\n\n".join(notes)


def group_by_note(transitions: Iterable[Transition]) -> dict[str, tuple[Transition, ...]]:
    """Distinct transitions keyed by note, in first-seen order.

    A law and its لائحة share one note, and every reference repeats its
    transition, so the block renders one entry per key.
    """
    grouped: dict[str, tuple[Transition, ...]] = {}
    for item in dict.fromkeys(transitions):
        grouped[item.note] = (*grouped.get(item.note, ()), item)
    return grouped


# -- Responder ----------------------------------------------------------------

#: For the chat writer, which reads the report's close, where the old-law section
#: sits. Sent only while the chat message carries a transition line.
RESPONDER_VERSION_RULE = (
    "This report covers a law in transition. Anything it attributes to the old "
    "version, including its section on the case where the old law applies, describes "
    "the old law only: never state such a rule as current, and if you mention it, name "
    "it as the old law's. Keep the report's attribution as it is: do not say the new "
    "law provides something the report takes from the old version or from another text."
)

# True whichever law the analysis landed on: the facts can put a matter under the
# old law, so a fixed line must never say the analysis IS under the new one.
# Gender-neutral on purpose, for a نظام and a لائحة alike.
_RESPONDER_LINES = {
    PERIOD_1: "بدأ نفاذ «{new}» في {date} بدلًا من «{old}»، والتقرير يبيّن أيهما ينطبق على حالتك.",
    PERIOD_2: (
        "بدأ نفاذ «{new}» في {date} بدلًا من «{old}»، والتحليل مبني على النسخة "
        "الجديدة إلا ما يذكره التقرير صراحةً."
    ),
}


def responder_line(transitions: Iterable[Transition]) -> str:
    """The fixed sentence for the chat message, one per distinct note.

    ``""`` when there is nothing to say: ``upcoming``, where the analysis is under
    the law the reader already expects, and ``after``, where the change is old news.
    """
    lines = []
    for group in group_by_note(transitions).values():
        first = group[0]
        template = _RESPONDER_LINES.get(first.phase)
        if template and first.new_title:
            lines.append(
                template.format(
                    new=first.new_title, date=first.in_force_on, old=first.old_title
                )
            )
    return "\n".join(lines)
