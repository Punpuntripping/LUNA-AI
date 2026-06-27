"""fetch_article — deterministic lookup of ONE article (مادة) by number.

A Pydantic AI **planner tool** that fetches the verbatim body of a single
article from a **named regulation**, *before* the planner decides the search,
so the planner can fold that text into ``planner_brief`` (the facts channel
that reaches the executors and the aggregator).

Why this exists: semantic search cannot reliably retrieve an article by its
*number* — the corpus writes article numbers as Arabic words inside the prose
("المادة الحادية والثمانون"), not the digit "81". A user asking about «المادة
81 من نظام العمل» can run the whole pipeline and still get an answer whose own
gaps say *"لم تتضمن المراجع النص الحرفي للمادة 81"*. The fix is a deterministic
structured lookup against the article-grain table ``articles_v2``.

Two deterministic steps (see FETCH_ARTICLE_PLAN.md §4):

1. **Resolve ``regulation_title`` → ``regulation_id``** — the only fuzzy part.
   PostgREST ILIKE candidate-fetch on ``title``/``clean_title`` (raw token,
   exact-char), then normalize BOTH sides app-side and rank in Python: exact
   normalized match wins outright; else string-similarity score
   (``difflib.SequenceMatcher`` — ``rapidfuzz`` is not a dependency) with a
   ``doc_type_bucket`` preference (``law_statute`` for «نظام»,
   ``executive_regulation`` for «لائحة») and a shorter-title tiebreak. If no
   exact match and the top-2 are close, return an ``AMBIGUOUS:`` payload so the
   planner can ``ask_user`` — never silently grab the wrong law.
2. **Fetch the article** — ``articles_v2.content`` keyed by
   ``(regulation_id, article_number:text)``. ``article_number`` is matched by
   exact text equality (compound values like ``"1-1"`` exist). Returns the
   article body as TEXT only — never an ``[n]`` citation, never ``article_ref``
   or ``chunk_parent_id``.

The resolver / normalizer / fetch layers are split out as pure functions so
they unit-test against a fake Supabase without an agent or a live DB. Mirrors
the structure of ``unfold_workspace_item`` (plain-string return, registered on
the decider only, sync PostgREST calls wrapped in ``asyncio.to_thread``).

Registration::

    from agents.tool_repository.fetch_article import register_fetch_article
    register_fetch_article(agent)   # deps must expose .supabase

The deps object must structurally satisfy :class:`HasSupabase` (``PlannerDeps``
already does, via ``.supabase``).
"""
from __future__ import annotations

import asyncio
import difflib
import logging
import re
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from pydantic_ai import Agent, RunContext

logger = logging.getLogger(__name__)


# --- Schema config: a table/column rename is a one-line change here. ----------
_REGS_TABLE = "regulations_v2"
_ARTICLES_TABLE = "articles_v2"

# Candidate-fetch select — one row per regulation, the cols the resolver ranks.
_REG_COLUMNS = "id,title,clean_title,doc_type_bucket,status_class"
# Article fetch — content is the ONLY column we need (never article_ref).
_ARTICLE_COLUMNS = "content"

# Ambiguity gate: if there is no exact normalized match AND the top-2
# similarity scores are within this margin, ask the user instead of guessing.
_AMBIGUITY_MARGIN = 0.1
# How many candidate titles to surface in the AMBIGUOUS: payload.
_AMBIGUOUS_LIST_N = 3
# Minimum similarity for a NON-exact match to be accepted. Below this the best
# candidate is too weak to trust — return "no match" (→ the not-found path,
# which names the USER's title) rather than confidently picking a wrong law.
# Calibrated against the corpus: a genuine partial match («العمل» → «نظام
# العمل») scores ≈ 0.48, whereas a spurious fallback-token hit («نظام الفساد
# المالي والإداري», absent from the corpus, → «لائحة اشتراطات السلامة …
# والإدارية») scores ≈ 0.38.
_MIN_MATCH_SCORE = 0.40

# doc_type_bucket nudges — «نظام» ⇒ a statute, «لائحة» ⇒ an executive reg.
_BUCKET_PREF_BONUS = 0.05
_LAW_KEYWORD = "نظام"
_REG_KEYWORD = "لائحة"
_BUCKET_LAW = "law_statute"
_BUCKET_EXEC = "executive_regulation"

# --- Statute-package pin config — a lean reuse of save_memo's persistence -------
# Successful fetches in a turn accumulate on ``deps._fetched_articles`` and are
# flushed into ONE durable workspace item per search (``flush_statute_package``)
# so they survive conversation compaction, re-load as a summary, and unfold on
# demand. Distinct from save_memo's USER-message memo: this is corpus text, so it
# carries its own ``subtype='statute_package'`` and marker — never the «رسالة
# أساسية من المستخدم» memo identity. Lean: a fire-and-forget insert (no router
# force-attach/chip sinks, which PlannerDeps doesn't carry) — the articles
# already ground THIS turn via planner_brief; the pin is for FUTURE turns.
_STATUTE_KIND = "note"
_STATUTE_PACKAGE_SUBTYPE = "statute_package"
_STATUTE_CREATED_BY = "agent"
_STATUTE_PACKAGE_MARKER = "> 📌 نصوص المواد المثبّتة من البحث"


# --------------------------------------------------------------------------- #
# Deps contract — leaner than HasWorkspaceContext: fetch_article only needs the
# supabase client (no wi_alias_map, no user_id scoping — the corpus is public).
# --------------------------------------------------------------------------- #


@runtime_checkable
class HasSupabase(Protocol):
    """Structural deps contract for the tool.

    ``.supabase`` is the only hard requirement (the corpus read). Pinning a
    successful fetch additionally uses ``.user_id`` / ``.conversation_id`` (to
    scope the workspace item) and ``.emit_sse`` (an optional chip) — all read
    via ``getattr`` and skipped when absent, so the tool degrades to a pure
    fetch on minimal deps. ``PlannerDeps`` carries all four. Kept loose
    (``object``) to avoid a hard import of the supabase client here.
    """

    supabase: object


# --------------------------------------------------------------------------- #
# Arabic title normalization — pure, unit-testable.
# --------------------------------------------------------------------------- #

# Arabic combining diacritics (tashkeel) — fatha/damma/kasra/shadda/sukun/
# tanween + the dagger alef and superscript marks. Stripped before comparison.
_TASHKEEL_RE = re.compile(r"[ؐ-ًؚ-ٰٟۖ-ۭ࣓-ࣿ]")
_TATWEEL = "ـ"  # kashida / tatweel — decorative letter-stretch, dropped.
_WS_RE = re.compile(r"\s+")
# A leading definite article «ال» that prefixes the whole title.
_LEADING_AL_RE = re.compile(r"^ال")


def _normalize_title(text: str) -> str:
    """Normalize an Arabic regulation title for comparison.

    Strips tashkeel + tatweel, unifies alef forms (أ/إ/آ/ٱ → ا), ة → ه,
    ى → ي, ؤ → و, ئ → ي, collapses whitespace, and drops a single leading
    «ال». Returns the normalized lowercase string (lowercasing is a no-op on
    Arabic letters but harmlessly normalizes any embedded Latin).

    Pure: no DB, no I/O — the comparison key for both the query title and each
    candidate ``title``/``clean_title``.
    """
    if not text:
        return ""
    s = _TASHKEEL_RE.sub("", text)
    s = s.replace(_TATWEEL, "")
    # Unify alef variants → bare alef.
    s = (
        s.replace("أ", "ا")
        .replace("إ", "ا")
        .replace("آ", "ا")
        .replace("ٱ", "ا")
    )
    # Common letter-shape unifications.
    s = s.replace("ة", "ه").replace("ى", "ي").replace("ؤ", "و").replace("ئ", "ي")
    s = _WS_RE.sub(" ", s).strip()
    s = _LEADING_AL_RE.sub("", s).strip()
    return s.lower()


def _distinctive_token(title: str) -> str:
    """Pick the single most distinctive raw token of a title for ILIKE retry.

    The longest word is the most specific (e.g. «التطوعي» / «المرور») — used
    only when the full-string ILIKE returns no candidates. Returns the raw
    (un-normalized) token because ILIKE is exact-char.
    """
    tokens = [t for t in _WS_RE.split(title.strip()) if t]
    if not tokens:
        return title.strip()
    return max(tokens, key=len)


# --------------------------------------------------------------------------- #
# Ranking — pure, unit-testable.
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class RegCandidate:
    """One scored regulation candidate.

    ``score`` ∈ [0, 1+bonus]; ``exact`` is True when the normalized titles
    match outright. ``display`` is the human title (clean_title or title) used
    in the AMBIGUOUS: payload and the success header.
    """

    reg_id: str
    display: str
    score: float
    exact: bool


def _score_candidate(query_norm: str, row: dict) -> RegCandidate:
    """Score one ``regulations_v2`` row against the normalized query title.

    Considers BOTH ``title`` and ``clean_title`` (best of the two wins); an
    exact normalized match on either pins ``exact=True`` and score ``1.0``.
    Adds a small ``doc_type_bucket`` preference bonus when the query implies a
    statute («نظام») / executive reg («لائحة»). Shorter titles get a tiny
    tiebreak so «نظام العمل» beats «نظام العمل التطوعي» on a near-tie.
    """
    title = (row.get("title") or "").strip()
    clean = (row.get("clean_title") or "").strip()
    display = clean or title

    cand_norms = [_normalize_title(t) for t in (title, clean) if t]
    exact = any(cn and cn == query_norm for cn in cand_norms)
    if exact:
        base = 1.0
    elif cand_norms:
        base = max(
            difflib.SequenceMatcher(None, query_norm, cn).ratio() for cn in cand_norms
        )
    else:
        base = 0.0

    # doc_type_bucket preference: only a nudge, never overrides an exact match.
    bonus = 0.0
    bucket = (row.get("doc_type_bucket") or "").strip()
    if not exact and bucket:
        if _LAW_KEYWORD in query_norm and bucket == _BUCKET_LAW:
            bonus += _BUCKET_PREF_BONUS
        if _REG_KEYWORD in query_norm and bucket == _BUCKET_EXEC:
            bonus += _BUCKET_PREF_BONUS

    # Shorter-title tiebreak: a hair of score per char saved (≤ the bucket
    # bonus so it never reorders across a real similarity gap).
    longest = max((len(n) for n in cand_norms), default=0)
    tiebreak = max(0.0, 0.02 - 0.0005 * longest)

    return RegCandidate(
        reg_id=str(row.get("id") or ""),
        display=display or "—",
        score=min(base, 1.0) + bonus + tiebreak,
        exact=exact,
    )


def _rank_candidates(query_title: str, rows: list[dict]) -> list[RegCandidate]:
    """Score + sort candidate rows best-first. Pure (no DB)."""
    query_norm = _normalize_title(query_title)
    scored = [_score_candidate(query_norm, r) for r in rows if r.get("id")]
    # Exact matches first, then by score desc.
    scored.sort(key=lambda c: (c.exact, c.score), reverse=True)
    return scored


# --------------------------------------------------------------------------- #
# Supabase reads — sync client (matches the rest of agents/). Wrapped in
# asyncio.to_thread at the call site inside the tool body.
# --------------------------------------------------------------------------- #


def _fetch_reg_candidates(supabase, query_title: str) -> list[dict]:
    """Candidate-fetch regulations whose title/clean_title ILIKE the query.

    PostgREST ILIKE is exact-char, so the RAW (un-normalized) query string is
    used as the ``%token%`` pattern. Runs the full-string pattern on both
    ``title`` and ``clean_title``, merges + de-dupes by ``id``; if that yields
    nothing, retries with the single most distinctive raw token. Never raises —
    a failed query logs and contributes nothing.
    """
    raw = (query_title or "").strip()
    if not raw:
        return []

    def _ilike(col: str, token: str) -> list[dict]:
        try:
            resp = (
                supabase.table(_REGS_TABLE)
                .select(_REG_COLUMNS)
                .ilike(col, f"%{token}%")
                .execute()
            )
            return list(getattr(resp, "data", None) or [])
        except Exception as exc:  # noqa: BLE001
            logger.warning("fetch_article: reg ILIKE %s ~ %r failed: %s", col, token, exc)
            return []

    def _merge(*lists: list[dict]) -> list[dict]:
        by_id: dict[str, dict] = {}
        for lst in lists:
            for r in lst:
                rid = str(r.get("id") or "")
                if rid and rid not in by_id:
                    by_id[rid] = r
        return list(by_id.values())

    rows = _merge(_ilike("title", raw), _ilike("clean_title", raw))
    if rows:
        return rows

    token = _distinctive_token(raw)
    if token and token != raw:
        rows = _merge(_ilike("title", token), _ilike("clean_title", token))
    return rows


def _fetch_article_content(supabase, regulation_id: str, article_number: str) -> str | None:
    """Fetch ``articles_v2.content`` for ``(regulation_id, article_number)``.

    ``article_number`` is matched by exact TEXT equality (the corpus stores
    compound values like ``"1-1"`` as strings). Returns the content string, or
    ``None`` when no such article row exists. Never raises.
    """
    try:
        resp = (
            supabase.table(_ARTICLES_TABLE)
            .select(_ARTICLE_COLUMNS)
            .eq("regulation_id", regulation_id)
            .eq("article_number", article_number)
            .limit(1)
            .execute()
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "fetch_article: article fetch failed for reg=%s art=%r: %s",
            regulation_id, article_number, exc,
        )
        return None
    data = getattr(resp, "data", None)
    if not data:
        return None
    # ``.limit(1)`` returns a list; some fakes may return a single dict.
    row = data[0] if isinstance(data, list) else data
    content = (row or {}).get("content")
    return content if content else None


# --------------------------------------------------------------------------- #
# Resolution layer — pure orchestration over the sync reads. Returns either a
# resolved (reg_id, display) pair, an ``AMBIGUOUS:`` payload, or a not-found
# sentinel. Synchronous; the tool body dispatches it via asyncio.to_thread.
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class ResolveResult:
    """Outcome of resolving a regulation title.

    Exactly one of ``reg_id`` (resolved) or ``ambiguous`` (needs ask_user) is
    populated; both empty ⇒ no candidate matched at all.
    """

    reg_id: str = ""
    display: str = ""
    ambiguous: str = ""  # the full "AMBIGUOUS: ..." payload when set
    exact: bool = False  # True only on an exact normalized title match (→ HIGH)


def _build_ambiguous(candidates: list[RegCandidate]) -> str:
    """Render the ``AMBIGUOUS:`` payload listing 2–3 candidate titles."""
    titles = []
    seen: set[str] = set()
    for c in candidates:
        if c.display and c.display not in seen:
            seen.add(c.display)
            titles.append(c.display)
        if len(titles) >= _AMBIGUOUS_LIST_N:
            break
    listed = "، ".join(titles)
    return (
        "AMBIGUOUS: تعذّر تحديد النظام المقصود بدقة. "
        f"المرشحون المحتملون: {listed}. "
        "اسأل المستخدم أيّ نظام يقصد قبل المتابعة."
    )


def resolve_regulation_id(supabase, regulation_title: str) -> ResolveResult:
    """Resolve a user-supplied regulation title to a ``regulations_v2.id``.

    Returns a :class:`ResolveResult`: a resolved ``reg_id`` on a confident
    match, an ``ambiguous`` payload when the top-2 non-exact candidates are too
    close (within :data:`_AMBIGUITY_MARGIN`), or an empty result when nothing
    matched at all. Synchronous (sync PostgREST reads inside).
    """
    rows = _fetch_reg_candidates(supabase, regulation_title)
    if not rows:
        return ResolveResult()

    ranked = _rank_candidates(regulation_title, rows)
    if not ranked:
        return ResolveResult()

    top = ranked[0]
    # An exact normalized match wins outright — never ambiguous (→ HIGH).
    if top.exact:
        return ResolveResult(reg_id=top.reg_id, display=top.display, exact=True)

    # Score floor: the best candidate is too weak to trust. A clean "no match"
    # (→ the not-found path, which names the USER's title) beats confidently
    # picking a low-similarity wrong law — e.g. «نظام الفساد المالي والإداري»
    # (absent from the corpus) resolving onto «لائحة اشتراطات السلامة …
    # والإدارية» via the fallback token. A genuine partial match stays above it.
    if top.score < _MIN_MATCH_SCORE:
        return ResolveResult()

    # Single candidate above the floor, no exact match — accept it (the planner
    # still searches).
    if len(ranked) == 1:
        return ResolveResult(reg_id=top.reg_id, display=top.display)

    # No exact match + top-2 close ⇒ ambiguous → ask the user.
    second = ranked[1]
    if (top.score - second.score) <= _AMBIGUITY_MARGIN:
        return ResolveResult(ambiguous=_build_ambiguous(ranked))

    return ResolveResult(reg_id=top.reg_id, display=top.display)


@dataclass(frozen=True)
class FetchArticleResult:
    """Rich outcome of a fetch — drives both the tool return and the pin.

    ``text`` is what the model sees. ``status`` is one of ``"ok"`` /
    ``"ambiguous"`` / ``"not_found"``. ``confidence`` is ``"high"`` (exact
    regulation match) / ``"medium"`` (above-floor non-exact match) / ``""``
    (no confident result). ``content`` is the verbatim article body (no header /
    no confidence note) — the body that gets pinned.
    """

    text: str
    status: str  # "ok" | "ambiguous" | "not_found"
    confidence: str = ""  # "high" | "medium" | ""
    reg_id: str = ""
    reg_name: str = ""
    article_number: str = ""
    content: str = ""


def fetch_article_result(
    supabase, regulation_title: str, article_number: str
) -> FetchArticleResult:
    """Full deterministic fetch: resolve title → fetch article → render + classify.

    Synchronous (the tool body wraps this in ``asyncio.to_thread``). On success
    returns ``status="ok"`` with the rendered ``text`` (header + body), a
    ``confidence`` (``high`` on an exact regulation match, ``medium`` on an
    above-floor non-exact one), and the verbatim ``content`` for pinning. On a
    medium match the resolved law differs from what the user typed, so a short
    confidence note is appended to ``text`` (NOT to ``content``) so the planner
    can verify before trusting it. Ambiguous / not-found return the
    corresponding plain string with no confidence and no pinnable content.

    TEXT ONLY — never a citation, never ``article_ref`` / ``chunk_parent_id``.
    """
    num = (article_number or "").strip()
    resolved = resolve_regulation_id(supabase, regulation_title)

    if resolved.ambiguous:
        return FetchArticleResult(text=resolved.ambiguous, status="ambiguous")

    if not resolved.reg_id:
        # No regulation matched at all — let the planner fall back to search.
        return FetchArticleResult(
            text=f"المادة {num} غير موجودة في {regulation_title.strip()}",
            status="not_found",
        )

    content = _fetch_article_content(supabase, resolved.reg_id, num)
    reg_name = resolved.display or regulation_title.strip()
    if not content:
        return FetchArticleResult(
            text=f"المادة {num} غير موجودة في {reg_name}",
            status="not_found", reg_id=resolved.reg_id, reg_name=reg_name,
            article_number=num,
        )

    confidence = "high" if resolved.exact else "medium"
    body = content.strip()
    text = f"## نص المادة {num} من {reg_name}\n\n{body}"
    if confidence == "medium":
        # Non-exact match: the resolved law is a best-guess for the title typed.
        text += (
            f"\n\n— (ثقة متوسطة: «{reg_name}» هو أقرب نظام مطابق للاسم المذكور، "
            "وليس تطابقًا تامًّا؛ تأكّد أنه النظام المقصود قبل اعتماده.)"
        )
    return FetchArticleResult(
        text=text, status="ok", confidence=confidence,
        reg_id=resolved.reg_id, reg_name=reg_name,
        article_number=num, content=body,
    )


def fetch_article_text(supabase, regulation_title: str, article_number: str) -> str:
    """Thin wrapper → the rendered ``text`` only (back-compat for callers/tests)."""
    return fetch_article_result(supabase, regulation_title, article_number).text


# --------------------------------------------------------------------------- #
# Statute package — ONE durable workspace item per search. Successful fetches
# accumulate on ``deps._fetched_articles`` during the decider's tool loop; the
# runner calls ``flush_statute_package`` once per turn to write the bundle. Lean
# reuse of save_memo's pattern: a fire-and-forget insert, best-effort (a failure
# NEVER affects the fetch). Lazy backend import + a monkeypatchable insert
# wrapper keep this module import-light and unit-testable.
# --------------------------------------------------------------------------- #


def accumulate_fetched_article(deps, *, reg_name: str, article_number: str,
                               content: str, confidence: str) -> None:
    """Record a successful fetch on the turn's accumulator for the package flush.

    Best-effort: appends ``{regulation, article_number, content, confidence}`` to
    ``deps._fetched_articles`` when that list slot exists (``PlannerDeps`` has
    it). A list append is atomic on the event loop, so concurrent ``fetch_article``
    calls accumulate safely without a lock. No-op on minimal deps (the fetch
    still returns its text).
    """
    bucket = getattr(deps, "_fetched_articles", None)
    if isinstance(bucket, list) and content:
        bucket.append({
            "regulation": reg_name,
            "article_number": str(article_number),
            "content": content,
            "confidence": confidence,
        })


def _package_title(articles: list[dict]) -> str:
    """Title for the statute package card: name the single article, else count."""
    if len(articles) == 1:
        a = articles[0]
        return f"نص المادة {a['article_number']} من {a['regulation']}"
    return f"نصوص المواد المستشهد بها ({len(articles)})"


def build_statute_package_md(articles: list[dict]) -> str:
    """Render the package body: the marker + one ``## نص المادة …`` section per
    article (verbatim content). Pure — unit-testable without a DB."""
    sections = "\n\n".join(
        f"## نص المادة {a['article_number']} من {a['regulation']}\n\n"
        f"{(a.get('content') or '').strip()}"
        for a in articles
    )
    return f"{_STATUTE_PACKAGE_MARKER}\n\n{sections}"


def _insert_statute_item(supabase, *, user_id: str, conversation_id: str,
                         title: str, content_md: str, metadata: dict) -> dict:
    """Insert the statute package row. Lazy backend import keeps this module
    light; tests monkeypatch THIS function rather than the heavy service layer."""
    from backend.app.services.workspace_service import create_workspace_item

    return create_workspace_item(
        supabase,
        user_id,
        kind=_STATUTE_KIND,
        created_by=_STATUTE_CREATED_BY,
        title=title,
        conversation_id=conversation_id,
        content_md=content_md,
        metadata=metadata,
    )


def flush_statute_package(deps) -> str | None:
    """Write the turn's accumulated articles as ONE ``statute_package`` workspace
    item, then clear the accumulator. One package per search.

    Deduped within the turn by ``(regulation, article_number)`` (the planner's
    retry loop may fetch the same article twice). Best-effort + fully guarded — a
    flush failure never propagates. Returns the new ``item_id``, or ``None`` when
    nothing was accumulated / scope is missing / on any error. The accumulator is
    snapshotted-and-cleared up front so a second flush can't double-write.
    """
    bucket = getattr(deps, "_fetched_articles", None)
    if not isinstance(bucket, list) or not bucket:
        return None
    articles = list(bucket)
    bucket.clear()

    user_id = getattr(deps, "user_id", "") or ""
    conversation_id = getattr(deps, "conversation_id", "") or ""
    if not (user_id and conversation_id):
        return None

    try:
        # Dedup within the turn — keep first occurrence of each (reg, article).
        seen: set[tuple[str, str]] = set()
        uniq: list[dict] = []
        for a in articles:
            key = (a.get("regulation", ""), str(a.get("article_number", "")))
            if key in seen:
                continue
            seen.add(key)
            uniq.append(a)
        if not uniq:
            return None

        row = _insert_statute_item(
            deps.supabase,
            user_id=user_id,
            conversation_id=conversation_id,
            title=_package_title(uniq),
            content_md=build_statute_package_md(uniq),
            metadata={
                "subtype": _STATUTE_PACKAGE_SUBTYPE,
                "articles": [
                    {
                        "regulation": a.get("regulation", ""),
                        "article_number": str(a.get("article_number", "")),
                        "confidence": a.get("confidence", ""),
                    }
                    for a in uniq
                ],
            },
        )
        item_id = row.get("item_id") or row.get("artifact_id") or None
        if item_id:
            _emit_pin_chip(deps, item_id, _package_title(uniq), len(uniq))
        return item_id
    except Exception as exc:  # noqa: BLE001
        logger.warning("fetch_article: statute package flush failed: %s", exc)
        return None


def _emit_pin_chip(deps, item_id: str, title: str, count: int) -> None:
    """Best-effort: surface a ``workspace_item_created`` chip via the planner's
    SSE sink so the pinned package card appears immediately. If no sink is wired,
    the card still loads on the next turn (persistence is the guarantee)."""
    emit = getattr(deps, "emit_sse", None)
    if not callable(emit):
        return
    try:
        emit({
            "type": "workspace_item_created",
            "item_id": item_id,
            "kind": _STATUTE_KIND,
            "subtype": _STATUTE_PACKAGE_SUBTYPE,
            "title": title,
            "created_by": _STATUTE_CREATED_BY,
        })
    except Exception:  # noqa: BLE001 — chip is cosmetic; never break the fetch
        logger.debug("fetch_article: pin chip emit failed", exc_info=True)


# --------------------------------------------------------------------------- #
# Pydantic AI tool.
# --------------------------------------------------------------------------- #


def register_fetch_article(agent: Agent) -> None:
    """Register the ``fetch_article`` tool on a Pydantic AI agent.

    The agent's deps must structurally satisfy :class:`HasSupabase`
    (``.supabase``). Registered on the **planner decider only** — it grounds the
    decider's ``planner_brief`` on the verbatim text of a numbered article
    before the search is decided.
    """

    @agent.tool
    async def fetch_article(  # noqa: RUF029 — supabase client is sync by design
        ctx: RunContext[HasSupabase],
        regulation_title: str,
        article_number: str,
    ) -> str:
        """Fetch the verbatim text of ONE article (مادة) from a named regulation.

        Use this BEFORE deciding the search, when the user cites a specific
        article *by number* in a specific law/regulation (e.g. «المادة 81 من
        نظام العمل»). Semantic search can't reliably retrieve an article by its
        number, so this does a deterministic structured lookup and returns the
        article's actual text — which you should then carry into
        ``planner_brief`` verbatim so it reaches the executors and aggregator.

        This does NOT replace the search: still run the normal reg_search so the
        answer gets its supporting sources and citations. The fetched article
        stays purely as ``planner_brief`` text — never turn it into a citation.

        Pass ``article_number`` as the plain string form the user used ("81",
        "1-1") — convert Arabic ordinals («الحادية والثمانون») or Arabic-Indic
        digits («٨١») to the plain Western-digit form first.

        All articles you fetch this turn are bundled into one durable reference
        card (it persists across turns) — you don't manage that.
        On an approximate (non-exact) regulation match the text carries a brief
        «(ثقة متوسطة …)» note: verify it's the intended law (``ask_user`` if
        unsure), and carry only the article body — not that note — into
        ``planner_brief``.

        Returns:
            - The article text, prefixed with a one-line header naming the
              resolved regulation, on success (plus a confidence note on a
              non-exact match).
            - A string starting ``AMBIGUOUS:`` listing candidate regulation
              titles when the named regulation is ambiguous — in which case use
              your ``ask_user`` tool to ask which one the user means.
            - ``"المادة N غير موجودة في <نظام>"`` when the article isn't found —
              fall back to a normal semantic search instead.

        Args:
            regulation_title: The regulation as the user named it, e.g.
                «نظام العمل».
            article_number: The article number as an exact-text key, e.g.
                "81" or compound "1-1".
        """
        try:
            result = await asyncio.to_thread(
                fetch_article_result, ctx.deps.supabase, regulation_title, article_number,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "fetch_article error for title=%r art=%r: %s",
                regulation_title, article_number, exc,
            )
            return f"المادة {(article_number or '').strip()} غير موجودة في {(regulation_title or '').strip()}"

        # A successful fetch is accumulated on the turn's deps slot; the runner
        # flushes all of them into ONE statute_package workspace item per search
        # (flush_statute_package). A plain list append — no DB write here.
        if result.status == "ok":
            accumulate_fetched_article(
                ctx.deps,
                reg_name=result.reg_name,
                article_number=result.article_number,
                content=result.content,
                confidence=result.confidence,
            )

        logger.info(
            "fetch_article: title=%r art=%r → status=%s conf=%s (%d chars)",
            regulation_title, article_number, result.status, result.confidence,
            len(result.text),
        )
        return result.text


__all__ = [
    "register_fetch_article",
    "fetch_article_result",
    "fetch_article_text",
    "resolve_regulation_id",
    "accumulate_fetched_article",
    "flush_statute_package",
    "build_statute_package_md",
    "_fetch_article_content",
    "_normalize_title",
    "FetchArticleResult",
    "RegCandidate",
    "ResolveResult",
    "HasSupabase",
]
