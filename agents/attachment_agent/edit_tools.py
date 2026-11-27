"""The edit tools (plan §2.2 "Edit tools", D6–D9, D12–D20).

Each tool STAGES a change in ``deps.staged_edits`` after checking what can be
checked without writing — the evidence, refs, the thread / gap, the text fix
(``check_text_fix``), decoding (``decode_for_store``). Nothing is written here:
``apply.apply_staged`` writes after the run's output passes validation, so a run
that fails changes nothing.

The guard every tool runs first: ``evidence`` (the user's words asking for the
change) must occur in the CURRENT user message — compared in the masked space the
model read, folded with ``citation_scan.fold`` and whitespace collapsed. No match
→ a refusal string, nothing staged. That makes "only what the user asked" (D7, D9)
a rule code enforces.

Free plan / no analysis (D20): the thread, gap, client and re-analysis tools
refuse with «الخيوط غير متاحة لهذا الملف»; text fixes and promotions still work.

Tool returns go to the model: short English lines, the storage APIs' Arabic
reasons passed through as they are.
"""
from __future__ import annotations

import asyncio
import html
import logging
import re
from typing import Any, Literal, Optional, Sequence

from pydantic import BaseModel, Field
from pydantic_ai import Agent, RunContext

from agents.memory.angle_mapper.edits import R_FAKE
from agents.memory.angle_mapper.refs import format_ref, parse_ref
from agents.tool_repository.citation_scan import find_folded, fold
from shared.attachments.text_corrections import REASON_NOT_FOUND, check_text_fix, decode_for_store

from .deps import AttachmentDeps, FileDoc, RecentEdit, SectionRow, StagedEdit, ThreadMap
from .tools import parse_refs

logger = logging.getLogger(__name__)

#: D20 — the user-facing reason when a file has no analysis «الخيوط».
NO_THREADS = "الخيوط غير متاحة لهذا الملف."
#: Fewest words the evidence may have.
MIN_EVIDENCE_WORDS = 2
#: Rows list_recent_edits shows.
RECENT_EDITS_MAX = 15
_ARG_MAX_CHARS = 120

REFUSED_EVIDENCE = (
    "Refused, nothing staged: `evidence` must be the user's own words that ASK for this change — at "
    "least two words, copied exactly from <current_message>, including the asking word itself (e.g. "
    "عدّل، صحّح، احذف، أضف، خلّ، اجعل، تراجع، أعد، الصحيح، مو، مهم، ماعجبني). A question about the files is not "
    "a request to change them. If the message does not ask for the change in so many words, answer "
    "needs_clarification and ask the user to state the change explicitly."
)

# ── the words that make a message ask for a change (FOLDED: no tashkeel, ا for
#    أ/إ/آ, ه for ة, ي for ى). A word counts with a leading و / ف too. ───────────
#: Imperatives — a word STARTING with one (object suffixes: احذفها، صححه، خليه).
CUE_PREFIXES: frozenset[str] = frozenset({
    "غير", "صحح", "احذف", "امسح", "شيل", "اضف", "زود", "ارفع", "رقي", "اجعل", "بدل",
    "استبدل", "اعيد", "تراجع", "رجع", "خلي", "ركز", "جوهري",
})
#: Whole words: short imperatives, correction markers, importance, dislike. Kept
#: whole where a prefix would hit a common legal word (اعدام، إهمال، كاتب عدل).
CUE_WORDS: frozenset[str] = frozenset({
    "عدل", "عدله", "عدلها", "عدلوا", "اعد", "اعده", "اعدها", "خل", "خله", "رق", "الغ",
    "الصحيح", "صحيح", "مو", "ليس", "بدال", "غلط", "الغلط", "خطا", "الخطا",
    "مهم", "مهمه", "مهمين", "اهم", "الاهم", "اهميه", "ماعجبني", "ماعجبنا",
})
#: Two-word cues.
CUE_PHRASES: tuple[str, ...] = ("ما عجبني", "ما عجبنا", "مو زين")

#: An undo that touches the gaps, or restores a whole map, cannot share a message
#: with other edits there: the gap numbers (or the map) would change under them.
R_MIX_GAPS = (
    "لا يُجمع في رسالة واحدة بين التراجع عن تعديل في الثغرات وتعديل ثغرات أخرى؛ "
    "يُطلب كلٌّ منهما في رسالة مستقلة."
)
R_MIX_SNAPSHOT = (
    "التراجع عن إعادة التحليل يعيد الخيوط كلها كما كانت، فلا يُجمع مع تعديل آخر في الخيوط في الرسالة نفسها."
)
_GAP_EDITS = frozenset({"add_gap", "edit_gap", "remove_gap"})
_MAP_EDITS = frozenset({"edit_thread", "add_thread", "remove_thread", "add_gap", "edit_gap", "remove_gap",
                        "edit_client", "reanalysis"})
#: Log kinds (``attachment_angle_map_edits.kind``) of a gap change.
_GAP_LOG_KINDS = frozenset({"add_gap", "edit_gap", "remove_gap"})

_QUOTES = "«»\"'“”‘’()[]{}"
_EDGE = _QUOTES + " .,،؛;:!?؟…-"
_THREAD_RE = re.compile(r"^\s*\[?\s*(?:M\s*0*(\d+)\s*[·.\-_:/ ]*)?A\s*0*(\d+)\s*\]?\s*$", re.IGNORECASE)
_GAP_RE = re.compile(r"^\s*\[?\s*(?:M\s*0*(\d+)\s*[·.\-_:/ ]*)?G\s*0*(\d+)\s*\]?\s*$", re.IGNORECASE)
_MAP_RE = re.compile(r"^\s*\[?\s*M\s*0*(\d+)\s*\]?\s*$", re.IGNORECASE)
_EDIT_RE = re.compile(r"^\s*\[?\s*E\s*0*(\d+)\s*\]?\s*$", re.IGNORECASE)

Side = Literal["claimant", "respondent", "prosecution", "victim", "intervenor", "contract_party", "unknown"]


# ---------------------------------------------------------------------------
# What an edit may change (typed, so the model sees the allowed values)
# ---------------------------------------------------------------------------


class ThreadFields(BaseModel):
    """Only the fields the user asked to change; leave the rest empty."""

    title: Optional[str] = None
    argument: Optional[str] = None
    strength: Optional[Literal["strong", "medium", "weak"]] = None
    weakness: Optional[str] = None
    legal_question: Optional[str] = None
    kind: Optional[Literal["strengthen", "attack", "answer"]] = None
    for_side: Optional[Side] = None
    for_party: Optional[str] = None
    sections: Optional[list[str]] = Field(default=None, description="Replace the sections: refs as F1·S7.")
    add_sections: Optional[list[str]] = Field(default=None, description="Sections to add, e.g. ['F1·S9'].")
    remove_sections: Optional[list[str]] = Field(default=None, description="Sections to drop.")


class GapFields(BaseModel):
    """Only the fields the user asked to change; leave the rest empty."""

    issue: Optional[str] = None
    kind: Optional[Literal["missing", "contradiction", "unanswered"]] = None
    sections: Optional[list[str]] = Field(default=None, description="Replace the sections: refs as F1·S7.")
    add_sections: Optional[list[str]] = None
    remove_sections: Optional[list[str]] = None


class ClientFields(BaseModel):
    """The client line «الموكّل». Only what the user named."""

    side: Optional[Side] = None
    party: Optional[str] = None
    basis: Optional[str] = None


_TEXT_FIELDS = ("title", "argument", "weakness", "legal_question", "for_party", "issue", "party", "basis")
_SECTION_KEYS = ("sections", "add_sections", "remove_sections")


# ---------------------------------------------------------------------------
# The evidence guard
# ---------------------------------------------------------------------------


_QUOTE_TABLE = str.maketrans({ch: " " for ch in _QUOTES})


def _norm(text: Any) -> str:
    return " ".join(fold(html.unescape(str(text or ""))).translate(_QUOTE_TABLE).casefold().split())


def _is_cue(word: str) -> bool:
    for w in (word, word[1:] if len(word) > 2 and word[0] in "وف" else ""):
        if w and (w in CUE_WORDS or any(w.startswith(p) for p in CUE_PREFIXES)):
            return True
    return False


def asks_for_change(text: Any) -> bool:
    """``text`` (folded or not) holds a word that asks for a change."""
    norm = _norm(text)
    if any(f" {p} " in f" {norm} " for p in CUE_PHRASES):
        return True
    return any(_is_cue(w) for w in re.findall(r"\w+", norm))


def evidence_ok(deps: AttachmentDeps, evidence: Any) -> bool:
    """``evidence`` is at least two words, asks for a change (a cue word —
    :data:`CUE_PREFIXES` / :data:`CUE_WORDS` / :data:`CUE_PHRASES`) and occurs in
    the current user message (masked, folded, quote marks ignored, spaces
    collapsed; edge punctuation around it ignored). A question about the files
    («وش قوة الخيط الثاني؟») never yields an edit."""
    ev = _norm(evidence).strip(_EDGE)
    if len(re.findall(r"\w+", ev)) < MIN_EVIDENCE_WORDS or not asks_for_change(ev):
        return False
    return ev in _norm(deps.user_message)


def _stage(deps: AttachmentDeps, kind: str, evidence: str, **args: Any) -> StagedEdit | None:
    """Stage one change; ``None`` when the same change (kind + every arg) is
    already staged — a repeated call never applies twice."""
    if any(e.kind == kind and e.args == args for e in deps.staged_edits):
        return None
    edit = StagedEdit(kind=kind, evidence=str(evidence or "").strip(), args=args)
    deps.staged_edits.append(edit)
    return edit


ALREADY_STAGED = "Already staged, nothing added: the same change is staged once."


def _undo_conflict(deps: AttachmentDeps, *, undo_kind: str = "", edit_kind: str = "") -> str:
    """Why an undo and another map edit cannot share this message (``""`` = they can)."""
    if undo_kind:
        staged = {e.kind for e in deps.staged_edits if e.kind != "undo"}
        if undo_kind == "snapshot" and staged & _MAP_EDITS:
            return R_MIX_SNAPSHOT
        if undo_kind in _GAP_LOG_KINDS and staged & _GAP_EDITS:
            return R_MIX_GAPS
        return ""
    undone = {e.args.get("map_kind") for e in deps.staged_edits if e.kind == "undo"}
    if "snapshot" in undone and edit_kind in _MAP_EDITS:
        return R_MIX_SNAPSHOT
    if edit_kind in _GAP_EDITS and undone & _GAP_LOG_KINDS:
        return R_MIX_GAPS
    return ""


def _refused(reason: str) -> str:
    return f"Refused, nothing staged: {reason} Put this in note."


def _staged(deps: AttachmentDeps, kind: str, **match: Any) -> bool:
    return any(
        e.kind == kind and all(e.args.get(k) == v for k, v in match.items()) for e in deps.staged_edits
    )


def _cut(text: Any, limit: int = _ARG_MAX_CHARS) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[:limit].rstrip() + "…"


def _decodes(deps: AttachmentDeps, values: Sequence[Any]) -> bool:
    """Every text decodes with no fake left (``decode_for_store``)."""
    source = deps.read_source()
    return all(decode_for_store(str(v), source=source) is not None for v in values if str(v or "").strip())


# ---------------------------------------------------------------------------
# Maps, threads, gaps and refs
# ---------------------------------------------------------------------------


def _map_of_file(deps: AttachmentDeps, f: FileDoc) -> ThreadMap | None:
    return next((m for m in deps.maps if f.item_id in m.doc_of_item), None)


def pick_map(deps: AttachmentDeps, analysis: Any = None) -> tuple[ThreadMap | None, str]:
    """The analysis an edit targets: ``M2``, a file label (its map), or the only one."""
    if not deps.maps:
        return None, NO_THREADS
    text = str(analysis or "").strip()
    if text:
        m = _MAP_RE.match(text)
        if m:
            found = next((t for t in deps.maps if t.label == f"M{int(m.group(1))}"), None)
            if found is None and len(deps.maps) == 1 and int(m.group(1)) == 1:
                found = deps.maps[0]
            return (found, "") if found else (None, f"Unknown analysis {text!r}: {_labels(deps)}.")
        f = deps.file(text)
        if f is None:
            return None, f"Unknown analysis {text!r}: {_labels(deps)}."
        found = _map_of_file(deps, f)
        return (found, "") if found else (None, NO_THREADS)
    if len(deps.maps) == 1:
        return deps.maps[0], ""
    return None, f"Several analyses are shown: say which one ({_labels(deps)})."


def _labels(deps: AttachmentDeps) -> str:
    return ", ".join(t.label or "M1" for t in deps.maps) or "none"


def _resolve(deps: AttachmentDeps, label: Any, regex: re.Pattern, has: Any, what: str) -> tuple[ThreadMap | None, int, str]:
    if not deps.maps:
        return None, 0, NO_THREADS
    m = regex.match(str(label or ""))
    if not m:
        return None, 0, f"Unknown {what} {label!r}: use the label <threads> shows, e.g. {'A2' if what == 'thread' else 'G1'}."
    n = int(m.group(2))
    if m.group(1):
        name = f"M{int(m.group(1))}"
        cands = [t for t in deps.maps if t.label == name or (not t.label and name == "M1")]
    else:
        cands = deps.maps if len(deps.maps) == 1 else [t for t in deps.maps if has(t, n)]
    if not cands:
        return None, 0, f"No {what} {label!r} in <threads>."
    if len(cands) > 1:
        options = " or ".join(f"{t.label}·{'A' if what == 'thread' else 'G'}{n}" for t in cands)
        return None, 0, f"{label!r} is in several analyses: say {options}."
    tmap = cands[0]
    if not has(tmap, n):
        return None, 0, f"No {what} {label!r} in <threads>."
    return tmap, n, ""


def resolve_thread(deps: AttachmentDeps, label: Any) -> tuple[ThreadMap | None, int, str]:
    """``A2`` / ``M1·A2`` → (map, seq) — or the reason it does not resolve."""
    return _resolve(deps, label, _THREAD_RE, lambda t, n: t.angle(n) is not None, "thread")


def resolve_gap(deps: AttachmentDeps, label: Any) -> tuple[ThreadMap | None, int, str]:
    """``G1`` / ``M2·G1`` → (map, 1-based index)."""
    return _resolve(deps, label, _GAP_RE, lambda t, n: 1 <= n <= len(t.gaps), "gap")


def map_refs(deps: AttachmentDeps, tmap: ThreadMap, refs: Sequence[Any] | None) -> tuple[list[str], list[str]]:
    """The agent's refs (``F1·S7``, runs, bare ``S7``) → the map's ``D{doc}.S{seq}``
    (order kept, repeats dropped); plus what did not resolve or is in no file of
    this analysis."""
    wanted, bad = parse_refs(deps, [str(r) for r in refs or []])
    out: list[str] = []
    for f, s in wanted:
        doc = tmap.doc_of_item.get(f.item_id)
        if not doc:
            bad.append(f"{f.ref(s)} (not in this analysis)")
            continue
        ref = format_ref(doc, s.seq)
        if ref not in out:
            out.append(ref)
    return out, bad


def section_of_ref(deps: AttachmentDeps, tmap: ThreadMap, ref: Any) -> tuple[FileDoc, SectionRow] | None:
    """A map ref ``D2.S7`` → the loaded file and section, when given here."""
    parsed = parse_ref(ref)
    if parsed is None:
        return None
    item = next((i for i, d in tmap.doc_of_item.items() if d == parsed[0]), None)
    f = next((x for x in deps.files.values() if x.item_id == item), None)
    s = f.section(parsed[1]) if f else None
    return (f, s) if f and s else None


def _map_for_refs(deps: AttachmentDeps, refs: Sequence[Any] | None) -> tuple[ThreadMap | None, str]:
    wanted, _bad = parse_refs(deps, [str(r) for r in refs or []])
    maps = {id(t): t for f, _s in wanted for t in [_map_of_file(deps, f)] if t is not None}
    if not maps:
        return None, NO_THREADS if wanted else "No valid section refs: use refs from the outline, e.g. F1·S7."
    if len(maps) > 1:
        return None, "These sections belong to different analyses; one change per analysis."
    return next(iter(maps.values())), ""


def _section_fields(
    deps: AttachmentDeps, tmap: ThreadMap, fields: dict, current: Sequence[str], *, need_one: bool,
) -> tuple[dict, str]:
    """``sections`` / ``add_sections`` / ``remove_sections`` converted to map refs
    and checked against ``current``; ``{}`` when none given. A full ``sections``
    list keeps the current refs into documents not given in this call (the model
    could neither see nor name them)."""
    given = {k: fields.get(k) for k in _SECTION_KEYS if fields.get(k) is not None}
    if not given:
        return {}, ""
    if "sections" in given and len(given) > 1:
        return {}, "Give either the full `sections` list or add_sections / remove_sections, not both."
    out: dict[str, list[str]] = {}
    for key, refs in given.items():
        converted, bad = map_refs(deps, tmap, refs)
        if bad:
            return {}, f"Unknown section refs: {', '.join(bad)}. Use refs from the outline, e.g. F1·S7."
        out[key] = converted
    if "sections" in out:
        loaded = {doc for item, doc in tmap.doc_of_item.items()
                  if any(f.item_id == item for f in deps.files.values())}
        unseen = [r for r in current if (parse_ref(r) or (None,))[0] not in loaded]
        out["sections"] = unseen + [r for r in out["sections"] if r not in unseen]
    after = list(out.get("sections", current))
    for ref in out.get("remove_sections", []):
        if ref not in after:
            return {}, "A section to remove is not in it now."
        after.remove(ref)
    for ref in out.get("add_sections", []):
        if ref not in after:
            after.append(ref)
    if need_one and not after:
        return {}, "A thread keeps at least one section."
    return out, ""


def _plain(fields: BaseModel | dict | None) -> dict:
    if fields is None:
        return {}
    if isinstance(fields, BaseModel):
        return fields.model_dump(exclude_none=True)
    return {k: v for k, v in dict(fields).items() if v is not None}


# ---------------------------------------------------------------------------
# promote_sections
# ---------------------------------------------------------------------------


def stage_promote(deps: AttachmentDeps, refs: list[str], evidence: str) -> str:
    if not evidence_ok(deps, evidence):
        return REFUSED_EVIDENCE
    wanted, bad = parse_refs(deps, refs)
    staged: list[str] = []
    already: list[str] = []
    for f, s in wanted:
        if s.value == "core":
            already.append(f.ref(s))
            continue
        if s.char_end <= s.char_start:
            bad.append(f.ref(s))
            continue
        if _staged(deps, "promote", item_id=f.item_id, seq=s.seq):
            already.append(f.ref(s) + " (already staged)")
            continue
        _stage(deps, "promote", evidence, item_id=f.item_id, file_id=f.file_id, seq=s.seq,
               char_start=s.char_start, char_end=s.char_end, what=s.what or s.label, title=f.title)
        staged.append(f.ref(s))
    lines: list[str] = []
    if staged:
        lines.append(f"Staged: {', '.join(staged)} → core (applied after you finish).")
    if already:
        lines.append(f"Already core, nothing to do: {', '.join(already)}. Tell the router so.")
    if bad:
        lines.append(f"Unknown section refs: {', '.join(bad)}. Use refs from the outline, e.g. F1·S7.")
    return "\n".join(lines) or "No section refs given."


# ---------------------------------------------------------------------------
# Threads
# ---------------------------------------------------------------------------


def _changed_text(deps: AttachmentDeps, fields: dict, current: dict) -> bool:
    """Any plain field the user named differs from what is stored."""
    source = deps.read_source()
    for k, v in fields.items():
        if k in _SECTION_KEYS:
            continue
        now = current.get(k)
        if k in _TEXT_FIELDS:
            real = decode_for_store(str(v).strip(), source=source) or ""
            if real != str(now or "").strip():
                return True
        elif v != now:
            return True
    return False


def stage_edit_thread(deps: AttachmentDeps, thread: str, fields: Any, evidence: str) -> str:
    if not evidence_ok(deps, evidence):
        return REFUSED_EVIDENCE
    tmap, seq, why = resolve_thread(deps, thread)
    if tmap is None:
        return why
    values = _plain(fields)
    if not values:
        return "Name at least one field to change."
    angle = tmap.angle(seq) or {}
    if "title" in values and not str(values["title"]).strip():
        return "A thread title cannot be empty."
    if not _decodes(deps, [values[k] for k in values if k in _TEXT_FIELDS]):
        return R_FAKE
    sections, why = _section_fields(deps, tmap, values, list(angle.get("sections") or []), need_one=True)
    if why:
        return why
    plain = {k: v for k, v in values.items() if k not in _SECTION_KEYS}
    if not sections and not _changed_text(deps, plain, angle):
        return f"Nothing to change: {tmap.thread_label(seq)} already has these values. Tell the router so."
    conflict = _undo_conflict(deps, edit_kind="edit_thread")
    if conflict:
        return _refused(conflict)
    if not _stage(deps, "edit_thread", evidence, map_id=tmap.map_id, seq=seq, fields={**plain, **sections},
                  title=str(angle.get("title") or "")):
        return ALREADY_STAGED
    return f"Staged: change {', '.join(values)} of {tmap.thread_label(seq)} (applied after you finish)."


def stage_add_thread(deps: AttachmentDeps, fields: Any, evidence: str) -> str:
    if not evidence_ok(deps, evidence):
        return REFUSED_EVIDENCE
    values = _plain(fields)
    if not str(values.get("title") or "").strip():
        return "A new thread needs a title."
    if not values.get("kind"):
        return "A new thread needs a kind: strengthen, attack or answer."
    refs = values.get("sections") or values.get("add_sections")
    if not refs:
        return "A new thread needs at least one section (refs as F1·S7)."
    tmap, why = _map_for_refs(deps, refs)
    if tmap is None:
        return why
    converted, bad = map_refs(deps, tmap, refs)
    if bad or not converted:
        return f"Unknown section refs: {', '.join(bad) or '—'}. Use refs from the outline, e.g. F1·S7."
    if not _decodes(deps, [values[k] for k in values if k in _TEXT_FIELDS]):
        return R_FAKE
    plain = {k: v for k, v in values.items() if k not in _SECTION_KEYS}
    conflict = _undo_conflict(deps, edit_kind="add_thread")
    if conflict:
        return _refused(conflict)
    if not _stage(deps, "add_thread", evidence, map_id=tmap.map_id, fields={**plain, "sections": converted},
                  title=str(values["title"]).strip()):
        return ALREADY_STAGED
    return f"Staged: new thread «{_cut(values['title'])}» (applied after you finish)."


def stage_remove_thread(deps: AttachmentDeps, thread: str, evidence: str) -> str:
    if not evidence_ok(deps, evidence):
        return REFUSED_EVIDENCE
    tmap, seq, why = resolve_thread(deps, thread)
    if tmap is None:
        return why
    if _staged(deps, "remove_thread", map_id=tmap.map_id, seq=seq):
        return f"{tmap.thread_label(seq)} is already staged for removal."
    conflict = _undo_conflict(deps, edit_kind="remove_thread")
    if conflict:
        return _refused(conflict)
    _stage(deps, "remove_thread", evidence, map_id=tmap.map_id, seq=seq,
           title=str((tmap.angle(seq) or {}).get("title") or ""))
    removed = sum(1 for e in deps.staged_edits if e.kind == "remove_thread" and e.args.get("map_id") == tmap.map_id)
    left = len(tmap.angles) - removed
    note = " Fewer than two threads will be left: «بحث شامل» needs two." if left < 2 else ""
    return f"Staged: remove {tmap.thread_label(seq)} (applied after you finish).{note}"


# ---------------------------------------------------------------------------
# Gaps and the client line
# ---------------------------------------------------------------------------


def stage_add_gap(deps: AttachmentDeps, fields: Any, evidence: str, analysis: str | None = None) -> str:
    if not evidence_ok(deps, evidence):
        return REFUSED_EVIDENCE
    values = _plain(fields)
    if not str(values.get("issue") or "").strip():
        return "A new gap needs its issue."
    refs = values.get("sections") or values.get("add_sections") or []
    if refs and not analysis:
        tmap, why = _map_for_refs(deps, refs)
    else:
        tmap, why = pick_map(deps, analysis)
    if tmap is None:
        return why
    converted, bad = map_refs(deps, tmap, refs)
    if bad:
        return f"Unknown section refs: {', '.join(bad)}. Use refs from the outline, e.g. F1·S7."
    if not _decodes(deps, [values["issue"]]):
        return R_FAKE
    plain = {k: v for k, v in values.items() if k not in _SECTION_KEYS}
    conflict = _undo_conflict(deps, edit_kind="add_gap")
    if conflict:
        return _refused(conflict)
    if not _stage(deps, "add_gap", evidence, map_id=tmap.map_id, fields={**plain, "sections": converted},
                  issue=str(values["issue"]).strip()):
        return ALREADY_STAGED
    return f"Staged: new gap «{_cut(values['issue'])}» (applied after you finish)."


def stage_edit_gap(deps: AttachmentDeps, gap: str, fields: Any, evidence: str) -> str:
    if not evidence_ok(deps, evidence):
        return REFUSED_EVIDENCE
    tmap, index, why = resolve_gap(deps, gap)
    if tmap is None:
        return why
    values = _plain(fields)
    if not values:
        return "Name at least one field to change."
    if "issue" in values and not str(values["issue"]).strip():
        return "A gap's issue cannot be empty."
    if not _decodes(deps, [values[k] for k in values if k in _TEXT_FIELDS]):
        return R_FAKE
    current = tmap.gaps[index - 1]
    sections, why = _section_fields(deps, tmap, values, list(current.get("sections") or []), need_one=False)
    if why:
        return why
    plain = {k: v for k, v in values.items() if k not in _SECTION_KEYS}
    if not sections and not _changed_text(deps, plain, current):
        return f"Nothing to change: {tmap.gap_label(index)} already has these values. Tell the router so."
    conflict = _undo_conflict(deps, edit_kind="edit_gap")
    if conflict:
        return _refused(conflict)
    if not _stage(deps, "edit_gap", evidence, map_id=tmap.map_id, index=index, fields={**plain, **sections},
                  issue=str(current.get("issue") or "")):
        return ALREADY_STAGED
    return f"Staged: change {', '.join(values)} of {tmap.gap_label(index)} (applied after you finish)."


def stage_remove_gap(deps: AttachmentDeps, gap: str, evidence: str) -> str:
    if not evidence_ok(deps, evidence):
        return REFUSED_EVIDENCE
    tmap, index, why = resolve_gap(deps, gap)
    if tmap is None:
        return why
    if _staged(deps, "remove_gap", map_id=tmap.map_id, index=index):
        return f"{tmap.gap_label(index)} is already staged for removal."
    conflict = _undo_conflict(deps, edit_kind="remove_gap")
    if conflict:
        return _refused(conflict)
    _stage(deps, "remove_gap", evidence, map_id=tmap.map_id, index=index,
           issue=str(tmap.gaps[index - 1].get("issue") or ""))
    return f"Staged: remove {tmap.gap_label(index)} (applied after you finish)."


def stage_edit_client(deps: AttachmentDeps, fields: Any, evidence: str, analysis: str | None = None) -> str:
    if not evidence_ok(deps, evidence):
        return REFUSED_EVIDENCE
    tmap, why = pick_map(deps, analysis)
    if tmap is None:
        return why
    values = _plain(fields)
    if not values:
        return "Name what to change: side, party or basis."
    if not _decodes(deps, [values[k] for k in values if k in _TEXT_FIELDS]):
        return R_FAKE
    if not _changed_text(deps, values, tmap.client):
        return "Nothing to change: the client line already says this. Tell the router so."
    conflict = _undo_conflict(deps, edit_kind="edit_client")
    if conflict:
        return _refused(conflict)
    if not _stage(deps, "edit_client", evidence, map_id=tmap.map_id, fields=values):
        return ALREADY_STAGED
    return f"Staged: client {', '.join(values)} (applied after you finish)."


# ---------------------------------------------------------------------------
# fix_text
# ---------------------------------------------------------------------------


def stage_fix_text(deps: AttachmentDeps, file: str, wrong: str, right: str, evidence: str) -> str:
    if not evidence_ok(deps, evidence):
        return REFUSED_EVIDENCE
    f = deps.file(file)
    if f is None:
        return f"Unknown file {file!r}. Files: {', '.join(deps.files)}."
    wrong_m = html.unescape(str(wrong or "")).strip()
    right_m = html.unescape(str(right or "")).strip()
    source = deps.read_source()
    wrong_r = decode_for_store(wrong_m, source=source)
    right_r = decode_for_store(right_m, source=source)
    if wrong_r is None or right_r is None:
        return R_FAKE
    if _staged(deps, "fix_text", file_id=f.file_id, wrong=wrong_r, right=right_r):
        return ALREADY_STAGED
    check = check_text_fix(f.real_text(), f.real_section_bounds(), wrong_r, right_r)
    if not check.ok:
        hint = ""
        if check.reason == REASON_NOT_FOUND and wrong_m:
            forms = list(dict.fromkeys(f.view.text[s:e] for s, e, _w in find_folded(f.view.text, wrong_m)))[:3]
            if forms:
                hint = (" The file writes it " + " / ".join(f"«{x}»" for x in forms)
                        + ": call fix_text again with that exact form.")
        return f"Refused, nothing staged: {check.reason}{hint}"
    _stage(deps, "fix_text", evidence, item_id=f.item_id, file_id=f.file_id, title=f.title,
           wrong=wrong_r, right=right_r, count=check.count)
    f.pending_fixes.append((wrong_r, right_r))
    return (f"Staged: «{wrong_m}» → «{right_m}» in {f.label}, {check.count} "
            f"occurrence{'s' if check.count != 1 else ''} (applied after you finish).")


# ---------------------------------------------------------------------------
# request_reanalysis
# ---------------------------------------------------------------------------


def stage_reanalysis(deps: AttachmentDeps, evidence: str) -> str:
    """The critique given to the mapper is the user's WHOLE current message (as the
    model read it, masked — ``start_reanalysis`` decodes it), never a paraphrase."""
    if not evidence_ok(deps, evidence):
        return REFUSED_EVIDENCE
    if not deps.maps:
        return NO_THREADS
    if any(e.kind == "reanalysis" for e in deps.staged_edits):
        return "A re-analysis is already staged."
    conflict = _undo_conflict(deps, edit_kind="reanalysis")
    if conflict:
        return _refused(conflict)
    targets = []
    for t in deps.maps:
        items = [f.item_id for f in deps.files.values() if f.item_id in t.doc_of_item]
        targets.append({"map_id": t.map_id, "item_ids": items})
    _stage(deps, "reanalysis", evidence, critique=deps.user_message, targets=targets)
    return "Staged: re-analysis of the threads «الخيوط» (starts after you finish; takes a few minutes)."


# ---------------------------------------------------------------------------
# list_recent_edits / undo
# ---------------------------------------------------------------------------


def _rows(resp: Any) -> list[dict]:
    data = getattr(resp, "data", None)
    return [r for r in (data or []) if isinstance(r, dict)]


def _file_by_id(deps: AttachmentDeps, file_id: Any) -> FileDoc | None:
    return next((f for f in deps.files.values() if f.file_id == str(file_id)), None)


def _in_title(deps: AttachmentDeps, file_id: Any) -> str:
    f = _file_by_id(deps, file_id)
    return f" في «{f.title}»" if f else ""


def _thread_title(deps: AttachmentDeps, map_id: str, target: str) -> str:
    m = re.match(r"^A(\d+)$", target or "")
    t = next((x for x in deps.maps if x.map_id == str(map_id)), None)
    angle = t.angle(int(m.group(1))) if (m and t) else None
    return _cut((angle or {}).get("title"), 80)


def _map_edit_line(deps: AttachmentDeps, row: dict) -> str:
    from .apply import FIELD_AR

    kind = str(row.get("kind") or "")
    before = row.get("before") if isinstance(row.get("before"), dict) else {}
    after = row.get("after") if isinstance(row.get("after"), dict) else {}
    target = str(row.get("target") or "")
    if kind == "edit_angle":
        title = _thread_title(deps, str(row.get("map_id")), target)
        names = "، ".join(FIELD_AR.get(k, k) for k in after)
        return f"تعديل الخيط «{title or target}»" + (f" ({names})" if names else "")
    if kind == "add_angle":
        return f"إضافة الخيط «{_cut(after.get('title'), 80)}»"
    if kind == "remove_angle":
        return f"حذف الخيط «{_cut(before.get('title'), 80)}»"
    if kind == "add_gap":
        return f"إضافة ثغرة: {_cut(after.get('issue'), 80)}"
    if kind == "edit_gap":
        return f"تعديل ثغرة: {_cut(after.get('issue') or before.get('issue'), 80)}"
    if kind == "remove_gap":
        return f"حذف ثغرة: {_cut(before.get('issue'), 80)}"
    if kind == "edit_client":
        names = "، ".join(FIELD_AR.get(k, k) for k in after if k != "confidence")
        return "تعديل الموكّل" + (f" ({names})" if names else "")
    if kind == "snapshot":
        return "إعادة تحليل الخيوط (التراجع عنها يعيد التحليل السابق)"
    return "تعديل في الخيوط"


def load_recent_edits(deps: AttachmentDeps) -> list[RecentEdit]:
    """This conversation's active changes, newest first, labelled ``E1``…; also
    kept in ``deps.recent_edits``. Sync (call in a thread). Never raises: a failed
    read leaves that kind out."""
    from agents.memory.angle_mapper.edits import load_edit_log
    from shared.attachments import segment_overrides, text_corrections

    sb, convo, user = deps.supabase, str(deps.conversation_id), str(deps.user_id)
    found: list[RecentEdit] = []
    try:
        rows = _rows(
            sb.table(text_corrections.TABLE)
            .select("id, file_id, wrong_text, right_text, occurrences, created_at")
            .eq("conversation_id", convo).eq("user_id", user).is_("reverted_at", "null")
            .execute()
        )
        for r in rows:
            line = (f"تصحيح «{_cut(r.get('wrong_text'), 60)}» إلى «{_cut(r.get('right_text'), 60)}»"
                    f"{_in_title(deps, r.get('file_id'))}")
            found.append(RecentEdit("", "text", str(r.get("id")), line, str(r.get("created_at") or "")))
    except Exception as exc:  # noqa: BLE001
        logger.warning("attachment_agent: text corrections not listed (%s)", type(exc).__name__)
    try:
        rows = _rows(
            sb.table(segment_overrides.TABLE)
            .select("id, file_id, char_start, char_end, created_at")
            .eq("conversation_id", convo).eq("user_id", user).is_("reverted_at", "null")
            .execute()
        )
        for r in rows:
            f = _file_by_id(deps, r.get("file_id"))
            what = ""
            if f is not None:
                hit = [s for s in f.sections if s.char_start < int(r.get("char_end") or 0)
                       and int(r.get("char_start") or 0) < s.char_end]
                what = "، ".join(f"«{_cut(s.what or s.label, 60)}»" for s in hit[:2])
            line = f"ترقية القسم {what or ''} إلى جوهري".replace("  ", " ") + _in_title(deps, r.get("file_id"))
            found.append(RecentEdit("", "promote", str(r.get("id")), line, str(r.get("created_at") or "")))
    except Exception as exc:  # noqa: BLE001
        logger.warning("attachment_agent: promotions not listed (%s)", type(exc).__name__)
    for r in load_edit_log(sb, None, conversation_id=convo, include_undone=False):
        if str(r.get("user_id") or "") != user or r.get("kind") == "undo":
            continue
        try:
            line = _map_edit_line(deps, r)
        except Exception:  # noqa: BLE001
            line = "تعديل في الخيوط"
        found.append(RecentEdit("", "map", str(r.get("id")), line, str(r.get("created_at") or ""),
                                kind=str(r.get("kind") or "")))

    # Newest first; rows without a time (none in production) after them, last read first.
    timed = sorted((e for e in found if e.created_at), key=lambda e: e.created_at, reverse=True)
    newest = (timed + [e for e in reversed(found) if not e.created_at])[:RECENT_EDITS_MAX]
    deps.recent_edits = {}
    for n, e in enumerate(newest, start=1):
        e.label = f"E{n}"
        deps.recent_edits[e.label] = e
    return newest


def render_recent_edits(deps: AttachmentDeps, edits: Sequence[RecentEdit]) -> str:
    from backend.app.services.masking_service import active_codec, persist_new_mappings

    from .context import _enc, turn_encoder

    if not edits:
        return "No changes made in this conversation yet."
    encode = turn_encoder()
    lines = ["This conversation's changes, newest first (undo one with undo(edit=\"E1\", evidence=…)):"]
    for e in edits:
        when = e.created_at[:16].replace("T", " ") if e.created_at else ""
        lines.append(f"{e.label} · {when + ' · ' if when else ''}{_enc(encode, e.line)}")
    try:
        persist_new_mappings(deps.supabase, deps.user_id, active_codec())
    except Exception:  # noqa: BLE001 — a fake not persisted only weakens decoding later
        logger.warning("attachment_agent: masking mappings not persisted for the edit list")
    return "\n".join(lines)


async def list_recent(deps: AttachmentDeps) -> str:
    edits = await asyncio.to_thread(load_recent_edits, deps)
    return await asyncio.to_thread(render_recent_edits, deps, edits)


async def stage_undo(deps: AttachmentDeps, edit: str, evidence: str) -> str:
    if not evidence_ok(deps, evidence):
        return REFUSED_EVIDENCE
    if not deps.recent_edits:
        await asyncio.to_thread(load_recent_edits, deps)
    m = _EDIT_RE.match(str(edit or ""))
    target = deps.recent_edits.get(f"E{int(m.group(1))}") if m else None
    if target is None:
        known = ", ".join(deps.recent_edits) or "none"
        return f"Unknown change {edit!r} (known: {known}). Call list_recent_edits first."
    if _staged(deps, "undo", id=target.id):
        return f"{target.label} is already staged to be undone."
    conflict = _undo_conflict(deps, undo_kind=target.kind)
    if conflict:
        return _refused(conflict)
    _stage(deps, "undo", evidence, source=target.source, id=target.id, line=target.line, map_kind=target.kind)
    return f"Staged: undo {target.label} (applied after you finish)."


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

_EVIDENCE_DOC = "The user's own words asking for this change, copied exactly from <current_message>."


def register_edit_tools(agent: Agent[AttachmentDeps, Any]) -> None:
    @agent.tool
    async def promote_sections(ctx: RunContext[AttachmentDeps], refs: list[str], evidence: str) -> str:
        """Mark sections as core «جوهري» because the user asked. Promote only — never demote.

        Args:
            refs: Section refs from the outline, e.g. ["F1·S7"].
            evidence: The user's own words asking for this change, copied exactly from <current_message>.
        """
        return stage_promote(ctx.deps, refs, evidence)

    @agent.tool
    async def edit_thread(ctx: RunContext[AttachmentDeps], thread: str, fields: ThreadFields, evidence: str) -> str:
        """Change fields of one thread «خيط» the user named. Only the fields the user asked for.

        Args:
            thread: The thread label as <threads> shows it, e.g. "A2" (or "M1·A2").
            fields: The new values; leave every other field empty.
            evidence: The user's own words asking for this change, copied exactly from <current_message>.
        """
        return stage_edit_thread(ctx.deps, thread, fields, evidence)

    @agent.tool
    async def add_thread(ctx: RunContext[AttachmentDeps], fields: ThreadFields, evidence: str) -> str:
        """Add a thread the user described. Needs title, kind and at least one section.

        Args:
            fields: title, kind, sections (refs as F1·S7) and anything else the user gave.
            evidence: The user's own words asking for this change, copied exactly from <current_message>.
        """
        return stage_add_thread(ctx.deps, fields, evidence)

    @agent.tool
    async def remove_thread(ctx: RunContext[AttachmentDeps], thread: str, evidence: str) -> str:
        """Remove a thread — only when the user says to remove it.

        Args:
            thread: The thread label, e.g. "A3".
            evidence: The user's own words asking for this change, copied exactly from <current_message>.
        """
        return stage_remove_thread(ctx.deps, thread, evidence)

    @agent.tool
    async def add_gap(
        ctx: RunContext[AttachmentDeps], fields: GapFields, evidence: str, analysis: str | None = None,
    ) -> str:
        """Add a gap «ثغرة» the user described.

        Args:
            fields: issue (required), kind, sections.
            evidence: The user's own words asking for this change, copied exactly from <current_message>.
            analysis: Only when several analyses are shown and no section is given: "M1" or "M2".
        """
        return stage_add_gap(ctx.deps, fields, evidence, analysis)

    @agent.tool
    async def edit_gap(ctx: RunContext[AttachmentDeps], gap: str, fields: GapFields, evidence: str) -> str:
        """Change a gap the user named.

        Args:
            gap: The gap label as <threads> shows it, e.g. "G1".
            fields: The new values; leave every other field empty.
            evidence: The user's own words asking for this change, copied exactly from <current_message>.
        """
        return stage_edit_gap(ctx.deps, gap, fields, evidence)

    @agent.tool
    async def remove_gap(ctx: RunContext[AttachmentDeps], gap: str, evidence: str) -> str:
        """Remove a gap — only when the user says so.

        Args:
            gap: The gap label, e.g. "G2".
            evidence: The user's own words asking for this change, copied exactly from <current_message>.
        """
        return stage_remove_gap(ctx.deps, gap, evidence)

    @agent.tool
    async def edit_client(
        ctx: RunContext[AttachmentDeps], fields: ClientFields, evidence: str, analysis: str | None = None,
    ) -> str:
        """Change the client line «الموكّل» as the user specified (side, party, basis). A specified
        change, not a re-analysis.

        Args:
            fields: Only what the user named.
            evidence: The user's own words asking for this change, copied exactly from <current_message>.
            analysis: Only when several analyses are shown: "M1" or "M2".
        """
        return stage_edit_client(ctx.deps, fields, evidence, analysis)

    @agent.tool
    async def fix_text(ctx: RunContext[AttachmentDeps], file: str, wrong: str, right: str, evidence: str) -> str:
        """Fix a small extraction mistake in a file's text (a misread name, word or number). Every
        occurrence in the file is fixed.

        Args:
            file: The file label, e.g. "F1".
            wrong: Exactly as the file writes it now (one line, a word or a short phrase).
            right: What it should read.
            evidence: The user's own words asking for this change, copied exactly from <current_message>.
        """
        return stage_fix_text(ctx.deps, file, wrong, right, evidence)

    @agent.tool
    async def request_reanalysis(ctx: RunContext[AttachmentDeps], evidence: str) -> str:
        """Start the analysis of the threads «الخيوط» again — only when the user asks for it to be
        redone. The analyser gets the user's whole message as their critique.

        Args:
            evidence: The user's own words asking for this change, copied exactly from <current_message>.
        """
        return stage_reanalysis(ctx.deps, evidence)

    @agent.tool
    async def list_recent_edits(ctx: RunContext[AttachmentDeps]) -> str:
        """This conversation's changes to the files and threads, newest first (E1, E2…)."""
        return await list_recent(ctx.deps)

    @agent.tool
    async def undo(ctx: RunContext[AttachmentDeps], edit: str, evidence: str) -> str:
        """Undo one change from list_recent_edits — only when the user asks.

        Args:
            edit: Its label, e.g. "E1".
            evidence: The user's own words asking for this change, copied exactly from <current_message>.
        """
        return await stage_undo(ctx.deps, edit, evidence)


__all__ = [
    "ClientFields",
    "GapFields",
    "ALREADY_STAGED",
    "CUE_PHRASES",
    "CUE_PREFIXES",
    "CUE_WORDS",
    "MIN_EVIDENCE_WORDS",
    "R_MIX_GAPS",
    "R_MIX_SNAPSHOT",
    "asks_for_change",
    "NO_THREADS",
    "REFUSED_EVIDENCE",
    "ThreadFields",
    "evidence_ok",
    "list_recent",
    "load_recent_edits",
    "map_refs",
    "pick_map",
    "register_edit_tools",
    "render_recent_edits",
    "resolve_gap",
    "resolve_thread",
    "section_of_ref",
    "stage_add_gap",
    "stage_add_thread",
    "stage_edit_client",
    "stage_edit_gap",
    "stage_edit_thread",
    "stage_fix_text",
    "stage_promote",
    "stage_reanalysis",
    "stage_remove_gap",
    "stage_remove_thread",
    "stage_undo",
]
