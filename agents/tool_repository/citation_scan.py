"""Citation scan — every «المادة / نظام / لائحة» mention in an attachment's text. No model.

Plan: ``.claude/plans/completion_search.md`` §4.6.1 (owner decision C14: "a reliable
tool for something like نظام, لائحة, مادة in the attachment, and return what found").

``scan_citations(content_md, doc_type, titles=None)`` is pure code: no database, no
model, the same input gives the same output. It lists

- **article mentions** — «المادة 77», «المادة (٧٧)», «المادة السابعة والسبعون»,
  «المادتين 76 و77», «المواد من 10 إلى 15», «في مادته الخامسة»; each with its
  character span in ``content_md``, its paragraph number and the article numbers
  as Latin digits;
- **law references** — a law named in full («نظام العمل», «اللائحة التنفيذية لنظام
  المرافعات الشرعية») and back-references («هذا النظام», «اللائحة», «لائحته
  التنفيذية»);
- for every mention, **how it is tied to a law** (``basis``) — the fact the caller
  uses to decide whether the link can be checked by code.

``titles`` is the library's list of regulation titles (``TitleIndex``). It is DATA
handed in by the caller, so the scan stays pure. With it, a law is recognised when
the words in the text equal a library title exactly — «نظام المعاملات المدنية الصادر
بالمرسوم…» stops at the title, and titles that do not start with «نظام / لائحة»
(«قواعد …», «ضوابط …») are found too. Without it, only the head words are used.

The document's **own headings** («المادة الخامسة: مدة العقد» at the start of a line)
are marked ``heading`` and never sent for resolution.

Positions are offsets into the ORIGINAL ``content_md``. Matching runs on a folded
copy (tashkeel and tatweel removed; alef, hamza, taa-marbuta and digit shapes
unified) with an index map back to the original.
"""
from __future__ import annotations

import bisect
import re
from dataclasses import dataclass, field
from typing import Iterable, Sequence

from agents.memory.attachment_segmenter.paragraphs import paragraphize
from agents.tool_repository.fetch_article import (
    _ORD_HUNDREDS,
    _ORD_TEENS,
    _ORD_TENS,
    _ORD_UNITS,
    _ordinal_token_variants,
)

#: Stored on every scan row. Bump when a rule below changes what is found.
SCAN_RULE_VERSION = "cs-scan-v2"

#: A range such as «المواد من 10 إلى 15» is expanded up to this many articles.
MAX_RANGE_ARTICLES = 10
#: Largest article number taken as an article (the largest law has 721 articles).
MAX_ARTICLE_NUMBER = 1_500
#: A law name is read for at most this many words.
MAX_LAW_WORDS = 8
#: The longest sentence kept around a mention, in characters.
MAX_SENTENCE_CHARS = 300
#: How far after the article number the law may be named and still be "the same phrase".
SAME_PHRASE_WINDOW = 70

# --------------------------------------------------------------------------- #
# Folding: a same-meaning copy of the text plus a map back to the original.
# --------------------------------------------------------------------------- #

_AR = "ء-ي"
# Tashkeel, Quranic marks, tatweel, zero-width and direction marks — removed.
_DROP_RE = re.compile(
    "[ؐ-ًؚ-ٰٟۖ-ۭ࣓-ࣿـ"
    "​-‏‪-‮⁦-⁩﻿]+"
)
# One character → one character, so offsets survive.
_FOLD_TABLE = str.maketrans({
    "أ": "ا", "إ": "ا", "آ": "ا", "ٱ": "ا",
    "ة": "ه", "ى": "ي", "ؤ": "و", "ئ": "ي",
    "ک": "ك", "ی": "ي",
    "٠": "0", "١": "1", "٢": "2", "٣": "3", "٤": "4",
    "٥": "5", "٦": "6", "٧": "7", "٨": "8", "٩": "9",
    "۰": "0", "۱": "1", "۲": "2", "۳": "3", "۴": "4",
    "۵": "5", "۶": "6", "۷": "7", "۸": "8", "۹": "9",
    " ": " ", " ": " ", " ": " ", "\t": " ", "\r": " ",
    "﴿": "(", "﴾": ")", "［": "[", "］": "]", "（": "(", "）": ")",
})
_INDIC_DIGIT_RE = re.compile("[٠-٩۰-۹]")


class _Folded:
    """The folded text and the way back: ``orig(i)`` is the original offset of folded ``i``."""

    __slots__ = ("text", "_cuts", "_shift", "_orig_len")

    def __init__(self, original: str) -> None:
        cuts: list[int] = []
        shift: list[int] = []
        removed = 0
        for m in _DROP_RE.finditer(original):
            cuts.append(m.start() - removed)
            removed += m.end() - m.start()
            shift.append(removed)
        self._cuts = cuts
        self._shift = shift
        self._orig_len = len(original)
        self.text = _DROP_RE.sub("", original).translate(_FOLD_TABLE)

    def orig(self, pos: int) -> int:
        if pos >= len(self.text):
            return self._orig_len
        k = bisect.bisect_right(self._cuts, pos)
        return pos + (self._shift[k - 1] if k else 0)

    def orig_end(self, end: int) -> int:
        """Original offset just after folded character ``end - 1`` (an exclusive end)."""
        return self.orig(end - 1) + 1 if end > 0 else 0


def fold(text: str) -> str:
    """The folded form used for every comparison in this module."""
    return _DROP_RE.sub("", text or "").translate(_FOLD_TABLE)


# Letters that may stand in front of a word without making it another word: «والبيع», «للبيع».
_PROCLITICS = frozenset("و ف ب ل ك ال لل وال فال بال كال ولل فلل وب ول فب فل وك".split())


def find_folded(text: str, phrase: str) -> list[tuple[int, int, bool]]:
    """Every place ``phrase`` occurs in ``text``, tashkeel and letter shapes ignored.

    ``(start, end, whole)`` in ORIGINAL offsets. ``whole`` is False when the match is
    part of a longer word («بيع» inside «مبيعات»); a leading «و / ال / لل …» does not
    make it partial. Spaces in the phrase match any run of whitespace.
    """
    words = fold(phrase).split()
    if not text or not words:
        return []
    fv = _Folded(text)
    pattern = re.compile(r"\s+".join(re.escape(w) for w in words))
    out: list[tuple[int, int, bool]] = []
    for m in pattern.finditer(fv.text):
        left = m.start()
        while left > 0 and _WORD_RE.fullmatch(fv.text[left - 1]):
            left -= 1
        prefix = fv.text[left:m.start()]
        after = fv.text[m.end():m.end() + 1]
        whole = (not prefix or prefix in _PROCLITICS) and not (after and _WORD_RE.fullmatch(after))
        out.append((fv.orig(m.start()), fv.orig_end(m.end()), whole))
    return out


# --------------------------------------------------------------------------- #
# Tokens
# --------------------------------------------------------------------------- #

_TOKEN_RE = re.compile(rf"[{_AR}]+|\d+|\n|[^\s{_AR}\d]")
_WORD_RE = re.compile(rf"[{_AR}]+")


@dataclass(frozen=True)
class _Tok:
    kind: str      # "w" word · "d" digits · "p" punctuation
    text: str
    start: int     # folded offsets
    end: int
    gap: int       # line breaks between the previous token and this one


def _tokenize(folded: str) -> list[_Tok]:
    raw: list[_Tok] = []
    gap = 0
    for m in _TOKEN_RE.finditer(folded):
        t = m.group()
        if t == "\n":
            gap += 1
            continue
        kind = "w" if _WORD_RE.fullmatch(t) else ("d" if t.isdigit() else "p")
        raw.append(_Tok(kind, t, m.start(), m.end(), gap))
        gap = 0
    # A detached «و» is the conjunction of the next word: «المنافسات و المشتريات».
    out: list[_Tok] = []
    i = 0
    while i < len(raw):
        tok = raw[i]
        nxt = raw[i + 1] if i + 1 < len(raw) else None
        if tok.kind == "w" and tok.text == "و" and nxt and nxt.kind == "w" and nxt.gap == 0:
            out.append(_Tok("w", "و" + nxt.text, tok.start, nxt.end, tok.gap))
            i += 2
            continue
        out.append(tok)
        i += 1
    return out


def _title_words(title: str) -> tuple[str, ...]:
    """A title as the scan's word tuple: folded, punctuation dropped, «و» attached."""
    return tuple(t.text for t in _tokenize(fold(title)) if t.kind in ("w", "d"))


def _proclitic_variants(word: str) -> list[str]:
    """«بنظام» → نظام · «للنظام» → النظام · «واللائحة» → اللائحة · «للائحة» → اللائحة / لائحة."""
    out = [word]

    def add(v: str) -> None:
        if v and v not in out:
            out.append(v)

    bases = [word]
    if word[:1] in "وف" and len(word) > 3:
        bases.append(word[1:])
        add(word[1:])
    for base in bases:
        if base.startswith("لل") and len(base) > 4:
            add("ال" + base[2:])   # للنظام → النظام
            add("ا" + base)        # للايحه → اللايحه
            add(base[1:])          # للايحه → لايحه
        elif base[:1] in "بلك" and len(base) > 3:
            add(base[1:])          # بنظام → نظام · بالنظام → النظام
    return out


# --------------------------------------------------------------------------- #
# The library's titles, as data
# --------------------------------------------------------------------------- #


class TitleIndex:
    """Exact-title lookup over word tuples. Built from ``(regulation_id, title)`` pairs.

    Pure: the caller loads the rows. A title is matched only as a whole — the
    words in the text must equal the words of a library title. Titles of fewer
    than two words are left out (one word can never safely name a law).
    """

    MAX_WORDS = 16

    def __init__(self, rows: Iterable[tuple[str, str]]) -> None:
        self._ids: dict[tuple[str, ...], list[str]] = {}
        self._lengths: dict[str, list[int]] = {}
        self._prefixes: dict[tuple[str, ...], list[tuple[str, ...]]] | None = None
        self._loose: dict[tuple[str, ...], list[tuple[str, ...]]] | None = None
        for reg_id, title in rows:
            words = _title_words(title or "")
            if not 2 <= len(words) <= self.MAX_WORDS:
                continue
            ids = self._ids.setdefault(words, [])
            if reg_id not in ids:
                ids.append(reg_id)
        by_first: dict[str, set[int]] = {}
        for words in self._ids:
            by_first.setdefault(words[0], set()).add(len(words))
        self._lengths = {first: sorted(ns, reverse=True) for first, ns in by_first.items()}

    def __len__(self) -> int:
        return len(self._ids)

    def ids(self, key: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(self._ids.get(key, ()))

    @staticmethod
    def _without_al(words: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(w[2:] if w.startswith("ال") and len(w) > 4 else w for w in words)

    def spelled_loosely(self, key: tuple[str, ...]) -> list[tuple[str, ...]]:
        """Titles that equal ``key`` once «ال» is ignored on every word — «نظام اثبات»
        for «نظام الإثبات». A guess, never an exact match: the caller treats it as such."""
        if len(key) < 2:
            return []
        if self._loose is None:
            loose: dict[tuple[str, ...], list[tuple[str, ...]]] = {}
            for words in self._ids:
                loose.setdefault(self._without_al(words), []).append(words)
            self._loose = loose
        return [t for t in self._loose.get(self._without_al(key), ()) if t != key]

    def starting_with(self, key: tuple[str, ...]) -> list[tuple[str, ...]]:
        """Titles that begin with ``key`` — «نظام المرافعات» begins two of them."""
        if len(key) < 2:
            return []
        if self._prefixes is None:
            prefixes: dict[tuple[str, ...], list[tuple[str, ...]]] = {}
            for words in self._ids:
                for n in range(2, len(words)):
                    prefixes.setdefault(words[:n], []).append(words)
            self._prefixes = prefixes
        return list(self._prefixes.get(key, ()))

    def longest(self, toks: Sequence[_Tok], i: int, first: str) -> tuple[int, tuple[str, ...]] | None:
        """The longest title that starts at token ``i`` (read as ``first``): ``(n_tokens, key)``."""
        lengths = self._lengths.get(first)
        if not lengths:
            return None
        run = [first]
        j = i + 1
        longest = lengths[0]
        while j < len(toks) and len(run) < longest:
            tok = toks[j]
            if tok.kind not in ("w", "d") or tok.gap > 1:
                break
            run.append(tok.text)
            j += 1
        for n in lengths:
            if n <= len(run):
                key = tuple(run[:n])
                if key in self._ids:
                    return n, key
        return None


# --------------------------------------------------------------------------- #
# Result types
# --------------------------------------------------------------------------- #

#: A law named in the text.
KIND_NAMED = "named"
#: «النظام», «هذا النظام», «اللائحة» — the law is the one named before.
KIND_BACK_REF = "back_ref"
#: «اللائحة التنفيذية», «لائحته التنفيذية» — the executive regulation of the law named before.
KIND_EXEC_BACK_REF = "exec_back_ref"
#: Looks like a law name but is not one: a pleading («لائحة الدعوى») or a company's own
#: bylaws («النظام الأساسي للشركة»). Never resolved.
KIND_NOT_A_LAW = "not_a_law"

#: The law is named in the same phrase as the article — «المادة 77 من نظام العمل».
BASIS_SAME_PHRASE = "same_phrase"
#: «المادة 77 من النظام» — a back-reference to the law named before.
BASIS_BACK_REF = "back_ref"
#: No law in the phrase, but the sentence names exactly one.
BASIS_SAME_SENTENCE = "same_sentence"
#: No law in the phrase; the whole document names exactly one law.
BASIS_SINGLE_LAW = "single_law"
#: No law in the phrase; the document names several — the nearest one before is a guess.
BASIS_NEAREST = "nearest"
#: The article belongs to something that is not a law — «المادة 5 من العقد».
BASIS_OTHER_TARGET = "other_target"
#: Nothing in the document names a law.
BASIS_NO_LAW = "no_law"


@dataclass(frozen=True)
class LawRef:
    seq: int
    char_start: int
    char_end: int
    text: str                       # as written in the document
    kind: str
    key: tuple[str, ...] = ()       # the folded words; equal keys = the same name
    in_library: bool = False        # ``key`` is a library title (only with a TitleIndex)
    open_end: bool = False          # the next word may still belong to the name («نظام العمل والعمال»)
    exec_of: bool = False           # «اللائحة التنفيذية ل…»: the executive regulation OF the law in ``key``


@dataclass(frozen=True)
class Mention:
    seq: int
    char_start: int
    char_end: int
    para_n: int
    numbers: tuple[str, ...]        # Latin digits — "77", "76/1", "25 مكرر"
    number_form: str                # digits | indic | words
    plural: bool = False
    range_wide: bool = False        # a range wider than MAX_RANGE_ARTICLES — endpoints only
    compound: bool = False          # "76/1" style — the plain number is a fallback, not a fact
    conflict: bool = False          # written twice («(31) الثانية والثلاثين») and the two disagree
    heading: bool = False           # the document's own clause heading
    line_start: bool = False
    basis: str = BASIS_NO_LAW
    law: int | None = None          # index into ScanResult.laws — the NAMED law
    via: int | None = None          # the back-reference it went through
    exec_reg: bool = False          # the executive regulation of ``law``
    inherited: bool = False         # shares the next mention's law («المادة 5 والمادة 6 من …»)
    other_target: str = ""
    laws_before: int = 0            # distinct named laws in the 1,500 characters before (back_ref / nearest)
    sentence_start: int = 0
    sentence_end: int = 0


@dataclass
class ScanResult:
    mentions: list[Mention] = field(default_factory=list)
    laws: list[LawRef] = field(default_factory=list)
    paragraphs: int = 0
    content_chars: int = 0
    rule_version: str = SCAN_RULE_VERSION

    @property
    def citing(self) -> list[Mention]:
        """Mentions that cite something — headings left out."""
        return [m for m in self.mentions if not m.heading]

    @property
    def named_laws(self) -> list[LawRef]:
        return [law for law in self.laws if law.kind == KIND_NAMED]

    def distinct_named(self) -> set[tuple]:
        return {(law.key, law.exec_of) for law in self.named_laws}


# --------------------------------------------------------------------------- #
# Article numbers
# --------------------------------------------------------------------------- #

_HEAD_RE = re.compile(
    rf"(?<![{_AR}])(?:"
    rf"[وف]?(?:[بك]?ال|لل)(?:(?P<sing>ماده)|(?P<dual>مادت(?:ين|ان))|(?P<plur>مواد))"
    rf"|[وف]?[بلك]?(?P<poss>مادت(?:ه|ها|هما))"
    rf")(?![{_AR}])"
)
_WS = r"[ ]*\n?[ ]*"                      # spaces with at most one line break
_PRE_NUMBER_RE = re.compile(rf"{_WS}(?:(?:ال)?(?:رقم|ارقام){_WS}[:/]?{_WS}|/{_WS})?")
_OPEN_RE = re.compile(r"[(\[{«\"]\s*")
_CLOSE_RE = re.compile(r"\s*[)\]}»\"]")
_DIGIT_ITEM_RE = re.compile(
    r"(?P<a>\d{1,4})(?!\d)"
    r"(?:\s?(?P<sep>[/\\.])\s?(?P<b>\d{1,3})(?!\d))?"
    r"(?:\s+(?P<bis>مكرر[ه]?))?"
)
_DASH_COMPOUND_RE = re.compile(r"\s?-\s?(?P<b>\d{1,3})(?!\d)")
_LIST_SEP_RE = re.compile(rf"{_WS}(?:،|,|و|او){_WS}")
_RANGE_SEP_RE = re.compile(rf"{_WS}(?:-|–|—|الي|حتي){_WS}")
_FROM_RE = re.compile(rf"{_WS}من\s+")
# «المادة 76 و77 من نظام …» — a singular head followed by more numbers, accepted only
# when the list runs straight into «من».
_SINGULAR_LIST_RE = re.compile(
    rf"(?P<list>(?:{_WS}(?:،|,|و){_WS}[(\[]?\s*\d{{1,4}}(?!\d)\s*[)\]]?)+)(?={_WS}من\s)"
)
_SPACES_RE = re.compile(_WS)
_COMMA_RE = re.compile(rf"{_WS}[،,]{_WS}")
# «… الحادية والثلاثين (31)» — the same number again, in brackets.
_ECHO_DIGITS_RE = re.compile(rf"{_WS}[(\[]\s*(?P<n>\d{{1,4}})\s*[)\]]")


def _ord_class(token: str) -> tuple[str, int, bool] | None:
    """``(class, value, has_conjunction)`` for one ordinal word, else ``None``."""
    for i, variant in enumerate(_ordinal_token_variants(token)):
        conj = i == 1
        if variant in _ORD_UNITS:
            return "unit", _ORD_UNITS[variant], conj
        if variant in _ORD_TENS:
            return "tens", _ORD_TENS[variant], conj
        if variant in _ORD_HUNDREDS:
            return "hundreds", _ORD_HUNDREDS[variant], conj
        if variant in _ORD_TEENS:
            return "teen", 10, conj
        if variant == "بعد" and not conj:
            return "after", 0, False
    return None


@dataclass
class _Num:
    unit: int = 0
    teen: bool = False
    tens: int = 0
    hundreds: int = 0
    hundreds_first: bool = False
    closed: bool = False

    @property
    def value(self) -> int:
        return self.unit + (10 if self.teen else 0) + self.tens + self.hundreds


def _parse_ordinals(folded: str, pos: int) -> tuple[list[int], int]:
    """Ordinal words from ``pos``: ``([numbers], end)``; ``([], pos)`` when there are none.

    Reads Arabic number grammar, not a plain sum: «السابعة والسبعون» is 77, but
    «الثانية والثالثة» is two articles, 2 and 3.
    """
    numbers: list[int] = []
    cur: _Num | None = None
    end = pos
    cursor = pos
    words: list[tuple[str, int]] = []

    def next_word(at: int) -> tuple[str, int, int] | None:
        m = _SPACES_RE.match(folded, at)
        start = m.end() if m else at
        w = _WORD_RE.match(folded, start)
        if not w:
            return None
        return w.group(), start, w.end()

    while len(words) < 9:
        got = next_word(cursor)
        if not got:
            break
        token, _start, tok_end = got
        cls = _ord_class(token)
        if cls is None:
            break
        kind, value, conj = cls
        if kind == "after":
            # «… بعد المائة» — only when a hundreds word follows.
            if cur is None or cur.closed or cur.hundreds:
                break
            nxt = next_word(tok_end)
            nxt_cls = _ord_class(nxt[0]) if nxt else None
            if not nxt_cls or nxt_cls[0] != "hundreds" or nxt_cls[2]:
                break
            cur.hundreds = nxt_cls[1]
            cur.closed = True
            cursor = end = nxt[2]
            words.append((token, tok_end))
            continue
        if kind == "unit":
            if cur is None:
                cur = _Num(unit=value)
            elif cur.hundreds_first and not cur.unit and not cur.tens and conj and not cur.closed:
                cur.unit = value
            elif conj:
                numbers.append(cur.value)
                cur = _Num(unit=value)
            else:
                break
        elif kind == "teen":
            if cur and 1 <= cur.unit <= 9 and not cur.teen and not cur.tens and not conj and not cur.closed:
                cur.teen = True
            else:
                break
        elif kind == "tens":
            if cur is None:
                cur = _Num(tens=value)
            elif not cur.tens and not cur.teen and not cur.closed and conj and (
                1 <= cur.unit <= 9 or (cur.hundreds_first and cur.unit <= 9)
            ):
                cur.tens = value
            elif conj:
                numbers.append(cur.value)
                cur = _Num(tens=value)
            else:
                break
        else:  # hundreds
            if cur is None:
                cur = _Num(hundreds=value, hundreds_first=True)
            elif conj and not cur.hundreds and not cur.closed:
                cur.hundreds = value
                cur.closed = True
            elif conj:
                numbers.append(cur.value)
                cur = _Num(hundreds=value, hundreds_first=True)
            else:
                break
        cursor = end = tok_end
        words.append((token, tok_end))
    if cur is not None:
        numbers.append(cur.value)
    numbers = [n for n in numbers if 0 < n <= MAX_ARTICLE_NUMBER]
    return (numbers, end) if numbers else ([], pos)


@dataclass
class _Parsed:
    numbers: list[str]
    form: str
    end: int
    range_wide: bool = False
    compound: bool = False
    conflict: bool = False      # the number is written twice and the two do not agree


def _expand(a: int, b: int) -> tuple[list[str], bool]:
    """A range ``a..b`` → its numbers, or the two endpoints and ``True`` when it is too wide."""
    if b <= a:
        return [str(a), str(b)], False
    if b - a + 1 > MAX_RANGE_ARTICLES:
        return [str(a), str(b)], True
    return [str(n) for n in range(a, b + 1)], False


def _digit_item(folded: str, pos: int, *, dash_compound: bool) -> tuple[str, int, int, bool] | None:
    """One number written in digits: ``(text, plain_int, end, compound)``."""
    m = _DIGIT_ITEM_RE.match(folded, pos)
    if not m:
        return None
    a = int(m.group("a"))
    if not 0 < a <= MAX_ARTICLE_NUMBER:
        return None
    text = m.group("a")
    end = m.end("a")
    compound = False
    if m.group("b"):
        sep = "/" if m.group("sep") in "/\\" else "."
        text = f"{m.group('a')}{sep}{m.group('b')}"
        end = m.end("b")
        compound = True
    elif dash_compound:
        d = _DASH_COMPOUND_RE.match(folded, end)
        if d:
            text = f"{m.group('a')}-{d.group('b')}"
            end = d.end()
            compound = True
    if m.group("bis") and m.start("bis") >= end:
        text = f"{text} مكرر"
        end = m.end("bis")
    return text, a, end, compound


def _parse_numbers(folded: str, has_indic, pos: int, *, plural: bool) -> _Parsed | None:
    """The article number(s) that follow a head word ending at ``pos``."""
    pre = _PRE_NUMBER_RE.match(folded, pos)
    cursor = pre.end() if pre else pos
    if plural:
        frm = _FROM_RE.match(folded, cursor)
        if frm:
            cursor = frm.end()
    opened = _OPEN_RE.match(folded, cursor)
    if opened:
        cursor = opened.end()

    def close(at: int) -> int:
        c = _CLOSE_RE.match(folded, at)
        return c.end() if c else at

    if cursor < len(folded) and folded[cursor].isdigit():
        item = _digit_item(folded, cursor, dash_compound=not plural)
        if not item:
            return None
        text, value, end, compound = item
        numbers = [text]
        range_wide = False
        last_value = value
        if plural:
            while True:
                end_closed = close(end)
                sep = _LIST_SEP_RE.match(folded, end_closed)
                rng = _RANGE_SEP_RE.match(folded, end_closed)
                nxt_at = None
                is_range = False
                if rng:
                    nxt_at, is_range = rng.end(), True
                elif sep:
                    nxt_at = sep.end()
                if nxt_at is None:
                    break
                o = _OPEN_RE.match(folded, nxt_at)
                if o:
                    nxt_at = o.end()
                nxt = _digit_item(folded, nxt_at, dash_compound=False) if nxt_at < len(folded) else None
                if not nxt:
                    break
                n_text, n_value, n_end, n_compound = nxt
                compound = compound or n_compound
                if is_range and not n_compound:
                    expanded, wide = _expand(last_value, n_value)
                    numbers = numbers[:-1] + expanded
                    range_wide = range_wide or wide
                else:
                    numbers.append(n_text)
                last_value = n_value
                end = n_end
        else:
            more = _SINGULAR_LIST_RE.match(folded, close(end))
            if more:
                for d in re.finditer(r"\d{1,4}", more.group("list")):
                    if 0 < int(d.group()) <= MAX_ARTICLE_NUMBER:
                        numbers.append(d.group())
                end = more.end("list")
        end = close(end)
        form = "indic" if has_indic(cursor, end) else "digits"
        conflict = False
        if len(numbers) == 1 and not compound:
            # Courts write the number twice — «المادة (31) الحادية والثلاثين من نظام …».
            # The words belong to the mention; when they name a DIFFERENT number the
            # digits were probably misread (a scan), and the mention is not trusted.
            echo, echo_end = _parse_ordinals(folded, end)
            if echo:
                conflict = echo != [value]
                end = close(echo_end)
        return _Parsed(_dedupe(numbers), form, end, range_wide, compound, conflict)

    values, end = _parse_ordinals(folded, cursor)
    if not values:
        return None
    numbers = [str(v) for v in values]
    range_wide = False
    if plural:
        while True:   # «المواد الخامسة، والسادسة، والسابعة»
            comma = _COMMA_RE.match(folded, end)
            more, more_end = _parse_ordinals(folded, comma.end()) if comma else ([], end)
            if not more:
                break
            values = values + more
            numbers += [str(v) for v in more]
            end = more_end
        rng = _RANGE_SEP_RE.match(folded, end)
        if rng:
            more, more_end = _parse_ordinals(folded, rng.end())
            if more:
                expanded, range_wide = _expand(values[-1], more[0])
                numbers = numbers[:-1] + expanded + [str(v) for v in more[1:]]
                end = more_end
    end = close(end)
    conflict = False
    if len(values) == 1 and not range_wide:
        echo = _ECHO_DIGITS_RE.match(folded, end)
        if echo:
            conflict = int(echo.group("n")) != values[0]
            end = echo.end()
    return _Parsed(_dedupe(numbers), "words", end, range_wide, False, conflict)


def _dedupe(items: Sequence[str]) -> list[str]:
    out: list[str] = []
    for item in items:
        if item not in out:
            out.append(item)
    return out


# --------------------------------------------------------------------------- #
# Law references
# --------------------------------------------------------------------------- #

# A law name never runs through these words.
_NAME_STOPS = frozenset("""
الصادر الصادره المعدل المعدله الجديد القديم السابق الحالي المشار المذكور المذكوره
التي الذي اللتي اللذين والتي والذي حيث ان بان لان اذ اذا قد لا لم لن هو هي كما او ثم
بل لكن نص نصت تنص ينص وتنص وينص ونصت المتضمن المتضمنه والمتضمن يقضي تقضي بموجب وفق
وفقا طبقا استنادا بتاريخ رقم برقم لعام لسنه وذلك ذلك هذا هذه علي الي عن مع بين عند
لدي حتي منذ خلال كان كانت يكون تكون ليس فان وان فقد وقد مما ما في من وفي ومن وعلي
الماده للماده والماده بالماده المواد ولايحته ولايحتها ولوايحه ذاته نفسه ذاتها نفسها
اعلاه انف سالف انه بانه لانه فانه وانه انها بانها لانها كونه لكونه يجيز تجيز يوجب
توجب يلزم تلزم يمنع تمنع يحظر يشترط يقرر تقرر
""".split())
# «النظام المذكور», «اللائحة المشار إليها» — the word after a back-reference.
_BACK_REF_NEXT = frozenset("""
المذكور المذكوره المشار ذاته ذاتها نفسه نفسها اعلاه انف الانف سالف السالف المنوه
الموما المعني المعنيه المتقدم المتقدمه المنظم
""".split())
_DEMONSTRATIVES = frozenset("هذا هذه لهذا لهذه بهذا بهذه وهذا وهذه ذات نفس لذات بذات لنفس بنفس ذلك تلك".split())
# «لائحة الدعوى» and its family are pleadings, not regulations.
_PLEADING_WORDS = frozenset("""
دعوي الدعوي دعواه دعواها دعواهم اعتراضيه الاعتراضيه اعتراض الاعتراض اعتراضه اعتراضها
استيناف الاستيناف استينافيه الاستينافيه جوابيه الجوابيه الجواب ادعاء الادعاء اتهام
الاتهام التماس الالتماس المدعي المدعيه الطعن طعن النقض الرد رد مذكره تظلم التظلم
المطالبه مطالبه ايضاحيه الايضاحيه تمييز التمييز المستانف المستانفه المعترض المعترضه
""".split())
# «نظام الشركة», «النظام الأساسي» (of a company), «النظام العام» (public order).
_OWN_RULES_WORDS = frozenset("""
الشركه شركه الجمعيه جمعيه الموسسه موسسه الصندوق صندوق النادي داخلي الداخلي الكتروني
الالكتروني اساسي الاساسي الاساس المنشاه
""".split())
_PUBLIC_ORDER = "العام"
# «نظام آخر», «نظام جديد» — the common noun, not a name.
_GENERIC_AFTER_NIZAM = frozenset("""
اخر اخري جديد قديم خاص معين محدد قانوني قضايي الي ما واحد مماثل مشابه بديل موحد متكامل
""".split())


@dataclass(frozen=True)
class _RawLaw:
    start: int                      # folded offsets
    end: int
    kind: str
    key: tuple[str, ...]
    in_library: bool = False
    open_end: bool = False
    exec_of: bool = False


def _find_laws(folded: str, toks: Sequence[_Tok], titles: TitleIndex | None) -> list[_RawLaw]:
    """Every law reference, in text order."""
    out: list[_RawLaw] = []
    n = len(toks)

    def word_at(j: int, max_gap: int = 1) -> str | None:
        if 0 <= j < n and toks[j].kind == "w" and toks[j].gap <= max_gap:
            return toks[j].text
        return None

    def read_name(i: int, first: str, taken: int = 1) -> tuple[int, tuple[str, ...]]:
        """Words of a name starting at ``i``: ``taken`` already accepted, then up to the first stop."""
        words = [first] + [toks[k].text for k in range(i + 1, i + taken)]
        j = i + taken
        while j < n and len(words) < MAX_LAW_WORDS:
            w = word_at(j)
            if w is None or w in _NAME_STOPS:
                break
            words.append(w)
            j += 1
        return j, tuple(words)

    def generic(prev: str | None, nxt: str | None) -> bool:
        """«النظام» / «اللائحة» used for "the law", not as the first word of a name.

        With the library's titles at hand a real name starting with the definite
        word has already matched one («النظام الأساسي للحكم»), so whatever is left
        («النظام السعودي», «النظام واللائحة») is the general word.
        """
        return (titles is not None or prev in _DEMONSTRATIVES or nxt is None
                or nxt in _BACK_REF_NEXT or nxt in _NAME_STOPS)

    i = 0
    while i < n:
        tok = toks[i]
        if tok.kind != "w":
            i += 1
            continue
        hit: tuple | None = None   # (next_i, kind, key, in_library, exec_of)
        variants = _proclitic_variants(tok.text)
        if titles is not None:
            for v in variants:
                found = titles.longest(toks, i, v)
                if found:
                    hit = (i + found[0], KIND_NAMED, found[1], True, False)
                    break
        if hit is None:
            prev = word_at(i - 1, max_gap=9) if i else None
            nxt = word_at(i + 1)
            for v in variants:
                if v == "نظام":
                    if nxt is None or nxt in _NAME_STOPS or nxt in _GENERIC_AFTER_NIZAM:
                        break
                    if nxt in _OWN_RULES_WORDS:
                        hit = (i + 2, KIND_NOT_A_LAW, ("نظام", nxt), False, False)
                        break
                    j, key = read_name(i, "نظام")
                    hit = (j, KIND_NAMED, key, False, False)
                    break
                if v == "النظام":
                    if nxt == _PUBLIC_ORDER:
                        break
                    if nxt in _OWN_RULES_WORDS:
                        hit = (i + 2, KIND_NOT_A_LAW, ("النظام", nxt), False, False)
                    elif generic(prev, nxt):
                        hit = (i + 1, KIND_BACK_REF, ("النظام",), False, False)
                    else:
                        j, key = read_name(i, "النظام")
                        hit = (j, KIND_NAMED, key, False, False)
                    break
                if v == "اللايحه":
                    if nxt == "التنفيذيه":
                        after = word_at(i + 2)
                        after2 = word_at(i + 3)
                        if after in _DEMONSTRATIVES and after2 == "النظام":
                            hit = (i + 4, KIND_EXEC_BACK_REF, ("اللايحه", "التنفيذيه"), False, False)
                        elif after == "للنظام":
                            hit = (i + 3, KIND_EXEC_BACK_REF, ("اللايحه", "التنفيذيه"), False, False)
                        elif after and after.startswith("ل") and len(after) > 3 and after not in _NAME_STOPS:
                            # «اللائحة التنفيذية لنظام العمل»: the executive regulation OF a library law.
                            parent = None
                            if titles is not None:
                                firsts = [after[1:]] + (["ال" + after[2:]] if after.startswith("لل") else [])
                                for first in firsts:
                                    parent = titles.longest(toks, i + 2, first)
                                    if parent:
                                        break
                            if parent:
                                hit = (i + 2 + parent[0], KIND_NAMED, parent[1], True, True)
                            else:
                                j, key = read_name(i, "اللايحه", taken=2)
                                hit = (j, KIND_NAMED, key, False, False)
                        else:
                            hit = (i + 2, KIND_EXEC_BACK_REF, ("اللايحه", "التنفيذيه"), False, False)
                    elif nxt in _PLEADING_WORDS or nxt in _OWN_RULES_WORDS:
                        hit = (i + 2, KIND_NOT_A_LAW, ("اللايحه", nxt), False, False)
                    elif generic(prev, nxt):
                        hit = (i + 1, KIND_BACK_REF, ("اللايحه",), False, False)
                    else:
                        j, key = read_name(i, "اللايحه")
                        hit = (j, KIND_NAMED, key, False, False)
                    break
                if v == "لايحه":
                    if nxt is None or nxt in _NAME_STOPS or nxt in _GENERIC_AFTER_NIZAM:
                        break
                    if nxt in _PLEADING_WORDS or nxt in _OWN_RULES_WORDS:
                        hit = (i + 2, KIND_NOT_A_LAW, ("لايحه", nxt), False, False)
                        break
                    j, key = read_name(i, "لايحه")
                    hit = (j, KIND_NAMED, key, False, False)
                    break
                if v in ("لايحته", "لايحتها", "لايحتهما"):
                    hit = (i + 2 if nxt == "التنفيذيه" else i + 1, KIND_EXEC_BACK_REF,
                           ("اللايحه", "التنفيذيه"), False, False)
                    break
        if hit is None:
            i += 1
            continue
        nxt_i, kind, key, in_library, exec_of = hit
        # «نظام العمل والعمال»: a title matched, but the text goes on with «و…» — the
        # name in the document may be a longer one than the library title.
        open_end = False
        if in_library and nxt_i < n:
            follow = toks[nxt_i]
            if follow.kind == "w" and follow.gap == 0 and follow.text.startswith("وال") and follow.text not in _NAME_STOPS:
                rest = follow.text[1:]
                open_end = rest not in ("النظام", "اللايحه", "الماده", "المواد") and not rest.startswith("المادت")
        out.append(_RawLaw(tok.start, toks[nxt_i - 1].end, kind, key, in_library, open_end, exec_of))
        i = nxt_i
    return out


# --------------------------------------------------------------------------- #
# The scan
# --------------------------------------------------------------------------- #

_LINE_PREFIX_RE = re.compile(r"^[\s#>*_\-–—•·|]*(?:[(\[]?\d{1,3}[)\].\-]\s*)?[\s*_]*$")
_HEADING_TAIL_RE = re.compile(r"^[\s*_]*(?:$|[:\-–—.])")
_QUOTED_CLAUSE_RE = re.compile(r"^[ ]*:")
_SENTENCE_STOP = ".؟!؛\n"
_PHRASE_BREAK_RE = re.compile(r"[.؟!؛:]|\n\s*\n")
# Between an article number and its law: brackets, a paragraph reference, then «من».
_TO_LAW_RE = re.compile(
    rf"^[\s)\]}}»\"]*(?:(?:و?(?:ال)?فقر[هت]\w*|(?:ال)?بند)\s*[(\[]?\s*[{_AR}\d/]{{1,12}}\s*[)\]]?\s*)?"
    rf"(?:من\s+(?:(?:هذا|هذه|ذات|نفس)\s+)?)?$"
)
_OTHER_TARGET_RE = re.compile(
    rf"[\s)\]}}»\"]*من\s+(?:(?:هذا|هذه|ذات|نفس)\s+)?(?P<word>[{_AR}]+)"
)
# A law named just before the article: «نظام العمل في مادته …», «نظام العمل (المادة 77)».
_FROM_LAW_RE = re.compile(r"^[\s(\[،,:\-–]{0,6}(?:و?في\s+)?$")
_CHAIN_RE = re.compile(r"^[\s)\]}»\"،,]*(?:و|او|ثم)?\s*$")
# «المادة 80 منه» — "of it": the law named before.
_OF_IT_RE = re.compile(rf"[\s)\]}}»\"]*من(?:ه|ها|هما)(?![{_AR}])")
_BACK_WINDOW = 1_500


def scan_citations(
    content_md: str,
    doc_type: str | None = None,
    titles: TitleIndex | None = None,
) -> ScanResult:
    """List every article mention and law reference in ``content_md``. Pure."""
    original = content_md or ""
    result = ScanResult(content_chars=len(original))
    if not original.strip():
        return result
    fv = _Folded(original)
    folded = fv.text
    toks = _tokenize(folded)

    paragraphs = paragraphize(original, doc_type=doc_type)
    result.paragraphs = len(paragraphs)
    para_starts = [p.char_start for p in paragraphs]

    def para_of(orig_pos: int) -> int:
        k = bisect.bisect_right(para_starts, orig_pos)
        return paragraphs[k - 1].n if k else (1 if paragraphs else 0)

    # ---- laws ----
    raw_laws = _find_laws(folded, toks, titles)
    laws: list[LawRef] = []
    for seq, r in enumerate(raw_laws):
        o_start, o_end = fv.orig(r.start), fv.orig_end(r.end)
        laws.append(LawRef(seq, o_start, o_end, original[o_start:o_end], r.kind, r.key,
                           r.in_library, r.open_end, r.exec_of))
    law_starts = [r.start for r in raw_laws]        # folded
    named_idx = [i for i, r in enumerate(raw_laws) if r.kind == KIND_NAMED]
    named_starts = [raw_laws[i].start for i in named_idx]
    distinct_named = {_law_identity(raw_laws[i]) for i in named_idx}

    def named_before(pos: int) -> int | None:
        k = bisect.bisect_left(named_starts, pos)
        return named_idx[k - 1] if k else None

    def distinct_before(pos: int, window: int) -> int:
        lo = bisect.bisect_left(named_starts, pos - window)
        hi = bisect.bisect_left(named_starts, pos)
        return len({_law_identity(raw_laws[named_idx[k]]) for k in range(lo, hi)})

    def has_indic(a: int, b: int) -> bool:
        return bool(_INDIC_DIGIT_RE.search(original[fv.orig(a):fv.orig_end(b)]))

    # ---- mentions ----
    drafts: list[dict] = []
    for head in _HEAD_RE.finditer(folded):
        plural = bool(head.group("dual") or head.group("plur"))
        parsed = _parse_numbers(folded, has_indic, head.end(), plural=plural)
        if not parsed:
            continue
        line_start_at = folded.rfind("\n", 0, head.start()) + 1
        line_end_at = folded.find("\n", parsed.end)
        if line_end_at < 0:
            line_end_at = len(folded)
        line_start = bool(_LINE_PREFIX_RE.match(folded[line_start_at:head.start()]))
        drafts.append({
            "start": head.start(), "end": parsed.end, "parsed": parsed, "plural": plural,
            "possessive": bool(head.group("poss")), "line_start": line_start,
            "line_start_at": line_start_at, "line_end_at": line_end_at,
        })

    links: list[dict] = [{} for _ in drafts]
    for i in range(len(drafts) - 1, -1, -1):
        d = drafts[i]
        start, end = d["start"], d["end"]
        link: dict = {"basis": BASIS_NO_LAW, "law": None, "via": None, "exec_reg": False,
                      "inherited": False, "other": "", "laws_before": 0}

        def take(ref_i: int) -> bool:
            ref = raw_laws[ref_i]
            if ref.kind == KIND_NAMED:
                link.update(basis=BASIS_SAME_PHRASE, law=ref_i, exec_reg=ref.exec_of)
                return True
            if ref.kind in (KIND_BACK_REF, KIND_EXEC_BACK_REF):
                target = named_before(ref.start)
                link.update(basis=BASIS_BACK_REF, law=target, via=ref_i,
                            exec_reg=ref.kind == KIND_EXEC_BACK_REF,
                            laws_before=distinct_before(ref.start, _BACK_WINDOW))
                return True
            if ref.kind == KIND_NOT_A_LAW:
                link.update(basis=BASIS_OTHER_TARGET, other=" ".join(ref.key))
                return True
            return False

        linked = False
        # 1. the law right after the number — «المادة 77 من نظام العمل»
        k = bisect.bisect_left(law_starts, end)
        if k < len(raw_laws) and raw_laws[k].start - end <= SAME_PHRASE_WINDOW:
            between = folded[end:raw_laws[k].start]
            if not _PHRASE_BREAK_RE.search(between) and _TO_LAW_RE.match(between):
                tied = between.rstrip().endswith(("من", "هذا", "هذه", "ذات", "نفس"))
                proclitic = folded[raw_laws[k].start] in "بل" and raw_laws[k].kind == KIND_NAMED
                if tied or proclitic:
                    linked = take(k)
        # 1b. «المادة 80 منه» — the law named before
        if not linked and _OF_IT_RE.match(folded, end):
            target = named_before(start)
            link.update(basis=BASIS_BACK_REF, law=target,
                        laws_before=distinct_before(start, _BACK_WINDOW))
            linked = True
        # 2. the law right before — «نظام العمل في مادته الخامسة»
        if not linked:
            k = bisect.bisect_right(law_starts, start) - 1
            if k >= 0 and 0 <= start - raw_laws[k].end <= 12:
                between = folded[raw_laws[k].end:start]
                if _FROM_LAW_RE.match(between) and (d["possessive"] or between.strip()):
                    linked = take(k)
        # 3. «المادة 5 والمادة 6 من نظام …» — share the next mention's law
        if not linked and i + 1 < len(drafts):
            nxt = links[i + 1]
            if _CHAIN_RE.match(folded[end:drafts[i + 1]["start"]]) and nxt.get("basis") in (
                BASIS_SAME_PHRASE, BASIS_BACK_REF, BASIS_OTHER_TARGET,
            ):
                link.update(nxt)
                link["inherited"] = True
                linked = True
        # 4. «المادة 5 من العقد» — it belongs to something that is not a law
        if not linked:
            m = _OTHER_TARGET_RE.match(folded, end)
            if m:
                link.update(basis=BASIS_OTHER_TARGET, other=m.group("word"))
                linked = True
        # 5. no law in the phrase — fall back on what the sentence / document names
        if not linked:
            s_lo, s_hi = _sentence(folded, start, end)
            lo = bisect.bisect_left(named_starts, s_lo)
            hi = bisect.bisect_left(named_starts, s_hi)
            in_sentence = {_law_identity(raw_laws[named_idx[x]]) for x in range(lo, hi)}
            before = named_before(start)
            link["laws_before"] = distinct_before(start, _BACK_WINDOW)
            if len(in_sentence) == 1:
                link.update(basis=BASIS_SAME_SENTENCE, law=named_idx[lo])
            elif len(distinct_named) == 1:
                link.update(basis=BASIS_SINGLE_LAW, law=named_idx[0])
            elif distinct_named:
                link.update(basis=BASIS_NEAREST, law=before if before is not None else named_idx[0])
        links[i] = link

    for seq, (d, link) in enumerate(zip(drafts, links)):
        parsed: _Parsed = d["parsed"]
        # The document's own clause: the mention opens a line and is followed by nothing,
        # or by a separator. A mention tied to a law in its own phrase is a citation
        # wherever it sits: «المادة 77» alone on a line, with «من نظام العمل» on the
        # next one, is a wrapped line and not a heading.
        heading = False
        direct = link["basis"] in (BASIS_SAME_PHRASE, BASIS_BACK_REF) and not link["inherited"]
        tail = folded[d["end"]:d["line_end_at"]]
        if d["line_start"] and not d["possessive"] and not direct:
            heading = bool(_HEADING_TAIL_RE.match(tail)) or "#" in folded[d["line_start_at"]:d["start"]]
        # «وقد جاء في العقد: (المادة الخامسة: التزامات المستأجر …)» — a clause quoted with
        # its title is the quoted document's own clause, wherever it sits on the line.
        # A law is cited «المادة 5 من نظام …», never «المادة 5: …».
        if not heading and not d["possessive"] and not direct and _QUOTED_CLAUSE_RE.match(tail):
            heading = True
        s_lo, s_hi = _sentence(folded, d["start"], d["end"])
        o_start, o_end = fv.orig(d["start"]), fv.orig_end(d["end"])
        result.mentions.append(Mention(
            seq=seq,
            char_start=o_start,
            char_end=o_end,
            para_n=para_of(o_start),
            numbers=tuple(parsed.numbers),
            number_form=parsed.form,
            plural=d["plural"],
            range_wide=parsed.range_wide,
            compound=parsed.compound,
            conflict=parsed.conflict,
            heading=heading,
            line_start=d["line_start"],
            basis=link["basis"],
            law=link["law"],
            via=link["via"],
            exec_reg=link["exec_reg"],
            inherited=link["inherited"],
            other_target=link["other"],
            laws_before=link["laws_before"],
            sentence_start=fv.orig(s_lo),
            sentence_end=fv.orig_end(s_hi) if s_hi > s_lo else fv.orig(s_lo),
        ))
    result.laws = laws
    return result


def _law_identity(ref: _RawLaw) -> tuple:
    """What makes two references the same law: the name, and whether it is its executive regulation."""
    return (ref.key, ref.exec_of)


def _sentence(folded: str, start: int, end: int) -> tuple[int, int]:
    """The sentence around ``[start, end)``, at most ``MAX_SENTENCE_CHARS`` long (folded offsets)."""
    half = max(0, (MAX_SENTENCE_CHARS - (end - start)) // 2)
    lo_limit = max(0, start - half)
    hi_limit = min(len(folded), end + half)
    lo = lo_limit
    for k in range(start - 1, lo_limit - 1, -1):
        if folded[k] in _SENTENCE_STOP:
            lo = k + 1
            break
    hi = hi_limit
    for k in range(end, hi_limit):
        if folded[k] in _SENTENCE_STOP:
            hi = k + 1
            break
    while lo < start and folded[lo].isspace():
        lo += 1
    return lo, hi


__all__ = [
    "BASIS_BACK_REF",
    "BASIS_NEAREST",
    "BASIS_NO_LAW",
    "BASIS_OTHER_TARGET",
    "BASIS_SAME_PHRASE",
    "BASIS_SAME_SENTENCE",
    "BASIS_SINGLE_LAW",
    "KIND_BACK_REF",
    "KIND_EXEC_BACK_REF",
    "KIND_NAMED",
    "KIND_NOT_A_LAW",
    "LawRef",
    "MAX_RANGE_ARTICLES",
    "Mention",
    "SCAN_RULE_VERSION",
    "ScanResult",
    "TitleIndex",
    "find_folded",
    "fold",
    "scan_citations",
]
