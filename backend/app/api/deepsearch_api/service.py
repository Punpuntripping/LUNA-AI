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
``app.state.supabase`` holds) — never a user-scoped client. ``blog_posts``
writes are service-role only.
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
from backend.app.services import blog_service
from backend.app.services.references_service import fetch_item_references
from shared.config import get_settings
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
        "metadata": req.metadata or {},
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


def _load_post_by_token(supabase: SupabaseClient, token: str) -> Optional[dict]:
    res = (
        supabase.table("blog_posts")
        .select("post_id, created_at")
        .eq("token", token)
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

        try:
            async with _get_semaphore():
                gen = await generate_answer_headless(
                    supabase,
                    bot_user_id=bot_user_id,
                    question=job["question"],
                    metadata=job.get("metadata") or {},
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
            result = await _snapshot_to_blog_post(supabase, job, gen)
        except Exception as e:  # noqa: BLE001
            logger.exception("process_job snapshot failed job_id=%s", job_id)
            await _fail_job(
                supabase,
                job_id,
                code="generation_failed",
                message="تعذّر إنشاء منشور المدونة من الإجابة",
                retryable=True,
                detail=str(e),
            )
            return

        # Complete.
        await run_db(
            _update_job,
            supabase,
            job_id,
            {
                "status": "completed",
                "post_id": result.post_id,
                "result": result.model_dump(mode="json"),
                "error": None,
                "completed_at": _now_iso(),
            },
        )
        try:
            _span.set_attribute("is_published", result.is_published)
            _span.set_attribute("confidence", result.confidence.label)
        except Exception:  # noqa: BLE001
            pass

        # Best-effort completion callback.
        callback_url = (job.get("callback_url") or "").strip()
        if callback_url:
            await _post_callback(callback_url, job_id, "completed", result.model_dump(mode="json"))


async def _snapshot_to_blog_post(
    supabase: SupabaseClient, job: dict, gen
) -> BlogJobResult:
    """Load the generated WI, derive confidence, insert the blog_posts snapshot."""
    settings = get_settings()
    bot_user_id = (settings.EDITORIAL_BOT_USER_ID or "").strip()
    wi_id = gen.workspace_item_id
    have_wi = bool(wi_id)

    wi_metadata: dict = {}
    wi_title: Optional[str] = None
    content_md = gen.content_text or ""
    references: list = []

    if have_wi:
        wi = await run_db(_load_workspace_item, supabase, wi_id)
        if wi:
            wi_metadata = wi.get("metadata") or {}
            wi_title = wi.get("title")
            content_md = wi.get("content_md") or ""
        # Cited references only (used_only=True) — same as the in-app share.
        # Phase C: no ``source_view`` rides along (the default is the metered
        # shape). This snapshot lands in ``blog_posts.references_json``, which
        # anonymous readers fetch — see the note in ``api/blog.py``.
        try:
            references = await fetch_item_references(supabase, wi_id, used_only=True)
        except Exception:  # noqa: BLE001
            logger.warning("snapshot: fetch_item_references failed", exc_info=True)
            references = []

    confidence = derive_confidence(wi_metadata, references, have_workspace_item=have_wi)
    is_published = decide_publish(
        job.get("publish_policy", "auto"),
        job.get("min_confidence", "high"),
        confidence.label,
    )

    question_text = job["question"]
    # Title precedence: explicit request title → engine/WI title → None.
    title = (job.get("title") or "").strip() or wi_title or None
    references_json = [r.model_dump(mode="json") for r in references]

    # Insert the snapshot. is_public stays column-default false (private/unlisted).
    token = await run_db(
        blog_service.insert_post,
        supabase,
        owner_user_id=bot_user_id,
        source_item_id=wi_id,  # None on a chat-only route (nullable, no FK)
        subtype=job.get("subtype"),
        question_text=question_text,
        title=title,
        content_md=content_md,
        references_json=references_json,
        display_mode=job.get("display_mode", "question"),
        is_published=is_published,
    )

    post = await run_db(_load_post_by_token, supabase, token) or {}
    post_id = post.get("post_id")
    created_at = post.get("created_at") or _now_iso()
    url = f"{settings.PUBLIC_WEB_URL}/blog/{token}"
    summary = blog_service.make_snippet(content_md)

    return BlogJobResult(
        post_id=post_id,
        token=token,
        url=url,
        is_published=is_published,
        confidence=confidence,
        title=title,
        question_text=question_text,
        summary=summary,
        content_md=content_md,
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
]
