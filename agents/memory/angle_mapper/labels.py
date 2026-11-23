"""Side labels — whose section is it, relative to the client (D14).

A section's side comes from the segmenter's ownership, never from the model's
reading: ``owner_class`` when it is a party key, else the side read from
``brought_by`` (keyword match, as tested in ``run_angle_planner.py``
``side_label`` / ``_bb_side``), else neutral. Relative to the client — or to an
angle's ``for_side`` when the client is unknown — the same side is **الموكّل**, the
opposite side (claimant ↔ respondent; a respondent also faces prosecution and
victim) is **الخصم**, anything else is **محايد**.

v3 (plan §5 item 12, Q15): every contract party has the same owner key,
``contract_party``, so for a contract client the party's WORDS decide — a
``contract_party`` section whose ``owner`` matches the client's party is the
client's, any other ``contract_party`` section is the other party's, a
``both_parties`` section is neutral.

Labels are computed whenever a view is built, never stored: the planner may
correct the client's side at search time (D11).

v4 (one map per message, D17): the per-section rule is unchanged; a section dict
from ANY document of the group works (the segmenter's rows carry the owner
fields). :func:`section_index` builds ``{ref: section dict}`` for a stored map and
:func:`resolve_ref` looks a ref up. Owner keys are relative to each document's own
proceeding, so :func:`angle_viewpoint` takes the section's ref and, for an angle
that serves the client, uses the client's key / name in THAT document when the map
gives one (``client.by_document``).
"""
from __future__ import annotations

import re
from typing import Any, Iterable, Literal, Mapping

from .refs import canonical_doc, canonical_ref, doc_of, format_doc, format_ref

#: Owner keys that are a side of the dispute (the tested ``PARTY``) …
LITIGATION_SIDES: frozenset[str] = frozenset({"claimant", "respondent", "prosecution", "victim", "intervenor"})
#: … plus the contract party (v3).
PARTY_SIDES: frozenset[str] = LITIGATION_SIDES | {"contract_party"}
CONTRACT_PARTY = "contract_party"
NEUTRAL = "neutral"

#: Who faces whom (the tested ``OPP``).
OPPOSITE: dict[str, frozenset[str]] = {
    "claimant": frozenset({"respondent"}),
    "respondent": frozenset({"claimant", "prosecution", "victim"}),
    "prosecution": frozenset({"respondent"}),
    "victim": frozenset({"respondent"}),
}

Label = Literal["own", "opp", "neutral"]
LABEL_AR: dict[str, str] = {"own": "الموكّل", "opp": "الخصم", "neutral": "محايد"}


def _get(section: Any, key: str) -> Any:
    if isinstance(section, Mapping):
        return section.get(key)
    return getattr(section, key, None)


def _owner_class(section: Any) -> str:
    return str(_get(section, "owner_class") or "").strip().lower()


def brought_by_side(text: str | None) -> str | None:
    """The side a ``brought_by`` names — the tested keyword match, verbatim."""
    t = text or ""
    if "العام" in t or "النيابة" in t:
        return "prosecution"
    if "عليه" in t or "ضد" in t:
        return "respondent"
    if "المدعي" in t or "المستأنف" in t or "المعترض" in t:
        return "claimant"
    return None


# Arabic diacritics (tashkeel, superscript alef) and tatweel.
_MARKS = re.compile(r"[ً-ٰٟـ]")
_ALEFS = str.maketrans({"أ": "ا", "إ": "ا", "آ": "ا", "ٱ": "ا"})
_NON_WORD = re.compile(r"[^\w]+", re.UNICODE)


def normalize_party(text: str | None) -> str:
    """Party words for matching: diacritics and tatweel dropped, alef forms
    unified, the article «ال» stripped from each word, punctuation and spaces
    removed. «الطرف الأول (المالك)» → «طرفاولمالك»."""
    t = _MARKS.sub("", str(text or "")).translate(_ALEFS)
    words = [w for w in _NON_WORD.split(t) if w]
    return "".join(w[2:] if w.startswith("ال") and len(w) > 3 else w for w in words)


def party_words_match(a: str | None, b: str | None) -> bool:
    """Normalised containment either way; empty never matches."""
    x, y = normalize_party(a), normalize_party(b)
    return bool(x) and bool(y) and (x in y or y in x)


def section_side(section: Mapping, client_party: str = "") -> str:
    """The section's side: a party key (``claimant | respondent | prosecution |
    victim | intervenor | contract_party``) or ``"neutral"``.

    ``owner_class`` when it is a party key; else the side ``brought_by`` names (the
    tested keyword match); else — v3, contracts — ``contract_party`` when
    ``brought_by`` names ``client_party`` (a non-party section the client's
    contract party relies on, e.g. a guarantee owned by a bank); else neutral.
    """
    oc = _owner_class(section)
    if oc in PARTY_SIDES:
        return oc
    brought_by = str(_get(section, "brought_by") or "")
    side = brought_by_side(brought_by)
    if side:
        return side
    if client_party and party_words_match(brought_by, client_party):
        return CONTRACT_PARTY
    return NEUTRAL


def relative_label(section: Mapping, me: str | None, client_party: str = "") -> Label:
    """``own`` (الموكّل) / ``opp`` (الخصم) / ``neutral`` (محايد) relative to ``me`` —
    the client's side, or the angle's ``for_side`` when the client is unknown.

    ``client_party`` (the client's / the angle's party as the file names it)
    matters only when ``me`` is ``contract_party``; without it every contract
    section is neutral (the sides cannot be told apart).
    """
    me = str(me or "").strip().lower()
    if me == CONTRACT_PARTY:
        return _contract_label(section, client_party)
    if me not in LITIGATION_SIDES:
        return "neutral"
    side = _litigation_side(section)
    if side == me:
        return "own"
    if side in OPPOSITE.get(me, frozenset()):
        return "opp"
    return "neutral"


def _litigation_side(section: Any) -> str:
    """The tested ``side_label`` rule, unchanged: a litigation owner key, else the
    side ``brought_by`` names, else neutral (no party words)."""
    oc = _owner_class(section)
    if oc in LITIGATION_SIDES:
        return oc
    return brought_by_side(str(_get(section, "brought_by") or "")) or NEUTRAL


def _contract_label(section: Mapping, client_party: str) -> Label:
    if not normalize_party(client_party):
        return "neutral"
    oc = _owner_class(section)
    if oc == "both_parties":
        return "neutral"
    if oc == CONTRACT_PARTY:
        if not normalize_party(str(_get(section, "owner") or "")):
            return "neutral"  # no owner words: which party it is cannot be told
        return "own" if party_words_match(_get(section, "owner"), client_party) else "opp"
    if section_side(section, client_party) == CONTRACT_PARTY:  # brought by the client's party
        return "own"
    return "neutral"


def client_in_document(client: Mapping | None, document: Any) -> tuple[str, str] | None:
    """``(side, party)`` the client has in ``document`` (``"D2"`` / ``2``) when the map
    names one in ``client.by_document`` (a party key), else ``None``."""
    if not client:
        return None
    want = canonical_doc(document)
    for entry in _get(client, "by_document") or []:
        side = str(_get(entry, "side") or "")
        if canonical_doc(_get(entry, "document")) == want and side in PARTY_SIDES:
            return side, str(_get(entry, "party") or "")
    return None


def angle_viewpoint(
    angle: Mapping, client: Mapping | None, ref: Any = None
) -> tuple[str | None, str]:
    """``(me, party)`` to label one of an angle's sections: the angle's own side when
    it names a party key, else the client's (the tested order), else ``(None, "")``.

    ``ref`` (v4) — the section being labelled. When the angle serves the client (its
    ``for_side`` is the client's, or it names no side) and the client has another
    key / name in that section's document (``client.by_document``), that one is
    used: owner keys are relative to each document's own proceeding."""
    for_side = str(_get(angle, "for_side") or "")
    client_side = str(_get(client, "side") or "") if client else ""
    if for_side in PARTY_SIDES:
        me, party = for_side, str(_get(angle, "for_party") or "")
    elif client_side in PARTY_SIDES:
        me, party = client_side, str(_get(client, "party") or "")
    else:
        return None, ""
    if ref is not None and me == client_side:
        doc = doc_of(ref)
        local = client_in_document(client, doc) if doc is not None else None
        if local is not None:
            return local
    return me, party


# ---------------------------------------------------------------------------
# Refs → section dicts (v4)
# ---------------------------------------------------------------------------


def section_index(items: Iterable[Mapping], segment_maps: Mapping[str, Any]) -> dict[str, dict]:
    """``{ref: section dict}`` for a stored angle map.

    ``items``: the map's ``attachment_angle_map_items`` rows (``doc_index``,
    ``item_id``) — ``store.load_angle_map_for_item(...)["items"]``.
    ``segment_maps``: ``{item_id: StoredMap | {"segments": [...]}}`` —
    ``attachment_segmenter.load_segment_maps`` over those item ids. A member with
    no segment map now (deleted, re-segmented) is left out: its refs resolve to
    ``None``.

    Each dict is the segment row's fields (``seq``, ``label``, ``value``,
    ``owner_class``, ``owner``, ``brought_by``, ``what``, ``content_md``…) plus
    ``ref``, ``doc`` (the D-number) and ``item_id`` — ready for
    :func:`section_side` / :func:`relative_label`.
    """
    out: dict[str, dict] = {}
    for it in items:
        try:
            doc = int(_get(it, "doc_index"))
        except (TypeError, ValueError):
            continue
        item_id = str(_get(it, "item_id") or "")
        smap = segment_maps.get(item_id)
        if smap is None:
            continue
        for seg in _get(smap, "segments") or []:
            row = seg.model_dump() if hasattr(seg, "model_dump") else dict(seg)
            try:
                ref = format_ref(doc, int(row.get("seq")))
            except (TypeError, ValueError):
                continue
            out[ref] = {**row, "ref": ref, "doc": doc, "item_id": item_id}
    return out


def resolve_ref(index: Mapping[str, Mapping], ref: Any) -> Mapping | None:
    """The section dict a ref names (any accepted spelling), or ``None``."""
    return index.get(canonical_ref(ref))


def document_of(ref: Any) -> str | None:
    """``"D2.S7"`` → ``"D2"`` (``None`` for a bare / invalid ref)."""
    doc = doc_of(ref)
    return format_doc(doc) if doc is not None else None


__all__ = [
    "CONTRACT_PARTY",
    "LABEL_AR",
    "LITIGATION_SIDES",
    "Label",
    "NEUTRAL",
    "OPPOSITE",
    "PARTY_SIDES",
    "angle_viewpoint",
    "brought_by_side",
    "client_in_document",
    "document_of",
    "normalize_party",
    "party_words_match",
    "relative_label",
    "resolve_ref",
    "section_index",
    "section_side",
]
