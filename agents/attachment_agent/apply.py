"""Write the staged edits — only after the run's output passed validation.

Plan §2.2: edit tools stage (``edit_tools.py``); the runner calls
:func:`apply_staged` once the model's output is accepted and its ``request`` is
``change`` / ``reanalyze``. A run that errors or times out never gets here, so it
changes nothing.

Order: as staged, except (1) gap removals of a map come after its other edits,
highest number first — a removal renumbers the gaps after it, and every gap edit
was staged against the numbers the model saw; (2) a re-analysis starts last, so
it reads the text fixes and edits made in the same message.

Writes go through the storage APIs with REAL text: text fixes were decoded when
staged; angle-map edits decode inside ``angle_mapper.edits``; evidence is
decoded here. Each change gives one Arabic line (``result.edits``) — applied
lines in the past tense, failures as «لم يُنفَّذ: … — reason». The lines go to
the router (a model), so they are ENCODED with the turn codec at the end and any
new fake is persisted. Never raises: a change that throws becomes a failure line.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any

from agents.memory.angle_mapper import edits as E
from backend.app.services.masking_service import active_codec, persist_new_mappings
from agents.utils.history import neutralize_system_brackets
from shared.attachments.derived import fix_derived_wording, save_derived, undo_derived
from shared.attachments.segment_overrides import revert_segment_override, save_segment_override
from shared.attachments.text_corrections import revert_text_correction, save_text_correction

from .context import _enc, turn_encoder
from .deps import AttachmentDeps, StagedEdit, ThreadMap
from .edit_tools import section_of_ref

logger = logging.getLogger(__name__)

#: Around the re-analysis start (it only validates and schedules).
REANALYSIS_START_TIMEOUT_S = 30

FIELD_AR: dict[str, str] = {
    "title": "العنوان", "argument": "الحجة", "strength": "القوة", "weakness": "نقطة الضعف",
    "legal_question": "المسألة القانونية", "for_side": "الصفة", "for_party": "الطرف", "kind": "النوع",
    "sections": "الأقسام", "ocr_suspect": "احتمال خطأ القراءة", "issue": "الوصف", "side": "الصفة",
    "party": "الطرف", "basis": "الأساس", "confidence": "درجة التأكد",
}
VALUE_AR: dict[Any, str] = {
    "strong": "قوية", "medium": "متوسطة", "weak": "ضعيفة",
    "strengthen": "تقوية", "attack": "هجوم", "answer": "رد",
    "missing": "نقص", "contradiction": "تعارض", "unanswered": "بلا رد",
    "claimant": "المدعي", "respondent": "المدعى عليه", "prosecution": "الادعاء العام", "victim": "المجني عليه",
    "intervenor": "المتدخل", "contract_party": "طرف في العقد", "unknown": "غير محدد",
    "clear": "واضح", "likely": "مرجّح", "unclear": "غير واضح",
}

R_NOT_SAVED = "تعذّر الحفظ الآن؛ لم يتغير شيء."
R_UNDO_GONE = "لم يُعثر على هذا التعديل أو سبق التراجع عنه."
R_FAILED = "تعذّر تنفيذ التعديل الآن."
R_REANALYSIS_OFF = "إعادة التحليل غير متاحة الآن."
#: After undoing a text fix: wording derived from it that changed since, so kept.
R_DERIVED_KEPT = "لم يُستعد ما تغيّر بعد التصحيح في"
LINE_REANALYSIS = "بدأت إعادة تحليل الخيوط، وتظهر النتيجة خلال دقائق"


@dataclass
class ApplyReport:
    #: One line per applied change (encoded once :func:`apply_staged` returns).
    applied: list[str] = field(default_factory=list)
    #: One line per change that could not be made, with its reason.
    failed: list[str] = field(default_factory=list)
    #: Re-analyses started (their lines are in ``applied``).
    reanalysis_started: int = 0

    @property
    def edits_applied(self) -> int:
        """Changes written, re-analysis starts not counted."""
        return len(self.applied) - self.reanalysis_started

    @property
    def lines(self) -> list[str]:
        return self.applied + self.failed


# ---------------------------------------------------------------------------
# Wording
# ---------------------------------------------------------------------------


def _cut(text: Any, limit: int = 120) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[:limit].rstrip() + "…"


def _places(n: int) -> str:
    if n == 1:
        return "موضع واحد"
    if n == 2:
        return "موضعين"
    return f"{n} مواضع" if 3 <= n <= 10 else f"{n} موضعًا"


#: Fields whose values are codes (shown in Arabic), not free text.
_CODED = frozenset({"strength", "kind", "for_side", "side", "confidence"})


def _value(field_name: str, v: Any) -> str:
    if isinstance(v, bool):
        return "نعم" if v else "لا"
    if field_name in _CODED:
        return VALUE_AR.get(v, str(v))
    return f"«{_cut(v)}»"


def _ref_names(deps: AttachmentDeps, tmap: ThreadMap | None, refs: list[Any]) -> str:
    named: list[str] = []
    other = 0
    for ref in refs:
        hit = section_of_ref(deps, tmap, ref) if tmap is not None else None
        if hit is None:
            other += 1
        else:
            named.append(f"«{_cut(hit[1].what or hit[1].label, 60)}»")
    if other:
        named.append(f"{other} قسم" if other == 1 else f"{other} أقسام")
    return "، ".join(named)


def _changes(deps: AttachmentDeps, tmap: ThreadMap | None, before: Any, after: Any) -> str:
    before = before if isinstance(before, dict) else {}
    after = after if isinstance(after, dict) else {}
    parts: list[str] = []
    for k, new in after.items():
        old = before.get(k)
        if k == "confidence" or new == old:
            continue
        if k == "sections":
            added = [r for r in new or [] if r not in (old or [])]
            removed = [r for r in old or [] if r not in (new or [])]
            if added:
                parts.append(f"أُضيف إليه {_ref_names(deps, tmap, added)}")
            if removed:
                parts.append(f"حُذف منه {_ref_names(deps, tmap, removed)}")
            continue
        parts.append(f"{FIELD_AR.get(k, k)} → {_value(k, new)}")
    return "؛ ".join(parts)


def _map(deps: AttachmentDeps, map_id: Any) -> ThreadMap | None:
    return next((t for t in deps.maps if t.map_id == str(map_id)), None)


def describe(e: StagedEdit) -> str:
    """What the change was, for a failure line."""
    a = e.args
    return {
        "promote": f"ترقية القسم «{_cut(a.get('what'), 60)}» إلى جوهري",
        "fix_text": f"تصحيح «{_cut(a.get('wrong'), 60)}» إلى «{_cut(a.get('right'), 60)}»",
        "edit_thread": f"تعديل الخيط «{_cut(a.get('title'), 80)}»",
        "add_thread": f"إضافة الخيط «{_cut(a.get('title'), 80)}»",
        "remove_thread": f"حذف الخيط «{_cut(a.get('title'), 80)}»",
        "add_gap": f"إضافة ثغرة: {_cut(a.get('issue'), 80)}",
        "edit_gap": f"تعديل الثغرة «{_cut(a.get('issue'), 80)}»",
        "remove_gap": f"حذف الثغرة «{_cut(a.get('issue'), 80)}»",
        "edit_client": "تعديل الموكّل",
        "undo": f"التراجع عن: {a.get('line') or 'تعديل سابق'}",
        "reanalysis": "إعادة تحليل الخيوط",
    }.get(e.kind, "التعديل")


def _failed(e: StagedEdit, reason: str) -> str:
    return f"لم يُنفَّذ: {describe(e)} — {reason or R_FAILED}"


def _real(text: str) -> str:
    """The user's words for a log column, decoded best-effort (never refused)."""
    codec = active_codec()
    if codec is None or not text:
        return text
    try:
        return codec.decode(text).text
    except Exception:  # noqa: BLE001
        return text


# ---------------------------------------------------------------------------
# One change
# ---------------------------------------------------------------------------


def _apply_one(deps: AttachmentDeps, e: StagedEdit) -> tuple[bool, str]:
    sb, a = deps.supabase, e.args
    who = {"user_id": deps.user_id, "conversation_id": deps.conversation_id, "message_id": deps.message_id}
    several = len(deps.files) > 1

    if e.kind == "promote":
        ok = save_segment_override(sb, file_id=a["file_id"], char_start=a["char_start"], char_end=a["char_end"],
                                   evidence=_real(e.evidence), **who)
        where = f" في «{a.get('title')}»" if several else ""
        return (True, f"رُقّي القسم «{_cut(a.get('what'), 80)}» إلى جوهري{where}") if ok else (False, _failed(e, R_NOT_SAVED))

    if e.kind == "fix_text":
        cid = save_text_correction(sb, file_id=a["file_id"], wrong=a["wrong"], right=a["right"],
                                   occurrences=a["count"], evidence=_real(e.evidence), **who)
        if not cid:
            return False, _failed(e, R_NOT_SAVED)
        changes: list = []
        fix_derived_wording(sb, file_id=a["file_id"], user_id=deps.user_id, wrong=a["wrong"], right=a["right"],
                            evidence=_real(e.evidence), message_id=deps.message_id, changes=changes)
        if changes:
            save_derived(sb, cid, deps.user_id, changes)   # what an undo puts back
        return True, f"صُحّح «{a['wrong']}» إلى «{a['right']}» في {_places(int(a['count']))} من «{a.get('title')}»"

    if e.kind == "undo":
        source = a.get("source")
        if source == "text":
            ok, reason, warning = revert_text_correction(sb, a["id"], deps.user_id), R_UNDO_GONE, ""
            if ok:
                missed = undo_derived(sb, a["id"], deps.user_id, evidence=_real(e.evidence),
                                      conversation_id=deps.conversation_id, message_id=deps.message_id)
                warning = f"{R_DERIVED_KEPT}: {'، '.join(missed)}" if missed else ""
        elif source == "promote":
            ok, reason, warning = revert_segment_override(sb, a["id"], deps.user_id), R_UNDO_GONE, ""
        else:
            r = E.undo_edit(sb, a["id"], deps.user_id, evidence=e.evidence,
                            conversation_id=deps.conversation_id, message_id=deps.message_id)
            ok, reason, warning = r.ok, r.reason, r.warning
        if not ok:
            return False, _failed(e, reason)
        return True, f"تُرُوجع عن: {a.get('line')}" + (f" — {warning}" if warning else "")

    tmap = _map(deps, a.get("map_id"))
    src = deps.read_source()
    if e.kind == "edit_thread":
        r = E.edit_angle(sb, a["map_id"], a["seq"], a["fields"], evidence=e.evidence, source=src, **who)
        line = f"عُدّل الخيط «{_cut(a.get('title'), 80)}»: {_changes(deps, tmap, r.before, r.after)}"
    elif e.kind == "add_thread":
        r = E.add_angle(sb, a["map_id"], a["fields"], evidence=e.evidence, source=src, **who)
        line = f"أُضيف خيط: «{_cut((r.after or {}).get('title') or a.get('title'), 80)}»"
    elif e.kind == "remove_thread":
        r = E.remove_angle(sb, a["map_id"], a["seq"], evidence=e.evidence, **who)
        line = f"حُذف الخيط «{_cut(a.get('title'), 80)}»"
    elif e.kind == "add_gap":
        r = E.add_gap(sb, a["map_id"], a["fields"], evidence=e.evidence, source=src, **who)
        line = f"أُضيفت ثغرة: {_cut((r.after or {}).get('issue') or a.get('issue'))}"
    elif e.kind == "edit_gap":
        r = E.edit_gap(sb, a["map_id"], a["index"], a["fields"], evidence=e.evidence, source=src, **who)
        line = f"عُدّلت الثغرة «{_cut(a.get('issue'), 80)}»: {_changes(deps, tmap, r.before, r.after)}"
    elif e.kind == "remove_gap":
        r = E.remove_gap(sb, a["map_id"], a["index"], evidence=e.evidence, **who)
        line = f"حُذفت الثغرة: {_cut(a.get('issue'))}"
    elif e.kind == "edit_client":
        r = E.edit_client(sb, a["map_id"], a["fields"], evidence=e.evidence, source=src, **who)
        line = f"عُدّل الموكّل: {_changes(deps, tmap, r.before, r.after)}"
    else:
        return False, _failed(e, R_FAILED)
    if not r.ok:
        return False, _failed(e, r.reason)
    return True, line + (f" — {r.warning}" if r.warning else "")


def _ordered(staged: list[StagedEdit]) -> list[StagedEdit]:
    def key(pair: tuple[int, StagedEdit]) -> tuple:
        i, e = pair
        if e.kind == "reanalysis":
            return (2, 0, i)
        if e.kind == "remove_gap":
            return (1, -int(e.args.get("index") or 0), i)
        return (0, 0, i)

    return [e for _i, e in sorted(enumerate(staged), key=key)]


def _apply_writes(deps: AttachmentDeps, staged: list[StagedEdit], report: ApplyReport) -> None:
    for e in staged:
        try:
            ok, line = _apply_one(deps, e)
        except Exception as exc:  # noqa: BLE001 — one change failing never stops the rest
            logger.warning("attachment_agent: %s not applied (%s)", e.kind, type(exc).__name__, exc_info=True)
            ok, line = False, _failed(e, R_FAILED)
        (report.applied if ok else report.failed).append(line)


async def _start_reanalysis(deps: AttachmentDeps, e: StagedEdit, report: ApplyReport) -> None:
    try:
        from agents.memory.angle_mapper.reanalysis import start_reanalysis
    except Exception as exc:  # noqa: BLE001 — not deployed with this build
        logger.warning("attachment_agent: re-analysis unavailable (%s)", type(exc).__name__)
        report.failed.append(_failed(e, R_REANALYSIS_OFF))
        return
    # As the model wrote it (masked): start_reanalysis decodes it itself.
    critique = str(e.args.get("critique") or e.evidence)
    started = 0
    for target in e.args.get("targets") or []:
        try:
            res = await asyncio.wait_for(
                start_reanalysis(
                    deps.supabase, item_ids=list(target.get("item_ids") or []), user_id=deps.user_id,
                    conversation_id=deps.conversation_id, message_id=deps.message_id, critique=critique,
                ),
                REANALYSIS_START_TIMEOUT_S,
            )
        except Exception as exc:  # noqa: BLE001 — incl. the timeout
            logger.warning("attachment_agent: re-analysis start failed (%s)", type(exc).__name__)
            report.failed.append(_failed(e, R_FAILED))
            continue
        if getattr(res, "ok", False):
            started += 1
        else:
            report.failed.append(_failed(e, str(getattr(res, "reason", "") or R_FAILED)))
    if started:
        report.applied.append(LINE_REANALYSIS)
        report.reanalysis_started += 1


async def apply_staged(deps: AttachmentDeps) -> ApplyReport:
    """Write every staged edit; the report's lines are encoded. Never raises."""
    report = ApplyReport()
    staged = _ordered(list(deps.staged_edits))
    writes = [e for e in staged if e.kind != "reanalysis"]
    try:
        if writes:
            await asyncio.to_thread(_apply_writes, deps, writes, report)
        for e in staged:
            if e.kind == "reanalysis":
                await _start_reanalysis(deps, e, report)
    except Exception as exc:  # noqa: BLE001
        logger.warning("attachment_agent: applying edits failed (%s)", type(exc).__name__, exc_info=True)
    encode = turn_encoder()
    # Masked like everything the router reads; brackets neutralized like quotes and notes
    # (a line can carry the user's or the file's words).
    report.applied = [neutralize_system_brackets(_enc(encode, line)) for line in report.applied]
    report.failed = [neutralize_system_brackets(_enc(encode, line)) for line in report.failed]
    try:
        await asyncio.to_thread(persist_new_mappings, deps.supabase, deps.user_id, active_codec())
    except Exception:  # noqa: BLE001 — a fake not persisted only weakens decoding later
        logger.warning("attachment_agent: masking mappings not persisted for the edit lines")
    return report


__all__ = ["ApplyReport", "FIELD_AR", "LINE_REANALYSIS", "VALUE_AR", "apply_staged", "describe"]
