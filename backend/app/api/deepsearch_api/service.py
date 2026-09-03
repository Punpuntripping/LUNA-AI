"""Job service for the Blog-Post Generation API.

Owns the async job lifecycle backed by ``public.blog_post_jobs`` (migration
086) + an in-process, Semaphore-gated asyncio worker:

    create_or_get_job → (idempotency dedup) → INSERT queued → spawn process_job
    process_job       → processing → generate headless → snapshot → completed
    get_job           → read one job row
    catchup_stuck_jobs→ boot sweep: re-queue / fail jobs stuck queued|processing

Single-worker invariant (``main.py`` refuses WEB_CONCURRENCY>1): the in-process
task + module-level keepalive set is correct here. If the backend is ever
scaled past one worker, job pickup MUST move to a DB-claim
(``UPDATE ... WHERE status='queued' RETURNING``) or two workers double-process
(plan §15).

``process_job`` runs detached from any request, so it builds its OWN
service-role client via ``get_supabase_client()`` (the same singleton
``app.state.supabase`` holds) — never a user-scoped client. ``public_blogs``
writes are service-role only (migration 153 grants no write policy at all).

⚠ **Since blog_subjects.md D16 the job writes v1 DIRECT into ``public_blogs``
and does NOT create a ``blog_posts`` row.** ``blog_posts`` stays exactly what it
was — the frozen share-link snapshot behind the 99 legacy tokens and every
in-app «مشاركة» — and this module no longer touches it (``blog_service`` is
imported for ``make_snippet`` only).

⚠ **ONE JOB PUBLISHES AT MOST ONE BLOG, and the database is what enforces it.**
Found by the first live end-to-end run (2026-09-02): a single POST produced TWO
published ``public_blogs`` rows 60 seconds apart, with different slugs.
``create_or_get_job`` had deduped correctly — only one job ever existed. The
duplicate came from the publish step, whose only guard was
``assert_slug_available``, and **the slug is derived from the aggregator's
headline, which is non-deterministic**: a re-drive regenerates a different
headline, takes a different slug, passes the uniqueness check, and publishes
again. Three layers now, cheapest first:

1. ``_existing_publication`` — a re-drive that finds its own blog returns it
   without regenerating, so a retry costs one indexed read, not a pipeline run.
2. ``idx_public_blogs_job`` (migration 156) — the authoritative guard, and the
   only one that closes the window between the ``public_blogs`` INSERT and the
   job row learning about it.
3. ``JobAlreadyPublishedError`` is handled as **success**: the article exists,
   which is the outcome the caller asked for.

⚠ **The editorial request fields live in ``metadata`` under ``_editorial``.**
``blog_post_jobs`` (migration 086) has no columns for ``type`` / ``subjects`` /
``slug`` / ``mode`` / ``support`` / ``publish_public`` / ``editorial_voice``,
and adding them is a migration this step does not own. Storing them on the job
row (rather than only in the worker's memory) is load-bearing: ``process_job``
re-reads the row, so a crash + ``catchup_stuck_jobs`` re-drive publishes with
the SAME plan and the SAME slug rather than silently reverting to defaults.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Optional

import httpx
from supabase import Client as SupabaseClient

from backend.app.api.deepsearch_api.generate import generate_answer_headless
from backend.app.api.deepsearch_api.models import (
    BlogJobConfidence,
    BlogJobReferenceItem,
    BlogJobReferences,
    BlogJobResult,
    BlogPostJobRequest,
)
from backend.app.services import blog_service, public_blog_service
from backend.app.services.references_service import fetch_item_references_payload
from shared.config import get_settings
from shared.seo.judgment_naming import slugify_ar
from shared.db.client import get_supabase_client
from shared.db.run import run_db
from shared.observability import get_logfire

logger = logging.getLogger(__name__)
_logfire = get_logfire()

_TABLE = "blog_post_jobs"

# Confidence label ordering + optional derived score (plan §11).
_CONF_RANK: dict[str, int] = {"low": 0, "medium": 1, "high": 2}
_CONF_SCORE: dict[str, float] = {"high": 0.85, "medium": 0.6, "low": 0.3}
_DEFAULT_CONFIDENCE = "medium"

# Max processing attempts before the boot catch-up sweep gives up and fails a
# perennially-stuck job (crash-loop guard).
_MAX_JOB_ATTEMPTS = 3

# ---------------------------------------------------------------------------
# In-process worker plumbing
# ---------------------------------------------------------------------------

# Keepalive set — holds spawned job tasks so they are not GC'd mid-run (mirrors
# message_service._inflight_pipelines). add_done_callback discards on completion.
_JOB_TASKS: set[asyncio.Task] = set()

# Lazily-created Semaphore gating concurrent GENERATION (not submission). Built
# on first use so it binds to the running loop + reads current settings.
_semaphore: Optional[asyncio.Semaphore] = None


def _get_semaphore() -> asyncio.Semaphore:
    global _semaphore
    if _semaphore is None:
        limit = max(1, int(get_settings().EDITORIAL_MAX_CONCURRENT_JOBS))
        _semaphore = asyncio.Semaphore(limit)
    return _semaphore


def _spawn_process_job(job_id: str) -> None:
    """Spawn the detached process_job task and keep it alive past the caller."""
    task = asyncio.create_task(process_job(job_id))
    _JOB_TASKS.add(task)
    task.add_done_callback(_JOB_TASKS.discard)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Pure helpers (unit-testable — no I/O)
# ---------------------------------------------------------------------------


def derive_confidence(
    wi_metadata: Optional[dict],
    references: list,
    *,
    have_workspace_item: bool,
) -> BlogJobConfidence:
    """Map the WI's ``metadata.confidence`` + reference relevance to a label.

    * With a workspace item: label = ``metadata.confidence`` (default 'medium'
      when absent/invalid); reasons cite the used-reference counts.
    * With NO workspace item (chat-only route, plan §10): forced 'low'.
    """
    if not have_workspace_item:
        return BlogJobConfidence(
            label="low",
            score=_CONF_SCORE["low"],
            reasons=["لم يُنتج تحليل قانوني موثّق لهذا السؤال"],
        )

    label = (wi_metadata or {}).get("confidence") or _DEFAULT_CONFIDENCE
    if label not in _CONF_RANK:
        label = _DEFAULT_CONFIDENCE

    total = len(references)
    high = sum(1 for r in references if getattr(r, "relevance", None) == "high")
    reasons: list[str] = []
    if total:
        reasons.append(f"{total} مرجع مستشهد به")
        if high:
            reasons.append(f"{high} مرجع عالي الصلة")
    else:
        reasons.append("لا توجد مراجع مستشهد بها")

    return BlogJobConfidence(
        label=label, score=_CONF_SCORE.get(label), reasons=reasons
    )


def decide_publish(publish_policy: str, min_confidence: str, label: str) -> bool:
    """Resolve ``is_published`` from the policy + confidence (plan §11).

    * ``always`` → True, ``never`` → False.
    * ``auto``   → label rank >= min_confidence rank.
    """
    policy = (publish_policy or "auto").lower()
    if policy == "always":
        return True
    if policy == "never":
        return False
    # auto
    threshold = _CONF_RANK.get((min_confidence or "high").lower(), _CONF_RANK["high"])
    return _CONF_RANK.get(label, 0) >= threshold


# Reserved key under ``blog_post_jobs.metadata`` holding the public_blogs half
# of the request. Namespaced with a leading underscore so it cannot be confused
# with marketing's own provenance keys; a caller-supplied ``_editorial`` is
# overwritten. See the module docstring for why this is not a set of columns.
EDITORIAL_META_KEY = "_editorial"


def _opt_bool(value: Any) -> Optional[bool]:
    """Tri-state bool: ``None`` stays ``None``, everything else coerces.

    ⚠ **Never write ``bool(cfg.get("support", False))``.** ``None`` is a VALUE
    on this path — "the planner decides" — and collapsing it into ``False``
    turns every partially-pinned job into a pinned ``support=false`` one with no
    error and no log line (blog_subjects.md §5 + §11).
    """
    if value is None:
        return None
    return bool(value)


def editorial_config(req: BlogPostJobRequest) -> dict[str, Any]:
    """The public_blogs half of a submit body, as stored on the job row."""
    return {
        "type": req.type,
        "subjects": list(req.subjects or []),
        "slug": (req.slug or None),
        "publish_public": bool(req.publish_public),
        "editorial_voice": bool(req.editorial_voice),
        "mode": req.mode,
        "support": req.support,      # ⚠ tri-state — see _opt_bool
    }


def read_editorial_config(job: dict) -> dict[str, Any]:
    """Read the editorial config back off a job row. Never raises.

    A job submitted before this key existed (or with a mangled metadata blob)
    reads back as unpinned, UNLISTED, editorial-voice — never a surprise publish.

    ⚠ **``publish_public`` is asymmetric on purpose, and the asymmetry is the
    point.** The REQUEST defaults it to ``True`` (D17 — writing a row into
    ``public_blogs`` is creating a public blog). This read-back defaults it to
    ``False``, because a row with no ``_editorial`` block never expressed an
    intent to honour: it is legacy or corrupt, and the conservative read is the
    one that cannot publish something nobody asked to publish. A real job never
    reaches this branch — ``_insert_job`` always writes the block, with the
    value the caller actually got.
    """
    raw = (job.get("metadata") or {}).get(EDITORIAL_META_KEY)
    if not isinstance(raw, dict):
        raw = {}
    subjects = raw.get("subjects")
    return {
        "type": raw.get("type"),
        "subjects": [str(x) for x in subjects] if isinstance(subjects, list) else [],
        "slug": raw.get("slug") or None,
        "publish_public": bool(raw.get("publish_public", False)),
        "editorial_voice": bool(raw.get("editorial_voice", True)),
        "mode": raw.get("mode") or None,
        "support": _opt_bool(raw.get("support")),
    }


class _StoredRef:
    """A frozen ``references_json`` entry, re-wrapped as a Reference-like object.

    ``derive_confidence`` and ``_references_summary`` both read ``.n`` /
    ``.title`` / ``.relevance`` off Reference objects. On the re-drive path the
    references come back as plain dicts off the row, so wrapping them reuses
    those two functions verbatim instead of growing a second, drifting copy of
    the same mapping.
    """

    __slots__ = ("n", "title", "relevance")

    def __init__(self, raw: dict) -> None:
        self.n = int(raw.get("n", 0) or 0)
        self.title = str(raw.get("title", "") or "")
        self.relevance = raw.get("relevance")


def _result_from_row(row: dict, job: dict) -> BlogJobResult:
    """Rebuild the completed-job payload from an already-published row.

    Used by the re-drive paths, which must return **exactly what the first
    attempt returned** — above all the same ``post_id``, since marketing may
    already hold it. Nothing here regenerates anything.

    ``have_workspace_item`` is recovered from ``source_item_id`` rather than
    assumed: a chat-only route stored ``NULL`` there and was forced to ``low``,
    and reconstructing it as if it had an artifact would invent a confidence
    rationale the original run never gave.
    """
    references = [_StoredRef(r) for r in (row.get("references_json") or []) if isinstance(r, dict)]
    source_item_id = row.get("source_item_id")
    confidence = derive_confidence(
        {"confidence": row.get("confidence")},
        references,
        have_workspace_item=bool(source_item_id),
    )
    content_md = row.get("content_md") or ""
    slug = row.get("slug") or ""
    return BlogJobResult(
        post_id=row.get("blog_id"),
        token=None,
        root_id=row.get("root_id"),
        slug=slug,
        is_public=bool(row.get("is_public")),
        url=f"{get_settings().PUBLIC_WEB_URL}/blog/{slug}",
        is_published=bool(row.get("is_published")),
        confidence=confidence,
        title=row.get("title"),
        question_text=row.get("question_text") or job.get("question") or "",
        summary=blog_service.make_snippet(content_md),
        content_md=content_md,
        references=_references_summary(references),
        workspace_item_id=source_item_id or job.get("workspace_item_id"),
        created_at=row.get("created_at") or _now_iso(),
    )


async def _existing_publication(
    supabase: SupabaseClient, job: dict
) -> Optional[dict]:
    """The blog this job already published, if any. One indexed read.

    ⚠ **Keyed on ``public_blogs.job_id``, not on ``blog_post_jobs.post_id``.**
    ``post_id`` is written by the same UPDATE that marks the job ``completed``,
    so in the exact crash window this guards — row written, job not yet
    completed — the job row does not know its own ``post_id`` and a check keyed
    on it would sail straight past and regenerate. ``job_id`` is written by the
    INSERT itself, so it is true the instant the article exists.

    Never raises: a lookup failure falls through to the normal path, where
    ``idx_public_blogs_job`` is still there to refuse the duplicate.
    """
    job_id = str(job.get("job_id") or "")
    if not job_id:
        return None
    try:
        return await run_db(
            public_blog_service.get_by_job_id, supabase, job_id
        )
    except Exception:  # noqa: BLE001
        logger.warning("re-drive lookup failed for job %s", job_id, exc_info=True)
        return None


def _references_summary(references: list) -> BlogJobReferences:
    """Build the ``references.count/top`` preview from Reference objects."""
    top: list[BlogJobReferenceItem] = []
    for r in references[:5]:
        top.append(
            BlogJobReferenceItem(
                n=int(getattr(r, "n", 0) or 0),
                title=str(getattr(r, "title", "") or ""),
                relevance=getattr(r, "relevance", None),
            )
        )
    return BlogJobReferences(count=len(references), top=top)


# ---------------------------------------------------------------------------
# Sync DB helpers (run under run_db / asyncio.to_thread)
# ---------------------------------------------------------------------------


def _select_job(supabase: SupabaseClient, job_id: str) -> Optional[dict]:
    res = (
        supabase.table(_TABLE)
        .select("*")
        .eq("job_id", job_id)
        .maybe_single()
        .execute()
    )
    return res.data if res is not None else None


def _select_job_by_key(supabase: SupabaseClient, idempotency_key: str) -> Optional[dict]:
    res = (
        supabase.table(_TABLE)
        .select("*")
        .eq("idempotency_key", idempotency_key)
        .maybe_single()
        .execute()
    )
    return res.data if res is not None else None


def _insert_job(supabase: SupabaseClient, req: BlogPostJobRequest) -> dict:
    # The editorial config rides INSIDE metadata (see the module docstring), so
    # a crash + catch-up re-drive replays the same plan, the same slug and the
    # same subjects rather than quietly falling back to defaults.
    metadata = dict(req.metadata or {})
    metadata[EDITORIAL_META_KEY] = editorial_config(req)
    payload = {
        "idempotency_key": req.idempotency_key,
        "status": "queued",
        "question": req.question,
        "title": req.title,
        "display_mode": req.display_mode,
        "subtype": req.subtype,
        "language": req.language,
        "publish_policy": req.publish_policy,
        "min_confidence": req.min_confidence,
        "metadata": metadata,
        "callback_url": req.callback_url,
    }
    res = supabase.table(_TABLE).insert(payload).execute()
    if not res.data:
        raise RuntimeError("blog_post_jobs insert returned no row")
    return res.data[0]


def _update_job(supabase: SupabaseClient, job_id: str, patch: dict) -> None:
    supabase.table(_TABLE).update(patch).eq("job_id", job_id).execute()


def _mark_processing(supabase: SupabaseClient, job_id: str) -> dict:
    """Atomically-ish bump to processing + increment attempts; return the row."""
    row = _select_job(supabase, job_id)
    attempts = int((row or {}).get("attempts", 0) or 0) + 1
    supabase.table(_TABLE).update(
        {"status": "processing", "attempts": attempts}
    ).eq("job_id", job_id).execute()
    return row or {}


def _select_stuck_jobs(supabase: SupabaseClient) -> list[dict]:
    res = (
        supabase.table(_TABLE)
        .select("job_id, status, attempts")
        .in_("status", ["queued", "processing"])
        .execute()
    )
    return res.data or []


def _load_workspace_item(supabase: SupabaseClient, item_id: str) -> Optional[dict]:
    res = (
        supabase.table("workspace_items")
        .select("item_id, content_md, title, metadata, kind")
        .eq("item_id", item_id)
        .maybe_single()
        .execute()
    )
    return res.data if res is not None else None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def get_job_by_idempotency_key(
    supabase: SupabaseClient, idempotency_key: str
) -> Optional[dict]:
    """Idempotency lookup used by the router BEFORE the rate limiter (free retry)."""
    return await run_db(_select_job_by_key, supabase, idempotency_key)


async def get_job(supabase: SupabaseClient, job_id: str) -> Optional[dict]:
    """Read one job row (poll path). ``None`` when it doesn't exist."""
    return await run_db(_select_job, supabase, job_id)


async def create_or_get_job(
    supabase: SupabaseClient, req: BlogPostJobRequest
) -> tuple[dict, bool]:
    """Insert a queued job (and spawn its worker) or return the existing one.

    Race-safe: the ``idempotency_key`` UNIQUE index means two concurrent
    identical submissions can't both insert — the loser catches the unique
    violation and re-reads the winner's row. Returns ``(job_row, is_new)``;
    the worker is spawned only for a genuinely-new row.
    """
    existing = await run_db(_select_job_by_key, supabase, req.idempotency_key)
    if existing is not None:
        return existing, False

    try:
        job = await run_db(_insert_job, supabase, req)
    except Exception as e:  # noqa: BLE001 — likely the unique-key race
        logger.info("create_or_get_job insert raced/failed, re-reading: %s", e)
        existing = await run_db(_select_job_by_key, supabase, req.idempotency_key)
        if existing is not None:
            return existing, False
        raise

    _spawn_process_job(job["job_id"])
    return job, True


async def wait_for_job(
    supabase: SupabaseClient, job_id: str, timeout_s: float
) -> dict:
    """Poll for a terminal status up to ``timeout_s`` (the ``?wait=N`` path).

    Returns the latest job row — terminal (completed/failed) if it finished in
    time, otherwise the still-in-flight row.
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + max(0.0, timeout_s)
    row = await get_job(supabase, job_id) or {}
    while row.get("status") not in ("completed", "failed"):
        if loop.time() >= deadline:
            break
        await asyncio.sleep(0.75)
        row = await get_job(supabase, job_id) or row
    return row


async def process_job(job_id: str) -> None:
    """Drive one job: generate → snapshot → complete (or fail).

    Runs detached from the request; builds its own service-role client. Never
    raises out — a failure is recorded on the job row as ``error``.
    """
    supabase = get_supabase_client()
    settings = get_settings()

    with _logfire.span("editorial.process_job", job_id=job_id) as _span:
        try:
            job = await run_db(_mark_processing, supabase, job_id)
        except Exception:  # noqa: BLE001
            logger.exception("process_job: failed to mark processing job_id=%s", job_id)
            return

        # ── Re-drive fast path ─────────────────────────────────────────────
        # A restart across an in-flight job re-spawns this worker. If the
        # previous attempt already published, regenerating would cost a full
        # pipeline run AND (before migration 156) mint a second article under a
        # different LLM headline. Return the existing blog instead.
        #
        # Ahead of the bot-user gate on purpose: a job that already published
        # should complete even if EDITORIAL_BOT_USER_ID has since been unset —
        # there is nothing left to generate, so the config it would need is
        # config it no longer needs.
        published = await _existing_publication(supabase, job)
        if published is not None:
            result = _result_from_row(published, job)
            logger.info(
                "process_job: job_id=%s already published root_id=%s — "
                "returning it without regenerating",
                job_id, result.root_id,
            )
            await _complete_job(supabase, job, result)
            try:
                _span.set_attribute("redrive_short_circuit", True)
                _span.set_attribute("root_id", result.root_id or "")
            except Exception:  # noqa: BLE001
                pass
            return

        bot_user_id = (settings.EDITORIAL_BOT_USER_ID or "").strip()
        if not bot_user_id:
            # Config gap — not retryable until an operator sets the bot user id.
            await _fail_job(
                supabase,
                job_id,
                code="configuration_error",
                message="لم يُهيأ مستخدم بوت التحرير (EDITORIAL_BOT_USER_ID غير مضبوط)",
                retryable=False,
            )
            return

        cfg = read_editorial_config(job)
        try:
            async with _get_semaphore():
                gen = await generate_answer_headless(
                    supabase,
                    bot_user_id=bot_user_id,
                    question=job["question"],
                    metadata=job.get("metadata") or {},
                    # The editorial pin (blog_subjects.md §5). BOTH may be None —
                    # that is the unpinned row of the table, not a default.
                    mode=cfg["mode"],
                    support=cfg["support"],
                    editorial_voice=cfg["editorial_voice"],
                    task_label=(job.get("title") or "").strip() or None,
                )
        except asyncio.TimeoutError:
            await _fail_job(
                supabase,
                job_id,
                code="generation_timeout",
                message="تجاوزت معالجة السؤال المهلة الزمنية المحددة",
                retryable=True,
            )
            return
        except Exception as e:  # noqa: BLE001
            logger.exception("process_job generation failed job_id=%s", job_id)
            await _fail_job(
                supabase,
                job_id,
                code="generation_failed",
                message="تعذّر توليد الإجابة لهذا السؤال",
                retryable=True,
                detail=str(e),
            )
            return

        # Record provenance early (survives a later snapshot hiccup).
        try:
            await run_db(
                _update_job,
                supabase,
                job_id,
                {
                    "conversation_id": gen.conversation_id,
                    "workspace_item_id": gen.workspace_item_id,
                },
            )
        except Exception:  # noqa: BLE001
            logger.warning("process_job: provenance update failed", exc_info=True)

        try:
            result = await _publish_to_public_blog(supabase, job, gen, cfg)
        except Exception as e:  # noqa: BLE001
            logger.exception("process_job publish failed job_id=%s", job_id)
            await _fail_job(
                supabase,
                job_id,
                code="generation_failed",
                message="تعذّر إنشاء منشور المدونة من الإجابة",
                retryable=True,
                detail=str(e),
            )
            return

        try:
            _span.set_attribute("is_published", result.is_published)
            _span.set_attribute("is_public", result.is_public)
            _span.set_attribute("confidence", result.confidence.label)
            _span.set_attribute("root_id", result.root_id or "")
        except Exception:  # noqa: BLE001
            pass

        await _complete_job(supabase, job, result)


async def _complete_job(
    supabase: SupabaseClient, job: dict, result: BlogJobResult
) -> None:
    """Mark the job completed and fire its callback. Shared by both paths.

    The fresh-publish path and the re-drive short circuit MUST agree byte for
    byte on what a completed job looks like — same ``post_id``, same ``result``
    blob, same callback — or a caller polling across a restart sees the job
    change its mind about what it produced.
    """
    job_id = job["job_id"]
    payload = result.model_dump(mode="json")
    await run_db(
        _update_job,
        supabase,
        job_id,
        {
            "status": "completed",
            "post_id": result.post_id,
            "result": payload,
            "error": None,
            "completed_at": _now_iso(),
        },
    )

    # Best-effort completion callback.
    callback_url = (job.get("callback_url") or "").strip()
    if callback_url:
        await _post_callback(callback_url, job_id, "completed", payload)


async def _publish_to_public_blog(
    supabase: SupabaseClient, job: dict, gen, cfg: dict[str, Any]
) -> BlogJobResult:
    """Write **v1 of a public blog** from the generated artifact (D16).

    Direct into ``public_blogs``; **no ``blog_posts`` row is created**. That is
    the whole point of D16 — ``blog_posts`` is a frozen snapshot and the public
    wing must be rewritable by the SEO agent, so the two tables never mix.

    The two visibility flags mean different things and are set independently
    (blog_subjects.md §5's table):

    ======================  ===============  ==================  ==================
    state                   gallery+sitemap  reachable by slug   set by
    ======================  ===============  ==================  ==================
    ``is_public=true``      yes              yes                 ``publish_public``
    ``is_public=false``     **no**           **yes — unlisted**  ``publish_public``
    ``is_published=false``  no               **no — 404**        confidence gate
    ======================  ===============  ==================  ==================

    So a ``low``-confidence article is written UNPUBLISHED regardless of what
    the request asked for, and ``publish_public=false`` is not a draft — it is
    exactly the posture a ``blog_posts`` share link has always had.

    ⚠ **This write is idempotent per JOB, and ``post_id`` is stable across
    re-drives.** The v1 row carries ``job_id`` (migration 156) under a unique
    index, so a second attempt at the same job cannot create a second article —
    it recovers the first one and returns the identical ``post_id`` / ``root_id``
    / ``slug``. The slug could never have provided that guarantee: it comes from
    the aggregator's headline, which is non-deterministic, so attempt two mints
    a different slug and the uniqueness check waves it through. That is exactly
    how one POST published two blogs on 2026-09-02.
    """
    settings = get_settings()
    bot_user_id = (settings.EDITORIAL_BOT_USER_ID or "").strip()
    wi_id = gen.workspace_item_id
    have_wi = bool(wi_id)

    wi_metadata: dict = {}
    wi_title: Optional[str] = None
    content_md = gen.content_text or ""
    # The FROZEN citation payload — serialized ``Reference`` dicts, each already
    # carrying ``has_source`` and ``library_url``. Empty on the chat-only route
    # (no workspace item ⇒ no citations at all).
    references_json: list[dict] = []

    if have_wi:
        wi = await run_db(_load_workspace_item, supabase, wi_id)
        if wi:
            wi_metadata = wi.get("metadata") or {}
            wi_title = wi.get("title")
            content_md = wi.get("content_md") or ""
        # Cited references only (used_only=True) — the SAME call, and therefore
        # the same frozen shape, as the legacy ``blog_posts`` share snapshot
        # (``blog.share_artifact``). That is the whole point of using the
        # PAYLOAD builder rather than ``fetch_item_references``: a snapshot is
        # everything the reader will ever get, so it must be built by the
        # function that knows what a reader needs — not by dumping the models
        # and hoping the two paths captured the same things. They did not.
        #
        # No ``source_view`` rides along (the payload is the metered shape):
        # this lands in ``public_blogs.references_json``, which anonymous
        # readers fetch, and the reveal is entitlement-checked per READER.
        #
        # ⚠ D18 — this array is COPIED VERBATIM into every later version. The
        # citation set of a published blog is CLOSED, which is what makes the
        # SEO rewrite checkable rather than merely instructed.
        #
        # TWO KEYS ONLY THIS CALL PRODUCES, both measured missing on the first
        # two live articles (2026-09-03) because this path used to
        # ``model_dump`` the models instead:
        #
        # * ``has_source`` — ``ReferencePanel`` gates «عرض المصدر» on it, and a
        #   blog reader has no workspace item to probe, so it MUST be frozen.
        #   All 15 references shipped without it: the metered reveal never
        #   rendered, on references the endpoint could have served.
        # * ``library_url`` — «افتح في ريحان», NAVIGATION and never a charge.
        #   Its absence is what left the two compliance citations with no
        #   affordance at all: empty ``landing_url``, no reveal, no in-app link.
        #   A citation a reader cannot act on in ANY way is worse than one that
        #   costs an unlock.
        try:
            references_json = await fetch_item_references_payload(
                supabase, wi_id, used_only=True
            )
        except Exception:  # noqa: BLE001
            logger.warning("publish: fetch_item_references failed", exc_info=True)
            references_json = []

    # ``derive_confidence`` / ``_references_summary`` read ``.n`` / ``.title`` /
    # ``.relevance`` off Reference-LIKE objects, and the payload above is plain
    # dicts. ``_StoredRef`` is the wrapper that already exists for exactly this
    # (the re-drive path wraps a frozen ``references_json`` the same way), so
    # both functions are reused verbatim rather than growing a dict-or-model
    # branch each.
    references = [_StoredRef(r) for r in references_json]

    confidence = derive_confidence(wi_metadata, references, have_workspace_item=have_wi)
    is_published = decide_publish(
        job.get("publish_policy", "auto"),
        job.get("min_confidence", "high"),
        confidence.label,
    )
    is_public = bool(cfg.get("publish_public"))

    question_text = job["question"]        # ANONYMIZED upstream — never a raw one.

    # Title precedence: request title → the body's first-line H1 → the WI title.
    # The H1 is stripped from the body either way (§6's H1 contract), which
    # ``insert_public_blog`` does for us by re-running ``extract_headline`` over
    # the ORIGINAL body with the title we resolved here.
    extracted, _stripped = public_blog_service.split_headline(content_md)
    title = (
        (job.get("title") or "").strip()
        or (extracted or "").strip()
        or (wi_title or "").strip()
        or None
    )

    # Arabic slug. Minted from the resolved title when marketing sent none.
    # ⚠ A purely-ASCII title mints an ASCII kebab slug, which
    # ``assert_slug_available`` refuses with a clean Arabic 400 — that shape
    # belongs to a SUBJECT (§3), and letting one through would make the blog
    # unreachable through the dispatcher.
    # This is the BACKSTOP: ``router._assert_mintable_slug`` already ran the
    # same check at submit time against the request title, so a caller normally
    # learns about a bad title before paying for a retrieval run. This branch
    # still fires for the case that one cannot predict — no request title, so
    # the headline (and therefore the slug) comes from the aggregator.
    slug = (cfg.get("slug") or "").strip() or slugify_ar(title or "")

    job_id = str(job.get("job_id") or "")
    if not job_id:
        # Should be unreachable — job_id is the table's PK, read back on insert.
        # Loud, because without it the row lands with a NULL job_id and the
        # per-job unique index simply does not apply to it (partial index).
        logger.error("publish: job row has no job_id; per-job dedupe is OFF")

    try:
        row = await run_db(
            public_blog_service.insert_public_blog,
            supabase,
            slug=slug,
            blog_type=cfg.get("type") or "",
            question_text=question_text,
            content_md=content_md,
            author_user_id=bot_user_id,
            title=title,
            references_json=references_json,
            subtype=job.get("subtype"),
            source_item_id=wi_id,     # None on a chat-only route (nullable, no FK)
            confidence=confidence.label,
            is_public=is_public,
            is_published=is_published,
            job_id=job_id or None,
        )
    except public_blog_service.JobAlreadyPublishedError:
        # ⚠ SUCCESS, not failure. Another attempt at this same job won the race
        # to `idx_public_blogs_job` — the article the caller asked for exists,
        # and failing the job here would report "no blog" about a live URL while
        # leaving a published article behind a failed job.
        #
        # This is the narrow window `_existing_publication` cannot cover: two
        # attempts that both looked, both saw nothing, and both generated.
        existing = await run_db(
            public_blog_service.get_by_job_id, supabase, job_id
        )
        if existing is None:
            # The index refused us but the row is not readable — a genuine
            # inconsistency (a concurrent soft delete). Do not invent a result.
            raise
        logger.warning(
            "publish: job_id=%s was already published as root_id=%s — "
            "returning the existing blog, discarding this attempt's article",
            job_id, existing.get("root_id"),
        )
        _logfire.warning(
            "editorial.duplicate_publish_prevented",
            job_id=job_id,
            root_id=str(existing.get("root_id") or ""),
            # The slug THIS attempt would have taken. Different from the live
            # one, because the headline is regenerated — that difference is the
            # whole reason the slug could not be the dedupe key.
            discarded_slug=slug,
            live_slug=str(existing.get("slug") or ""),
        )
        return _result_from_row(existing, job)

    root_id = row.get("root_id")
    blog_id = row.get("blog_id")
    resolved_slug = row.get("slug") or slug

    # Tell the job row what it just published, BEFORE the subject filing and the
    # completion update. This is what lets `_existing_publication` short-circuit
    # a later re-drive cheaply; the unique index remains the guarantee for the
    # window before this lands. Best-effort — a failure here costs a wasted
    # regeneration on re-drive, which the index then refuses safely.
    try:
        await run_db(
            _update_job, supabase, job["job_id"],
            {"post_id": blog_id},
        )
    except Exception:  # noqa: BLE001
        logger.warning(
            "publish: could not stamp post_id on job %s", job.get("job_id"),
            exc_info=True,
        )

    # File it under its subjects. Keyed on root_id — subjects belong to the
    # LOGICAL blog, so an SEO rewrite never has to re-file them (migration 154).
    #
    # ⚠ Best-effort ON PURPOSE, and the only place in this module where a
    # validation failure is swallowed. The slugs were already validated at
    # SUBMIT time (an unknown one is a 400 there, never a silent drop). If one
    # was deactivated during the 1–4 minutes the run took, the blog itself is
    # already written: failing the job here would leave a published article
    # behind a "failed" job, and the catch-up re-drive would then collide with
    # its own slug (409). Loud log, live blog, recoverable by hand.
    filed: list[str] = []
    subjects = list(cfg.get("subjects") or [])
    if subjects:
        try:
            filed = await run_db(
                public_blog_service.set_subjects, supabase, root_id, subjects
            )
        except Exception as e:  # noqa: BLE001
            logger.error(
                "publish: could not file blog root_id=%s under subjects %s: %s",
                root_id, subjects, e, exc_info=True,
            )
            _logfire.error(
                "editorial.subjects_attach_failed",
                root_id=str(root_id or ""),
                subjects=subjects,
                error=str(e)[:300],
            )

    created_at = row.get("created_at") or _now_iso()
    url = f"{settings.PUBLIC_WEB_URL}/blog/{resolved_slug}"
    # The body as STORED — the headline was stripped out of it into `title`.
    stored_md = row.get("content_md") or content_md
    summary = blog_service.make_snippet(stored_md)

    logger.info(
        "editorial: published public blog root_id=%s slug=%s "
        "is_public=%s is_published=%s confidence=%s subjects=%s",
        root_id, resolved_slug, is_public, is_published, confidence.label, filed,
    )

    return BlogJobResult(
        # ⚠ The uuid of the VERSION this job wrote, not a blog_posts.post_id —
        # there is no blog_posts row (D16). ``root_id`` is what every later
        # marketing call addresses.
        post_id=blog_id,
        token=None,               # D17 — no token; the slug is the whole address
        root_id=root_id,
        slug=resolved_slug,
        is_public=is_public,
        url=url,
        is_published=is_published,
        confidence=confidence,
        title=row.get("title") or title,
        question_text=question_text,
        summary=summary,
        content_md=stored_md,
        references=_references_summary(references),
        workspace_item_id=wi_id,
        created_at=created_at,
    )



async def _fail_job(
    supabase: SupabaseClient,
    job_id: str,
    *,
    code: str,
    message: str,
    retryable: bool,
    detail: Optional[str] = None,
) -> None:
    error: dict[str, Any] = {"code": code, "message": message, "retryable": retryable}
    if detail:
        error["detail"] = detail[:500]
    try:
        await run_db(
            _update_job,
            supabase,
            job_id,
            {"status": "failed", "error": error, "completed_at": _now_iso()},
        )
    except Exception:  # noqa: BLE001
        logger.exception("_fail_job: could not persist failure for %s", job_id)

    # Best-effort failure callback.
    job = await get_job(supabase, job_id) or {}
    callback_url = (job.get("callback_url") or "").strip()
    if callback_url:
        await _post_callback(callback_url, job_id, "failed", {"error": error})


async def _post_callback(
    callback_url: str, job_id: str, status: str, payload: dict
) -> None:
    """Fire-and-forget completion webhook. Never raises."""
    body = {"job_id": job_id, "status": status}
    if status == "failed":
        body["error"] = payload.get("error")
    else:
        body["result"] = payload
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            await client.post(callback_url, json=body)
    except Exception as e:  # noqa: BLE001
        logger.warning("blog-post callback POST failed (%s): %s", callback_url, e)


async def catchup_stuck_jobs(supabase: SupabaseClient) -> dict:
    """Boot sweep: re-queue or fail jobs stuck in queued/processing.

    A restart across an in-flight job leaves it stranded (the in-process task
    died with the worker). For each stuck job: if it has already been attempted
    ``_MAX_JOB_ATTEMPTS`` times, mark it failed (retryable) — a crash-loop
    guard; otherwise re-spawn its worker (which bumps attempts).

    Safe no-op when ``EDITORIAL_BOT_USER_ID`` is unset (nothing can generate).
    Returns a small stats dict for logging.
    """
    settings = get_settings()
    if not (settings.EDITORIAL_BOT_USER_ID or "").strip():
        logger.info("blog-job catch-up skipped: EDITORIAL_BOT_USER_ID unset")
        return {"skipped": True, "requeued": 0, "failed": 0}

    rows = await run_db(_select_stuck_jobs, supabase)
    requeued = 0
    failed = 0
    for row in rows:
        job_id = row.get("job_id")
        if not job_id:
            continue
        attempts = int(row.get("attempts", 0) or 0)
        if attempts >= _MAX_JOB_ATTEMPTS:
            await _fail_job(
                supabase,
                job_id,
                code="generation_interrupted",
                message="توقفت المعالجة بعد عدة محاولات؛ يرجى إعادة الإرسال بمفتاح تعريف جديد",
                retryable=True,
            )
            failed += 1
        else:
            _spawn_process_job(job_id)
            requeued += 1

    stats = {"skipped": False, "requeued": requeued, "failed": failed}
    logger.info("blog-job catch-up: %s", stats)
    return stats


__all__ = [
    "create_or_get_job",
    "get_job",
    "get_job_by_idempotency_key",
    "wait_for_job",
    "process_job",
    "catchup_stuck_jobs",
    "derive_confidence",
    "decide_publish",
    "editorial_config",
    "read_editorial_config",
    "EDITORIAL_META_KEY",
]
