"""The helper's lookups — word match only (plan §4.2, owner decisions C11–C13).

Built from simple search's parts, which stay untouched:

- a law by its title — ``fetch_article._resolve_candidates`` (the library's BM25 plus
  title ILIKE) and ``regulation_versions.resolve`` for a law that has two versions;
- an article by number — ``article_number_keys`` (digits, ٠-٩, ordinal words) and
  ``regulation_versions.pick_articles``;
- a circular — ``manual_search_core(…, embedding_fn=None)``: with no embedder every
  similarity call in that ladder is off;
- a service — title ILIKE plus the ``services.fts`` word-search column (all rows);
- the opener — ``simple_search.unfold`` for a circular and a service.

Nothing here records a search fee, and nothing reaches an embedding or a similarity
function. ``Library`` is the seam: the agent talks to it, tests replace it.
"""
from __future__ import annotations

import asyncio
import logging
import re
import threading
import time
from dataclasses import dataclass
from datetime import date
from typing import Any, Protocol, Sequence

from agents.completion_search.confidence import (
    IN_FORCE,
    PREFIX_LONG,
    PREFIX_SHORT,
    GateResult,
    TitleCandidate,
    title_gate,
)
from agents.tool_repository.citation_scan import TitleIndex, _title_words, find_folded, fold
from agents.tool_repository.fetch_article import (
    _RECALL_ONLY_RUNGS as _REG_RECALL_ONLY,
    _resolve_candidates,
    _strict_exact,
    article_number_keys,
    coverage,
)
from shared.library.supersession import riyadh_today

logger = logging.getLogger(__name__)

_REGS_TABLE = "regulations_v2"
_ARTICLES_TABLE = "articles_v2"
_CASES_TABLE = "cases"
_INDEX_TABLE = "search_index"
_PRINCIPLES_TABLE = "official_principles"
_CASE_COLUMNS = "id,case_ref,case_number,judgment_number,court,city,date_hijri,date_gregorian,year_gregorian"
#: Old rulings carry placeholders instead of a number («unk01»). They never match.
_PLACEHOLDER_NUMBER_RE = re.compile(r"^\s*unk\d*\s*$", re.I)
#: A ruling is looked up by a number, never by a word.
_HAS_DIGIT_RE = re.compile(r"\d")
_SERVICES_TABLE = "services"
_CIRCULARS_TABLE = "circulars"
_ENTITIES_TABLE = "entities"
_PAGE = 1_000
#: The issuing body / provider must cover this share of the words the caller gave.
_ISSUER_MIN_COVERAGE = 0.5
_EXCERPT_BEFORE = 80
_EXCERPT_AFTER = 160
#: What may stand before a term at the start of an article or of a list line.
_OPENERS = " \t\n\r«\"'(-–—•*·#_>0123456789.)"
_DEFINITION_CUES = ("يقصد", "المقصود", "يراد", "تعريف", "تعني", "يعني")
#: The word after a term where a law says what the term IS: «البيع عقد…», «العامل كل شخص…».
_DEFINES_NEXT = frozenset("عقد هو هي كل اي ما اتفاق تصرف حق التزام شخص مال من".split())
# «##### المادة السابعة بعد الثلاثمائة» — the heading an article's text may open with.
_ARTICLE_LABEL_RE = re.compile(r"^[\s#*_>]*(?:الماده|ماده)\s+[^\n:.\-–]{1,60}(?:[:.\-–]|\n|$)[\s*_]*")


# --------------------------------------------------------------------------- #
# Plain data
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class LawLookup:
    """A law name, resolved as far as code can take it."""

    gate: GateResult
    versioned: Any = None            # regulation_versions.VersionedTitle when the law has versions
    note: str = ""

    @property
    def law(self) -> TitleCandidate | None:
        return self.gate.winner

    @property
    def in_force(self) -> bool:
        law = self.gate.winner
        return bool(law) and (law.extra.get("status") in IN_FORCE or law.extra.get("status") is None)


@dataclass(frozen=True)
class ArticleText:
    regulation_id: str
    regulation_title: str
    number: str
    article_id: str
    content: str
    note: str = ""


@dataclass(frozen=True)
class PhraseHit:
    article: ArticleText
    whole: bool                # the phrase stands as its own word(s)
    definition_like: bool      # the article opens with it, or introduces it with «يقصد بـ»
    excerpt: str               # a few words around the first hit
    defines: bool = False      # stronger: «البيع عقد …», «العامل: كل شخص …» — the term, then what it is


class Library(Protocol):
    """Everything the helper may ask the library. Async so tests need no thread."""

    async def title_index(self) -> TitleIndex: ...
    async def find_law(self, name: str) -> LawLookup: ...
    async def article(self, lookup: LawLookup, number: str) -> list[ArticleText]: ...
    async def articles(self, lookup: LawLookup) -> list[ArticleText]: ...
    async def has_articles(self, lookup: LawLookup) -> bool: ...
    async def circulars(self, words: str, issuer: str = "") -> list[TitleCandidate]: ...
    async def services(self, name: str, provider: str = "") -> list[TitleCandidate]: ...
    async def open_text(self, kind: str, object_id: str, title: str) -> str: ...
    async def cases(self, case_number: str, court: str = "", year: str = "") -> list[TitleCandidate]: ...
    async def principles(self, number: str = "", words: str = "") -> list[TitleCandidate]: ...


# --------------------------------------------------------------------------- #
# Pure: a phrase inside a law's articles (Mode B)
# --------------------------------------------------------------------------- #


def article_sort_key(number: str) -> tuple[int, str]:
    m = re.match(r"\d+", number or "")
    return (int(m.group()) if m else 10**9, number or "")


def find_phrase(articles: Sequence[ArticleText], phrase: str) -> list[PhraseHit]:
    """Articles whose text holds ``phrase`` — one hit per article, in article order.

    Tashkeel and letter shapes are ignored, so «البيع» finds «الْبَيْعُ». The law's own
    wording is what is matched: the word «تعريف» is not in the civil law's sale article,
    «البيع» is.
    """
    out: list[PhraseHit] = []
    for art in sorted(articles, key=lambda a: article_sort_key(a.number)):
        spots = find_folded(art.content, phrase)
        if not spots:
            continue
        whole_spots = [s for s in spots if s[2]]
        start, end, whole = (whole_spots or spots)[0]
        folded_head = fold(art.content[:start])
        body_head = _ARTICLE_LABEL_RE.sub("", folded_head, count=1)
        last_line = folded_head.rsplit("\n", 1)[-1]
        after = fold(art.content[end:end + 4]).lstrip()
        definition_like = whole and (
            # the article opens with the term — «البيع عقد …»
            not body_head.strip(_OPENERS)
            # a definitions list — a line that starts with the term, then «:»
            or (not last_line.strip(_OPENERS) and after[:1] in (":", "-", "–"))
            # «يقصد بـ …»
            or any(cue in folded_head[-40:] for cue in _DEFINITION_CUES)
        )
        following = fold(art.content[end:end + 30]).lstrip()
        next_word = (following.lstrip(":-– ").split() or [""])[0]
        defines = bool(definition_like) and (following[:1] in (":", "-", "–") or next_word in _DEFINES_NEXT)
        lo = max(0, start - _EXCERPT_BEFORE)
        hi = min(len(art.content), end + _EXCERPT_AFTER)
        excerpt = " ".join(art.content[lo:hi].split())
        out.append(PhraseHit(art, whole, bool(definition_like),
                             ("…" if lo else "") + excerpt + ("…" if hi < len(art.content) else ""),
                             defines))
    return out


# «مائتان وأربعة وثلاثون» — a number written as a plain count, not as an ordinal. The
# library's own reader (``article_number_keys``) knows ordinals («الرابعة والثلاثون بعد
# المائتين»); a caller that rewrites digits into words usually writes the count.
_CARDINAL_WORDS: dict[str, int] = {
    "واحد": 1, "واحده": 1, "احد": 1, "احدي": 1, "اثنان": 2, "اثنين": 2, "اثنتان": 2, "اثنتين": 2, "اثنا": 2,
    "اثني": 2, "اثنتا": 2, "اثنتي": 2, "ثلاث": 3, "ثلاثه": 3, "اربع": 4, "اربعه": 4, "خمس": 5, "خمسه": 5,
    "ست": 6, "سته": 6, "سبع": 7, "سبعه": 7, "ثمان": 8, "ثماني": 8, "ثمانيه": 8, "تسع": 9, "تسعه": 9,
    "عشر": 10, "عشره": 10,
    "عشرون": 20, "عشرين": 20, "ثلاثون": 30, "ثلاثين": 30, "اربعون": 40, "اربعين": 40, "خمسون": 50,
    "خمسين": 50, "ستون": 60, "ستين": 60, "سبعون": 70, "سبعين": 70, "ثمانون": 80, "ثمانين": 80,
    "تسعون": 90, "تسعين": 90,
    "مايه": 100, "ميه": 100, "مايتان": 200, "مايتين": 200, "ميتان": 200, "ميتين": 200,
    "ثلاثمايه": 300, "ثلاثميه": 300, "اربعمايه": 400, "اربعميه": 400, "خمسمايه": 500, "خمسميه": 500,
    "ستمايه": 600, "ستميه": 600, "سبعمايه": 700, "سبعميه": 700, "ثمانمايه": 800, "ثمانميه": 800,
    "تسعمايه": 900, "تسعميه": 900,
}


def cardinal_to_int(text: str) -> int | None:
    """«مائتان وأربعة وثلاثون» → 234. ``None`` unless EVERY word is a number word.

    One number, added up: hundreds + units + tens, «أحد عشر» = 11. Any other word
    makes the whole reading fail — a half-read number would name the wrong article.
    """
    total = 0
    seen = False
    for word in fold(text or "").split():
        word = word[1:] if word.startswith("و") and word[1:] in _CARDINAL_WORDS else word
        word = word[2:] if word.startswith("ال") and word[2:] in _CARDINAL_WORDS else word
        value = _CARDINAL_WORDS.get(word)
        if value is None:
            return None
        total += value
        seen = True
    return total if seen and 0 < total < 2_000 else None


def compound_keys(number: str) -> list[str]:
    """Keys an article number may be stored under: the library's own folds, plus «76/1» ↔ «76-1»."""
    keys = list(article_number_keys(number))
    counted = cardinal_to_int(number)
    if counted is not None and str(counted) not in keys:
        keys.append(str(counted))
    for key in list(keys):
        for a, b in (("/", "-"), ("-", "/")):
            if a in key:
                swapped = key.replace(a, b)
                if swapped not in keys:
                    keys.append(swapped)
    return keys


def prefix_relation(asked: str, title: str) -> str:
    """How an asked name and a title open each other, word by word; ``""`` when they do not."""
    a, t = _title_words(asked), _title_words(title)
    if len(a) < 2 or len(t) < 2 or a == t:
        return ""
    if len(a) < len(t) and t[:len(a)] == a:
        return PREFIX_SHORT
    if len(t) < len(a) and a[:len(t)] == t:
        return PREFIX_LONG
    return ""


def _candidate_from_reg(c: Any, asked: str = "") -> TitleCandidate:
    return TitleCandidate(
        id=c.reg_id, title=c.display, exact=bool(c.exact), coverage=float(c.coverage or 0.0),
        recall_only=c.rung in _REG_RECALL_ONLY, label=c.bucket_label or "",
        relevance=float(c.relevance or 0.0), prefix=prefix_relation(asked, c.display),
    )


def executive_parent(name: str) -> str:
    """«اللائحة التنفيذية لنظام العمل» → «نظام العمل»; ``""`` when the name is not of that form."""
    words = (name or "").split()
    if len(words) < 3 or fold(words[0]) not in ("اللايحه", "لايحه") or fold(words[1]) != "التنفيذيه":
        return ""
    first = words[2]
    if not fold(first).startswith("ل") or len(first) < 4:
        return ""
    head = ("ال" + first[2:]) if fold(first).startswith("لل") else first[1:]
    return " ".join([head, *words[3:]])


def narrow_by_owner(candidates: Sequence[TitleCandidate], owner_words: str) -> list[TitleCandidate]:
    """Keep the candidates whose issuing body / provider matches the caller's words.

    When nothing matches, the pool is returned unchanged: a wrong guess about the
    issuer must not hide the document.
    """
    words = (owner_words or "").strip()
    if not words:
        return list(candidates)
    kept = [c for c in candidates if coverage(words, c.extra.get("owner") or "") >= _ISSUER_MIN_COVERAGE]
    return kept or list(candidates)


def clean_case_number(raw: str) -> str:
    """The number as the library stores it: Latin digits, no «رقم», no brackets. ``""`` = unusable."""
    from shared.privacy.codec import normalize_digits

    text = normalize_digits((raw or "").strip()).strip("()[]«»\"' ")
    text = re.sub(r"^(?:القضية|الدعوى|الحكم|الصك)?\s*(?:رقم|برقم)\s*[:/]?\s*", "", text).strip()
    if not text or _PLACEHOLDER_NUMBER_RE.match(text) or not _HAS_DIGIT_RE.search(text):
        return ""
    return text


def case_title(row: dict) -> str:
    """One line that tells a ruling from another with the same number: court, city, date."""
    number = (row.get("case_number") or row.get("judgment_number") or "").strip()
    court = " ".join(x for x in ((row.get("court") or "").strip(), (row.get("city") or "").strip()) if x)
    when = (row.get("date_hijri") or "").strip()
    when = f"{when}هـ" if when and not when.endswith("هـ") else when
    if not when and row.get("date_gregorian"):
        when = f"{row['date_gregorian']}م"
    parts = [f"الحكم في القضية رقم {number}"] + [x for x in (court, when) if x]
    return " — ".join(parts)


def narrow_cases(rows: Sequence[dict], court: str, year: str) -> list[dict]:
    """Narrow rulings that share a number by court, then by year. A filter that matches nothing is dropped."""
    from shared.privacy.codec import normalize_digits

    out = list(rows)
    court = (court or "").strip()
    if court and len(out) > 1:
        kept = [r for r in out if coverage(court, f"{r.get('court') or ''} {r.get('city') or ''}") >= 0.5]
        out = kept or out
    year = re.sub(r"\D", "", normalize_digits(year or ""))
    if year and len(out) > 1:
        def in_year(r: dict) -> bool:
            return (year in normalize_digits(str(r.get("date_hijri") or ""))
                    or year == str(r.get("year_gregorian") or "")
                    or year in str(r.get("date_gregorian") or "")
                    or year in normalize_digits(str(r.get("case_number") or "")))
        kept = [r for r in out if in_year(r)]
        out = kept or out
    return out


def principle_text(row: dict) -> tuple[str, str]:
    """``(title, text)`` of one judicial principle — the rule, then the decisions it rests on."""
    from agents.deep_search_v4.ura.principles import (
        principle_decision_line,
        principle_decisions,
        principle_title,
    )

    number = row.get("principle_no")
    title = principle_title(number if isinstance(number, int) else None, row.get("chapter_title") or "")
    body = (row.get("text") or "").strip()
    decisions = principle_decision_line(principle_decisions(row))
    names = "، ".join(str(n).strip() for n in (row.get("issuing_body_names") or []) if str(n or "").strip())
    tail = " — ".join(x for x in (names, decisions) if x)
    return title, (f"{body}\n\n({tail})" if tail else body)


def _service_exact(query: str, name: str) -> bool:
    """A service name often carries its provider in front: «وزارة التجارة - قيد سجل تجاري»."""
    if _strict_exact(query, name):
        return True
    tail = name.split(" - ", 1)[-1] if " - " in name else ""
    return bool(tail) and _strict_exact(query, tail)


# --------------------------------------------------------------------------- #
# The library's title list — loaded once per process, refreshed hourly
# --------------------------------------------------------------------------- #

_TITLE_TTL_S = 3_600
_title_cache: tuple[float, TitleIndex] | None = None
_title_lock = threading.Lock()


def load_title_index(supabase, *, force: bool = False) -> TitleIndex:
    """Every regulation's ``title`` and ``clean_title`` as a ``TitleIndex``. Sync."""
    global _title_cache
    now = time.monotonic()
    cached = _title_cache
    if cached and not force and now - cached[0] < _TITLE_TTL_S:
        return cached[1]
    with _title_lock:
        cached = _title_cache
        if cached and not force and now - cached[0] < _TITLE_TTL_S:
            return cached[1]
        pairs: list[tuple[str, str]] = []
        start = 0
        while True:
            rows = (
                supabase.table(_REGS_TABLE).select("id,title,clean_title").order("id")
                .range(start, start + _PAGE - 1).execute().data or []
            )
            for r in rows:
                for t in {r.get("title") or "", r.get("clean_title") or ""}:
                    if t.strip():
                        pairs.append((str(r["id"]), t))
            if len(rows) < _PAGE:
                break
            start += _PAGE
        index = TitleIndex(pairs)
        _title_cache = (now, index)
        return index


# --------------------------------------------------------------------------- #
# The real library
# --------------------------------------------------------------------------- #


class SupabaseLibrary:
    """``Library`` over the sync service-role Supabase client."""

    def __init__(self, supabase, *, today: date | None = None) -> None:
        self.sb = supabase
        self.today = today or riyadh_today()

    # -- laws ---------------------------------------------------------------

    async def title_index(self) -> TitleIndex:
        return await asyncio.to_thread(load_title_index, self.sb)

    async def find_law(self, name: str) -> LawLookup:
        return await asyncio.to_thread(self._find_law, name)

    def _reg_status(self, ids: Sequence[str]) -> dict[str, str]:
        ids = [i for i in dict.fromkeys(ids) if i]
        if not ids:
            return {}
        try:
            rows = self.sb.table(_REGS_TABLE).select("id,status_class").in_("id", ids).execute().data or []
        except Exception as exc:  # noqa: BLE001
            logger.warning("completion_search: status read failed: %s", exc)
            return {}
        return {str(r["id"]): (r.get("status_class") or "") for r in rows}

    def _with_status(self, cand: TitleCandidate, status: dict[str, str]) -> TitleCandidate:
        return TitleCandidate(cand.id, cand.title, cand.exact, cand.coverage, cand.recall_only,
                              cand.label, {**cand.extra, "status": status.get(cand.id)},
                              cand.relevance, cand.prefix)

    def _executive_of(self, name: str) -> LawLookup | None:
        """The executive regulation of an exactly named law, through ``parent_law_id``."""
        parent_name = executive_parent(name)
        if not parent_name:
            return None
        parent = self._find_law(parent_name, _allow_executive=False)
        if parent.gate.state != "exact":
            return None
        try:
            rows = (
                self.sb.table(_REGS_TABLE).select("id,title,clean_title,status_class")
                .eq("parent_law_id", parent.law.id).eq("doc_type_bucket", "executive_regulation")
                .execute().data or []
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("completion_search: executive regulation read failed: %s", exc)
            return None
        cands = [
            TitleCandidate(str(r["id"]), (r.get("clean_title") or r.get("title") or "").strip(), True, 1.0,
                           extra={"status": r.get("status_class") or ""})
            for r in rows
        ]
        live = [c for c in cands if c.extra["status"] in IN_FORCE] or cands
        if len(live) == 1:
            return LawLookup(GateResult("exact", live[0], (live[0],)))
        if live:
            return LawLookup(GateResult("several", None, tuple(live[:3])))
        return None

    def _find_law(self, name: str, _allow_executive: bool = True) -> LawLookup:
        from agents.tool_repository.regulation_versions import resolve as resolve_versions

        name = (name or "").strip()
        if not name:
            return LawLookup(GateResult("none"))
        versioned = resolve_versions(self.sb, name, self.today)
        resolved = versioned.resolved
        if resolved.reg_id:
            status = self._reg_status([resolved.reg_id])
            law = self._with_status(
                TitleCandidate(resolved.reg_id, versioned.label or resolved.display, True, 1.0), status
            )
            return LawLookup(GateResult("exact", law, (law,)), versioned if versioned.versions else None)

        if _allow_executive:
            through_parent = self._executive_of(name)
            if through_parent is not None:
                return through_parent

        ranked = [_candidate_from_reg(c, name) for c in _resolve_candidates(self.sb, name)]
        gate = title_gate(ranked)
        note = ""
        if gate.state == "several" and gate.shown and all(c.exact for c in gate.shown):
            # Several regulations carry this very title. One hard fact may still settle
            # it: exactly one of them is in force (plan §8 Q5).
            exact = [c for c in ranked if c.exact]
            status = self._reg_status([c.id for c in exact])
            live = [c for c in exact if status.get(c.id) in IN_FORCE]
            if len(live) == 1:
                law = self._with_status(live[0], status)
                gate = GateResult("exact", law, (law,))
                note = "يحمل هذا الاسم أكثر من نص في المكتبة؛ أُعيد الساري منها."
        if gate.winner is not None and "status" not in gate.winner.extra:
            status = self._reg_status([gate.winner.id])
            law = self._with_status(gate.winner, status)
            gate = GateResult(gate.state, law, gate.shown)
        return LawLookup(gate, None, note)

    # -- articles -----------------------------------------------------------

    async def article(self, lookup: LawLookup, number: str) -> list[ArticleText]:
        return await asyncio.to_thread(self._article, lookup, number)

    def _article_row(self, regulation_id: str, number: str) -> dict:
        for key in compound_keys(number):
            try:
                rows = (
                    self.sb.table(_ARTICLES_TABLE).select("id,article_number,content")
                    .eq("regulation_id", regulation_id).eq("article_number", key).limit(1).execute().data or []
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("completion_search: article read failed reg=%s: %s", regulation_id, exc)
                return {}
            if rows and (rows[0].get("content") or "").strip():
                return rows[0]
        return {}

    def _article(self, lookup: LawLookup, number: str) -> list[ArticleText]:
        law = lookup.law
        number = (number or "").strip()
        if law is None or not number:
            return []
        versioned = lookup.versioned
        if versioned is not None and versioned.versions:
            from agents.tool_repository.regulation_versions import pick_articles

            pick = pick_articles(self.sb, versioned, number, self.today)
            out = []
            for hit in pick.hits:
                title = hit.version.label if pick.labelled else hit.version.title
                out.append(ArticleText(
                    hit.version.reg_id, title, str(hit.article.get("article_number") or number),
                    str(hit.article.get("id") or ""), (hit.article.get("content") or "").strip(), pick.note,
                ))
            return out
        row = self._article_row(law.id, number)
        if not row:
            return []
        return [ArticleText(law.id, law.title, str(row.get("article_number") or number),
                            str(row.get("id") or ""), (row.get("content") or "").strip(), lookup.note)]

    async def articles(self, lookup: LawLookup) -> list[ArticleText]:
        return await asyncio.to_thread(self._articles, lookup)

    async def has_articles(self, lookup: LawLookup) -> bool:
        """False when the library holds the law but none of its article texts."""
        return await asyncio.to_thread(self._has_articles, lookup)

    def _has_articles(self, lookup: LawLookup) -> bool:
        ids = [lookup.law.id] if lookup.law else []
        if lookup.versioned is not None:
            ids += [v.reg_id for v in lookup.versioned.versions]
        for reg_id in dict.fromkeys(ids):
            try:
                rows = (self.sb.table(_ARTICLES_TABLE).select("id").eq("regulation_id", reg_id)
                        .limit(1).execute().data or [])
            except Exception as exc:  # noqa: BLE001
                logger.warning("completion_search: article presence read failed: %s", exc)
                return True          # unknown — do not claim the law has no text
            if rows:
                return True
        return False

    def _articles(self, lookup: LawLookup) -> list[ArticleText]:
        law = lookup.law
        if law is None:
            return []
        out: list[ArticleText] = []
        start = 0
        while True:
            try:
                rows = (
                    self.sb.table(_ARTICLES_TABLE).select("id,article_number,content")
                    .eq("regulation_id", law.id).order("id").range(start, start + _PAGE - 1).execute().data or []
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("completion_search: articles read failed reg=%s: %s", law.id, exc)
                break
            for r in rows:
                content = (r.get("content") or "").strip()
                if content:
                    out.append(ArticleText(law.id, law.title, str(r.get("article_number") or ""),
                                           str(r.get("id") or ""), content))
            if len(rows) < _PAGE:
                break
            start += _PAGE
        return out

    # -- circulars ----------------------------------------------------------

    async def circulars(self, words: str, issuer: str = "") -> list[TitleCandidate]:
        from agents.simple_search.manual_search import _RECALL_ONLY_RUNGS, manual_search_core

        # embedding_fn=None switches off every similarity call in the ladder (C12).
        rows = await manual_search_core(self.sb, (words or "").strip(), "circulars", embedding_fn=None)
        owners = await asyncio.to_thread(self._circular_owners, [r.get("id") for r in rows])
        cands = [
            TitleCandidate(
                id=str(r.get("id") or ""), title=r.get("title") or "", exact=bool(r.get("pin")),
                coverage=float(r.get("coverage") or 0.0), recall_only=r.get("rung") in _RECALL_ONLY_RUNGS,
                label=owners.get(str(r.get("id") or ""), ""),
                extra={"owner": owners.get(str(r.get("id") or ""), "")},
            )
            for r in rows if r.get("id")
        ]
        return narrow_by_owner(cands, issuer)

    def _circular_owners(self, ids: Sequence[Any]) -> dict[str, str]:
        ids = [str(i) for i in ids if i]
        if not ids:
            return {}
        try:
            rows = self.sb.table(_CIRCULARS_TABLE).select("id,entity_id").in_("id", ids).execute().data or []
            entity_ids = sorted({str(r["entity_id"]) for r in rows if r.get("entity_id")})
            names = {}
            if entity_ids:
                ents = self.sb.table(_ENTITIES_TABLE).select("id,entity_name").in_("id", entity_ids).execute().data or []
                names = {str(e["id"]): (e.get("entity_name") or "") for e in ents}
            return {str(r["id"]): names.get(str(r.get("entity_id") or ""), "") for r in rows}
        except Exception as exc:  # noqa: BLE001
            logger.warning("completion_search: circular owners read failed: %s", exc)
            return {}

    # -- services -----------------------------------------------------------

    async def services(self, name: str, provider: str = "") -> list[TitleCandidate]:
        return await asyncio.to_thread(self._services, name, provider)

    def _services(self, name: str, provider: str) -> list[TitleCandidate]:
        query = (name or "").strip()
        if not query:
            return []
        rows: dict[str, dict] = {}
        for rung, fetch in (("ilike", self._services_ilike), ("fts", self._services_fts)):
            for r in fetch(query):
                rows.setdefault(str(r.get("id") or ""), {**r, "_rung": rung})
        cands = []
        for sid, r in rows.items():
            title = (r.get("service_name_ar") or "").strip()
            if not sid or not title:
                continue
            cands.append(TitleCandidate(
                id=sid, title=title, exact=_service_exact(query, title), coverage=coverage(query, title),
                label=(r.get("provider_name") or "").strip(),
                extra={"owner": (r.get("provider_name") or "").strip()},
            ))
        cands.sort(key=lambda c: (c.exact, c.coverage, -len(c.title)), reverse=True)
        return narrow_by_owner(cands, provider)[:8]

    def _services_ilike(self, query: str) -> list[dict]:
        try:
            return (
                self.sb.table(_SERVICES_TABLE).select("id,service_name_ar,provider_name")
                .ilike("service_name_ar", f"%{query}%").limit(25).execute().data or []
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("completion_search: services ILIKE failed: %s", exc)
            return []

    def _services_fts(self, query: str) -> list[dict]:
        """Word match on the table's own ``fts`` column — filled on every service row."""
        try:
            return (
                # limit() before text_search(): the builder text_search returns has no limit().
                self.sb.table(_SERVICES_TABLE).select("id,service_name_ar,provider_name").limit(40)
                .text_search("fts", query, options={"config": "arabic", "type": "plain"})
                .execute().data or []
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("completion_search: services word search failed: %s", exc)
            return []

    # -- rulings, by case number ---------------------------------------------

    async def cases(self, case_number: str, court: str = "", year: str = "") -> list[TitleCandidate]:
        return await asyncio.to_thread(self._cases, case_number, court, year)

    def _case_ids_from_index(self, number: str) -> list[str]:
        """Exact match on the library index's ``case_number`` facet (indexed)."""
        try:
            rows = (
                self.sb.table(_INDEX_TABLE).select("content_id").eq("corpus", "judgment")
                .is_("owner_user_id", "null").contains("facets", {"case_number": number})
                .limit(25).execute().data or []
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("completion_search: case index lookup failed: %s", exc)
            return []
        return [str(r["content_id"]) for r in rows if r.get("content_id")]

    def _case_rows(self, column: str, value: str) -> list[dict]:
        try:
            query = self.sb.table(_CASES_TABLE).select(_CASE_COLUMNS)
            query = query.in_("id", value) if column == "id" else query.eq(column, value)
            return query.limit(25).execute().data or []
        except Exception as exc:  # noqa: BLE001
            logger.warning("completion_search: cases read (%s) failed: %s", column, exc)
            return []

    def _cases(self, case_number: str, court: str, year: str) -> list[TitleCandidate]:
        number = clean_case_number(case_number)
        if not number:
            return []
        ids = self._case_ids_from_index(number)
        rows = self._case_rows("id", ids) if ids else []
        if not rows:                      # about 1,300 rulings are not in the index
            rows = self._case_rows("case_number", number) or self._case_rows("judgment_number", number)
        rows = [r for r in rows if not _PLACEHOLDER_NUMBER_RE.match(str(r.get("case_number") or ""))]
        rows = narrow_cases(rows, court, year)
        return [
            TitleCandidate(
                id=str(r["id"]), title=case_title(r), exact=True, coverage=1.0,
                label=(r.get("court") or "").strip(), extra={"owner": (r.get("court") or "").strip()},
            )
            for r in rows
        ]

    # -- judicial principles -------------------------------------------------

    async def principles(self, number: str = "", words: str = "") -> list[TitleCandidate]:
        return await asyncio.to_thread(self._principles, number, words)

    def _principles(self, number: str, words: str) -> list[TitleCandidate]:
        from agents.deep_search_v4.ura.principles import PRINCIPLE_COLUMNS
        from shared.privacy.codec import normalize_digits

        digits = re.sub(r"\D", "", normalize_digits(number or ""))
        table = self.sb.table(_PRINCIPLES_TABLE)
        try:
            if digits:
                rows = table.select(PRINCIPLE_COLUMNS).eq("principle_no", int(digits)).limit(5).execute().data or []
                exact = True
            else:
                phrase = (words or "").strip()
                if not phrase:
                    return []
                rows = (
                    table.select(PRINCIPLE_COLUMNS)
                    .or_(f"text.ilike.%{phrase}%,chapter_title.ilike.%{phrase}%").limit(25).execute().data or []
                )
                exact = False
        except Exception as exc:  # noqa: BLE001
            logger.warning("completion_search: principles read failed: %s", exc)
            return []
        out = []
        for r in rows:
            title, text = principle_text(r)
            reach = f"{r.get('chapter_title') or ''} {r.get('text') or ''}"
            out.append(TitleCandidate(
                id=str(r["id"]), title=title, exact=exact,
                coverage=1.0 if exact else coverage(words, reach),
                label=(r.get("chapter_title") or "").strip(),
                extra={"text": text, "principle_no": str(r.get("principle_no") or "")},
            ))
        out.sort(key=lambda c: c.coverage, reverse=True)
        return out

    # -- opener -------------------------------------------------------------

    async def open_text(self, kind: str, object_id: str, title: str) -> str:
        """The standard rendering of a circular, a service or a ruling (``simple_search.unfold``).

        A ruling is opened with NO unlock (owner decision C24): the opener is handed a
        granted access object built here, so the library's entitlement check is never
        called, nothing is charged and no plan is read.
        """
        from agents.simple_search.models import ResolvedObject
        from agents.simple_search.unfold import JudgmentAccess, unfold

        if kind == "circular":
            obj = ResolvedObject(level="circular", circular_id=object_id, title=title)
        elif kind == "service":
            obj = ResolvedObject(level="service", service_id=object_id, title=title)
        elif kind == "case":
            async def granted(case_id: str) -> JudgmentAccess:
                return JudgmentAccess(case_id=case_id, granted=True, charged=False, reason="completion_search")

            result = await unfold(
                self.sb, ResolvedObject(level="judgment", case_id=object_id, title=title),
                judgment_access=granted,
            )
            return (result.text or "").strip()
        else:
            raise ValueError(f"open_text: unknown kind {kind!r}")
        result = await unfold(self.sb, obj)
        return (result.text or "").strip()


__all__ = [
    "ArticleText",
    "LawLookup",
    "Library",
    "PhraseHit",
    "SupabaseLibrary",
    "article_sort_key",
    "compound_keys",
    "find_phrase",
    "load_title_index",
    "narrow_by_owner",
    "cardinal_to_int",
    "prefix_relation",
    "executive_parent",
    "case_title",
    "clean_case_number",
    "narrow_cases",
    "principle_text",
]
