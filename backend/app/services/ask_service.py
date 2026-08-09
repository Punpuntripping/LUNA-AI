"""اسأل ريحان — anonymous popup ask + post-signup claim (SEO Library Phase 4).

Business logic behind ``backend/app/api/public_ask.py``. See
``.claude/plans/seo_public_library.md`` § "Phase 4 — Conversion layer" and the
storage schema ``shared/db/migrations/099_anon_questions.sql``.

Trust boundary (mirrors the library gate philosophy — 099 header):
    ``anon_questions.answer_md`` holds the COMPLETE generated answer server-side.
    An anon client only ever receives the first ``visible_prefix_chars`` (220)
    of it — the remainder NEVER leaves the server. The full answer is revealed
    ONLY via the authed claim endpoint (the "continuity moment").

Environment variables (read fresh per request so a deploy flip needs no restart):
    ANON_ASK_ENABLED     — master kill switch. DEFAULT OFF (fail-closed). Any of
                           {1,true,yes,on} turns the anon ask endpoint on;
                           anything else → 503 «الخدمة غير متاحة حالياً».
    ANON_ASK_DAILY_MAX   — global cap on anon_questions rows created per UTC day
                           (cost guard). Default 200. Exceeded → 503.
    TURNSTILE_SECRET_KEY — Cloudflare Turnstile secret. When SET, every ask must
                           carry a valid ``turnstile_token`` (verified against
                           Cloudflare siteverify, httpx 5s, FAIL-CLOSED) → 403 on
                           failure. When UNSET (keys pending), verification is
                           skipped entirely.

Grounding is page-context ONLY (no deep_search): the answer is grounded in the
current page's own text (first chunks of a regulation, a مادة's text, a service's
structured fields, or a blog post's body), capped at ~6000 chars. Unknown/missing
pages degrade gracefully to an ungrounded (but cautious) answer.

Cost ledger: ``llm_calls`` (migration 058) requires ``user_id`` + ``conversation_id``
NOT NULL, and an anon ask has neither — so per the Phase-4 spec we SKIP the ledger
row and emit a LOUD warning + Logfire event instead (see ``_log_skipped_ledger``).
A dedicated anon-attribution ledger is a follow-up (TODO below).

Every user-facing error message is Arabic. DB helpers are SYNCHRONOUS (service-role
client, invoked via ``run_db``/``asyncio.to_thread``, same convention as
``library_service``); the single LLM call is async and lives in
``generate_anon_answer``.
"""
from __future__ import annotations

import asyncio
import logging
import os
import uuid as _uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from supabase import Client as SupabaseClient

from backend.app.errors import ErrorCode, LunaHTTPException, MSG_SERVICE_UNAVAILABLE
# THE definition of chunk reading order, imported rather than copied so the two
# can never silently diverge (see ``_ground_regulation``). One-way edge:
# library_service pulls in search_service + shared only and never imports
# ask_service, so this cannot cycle.
from backend.app.services.library_service import _ordered_chunk_query
from shared.observability import get_logfire
from shared.seo.judgment_naming import court_level_label

logger = logging.getLogger(__name__)
_logfire = get_logfire()

# ---------------------------------------------------------------------------
# Tunables
# ---------------------------------------------------------------------------
# Leading characters of answer_md an anon client may see (mirrors the schema
# default anon_questions.visible_prefix_chars = 220).
VISIBLE_PREFIX_CHARS = 220
# Hard cap on grounding context fed to the model (plan § Phase 4: "~6000 chars").
MAX_CONTEXT_CHARS = 6000
# First N chunks of a regulation used as grounding (plan: "first ~4 chunks").
REGULATION_CHUNKS = 4
# Wall-clock ceiling on the single flash generation. A wedged provider connection
# must not pin a request worker; 30s is comfortably above p99 flash latency.
LLM_TIMEOUT_S = 30.0
# Output cap for the answer (plan: "Cap output ~600 tokens"). Applied as a real
# provider max_tokens so generation stops naturally (no UsageLimitExceeded raise).
OUTPUT_MAX_TOKENS = 700
# Per-session unclaimed-question window: max 1 unclaimed ask per session per 24h.
SESSION_WINDOW_HOURS = 24
# Fallback global daily cap when ANON_ASK_DAILY_MAX is unset/garbage.
DEFAULT_DAILY_MAX = 200

# tier_2 flash slot reused for the anon-ask generation. NOTE(flag): the Phase-4
# spec asked for a dedicated ``anon_ask`` slot in agents/utils/agent_models.py,
# First-class tier_2 deepseek-flash slot for the anon popup (declared in
# agents/utils/agent_models.py — keeps per-slot cost/telemetry attribution clean).
_MODEL_SLOT = "anon_ask"

# System prompt (Arabic): concise assistant lawyer, answers ONLY from the attached
# context, plain style, always flags the answer as استرشادية (advisory).
_SYSTEM_PROMPT_AR = (
    "أنت محامٍ مساعد في منصة ريحان القانونية السعودية. مهمتك الإجابة بإيجاز ووضوح "
    "على سؤال الزائر اعتماداً على «السياق» المرفق من الصفحة التي يتصفحها فقط، دون "
    "الاستعانة بأي مصدر خارجي.\n"
    "- اكتب بالعربية الفصحى وبأسلوب مباشر ومنظّم.\n"
    "- إذا كان السياق كافياً فأجب منه مباشرةً واذكر رقم المادة أو الفقرة عند وجودها.\n"
    "- إذا لم يكفِ السياق أو لم يتوفر، فقدّم إجابة عامة حذرة ووضّح أنها لا تُغني عن "
    "الرجوع إلى النص النظامي الكامل.\n"
    "- اجعل الإجابة موجزة (بضع فقرات قصيرة على الأكثر).\n"
    "- اختم دائماً بتنبيه قصير أن هذه الإجابة استرشادية ولا تُعدّ استشارة قانونية "
    "نهائية، ويُنصح بمراجعة مختصّ."
)


# ===========================================================================
# Environment readers (fresh per request — a deploy flip needs no restart)
# ===========================================================================


def _envbool(name: str, default: bool = False) -> bool:
    v = os.getenv(name)
    if v is None:
        return default
    return v.strip().lower() in {"1", "true", "yes", "on"}


def anon_ask_enabled() -> bool:
    """Master kill switch. DEFAULT OFF (fail-closed) — deploy flips it on."""
    return _envbool("ANON_ASK_ENABLED", default=False)


def daily_max() -> int:
    """Global per-UTC-day cap on anon_questions rows (cost guard)."""
    raw = os.getenv("ANON_ASK_DAILY_MAX")
    if raw is None or not raw.strip():
        return DEFAULT_DAILY_MAX
    try:
        val = int(raw.strip())
        return val if val > 0 else DEFAULT_DAILY_MAX
    except ValueError:
        return DEFAULT_DAILY_MAX


def turnstile_secret() -> Optional[str]:
    """Cloudflare Turnstile secret, or None when unset (verification skipped)."""
    v = os.getenv("TURNSTILE_SECRET_KEY")
    v = (v or "").strip()
    return v or None


# ===========================================================================
# Small pure helpers
# ===========================================================================


def _looks_like_uuid(value: str) -> bool:
    try:
        _uuid.UUID(str(value))
        return True
    except (ValueError, AttributeError, TypeError):
        return False


def visible_prefix(text: str, n: int = VISIBLE_PREFIX_CHARS) -> str:
    """First ``n`` chars of ``text``, cut at the last whitespace at/before ``n``.

    Never splits a word (falls back to a hard cut only when the first ``n`` chars
    contain no whitespace). Trailing whitespace stripped. Pure, no DB.
    """
    text = text or ""
    n = max(0, int(n))
    if len(text) <= n:
        return text
    cut_at = next((i for i in range(n - 1, -1, -1) if text[i].isspace()), None)
    if cut_at is not None and cut_at > 0:
        return text[:cut_at].rstrip()
    return text[:n].rstrip()


# ===========================================================================
# Abuse controls — DB counts (sync; call via run_db)
# ===========================================================================


def session_unclaimed_count(supabase: SupabaseClient, session_key: str) -> int:
    """Count this session's UNCLAIMED asks in the last ``SESSION_WINDOW_HOURS``.

    Drives the per-session cap (max 1 unclaimed question / session / 24h). Only
    unclaimed rows count — once a visitor signs up and claims, the session is free
    to ask again. Fail-soft: a query error returns 0 (do not lock a user out on a
    DB blip — the global daily cap remains the hard backstop).
    """
    since = (datetime.now(timezone.utc) - timedelta(hours=SESSION_WINDOW_HOURS)).isoformat()
    try:
        res = (
            supabase.table("anon_questions")
            .select("id", count="exact")
            .eq("session_key", session_key)
            .is_("claimed_by_user_id", "null")
            .gte("created_at", since)
            .limit(1)
            .execute()
        )
        return int(res.count or 0)
    except Exception as e:  # noqa: BLE001
        logger.warning("anon_ask: session cap count failed (fail-open): %s", e)
        return 0


def global_today_count(supabase: SupabaseClient) -> int:
    """Count ALL anon_questions rows created since the start of the current UTC day.

    Drives the global daily budget (ANON_ASK_DAILY_MAX). Fail-CLOSED to the cap on
    error (return the cap so the endpoint 503s) — a counting failure must not let
    the daily cost guard silently disappear.
    """
    start = datetime.now(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0
    ).isoformat()
    try:
        res = (
            supabase.table("anon_questions")
            .select("id", count="exact")
            .gte("created_at", start)
            .limit(1)
            .execute()
        )
        return int(res.count or 0)
    except Exception as e:  # noqa: BLE001
        logger.warning("anon_ask: daily cap count failed (fail-closed to cap): %s", e)
        return daily_max()


async def verify_turnstile(token: Optional[str], remote_ip: Optional[str] = None) -> bool:
    """Verify a Cloudflare Turnstile token against siteverify (httpx, 5s).

    Returns True when Turnstile is DISABLED (no secret configured — keys pending).
    When enabled: a missing token is an immediate fail, and any network/parse
    error is treated as a FAILURE (fail-closed) — Turnstile only gates when the
    operator has explicitly set the secret, so failing closed is the safe abuse
    posture. Returns True only on an explicit ``success: true`` from Cloudflare.
    """
    secret = turnstile_secret()
    if not secret:
        return True  # Turnstile disabled — skip (keys pending).
    if not token:
        return False
    try:
        import httpx

        data = {"secret": secret, "response": token}
        if remote_ip:
            data["remoteip"] = remote_ip
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.post(
                "https://challenges.cloudflare.com/turnstile/v0/siteverify",
                data=data,
            )
            payload = resp.json()
        return bool(payload.get("success"))
    except Exception as e:  # noqa: BLE001
        logger.warning("anon_ask: Turnstile verify failed (fail-closed): %s", e)
        return False


# ===========================================================================
# Grounding — fetch the current page's own text (no deep_search)
# ===========================================================================


def _resolve_content_id(
    supabase: SupabaseClient, content_type: str, page_id: str
) -> Optional[str]:
    """Resolve a page_id to a corpus content_id: a raw uuid is used directly,
    otherwise it is treated as a ``seo_item_meta`` slug for ``content_type``."""
    if _looks_like_uuid(page_id):
        return page_id
    try:
        res = (
            supabase.table("seo_item_meta")
            .select("content_id")
            .eq("content_type", content_type)
            .eq("slug", page_id)
            .limit(1)
            .execute()
        )
        rows = res.data or []
        return rows[0].get("content_id") if rows else None
    except Exception as e:  # noqa: BLE001
        logger.warning("anon_ask: content_id resolve failed (%s/%s): %s", content_type, page_id, e)
        return None


def _ground_regulation(supabase: SupabaseClient, page_id: str) -> str:
    """Ground on the first ``REGULATION_CHUNKS`` sections of the DOCUMENT.

    Order comes from ``library_service._ordered_chunk_query`` — the single
    definition of chunk reading order (``corpus DESC, position, chunk_ref``).
    A local ``.order("position")`` is wrong here: ``position`` is scoped per
    STREAM, so the appendix chunks restart at 1 alongside the body and a bare
    position sort interleaves the ملاحق into the operative text. The
    ``.limit()`` below then truncates that jumble, i.e. the model would answer
    about a نظام from text that jumps between the لائحة and its annexes.
    """
    content_id = _resolve_content_id(supabase, "regulation", page_id)
    if not content_id:
        return ""
    try:
        res = (
            _ordered_chunk_query(supabase, str(content_id), "content, position")
            .limit(REGULATION_CHUNKS)
            .execute()
        )
        parts = [(r.get("content") or "").strip() for r in (res.data or [])]
        return "\n\n".join(p for p in parts if p)
    except Exception as e:  # noqa: BLE001
        logger.warning("anon_ask: regulation grounding failed (%s): %s", page_id, e)
        return ""


def _ground_article(supabase: SupabaseClient, page_id: str) -> str:
    """Resolve a مادة's text. page_id may be ``'{reg_slug}/{article_slug}'``
    (what the مادة page sends — the only UNAMBIGUOUS public shape),
    ``'{regulation_id}#{article_no}'`` (the seo_item_meta gate key convention),
    the seo_articles row uuid, or a bare article slug (LAST RESORT — «المادة-80»
    exists in ~1,769 regulations, so a bare slug picks an arbitrary one; kept
    only as a legacy fallback). Falls back to the owning chunk's body when
    article_text is NULL (extraction_status='chunk_fallback')."""
    row: Optional[dict[str, Any]] = None
    try:
        if "/" in page_id:
            # '{reg_slug}/{article_slug}' — resolve the regulation through the
            # sidecar, then the مادة within it. Unambiguous.
            reg_slug, _, art_slug = page_id.partition("/")
            sidecar = (
                supabase.table("seo_item_meta")
                .select("content_id")
                .eq("content_type", "regulation")
                .eq("slug", reg_slug.strip())
                .limit(1)
                .execute()
            )
            sidecar_row = (sidecar.data or [None])[0]
            if sidecar_row and sidecar_row.get("content_id"):
                res = (
                    supabase.table("seo_articles")
                    .select("article_text, chunk_id")
                    .eq("regulation_id", sidecar_row["content_id"])
                    .eq("slug", art_slug.strip())
                    .limit(1)
                    .execute()
                )
                row = (res.data or [None])[0]
        elif "#" in page_id:
            reg_id, _, art = page_id.partition("#")
            if art.strip().isdigit() and _looks_like_uuid(reg_id):
                res = (
                    supabase.table("seo_articles")
                    .select("article_text, chunk_id")
                    .eq("regulation_id", reg_id)
                    .eq("article_no", int(art.strip()))
                    .limit(1)
                    .execute()
                )
                row = (res.data or [None])[0]
        elif _looks_like_uuid(page_id):
            res = (
                supabase.table("seo_articles")
                .select("article_text, chunk_id")
                .eq("id", page_id)
                .limit(1)
                .execute()
            )
            row = (res.data or [None])[0]
        else:
            res = (
                supabase.table("seo_articles")
                .select("article_text, chunk_id")
                .eq("slug", page_id)
                .limit(1)
                .execute()
            )
            row = (res.data or [None])[0]
    except Exception as e:  # noqa: BLE001
        logger.warning("anon_ask: article grounding lookup failed (%s): %s", page_id, e)
        return ""

    if not row:
        return ""
    text = (row.get("article_text") or "").strip()
    if text:
        return text
    chunk_id = row.get("chunk_id")
    if not chunk_id:
        return ""
    try:
        cres = (
            supabase.table("chunks_v2")
            .select("content")
            .eq("id", chunk_id)
            .limit(1)
            .execute()
        )
        crows = cres.data or []
        return (crows[0].get("content") or "").strip() if crows else ""
    except Exception as e:  # noqa: BLE001
        logger.warning("anon_ask: article chunk fallback failed (%s): %s", page_id, e)
        return ""


def _ground_judgment(supabase: SupabaseClient, page_id: str) -> str:
    """Grounding context for a /judgments/{slug} page — the ruling's own text.

    Composed from the narrative columns in the order a reader meets them, each
    under its Arabic heading so the model can attribute a statement to the right
    part of the ruling («ما الذي قضت به المحكمة؟» must answer from المنطوق, not
    from the plaintiff's own أسانيد).

    Grounding deliberately includes the sections the PAGE gates (الأسباب
    والتسبيب above all). That matches the policy every other grounded type
    already follows — ``_ground_article`` grounds on the full article text
    regardless of the page's gate — and it does not leak the gate: grounding is
    server-side only, and an anon reader is shown just the ``visible_prefix_chars``
    teaser of the ANSWER, never the source bytes.
    """
    content_id = _resolve_content_id(supabase, "judgment", page_id)
    if not content_id:
        return ""
    try:
        res = (
            supabase.table("cases")
            .select(
                "court, court_level, city, case_number, date_hijri, "
                "short_summary, facts, claims, reasoning, ruling, "
                "appeal_reasoning, appeal_ruling"
            )
            .eq("id", content_id)
            .limit(1)
            .execute()
        )
        rows = res.data or []
    except Exception as e:  # noqa: BLE001
        logger.warning("anon_ask: judgment grounding failed (%s): %s", page_id, e)
        return ""
    if not rows:
        return ""
    case = rows[0]

    header_bits = [
        (case.get("court") or "").strip(),
        court_level_label(case.get("court_level")) or "",
        (case.get("city") or "").strip(),
        (case.get("date_hijri") or "").strip(),
    ]
    header = " — ".join(bit for bit in header_bits if bit)

    parts: list[str] = []
    if header:
        parts.append(f"الحكم: {header}")
    number = (case.get("case_number") or "").strip()
    if number:
        parts.append(f"رقم القضية: {number}")

    for key, label in (
        ("short_summary", "الملخص"),
        ("facts", "الوقائع"),
        ("claims", "الطلبات"),
        ("reasoning", "الأسباب والتسبيب"),
        ("ruling", "المنطوق"),
        ("appeal_reasoning", "تسبيب حكم الاستئناف"),
        ("appeal_ruling", "منطوق حكم الاستئناف"),
    ):
        value = case.get(key)
        text = value.strip() if isinstance(value, str) else ""
        if text:
            parts.append(f"{label}:\n{text}")
    return "\n\n".join(parts)


def _ground_blog(supabase: SupabaseClient, page_id: str) -> str:
    try:
        res = (
            supabase.table("blog_posts")
            .select("content_md")
            .eq("token", page_id)
            .is_("deleted_at", "null")
            .limit(1)
            .execute()
        )
        rows = res.data or []
        return (rows[0].get("content_md") or "").strip() if rows else ""
    except Exception as e:  # noqa: BLE001
        logger.warning("anon_ask: blog grounding failed (%s): %s", page_id, e)
        return ""


def fetch_grounding(
    supabase: SupabaseClient, page_type: str, page_id: str
) -> str:
    """Return the current page's own text as grounding context (≤ MAX_CONTEXT_CHARS).

    Page-context ONLY — no deep_search, no retrieval. Handles the five grounded
    page types (regulation / article / service / judgment / blog); any other type
    or a missing page yields ``""`` (the model then answers cautiously without
    grounding). Sync — call via ``run_db``.
    """
    page_type = (page_type or "").strip().lower()
    page_id = (page_id or "").strip()
    if not page_id:
        return ""
    if page_type == "regulation":
        text = _ground_regulation(supabase, page_id)
    elif page_type == "article":
        text = _ground_article(supabase, page_id)
    elif page_type == "judgment":
        text = _ground_judgment(supabase, page_id)
    elif page_type == "blog":
        text = _ground_blog(supabase, page_id)
    else:
        text = ""
    return (text or "")[:MAX_CONTEXT_CHARS]


# ===========================================================================
# Storage (sync; call via run_db)
# ===========================================================================


def insert_anon_question(
    supabase: SupabaseClient,
    *,
    session_key: str,
    page_type: str,
    page_id: str,
    question: str,
    answer_md: str,
    model: Optional[str],
) -> Optional[str]:
    """Insert the full answer into ``anon_questions``; return the new row id.

    ``answer_md`` is the COMPLETE answer (trust boundary: only the visible prefix
    is ever returned to an anon client). Returns None if the insert yields no row.
    """
    try:
        res = (
            supabase.table("anon_questions")
            .insert(
                {
                    "session_key": session_key,
                    "page_type": page_type,
                    "page_id": page_id,
                    "question": question,
                    "answer_md": answer_md,
                    "visible_prefix_chars": VISIBLE_PREFIX_CHARS,
                    "model": model,
                }
            )
            .execute()
        )
        rows = res.data or []
        return str(rows[0].get("id")) if rows else None
    except Exception as e:  # noqa: BLE001
        logger.exception("anon_ask: insert anon_question failed: %s", e)
        raise LunaHTTPException(
            status_code=503,
            code=ErrorCode.SERVICE_UNAVAILABLE,
            detail=MSG_SERVICE_UNAVAILABLE,
        )


def get_teaser(
    supabase: SupabaseClient, question_id: str, session_key: str
) -> Optional[dict[str, Any]]:
    """Re-fetch one's own teaser (visible prefix) after a refresh.

    Matches on id AND session_key. Returns ``{question, visible_prefix,
    is_truncated, claimed}`` or ``None`` (route → 404) when the row is missing or
    the session_key doesn't match. Sync — call via ``run_db``.
    """
    try:
        res = (
            supabase.table("anon_questions")
            .select("session_key, question, answer_md, visible_prefix_chars, claimed_by_user_id")
            .eq("id", question_id)
            .limit(1)
            .execute()
        )
        rows = res.data or []
    except Exception as e:  # noqa: BLE001
        logger.warning("anon_ask: teaser fetch failed (%s): %s", question_id, e)
        return None
    if not rows:
        return None
    row = rows[0]
    if row.get("session_key") != session_key:
        return None
    answer = row.get("answer_md") or ""
    prefix = visible_prefix(answer, row.get("visible_prefix_chars") or VISIBLE_PREFIX_CHARS)
    return {
        "question": row.get("question") or "",
        "visible_prefix": prefix,
        "is_truncated": len(prefix) < len(answer),
        "claimed": bool(row.get("claimed_by_user_id")),
    }


def claim_answer(
    supabase: SupabaseClient,
    *,
    question_id: str,
    session_key: str,
    user_id: str,
) -> dict[str, Any]:
    """Claim the full answer for an authed user (the "continuity moment").

    The row must match BOTH id and session_key. Outcomes:
      - unclaimed          → set claimed_by_user_id + claimed_at, return full answer;
      - claimed by SAME user → return it again (idempotent — double-click / re-login);
      - claimed by ANOTHER  → 403 «هذه الإجابة مرتبطة بحساب آخر»;
      - wrong session_key / missing → 404 «الإجابة غير موجودة».

    Sync — call via ``run_db``. Raises LunaHTTPException (Arabic) for 403/404.
    """
    def _payload(row: dict[str, Any]) -> dict[str, Any]:
        return {
            "question": row.get("question") or "",
            "answer_md": row.get("answer_md") or "",
            "page_type": row.get("page_type") or "",
            "page_id": row.get("page_id") or "",
        }

    def _fetch() -> Optional[dict[str, Any]]:
        res = (
            supabase.table("anon_questions")
            .select("session_key, question, answer_md, page_type, page_id, claimed_by_user_id")
            .eq("id", question_id)
            .limit(1)
            .execute()
        )
        rows = res.data or []
        return rows[0] if rows else None

    not_found = LunaHTTPException(
        status_code=404, code=ErrorCode.ARTIFACT_NOT_FOUND, detail="الإجابة غير موجودة"
    )
    claimed_by_other = LunaHTTPException(
        status_code=403, code=ErrorCode.FORBIDDEN, detail="هذه الإجابة مرتبطة بحساب آخر"
    )

    try:
        row = _fetch()
    except Exception as e:  # noqa: BLE001
        logger.exception("anon_ask: claim fetch failed (%s): %s", question_id, e)
        raise LunaHTTPException(
            status_code=503, code=ErrorCode.SERVICE_UNAVAILABLE, detail=MSG_SERVICE_UNAVAILABLE
        )

    if not row or row.get("session_key") != session_key:
        raise not_found

    claimed = row.get("claimed_by_user_id")
    if claimed:
        if str(claimed) == str(user_id):
            return _payload(row)  # idempotent same-user re-claim
        raise claimed_by_other

    # Unclaimed — attempt a guarded update (only if still NULL, to win any race).
    now = datetime.now(timezone.utc).isoformat()
    try:
        upd = (
            supabase.table("anon_questions")
            .update({"claimed_by_user_id": user_id, "claimed_at": now})
            .eq("id", question_id)
            .is_("claimed_by_user_id", "null")
            .execute()
        )
    except Exception as e:  # noqa: BLE001
        logger.exception("anon_ask: claim update failed (%s): %s", question_id, e)
        raise LunaHTTPException(
            status_code=503, code=ErrorCode.SERVICE_UNAVAILABLE, detail=MSG_SERVICE_UNAVAILABLE
        )

    if upd.data:
        return _payload(upd.data[0])

    # Lost the race (someone claimed between fetch and update) — re-check owner.
    row2 = _fetch()
    if row2 and str(row2.get("claimed_by_user_id") or "") == str(user_id):
        return _payload(row2)  # the racer was us (idempotent)
    raise claimed_by_other


# ===========================================================================
# LLM generation (async)
# ===========================================================================


def _build_user_message(question: str, context: str, page_type: str) -> str:
    if context:
        return (
            f"السياق (من صفحة من نوع «{page_type}»):\n"
            f"\"\"\"\n{context}\n\"\"\"\n\n"
            f"سؤال الزائر:\n{question}"
        )
    return (
        "لا يتوفر سياق من الصفحة الحالية. أجب إجابة عامة حذرة ووضّح أنها لا تُغني "
        "عن الرجوع إلى النص النظامي الكامل.\n\n"
        f"سؤال الزائر:\n{question}"
    )


def _model_label(result: Any) -> str:
    """Best-effort label of the model that actually responded (FallbackModel may
    swap off the primary). Falls back to the slot intent label."""
    try:
        model = None
        for m in result.all_messages() or []:
            mn = getattr(m, "model_name", None)
            if mn:
                model = mn
        if model:
            return str(model)
    except Exception:
        pass
    return f"{_MODEL_SLOT}:tier_2"


def _log_skipped_ledger(
    *, tokens_in: int, tokens_out: int, tokens_reasoning: int, model: str
) -> None:
    """LOUD record that the llm_calls ledger row was intentionally SKIPPED.

    ``llm_calls`` (058) requires ``user_id`` + ``conversation_id`` NOT NULL, and an
    anon ask has neither, so no ledger row can be written. Emit a warning + a
    Logfire event carrying the token counts + est. cost so anon spend is still
    observable until a dedicated anon-attribution ledger ships.

    TODO(seo-library phase 4 follow-up): add a nullable-user anon-spend ledger (or
    relax llm_calls to allow anon rows) and write the row here instead of logging.
    """
    est_cost = 0.0
    try:
        from agents.utils.agent_models import cost_usd

        est_cost = round(cost_usd(model, tokens_in, tokens_out, tokens_reasoning, 0), 6)
    except Exception:  # noqa: BLE001
        pass
    logger.warning(
        "anon_ask: llm_calls ledger row SKIPPED (llm_calls.user_id/conversation_id "
        "are NOT NULL and an anon ask has neither). TODO: dedicated anon-spend "
        "ledger. model=%s tokens_in=%s tokens_out=%s tokens_reasoning=%s est_cost_usd=%s",
        model, tokens_in, tokens_out, tokens_reasoning, est_cost,
    )
    try:
        _logfire.info(
            "anon_ask.ledger_skipped",
            reason="llm_calls.user_id/conversation_id NOT NULL — anon has neither",
            model=model,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            tokens_reasoning=tokens_reasoning,
            est_cost_usd=est_cost,
        )
    except Exception:  # noqa: BLE001
        pass


async def generate_anon_answer(
    supabase: SupabaseClient,
    *,
    question: str,
    page_type: str,
    page_id: str,
    session_key: str,
) -> dict[str, Any]:
    """Ground → generate (one tier_2 flash call) → store → return the teaser.

    Returns ``{question_id, visible_prefix, is_truncated, total_chars}``. The FULL
    answer is stored in ``anon_questions.answer_md`` and NEVER returned here. Raises
    a 503 (Arabic) on generation failure/timeout so the anon client sees a clean
    transient error rather than a 500.
    """
    from shared.db.run import run_db

    # 1. Grounding (page context only) — off the event loop.
    context = await run_db(fetch_grounding, supabase, page_type, page_id)
    grounded = bool(context)

    # 2. One tier_2 flash generation. Lazy imports keep app import light + avoid
    #    an agents<->backend import cycle at module load (same as templates_service).
    from agents.utils.agent_models import get_agent_model
    from agents.utils.tracking import track_stage
    from pydantic_ai import Agent
    from pydantic_ai.usage import UsageLimits

    user_msg = _build_user_message(question, context, page_type)

    with track_stage(
        "public.anon_ask",
        agent_family="public_library",
        subtype="anon_ask",
        page_type=page_type,
        grounded=grounded,
        context_chars=len(context),
    ) as span:
        try:
            agent = Agent(
                get_agent_model(_MODEL_SLOT),
                name="anon_ask",
                instructions=_SYSTEM_PROMPT_AR,
                retries=1,
            )
            result = await asyncio.wait_for(
                agent.run(
                    user_msg,
                    usage_limits=UsageLimits(request_limit=2),
                    model_settings={"max_tokens": OUTPUT_MAX_TOKENS, "temperature": 0.3},
                ),
                timeout=LLM_TIMEOUT_S,
            )
        except asyncio.TimeoutError:
            logger.warning("anon_ask: LLM call timed out after %.0fs", LLM_TIMEOUT_S)
            try:
                span.set_outcome("llm_timeout")
            except Exception:  # noqa: BLE001
                pass
            raise LunaHTTPException(
                status_code=503, code=ErrorCode.SERVICE_UNAVAILABLE, detail=MSG_SERVICE_UNAVAILABLE
            )
        except LunaHTTPException:
            raise
        except Exception as e:  # noqa: BLE001
            logger.warning("anon_ask: LLM call failed: %s", e)
            try:
                span.set_outcome("llm_failed")
            except Exception:  # noqa: BLE001
                pass
            raise LunaHTTPException(
                status_code=503, code=ErrorCode.SERVICE_UNAVAILABLE, detail=MSG_SERVICE_UNAVAILABLE
            )

        answer = (result.output or "").strip() if isinstance(result.output, str) else ""
        if not answer:
            logger.warning("anon_ask: LLM returned empty answer")
            try:
                span.set_outcome("empty")
            except Exception:  # noqa: BLE001
                pass
            raise LunaHTTPException(
                status_code=503, code=ErrorCode.SERVICE_UNAVAILABLE, detail=MSG_SERVICE_UNAVAILABLE
            )

        model = _model_label(result)

        # Usage → span attributes + the LOUD skipped-ledger record.
        tokens_in = tokens_out = tokens_reasoning = 0
        try:
            usage = result.usage()
            tokens_in = int(getattr(usage, "input_tokens", 0) or 0)
            tokens_out = int(getattr(usage, "output_tokens", 0) or 0)
            details = getattr(usage, "details", None) or {}
            tokens_reasoning = int(details.get("reasoning_tokens", 0) or 0)
        except Exception:  # noqa: BLE001
            pass
        try:
            span.set(
                tokens_in=tokens_in,
                tokens_out=tokens_out,
                tokens_reasoning=tokens_reasoning,
                model_used=model,
                answer_chars=len(answer),
            )
        except Exception:  # noqa: BLE001
            pass
        _log_skipped_ledger(
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            tokens_reasoning=tokens_reasoning,
            model=model,
        )

    # 3. Store the FULL answer (only the prefix is ever returned to anon).
    question_id = await run_db(
        insert_anon_question,
        supabase,
        session_key=session_key,
        page_type=page_type,
        page_id=page_id,
        question=question,
        answer_md=answer,
        model=model,
    )
    if not question_id:
        raise LunaHTTPException(
            status_code=503, code=ErrorCode.SERVICE_UNAVAILABLE, detail=MSG_SERVICE_UNAVAILABLE
        )

    prefix = visible_prefix(answer, VISIBLE_PREFIX_CHARS)
    return {
        "question_id": question_id,
        "visible_prefix": prefix,
        "is_truncated": len(prefix) < len(answer),
        "total_chars": len(answer),
    }


__all__ = [
    "VISIBLE_PREFIX_CHARS",
    "anon_ask_enabled",
    "daily_max",
    "turnstile_secret",
    "visible_prefix",
    "session_unclaimed_count",
    "global_today_count",
    "verify_turnstile",
    "fetch_grounding",
    "insert_anon_question",
    "get_teaser",
    "claim_answer",
    "generate_anon_answer",
]
