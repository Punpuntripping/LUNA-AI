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
    SimpleSearchLevel,
    UnfoldResult,
)
from agents.simple_search.prompts import (
    build_searcher_user_message,
    build_synthesizer_user_message,
)
from agents.simple_search.publisher import publish_simple_search_result
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
from agents.utils.welcome import render_welcome_instruction

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
    """Remove ``[n]`` markers from a reply that publishes no card.

    Only ever applied when ``wi_warranted`` is False — with a card, the markers
    are load-bearing and `[n]` is the anchor into المراجع. Collapses the space
    a removed marker leaves behind so «النص [1] .» does not become «النص  .».
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
    """One synthesizer's verdict on one document.

    ``refused`` marks the §13l.5 case: the ledger refused the ruling, so no
    synthesizer was built at all and ``output`` carries a line this module
    wrote, not one a model wrote.
    """

    group: _Group
    output: SynthesizerOutput
    references: list[Reference]
    refused: bool = False


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
    welcome_instruction: str | None,
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
    """
    access = judgment_access
    if unlock_records is not None:
        access = _recording_judgment_access(judgment_access, unlock_records, fanout_index)
    unfolds = [
        await unfold(supabase, obj, judgment_access=access)
        for obj in group.objects
    ]
    references = build_references(group, unfolds)

    if _is_refused_judgment(group, unfolds):
        logger.info(
            "simple_search: judgment access refused for %s — answering with the "
            "refusal line, NOT a synthesizer (§13l.5)", group.key,
        )
        return _Answer(
            group=group,
            output=SynthesizerOutput(
                synthesis_md=refusal_message(group, unfolds),
                # No body ⇒ nothing to put on a card, and the references would
                # point at a document the user cannot open.
                wi_warranted=False,
            ),
            references=references,
            refused=True,
        )

    agent = create_synthesizer_agent(group.level)
    result = await run_tracked(
        agent,
        build_synthesizer_user_message(
            question,
            unfolds,
            references,
            welcome_instruction=welcome_instruction,
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
    return _Answer(group=group, output=output, references=references)


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
        welcome: this turn's welcome state. Rendered into the FIRST synthesizer
            only — the greeting is the opening LINE of the answer, and three
            fan-out replies each opening with «أهلين» would read as a stutter.
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
    a second synthesizer for it.
    """
    answers: dict[str, _Answer] = {}
    unlock_records: list[_UnlockRecord] = []
    skip = set(skip_keys or ())
    dispatched = 0          # fan-out slots handed out this turn — the note order
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
            delivered = await _finalise(
                list(answers.values()),
                supabase=supabase,
                user_id=user_id,
                conversation_id=conversation_id,
                case_id=case_id,
                unlock_records=unlock_records,
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
                delivered.paused = True
                delivered.question_text = q or None
            else:
                # No row ⇒ nothing can consume a reply to this question, so it
                # must not be emitted as an `agent_question`: it goes out as the
                # last chat message and the next turn routes fresh.
                if q:
                    delivered.chat_messages.append(q)
                if not delivered.chat_messages:
                    delivered.chat_messages.append(_DEGRADED_AR)
            span.set(
                outcome="paused" if run_id else "asked_inline",
                cycles=cycles,
                delivered=len(delivered.chat_messages),
            )
            return delivered

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

        todo = [
            g for g in group_documents(output.resolved)
            if g.key not in answers and g.key not in skip
        ]
        if not todo:
            break

        round_answers = await _run_round(
            todo,
            supabase=supabase,
            question=question,
            conversation_id=conversation_id,
            case_id=case_id,
            # Only the first synthesizer of the turn carries the greeting.
            welcome_instruction=welcome_instruction if not answers else None,
            detail_level=detail_level,
            judgment_access=judgment_access,
            recent_messages=recent_messages,
            unlock_records=unlock_records,
            fanout_base=dispatched,
        )
        dispatched += len(todo)

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
        unlock_records=unlock_records,
        # A resume that re-selects only documents the paused leg already
        # answered has nothing new to say — and «لم أتمكّن من تأكيد المصدر»
        # would be a lie about work that succeeded.
        degraded_text=_ALREADY_ANSWERED_AR if (skip and not answers) else _DEGRADED_AR,
    )


async def _run_round(
    groups: list[_Group],
    *,
    supabase: Any,
    question: str,
    conversation_id: str,
    case_id: str | None,
    welcome_instruction: str | None,
    detail_level: str,
    judgment_access: JudgmentAccessResolver,
    recent_messages: list | None = None,
    unlock_records: list[_UnlockRecord] | None = None,
    fanout_base: int = 0,
) -> list[_Answer]:
    """Run one synthesizer per document, concurrently, in fan-out order."""
    if not groups:
        return []
    tasks = [
        _synthesize_group(
            group,
            supabase=supabase,
            question=question,
            conversation_id=conversation_id,
            case_id=case_id,
            # The welcome line belongs to the first reply the user reads.
            welcome_instruction=welcome_instruction if i == 0 else None,
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


async def _finalise(
    answers: list[_Answer],
    *,
    supabase: Any,
    user_id: str,
    conversation_id: str,
    case_id: str | None,
    unlock_records: list[_UnlockRecord] | None = None,
    degraded_fallback: bool = True,
    degraded_text: str = _DEGRADED_AR,
) -> SimpleSearchRunResult:
    """Publish the warranted cards and assemble the C1 result.

    One chat message per synthesizer, in fan-out order. ``created_item_ids`` may
    be shorter than ``chat_messages``: not every lookup deserves a card, and a
    publish failure degrades to a chat-only answer rather than losing it.

    ``degraded_fallback`` is False on the pause path: there, an empty answer set
    is not a failure — the searcher's question IS the turn's message, and
    «لم أتمكّن من تأكيد المصدر» in front of it would contradict it.

    §13l.6 — when the ledger was charged this turn, ONE Arabic line naming how
    many rulings were opened is appended to the LAST chat message. Last, not
    first: the acknowledgment is a footer, and it is appended after the cards
    are published so the durable card never carries a billing line.
    """
    chat_messages: list[str] = []
    created_item_ids: list[str] = []
    sse_events: list[dict] = []

    for answer in answers:
        body = answer.output.synthesis_md.strip()
        if not body:
            continue
        if not answer.output.wi_warranted:
            # No card ⇒ no ``workspace_item_references`` rows ⇒ every ``[n]`` in
            # this body points at nothing. The two fields are independent by
            # design: ``_CITATION_RULES`` requires ``[n]`` unconditionally while
            # ``wi_warranted`` is the synthesizer's own "is this worth a card"
            # call — so a not-worth-a-card answer is *invited* to cite and then
            # ships uncarded. Observed live in the Case-B eval: a ``[1]`` in a
            # chat reply with no panel behind it. Strip the markers rather than
            # suppress the answer: the prose is still correct, and a dead
            # citation is worse than none.
            chat_messages.append(_strip_citation_markers(body))
            continue
        chat_messages.append(body)
        try:
            published = await publish_simple_search_result(
                supabase,
                user_id=user_id,
                conversation_id=conversation_id,
                case_id=case_id,
                message_id=None,
                title=answer.output.wi_title or answer.group.objects[0].title,
                content_md=body,
                references=answer.references,
                cited_numbers=list(answer.output.used_refs),
                level=answer.group.level,
            )
        except Exception as exc:  # noqa: BLE001 — the answer is already written
            logger.warning("simple_search: publish failed for %s: %s", answer.group.key, exc)
            continue
        if published.item_id:
            created_item_ids.append(published.item_id)
            sse_events.extend(published.sse_events)

    if not chat_messages and degraded_fallback:
        # Every round rejected, or every synthesizer failed. Say so in Arabic
        # rather than returning an empty turn.
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
    "pause_slot_is_taken",
]
