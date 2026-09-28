"""Exhibit numbering for writer documents — «مرفق رقم n», never ``WI-{seq}``.

``WI-{seq}`` is the conversation-scoped handle agents use in chat; it is gappy
and duplicated (a document uploaded twice has two aliases), so it must never
reach a document the lawyer files. The writer is prompted to cite user
documents as «(مرفق رقم n)» and list them in ``exhibits``; this module is the
deterministic safety net behind that prompt:

* ``sanitize_document`` — rewrites any ``WI-{seq}`` left in the sections
  (attachment → «مرفق رقم n», anything else → stripped), never refuses, and
  keeps the trailing ``## المرفقات`` section in step with ``exhibits``.
* ``format_exhibit_guide_ar`` — the chat block mapping each مرفق to its card.
* ``annotate_chat_aliases`` — «(WI-9)» in chat bullets → «(مرفق 2 · WI-9)».

See ``.claude/plans/writer_exhibit_numbering.md``.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable

from .models import ExhibitRef, WriterSection

ATTACHMENT_KIND = "attachment"

# Subtypes whose documents get a trailing «المرفقات» list. Contracts use
# «ملحق» annexes (out of scope); a summary is not a filing.
_EXHIBIT_LIST_SUBTYPES = frozenset({"memo", "legal_opinion", "defense_brief", "letter"})

_EXHIBITS_HEADING = "## المرفقات"

_ALIAS = r"WI\s*-\s*(\d+)"
_ALIAS_RE = re.compile(_ALIAS, re.IGNORECASE)
# A parenthetical made ONLY of aliases and separators: «(WI-9)», «(WI-9، WI-8)»,
# «(WI-9 وWI-8)». Arabic ornate parentheses are tolerated.
_SEP = r"\s*(?:[،,؛;]\s*)?(?:و\s*)?"
_GROUP_RE = re.compile(
    r"(?P<lead>[ \t]*)[(﴾﴿]\s*"
    rf"(?P<body>{_ALIAS}(?:{_SEP}{_ALIAS})*)"
    r"\s*[)﴿﴾]",
    re.IGNORECASE,
)
# Chat annotation skips aliases it already annotated («مرفق 2 · WI-9»).
_CHAT_ALIAS_RE = re.compile(r"(?<!· )" + _ALIAS, re.IGNORECASE)
# «مرفق رقم 4» / «المرفق رقم (4)» in the document body.
_BODY_REF_RE = re.compile(r"(مرفق\s+رقم\s*\(?\s*)(\d+)")
# Chat text is looser: «مرفق 3», «مرفق (3)», «مرفق رقم 3».
_CHAT_REF_RE = re.compile(r"(مرفق\s*(?:رقم\s*)?\(?\s*)(\d+)")
# Agent output is exempt from the Latin-digits policy, so the writer may number
# «مرفق رقم ١». Numbers this module writes INTO the document follow whichever
# script the document already uses, so one filing never mixes «١» and «1».
_TO_ARABIC_INDIC = str.maketrans("0123456789", "٠١٢٣٤٥٦٧٨٩")
_ARABIC_INDIC_RE = re.compile(r"[٠-٩]")
_LATIN_DIGIT_RE = re.compile(r"[0-9]")
_SPACE_BEFORE_PUNCT_RE = re.compile(r"[ \t]+([.،,؛;:!?؟])")
_DOUBLE_SPACE_RE = re.compile(r"(?<=\S)[ \t]{2,}")


@dataclass(frozen=True)
class AliasInfo:
    """What a ``WI-{seq}`` alias points at, from the writer's package."""

    seq: int
    kind: str
    title: str = ""
    item_id: str = ""


@dataclass
class SanitizeStats:
    """Counters for the publish span — how often the prompt rule slipped."""

    leaks_attachment: int = 0   # alias → rewritten to «مرفق رقم n»
    leaks_stripped: int = 0     # search/draft/notes alias → removed
    leaks_unknown: int = 0      # alias not in the package (hallucinated)
    exhibits_added: int = 0     # attachments cited by alias but missing from exhibits
    exhibits_dropped: int = 0   # exhibits entries that are not attachments / duplicates
    renumbered: bool = False    # exhibit numbers reordered to first-mention order

    @property
    def total_leaks(self) -> int:
        return self.leaks_attachment + self.leaks_stripped + self.leaks_unknown


@dataclass
class SanitizeResult:
    title: str
    sections: list[WriterSection]
    exhibits: list[ExhibitRef]
    stats: SanitizeStats = field(default_factory=SanitizeStats)
    # old n → new n when exhibits were reordered; apply to chat text the LLM
    # wrote with the old numbers (``renumber_chat_refs``). Empty = unchanged.
    renumber: dict[int, int] = field(default_factory=dict)


def build_alias_map(items: Iterable[dict]) -> dict[int, AliasInfo]:
    """``{seq: AliasInfo}`` from ``{wi_seq, kind, title, item_id}`` dicts."""
    out: dict[int, AliasInfo] = {}
    for it in items or []:
        if not isinstance(it, dict):
            continue
        seq = it.get("wi_seq")
        if seq is None:
            continue
        try:
            seq = int(seq)
        except (TypeError, ValueError):
            continue
        if seq in out:
            continue
        out[seq] = AliasInfo(
            seq=seq,
            kind=str(it.get("kind") or ""),
            title=str(it.get("title") or ""),
            item_id=str(it.get("item_id") or it.get("artifact_id") or ""),
        )
    return out


def _alias_seq(alias: str) -> int | None:
    m = _ALIAS_RE.fullmatch((alias or "").strip())
    return int(m.group(1)) if m else None


def _normalise_exhibits(
    exhibits: list[ExhibitRef],
    aliases: dict[int, AliasInfo],
    stats: SanitizeStats,
) -> list[ExhibitRef]:
    """Keep only attachment exhibits with a unique ``n`` and unique alias.

    The writer's own numbers are trusted (the body already says «مرفق رقم n»
    with them), so nothing is renumbered — entries are only dropped.
    """
    kept: list[ExhibitRef] = []
    seen_n: set[int] = set()
    seen_seq: set[int] = set()
    for ex in sorted(exhibits or [], key=lambda e: e.n):
        seq = _alias_seq(ex.wi)
        info = aliases.get(seq) if seq is not None else None
        if (
            info is None
            or info.kind != ATTACHMENT_KIND
            or ex.n in seen_n
            or seq in seen_seq
        ):
            stats.exhibits_dropped += 1
            continue
        also: list[str] = []
        for a in ex.also_wi or []:
            s = _alias_seq(a)
            if s is None or s in seen_seq or s == seq:
                continue
            a_info = aliases.get(s)
            if a_info is None or a_info.kind != ATTACHMENT_KIND:
                continue
            also.append(f"WI-{s}")
            seen_seq.add(s)
        seen_n.add(ex.n)
        seen_seq.add(seq)
        kept.append(
            ExhibitRef(
                n=ex.n,
                wi=f"WI-{seq}",
                label_ar=(ex.label_ar or "").strip() or info.title,
                also_wi=also,
            )
        )
    return kept


def _fmt(n: int, arabic: bool) -> str:
    return str(n).translate(_TO_ARABIC_INDIC) if arabic else str(n)


def _uses_arabic_digits(sections: list[WriterSection]) -> bool:
    """True when the document's own digits are mostly Arabic-Indic."""
    text = "\n".join(f"{s.heading_ar}\n{s.body_md}" for s in sections or [])
    return len(_ARABIC_INDIC_RE.findall(text)) > len(_LATIN_DIGIT_RE.findall(text))


class _Numberer:
    """Resolves an alias seq to an exhibit number, minting new ones on demand."""

    def __init__(
        self,
        exhibits: list[ExhibitRef],
        aliases: dict[int, AliasInfo],
        stats: SanitizeStats,
        arabic_digits: bool = False,
    ) -> None:
        self.arabic_digits = arabic_digits
        self.exhibits = exhibits
        self.aliases = aliases
        self.stats = stats
        self.by_seq: dict[int, int] = {}
        for ex in exhibits:
            seq = _alias_seq(ex.wi)
            if seq is not None:
                self.by_seq[seq] = ex.n
            for a in ex.also_wi:
                s = _alias_seq(a)
                if s is not None:
                    self.by_seq[s] = ex.n

    def exhibit_for(self, seq: int) -> int | None:
        """Exhibit n for an attachment alias; ``None`` for any other kind."""
        info = self.aliases.get(seq)
        if info is None:
            self.stats.leaks_unknown += 1
            return None
        if info.kind != ATTACHMENT_KIND:
            self.stats.leaks_stripped += 1
            return None
        self.stats.leaks_attachment += 1
        n = self.by_seq.get(seq)
        if n is None:
            n = max((e.n for e in self.exhibits), default=0) + 1
            self.exhibits.append(
                ExhibitRef(n=n, wi=f"WI-{seq}", label_ar=info.title or f"مستند رقم {n}")
            )
            self.by_seq[seq] = n
            self.stats.exhibits_added += 1
        return n


def _rewrite_text(text: str, numberer: _Numberer) -> str:
    if not text or not _ALIAS_RE.search(text):
        return text

    def _group(m: re.Match) -> str:
        ns: list[int] = []
        for seq_s in _ALIAS_RE.findall(m.group("body")):
            n = numberer.exhibit_for(int(seq_s))
            if n is not None and n not in ns:
                ns.append(n)
        if not ns:
            return ""  # drop the parenthetical AND the space before it
        return f"{m.group('lead')}(" + "، ".join(f"مرفق رقم {_fmt(n, numberer.arabic_digits)}" for n in ns) + ")"

    text = _GROUP_RE.sub(_group, text)

    def _bare(m: re.Match) -> str:
        n = numberer.exhibit_for(int(m.group(1)))
        return (
            f"المرفق رقم {_fmt(n, numberer.arabic_digits)}"
            if n is not None else "ما سبق بيانه"
        )

    text = _ALIAS_RE.sub(_bare, text)
    text = _SPACE_BEFORE_PUNCT_RE.sub(r"\1", text)
    return _DOUBLE_SPACE_RE.sub(" ", text)


def _is_exhibits_heading(heading: str) -> bool:
    return heading.lstrip("#").strip() == "المرفقات"


def _exhibits_body(exhibits: list[ExhibitRef], arabic_digits: bool = False) -> str:
    return "\n".join(
        f"{_fmt(ex.n, arabic_digits)}. {ex.label_ar.strip()}"
        for ex in sorted(exhibits, key=lambda e: e.n)
    )


def sanitize_document(
    *,
    title: str,
    sections: list[WriterSection],
    exhibits: list[ExhibitRef],
    aliases: dict[int, AliasInfo],
    subtype: str,
) -> SanitizeResult:
    """Remove every ``WI-{seq}`` from the document and settle ``exhibits``.

    Never raises on content: an alias it cannot map is stripped, not refused.
    Idempotent — a second pass over its own output changes nothing.
    """
    stats = SanitizeStats()
    ex_list = _normalise_exhibits(exhibits, aliases, stats)
    arabic_digits = _uses_arabic_digits(sections)
    numberer = _Numberer(ex_list, aliases, stats, arabic_digits)

    new_title = _rewrite_text(title or "", numberer)
    new_sections: list[WriterSection] = []
    for sec in sections or []:
        new_sections.append(
            WriterSection(
                heading_ar=_rewrite_text(sec.heading_ar or "", numberer),
                body_md=_rewrite_text(sec.body_md or "", numberer),
            )
        )

    ex_list = numberer.exhibits
    renumber = _first_mention_order(new_sections, ex_list)
    if renumber:
        stats.renumbered = True
        new_sections = [
            WriterSection(
                heading_ar=_apply_renumber(s.heading_ar, renumber, _BODY_REF_RE),
                body_md=_apply_renumber(s.body_md, renumber, _BODY_REF_RE),
            )
            for s in new_sections
        ]
        ex_list = [ex.model_copy(update={"n": renumber.get(ex.n, ex.n)}) for ex in ex_list]
    if ex_list and subtype in _EXHIBIT_LIST_SUBTYPES:
        body = _exhibits_body(ex_list, arabic_digits)
        idx = next(
            (i for i, s in enumerate(new_sections) if _is_exhibits_heading(s.heading_ar)),
            None,
        )
        if idx is None:
            new_sections.append(WriterSection(heading_ar=_EXHIBITS_HEADING, body_md=body))
        else:
            # The structured list is authoritative — rebuild the LLM's section
            # from it and move it to the end of the document.
            new_sections.pop(idx)
            new_sections.append(WriterSection(heading_ar=_EXHIBITS_HEADING, body_md=body))

    return SanitizeResult(
        title=new_title,
        sections=new_sections,
        exhibits=sorted(ex_list, key=lambda e: e.n),
        stats=stats,
        renumber=renumber,
    )


def _first_mention_order(
    sections: list[WriterSection], exhibits: list[ExhibitRef]
) -> dict[int, int]:
    """``{old_n: new_n}`` so exhibits number 1..K by first mention in the body.

    The «المرفقات» section is skipped (it is rebuilt from the list). Exhibits
    never mentioned keep their relative order after the mentioned ones.
    Returns ``{}`` when the numbering is already in order.
    """
    known = {ex.n for ex in exhibits}
    order: list[int] = []
    for sec in sections:
        if _is_exhibits_heading(sec.heading_ar):
            continue
        for m in _BODY_REF_RE.finditer(f"{sec.heading_ar}\n{sec.body_md}"):
            n = int(m.group(2))
            if n in known and n not in order:
                order.append(n)
    order += [ex.n for ex in sorted(exhibits, key=lambda e: e.n) if ex.n not in order]
    mapping = {old: new for new, old in enumerate(order, start=1)}
    return {} if all(o == n for o, n in mapping.items()) else mapping


def _apply_renumber(text: str, mapping: dict[int, int], pattern: re.Pattern) -> str:
    """Rewrite exhibit numbers in one pass (no 4→1→… chaining)."""
    if not text or not mapping:
        return text

    def _sub(m: re.Match) -> str:
        old = int(m.group(2))
        arabic = bool(_ARABIC_INDIC_RE.search(m.group(2)))
        return f"{m.group(1)}{_fmt(mapping.get(old, old), arabic)}"

    return pattern.sub(_sub, text)


def renumber_chat_refs(text: str, mapping: dict[int, int]) -> str:
    """Apply a sanitize renumbering to LLM chat text («مرفق 3» → «مرفق 1»)."""
    return _apply_renumber(text, mapping, _CHAT_REF_RE)


def exhibits_metadata(
    exhibits: list[ExhibitRef], aliases: dict[int, AliasInfo]
) -> list[dict]:
    """Serialise exhibits for ``workspace_items.metadata.exhibits``."""
    out: list[dict] = []
    for ex in exhibits:
        seq = _alias_seq(ex.wi)
        info = aliases.get(seq) if seq is not None else None
        out.append({
            "n": ex.n,
            "wi": ex.wi,
            "item_id": info.item_id if info else None,
            "label_ar": ex.label_ar,
            "also_wi": list(ex.also_wi),
        })
    return out


def format_exhibit_guide_ar(exhibits: list[dict]) -> str:
    """Chat block telling the lawyer which card is which مرفق. Empty → ""."""
    rows = sorted(
        (e for e in exhibits or [] if isinstance(e, dict) and e.get("n")),
        key=lambda e: int(e["n"]),
    )
    if not rows:
        return ""
    lines = ["**ما تُرفقه مع المستند:**"]
    for e in rows:
        line = f"• مرفق ({int(e['n'])}) — {str(e.get('label_ar') or '').strip()} ← {e.get('wi')}"
        also = [a for a in (e.get("also_wi") or []) if a]
        if also:
            line += f" (مرفوع أيضاً باسم {'، '.join(also)} — تكفي نسخة واحدة)"
        lines.append(line)
    return "\n".join(lines)


def annotate_chat_aliases(text: str, exhibits: list[dict]) -> str:
    """«WI-9» in a chat line → «مرفق 2 · WI-9» when WI-9 is an exhibit.

    Chat keeps the alias (it is how the user finds the card); this only adds
    the exhibit number so bullets agree with the document and the guide.
    """
    if not text or not exhibits:
        return text
    by_seq: dict[int, int] = {}
    for e in exhibits:
        if not isinstance(e, dict) or not e.get("n"):
            continue
        for a in [e.get("wi"), *(e.get("also_wi") or [])]:
            s = _alias_seq(a or "")
            if s is not None:
                by_seq[s] = int(e["n"])

    def _sub(m: re.Match) -> str:
        n = by_seq.get(int(m.group(1)))
        return f"مرفق {n} · WI-{m.group(1)}" if n is not None else m.group(0)

    return _CHAT_ALIAS_RE.sub(_sub, text)


__all__ = [
    "AliasInfo",
    "SanitizeResult",
    "SanitizeStats",
    "annotate_chat_aliases",
    "build_alias_map",
    "exhibits_metadata",
    "format_exhibit_guide_ar",
    "renumber_chat_refs",
    "sanitize_document",
]
