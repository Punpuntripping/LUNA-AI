"""Code checks on an angle map (plan §4.4), on document-qualified refs (angles-v4).

The sections these functions take are the runner's flat list of section dicts,
one per section of every document in the group, each carrying its ``ref``
(``"D2.S7"``) and ``value`` (``group.GroupDoc.sections``).

- **Refs**: every ref in ``angles[].sections``, ``context``, ``gaps[].sections``
  and ``no_angle[].section`` must be a section of the group, and every document
  in ``client.by_document`` / ``unrelated_documents`` a document of it.
  :func:`qualify_refs` first turns bare ``S7`` into ``D1.S7`` when the group has
  one document. :func:`invalid_refs` feeds the agent's output validator (one
  ``ModelRetry``); whatever is still wrong after that retry is dropped by
  :func:`drop_invalid_refs`, so a stored map never names a section that does not
  exist.
- **Every angle has a section**: :func:`sectionless_angles` (an angle naming no
  section of the group) goes back with the same one retry; an angle still without
  one after it is dropped by :func:`drop_sectionless_angles` (the runner counts
  them, and fails the run when none is left).
- **Coverage** (D13): every ``core`` section of every document is in an angle, in
  ``context`` or in ``no_angle``. :func:`unplaced_core` lists the ones that are
  not — the runner sends them back once (the repair call), then
  :func:`mark_unplaced` files what is still missing under ``no_angle`` with
  «لم يُصنَّف».
- **One question per angle** (angles-v5): :func:`multi_question_angles` (a
  ``legal_question`` with a second «؟» / "?", or a second question chained with
  «و» / «،» / «أم» — :func:`compound_question`) goes back with the same one retry;
  what is still there after it is kept (code cannot pick the question) and counted.
- **Gaps, fixed in code after validation** (no retry — retries never fixed them):
  :func:`drop_equal_value_gaps` drops a ``contradiction`` gap between equal
  figures (:func:`equal_valued`); :func:`merge_ocr_gaps` turns an ``ocr_suspect``
  contradiction into ``missing`` and keeps ONE ``ocr_suspect`` gap per document.
"""
from __future__ import annotations

import re
from typing import Any, Iterable, Mapping, Sequence

from .models import AngleMap, Gap, NoAngle
from .refs import canonical_ref, doc_of, format_doc, qualify

#: ``no_angle[].why`` for a core section the model never placed (plan §4.4).
UNPLACED_WHY = "لم يُصنَّف"


def _get(obj: Any, key: str) -> Any:
    if isinstance(obj, Mapping):
        return obj.get(key)
    return getattr(obj, key, None)


def section_refs(sections: Iterable[Any]) -> frozenset[str]:
    """Every section ref of the group."""
    return frozenset(canonical_ref(_get(s, "ref")) for s in sections)


def core_refs(sections: Iterable[Any]) -> list[str]:
    """The ``core`` sections' refs, document by document, in document order."""
    return [canonical_ref(_get(s, "ref")) for s in sections if str(_get(s, "value") or "") == "core"]


def doc_refs(refs: Iterable[str]) -> frozenset[str]:
    """The group's documents (``"D1"``…) — every document has sections."""
    return frozenset(format_doc(d) for d in (doc_of(r) for r in refs) if d is not None)


def _single_doc(refs: Iterable[str]) -> bool:
    return doc_refs(refs) == {"D1"}


def qualify_refs(amap: AngleMap, refs: Iterable[str]) -> AngleMap:
    """Bare ``S7`` → ``D1.S7`` everywhere when the group has ONE document (the only
    case where a bare number is unambiguous); any other map is returned as is."""
    if not _single_doc(refs):
        return amap

    def q(xs: Sequence[str]) -> list[str]:
        return [qualify(x, True) for x in xs]

    return amap.model_copy(update={
        "angles": [a.model_copy(update={"sections": q(a.sections)}) for a in amap.angles],
        "context": q(amap.context),
        "gaps": [g.model_copy(update={"sections": q(g.sections)}) for g in amap.gaps],
        "no_angle": [n.model_copy(update={"section": qualify(n.section, True)}) for n in amap.no_angle],
    })


def invalid_refs(amap: AngleMap, refs: Iterable[str]) -> list[str]:
    """One line per place that names a ref (or a document) outside the group
    (``"angles[2].sections: D1.S41, S7"``); ``[]`` when every ref is a section."""
    valid = set(refs)
    docs = doc_refs(valid)
    problems: list[str] = []

    def bad(xs: Iterable[str]) -> list[str]:
        return [x for x in xs if x not in valid]

    for i, a in enumerate(amap.angles, start=1):
        if b := bad(a.sections):
            problems.append(f"angles[{i}].sections: {', '.join(b)}")
    if b := bad(amap.context):
        problems.append(f"context: {', '.join(b)}")
    for i, g in enumerate(amap.gaps, start=1):
        if b := bad(g.sections):
            problems.append(f"gaps[{i}].sections: {', '.join(b)}")
    if b := bad(n.section for n in amap.no_angle):
        problems.append(f"no_angle sections: {', '.join(b)}")
    if b := [e.document for e in amap.client.by_document if e.document not in docs]:
        problems.append(f"client.by_document documents: {', '.join(b)}")
    if b := [u.document for u in amap.unrelated_documents if u.document not in docs]:
        problems.append(f"unrelated_documents: {', '.join(b)}")
    return problems


def _keep(xs: Iterable[str], valid: set[str]) -> list[str]:
    """Valid refs, first occurrence only, order kept."""
    return [x for x in dict.fromkeys(xs) if x in valid]


def drop_invalid_refs(amap: AngleMap, refs: Iterable[str]) -> tuple[AngleMap, int]:
    """``(clean map, refs dropped)``: unknown refs removed everywhere, duplicates
    inside one list removed (order kept), one ``no_angle`` entry per section, one
    ``by_document`` / ``unrelated_documents`` entry per known document. Angles and
    gaps are kept even when no section is left."""
    valid = set(refs)
    docs = doc_refs(valid)
    dropped = 0

    def clean(xs: Sequence[str]) -> list[str]:
        nonlocal dropped
        kept = _keep(xs, valid)
        dropped += sum(1 for x in xs if x not in valid)
        return kept

    angles = [a.model_copy(update={"sections": clean(a.sections)}) for a in amap.angles]
    gaps = [g.model_copy(update={"sections": clean(g.sections)}) for g in amap.gaps]
    context = clean(amap.context)
    no_angle: list[NoAngle] = []
    seen: set[str] = set()
    for n in amap.no_angle:
        if n.section not in valid:
            dropped += 1
            continue
        if n.section in seen:
            continue
        seen.add(n.section)
        no_angle.append(n)

    def per_doc(entries: Sequence[Any]) -> list[Any]:
        nonlocal dropped
        out, kept = [], set()
        for e in entries:
            if e.document not in docs:
                dropped += 1
            elif e.document not in kept:
                kept.add(e.document)
                out.append(e)
        return out

    client = amap.client.model_copy(update={"by_document": per_doc(amap.client.by_document)})
    unrelated = per_doc(amap.unrelated_documents)
    return (
        amap.model_copy(update={
            "client": client,
            "angles": angles,
            "gaps": gaps,
            "context": context,
            "no_angle": no_angle,
            "unrelated_documents": unrelated,
        }),
        dropped,
    )


def sectionless_angles(amap: AngleMap, refs: Iterable[str]) -> list[int]:
    """The angles (1-based) that name NO section of the group — no ref at all, or
    only refs outside it. An angle is a line of argument over sections; one with
    none gives a search nothing to stand on (a real preview, 2026-11-22: 9 such
    angles, every core section then unplaced)."""
    valid = set(refs)
    return [i for i, a in enumerate(amap.angles, start=1) if not any(x in valid for x in a.sections)]


def drop_sectionless_angles(amap: AngleMap) -> tuple[AngleMap, int]:
    """``(map, angles dropped)``: every angle with no section left — run it after
    :func:`drop_invalid_refs`, which removed the refs outside the group."""
    kept = [a for a in amap.angles if a.sections]
    dropped = len(amap.angles) - len(kept)
    if not dropped:
        return amap, 0
    return amap.model_copy(update={"angles": kept}), dropped


def placed_refs(output: AngleMap | Mapping[str, Any]) -> set[str]:
    """Sections the map places: in an angle, in ``context`` or in ``no_angle``
    (``gaps`` do not place a section)."""
    if isinstance(output, AngleMap):
        output = output.model_dump()
    placed: set[str] = set()
    for a in output.get("angles") or []:
        placed.update(canonical_ref(x) for x in (a.get("sections") or []))
    placed.update(canonical_ref(x) for x in (output.get("context") or []))
    placed.update(
        canonical_ref(n.get("section")) for n in (output.get("no_angle") or []) if n.get("section") is not None
    )
    return placed


def unplaced_core(output: AngleMap | Mapping[str, Any], sections: Iterable[Any]) -> list[str]:
    """Core section refs (document order) in no angle, not in ``context`` and not
    in ``no_angle``. ``output``: an :class:`AngleMap` or its ``model_dump()``;
    ``sections``: the group's section dicts (each with ``ref`` and ``value``)."""
    placed = placed_refs(output)
    return [r for r in core_refs(sections) if r not in placed]


def mark_unplaced(amap: AngleMap, unplaced: Iterable[str]) -> AngleMap:
    """``no_angle`` + one «لم يُصنَّف» entry per still-unplaced section."""
    extra = [NoAngle(section=str(r), why=UNPLACED_WHY) for r in unplaced]
    if not extra:
        return amap
    return amap.model_copy(update={"no_angle": [*amap.no_angle, *extra]})


# ---------------------------------------------------------------------------
# angles-v5: one question per angle; a contradiction is two DIFFERENT values;
# one OCR gap per document
# ---------------------------------------------------------------------------

_QUESTION_MARKS = ("؟", "?")

#: Arabic diacritics and tatweel — dropped before the clause check; hamza'd alefs
#: read as a bare alef (``أم`` → ``ام``, ``أن``/``إن`` → ``ان``, ``أي`` → ``اي``).
_DIACRITICS_RE = re.compile("[ؐ-ًؚ-ٰٟۖ-ۭـ]")
_ALEFS = str.maketrans("أإآٱ", "اااا")

#: Words that open a question (alef-normalised). ``ما`` and ``من`` also open a
#: relative clause / a phrase («وما في حكمه», «ومن ثم»), so they are judged by the
#: word after them (below).
_ASKING = ("هل", "كيف", "كيفية", "متى", "لماذا", "ماذا", "اين", "كم", "مدى", "اي")
#: A second question joined to the first by «و» (``وهل`` / ``و هل``) or after «،»
#: (``، هل`` / ``، وهل``) — group 1 the opener, group 2 the next word.
_JOINED_RE = re.compile(
    r"(?:(?<!\w)و\s*|[،,؛;]\s*(?:و\s*)?)(" + "|".join((*_ASKING, "ما", "من")) + r")(?!\w)\s*(\w*)"
)
#: «أم» opening an alternative QUESTION («…أم أن…», «…أم هل…») — never «أم» before a
#: noun («هل العقد بيع أم إيجار؟», «من تاريخ التبليغ أم من تاريخ العلم؟»: one question).
_ALTERNATIVE_RE = re.compile(
    r"(?<!\w)ام\s+(?:ان|انه|انها|انهم|هل|كيف|متى|لماذا|ماذا|اين|كم)(?!\w)"
)
#: After «وما» / «، ما», words that make it a relative or conditional phrase, not a
#: question («وما في حكمه», «وما بعدها», «ما لم», «ما دام», «وما إلى ذلك»).
_MA_PHRASE = frozenset({
    "لم", "دام", "زال", "عدا", "عداه", "عداها", "سوى", "خلا", "بين", "بعد", "بعده", "بعدها", "قبل", "قبله",
    "قبلها", "دون", "فوق", "في", "على", "الى", "اليه", "اليها", "شابه", "شابهه", "شابهها", "ماثل", "ماثله",
    "ماثلها", "نحو", "نحوه", "نحوها", "يليه", "يليها",
})
#: After «ومن» / «، من», words that make it "and WHO" — otherwise it is the preposition
#: («ومن ثم», «ومن تاريخ التبليغ»). A verb in the present («ومن يتحمل») counts too.
_WHO_NEXT = frozenset({
    "هو", "هي", "هم", "له", "لها", "لهم", "الذي", "التي", "الذين", "المسؤول", "المسئول", "المختص", "الملزم",
})


def _asks(opener: str, nxt: str) -> bool:
    """Whether a joined ``opener`` + next word opens a QUESTION."""
    if opener == "ما":
        return bool(nxt) and nxt not in _MA_PHRASE
    if opener == "من":
        return nxt in _WHO_NEXT or (nxt.startswith("ي") and not nxt.startswith("يوم"))
    return True


def compound_question(question: str) -> bool:
    """A ``legal_question`` that asks more than one question: a second «؟» / "?", or —
    besides its opening — a second question joined by «و» / «،» («…، وهل…»,
    «…، وما أثر…», «…، ومدى…», «ومن يتحمل…») or opened by «أم» («…أم أن…»). A «ما»
    inside a phrase is not one («ما أثر … على …؟», «وما في حكمه», «ما لم…»)."""
    q = str(question or "")
    if sum(q.count(m) for m in _QUESTION_MARKS) > 1:
        return True
    text = _DIACRITICS_RE.sub("", q).translate(_ALEFS).strip()
    if any(m.start() > 0 and _asks(m.group(1), m.group(2)) for m in _JOINED_RE.finditer(text)):
        return True
    return bool(_ALTERNATIVE_RE.search(text))


def multi_question_angles(amap: AngleMap) -> list[int]:
    """The angles (1-based) whose ``legal_question`` asks more than one question
    (:func:`compound_question`)."""
    return [i for i, a in enumerate(amap.angles, start=1) if compound_question(a.legal_question)]


#: Arabic-Indic and Extended Arabic-Indic digits (and the Arabic decimal /
#: thousands separators) → Latin.
_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹٫٬", "01234567890123456789.,")
#: Section / paragraph / document refs the issue may quote (``D1·S5``, ``S7``,
#: ``P012``, ``D2``) — their numbers are not values.
_REF_RE = re.compile(r"(?<![A-Za-z0-9])[DSP]\d+(?:\s*[·.\-]\s*[SP]\d+)?(?![A-Za-z0-9])", re.I)
#: One value: digits, with inner separators (dates 1445/05/12, amounts 1,250.50, times).
_VALUE_RE = re.compile(r"\d+(?:[.,/:\-]\d+)*")


def _number(value: str) -> str:
    """Thousands commas dropped; a decimal's trailing zeros too (``5000.00`` → ``5000``)."""
    value = value.replace(",", "")
    if value.count(".") == 1 and not any(c in value for c in "/:-"):
        value = value.rstrip("0").rstrip(".")
    return value


def compared_values(issue: str) -> list[str]:
    """The numeric values an issue names, digits normalised (Arabic-Indic → Latin,
    thousands commas and a decimal's trailing zeros dropped), refs ignored."""
    text = _REF_RE.sub(" ", str(issue or "").translate(_DIGITS))
    return [_number(m.group(0)) for m in _VALUE_RE.finditer(text)]


def _figure(value: str) -> bool:
    """A value that reads as a figure — a date, an amount, a decimal (3+ digits, or a
    separator) — not a clause / article / item number («البند 5»)."""
    return sum(c.isdigit() for c in value) >= 3 or any(c in value for c in "./:-")


def _shape(value: str) -> str:
    """A figure's shape: its separators (``34758.75`` → ``9.9``, ``1445/05/12`` →
    ``9/9/9``, an invoice number ``10234`` → ``9``)."""
    return re.sub(r"\d+", "9", value)


def equal_valued(issue: str) -> bool:
    """A ``contradiction`` issue whose figures are equal: one figure appears at least
    twice, and no shape holds two DIFFERENT figures (those could be the
    contradiction). Another figure of another shape (an invoice number next to two
    equal amounts) does not save it; no figure, or one, never makes it equal."""
    figures = [v for v in compared_values(issue) if _figure(v)]
    by_shape: dict[str, set[str]] = {}
    for v in figures:
        by_shape.setdefault(_shape(v), set()).add(v)
    if any(len(values) > 1 for values in by_shape.values()):
        return False
    return len(figures) > len(set(figures))


def equal_value_contradictions(amap: AngleMap) -> list[int]:
    """The ``contradiction`` gaps (1-based) whose figures are equal (:func:`equal_valued`)."""
    return [i for i, g in enumerate(amap.gaps, start=1) if g.kind == "contradiction" and equal_valued(g.issue)]


def drop_equal_value_gaps(amap: AngleMap) -> tuple[AngleMap, int]:
    """``(map, gaps dropped)``: every :func:`equal_value_contradictions` gap removed —
    in code, after validation (a retry never fixed them)."""
    same = set(equal_value_contradictions(amap))
    if not same:
        return amap, 0
    gaps = [g for i, g in enumerate(amap.gaps, start=1) if i not in same]
    return amap.model_copy(update={"gaps": gaps}), len(same)


#: How the issues of one document's OCR gaps are joined.
OCR_ISSUE_JOIN = "؛ "


def merge_ocr_gaps(amap: AngleMap) -> tuple[AngleMap, int]:
    """``(map, gaps merged)``: an ``ocr_suspect`` gap is a reading problem, never a
    contradiction — ``kind: contradiction`` becomes ``missing``; and a document keeps
    ONE ``ocr_suspect`` gap (its first; a gap's document is its first section's, none
    for a gap without sections): the later ones' issues are joined to it with «؛»
    and their sections added (union, order kept)."""
    gaps: list[Gap] = []
    first: dict[int | None, int] = {}  # document → index in ``gaps`` of its OCR gap
    merged = 0
    for g in amap.gaps:
        if not g.ocr_suspect:
            gaps.append(g)
            continue
        if g.kind == "contradiction":
            g = g.model_copy(update={"kind": "missing"})
        doc = doc_of(g.sections[0]) if g.sections else None
        if doc not in first:
            first[doc] = len(gaps)
            gaps.append(g)
            continue
        keep = gaps[first[doc]]
        issue, more = keep.issue.strip(), g.issue.strip()
        if more and more not in issue:
            issue = OCR_ISSUE_JOIN.join(x for x in (issue.rstrip(" .؛"), more) if x)
        gaps[first[doc]] = keep.model_copy(update={
            "issue": issue,
            "sections": list(dict.fromkeys([*keep.sections, *g.sections])),
        })
        merged += 1
    if gaps == list(amap.gaps):
        return amap, 0
    return amap.model_copy(update={"gaps": gaps}), merged


__all__ = [
    "OCR_ISSUE_JOIN",
    "UNPLACED_WHY",
    "compared_values",
    "compound_question",
    "core_refs",
    "doc_refs",
    "drop_equal_value_gaps",
    "drop_invalid_refs",
    "drop_sectionless_angles",
    "equal_value_contradictions",
    "equal_valued",
    "invalid_refs",
    "mark_unplaced",
    "merge_ocr_gaps",
    "multi_question_angles",
    "placed_refs",
    "qualify_refs",
    "section_refs",
    "sectionless_angles",
    "unplaced_core",
]
