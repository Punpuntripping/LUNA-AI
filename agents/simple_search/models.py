"""Pure schemas for the ``simple_search`` family — the lookup agent.

``simple_search`` puts ONE legal object in front of the user (plan
``.claude/plans/simple_search_family.md``). This module holds the vocabulary the
whole family agrees on: the **six entry levels** (§4), the **resolved object**
the searcher hands the synthesizer (§2.1.6), the **ladder decision** (§5), and
the **unfold result** the synthesizer is prompted with (§2.2).

This module is **pure** — it imports only ``pydantic`` and the stdlib, never
``pydantic_ai``, never ``supabase``, never any executor package. That is the
same discipline documented at
:mod:`agents.deep_search_v4.planner.models`, and it exists for the same reason:
the ladder and the unfold renderers are ordinary Python with measurable
behaviour, so their test suite must run without the agent runtime or a live DB.

Level ≠ Layer ≠ Tier. A *level* here is the **kind of legal object** being
opened (six of them). It is unrelated to the architectural Layer 1–4 and to the
model cost ``tier_1``/``tier_2`` — see ``CLAUDE.md`` § "Layer vs Tier".
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


# --------------------------------------------------------------------------- #
# §4 — the six entry levels.
# --------------------------------------------------------------------------- #

# A ``Literal`` rather than an ``enum.Enum``: it is the house shape for a closed
# vocabulary on a pydantic field (``Mode`` in planner/models.py:54, ``Tier`` in
# utils/agent_models.py), it serialises as a plain string with no ``.value``
# dance at every call site, and an unknown value fails validation loudly.
SimpleSearchLevel = Literal[
    "chunk",            # L1 — one chunks_v2 row (a section of a regulation)
    "regulation_doc",   # L2 — a whole regulations_v2 document
    "article",          # L3 — one articles_v2 row (مادة)
    "judgment",         # L4 — one cases row (حكم)
    "circular",         # L5 — one circulars row (تعميم)
    "service",          # L6 — one services row (خدمة حكومية)
]

# Iteration order = plan §4 order (L1…L6). Keep the two in sync; the dispatch
# registry in ``unfold.py`` is asserted exhaustive against this tuple.
SIMPLE_SEARCH_LEVELS: tuple[SimpleSearchLevel, ...] = (
    "chunk",
    "regulation_doc",
    "article",
    "judgment",
    "circular",
    "service",
)

# Arabic display noun per level — used in unfold headers and error strings.
LEVEL_LABEL_AR: dict[SimpleSearchLevel, str] = {
    "chunk": "مقطع نظامي",
    "regulation_doc": "نظام",
    "article": "مادة",
    "judgment": "حكم",
    "circular": "تعميم",
    "service": "خدمة",
}


# --------------------------------------------------------------------------- #
# §6.1a — the wire contract. PINNED: every layer must match this byte for byte.
# --------------------------------------------------------------------------- #
#
# The plan pins these names because four different hands write them (the SQL
# CHECK, two Python Literals, the TS union) and they agree only if they agree
# here first. This module is NOT the SSoT — plan §6.1a is — but the resolver
# needs the mapping to build a ``ref_id``, and one table beats six scattered
# f-strings. If §6.1a ever moves, these three dicts move with it.
#
# ``regulation_docs``, NOT ``regulations`` — that name is taken and means a
# CHUNK (§6.2: ``domain='regulations'`` hard-assumes a ``chunks_v2.id``, and a
# regulations_v2 uuid inserted under it renders a dead stub with zero errors).

LEVEL_DOMAIN: dict[SimpleSearchLevel, str] = {
    "chunk": "regulations",
    "regulation_doc": "regulation_docs",
    "article": "articles",
    "judgment": "cases",
    "circular": "circulars",
    "service": "compliance",
}

# ``ref_id`` prefix per level. Distinct prefixes AND distinct domains — §6.2.
LEVEL_REF_PREFIX: dict[SimpleSearchLevel, str] = {
    "chunk": "reg",
    "regulation_doc": "regdoc",
    "article": "article",
    "judgment": "case",
    "circular": "circular",
    "service": "service",
}

# ``Reference.source_type`` per level. ``article_full`` / ``regulation_summary``
# are the two NEW types (§6.1); ``article`` and ``regulation`` are already taken
# by the legacy SourceView union and reusing them fails at import time (§9 trap 2).
LEVEL_SOURCE_TYPE: dict[SimpleSearchLevel, str] = {
    "chunk": "chunk",
    "regulation_doc": "regulation_summary",
    "article": "article_full",
    "judgment": "case",
    "circular": "circular",
    # ``gov_service`` — NOT ``compliance``. ``compliance`` is the DOMAIN value;
    # the ``Reference.source_type`` Literal (``aggregator/models.py:36-52``) has
    # no ``compliance`` member, so every service reference built from this table
    # failed validation at construction. Caught during the wave-2 build.
    "service": "gov_service",
}


# --------------------------------------------------------------------------- #
# The resolved object — the searcher's product (§2.1.6).
# --------------------------------------------------------------------------- #

# Which identity field is load-bearing for each level. ``missing_id`` reads this
# so a half-resolved object is caught by code rather than by an empty unfold.
_LEVEL_PRIMARY_ID_FIELD: dict[SimpleSearchLevel, str] = {
    "chunk": "chunk_id",
    "regulation_doc": "regulation_id",
    "article": "article_id",
    "judgment": "case_id",
    "circular": "circular_id",
    "service": "service_id",
}


class ResolvedObject(BaseModel):
    """ONE legal object, identified but **not yet unfolded**.

    The searcher hands this to the synthesizer: ids + level + the display fields
    it matched on — never the body (§2.1.6 "hand off identity, not content").
    Every id field is a plain ``str`` defaulting to ``""`` rather than
    ``str | None``, so call sites never branch on None before a truthiness test.

    ``article`` is the one level with two viable identities: ``article_id``
    (resolved straight off ``articles_v2``) or the
    ``(regulation_id, article_number)`` pair that ``fetch_article`` resolves.
    :meth:`missing_id` accepts either.
    """

    level: SimpleSearchLevel

    # -- identity -----------------------------------------------------------
    regulation_id: str = ""   # regulations_v2.id — L2, and the parent for L1/L3
    chunk_id: str = ""        # chunks_v2.id — L1
    article_id: str = ""      # articles_v2.id — L3
    article_number: str = ""  # articles_v2.article_number (TEXT: "81", "1-1")
    case_id: str = ""         # cases.id — L4
    case_ref: str = ""        # cases.case_ref — the key `case:<ref>` refs use
    circular_id: str = ""     # circulars.id — L5
    service_id: str = ""      # services.id — L6

    # -- display (what the USER saw, §2.3.1) --------------------------------
    title: str = ""       # the card's own label
    subtitle: str = ""    # parent regulation / court / issuing entity
    doc_type: str = ""    # regulations_v2.doc_type_raw — the user-visible chip
    source_url: str = ""  # landing_url / details_url / source / service_url

    def primary_id(self) -> str:
        """The id this level is opened by (``""`` when unresolved)."""
        return str(getattr(self, _LEVEL_PRIMARY_ID_FIELD[self.level], "") or "")

    def missing_id(self) -> str:
        """Name of the required-but-empty id field, or ``""`` when resolvable.

        Deliberately a query, not a raise: the searcher resolves incrementally
        and a half-built object is a normal intermediate state. The *unfold*
        entry point is where an unresolvable object becomes an error.
        """
        if self.primary_id():
            return ""
        # L3 second identity: (regulation_id, article_number) is equally valid.
        if self.level == "article" and self.regulation_id and self.article_number:
            return ""
        # L4 second identity: ``case_ref`` alone resolves a ruling. It is the key
        # ``case:<ref>`` references have always carried, so a WI-ref attachment
        # (case C) arrives holding the ref and NOT the uuid — ``unfold_judgment``
        # queries on it directly. Without this leg the dispatcher rejects a
        # perfectly resolvable object before the fetch that would have worked.
        if self.level == "judgment" and self.case_ref:
            return ""
        return _LEVEL_PRIMARY_ID_FIELD[self.level]

    def domain(self) -> str:
        """``workspace_item_references.domain`` for this level (§6.1a)."""
        return LEVEL_DOMAIN[self.level]

    def source_type(self) -> str:
        """``Reference.source_type`` for this level (§6.1a)."""
        return LEVEL_SOURCE_TYPE[self.level]

    def ref_id(self) -> str:
        """``<prefix>:<id>`` for this level, or ``""`` when unresolved.

        Judgments key on ``case_ref`` (not ``cases.id``) because that is what
        ``case:`` refs have always carried — see ``ura/enrich._enrich_cases``.
        """
        prefix = LEVEL_REF_PREFIX[self.level]
        if self.level == "judgment":
            ident = self.case_ref or self.case_id
        else:
            ident = self.primary_id()
        return f"{prefix}:{ident}" if ident else ""

    def label_ar(self) -> str:
        """Arabic noun for this level («نظام», «مادة», …)."""
        return LEVEL_LABEL_AR[self.level]


# --------------------------------------------------------------------------- #
# §5 — the ladder decision.
# --------------------------------------------------------------------------- #

# ``rung`` values. 0 means the three-rung ladder does not apply: the level has
# no summary alternative to fall back to (a single article / ruling / circular
# body has no `summary` twin the way a multi-chunk regulation does), so it is
# served verbatim and position-truncated at the content ceiling if it overruns.
RUNG_NOT_APPLICABLE = 0
RUNG_FULL_CONTENT = 1
RUNG_SUMMARIES = 2
RUNG_TRUNCATED_SUMMARIES = 3

LadderPayload = Literal["content", "summary", "none"]


class LadderDecision(BaseModel):
    """Which rung fires for one regulation, and the char slice each side gets.

    Produced by ``unfold.choose_rung`` from **row metadata only** (§5.4), before
    any body is materialised. ``body_budget_chars`` / ``appendix_budget_chars``
    are meaningful at rung 3 only; below that both sides are served whole and
    the two fields carry the measured sizes.
    """

    rung: int
    payload: LadderPayload = "content"

    # What was measured to get here — both tests measure body + appendixes
    # TOGETHER (§5.1). Kept split so a caller can log which side drove the flip.
    body_content_chars: int = 0
    appendix_content_chars: int = 0
    body_summary_chars: int = 0
    appendix_summary_chars: int = 0

    # Rung-3 slices after the §5.3 reservation + spillover.
    body_budget_chars: int = 0
    appendix_budget_chars: int = 0

    # Machine-readable reason token (telemetry, never user-facing).
    reason: str = ""

    # True when the content ladder ran on an ESTIMATE because the body was too
    # large to materialise (§5.4). The rung is still sound — see the guard-factor
    # note in ``unfold.py`` — but ``body_content_chars`` is approximate.
    content_estimated: bool = False

    @property
    def total_content_chars(self) -> int:
        return self.body_content_chars + self.appendix_content_chars

    @property
    def total_summary_chars(self) -> int:
        return self.body_summary_chars + self.appendix_summary_chars


# --------------------------------------------------------------------------- #
# The unfold result — the synthesizer's input (§2.2).
# --------------------------------------------------------------------------- #


class UnfoldSection(BaseModel):
    """Per-slice accounting for one rendered section.

    ``name`` is a machine token (``"body"``, ``"appendixes"``, ``"content"``,
    ``"intro"``, …), never Arabic — the Arabic heading lives in the rendered
    text. ``units`` are whole chunks / articles; a level with a single body
    reports ``units_total = 1``.
    """

    name: str
    units_total: int = 0
    units_kept: int = 0
    chars_total: int = 0
    chars_kept: int = 0

    @property
    def truncated(self) -> bool:
        return self.units_kept < self.units_total or self.chars_kept < self.chars_total


class UnfoldResult(BaseModel):
    """The agent-facing text for ONE resolved object, plus what it cost.

    ``text`` is Arabic markdown and is the ONLY field the synthesizer prompt
    interpolates. Everything else is telemetry: which rung fired, the measured
    token estimate, and exactly what was dropped — so a thin answer can be
    traced to a truncation rather than to the model.
    """

    level: SimpleSearchLevel
    text: str = ""

    rung: int = RUNG_NOT_APPLICABLE
    payload: LadderPayload = "content"

    chars: int = 0
    estimated_tokens: int = 0

    sections: list[UnfoldSection] = Field(default_factory=list)

    # Machine tokens (e.g. ``"content_over_ceiling"``, ``"row_not_found"``).
    # English by design: telemetry, not a user surface.
    notes: list[str] = Field(default_factory=list)

    # False when the object could not be read at all — ``text`` then carries a
    # short Arabic explanation the synthesizer can relay.
    ok: bool = True

    @property
    def truncated(self) -> bool:
        """True when ANY section dropped units or chars."""
        return any(s.truncated for s in self.sections)

    def truncated_sections(self) -> list[str]:
        """Names of the sections that lost content."""
        return [s.name for s in self.sections if s.truncated]

    def section(self, name: str) -> UnfoldSection | None:
        """The named section's accounting, or None."""
        for s in self.sections:
            if s.name == name:
                return s
        return None


__all__ = [
    "SimpleSearchLevel",
    "SIMPLE_SEARCH_LEVELS",
    "LEVEL_LABEL_AR",
    "LEVEL_DOMAIN",
    "LEVEL_REF_PREFIX",
    "LEVEL_SOURCE_TYPE",
    "ResolvedObject",
    "LadderDecision",
    "LadderPayload",
    "UnfoldSection",
    "UnfoldResult",
    "RUNG_NOT_APPLICABLE",
    "RUNG_FULL_CONTENT",
    "RUNG_SUMMARIES",
    "RUNG_TRUNCATED_SUMMARIES",
]
