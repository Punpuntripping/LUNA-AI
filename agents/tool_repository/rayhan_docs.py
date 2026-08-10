"""rayhan_docs — let the router answer questions about ريحان itself.

Two router tools over the ``product_docs`` table (migration 126):

* ``open_rayhan_page(page)`` — what Rayhan is, who it's for, how it compares to
  general AI tools, how plans work, and the three legal documents (الخصوصية /
  الشروط / تقنيع المعرّفات).
* ``open_rayhan_guide(guide)`` — how to actually use it: the agents, مساحة
  العمل, the legal library, the usage-points policy, a step-by-step guide,
  best practices for phrasing a legal question, and worked examples.

Why two tools and not one with a category argument: the split is how the owner
thinks about the surface (marketing/policy vs. how-to), and giving the model
two narrow tools with disjoint enums is a stronger selection signal than one
wide tool whose right answer depends on reading fifteen descriptions.

WHY THE KEYS ARE A ``Literal`` AND THE BODIES ARE IN THE DB
-----------------------------------------------------------
The key set renders into each tool's JSON schema, so the model sees the entire
catalog — every doc, with a one-line description — **without a single token of
it entering the router's system prompt**, which is the cached prefix on every
turn of every conversation. It also makes a hallucinated key structurally
impossible rather than something to validate.

The bodies live in Postgres because product copy changes on marketing's clock,
not on a deploy's. Adding a doc needs a code change (one enum member); changing
what a doc *says* needs only a row update.

⚠ Renaming a key here without renaming it in the table removes the doc from the
model's reach silently: the tool keeps advertising the key and the lookup finds
nothing. Same for ``catalog`` — a row catalogued ``about`` is unreachable from
``open_rayhan_guide`` no matter what its key says.

Caching: a per-process TTL cache. These rows change a few times a month and are
read on a latency-sensitive path, so re-fetching per call would be a round-trip
spent to observe nothing changed. The TTL is what makes a console edit go live
without a redeploy — it is the whole reason the content is in a table, so keep
it short enough that a fix feels immediate.

Registration::

    from agents.tool_repository.rayhan_docs import register_rayhan_docs
    register_rayhan_docs(agent)   # deps need only `.supabase`
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Literal, Protocol, get_args, runtime_checkable

from pydantic_ai import Agent, RunContext

logger = logging.getLogger(__name__)


# --- Schema config: a table/column rename is a one-line change here. ---------
_TABLE = "product_docs"

# How long a fetched doc stays served from memory. Ten minutes: long enough
# that a burst of questions in one conversation costs one round-trip, short
# enough that fixing a wrong sentence in the Supabase console reaches users
# while the person who reported it is still in the chat.
_CACHE_TTL_SECONDS = 600

# Hard cap on the returned body. Sized so the longest doc — the terms of
# service, 14.3 KB — arrives WHOLE. That is deliberate: truncating a binding
# legal text mid-clause and then answering from the stump is worse than not
# answering, because the half that got cut is where the exceptions live.
# A doc that would exceed this is a doc that needs splitting, not trimming.
_MAX_CHARS = 18_000

_PUBLIC_ORIGIN = "https://rayhanai.com"


# --- The catalog: keys are a contract with product_docs.doc_key -------------

AboutDocKey = Literal[
    "about",
    "audiences",
    "vs_chatgpt",
    "pricing",
    "privacy",
    "terms",
    "masking",
    "data_protection",
]

GuideDocKey = Literal[
    "how_it_works",
    "workspace",
    "library",
    "usage_limits",
    "guide",
    "best_practices",
    "examples",
]

ABOUT_DOC_KEYS: tuple[str, ...] = get_args(AboutDocKey)
GUIDE_DOC_KEYS: tuple[str, ...] = get_args(GuideDocKey)
ALL_DOC_KEYS: tuple[str, ...] = ABOUT_DOC_KEYS + GUIDE_DOC_KEYS

CATALOG_ABOUT = "about"
CATALOG_GUIDE = "guide"


@runtime_checkable
class HasSupabase(Protocol):
    """Structural deps contract — the tools need nothing but a client.

    ``RouterDeps`` satisfies this. Kept loose (``object``) to avoid a hard
    supabase import in this module.
    """

    supabase: object


# --------------------------------------------------------------------------- #
# Pure surface — unit-testable without an agent or a live DB.
# --------------------------------------------------------------------------- #

# doc_key → (fetched_at_monotonic, rendered_markdown). Module-level, so it is
# shared across every conversation in the process — which is the point: these
# docs are identical for all users, and a container serving many turns should
# pay for each one once.
_cache: dict[str, tuple[float, str]] = {}


def clear_cache() -> None:
    """Drop every cached doc. For tests, and for a future admin refresh hook."""
    _cache.clear()


def _cache_get(doc_key: str, *, now: float | None = None) -> str | None:
    """Return the cached rendering if present and unexpired, else ``None``."""
    entry = _cache.get(doc_key)
    if entry is None:
        return None
    fetched_at, rendered = entry
    clock = time.monotonic() if now is None else now
    if clock - fetched_at > _CACHE_TTL_SECONDS:
        _cache.pop(doc_key, None)
        return None
    return rendered


def _cache_put(doc_key: str, rendered: str, *, now: float | None = None) -> None:
    _cache[doc_key] = (time.monotonic() if now is None else now, rendered)


def _text(value: object) -> str:
    """Coerce a row field to a clean string, treating anything else as absent.

    Every column read here is ``text`` in Postgres, so this is not about real
    rows — it is about the fakes. Pydantic AI's ``TestModel`` exercises every
    registered tool against whatever client the test injected (a ``MagicMock``
    in the router's smoke test), and a ``MagicMock`` answers ``.get()`` with
    another mock that has a ``.strip()`` and no ``__len__`` — so the length
    check below dies with a ``TypeError`` and the whole smoke test fails inside
    a tool it was not testing. Treating a non-``str`` as empty keeps that
    failure honest and localised.
    """
    return value.strip() if isinstance(value, str) else ""


def render_doc(row: dict) -> str:
    """Render a ``product_docs`` row into the markdown the model reads.

    The header is three lines of orientation the body itself does not carry:
    what this document is, and — when the doc mirrors a live page — the public
    URL. That URL is the reason the router can say «التفاصيل الكاملة في
    rayhanai.com/pricing» instead of paraphrasing a page it half-remembers.

    A doc with no ``canonical_path`` gets NO url line, and that silence is
    load-bearing: `guide`, `best_practices` and `examples` have no page behind
    them yet, and a router that invents `/learn/best-practices` sends the user
    to a 404 the first time they trust it.
    """
    title = _text(row.get("title"))
    blurb = _text(row.get("blurb"))
    body = _text(row.get("content_md"))
    path = _text(row.get("canonical_path"))

    lines = [f"# {title}" if title else "# (بدون عنوان)"]
    if blurb:
        lines.append(f"_{blurb}_")
    if path:
        lines.append(f"الصفحة العامة: {_PUBLIC_ORIGIN}{path}")
    lines.append("")

    if len(body) > _MAX_CHARS:
        # Should not happen — _MAX_CHARS is sized above the longest doc. If it
        # ever does, say so in the text rather than letting the model answer
        # from a body it cannot tell is incomplete.
        body = body[:_MAX_CHARS].rstrip() + (
            "\n\n… (النص مقتطع — أحِل المستخدم إلى الصفحة العامة للاطلاع على "
            "النص الكامل)"
        )
        logger.warning("render_doc: %r exceeded %d chars", row.get("doc_key"), _MAX_CHARS)

    lines.append(body)
    return "\n".join(lines)


def fetch_doc(supabase, doc_key: str, catalog: str) -> str | None:
    """Fetch one published doc and render it. ``None`` if there is no such row.

    ``catalog`` is part of the lookup, not a post-filter: it is what keeps
    ``open_rayhan_guide`` from serving the terms of service just because the
    model asked for the wrong key. Never raises — a DB hiccup is logged and
    surfaces as ``None``, which the tool turns into an honest "I don't have
    this" rather than a fabricated answer.
    """
    try:
        result = (
            supabase.table(_TABLE)
            .select("doc_key, title, blurb, content_md, canonical_path")
            .eq("doc_key", doc_key)
            .eq("catalog", catalog)
            .eq("is_published", True)
            .maybe_single()
            .execute()
        )
    except Exception as exc:  # noqa: BLE001 — never break a turn over a doc
        logger.warning("fetch_doc(%r, %r) failed: %s", doc_key, catalog, exc)
        return None

    row = getattr(result, "data", None) if result is not None else None
    if not row:
        logger.info("fetch_doc: no published %r row in catalog %r", doc_key, catalog)
        return None
    return render_doc(row)


async def open_doc(deps, doc_key: str, catalog: str) -> str:
    """Cache-checked, thread-offloaded fetch. Returns ``""`` when unavailable.

    The supabase client is sync, so the fetch goes through ``to_thread`` to
    keep the SSE event loop free — the same shape ``fetch_article`` and
    ``unfold_workspace_item`` use.
    """
    cached = _cache_get(doc_key)
    if cached is not None:
        return cached

    rendered = await asyncio.to_thread(fetch_doc, deps.supabase, doc_key, catalog)
    if rendered is None:
        return ""
    _cache_put(doc_key, rendered)
    return rendered


_NOT_FOUND = (
    "لا يتوفر هذا المستند حالياً. لا تخترع محتواه — أخبر المستخدم أنك لا تملك "
    "التفاصيل الآن، وأحِله إلى الصفحات العامة على rayhanai.com."
)


# --------------------------------------------------------------------------- #
# Registration
# --------------------------------------------------------------------------- #


def register_rayhan_docs(agent: Agent) -> None:
    """Register ``open_rayhan_page`` + ``open_rayhan_guide`` on an agent.

    The agent's deps must structurally satisfy :class:`HasSupabase`.
    """

    @agent.tool
    async def open_rayhan_page(  # noqa: RUF029 — supabase client is sync by design
        ctx: RunContext[HasSupabase],
        page: AboutDocKey,
    ) -> str:
        """Open an official ريحان document about the product, its plans, or its policies.

        Call this before answering ANY question about ريحان as a product — what
        it is, who it is for, what it costs, what it does with the user's data.
        Answer only from what this returns; never from memory.

        Available pages:

        * ``about`` — what ريحان is, what it does, and the corpus it searches
        * ``audiences`` — who it is built for (lawyers, specialists, founders,
          individuals) with real example questions per audience
        * ``vs_chatgpt`` — how ريحان differs from general AI tools like ChatGPT
        * ``pricing`` — how plans and usage points work. NOTE: carries no
          amounts — for the current prices, point the user to
          https://rayhanai.com/pricing
        * ``privacy`` — the privacy policy, verbatim
        * ``terms`` — the terms and conditions, verbatim
        * ``masking`` — وضع السرية: how personal identifiers in the user's
          messages are masked before any model sees them
        * ``data_protection`` — the plain-language account of where data lives,
          which processors touch it, and what the user controls

        Args:
            page: Which document to open.
        """
        text = await open_doc(ctx.deps, page, CATALOG_ABOUT)
        return text or _NOT_FOUND

    @agent.tool
    async def open_rayhan_guide(  # noqa: RUF029 — supabase client is sync by design
        ctx: RunContext[HasSupabase],
        guide: GuideDocKey,
    ) -> str:
        """Open an official ريحان guide on how to use the product well.

        Call this before answering ANY question about how to use ريحان — how it
        works, what a feature does, how to get better results. Answer only from
        what this returns; never from memory.

        Available guides:

        * ``how_it_works`` — the three agents (الموجّه / الباحث / الكاتب), what
          each does, and why the answers carry sources
        * ``workspace`` — مساحة العمل: what it holds, why it exists, how
          numbered references trace back to official sources
        * ``library`` — المكتبة القانونية: what the corpus contains and how the
          public library pages are browsed
        * ``usage_limits`` — the points policy: what each operation consumes,
          the session and weekly windows, what is free
        * ``guide`` — step by step, from first message to finished document
        * ``best_practices`` — how to phrase a legal question so the search
          lands: facts, parties, dates, what to attach
        * ``examples`` — real example questions and what ريحان returns for each

        Args:
            guide: Which guide to open.
        """
        text = await open_doc(ctx.deps, guide, CATALOG_GUIDE)
        return text or _NOT_FOUND


__all__ = [
    "register_rayhan_docs",
    "open_doc",
    "fetch_doc",
    "render_doc",
    "clear_cache",
    "AboutDocKey",
    "GuideDocKey",
    "ABOUT_DOC_KEYS",
    "GUIDE_DOC_KEYS",
    "ALL_DOC_KEYS",
    "CATALOG_ABOUT",
    "CATALOG_GUIDE",
    "HasSupabase",
]
