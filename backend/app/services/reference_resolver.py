"""``ref_id`` → gating identity: the Phase C resolver (plan §6.3, D15 / D15.1).

A chat citation and a public-library page can point at the SAME statute through
two completely different identifiers. The metering ledger
(``library_unlocks``) and the ``seo_item_meta`` sidecar both key on
``(content_type, content_id)``; a reference row keys on the URA-emitted
``ref_id``. This module is the ONE translation between them.

    ``reg:<uuid>``        → ``('article', '{regulation_id}#{n}')`` | ``('regulation', regulation_id)``
    ``case:<case_ref>``   → ``('judgment', cases.id)``
    ``circular:<uuid>``   → ``('circular', circulars.id)``
    ``compliance:<sha1>`` → ``('service', services.id)``  — never gated, never charged
    ``article:<uuid>``    → ``('article', '{regulation_id}#{n}')`` | ``('regulation', regulation_id)``
    ``regdoc:<uuid>``     → ``('regulation', regulations_v2.id)``

THE TWO simple_search PREFIXES ADD NO METERING VOCABULARY
---------------------------------------------------------
``article:`` and ``regdoc:`` (plan §6.1a) name an ``articles_v2`` row and a
``regulations_v2`` row directly — no chunk in between — so they SHORT-CIRCUIT to
the two content types the ledger already speaks (``library_items_service``'s
``SHELF_CONTENT_TYPES``). No new content_type is minted, and none is needed: a
مادة reached by lookup and a مادة reached by chunk are the same مادة, must cost
the same unlock, and must be covered by the same D5 نظام grant. Inventing a third
type would double-charge a user who already opened the نظام.

They are also NOT ``always_free``. D12's "regulations are not metered" governs
what the AGENT may read while composing an answer (it reads full content
unconditionally); this module governs the USER-facing «عرض المصدر» reveal, where
a نظام has always cost exactly what a ``reg:`` chunk of that same نظام costs.
Keeping them identical is the point — the two paths are two doors onto one
document.

WHY ``reg:`` IS NOT A REGULATION ID (the trap this module exists for)
---------------------------------------------------------------------
``reg:<uuid>`` carries a **``chunks_v2.id``**, not a ``regulations_v2.id`` — a
chunk is an arbitrary slice of a نظام that may own zero, one, or many مواد. The
sidecar has no chunk-level identity at all, so the chunk must be lifted to the
nearest thing the library publishes:

* the chunk owns EXACTLY ONE مادة → ``('article', '{regulation_id}#{article_no}')``
* anything else (0 مواد, or 2+)   → ``('regulation', regulation_id)``

**D15.1, verified live 2026-07-27: 50,923 مواد live across 11,455 distinct
chunks, of which only 2,140 own exactly one مادة — so ~81% of ``reg:`` citations
resolve to ``regulation``, not ``article``.** That is intended, not a bug. A
``regulation`` unlock costs ``clamp(ceil(n/25),1,8)`` (median نظام = 1) and grants
the WHOLE statute (D5: the نظام covers every مادة under it). Do NOT "fix" it by
charging regulation price for chunk-only access — that is exactly the trick
feeling §5.1 forbids. The corollary the UI must honour: a ``reg:``-backed reveal
unlocked the **نظام**, so the toast/balance chip names the نظام, not the chunk —
which is why :class:`ResolvedRef` carries ``title``/``article_no`` alongside the
bare tuple.

FAIL CLOSED
-----------
Every unresolvable / malformed / vanished id returns ``None``, and the caller
MUST refuse (402, ``reason='unresolvable'``). A resolver that fails open hands
out corpus bytes for free, which is the whole thing this phase is bounding.

Two exceptions are policy, not leaks:
* ``service`` — compliance pages are never gated (§1.3), so the tuple comes back
  with ``always_free=True``; ``resolve_access`` also short-circuits
  ``content_type='service'`` to open. No charge, no ledger row, ever.
* a circular whose body is ``<= CIRCULAR_FREE_LENGTH`` (800) chars — the public
  ``/circulars/{slug}`` page serves it FULLY OPEN to anonymous visitors
  (``library_service.effective_circular_gate``). Charging an unlock in chat for
  bytes the public page gives away for free is the same trick feeling, so short
  circulars come back ``always_free=True`` too. The pure downgrade function is
  reused verbatim so the two surfaces cannot drift.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any, Optional

from backend.app.services.library_service import effective_circular_gate
from shared.db.run import run_db

logger = logging.getLogger(__name__)

# Loose alias — the project uses the SYNC supabase-py client inside async
# handlers, driven through ``run_db`` (asyncio.to_thread).
SupabaseClient = Any

__all__ = [
    "ResolvedRef",
    "resolve_ref",
    "resolve_ref_id",
]

_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)

# ``ura.reg_adapter._service_ref_id`` mints sha1(service_ref)[:16]. Compliance
# refs are the one family whose ref_id sometimes reaches us WITHOUT its prefix
# (D15 lists it as "bare sha1"), so the shape itself has to be recognisable.
_SHA1_16_RE = re.compile(r"^[0-9a-f]{16}$")

# jsonb key inside ``chunks_v2.owns`` listing the مواد a chunk owns:
# ``{"MADDA": [6]}`` / ``{"MADDA": [7, 8, 9]}``.
_OWNS_ARTICLE_KEY = "madda"


@dataclass(frozen=True)
class ResolvedRef:
    """What a citation points at, in the vocabulary the ledger speaks.

    ``content_type`` / ``content_id`` are exactly the pair
    ``library_service.resolve_access`` and ``seo_item_meta`` key on.

    ``title`` / ``article_no`` exist for D15.1: after a ``reg:`` reveal the user
    must be told which **نظام** was unlocked (never "chunk 4f2a…"). They are
    best-effort — populated only when the resolution already had to read the row
    that carries them, so no extra round-trip is spent on a label. An empty
    ``title`` means "the caller should fall back to the SourceView's own title".

    ``parent_regulation_id`` is passed straight through to ``resolve_access`` so
    the D5 نظام-covers-مادة check works without re-deriving it from the key.

    ``always_free`` marks the two policy-open cases (a compliance service, a
    short circular). The caller must NOT charge, must NOT write a ledger row, and
    must still serve the content.
    """

    content_type: str
    content_id: str
    title: str = ""
    parent_regulation_id: Optional[str] = None
    article_no: Optional[int] = None
    always_free: bool = False
    free_reason: str = ""

    def as_tuple(self) -> tuple[str, str]:
        return (self.content_type, self.content_id)


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------


async def resolve_ref_id(
    supabase: SupabaseClient,
    ref_id: str,
    *,
    domain: Optional[str] = None,
    item_id: Optional[str] = None,
) -> Optional[tuple[str, str]]:
    """``ref_id`` → ``(content_type, content_id)``, or ``None`` (fail closed).

    The pinned D15 signature. It is a thin projection of :func:`resolve_ref` —
    use that one when you need the label / parent / free-by-policy flags.
    """
    resolved = await resolve_ref(supabase, ref_id, domain=domain, item_id=item_id)
    return resolved.as_tuple() if resolved is not None else None


async def resolve_ref(
    supabase: SupabaseClient,
    ref_id: str,
    *,
    domain: Optional[str] = None,
    item_id: Optional[str] = None,
) -> Optional[ResolvedRef]:
    """Full resolution of one ``workspace_item_references`` row.

    Args:
        ref_id: the URA-emitted identifier stored on the row.
        domain: the row's ``domain`` column (``regulations`` | ``cases`` |
            ``compliance`` | ``circulars`` | ``articles`` | ``regulation_docs``).
            Used only to disambiguate a prefix-less ``ref_id``; an explicit
            prefix always wins, because the prefix and the id were minted
            together by the same adapter.
        item_id: the row's ``item_id`` column — the source-row PK
            (``chunks_v2.id`` / ``cases.id`` / ``services.id`` /
            ``circulars.id`` / ``articles_v2.id`` / ``regulations_v2.id``) when
            the publisher managed to resolve it. Where it is authoritative it is
            preferred, since it skips a lookup.

    Returns:
        ``ResolvedRef`` or ``None``. ``None`` means REFUSE — never "allow".
    """
    rid = (ref_id or "").strip()
    dom = (domain or "").strip().lower()
    iid = str(item_id).strip() if item_id else ""

    prefix, sep, tail = rid.partition(":")
    if not sep:
        prefix, tail = "", rid
    prefix = prefix.strip().lower()
    tail = tail.strip()

    # Prefix first (it was minted with the id), domain as the fallback for a
    # prefix-less / legacy row.
    if prefix == "reg" or (not prefix and dom == "regulations"):
        return await _resolve_regulation(supabase, tail, iid)
    # simple_search — checked BEFORE the generic branches below purely for
    # readability; the prefixes are disjoint, and `regdoc` is not a `reg` match
    # because the comparison is on the whole prefix token, not a startswith.
    if prefix == "article" or (not prefix and dom == "articles"):
        return await _resolve_article_row(supabase, tail, iid)
    if prefix == "regdoc" or (not prefix and dom == "regulation_docs"):
        return await _resolve_regulation_doc(supabase, tail, iid)
    if prefix == "case" or (not prefix and dom == "cases"):
        return await _resolve_case(supabase, tail, iid)
    if prefix == "circular" or (not prefix and dom == "circulars"):
        return await _resolve_circular(supabase, tail, iid)
    if (
        prefix == "compliance"
        or (not prefix and dom == "compliance")
        or (not prefix and _SHA1_16_RE.match(tail or ""))
    ):
        return _resolve_service(iid)

    logger.info(
        "reference_resolver: unresolvable ref_id=%r domain=%r — refusing", rid, domain
    )
    return None


# ---------------------------------------------------------------------------
# Per-domain resolution
# ---------------------------------------------------------------------------


async def _resolve_regulation(
    supabase: SupabaseClient, tail: str, item_id: str
) -> Optional[ResolvedRef]:
    """``reg:<chunks_v2.id>`` → the نظام, or the single مادة the chunk owns."""
    chunk_id = tail if _UUID_RE.match(tail or "") else ""
    if not chunk_id and _UUID_RE.match(item_id or ""):
        # Legacy rows whose ref_id lost its uuid still carry chunks_v2.id here.
        chunk_id = item_id
    if not chunk_id:
        logger.info("reference_resolver: reg ref without a chunk uuid (%r)", tail)
        return None

    row = await run_db(_fetch_chunk, supabase, chunk_id)
    if not row:
        # Re-chunked or deleted → nothing to unlock. Fail closed.
        logger.info("reference_resolver: chunks_v2 %s not found — refusing", chunk_id)
        return None

    regulation_id = str(row.get("regulation_id") or "").strip()
    if not regulation_id:
        logger.info("reference_resolver: chunk %s has no regulation_id", chunk_id)
        return None

    owned = _owned_article_numbers(row.get("owns"))
    if len(owned) == 1:
        return ResolvedRef(
            content_type="article",
            content_id=f"{regulation_id}#{owned[0]}",
            parent_regulation_id=regulation_id,
            article_no=owned[0],
        )

    # 0 مواد or 2+ → the نظام itself (D15.1: the majority case, by design).
    return ResolvedRef(
        content_type="regulation",
        content_id=regulation_id,
        parent_regulation_id=regulation_id,
    )


async def _resolve_article_row(
    supabase: SupabaseClient, tail: str, item_id: str
) -> Optional[ResolvedRef]:
    """``article:<articles_v2.id>`` → the مادة, or its نظام when unpublishable.

    One lookup, because the sidecar key is ``'{regulation_id}#{article_no}'`` and
    an ``articles_v2.id`` carries neither half. The row gives both.

    The int() gate is the SAME policy ``_owned_article_numbers`` applies to a
    chunk's ``owns`` map: a compound number («7-4», «1-1» — they exist in the
    corpus) has no published مادة page, so no sidecar key can be minted for it
    and the ref must lift to the نظام. Never dropped — a citation the reader can
    see must always resolve to something they can unlock.

    ``parent_regulation_id`` is set on BOTH branches so ``resolve_access`` can
    apply D5 (a نظام unlock covers every مادة under it) without re-deriving it.
    """
    article_id = tail if _UUID_RE.match(tail or "") else ""
    if not article_id and _UUID_RE.match(item_id or ""):
        article_id = item_id
    if not article_id:
        logger.info("reference_resolver: article ref without a uuid (%r)", tail)
        return None

    row = await run_db(_fetch_article, supabase, article_id)
    if not row:
        logger.info("reference_resolver: articles_v2 %s not found — refusing", article_id)
        return None

    regulation_id = str(row.get("regulation_id") or "").strip()
    if not regulation_id:
        logger.info("reference_resolver: article %s has no regulation_id", article_id)
        return None

    raw_number = str(row.get("article_number") or "").strip()
    try:
        article_no: Optional[int] = int(raw_number)
    except (TypeError, ValueError):
        article_no = None

    if article_no is not None:
        return ResolvedRef(
            content_type="article",
            content_id=f"{regulation_id}#{article_no}",
            parent_regulation_id=regulation_id,
            article_no=article_no,
        )

    return ResolvedRef(
        content_type="regulation",
        content_id=regulation_id,
        parent_regulation_id=regulation_id,
    )


async def _resolve_regulation_doc(
    supabase: SupabaseClient, tail: str, item_id: str
) -> Optional[ResolvedRef]:
    """``regdoc:<regulations_v2.id>`` → ``('regulation', id)``.

    The id IS the answer, so the lookup exists for two other reasons: to FAIL
    CLOSED on a vanished / fabricated uuid (a resolver that trusted the id would
    let a caller mint a ledger row for a نظام that does not exist), and to carry
    the نظام's title into the reveal's «unlocked» payload so the toast names the
    document rather than a uuid.

    ``item_id`` is preferred over the ref_id tail — it is the same
    ``regulations_v2.id`` the write path validated — matching ``_resolve_circular``.
    """
    reg_id = item_id if _UUID_RE.match(item_id or "") else ""
    if not reg_id and _UUID_RE.match(tail or ""):
        reg_id = tail
    if not reg_id:
        logger.info("reference_resolver: regdoc ref without a uuid (%r)", tail)
        return None

    row = await run_db(_fetch_regulation, supabase, reg_id)
    if not row:
        logger.info("reference_resolver: regulations_v2 %s not found — refusing", reg_id)
        return None

    return ResolvedRef(
        content_type="regulation",
        content_id=reg_id,
        title=((row.get("clean_title") or row.get("title") or "") or "").strip(),
        parent_regulation_id=reg_id,
    )


async def _resolve_case(
    supabase: SupabaseClient, tail: str, item_id: str
) -> Optional[ResolvedRef]:
    """``case:<case_ref>`` → ``('judgment', cases.id)``.

    ``cases.id`` is the sidecar ``content_id`` for judgments — confirmed against
    ``scripts/build_judgment_slugs.py`` which writes ``cases.id`` verbatim.

    ``item_id`` is already ``cases.id`` (``persist_item_references`` resolves it
    through ``case_ref`` at write time), so when it is present the whole lookup
    is skipped. Falls back to the ``case_ref`` lookup, then — for pre-URA-v3 rows
    that minted ``case:<uuid>`` — to a direct id lookup.
    """
    if _UUID_RE.match(item_id or ""):
        return ResolvedRef(content_type="judgment", content_id=item_id)

    case_ref = tail
    if case_ref:
        row = await run_db(_fetch_case_by_ref, supabase, case_ref)
        if row and row.get("id"):
            return ResolvedRef(
                content_type="judgment",
                content_id=str(row["id"]),
                title=_case_label(row),
            )

    if _UUID_RE.match(case_ref or ""):
        row = await run_db(_fetch_case_by_id, supabase, case_ref)
        if row and row.get("id"):
            return ResolvedRef(
                content_type="judgment",
                content_id=str(row["id"]),
                title=_case_label(row),
            )

    logger.info("reference_resolver: case %r not found — refusing", case_ref)
    return None


async def _resolve_circular(
    supabase: SupabaseClient, tail: str, item_id: str
) -> Optional[ResolvedRef]:
    """``circular:<circulars.id>`` → ``('circular', id)``.

    Also decides the short-circular policy exemption: the public
    ``/circulars/{slug}`` page renders a ``<= 800``-char body fully open to
    anonymous visitors, so charging an unlock for the same bytes in chat would be
    a strictly worse deal than not signing in. The gate downgrade is delegated to
    ``library_service.effective_circular_gate`` so the two surfaces cannot drift.

    Cost note: this reads ``circulars.content`` purely to measure it (PostgREST
    has no ``length()`` projection), and that column holds the 168 KB outlier.
    The read is server-side only, happens once per reveal, and rides behind the
    20/min route limiter — the alternative (charging for free content) is worse.
    """
    circ_id = item_id if _UUID_RE.match(item_id or "") else ""
    if not circ_id and _UUID_RE.match(tail or ""):
        circ_id = tail
    if not circ_id:
        logger.info("reference_resolver: circular ref without a uuid (%r)", tail)
        return None

    row = await run_db(_fetch_circular, supabase, circ_id)
    if not row:
        logger.info("reference_resolver: circular %s not found — refusing", circ_id)
        return None

    body_len = len(row.get("content") or "")
    is_open = effective_circular_gate("gated", body_len) == "open"
    return ResolvedRef(
        content_type="circular",
        content_id=circ_id,
        title=(row.get("title") or "").strip(),
        always_free=is_open,
        free_reason="short_circular" if is_open else "",
    )


def _resolve_service(item_id: str) -> ResolvedRef:
    """Compliance service → never gated, never charged, never a ledger row (§1.3).

    The ``compliance:<sha1>`` ref_id carries only a hash of ``service_ref``, so
    ``item_id`` (``services.id``) is the only usable handle. It may legitimately
    be NULL on rows whose service lookup failed at write time — the reveal is
    still free, there is simply nothing to shelve in «مكتبتي».
    """
    return ResolvedRef(
        content_type="service",
        content_id=item_id if _UUID_RE.match(item_id or "") else "",
        always_free=True,
        free_reason="service",
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _owned_article_numbers(owns: Any) -> list[int]:
    """The مادة numbers a chunk owns, from the ``owns`` jsonb.

    Shape is ``{"MADDA": [6]}`` / ``{"MADDA": [7, 8, 9]}``. The key is matched
    case-insensitively and a scalar value is tolerated; non-integer entries are
    dropped because the sidecar key ``'{regulation_id}#{article_no}'`` is only
    ever minted with an integer مادة number (compound refs like «36-3» have no
    published مادة page, so their chunk must resolve to the نظام).
    """
    if not isinstance(owns, dict):
        return []
    raw: Any = None
    for key, value in owns.items():
        if str(key).strip().lower() == _OWNS_ARTICLE_KEY:
            raw = value
            break
    if raw is None:
        return []
    if not isinstance(raw, (list, tuple)):
        raw = [raw]

    out: list[int] = []
    for entry in raw:
        try:
            out.append(int(str(entry).strip()))
        except (TypeError, ValueError):
            # A non-numeric مادة ref means we cannot mint the sidecar key; the
            # chunk therefore falls through to the نظام (never silently dropped).
            return []
    return out


def _case_label(row: dict[str, Any]) -> str:
    """``court | case_number | date_hijri`` — mirrors ``CaseSourceView.title``."""
    parts = [
        str(row.get("court") or "").strip(),
        str(row.get("case_number") or "").strip(),
        str(row.get("date_hijri") or "").strip(),
    ]
    return " | ".join(p for p in parts if p)


# --- sync Supabase reads (always driven through run_db) --------------------


def _fetch_chunk(supabase: SupabaseClient, chunk_id: str) -> Optional[dict[str, Any]]:
    """``chunks_v2`` → ``regulation_id`` + ``owns``. Read-only.

    A query FAILURE returns ``None``, which makes the caller REFUSE. That is the
    safe direction: a DB blip must never mint free corpus access.
    """
    try:
        res = (
            supabase.table("chunks_v2")
            .select("id, regulation_id, owns")
            .eq("id", chunk_id)
            .limit(1)
            .execute()
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("reference_resolver: chunks_v2 lookup failed (%s): %s", chunk_id, e)
        return None
    rows = res.data or []
    return rows[0] if rows else None


def _fetch_article(supabase: SupabaseClient, article_id: str) -> Optional[dict[str, Any]]:
    """``articles_v2`` → ``regulation_id`` + ``article_number``. Read-only.

    A query FAILURE returns ``None`` → the caller REFUSES. Same safe direction as
    ``_fetch_chunk``: a DB blip must never mint free corpus access.
    """
    try:
        res = (
            supabase.table("articles_v2")
            .select("id, regulation_id, article_number")
            .eq("id", article_id)
            .limit(1)
            .execute()
        )
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "reference_resolver: articles_v2 lookup failed (%s): %s", article_id, e
        )
        return None
    rows = res.data or []
    return rows[0] if rows else None


def _fetch_regulation(supabase: SupabaseClient, reg_id: str) -> Optional[dict[str, Any]]:
    """``regulations_v2`` → existence proof + the title for the unlock toast."""
    try:
        res = (
            supabase.table("regulations_v2")
            .select("id, title, clean_title")
            .eq("id", reg_id)
            .limit(1)
            .execute()
        )
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "reference_resolver: regulations_v2 lookup failed (%s): %s", reg_id, e
        )
        return None
    rows = res.data or []
    return rows[0] if rows else None


def _fetch_case_by_ref(supabase: SupabaseClient, case_ref: str) -> Optional[dict[str, Any]]:
    """``cases`` by the human-readable ``case_ref`` (NOT the uuid)."""
    try:
        res = (
            supabase.table("cases")
            .select("id, case_ref, court, case_number, date_hijri")
            .eq("case_ref", case_ref)
            .limit(1)
            .execute()
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("reference_resolver: cases lookup failed (%s): %s", case_ref, e)
        return None
    rows = res.data or []
    return rows[0] if rows else None


def _fetch_case_by_id(supabase: SupabaseClient, case_id: str) -> Optional[dict[str, Any]]:
    """``cases`` by uuid — legacy ``case:<uuid>`` ref_ids only."""
    try:
        res = (
            supabase.table("cases")
            .select("id, case_ref, court, case_number, date_hijri")
            .eq("id", case_id)
            .limit(1)
            .execute()
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("reference_resolver: cases id lookup failed (%s): %s", case_id, e)
        return None
    rows = res.data or []
    return rows[0] if rows else None


def _fetch_circular(supabase: SupabaseClient, circ_id: str) -> Optional[dict[str, Any]]:
    """``circulars`` → title + body (the body is read only to measure it)."""
    try:
        res = (
            supabase.table("circulars")
            .select("id, title, content")
            .eq("id", circ_id)
            .limit(1)
            .execute()
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("reference_resolver: circulars lookup failed (%s): %s", circ_id, e)
        return None
    rows = res.data or []
    return rows[0] if rows else None
