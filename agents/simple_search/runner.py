"""The ``simple_search`` turn runner — the 3-cycle loop, the fan-out, the pool.

Implements the pinned entry point of plan §12a C1. The orchestrator's dispatch
branch codes against :func:`run_simple_search` and
:class:`SimpleSearchRunResult` exactly as written there; this module is the
other half of that contract.

Three things it enforces, and they are the three the plan locks:

**D3 — 3 cycles, per turn, SHARED pool.** One counter, turn-wide. A cycle is one
*searcher* round plus the synthesizers it feeds. Two synthesizers rejecting in
the same round consume ONE cycle between them, not one each: they loop back
together into a single fresh searcher round. The budget is therefore at most 3
searcher runs per turn no matter how the fan-out splits — a per-synthesizer
budget would be 3 × the fan-out, which is exactly what "shared pool" rules out.
A loop-back always builds a **brand new** synthesizer agent, with no memory of
the object it rejected.

**D4/D5 — fan-out is 3 DOCUMENTS, one synthesizer each.** The unit is the
document, not the citation: N articles of ONE نظام are one document and reach
ONE synthesizer, as N unfolds answered together. Two articles of two different
أنظمة are two documents and two synthesizers, each writing its own chat reply
and its own workspace item.

**§2.3 — the searcher ALWAYS runs.** An earlier cut had an attached library page
skip it and be synthesized directly. That was wrong: attaching a page is
CONTEXT, not a routing decision, so a user could carry in نظام العمل, ask about
a مادة in نظام التنفيذ, and be answered about the wrong document — repeatedly,
since the carried item persists as a workspace item. The pre-resolved identity
is now a *candidate handle* the searcher may select, saving a lookup rather than
imposing an answer.

**D12 — the ruling unlock is charged HERE.** ``unfold.py`` refuses to render
``cases.content`` without a :class:`~agents.simple_search.unfold.JudgmentAccess`
verdict; this module is where that verdict comes from, because the charge lives
in ``backend/`` and the retrieval core must stay free of it (§11a). One resolver
is built per turn and threaded down as a **required** argument, so a forgotten
wiring is a ``TypeError`` rather than a free ruling. Regulations, articles,
chunks, circulars and services are NOT metered.

Both agents see the conversation window (``recent_messages``), loaded by the
same ``orchestrator._load_recent_messages`` the planners use — which also
carries the provenance tags and the masking codec. The router does not
paraphrase, so «واللي بعدها؟» arrives with its referent nowhere else.

**§13l — what the money lane changed.** Four rules this module now enforces,
each measured as broken first (`agents_reports/simple_search_adv_money_state.md`):

* **Deliver, then ask.** A pause returns the answers it already produced —
  they were charged for. The old branch returned an empty result and threw
  three billed rulings away.
* **A pause is resumable or it is not written.** Rows carry ``message_history``
  + a ``deferred_payload`` (tool_call_id, the turn's question, the ``C1…Cn``
  registry); :func:`resume_simple_search` consumes them. If the conversation's
  single pause slot is already taken, no row is written at all and the question
  is delivered as an ordinary message.
* **A refused ruling never reaches a synthesizer.** An LLM handed an empty body
  explains it as «غير موجود» — measured, 3/3 replies.
* **The spend is visible.** ``unlock_notes`` carries every ruling opened, and a
  charged turn says so in one Arabic line.

**The responder owns the turn** (``.claude/plans/simple_search_responder.md``).
Up to three synthesizers still run concurrently and blind to each other, but
none of them writes the bubble and none of them decides whether a card exists:
:func:`_finalise` collects every settled answer, hands the whole set to ONE
:mod:`agents.simple_search.responder` call as bounded digests, and **publishes
only what comes back carded** — the same gate ``should_publish`` puts in front
of ``publish_search_result`` (``orchestrator.py:3063``). Three consequences run
through this module:

* Nothing is written to ``workspace_items`` before that agent answers (trap
  §11.5), so the publish loop lives *after* the call, not before it.
* A ``card=False`` verdict moves a body into the bubble; it can never lose one
  (trap §11.4). Uncarded bodies are carried verbatim, by code, with their
  ``[n]`` markers stripped — the responder never retypes legal text (D4).
* **Failure publishes NOTHING** (D7) — the deliberate inverse of deep_search's
  ``_response_from_artifact`` (``planner/runner.py:140``), which publishes on
  responder failure because there the artifact is the product. Here the bubble
  carries the text, so a raised responder still delivers every body in full and
  the turn simply leaves no card.
"""
from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any, Awaitable, Callable

from agents.deep_search_v4.aggregator.models import Reference
from agents.models import WorkspaceItemSnapshot
from agents.paused_runs import PauseRecord, find_open_pause, record_pause
from agents.simple_search.models import (
    LEVEL_SOURCE_TYPE,
    ResolvedObject,
    ResponderDocDigest,
    SimpleSearchLevel,
    UnfoldResult,
)
from agents.simple_search.prompts import (
    build_responder_user_message,
    build_searcher_user_message,
    build_synthesizer_user_message,
)
from agents.simple_search.publisher import publish_simple_search_result
from agents.simple_search.responder import (
    RESPONDER_EXCERPT_CHARS,
    RESPONDER_LIMITS,
    RESPONDER_SLOT,
    ResponderDeps,
    ResponderOutput,
    create_responder_agent,
)
from agents.simple_search.searcher import (
    MAX_FANOUT_DOCUMENTS,
    SEARCHER_LIMITS,
    SEARCHER_SLOT,
    SearcherDecision,
    SearcherDeps,
    collect_case_c_candidates,
    create_searcher_agent,
    identity_key,
)
from agents.simple_search.synthesizer import (
    SYNTHESIZER_LIMITS,
    SYNTHESIZER_SLOT,
    SynthesizerDeps,
    SynthesizerOutput,
    create_synthesizer_agent,
)
from agents.simple_search.unfold import (
    JudgmentAccess,
    JudgmentAccessResolver,
    unfold,
)
from agents.utils.tracking import run_tracked, track_stage
from agents.utils.welcome import compose_opening, render_welcome_instruction

if TYPE_CHECKING:  # pragma: no cover — typing only
    from supabase import Client as SupabaseClient

    from agents.utils.welcome import WelcomeState

logger = logging.getLogger(__name__)

AGENT_FAMILY = "simple_search"

#: D3 — the retrieval budget for ONE turn, shared across every synthesizer.
MAX_CYCLES = 3

#: How long a searcher pause stays resumable. Mirrors the planner's window.
PAUSE_TTL_HOURS = 24

#: Reference hover snippet. Display only — the model grounds on the unfold.
_REF_SNIPPET_CHARS = 400

#: ``Reference.source_type`` per level. Derived from the §6.1a table in
#: Was a local correction for ``models.LEVEL_SOURCE_TYPE["service"]``, which
#: mapped to the DOMAIN value ``"compliance"`` instead of the ``source_type``
#: ``"gov_service"`` — every service reference failed validation at
#: construction. **Fixed at source in ``models.py``**; this override is now
#: empty and kept only as the seam (plus its regression test) in case another
#: level ever needs a source_type that differs from the table.
_SOURCE_TYPE_FIX: dict[str, str] = {}

#: ``[1]``, ``[1,3]``, ``[١,٣]`` — the citation form `_CITATION_RULES` mandates.
#: Mirrors ``postvalidator._CITATION_RE`` (Arabic comma included) so the writer
#: and the stripper agree on what a marker is.
_CITATION_MARKER_RE = re.compile(r"\[\s*[\d٠-٩]+(?:\s*[,،]\s*[\d٠-٩]+)*\s*\]")


def _strip_citation_markers(text: str) -> str:
    """Remove ``[n]`` markers from a body that ships in the bubble, uncarded.

    Applied to **every** body the responder declines to card, plus every body on
    the D7 failure path — those are the two ways a synthesis reaches the chat
    without a panel behind it. With a card the markers are load-bearing (`[n]`
    is the anchor into المراجع) and the body never comes through here at all,
    because a carded answer contributes nothing to the bubble (§9).

    The older note that this fired only when the synthesizer's own card flag was
    False died with that field: the synthesizer no longer rules on cards at all
    (responder plan §5), so it cites unconditionally and the marker strip became
    the designed hand-off step between two agents that each did their own job
    correctly, rather than a patch over one agent contradicting itself.

    Collapses the space a removed marker leaves behind so «النص [1] .» does not
    become «النص  .».
    """
    if not text:
        return text
    stripped = _CITATION_MARKER_RE.sub("", text)
    stripped = re.sub(r"[ \t]{2,}", " ", stripped)
    return re.sub(r"\s+([.،,؛:])", r"\1", stripped).strip()


_DEGRADED_AR = (
    "لم أتمكّن من تأكيد أن المصدر الذي عُثر عليه هو المطلوب بالضبط. "
    "وضّح لي اسم النظام أو رقم المادة أو المحكمة، وسأفتحه مباشرة."
)

#: The resume-only degradation: the reply pointed back at a document this turn
#: already answered and delivered before it paused, so there is nothing new to
#: write — and claiming the source could not be confirmed would be a lie about
#: work that succeeded.
_ALREADY_ANSWERED_AR = (
    "هذا هو المصدر نفسه الذي أجبتك عنه قبل قليل. "
    "إن كنت تقصد مصدرًا آخر، سمِّه لي وسأفتحه."
)


# =========================================================================== #
# D12 / §7.3 — the ruling unlock. THE money edge of this family.
# =========================================================================== #

#: ``library_unlocks.content_type`` for a ruling. **Only judgments are metered**
#: (D12); regulations, articles, chunks, circulars and services are not, and no
#: other level reaches this code.
_JUDGMENT_CONTENT_TYPE = "judgment"

#: ``library_unlocks.surface`` — analytics ONLY. ``resolve_access`` is explicit
#: that surface must never affect the charge, or the reference panel becomes the
#: metering bypass migration 104 closed. 'reference' because that is what the
#: user is opening: a source cited in their own workspace.
_UNLOCK_SURFACE = "reference"


def judgment_access_resolver(supabase, user_id: str) -> JudgmentAccessResolver:
    """The D12 charge, bound to one (client, user) — §11a says this may live here.

    ``resolve_access`` lives in ``backend/`` and this is a state-writing edge of
    the family, which §11a's working table puts on the *may import* side (the
    precedent is ``agent_search/publisher.py``, verbatim). What it must NOT do is
    reach into ``unfold.py``, so the import stays in this function and the core
    receives a plain callable.

    **The same unlock ``/judgments`` uses, by construction.** The key is
    ``(user_id, 'judgment', cases.id)``, and ``library_unlocks`` carries
    ``UNIQUE (user_id, content_type, content_id)`` — so a ruling the user already
    opened on the judgments page or through «عرض المصدر» resolves at step 4 of
    ``resolve_access`` to ``already_unlocked / charged=False`` and no second row
    can exist even in a race (the insert is ``ON CONFLICT DO NOTHING``). Measured
    on the live ledger: 17/17 judgment rows key on a ``cases.id``, across 9
    ``surface='reference'`` and 8 ``surface='library'``.

    ``user_id`` is a **users.user_id** — which is what the orchestrator carries
    (``messages.py`` maps ``auth_id`` through ``get_user_id`` before the stream
    starts). An empty one resolves to ``anonymous`` and refuses, which is the
    correct direction for a missing id.
    """

    async def _resolve(case_id: str) -> JudgmentAccess:
        try:
            # Lazy + local: keeps the module import-light and keeps the backend
            # dependency off every path that does not open a ruling.
            from backend.app.services.library_service import resolve_access

            decision = await resolve_access(
                supabase,
                user_id,
                _JUDGMENT_CONTENT_TYPE,
                case_id,
                surface=_UNLOCK_SURFACE,
            )
        except Exception as exc:  # noqa: BLE001
            # FAIL CLOSED. ``resolve_access`` already grants on a ledger WRITE
            # failure by policy (a DB blip must not paywall a paying customer);
            # reaching here means the decision itself could not be made, and
            # serving a PDPL-sensitive ruling on no decision is the bug this
            # whole change exists to remove.
            logger.error(
                "simple_search: judgment access resolution FAILED for case %s "
                "(user %s) — refusing the body: %s",
                case_id, user_id, exc, exc_info=True,
            )
            return JudgmentAccess(
                case_id=case_id, granted=False, reason="access_error"
            )
        return JudgmentAccess(
            case_id=case_id,
            granted=bool(decision.may_unlock),
            charged=bool(decision.charged),
            reason=str(decision.reason or ""),
        )

    return _resolve


# =========================================================================== #
# C1 — the pinned wire contract. Parallel lanes code against exactly this.
# =========================================================================== #


@dataclass
class SimpleSearchRunResult:
    chat_messages: list[str]           # one per synthesizer, in fan-out order
    created_item_ids: list[str]        # WIs published this turn (may be shorter)
    sse_events: list[dict]             # workspace_item_created, drained by _route
    paused: bool = False               # True when the searcher called ask_user
    question_text: str | None = None   # the ask_user question, when paused
    aborted: bool = False              # not a lookup → orchestrator hands off to deep_search
    #: Why it aborted (integrative comparison / application / out of scope).
    #: Additive to the §12a C1 pin — the orchestrator logs it on the hand-off so
    #: a turn that silently changed family is traceable afterwards.
    abort_reason: str = ""
    #: §13l — every judgment access this turn, in fan-out order. Surfaces the
    #: spend C1 could not express (§13j #4). ``charged=False`` ⇒ the user had
    #: already unlocked that ruling, so the turn opened it for free.
    #: **Grants only.** A REFUSED ruling is not an access the user paid for and
    #: would be indistinguishable here from an already-unlocked one; refusals
    #: are surfaced as their own Arabic chat message instead (§13l.5).
    unlock_notes: list[dict] = field(default_factory=list)  # {"case_id": str, "charged": bool}


def _empty(**over: Any) -> SimpleSearchRunResult:
    """A result with the three required lists empty (pause / abort / no-op)."""
    return SimpleSearchRunResult(
        chat_messages=[], created_item_ids=[], sse_events=[], **over
    )


# =========================================================================== #
# §13j #4 — the spend surface. The charge happens three layers down (`unfold`
# → the injected resolver); this is how it reaches the object that reaches the
# user.
# =========================================================================== #


@dataclass
class _UnlockRecord:
    """One ledger verdict, tagged with the fan-out slot that asked for it.

    ``order`` is assigned when the group is DISPATCHED, not when the ledger
    answers: groups are unfolded concurrently (``_run_round`` gathers them), so
    completion order is not fan-out order and the C1 pin says fan-out order.
    """

    order: int
    case_id: str
    granted: bool
    charged: bool


def _recording_judgment_access(
    resolver: JudgmentAccessResolver, records: list[_UnlockRecord], order: int
) -> JudgmentAccessResolver:
    """Wrap the D12 resolver so every verdict it gives is recorded.

    Records at the seam rather than reading the unfold's ``notes`` afterwards,
    because a REJECTED group's unfold never reaches ``_finalise`` — and a charge
    for a document the synthesizer then rejected is exactly the spend §13j #4
    says the user can never see (`unlock-01`: two rulings charged, one served).
    """

    async def _resolve(case_id: str) -> JudgmentAccess:
        access = await resolver(case_id)
        records.append(
            _UnlockRecord(
                order=order,
                case_id=str(access.case_id or case_id or ""),
                granted=bool(access.granted),
                charged=bool(access.charged),
            )
        )
        return access

    return _resolve


def unlock_notes_payload(records: list[_UnlockRecord]) -> list[dict]:
    """The C1 ``unlock_notes`` list: one entry per RULING, in fan-out order.

    Deduped by ``case_id`` — a loop-back re-unfolds the same ruling and asks the
    ledger a second time (free, `already_unlocked`), and two rows for one ruling
    would read as two opens. ``charged`` is OR-ed so the paid access wins over
    the free re-read.
    """
    payload: list[dict] = []
    index: dict[str, dict] = {}
    for rec in sorted(records, key=lambda r: r.order):
        if not rec.granted or not rec.case_id:
            continue
        seen = index.get(rec.case_id)
        if seen is not None:
            seen["charged"] = bool(seen["charged"] or rec.charged)
            continue
        note = {"case_id": rec.case_id, "charged": bool(rec.charged)}
        index[rec.case_id] = note
        payload.append(note)
    return payload


#: §13l.6 — ONE Arabic line naming how many rulings this turn opened from the
#: user's balance. Number agreement is not decoration: «فُتح 2 أحكام» is the
#: kind of sentence that makes the whole product look machine-translated.
def unlock_acknowledgement(charged_count: int) -> str:
    """The acknowledgment line for ``charged_count`` newly-opened rulings.

    Empty when nothing was charged — a turn that opened only already-unlocked
    rulings spent nothing and must not imply otherwise.
    """
    if charged_count <= 0:
        return ""
    if charged_count == 1:
        counted = "حكم واحد"
    elif charged_count == 2:
        counted = "حكمان"
    elif charged_count <= 10:
        # 3–10 take the plural (أحكام) with the counting noun in the opposite
        # gender — حكم is masculine, so the numeral carries the ـة.
        counted = f"{_ARABIC_COUNT_3_10.get(charged_count, str(charged_count))} أحكام"
    else:
        # 11+ takes the singular accusative تمييز. Unreachable while the D4
        # fan-out cap is 3, kept so the helper is total.
        counted = f"{charged_count} حكمًا"
    return f"فُتح {counted} من رصيد الفتح لديك."


_ARABIC_COUNT_3_10: dict[int, str] = {
    3: "ثلاثة", 4: "أربعة", 5: "خمسة", 6: "ستة",
    7: "سبعة", 8: "ثمانية", 9: "تسعة", 10: "عشرة",
}


# =========================================================================== #
# Grouping — the fan-out unit is the DOCUMENT (D5).
# =========================================================================== #


@dataclass
class _Group:
    """One document and every resolved object that belongs to it."""

    key: str
    level: SimpleSearchLevel
    objects: list[ResolvedObject] = field(default_factory=list)


def document_key(obj: ResolvedObject) -> str:
    """The document an object belongs to.

    Chunks and articles key on their **parent regulation** — that is what makes
    N articles of one نظام one document (D5). Everything else is its own
    document. Falls back to the object's own ref when the parent is unknown, so
    two parentless articles never collapse into one group.
    """
    if obj.level in ("chunk", "article", "regulation_doc") and obj.regulation_id:
        return f"reg:{obj.regulation_id}"
    # ``identity_key``, not ``ref_id()``: an object identified by something other
    # than its own PK (an article by number, a ruling by case_ref) has an empty
    # ref_id, and keying on that collapses distinct documents into one.
    return identity_key(obj)


def group_documents(objects: list[ResolvedObject]) -> list[_Group]:
    """Group resolved objects by document, preserving order, capped at D4's 3."""
    groups: dict[str, _Group] = {}
    for obj in objects:
        key = document_key(obj)
        grp = groups.get(key)
        if grp is None:
            if len(groups) >= MAX_FANOUT_DOCUMENTS:
                logger.info("simple_search: fan-out cap hit — dropping %s", key)
                continue
            grp = _Group(key=key, level=obj.level)
            groups[key] = grp
        grp.objects.append(obj)
    return list(groups.values())


# =========================================================================== #
# References — numbered in CODE, before the LLM runs (§6.4).
# =========================================================================== #


#: Levels whose ``source_url`` is a regulation landing page vs. the two that
#: carry their own link field on the card.
_LANDING_URL_LEVELS = ("chunk", "regulation_doc", "article")
#: Levels whose ``subtitle`` is an ENTITY (a court, an issuing body) rather than
#: a parent document — the panel prints those in its ``entity_name`` slot.
_ENTITY_LEVELS = ("judgment", "circular")


def build_references(group: _Group, unfolds: list[UnfoldResult]) -> list[Reference]:
    """One pre-numbered :class:`Reference` per object in the group.

    Numbers are 1-based **within the group**, because each group publishes its
    own workspace item and ``[n]`` is scoped to a card. The model only selects
    among these — it never mints a number (the central anti-hallucination
    mechanism, ``aggregator/models.py:29-33``).

    **The card is labelled with the card's own strings** — ``obj.title`` /
    ``obj.subtitle`` / ``obj.doc_type`` / ``obj.source_url``, all filled by
    ``searcher._enrich_identities`` from the backing row. Two rules the previous
    version broke:

    * ``title`` is a label, never a body. It used to be the 500-char manifest
      PREVIEW line, chip prefix and «## الملخص» markdown included.
    * ``regulation_title`` is the PARENT slot, and it follows the house mapping
      (``preprocessor._reference_from_ura``): the parent نظام for a chunk or a
      مادة, the court for a ruling, the issuing entity for a تعميم, the provider
      for a خدمة — and for a WHOLE نظام its own name, because there is no parent
      above a document (``aggregator/models.py:160-163``, where ``render_label``
      relies on exactly that).

    ``doc_type`` closes the loop the parity work opened: a chunk the searcher
    identified *because* its chip read «لائحة تنفيذية» is republished with that
    chip, instead of falling back to «نظام» on the card the answer cites.
    ``article_num`` is carried for the same reason — without it ``render_label``
    degrades every مادة of one statute to the same bare نظام line, and
    ``referenceLabel`` cannot rebuild «المادة 81 من نظام العمل».
    """
    refs: list[Reference] = []
    for n, (obj, unfolded) in enumerate(zip(group.objects, unfolds), 1):
        source_type = _SOURCE_TYPE_FIX.get(obj.level, LEVEL_SOURCE_TYPE[obj.level])
        parent = obj.subtitle or (obj.title if obj.level == "regulation_doc" else "")
        refs.append(
            Reference(
                n=n,
                source_type=source_type,  # type: ignore[arg-type]
                regulation_title=parent or obj.label_ar(),
                title=obj.title or obj.label_ar(),
                article_num=obj.article_number or None,
                snippet=(unfolded.text or "")[:_REF_SNIPPET_CHARS],
                relevance="high",
                ref_id=obj.ref_id(),
                domain=obj.domain(),  # type: ignore[arg-type]
                doc_type=obj.doc_type,
                entity_name=obj.subtitle if obj.level in _ENTITY_LEVELS else "",
                landing_url=obj.source_url if obj.level in _LANDING_URL_LEVELS else "",
                details_url=obj.source_url if obj.level == "judgment" else "",
                service_url=obj.source_url if obj.level == "service" else "",
            )
        )
    return refs


# =========================================================================== #
# One group → one synthesizer.
# =========================================================================== #


@dataclass
class _Answer:
    """One synthesizer's verdict on one document, plus what the responder needs.

    ``refused`` marks the §13l.5 case: the ledger refused the ruling, so no
    synthesizer was built at all and ``output`` carries a line this module
    wrote, not one a model wrote. It is also a **hard card veto** in
    :func:`_finalise` — a body that says «هذا الحكم يحتاج رصيد» is not a
    document, whatever the responder rules.

    The last three fields exist for the responder (plan §6/§7) and are filled
    where the data is, in :func:`_synthesize_group`:

    * ``fanout_index`` — the dispatch slot this group was handed. It is the
      only ordering that survives to :func:`_finalise`: ``answers`` is a dict
      keyed by ``document_key`` and insertion-ordered by *round*, so a document
      answered in cycle 2 (after a rejection) sorts after cycle-1 answers no
      matter which one the user asked for first (§7 "Order"). The ``D1..Dn``
      labels, the bubble order and the responder's framing order all derive
      from it.
    * ``truncated`` / ``summary_payload`` — the two unfold facts D3 says the
      responder must *see* rather than guess at: a body too truncated to stand
      as a document is an explicit decline case, and so is a §5 ladder that
      served summaries instead of the text. Computed from the local ``unfolds``
      list and carried here **because :func:`_finalise` must not re-unfold** —
      unfold is an I/O path and the judgment ledger is charged inside it
      (``_recording_judgment_access``), so a second call is a double charge as
      well as a double read.
    """

    group: _Group
    output: SynthesizerOutput
    references: list[Reference]
    refused: bool = False
    fanout_index: int = 0
    truncated: bool = False
    summary_payload: bool = False


#: §13j #5 — the note ``unfold._judgment_refused`` stamps on a no-body result.
_JUDGMENT_DENIED_NOTE = "judgment_access_denied"


def _is_refused_judgment(group: _Group, unfolds: list[UnfoldResult]) -> bool:
    """True when this whole group came back access-refused and thus bodiless.

    ALL of them, not any: a group is one document, and a group with any served
    body still has something to answer from. In practice a judgment group holds
    exactly one ruling, because every ruling is its own ``document_key``.
    """
    if group.level != "judgment" or not unfolds:
        return False
    return all(_JUDGMENT_DENIED_NOTE in (u.notes or []) for u in unfolds)


def refusal_message(group: _Group, unfolds: list[UnfoldResult]) -> str:
    """The Arabic reply for a ruling the ledger refused — written HERE, in code.

    §13j #5 measured what happens when a refused unfold is handed to a
    synthesizer anyway: with no body to explain, the model explains the *absence*
    — «لا يتوفر أي نص», «الموجود لدي حكم واحد فقط» — and 3/3 replies told the
    user the other rulings and their own attached report did not exist. An LLM
    must never be asked to narrate an empty document.

    The line is also **attributable**, which the measured replies were not: it
    names the ruling that was withheld, so a partial fan-out reads as "these two
    opened, that one needs balance" instead of as a broken system.

    ``unfold``'s own per-reason lines are reused verbatim (they name the quota,
    the frozen library, the missing plan or the login — never non-existence).
    """
    obj = group.objects[0] if group.objects else None
    name = ""
    if obj is not None:
        name = (obj.title or "").strip() or (
            f"الحكم {obj.case_ref}" if obj.case_ref else ""
        )
    court = (obj.subtitle or "").strip() if obj is not None else ""
    head = f"الحكم المطلوب: «{name}»" if name else "الحكم المطلوب"
    if court:
        head = f"{head} — {court}"
    # Dedupe: one line per distinct reason, in order.
    reasons = list(dict.fromkeys(
        (u.text or "").strip() for u in unfolds if (u.text or "").strip()
    ))
    if not reasons:
        return f"{head}."
    return f"{head}.\n\n" + "\n".join(reasons)


async def _synthesize_group(
    group: _Group,
    *,
    supabase: Any,
    question: str,
    conversation_id: str,
    case_id: str | None,
    detail_level: str,
    judgment_access: JudgmentAccessResolver,
    recent_messages: list | None = None,
    unlock_records: list[_UnlockRecord] | None = None,
    fanout_index: int = 0,
) -> _Answer:
    """Unfold the group's objects, then run ONE fresh synthesizer over them.

    ``unfold(always)`` runs here, in the runner, for cases A, B and C alike
    (§2.2) — it is the synthesizer's input path and never belonged to the
    searcher. A fresh agent is built per call: a loop-back must start over.

    ``judgment_access`` is **required, with no default**. That is deliberate: a
    default of ``None`` is how a wiring gap turns into free rulings, and the eval
    measured exactly that (17 unlocks before, 17 after, full body served). A
    forgotten argument is now a ``TypeError`` at the call site.

    **A refused ruling stops here** (§13l.5): no synthesizer is built, and the
    group's reply is :func:`refusal_message`, written deterministically.

    **No ``welcome_instruction``.** It used to arrive here and be handed to
    ``build_synthesizer_user_message`` for the first group of the first round
    only. The responder owns the opening line of the turn now (responder plan
    §9), so the welcome is passed to it in :func:`_finalise` instead — which
    also retires the "which synthesizer is first?" question the old
    ``welcome_instruction if i == 0`` threading had to answer from inside a
    concurrent fan-out. ``build_synthesizer_user_message`` still *accepts* the
    argument; this module simply stops passing one.

    ``fanout_index`` is no longer only the ``_UnlockRecord`` sort key: it is
    carried onto the returned :class:`_Answer` as the turn's one stable
    ordering (see that dataclass).
    """
    access = judgment_access
    if unlock_records is not None:
        access = _recording_judgment_access(judgment_access, unlock_records, fanout_index)
    unfolds = [
        await unfold(supabase, obj, judgment_access=access)
        for obj in group.objects
    ]
    references = build_references(group, unfolds)
    # D3 — the two unfold facts the responder must SEE rather than guess at,
    # captured here because this is the only scope that holds ``unfolds``.
    truncated = any(u.truncated for u in unfolds)
    summary_payload = any(u.payload == "summary" for u in unfolds)

    if _is_refused_judgment(group, unfolds):
        logger.info(
            "simple_search: judgment access refused for %s — answering with the "
            "refusal line, NOT a synthesizer (§13l.5)", group.key,
        )
        return _Answer(
            group=group,
            # No body ⇒ nothing to put on a card, and the references would point
            # at a document the user cannot open. That verdict is no longer a
            # field on this output (it moved to the responder, plan §5) — it is
            # enforced as ``_Answer.refused``, which :func:`_finalise` vetoes
            # unconditionally, and the line still reaches the bubble so the turn
            # names the ruling it could not open (§13j #5).
            output=SynthesizerOutput(synthesis_md=refusal_message(group, unfolds)),
            references=references,
            refused=True,
            fanout_index=fanout_index,
            truncated=truncated,
            summary_payload=summary_payload,
        )

    agent = create_synthesizer_agent(group.level)
    result = await run_tracked(
        agent,
        build_synthesizer_user_message(
            question,
            unfolds,
            references,
            detail_level=detail_level,
            recent_messages=recent_messages,
        ),
        deps=SynthesizerDeps(conversation_id=conversation_id, case_id=case_id),
        stage="simple_search.synthesize",
        slot=SYNTHESIZER_SLOT,
        agent_family=AGENT_FAMILY,
        subtype=group.level,
        usage_limits=SYNTHESIZER_LIMITS,
    )
    output = result.output
    if not isinstance(output, SynthesizerOutput):  # defensive; salvager returns one
        output = SynthesizerOutput(synthesis_md=str(output or ""))
    return _Answer(
        group=group,
        output=output,
        references=references,
        fanout_index=fanout_index,
        truncated=truncated,
        summary_payload=summary_payload,
    )


# =========================================================================== #
# Attachments — case B (library object) vs case C (WI refs).
# =========================================================================== #

# §8 — the library carrier writes ``kind='references'`` items. Its uncapped kind
# is what keeps library objects from crowding the 15-item workspace cap.
_LIBRARY_KIND = "references"

# §8 page types → our level vocabulary. ``circular`` / ``form`` / ``calculator``
# / ``topic`` have no grounder yet (§13 deferred), so they never arrive here.
_PAGE_TYPE_LEVEL: dict[str, SimpleSearchLevel] = {
    "regulation": "regulation_doc",
    "article": "article",
    "judgment": "judgment",
}


def _as_snapshot(item: Any) -> WorkspaceItemSnapshot:
    """Normalize one attached item to the orchestrator's snapshot contract.

    ``_load_attached_items`` (orchestrator) hands every family a
    :class:`WorkspaceItemSnapshot`, and this family was written against plain
    dicts — so the first turn that ran with a workspace item attached died on
    ``'WorkspaceItemSnapshot' object has no attribute 'get'`` and every later
    turn in that conversation died the same way (the card persists, so the
    crash repeats forever). Coercing ONCE here keeps a single internal type and
    leaves every reader below on attribute access.

    Dicts are still accepted because the ``eval/`` harnesses hand-build partial
    ones (often just ``{"item_id": ...}``); missing fields default rather than
    raise, matching what the previous ``.get(...) or ""`` reads did.
    """
    if isinstance(item, WorkspaceItemSnapshot):
        return item
    d = item if isinstance(item, dict) else {}
    return WorkspaceItemSnapshot(
        item_id=str(d.get("item_id") or ""),
        kind=str(d.get("kind") or ""),
        title=str(d.get("title") or ""),
        content_md=str(d.get("content_md") or ""),
        summary=str(d.get("summary") or ""),
        word_count=int(d.get("word_count") or 0),
        metadata=d.get("metadata") if isinstance(d.get("metadata"), dict) else {},
        wi_seq=d.get("wi_seq"),
    )


def resolved_from_attachment(item: WorkspaceItemSnapshot) -> ResolvedObject | None:
    """Case B: recover a pre-resolved identity from a library workspace item.

    Reads, in order:

    1. ``metadata.simple_search_object`` — an explicit :class:`ResolvedObject`
       payload. This is the contract the case-B route should write, because it
       is the only shape that needs no second resolution step.
    2. ``metadata.source_page_type`` + ``metadata.source_row_id`` — the §8 dedup
       keys plus the backing row's uuid.

    Returns None when neither is usable, which drops the turn back to the
    searcher rather than opening a guessed object. Note that
    ``metadata.source_page_id`` alone is a **slug**, not an id, so it cannot
    resolve here without a DB lookup — see the report note on C3.
    """
    if (item.kind or "") != _LIBRARY_KIND:
        return None
    meta = item.metadata or {}
    if not isinstance(meta, dict):
        return None

    payload = meta.get("simple_search_object")
    if isinstance(payload, dict):
        try:
            obj = ResolvedObject.model_validate(payload)
        except Exception:  # noqa: BLE001 — a malformed payload is not fatal
            logger.warning("simple_search: bad simple_search_object on %s", item.item_id)
        else:
            if not obj.missing_id():
                if not obj.title:
                    obj.title = str(item.title or "")
                return obj

    level = _PAGE_TYPE_LEVEL.get(str(meta.get("source_page_type") or ""))
    row_id = str(meta.get("source_row_id") or "").strip()
    if not level or not row_id:
        return None
    obj = ResolvedObject(level=level, title=str(item.title or ""))
    if level == "regulation_doc":
        obj.regulation_id = row_id
    elif level == "article":
        obj.article_id = row_id
    else:
        obj.case_id = row_id
    return None if obj.missing_id() else obj


# =========================================================================== #
# Pause (§2.1.4) — the deferred ask_user + paused_runs machinery.
# =========================================================================== #


def _deferred_args(call: Any) -> dict:
    """``ToolCallPart.args`` as a dict, whatever the provider sent.

    ``args`` is a dict OR a raw JSON string depending on the provider — reading
    the string verbatim shows the user ``{"question": "…"}`` (observed twice
    live, adv money/state lane). The orchestrator's planner path uses
    ``args_as_dict()`` for exactly this hazard; invisible to the scripted-model
    tests because ``_fmodels`` always passes dicts.
    """
    import json

    as_dict = getattr(call, "args_as_dict", None)
    if callable(as_dict):
        try:
            parsed = as_dict()
            if isinstance(parsed, dict):
                return parsed
        except Exception:  # noqa: BLE001 — fall through to the manual parse
            pass
    args = getattr(call, "args", None)
    if isinstance(args, dict):
        return args
    if isinstance(args, str) and args.strip().startswith("{"):
        try:
            parsed = json.loads(args)
        except ValueError:
            return {}
        if isinstance(parsed, dict):
            return parsed
    return {}


def _deferred_call(deferred: Any) -> Any | None:
    """The first pending tool call on a ``DeferredToolRequests`` output."""
    for call in getattr(deferred, "calls", None) or []:
        return call
    return None


def _deferred_question(deferred: Any) -> str:
    """The Arabic question text out of a ``DeferredToolRequests`` payload."""
    for call in getattr(deferred, "calls", None) or []:
        args = _deferred_args(call)
        if args.get("question"):
            return str(args["question"])
        raw = getattr(call, "args", None)
        if isinstance(raw, str) and raw and not raw.strip().startswith("{"):
            return raw
    return ""


def pause_slot_is_taken(supabase, conversation_id: str, user_id: str) -> dict | None:
    """The conversation's open pause row, if another run already owns the slot.

    §9 trap 10 — ``find_open_pause`` reads THE single open pause per
    conversation, and the searcher shares that slot with the deep_search
    planner. On the eval account the incumbent was a **June** planner pause,
    still open in August.
    """
    try:
        return find_open_pause(supabase, conversation_id, user_id)
    except Exception as exc:  # noqa: BLE001 — a read failure must not pause-block
        logger.warning("simple_search: open-pause lookup failed: %s", exc)
        return None


def _record_searcher_pause(
    supabase, result: Any, *, user_id: str, conversation_id: str,
    case_id: str | None, question: str,
    deferred: Any = None,
    turn_question: str = "",
    candidates: dict[str, ResolvedObject] | None = None,
    candidate_lines: list[str] | None = None,
    answered_keys: list[str] | None = None,
) -> str | None:
    """Persist the searcher's pause **so that it can actually be resumed**.

    §13j #2 measured the old row: no ``deferred_payload``, so even after the
    orchestrator learned to resume this family, ``_resume_major_agent_inner``
    would raise ``ValueError("deferred_payload missing tool_call_id")`` and
    abandon. The searcher's ``ask_user`` was write-only machinery — a question
    whose answer went nowhere.

    Two halves make it resumable, and both must be here (§13l.4):

    * ``message_history`` — ``result.all_messages_json()``, the bytes
      :func:`resume_simple_search` feeds back to ``agent.run``.
    * ``deferred_payload`` — the ``ask_user`` call's ``tool_call_id`` (what the
      user's reply is keyed to), **plus** the family-internal state a resumed
      turn cannot otherwise recover: the turn's original question, the
      ``C1…Cn`` candidate registry (never persisted anywhere else — a case-C
      resume without it cannot resolve the handle the searcher is about to
      pick), and the group keys already answered and delivered.

    The caller checks the slot BEFORE calling this (§13l.3): the two outcomes
    differ (a pause vs. an inline question), so the decision belongs where the
    result is assembled, not here.
    """
    try:
        history = None
        try:
            history = result.all_messages_json()
        except Exception:  # noqa: BLE001 — history is a resume nicety, not a gate
            logger.debug("simple_search: no message history for pause", exc_info=True)

        call = _deferred_call(deferred)
        payload: dict[str, Any] = {
            "tool_call_id": getattr(call, "tool_call_id", "") if call else "",
            "tool_name": getattr(call, "tool_name", "ask_user") if call else "ask_user",
            "args": _deferred_args(call) if call else {"question": question},
            "partial_output": None,
            # ── family-internal resume surface ──────────────────────────────
            "question": turn_question,
            "candidates": {
                handle: obj.model_dump() for handle, obj in (candidates or {}).items()
            },
            "candidate_lines": list(candidate_lines or []),
            "answered_keys": list(answered_keys or []),
        }
        if not payload["tool_call_id"]:
            logger.warning(
                "simple_search: pausing with no tool_call_id — the row will not "
                "rehydrate and the reply will be answered as a fresh turn",
            )

        now = datetime.now(timezone.utc)
        return record_pause(
            supabase,
            PauseRecord(
                conversation_id=conversation_id,
                user_id=user_id,
                case_id=case_id,
                agent_family=AGENT_FAMILY,
                message_history=history,
                deferred_payload=payload,
                question_text=question,
                pause_reason="clarify",
                asked_at=now,
                expires_at=now + timedelta(hours=PAUSE_TTL_HOURS),
            ),
        )
    except Exception as exc:  # noqa: BLE001 — never break a user-facing turn
        logger.warning("simple_search: pause record failed: %s", exc)
        return None


# =========================================================================== #
# The entry point — C1.
# =========================================================================== #


async def run_simple_search(
    question: str,
    supabase: "SupabaseClient",
    user_id: str,
    conversation_id: str,
    case_id: str | None,
    *,
    # case B/C payload; [] for case A. The orchestrator passes
    # WorkspaceItemSnapshot; dicts are coerced by _as_snapshot for eval harnesses.
    attached_items: "list[WorkspaceItemSnapshot] | list[dict] | None" = None,
    recent_messages: list | None = None,        # the planners' conversation window
    user_preferences: dict | None = None,
    user_call_name: str | None = None,
    welcome: "WelcomeState | None" = None,
    emit_sse: "Callable[[dict], Awaitable[None]] | None" = None,
) -> "SimpleSearchRunResult":
    """Run one ``simple_search`` turn: resolve, open, answer, publish.

    Args:
        question: the user's raw message. Passed through unchanged — neither
            agent in this family paraphrases it (§2.1).
        supabase: the sync **service-role** client. The anon key hits RLS and
            silently returns empty results (§9 trap 11).
        user_id: a **users.user_id**, never an ``auth_id``. It is what the
            ledger keys on, so the wrong one here would charge the wrong
            account — the orchestrator receives it already mapped.
        attached_items: the workspace items the router attached, as
            :class:`WorkspaceItemSnapshot` (what ``_load_attached_items``
            returns). Normalized via ``_as_snapshot``, so plain dicts from the
            ``eval/`` harnesses work too. A
            ``kind='references'`` library item is **case B** — the searcher is
            skipped entirely. Any other attached item is **case C** — its cited
            sources become the searcher's candidate list.
        user_preferences: read for ``detail_level`` only.
        user_call_name: reserved; the welcome line already carries the address.
        welcome: this turn's welcome state. Rendered into the **responder**
            (responder plan §9) — the greeting is the opening LINE of the turn,
            and the responder now owns the opening. It used to go to the first
            synthesizer of the first round, guarded by ``if i == 0``, because
            three fan-out replies each opening with «أهلين» would read as a
            stutter; with one voice writing the lead-in there is no first reply
            to pick any more. ``mark_welcomed`` stays where it is
            (``orchestrator.py:2775``): a turn that only asked a question must
            still leave the welcome unspent, and that invariant is the
            orchestrator's.
        emit_sse: async sink handed to the searcher's tools for live chips. The
            turn's ``workspace_item_created`` events are NOT sent through it —
            they come back on ``sse_events`` so ``_route`` drains them exactly
            once (C1).

    Returns:
        A :class:`SimpleSearchRunResult`. Never raises for an agent-level
        failure: a pause, an abort and an exhausted loop are all normal
        outcomes with their own shape.
    """
    prefs = user_preferences or {}
    detail_level = str(prefs.get("detail_level") or "")
    welcome_instruction = render_welcome_instruction(welcome) or None
    # The same greeting as literal text — D7's fallback opens with it when
    # the responder never gets to (see `_finalise`).
    welcome_opening = compose_opening(welcome) if welcome else ""
    items = [_as_snapshot(i) for i in (attached_items or [])]
    # D12 — built once per turn, bound to this user. Every ruling body served
    # this turn goes through it; nothing else in the family is metered.
    grant_judgment_access = judgment_access_resolver(supabase, user_id)

    with track_stage(
        "simple_search.turn",
        conversation_id=conversation_id,
        case_id=case_id,
        agent_family=AGENT_FAMILY,
        question_chars=len(question or ""),
        attached_count=len(items),
    ) as span:
        # ── The searcher ALWAYS runs. ───────────────────────────────────────
        # An earlier cut short-circuited here: any attached library page was
        # synthesized directly and the searcher was skipped. That was wrong.
        # Attaching a page is CONTEXT, not a routing decision — the user can
        # attach نظام العمل and then ask «اش المادة 5 من نظام التنفيذ؟», and the
        # short-circuit answered about the wrong document. Worse, the carried
        # item persists as a workspace item, so the hijack could repeat on later
        # turns. Every turn starts at the router, and inside this family every
        # turn starts at the searcher.
        #
        # The pre-resolved identity is still worth having — it just becomes a
        # CANDIDATE the searcher may select (saving a resolution round-trip)
        # rather than an answer imposed on the question.
        deps = SearcherDeps(
            supabase=supabase,
            user_id=user_id,
            conversation_id=conversation_id,
            case_id=case_id,
            emit_sse=emit_sse,
            recent_messages=list(recent_messages or []),
        )
        for item in items:
            obj = resolved_from_attachment(item)
            if obj is not None:
                deps.register_candidate(
                    obj,
                    f"{obj.label_ar()}: {obj.title or '(بدون عنوان)'} "
                    f"— أحضرها المستخدم من المكتبة",
                )
        if deps.candidates:
            span.set(library_candidates=len(deps.candidates))
        wi_ids = [i.item_id for i in items if i.item_id]
        if wi_ids:
            # The attached CARDS' own identities, ahead of their refs. Without
            # this the searcher receives a card's references but not the card:
            # «اش الحكم اللي في المذكرة؟» hunted for a source NAMED "مذكرة"
            # among the refs and asked the user (adv family lane, casec-04).
            # The searcher also only speaks C-handles while users and the
            # router say WI-N — this line is where the two vocabularies meet.
            for item in items:
                seq = item.wi_seq
                title = str(item.title or "").strip()
                if seq is not None and title:
                    deps.candidate_lines.append(
                        f"(البطاقة المرفقة WI-{seq} «{title}» — عندما يقول المستخدم "
                        f"«المذكرة»/«التقرير»/«WI-{seq}» فهو يقصد هذه البطاقة؛ "
                        f"مصادرها هي المرشحات التالية)"
                    )
            # unfold(preview) — bounded ABOVE by what the card shows (§2.3.2).
            for obj, preview in await asyncio.to_thread(
                collect_case_c_candidates, supabase, wi_ids
            ):
                deps.register_candidate(obj, preview)
            span.set(case="C", candidates=len(deps.candidates))

        return await _answer_loop(
            question=question,
            supabase=supabase,
            user_id=user_id,
            conversation_id=conversation_id,
            case_id=case_id,
            deps=deps,
            welcome_instruction=welcome_instruction,
            welcome_opening=welcome_opening,
            detail_level=detail_level,
            judgment_access=grant_judgment_access,
            recent_messages=recent_messages,
            span=span,
        )


async def _answer_loop(
    *,
    question: str,
    supabase: Any,
    user_id: str,
    conversation_id: str,
    case_id: str | None,
    deps: Any,
    welcome_instruction: str | None,
    welcome_opening: str = "",
    detail_level: str,
    judgment_access: JudgmentAccessResolver,
    recent_messages: list | None,
    span: Any,
    first_run_kwargs: dict | None = None,
    skip_keys: set[str] | None = None,
    turn_question: str = "",
) -> SimpleSearchRunResult:
    """The D3 loop — shared by fresh dispatch and by :func:`resume_simple_search`.

    ``first_run_kwargs`` is how a resumed turn re-enters here: it carries the
    rehydrated ``message_history`` + ``deferred_tool_results`` for cycle 1 only,
    so the resumed searcher's decision flows into synthesis and publication by
    exactly the same path a fresh one does. Everything after that first call is
    identical code — that is the point, and it is why resume cannot drift from
    dispatch.

    ``skip_keys`` are documents already answered and DELIVERED on the leg that
    paused; re-synthesizing one would show the user the same reply twice and pay
    a second synthesizer for it. They still have to reach :func:`_finalise`
    (responder plan §8): a skipped group never enters ``answers``, so without
    ``delivered`` below the responder would present the new document as if the
    earlier one had never been opened — «فتحت لك نظام العمل» on a turn where the
    user is already looking at نظام العمل.
    """
    answers: dict[str, _Answer] = {}
    unlock_records: list[_UnlockRecord] = []
    skip = set(skip_keys or ())
    #: §8 — the groups this leg SKIPPED because the paused leg already delivered
    #: and carded them. Collected here because this is the only scope that holds
    #: both ``group_documents(output.resolved)`` (the objects, with their titles
    #: and level) and ``skip`` (the keys). Keyed by group key: a document
    #: re-selected on two cycles is one delivered document, not two.
    delivered: dict[str, _Group] = {}
    dispatched = 0          # fan-out slots handed out this turn — the note order
    #: Synthesizers that were dispatched and never came back — the ONLY loss the
    #: user can see, and therefore the only thing §7.2's honesty line may fire
    #: on. It is deliberately NOT ``dispatched - len(answers)``: that difference
    #: also counts rejections, and a rejection is a document the loop *replaced*
    #: (D3), not one it dropped. Counting slots made every turn with a loop-back
    #: — the exact path the retry pool exists for — open by apologising for a
    #: document the user did receive.
    lost = 0
    cycles = 0
    # ONE counter, turn-wide (D3). Not per synthesizer, not per document.
    while cycles < MAX_CYCLES:
        cycles += 1
        agent = create_searcher_agent()
        run_kwargs = dict(first_run_kwargs or {}) if cycles == 1 else {}
        prompt = (
            run_kwargs.pop("user_prompt")
            if "user_prompt" in run_kwargs
            else build_searcher_user_message(question)
        )
        result = await run_tracked(
            agent,
            prompt,
            deps=deps,
            stage="simple_search.search",
            slot=SEARCHER_SLOT,
            agent_family=AGENT_FAMILY,
            usage_limits=SEARCHER_LIMITS,
            **run_kwargs,
        )
        output = result.output

        if not isinstance(output, SearcherDecision):
            # DeferredToolRequests — ask_user. The turn pauses here…
            q = _deferred_question(output)
            # …but NOT before delivering what it already answered. §13j #1: the
            # old pause branch returned an empty result, throwing away replies
            # that were already synthesized AND already charged for (measured:
            # 3 unlocks billed, zero replies delivered, and the question asked
            # the user to re-attach the report whose rulings had just been
            # billed). Work that is paid for is delivered.
            #
            # The responder DOES run here (§8): those answers were already paid
            # for, and the pre-question delivery is exactly where framing helps
            # most. What it does not do is suggest — the searcher's ``ask_user``
            # question, appended below or emitted as an ``agent_question``, IS
            # the turn's next step, and a second offer above it reads as two
            # competing questions.
            result_so_far = await _finalise(
                list(answers.values()),
                supabase=supabase,
                user_id=user_id,
                conversation_id=conversation_id,
                case_id=case_id,
                question=question,
                # Answers in hand + crashes. NOT the slot counter: see ``lost``.
                dispatched=len(answers) + lost,
                recent_messages=recent_messages,
                welcome_instruction=welcome_instruction,
                welcome_opening=welcome_opening,
                unselected_candidates=unselected_candidate_lines(
                    deps,
                    answered_keys=set(answers) | skip,
                    unlock_records=unlock_records,
                ),
                delivered_groups=list(delivered.values()),
                unlock_records=unlock_records,
                suppress_suggestion=True,
                degraded_fallback=False,
            )

            # §13l.3 — the pause slot holds ONE row per conversation and the
            # deep_search planner shares it. If it is taken (on the eval account
            # by a pause open since June), do NOT write a second row: the
            # searcher's question goes out as a normal message and the user's
            # answer arrives as a fresh routed turn. A lost answer-channel beats
            # a swapped question — the measured alternative showed the user the
            # PLANNER's months-old question and resumed the wrong agent.
            incumbent = pause_slot_is_taken(supabase, conversation_id, user_id)
            run_id = None
            if incumbent is not None:
                logger.error(
                    "simple_search: pause slot already held by run %s (family=%s, "
                    "asked_at=%s) for conversation %s — asking inline instead of "
                    "recording a second row (§13l.3)",
                    incumbent.get("run_id"), incumbent.get("agent_family"),
                    incumbent.get("asked_at"), conversation_id,
                )
            else:
                run_id = _record_searcher_pause(
                    supabase, result, user_id=user_id,
                    conversation_id=conversation_id, case_id=case_id, question=q,
                    deferred=output,
                    turn_question=turn_question or question,
                    candidates=getattr(deps, "candidates", None),
                    candidate_lines=getattr(deps, "candidate_lines", None),
                    answered_keys=[*skip, *answers.keys()],
                )

            if run_id:
                result_so_far.paused = True
                result_so_far.question_text = q or None
            else:
                # No row ⇒ nothing can consume a reply to this question, so it
                # must not be emitted as an `agent_question`: it goes out as the
                # last chat message and the next turn routes fresh.
                if q:
                    result_so_far.chat_messages.append(q)
                if not result_so_far.chat_messages:
                    result_so_far.chat_messages.append(_DEGRADED_AR)
            span.set(
                outcome="paused" if run_id else "asked_inline",
                cycles=cycles,
                delivered=len(result_so_far.chat_messages),
            )
            return result_so_far

        if output.aborted:
            # Most often an INTEGRATIVE question the fan-out cannot serve:
            # each object goes to its own synthesizer, which never sees the
            # others. The orchestrator hands the turn to deep_search — NOT
            # back through the router, which just chose this family and
            # (measured: 0/9 on comparison) would choose it again.
            logger.info(
                "simple_search: aborted (%s) — handing off to deep_search",
                output.abort_reason,
            )
            span.set(outcome="aborted", cycles=cycles,
                     abort_reason=output.abort_reason or "")
            return _empty(
                aborted=True,
                abort_reason=output.abort_reason or "",
                unlock_notes=unlock_notes_payload(unlock_records),
            )

        grouped = group_documents(output.resolved)
        # §8 — a group the paused leg already delivered is skipped for synthesis
        # but REMEMBERED for the responder. This is the only place both halves
        # are in hand, and the resolved objects are what carry the title and the
        # level the digest needs.
        for grp in grouped:
            if grp.key in skip and grp.key not in delivered:
                delivered[grp.key] = grp
        todo = [g for g in grouped if g.key not in answers and g.key not in skip]
        if not todo:
            break

        round_answers = await _run_round(
            todo,
            supabase=supabase,
            question=question,
            conversation_id=conversation_id,
            case_id=case_id,
            detail_level=detail_level,
            judgment_access=judgment_access,
            recent_messages=recent_messages,
            unlock_records=unlock_records,
            fanout_base=dispatched,
        )
        dispatched += len(todo)
        # ``_run_round`` logs and drops a synthesizer that raised, so the gap
        # between what went out and what came back IS the crash count.
        lost += len(todo) - len(round_answers)

        rejections: list[str] = []
        for answer in round_answers:
            if answer.output.is_answer():
                answers[answer.group.key] = answer
            else:
                rejections.append(
                    answer.output.rejection_reason.strip()
                    or "الكائن المسترجَع ليس المطلوب."
                )

        if not rejections:
            break
        # The shared pool is what makes this ONE loop-back for the whole
        # turn: every rejection from this round feeds the SAME next cycle.
        deps.rejection_notes.extend(rejections)

    span.set(cycles=cycles, answered=len(answers))
    return await _finalise(
        list(answers.values()),
        supabase=supabase,
        user_id=user_id,
        conversation_id=conversation_id,
        case_id=case_id,
        question=question,
        # Answers in hand + crashes. NOT the slot counter: see ``lost``.
        dispatched=len(answers) + lost,
        recent_messages=recent_messages,
        welcome_instruction=welcome_instruction,
        welcome_opening=welcome_opening,
        unselected_candidates=unselected_candidate_lines(
            deps,
            answered_keys=set(answers) | skip,
            unlock_records=unlock_records,
        ),
        delivered_groups=list(delivered.values()),
        unlock_records=unlock_records,
        # A resume that re-selects only documents the paused leg already
        # answered has nothing new to say — and «لم أتمكّن من تأكيد المصدر»
        # would be a lie about work that succeeded. No responder call on that
        # path either (§8): with no answers there is nothing to respond about.
        degraded_text=_ALREADY_ANSWERED_AR if (skip and not answers) else _DEGRADED_AR,
    )


async def _run_round(
    groups: list[_Group],
    *,
    supabase: Any,
    question: str,
    conversation_id: str,
    case_id: str | None,
    detail_level: str,
    judgment_access: JudgmentAccessResolver,
    recent_messages: list | None = None,
    unlock_records: list[_UnlockRecord] | None = None,
    fanout_base: int = 0,
) -> list[_Answer]:
    """Run one synthesizer per document, concurrently, in fan-out order.

    ``fanout_base`` makes the slot numbers turn-wide rather than round-wide, so
    a cycle-2 document keeps sorting after every cycle-1 document in
    :func:`_finalise` — see :class:`_Answer.fanout_index`.

    No welcome here any more (responder plan §9): the greeting is the opening
    line of the *turn*, not of whichever synthesizer happened to be dispatched
    first, and it is now handed to the responder in :func:`_finalise`.
    """
    if not groups:
        return []
    tasks = [
        _synthesize_group(
            group,
            supabase=supabase,
            question=question,
            conversation_id=conversation_id,
            case_id=case_id,
            detail_level=detail_level,
            judgment_access=judgment_access,
            recent_messages=recent_messages,
            unlock_records=unlock_records,
            fanout_index=fanout_base + i,
        )
        for i, group in enumerate(groups)
    ]
    settled = await asyncio.gather(*tasks, return_exceptions=True)
    answers: list[_Answer] = []
    for group, outcome in zip(groups, settled):
        if isinstance(outcome, BaseException):
            logger.error(
                "simple_search: synthesizer failed for %s: %s",
                group.key, outcome, exc_info=outcome,
            )
            continue
        answers.append(outcome)
    return answers


# =========================================================================== #
# The responder — the turn's voice, and the publish gate (responder plan §7).
# =========================================================================== #


#: A ``candidate_lines`` entry that starts with a handle is an OBJECT the
#: searcher registered. Entries without one are prose the runner appended for
#: the searcher's benefit (the «البطاقة المرفقة WI-N …» note), and they name
#: nothing that could be opened next turn.
_CANDIDATE_HANDLE_RE = re.compile(r"^\s*(C\d+)\b")


def _object_title(obj: ResolvedObject | None) -> str:
    """One document's display name: its own title, else the Arabic level noun.

    The middle and last rungs of the §6 title chain (the responder's own string
    is the first). Shared by the digest — where it is what the responder names
    the document by — and by the card title, so a card and the sentence
    introducing it cannot disagree about what the document is called.
    """
    if obj is None:
        return ""
    return (obj.title or "").strip() or obj.label_ar()


def unselected_candidate_lines(
    deps: Any,
    *,
    answered_keys: set[str] | None = None,
    unlock_records: list[_UnlockRecord] | None = None,
) -> list[str]:
    """The objects this turn considered and did NOT open — suggestion material.

    §4/§6: the searcher **saw** the لائحة تنفيذية sitting beside the نظام and
    chose not to open it, so «تحب أفتح لك اللائحة؟» is grounded in what the turn
    actually weighed rather than invented by a model staring at its own output.
    ``build_responder_user_message`` fences these as the ONLY offerable objects.

    Two filters, and both are the runner's job because ``prompts.py`` holds
    neither piece of state:

    * **Already opened.** A candidate whose ``document_key`` is in
      ``answered_keys`` (this leg's answers plus the paused leg's ``skip`` set)
      was just delivered; offering to open it is offering to repeat the turn.
    * **Refused rulings** — trap §11.7. A ruling the ledger declined this turn
      appears in ``unlock_records`` with ``granted=False``, and offering to open
      it is offering the user the exact thing they were just told they cannot
      have. This is why unlock state is a parameter here and not of the prompt
      builder: the filtering decision belongs to whoever holds the ledger
      verdicts.
    """
    candidates = getattr(deps, "candidates", None) or {}
    answered = set(answered_keys or ())
    refused_cases = {
        rec.case_id for rec in (unlock_records or [])
        if rec.case_id and not rec.granted
    }
    lines: list[str] = []
    for raw in getattr(deps, "candidate_lines", None) or []:
        text = str(raw or "").strip()
        if not text:
            continue
        match = _CANDIDATE_HANDLE_RE.match(text)
        if match is None:
            continue
        obj = candidates.get(match.group(1).upper())
        if obj is None:
            continue
        if document_key(obj) in answered:
            continue
        if obj.level == "judgment" and str(obj.case_id or "") in refused_cases:
            continue
        lines.append(text)
    return lines


async def _finalise(
    answers: list[_Answer],
    *,
    supabase: Any,
    user_id: str,
    conversation_id: str,
    case_id: str | None,
    question: str,
    dispatched: int = 0,
    recent_messages: list | None = None,
    welcome_instruction: str | None = None,
    welcome_opening: str = "",
    unselected_candidates: list[str] | None = None,
    delivered_groups: list[_Group] | None = None,
    unlock_records: list[_UnlockRecord] | None = None,
    suppress_suggestion: bool = False,
    degraded_fallback: bool = True,
    degraded_text: str = _DEGRADED_AR,
) -> SimpleSearchRunResult:
    """Run the responder, publish what it cards, assemble the bubble (§7/§9).

    **The single choke point.** Both the terminal path and the pause path reach
    here, and ``resume_simple_search`` re-enters through the same
    :func:`_answer_loop` — so putting any of this anywhere else guarantees
    dispatch and resume drift apart invisibly (trap §11.6).

    Order is load-bearing (§7), and it is the order of the code below:

    1. drop empty bodies — but **keep the refusals**, whose bodies this module
       wrote (:func:`refusal_message`);
    2. sort by ``fanout_index`` and assign ``D1..Dn`` in that order — dispatch
       order, not ``answers`` insertion order (see :class:`_Answer`);
    3. digest each answer into a :class:`ResponderDocDigest` — a bounded
       excerpt and the measured body length, never the body (trap §11.8);
    4. **call the responder**;
    5. apply the code vetoes to its verdicts;
    6. publish only what survives as ``card=True`` — nothing is written to
       ``workspace_items`` before step 4 returns (trap §11.5), exactly as
       ``should_publish`` gates ``publish_search_result``
       (``orchestrator.py:3063``);
    7. assemble the bubble (§9).

    **The bubble**, in this fixed order, returned as ordered ``chat_messages``
    entries because ``orchestrator.py:2736`` joins them with ``"\\n\\n"``::

        [responder.chat_summary_md]
        [verbatim synthesis_md of every UNCARDED answer, dispatch order]  ← code
        [responder.suggestion_md]              ← omitted when suppressed (§8)
        [unlock_acknowledgement(...)]          ← code, unchanged (D5)

    A carded answer contributes **nothing** to the bubble — its body lives on
    its card, which is the §1.1 fix: before this, the same ``synthesis_md`` was
    the bubble AND the card's ``content_md``, so a carded lookup showed the user
    the whole document twice. Uncarded bodies are moved by code and never
    regenerated (D4), and a ``card=False`` verdict can never *lose* one: it
    decides **where** the text goes, never **whether** it ships (trap §11.4).

    **The vetoes code keeps** (D3 — the responder rules on everything else,
    with the body in hand):

    * A **refused judgment** never gets a card, whatever the verdict says. Its
      body is :func:`refusal_message`, written here, and a "you need balance"
      line is not a document. It is still shown to the responder as a document
      so the turn frames the refusal honestly instead of going silent about a
      ruling the user asked for by name — the §13j #5 failure this family fixed
      once already, where 3/3 replies explained the *absence* of the ruling.
    * An **already-delivered** document never gets a card: the paused leg
      published one, and a second publish is a duplicate row in a 15-item-capped
      workspace. Enforced structurally — a delivered group has no ``_Answer``,
      so it is not in the publish loop at all (``zip`` below stops at ``live``).
    * A **missing verdict** for a dispatched label is ``card=False``, not an
      error (§6): the safe default under D7 is to leave no card. An *unknown*
      label never reaches here — the responder's output validator turns that one
      into an Arabic ``ModelRetry``.
    * ``card=True`` with an **empty title** falls back
      ``objects[0].title`` → ``label_ar()``, the chain that has always been here.

    **The responder is NOT called when there is nothing to respond about**
    (trap §11.3). With no live answer the code-written ``_DEGRADED_AR`` /
    ``_ALREADY_ANSWERED_AR`` line stands and no LLM runs — a model asked to
    narrate an empty result invents absence («لا يوجد نظام بهذا الاسم»), the
    exact failure :func:`refusal_message` exists to prevent, and the same
    conclusion ``_minimal_response`` (``planner/runner.py:158``) reached
    independently.

    **Failure publishes NOTHING (D7).** If the responder raises, times out or
    exhausts its retries, the turn degrades to the pre-responder behaviour —
    every body in the bubble in dispatch order with markers stripped, no cards,
    empty ``created_item_ids``, and the unlock acknowledgement still appended.
    The deliberate inverse of ``_response_from_artifact``
    (``planner/runner.py:140``), which publishes on failure because there the
    artifact is the product: here the bubble carries the text, so a missing card
    costs the user a re-ask while a wrongly-published one is permanent clutter.

    ``degraded_fallback`` is False on the pause path: there, an empty answer set
    is not a failure — the searcher's question IS the turn's message, and
    «لم أتمكّن من تأكيد المصدر» in front of it would contradict it.

    §13l.6 — when the ledger was charged this turn, ONE Arabic line naming how
    many rulings were opened is appended to the LAST chat message. Last, not
    first: the acknowledgment is a footer, and it is appended after the cards
    are published so the durable card never carries a billing line.

    Args:
        answers: every settled :class:`_Answer` of the turn, in whatever order
            ``answers.values()`` produced them — this function sorts.
        question: the user's RAW message, never a paraphrase (§2.1). Required
            and undefaulted: a responder handed an empty ``<user_message>``
            frames a turn it cannot see.
        dispatched: fan-out slots handed out this leg. When it exceeds the
            number of answers a synthesizer was dropped (``_run_round`` logs and
            continues) and the responder is told to say so (§7.2) — without it
            the turn announces "opened both" for a turn that opened one.
        unselected_candidates: pre-filtered lines from
            :func:`unselected_candidate_lines`.
        delivered_groups: §8 — documents the paused leg already delivered and
            carded. Digested with ``already_delivered=True``, an empty excerpt
            and no body: they can produce neither bubble text nor a card, and
            exist so the responder has continuity («سبق أن فتحت لك نظام العمل
            قبل قليل») instead of re-announcing a document the user is looking
            at.
        suppress_suggestion: the pause leg (§8). Told to the prompt AND enforced
            here on the way into the bubble.
    """
    created_item_ids: list[str] = []
    sse_events: list[dict] = []

    # ── 1/2. Empty bodies out; dispatch order in. ───────────────────────────
    # A REFUSED answer stays: its body is the code-written refusal line, the
    # user asked for that ruling by name, and silence about it is the §13j #5
    # failure. Its card is vetoed below, not its text.
    live = sorted(
        (a for a in answers if a.output.synthesis_md.strip()),
        key=lambda a: a.fanout_index,
    )

    # ── 3. The digest. Bounded excerpt + measured length, never the body. ───
    docs: list[ResponderDocDigest] = []
    for n, answer in enumerate(live, 1):
        body = answer.output.synthesis_md.strip()
        obj = answer.group.objects[0] if answer.group.objects else None
        docs.append(
            ResponderDocDigest(
                label=f"D{n}",
                level=answer.group.level,
                object_title=_object_title(obj),
                excerpt=body[:RESPONDER_EXCERPT_CHARS],
                body_chars=len(body),
                truncated=answer.truncated,
                summary_payload=answer.summary_payload,
            )
        )
    # §8 — the already-delivered set is appended AFTER this leg's answers so
    # ``D1`` keeps meaning "the first document this leg opened", which is what
    # ``fanout_index``, the bubble order and the responder's framing all agree
    # on. Their ``already_delivered="true"`` attribute, not their position, is
    # what tells the responder they are context rather than new work.
    for n, group in enumerate(delivered_groups or [], len(live) + 1):
        docs.append(
            ResponderDocDigest(
                label=f"D{n}",
                level=group.level,
                object_title=_object_title(group.objects[0] if group.objects else None),
                excerpt="",
                body_chars=0,
                already_delivered=True,
            )
        )

    # ── 4. The call. Everything above is input; nothing below runs first. ───
    responder: ResponderOutput | None = None
    summary = ""
    suggestion = ""
    if live:
        try:
            # Built HERE, per turn — never a module-level singleton. The house
            # test harness stubs agents by patching each module's
            # ``get_agent_model`` (``tests/_fmodels.py``), and a cached instance
            # would bind a live model at import and make this path untestable.
            agent = create_responder_agent()
            result = await run_tracked(
                agent,
                build_responder_user_message(
                    question,
                    docs,
                    recent_messages=recent_messages,
                    # The delivered digests count as answered in the prompt's
                    # `<turn>` tag, so they must count as dispatched too, or a
                    # resume leg reports a dropped synthesizer that never was.
                    # The builder clamps this up to `len(docs)`; it never lies
                    # downward.
                    dispatched=int(dispatched or 0) + (len(docs) - len(live)),
                    unselected_candidates=unselected_candidates,
                    welcome_instruction=welcome_instruction,
                    suppress_suggestion=suppress_suggestion,
                ),
                deps=ResponderDeps(
                    conversation_id=conversation_id,
                    case_id=case_id,
                    # EXACTLY the labels rendered above: the output validator
                    # retries any `doc` outside this tuple, so a mismatch here
                    # turns every verdict into an unwinnable retry loop.
                    doc_labels=tuple(d.label for d in docs),
                ),
                stage="simple_search.respond",
                slot=RESPONDER_SLOT,
                agent_family=AGENT_FAMILY,
                usage_limits=RESPONDER_LIMITS,
            )
            output = result.output
            if not isinstance(output, ResponderOutput):  # defensive; salvager returns one
                raise TypeError(
                    f"responder returned {type(output).__name__}, not ResponderOutput"
                )
            summary = output.chat_summary_md.strip()
            if not summary:
                # An empty lead-in with every body carded is an EMPTY bubble —
                # the one outcome no branch here may produce. Treated as a
                # failure rather than papered over: the responder's whole job is
                # the opening, and D7 says the safe direction is text without
                # cards, never cards without text.
                raise ValueError("responder returned an empty chat_summary_md")
            suggestion = output.suggestion_md.strip()
            responder = output
        except Exception as exc:  # noqa: BLE001 — D7: degrade, never raise
            logger.warning(
                "simple_search: responder failed for conversation %s (%d answer(s), "
                "%d dispatched) — delivering every body uncarded and publishing "
                "nothing (D7): %s",
                conversation_id, len(live), dispatched, exc, exc_info=True,
            )
            responder = None
            # §9 moved the greeting off the synthesizer, which made the
            # responder its ONLY writer — and D7 lets the responder fail while
            # the bodies still ship. The orchestrator sees a delivered answer
            # and marks the welcome spent (``orchestrator.py`` tail), so a
            # greeting that was never written is burned for good: one user, one
            # chance, gone to an exception they never saw. The composed line is
            # literal Arabic text, so code can open with it exactly as the model
            # was going to.
            summary = welcome_opening
            suggestion = ""

    # ── 5/6. Vetoes, then the gated publish. ────────────────────────────────
    # ``zip`` stops at ``live``: the delivered digests that trail ``docs`` have
    # no answer behind them, which is exactly how §8's "never re-card" is
    # enforced — there is nothing here to publish them from.
    bodies: list[str] = []
    for answer, digest in zip(live, docs):
        body = answer.output.synthesis_md.strip()
        verdict = responder.verdict_for(digest.label) if responder is not None else None
        card = bool(verdict is not None and verdict.card)
        if card and answer.refused:
            # Hard veto, D3. The ledger said no; there is no body to card.
            logger.info(
                "simple_search: vetoing the card for refused judgment %s — the "
                "responder granted one over a refusal line (§13l.5)",
                answer.group.key,
            )
            card = False
        if not card:
            bodies.append(_strip_citation_markers(body))
            continue

        obj = answer.group.objects[0] if answer.group.objects else None
        try:
            published = await publish_simple_search_result(
                supabase,
                user_id=user_id,
                conversation_id=conversation_id,
                case_id=case_id,
                message_id=None,
                # The fallback chain §6 pins: the responder's title, the
                # object's own, then the level noun. The publisher has one more
                # («نتيجة بحث») behind all three.
                title=(verdict.title or "").strip() or _object_title(obj),
                content_md=body,
                references=answer.references,
                cited_numbers=list(answer.output.used_refs),
                level=answer.group.level,
            )
        except Exception as exc:  # noqa: BLE001 — the answer is already written
            logger.warning("simple_search: publish failed for %s: %s", answer.group.key, exc)
            # No card ⇒ the body has nowhere else to live. Trap §11.4 again:
            # the answer ships either way, and its `[n]` now point at nothing.
            bodies.append(_strip_citation_markers(body))
            continue
        if published.item_id:
            created_item_ids.append(published.item_id)
            sse_events.extend(published.sse_events)
        else:
            bodies.append(_strip_citation_markers(body))

    # ── 7. Assembly (§9). ───────────────────────────────────────────────────
    chat_messages: list[str] = []
    if summary:
        chat_messages.append(summary)
    chat_messages.extend(bodies)
    # Suppressed twice on purpose: the prompt is told to leave it empty (a model
    # instruction), and a suggestion that arrives anyway is dropped here (the
    # guarantee). The searcher's question is about to follow this message.
    if suggestion and not suppress_suggestion:
        chat_messages.append(suggestion)

    if not chat_messages and degraded_fallback:
        # Every round rejected, or every synthesizer failed — so the responder
        # was never called (trap §11.3) and there is nothing to assemble. Say so
        # in a line written HERE, in Arabic, rather than returning an empty turn.
        chat_messages.append(degraded_text)

    unlock_notes = unlock_notes_payload(unlock_records or [])
    ack = unlock_acknowledgement(sum(1 for n in unlock_notes if n["charged"]))
    if ack and chat_messages:
        chat_messages[-1] = f"{chat_messages[-1].rstrip()}\n\n{ack}"
    elif ack:
        # Charged, and every answer it paid for was rejected — so the pause path
        # has nothing to append to. `unlock-01` + `unlock-02` in one turn: money
        # spent, nothing said. It gets said.
        chat_messages.append(ack)

    return SimpleSearchRunResult(
        chat_messages=chat_messages,
        created_item_ids=created_item_ids,
        sse_events=sse_events,
        unlock_notes=unlock_notes,
    )


# =========================================================================== #
# Resume (§13l.4) — the other half of a question that can be answered.
# =========================================================================== #


def _history_bytes(raw: Any) -> bytes:
    """``paused_runs.message_history`` → bytes, whatever PostgREST returned.

    BYTEA comes back as Postgres-native ``\\x``-prefixed hex by default; some
    serializers produce base64. Copied from ``orchestrator._resume_major_agent_inner``
    — the two must agree, because either can read a row the other wrote.
    """
    import base64

    if isinstance(raw, (bytes, bytearray)):
        return bytes(raw)
    if isinstance(raw, str):
        if raw.startswith("\\x"):
            return bytes.fromhex(raw[2:])
        return base64.b64decode(raw)
    raise ValueError(f"unexpected message_history type: {type(raw)}")


async def resume_simple_search(
    user_reply: str,
    pause_row: dict,
    supabase: "SupabaseClient",
    user_id: str,
    conversation_id: str,
    case_id: str | None,
    *,
    emit_sse: "Callable[[dict], Awaitable[None]] | None" = None,
    recent_messages: list | None = None,
    user_preferences: dict | None = None,
    welcome: "WelcomeState | None" = None,
) -> "SimpleSearchRunResult":
    """Resume a paused searcher with the user's reply — §13l.4.

    §13j #2: before this existed, ``ask_user`` was write-only machinery. The
    orchestrator had no simple_search branch on the resume leg AND the row
    carried no ``deferred_payload``, so a cooperative reply («الحكم الأول») was
    re-routed as a brand-new turn with the candidate list, the message history
    and the tool call destroyed.

    The mechanism is the planner's, verbatim in shape
    (``orchestrator._resume_major_agent_inner``): rehydrate the serialized
    ``message_history``, wrap the reply in ``DeferredToolResults`` keyed by the
    stored ``tool_call_id``, and hand both to a fresh searcher agent. What is
    ours is the **registry**: ``C1…Cn`` lives on ``SearcherDeps``, which is
    never persisted, so the pause row carries it and this function puts it back
    — without it a resumed case-C searcher selects a handle the output validator
    cannot resolve.

    From the searcher's answer onward this re-enters :func:`_answer_loop`, so a
    resumed decision fans out, unfolds, charges, synthesizes and publishes by
    exactly the same code as a fresh one.

    **Never raises.** A row that cannot be rehydrated (expired schema, missing
    tool_call_id, truncated history) degrades to a fresh lookup over the
    original question PLUS the reply — which is strictly better than an error
    and better than re-routing a bare «الحكم الأول» through the router.

    **A resumed searcher that asks AGAIN does not chain.** ``pause_row`` is
    still open while this runs (the caller resolves it afterwards, as
    ``_resume_major_agent_inner``'s ``finally`` does), so §13l.3 sees an
    occupied slot and delivers the second question inline. Deliberate: writing
    a second row here would leave two, and deleting the first would race the
    caller's own resolve.

    Args:
        user_reply: what the user answered the ``ask_user`` question with.
        pause_row: the ``paused_runs`` row (as ``find_open_pause`` returns it).
        emit_sse: same live-chip sink as the fresh path.

    Returns:
        A :class:`SimpleSearchRunResult`, same shape and same guarantees.
    """
    from pydantic_ai import DeferredToolResults
    from pydantic_ai.messages import ModelMessagesTypeAdapter

    payload = pause_row.get("deferred_payload") or {}
    if not isinstance(payload, dict):
        payload = {}
    turn_question = str(payload.get("question") or "").strip()
    reply = (user_reply or "").strip()
    # The question a resumed synthesizer answers: the original ask, plus what
    # the clarification settled. Either half alone is misleading — «الحكم
    # الأول» is not a question, and the original alone lost the answer.
    question = (
        f"{turn_question}\n\n(توضيح المستخدم: {reply})" if turn_question and reply
        else (turn_question or reply)
    )

    prefs = user_preferences or {}
    detail_level = str(prefs.get("detail_level") or "")
    welcome_instruction = render_welcome_instruction(welcome) or None
    # The same greeting as literal text — D7's fallback opens with it when
    # the responder never gets to (see `_finalise`).
    welcome_opening = compose_opening(welcome) if welcome else ""
    grant_judgment_access = judgment_access_resolver(supabase, user_id)

    deps = SearcherDeps(
        supabase=supabase,
        user_id=user_id,
        conversation_id=conversation_id,
        case_id=case_id,
        emit_sse=emit_sse,
        recent_messages=list(recent_messages or []),
    )
    # Put the handle registry back BEFORE the run: the resumed searcher's very
    # first output may name C2, and the output validator resolves against deps.
    restored = 0
    for handle, raw in (payload.get("candidates") or {}).items():
        try:
            deps.candidates[str(handle).upper()] = ResolvedObject.model_validate(raw)
            restored += 1
        except Exception:  # noqa: BLE001 — one bad candidate is not the turn
            logger.warning("simple_search: unrestorable candidate %s on resume", handle)
    deps.candidate_lines.extend(str(line) for line in (payload.get("candidate_lines") or []))
    skip_keys = {str(k) for k in (payload.get("answered_keys") or []) if k}

    first_run_kwargs: dict[str, Any] | None = None
    try:
        history = ModelMessagesTypeAdapter.validate_json(
            _history_bytes(pause_row.get("message_history"))
        )
        tool_call_id = str(payload.get("tool_call_id") or "")
        if not tool_call_id:
            raise ValueError("deferred_payload missing tool_call_id")
        first_run_kwargs = {
            # Empty prompt: the rehydrated history already carries the question,
            # the tool calls and the candidate previews (same as the planner's
            # resume leg, orchestrator.py:1060).
            "user_prompt": "",
            "message_history": history,
            "deferred_tool_results": DeferredToolResults(
                calls={tool_call_id: reply}
            ),
        }
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "simple_search: could not rehydrate pause %s — falling back to a "
            "fresh lookup over the original question + the reply: %s",
            pause_row.get("run_id"), exc, exc_info=True,
        )
        # The fresh leg must not skip the already-answered documents blindly:
        # without the history it may well be re-resolving them for the first
        # time. Keep the skip set — the answers were delivered either way.

    with track_stage(
        "simple_search.resume",
        conversation_id=conversation_id,
        case_id=case_id,
        agent_family=AGENT_FAMILY,
        question_chars=len(question or ""),
        rehydrated=first_run_kwargs is not None,
        candidates=restored,
    ) as span:
        return await _answer_loop(
            question=question,
            supabase=supabase,
            user_id=user_id,
            conversation_id=conversation_id,
            case_id=case_id,
            deps=deps,
            welcome_instruction=welcome_instruction,
            welcome_opening=welcome_opening,
            detail_level=detail_level,
            judgment_access=grant_judgment_access,
            recent_messages=recent_messages,
            span=span,
            first_run_kwargs=first_run_kwargs,
            skip_keys=skip_keys,
            turn_question=turn_question or question,
        )


__all__ = [
    "AGENT_FAMILY",
    "MAX_CYCLES",
    "SimpleSearchRunResult",
    "judgment_access_resolver",
    "run_simple_search",
    "resume_simple_search",
    "document_key",
    "group_documents",
    "build_references",
    "refusal_message",
    "resolved_from_attachment",
    "unlock_acknowledgement",
    "unlock_notes_payload",
    "unselected_candidate_lines",
    "pause_slot_is_taken",
]
