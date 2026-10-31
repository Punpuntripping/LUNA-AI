"""regulation_versions — which version of a replaced law a lookup means.

``shared.library.supersession`` knows what a replaced law means during its
transition. This module applies that to the deterministic lookups, the planner's
``fetch_article`` and ``simple_search``'s resolvers, so both read a title and an
article number the same way.

Three things a plain lookup gets wrong for a law with a pair:

* **A title can carry a version.** «نظام الواحات القديم» matches no row, however
  the title happens to be stored or indexed. :func:`resolve` reads the marker,
  resolves the base title, then picks the side from the pair.
* **One number, two rules.** Numbering differs by design between an old law and
  its replacement, so «المادة 46» returned from one version alone reads like a
  right answer and may be an unrelated rule.
* **A number can exist on one side only.** Old لوائح use compound numbers
  (``1-11``) and new ones plain numbers, so this is the normal case there.

A row with no pair never reaches any of this: :func:`resolve` hands back exactly
what ``resolve_regulation_id`` returned and ``versions`` stays empty, so the
caller takes today's path. Nothing here names a law, a ref or a date.

Depends on ``fetch_article``, which is the lower layer: ``fetch_article`` imports
this module inside its functions, never at module level.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Iterable

from agents.tool_repository.fetch_article import (
    RegCandidate,
    ResolveResult,
    _build_ambiguous,
    article_number_keys,
    resolve_regulation_id,
)
from shared.library.supersession import (
    AFTER,
    PERIOD_1,
    PERIOD_2,
    PROVISIONS_HEADING,
    UPCOMING,
    VERSION_NEW,
    VERSION_OLD,
    Pair,
    SupersessionMap,
    Transition,
    aggregator_status,
    load_supersessions,
    phase,
    strip_version_marker,
    version_choice,
    version_title,
)

__all__ = [
    "ArticleHit",
    "ArticlePick",
    "OpenedArticle",
    "Version",
    "VersionedTitle",
    "article_hits",
    "in_surviving_part",
    "open_article",
    "opened_section",
    "pick_articles",
    "resolve",
    "version_question",
    "version_title",
]

logger = logging.getLogger(__name__)

_ARTICLES_TABLE = "articles_v2"
_CHUNKS_TABLE = "chunks_v2"
_ARTICLE_COLUMNS = "id,article_number,content,chunk_parent_id"
_CHUNK_COLUMNS = "id,content"

#: How much of an article body identifies it inside a chunk, for an old row with
#: no ``chunk_parent_id``. Measured live on the one such law (273 articles, 22
#: chunks): one chunk for 271, two ambiguous, none missed. A false match only
#: returns both versions, which is harmless.
_PROBE_CHARS = 40
# The article's own number on its opening line («٤/٣٣»), which is not part of the
# body. A chunk can end on the number and the next open on the body, so a probe
# that includes it finds neither (5 of those 273 articles, one of them surviving).
_LEADING_NUMBER_RE = re.compile(r"^\s*\d+(?:\s*[/\-]\s*\d+)*\s*")

#: Gender-neutral on purpose: the same sentence serves a نظام and a لائحة. Titles
#: stay in guillemets because «لـ» and «بـ» can't join one that opens with «ال».
_STATUS_BY_PHASE = {
    UPCOMING: "يبدأ نفاذ «{new}» في {date}، ويستمر العمل بـ«{old}» حتى ذلك التاريخ.",
    PERIOD_1: (
        "بدأ نفاذ «{new}» في {date} وهو نافذ الآن، وما زالت الفترة الانتقالية لـ«{old}» قائمة، "
        "فحدِّد أيهما ينطبق من وقائع المسألة والأحكام الانتقالية إن وُجدت."
    ),
    PERIOD_2: (
        "بدأ نفاذ «{new}» في {date} وهو نافذ الآن بدلًا من «{old}»، فلا يُرجع إلى النسخة القديمة "
        "إلا فيما أبقته الأحكام الانتقالية أو في مسألة قديمة."
    ),
}
# `after` only has a status sentence with pending parts, which is period 2's position.
_STATUS_BY_PHASE[AFTER] = _STATUS_BY_PHASE[PERIOD_2]

_CARRY_ONE = "انقل النص إلى planner_brief بعنوانه هذا."
_CARRY_BOTH = "انقل النصين كليهما إلى planner_brief، كلٌّ تحت عنوانه، ولا تدمج بينهما."
_NAME_THE_VERSION = "اذكر في جوابك أي نسخة هذه."
_ASK_ONCE = (
    "\n\nللاسم نسختان في فترة انتقالية، ولكلٍّ منهما أحكامها. إن كان المستخدم قد "
    "حدّد النسخة في هذه المحادثة فأعد النداء بها مباشرةً ولا تسأله مرة أخرى؛ وإلا "
    "فاسأله مخاطبًا إياه: «أيَّ النسختين تقصد؟»، واذكر الاسمين في سؤالك."
)


#: ``fetch_article_identity``'s miss shape.
_NO_ARTICLE = {"id": "", "article_number": ""}


# -- Which rows a title means -------------------------------------------------


@dataclass(frozen=True)
class Version:
    """One row of a transition, and the pair it sits in."""

    reg_id: str
    title: str
    side: str  # VERSION_OLD | VERSION_NEW
    pair: Pair

    @property
    def label(self) -> str:
        return version_title(self.title, self.side)


@dataclass(frozen=True)
class VersionedTitle:
    """A resolved title, plus the versions behind it when it sits in a pair.

    ``resolved`` is the same shape ``resolve_regulation_id`` returns, its row
    moved to the side the title means. ``choice`` is the explicit marker, if any.
    ``versions`` lists the new side first and is empty for any other regulation,
    which is the caller's signal to take today's path.
    """

    resolved: ResolveResult
    choice: str | None = None
    versions: tuple[Version, ...] = ()

    @property
    def label(self) -> str:
        """The name to show: the version's label, or the resolved title."""
        for version in self.versions:
            if version.reg_id == self.resolved.reg_id:
                return version.label
        return self.resolved.display


def _versions_of(supersessions: SupersessionMap, reg_id: Any) -> tuple[Version, ...]:
    """Every version a row can mean, new first. Empty for a row with no pair.

    From the old side that is all its replacements (a split). From the new side
    it is this row and every old law it replaces (a merge), never the other
    replacements of a split: those are different laws.
    """
    if not reg_id:
        return ()
    key = str(reg_id)
    retiring = supersessions.old_side(key)
    pairs = (retiring,) if retiring else supersessions.for_regulation(key)
    new: dict[str, Version] = {}
    old: dict[str, Version] = {}
    for pair in pairs:
        for successor in pair.replacements:
            if successor.reg_id and (retiring or successor.reg_id == key):
                new.setdefault(
                    successor.reg_id,
                    Version(successor.reg_id, successor.title, VERSION_NEW, pair),
                )
        old.setdefault(pair.old_id, Version(pair.old_id, pair.old_title, VERSION_OLD, pair))
    return (*new.values(), *old.values())


def _tied_versions(
    supersessions: SupersessionMap, tied: ResolveResult
) -> tuple[Version, ...]:
    """The versions behind a tie of exact pins, if the tie is one pair and nothing else.

    A replaced law usually keeps its title, so both rows can pin it. A pinned row
    that is no version of the others means the title really is ambiguous, and the
    shortlist stays the planner's.
    """
    pins = [c for c in tied.shortlist if c.exact]
    if len(pins) < 2 or len(pins) != len(tied.shortlist):
        return ()
    tied_ids = {c.reg_id for c in pins}
    for pin in pins:
        versions = _versions_of(supersessions, pin.reg_id)
        if tied_ids <= {v.reg_id for v in versions}:
            return versions
    return ()


def _pinned_versions(
    supersessions: SupersessionMap, result: ResolveResult
) -> tuple[Version, ...]:
    """The versions a resolve result pins: its one row, or a tie between versions."""
    return _versions_of(supersessions, result.reg_id) or _tied_versions(supersessions, result)


def _on_side(
    versions: tuple[Version, ...], choice: str | None, today: date
) -> VersionedTitle:
    # Before the new law is in force, a plain title means the law in force.
    default = VERSION_OLD if phase(versions[0].pair, today) == UPCOMING else VERSION_NEW
    wanted = choice or default
    chosen = next((v for v in versions if v.side == wanted), versions[0])
    return VersionedTitle(
        resolved=ResolveResult(reg_id=chosen.reg_id, display=chosen.title, exact=True),
        choice=choice,
        versions=versions,
    )


def resolve(supabase, title: str, today: date) -> VersionedTitle:
    """Resolve a title, honouring an explicit old/new marker.

    The title is resolved as typed first, so an ordinary law costs no extra
    round trip and comes back untouched. A pair is looked for only from a pin:
    the shortlist stays the planner's to choose from. Both rows of a pair can pin
    one title, and that tie counts as the pin.

    The marker is stripped only when the typed title didn't pin. Some real titles
    contain these words and are not versions at all («… الأصناف النباتية
    الجديدة», «… الموظف الجديد»), so a stripped title is trusted only if it pins
    a row with a pair. Otherwise the typed result stands.
    """
    supersessions = load_supersessions(supabase)
    typed = resolve_regulation_id(supabase, title)
    choice = version_choice(title)

    versions = _pinned_versions(supersessions, typed)
    if versions:
        return _on_side(versions, choice, today)

    base = strip_version_marker(title)
    if typed.reg_id or base == " ".join((title or "").split()):
        return VersionedTitle(typed)

    versions = _pinned_versions(supersessions, resolve_regulation_id(supabase, base))
    if versions:
        return _on_side(versions, choice, today)
    return VersionedTitle(typed)


def version_question(versioned: VersionedTitle, today: date) -> str:
    """The ``AMBIGUOUS:`` payload for ``simple_search``, or ``""`` for no question.

    Period 1 only, with no marker and both sides in the corpus. ``_build_ambiguous``
    so the searcher's existing rule for that prefix calls ``ask_user``. Once per
    conversation needs no storage: the searcher reads its recent messages and
    re-calls with the marked title, which resolves without coming back here.
    """
    versions = versioned.versions
    if not versions or versioned.choice or phase(versions[0].pair, today) != PERIOD_1:
        return ""
    if len({v.side for v in versions}) < 2:
        return ""
    candidates = [
        RegCandidate(reg_id=v.reg_id, display=v.label, score=0.0, exact=False)
        for v in versions
    ]
    return _build_ambiguous(candidates) + _ASK_ONCE


# -- Transition wording -------------------------------------------------------


def _status_sentence(item: Transition) -> str:
    return _STATUS_BY_PHASE[item.phase].format(
        new=version_title(item.new_title, VERSION_NEW),
        old=version_title(item.old_title, VERSION_OLD),
        date=item.in_force_on,
    )


def _transition_state(
    supabase, reg_ids: Iterable[str], today: date
) -> tuple[str, str]:
    """``(status sentences, transitional provisions)`` for these rows.

    Both empty for a row with nothing to say: a new law once nothing is pending.
    The provisions stay out of ``upcoming``, when the old law simply applies.
    Deduped, since two rows of one pair repeat the same transition.
    """
    supersessions = load_supersessions(supabase)
    sentences: dict[str, None] = {}
    provisions: dict[str, None] = {}
    for reg_id in reg_ids:
        view = aggregator_status(supersessions, {"id": reg_id}, today)
        if view.status:
            sentences[f"{view.status}."] = None
        for item in view.transitions:
            if item.new_title:
                sentences[_status_sentence(item)] = None
            if item.note and item.phase != UPCOMING:
                provisions[item.note] = None
    return " ".join(sentences), "\n\n".join(provisions)


def opened_section(supabase, reg_id: Any, today: date) -> list[str]:
    """The lines to add to a document opened from a row in a pair.

    ``[]`` for every other regulation, and for a new law once nothing is pending,
    so a plain document renders byte-identically. May read the database when the
    cache has expired: callers on the event loop wrap it in ``to_thread``.
    """
    versions = _versions_of(load_supersessions(supabase), reg_id)
    version = next((v for v in versions if v.reg_id == str(reg_id)), None)
    if version is None:
        return []
    status, provisions = _transition_state(supabase, (version.reg_id,), today)
    if not status:
        return []
    lines = [
        "## حالة هذه النسخة",
        "",
        f"**النسخة:** {version.label}",
        "",
        f"{status} {_NAME_THE_VERSION}",
        "",
    ]
    if provisions:
        lines += [f"## {PROVISIONS_HEADING}", "", provisions, ""]
    return lines


# -- Which versions of an article ---------------------------------------------


@dataclass(frozen=True)
class ArticleHit:
    """One version's row for an article number."""

    version: Version
    article: dict[str, Any]


@dataclass(frozen=True)
class ArticlePick:
    """What ``fetch_article`` shows: the rows, and the note for ``text`` only.

    ``labelled`` is False when the result should read like any other law's.
    ``hits`` empty means the number is in no version.
    """

    hits: tuple[ArticleHit, ...] = ()
    note: str = ""
    labelled: bool = True


def _find_article(supabase, regulation_id: str, number: str) -> dict[str, Any]:
    """One ``articles_v2`` row by exact-text number, ``{}`` on a miss. Never raises.

    ``_fetch_article_content`` selects the body alone, and the surviving-part
    check also needs ``chunk_parent_id``.
    """
    for key in article_number_keys(number):
        try:
            resp = (
                supabase.table(_ARTICLES_TABLE)
                .select(_ARTICLE_COLUMNS)
                .eq("regulation_id", regulation_id)
                .eq("article_number", key)
                .limit(1)
                .execute()
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "regulation_versions: article fetch failed reg=%s art=%r: %s",
                regulation_id, key, exc,
            )
            return {}
        rows = getattr(resp, "data", None) or []
        if rows and rows[0].get("content"):
            return rows[0]
    return {}


def article_hits(
    supabase, versioned: VersionedTitle, number: str
) -> tuple[ArticleHit, ...]:
    """The versions holding this number, new first."""
    found = ((v, _find_article(supabase, v.reg_id, number)) for v in versioned.versions)
    return tuple(ArticleHit(v, row) for v, row in found if row)


def in_surviving_part(supabase, pair: Pair, article: dict[str, Any]) -> bool:
    """True when an old article sits in a chunk of a part still in force.

    ``chunk_parent_id`` when the row has one, otherwise the body's opening found
    inside the surviving chunks, because not every old law carries the link. A
    failed read answers True: showing both versions is the harmless side, and
    answering False would hide law still in force.
    """
    refs = sorted(pair.surviving_chunks)
    if not refs:
        return False
    try:
        resp = (
            supabase.table(_CHUNKS_TABLE)
            .select(_CHUNK_COLUMNS)
            .in_("chunk_ref", refs)
            .execute()
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("regulation_versions: surviving chunks read failed: %s", exc)
        return True
    chunks = getattr(resp, "data", None) or []
    parent = str(article.get("chunk_parent_id") or "")
    if parent:
        return any(str(c.get("id")) == parent for c in chunks)
    body = _LEADING_NUMBER_RE.sub("", article.get("content") or "", count=1)
    probe = body.strip()[:_PROBE_CHARS]
    return bool(probe) and any(probe in (c.get("content") or "") for c in chunks)


def _pick(
    supabase,
    hits: tuple[ArticleHit, ...],
    today: date,
    *,
    lead: str = "",
    labelled: bool = True,
) -> ArticlePick:
    status, provisions = _transition_state(
        supabase, [h.version.reg_id for h in hits], today
    )
    carry = _CARRY_BOTH if len(hits) > 1 else _CARRY_ONE
    note = "> ⚠ " + " ".join(s for s in (lead, status, carry) if s)
    if provisions:
        note += f"\n\n### {PROVISIONS_HEADING}\n\n{provisions}"
    return ArticlePick(hits=hits, note=note, labelled=labelled)


def _numbering_lead(
    versioned: VersionedTitle, hits: tuple[ArticleHit, ...], number: str
) -> str:
    """The note for a number in one version only; ``""`` if there is no other."""
    shown = {h.version.reg_id for h in hits}
    others = [f"«{v.label}»" for v in versioned.versions if v.reg_id not in shown]
    if not others:
        return ""
    found = "، ".join(f"«{h.version.label}»" for h in hits)
    return (
        f"الترقيم يختلف بين النسختين: المادة {number} واردة في {found} فقط، "
        f"ولا مقابل لها بهذا الرقم في {' ولا في '.join(others)}."
    )


def pick_articles(
    supabase, versioned: VersionedTitle, number: str, today: date
) -> ArticlePick:
    """The ``fetch_article`` table: which versions of one number to show.

    =====================  ==============  ================  ==================
    Case                   Period 1        Period 2          After
    =====================  ==============  ================  ==================
    Title names the old    Old             Old               Old
    Plain, or names new    Both            New + warning     New
    Old part still alive   Both            Both              Both, until the
                                                             entry is removed
    In one version only    That one, noting the numbering differs, in every phase
    In neither             Not found       Not found         Not found
    =====================  ==============  ================  ==================

    ``upcoming`` behaves as for a law with no pair, returning the old law as
    current, and only adds the date. The number-in-one-version row comes first
    because nothing else can be shown, whatever the title asked for.
    """
    hits = article_hits(supabase, versioned, number)
    if not hits:
        return ArticlePick()

    new = tuple(h for h in hits if h.version.side == VERSION_NEW)
    old = tuple(h for h in hits if h.version.side == VERSION_OLD)
    if not new or not old:
        return _pick(supabase, hits, today, lead=_numbering_lead(versioned, hits, number))

    if versioned.choice == VERSION_OLD:
        return _pick(supabase, old, today)

    current = phase(old[0].version.pair, today)
    if current == UPCOMING:
        if versioned.choice == VERSION_NEW:
            return _pick(supabase, new, today)
        return _pick(supabase, old, today, labelled=False)

    alive = tuple(
        h for h in old
        if phase(h.version.pair, today) == PERIOD_1
        or in_surviving_part(supabase, h.version.pair, h.article)
    )
    if alive:
        lead = f"الرقم {number} وارد في النسختين بنصين مستقلين."
        if current != PERIOD_1:
            lead += (
                " ويظهر النص القديم لأن موضعه يضم جزءًا ما زال ساريًا؛ "
                "راجع موضوعه قبل الاعتماد عليه."
            )
        return _pick(supabase, (*new, *alive), today, lead=lead)

    if current == AFTER:
        return ArticlePick(hits=new, labelled=False)

    return _pick(
        supabase, new, today,
        lead=(
            f"الترقيم تغيّر بين النسختين، فالرقم {number} قد يكون حكمًا آخر في النسخة "
            f"القديمة؛ إن كانت المسألة قديمة فأعد النداء باسم «{old[0].version.label}»."
        ),
    )


# -- What simple_search opens -------------------------------------------------


@dataclass(frozen=True)
class OpenedArticle:
    """The article a ``resolve_article`` call opens, or the question to ask first.

    ``identity`` has the shape of ``fetch_article_identity``: identity, never
    content, whatever the lookup read along the way.
    """

    question: str = ""
    resolved: ResolveResult = field(default_factory=ResolveResult)
    label: str = ""
    identity: dict[str, str] = field(default_factory=dict)
    note: str = ""


def open_article(
    supabase, versioned: VersionedTitle, number: str, today: date
) -> OpenedArticle:
    """Which version ``simple_search`` opens for this number.

    Asks in period 1 only when the number is in both versions. Otherwise it opens
    the side the title resolved to, or the only side holding the number. There is
    no surviving-part check: the owner chose not to ask after period 1, and the
    note on the opened document is the safeguard.
    """
    hits = article_hits(supabase, versioned, number)
    if len({h.version.side for h in hits}) == 2:
        question = version_question(versioned, today)
        if question:
            return OpenedArticle(question=question)

    resolved = versioned.resolved
    chosen = next(
        (h for h in hits if h.version.reg_id == resolved.reg_id),
        hits[0] if hits else None,
    )
    if chosen is None:
        # In neither version's articles. The resolved row stays the target: its
        # chunks' `owns` map is the opening step's fallback.
        return OpenedArticle(
            resolved=resolved, label=versioned.label, identity=dict(_NO_ARTICLE)
        )

    version = chosen.version
    lead = _numbering_lead(versioned, hits, number) if len(hits) == 1 else ""
    return OpenedArticle(
        resolved=ResolveResult(reg_id=version.reg_id, display=version.title, exact=True),
        label=version.label,
        identity={
            "id": str(chosen.article.get("id") or ""),
            "article_number": str(chosen.article.get("article_number") or ""),
        },
        note=f" ({lead})" if lead else "",
    )
