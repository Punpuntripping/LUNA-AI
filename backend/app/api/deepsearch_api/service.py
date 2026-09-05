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
import json
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
    # ``describe_query`` (migration 038) is the router's own reading of the
    # question. It is part of what the pipeline understood the query to BE, so
    # it belongs in the frozen generation context; nothing else here reads it.
    res = (
        supabase.table("workspace_items")
        .select("item_id, content_md, title, metadata, kind, describe_query")
        .eq("item_id", item_id)
        .maybe_single()
        .execute()
    )
    return res.data if res is not None else None


def _load_retrieval_artifact(
    supabase: SupabaseClient, item_id: str
) -> Optional[dict]:
    """The forensic URA row this workspace item was built from, or ``None``.

    ``retrieval_artifacts.artifact_id`` points at ``workspace_items.item_id``
    (the FK followed migration 026's rename). The row is written **best-effort**
    by ``agents/agent_search/publisher._persist_forensics`` — a hiccup there is
    swallowed so it cannot break a live turn — so its absence is normal-ish and
    must never be treated as an error.

    Newest-first + ``limit(1)``: the publisher writes one row per turn, and
    ``generate_answer_headless`` already resolves >1 publish with "last wins".
    """
    res = (
        supabase.table("retrieval_artifacts")
        .select(
            "ura_id, ura_json, schema_version, high_count, medium_count, "
            "produced_by, duration_ms, created_at"
        )
        .eq("artifact_id", item_id)
        .is_("deleted_at", "null")
        .order("created_at", desc=True)
        .limit(1)
        .execute()
    )
    rows = getattr(res, "data", None) or []
    return rows[0] if rows else None


def _load_reranker_runs(supabase: SupabaseClient, ura_id: str) -> list[dict]:
    """Per-sub-query reranker forensics for one URA. Empty list when absent.

    ``sub_query_index`` is GLOBAL across the executors (reg, then compliance,
    then case — ``retrieval_artifacts_service._EXECUTOR_ORDER``) and uses the
    same scheme as the URA's own ``sub_queries[].index``, which is what lets the
    two be merged by index below without a join key of their own.
    """
    res = (
        supabase.table("reranker_runs")
        .select(
            "agent_family, sub_query_index, sub_query_text, sub_query_rationale, "
            "kept_results, dropped_results, sufficient, summary_note"
        )
        .eq("ura_id", ura_id)
        .order("sub_query_index")
        .execute()
    )
    return getattr(res, "data", None) or []


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


# ---------------------------------------------------------------------------
# generation_context — migration 157
# ---------------------------------------------------------------------------
#
# WHAT THIS COLUMN IS FOR
# -----------------------
# The article is the product; this column is provenance. It holds the FIRST
# DRAFT plus the complete context the aggregator worked from, frozen at
# generation, so a later editor — a human, or the SEO agent in
# ``marketing_agents.md`` — has the whole picture and not only the finished
# prose. A rewrite replaces ``content_md``; it must not be able to replace what
# the article was BUILT from.
#
# ⚠ SERVICE-ROLE ONLY, NEVER READER-FACING. ``SELECT (generation_context)`` is
# revoked from anon/authenticated (migration 157) because migration 153 gives
# anon a row-level policy on ``public_blogs`` and RLS filters ROWS, not COLUMNS.
# This object carries verbatim corpus bodies; on a public read it would be an
# unmetered corpus feed on a public table — the same hole the access-tiers work
# exists to close. It appears in no response model and in no ``select()`` list.
#
# ⚠ SNAPSHOT, NEVER A POINTER. The forensic rows this is built from
# (``retrieval_artifacts``, ``reranker_runs``) are written best-effort and are
# keyed to a ``workspace_items`` row, while the whole design of ``public_blogs``
# says a published article must outlive its workspace item. So every value is
# COPIED in. Nothing here is an id to be resolved later.
#
# ⚠ BEST-EFFORT. A missing or failed capture writes NULL and logs at WARNING; it
# never fails the publish. A partial snapshot that reads as complete is worse
# than an honest null, which is why the URA is required rather than patched
# around: without it there is no aggregator input to record, only the article
# that is already on the row.
#
# WHAT IS *NOT* IN HERE, AND WHY
# ------------------------------
# * ``aggregator_input.context_blocks`` — the planner-curated bundle
#   (``AggregatorInput.context_blocks``) is rendered into the aggregator's user
#   message and then dropped. It is not on the URA, not on the workspace item
#   and not in any forensic table, so it is genuinely unrecoverable here.
# * ``first_draft.gaps`` — ``AggregatorOutput.gaps`` reaches the CLI and the log
#   renderer and nothing else; no DB column holds it.
# Both are named in ``unavailable`` on every object rather than silently
# omitted, so a reader can tell "not captured" from "was empty".

GENERATION_CONTEXT_SCHEMA = "1"

# Ceiling on the stored object, in bytes of UTF-8 JSON.
#
# ⚠ The scale here is NOT the one migration 157 quotes. Measured on the two live
# articles (2026-09-05), raw and uncapped, this object is **263 kB and 330 kB** —
# ``ura_json`` alone is 186 kB / 255 kB, plus 72 kB / 64 kB of ``reranker_runs``,
# against a 9 kB and 12 kB article. The migration's 51 kB average / 154 kB peak
# describes the CHAT fleet; an editorial run fans out wider (27 and 39 kept
# results across 13 and 11 sub-queries), so both live articles are already past
# that peak and the guard below is load-bearing rather than theoretical.
#
# 256 KiB is ~20x the article and leaves both live runs holding the majority of
# their retrieval bodies (90 kB of 148 kB, and 115 kB of 123 kB) after a few
# rungs of the ladder. It is a stop against a pathological run — the URA schema
# documents a single 168 kB circular, and a case-heavy fan-out can double the
# result count — not a target. Nothing reads this column on a request path (every
# ``select()`` in ``public_blog_service`` names its columns), so the cost is
# TOAST storage on a row nobody fetches, and ~100 articles a year is ~25 MB.
_CONTEXT_BUDGET_BYTES = 262_144

# Slack for the counters written back into ``truncation`` after the last
# measurement, so the recorded size cannot end up a hair over the ceiling it
# reports.
_BUDGET_HEADROOM_BYTES = 1_024

_TRUNC_MARK = " … [اقتُطع]"

# Fields on a URA result that carry SOURCE BODY text. These are the largest
# thing in the object by an order of magnitude — measured across both live
# articles they are 137 kB of 178 kB and 191 kB of 250 kB — so they are what
# gets cut, ahead of any structure.
_BODY_FIELDS: tuple[str, ...] = (
    "chunk_content",
    "chunk_agent_content",
    "chunk_display",
    "chunk_context",
    "case_content",
    "short_summary",
    "content",
)

# Body carriers nested one level down: resolved cross-referenced مادة bodies on
# the regulation domain, and the case domain's ``referenced_regulations`` (which
# stores its body under ``reference_content``).
_NESTED_BODY_LISTS: tuple[str, ...] = ("cross_refs", "referenced_regulations")
_NESTED_BODY_KEYS: tuple[str, ...] = ("content", "reference_content")

# The identity of a result, kept when even an emptied body will not fit. An
# editor can still see WHAT was retrieved and re-fetch it by ``ref_id``.
_RESULT_STUB_KEYS: tuple[str, ...] = (
    "ref_id",
    "domain",
    "relevance",
    "source_type",
    "appears_in_sub_queries",
)

_UNAVAILABLE_ALWAYS: tuple[str, ...] = (
    "aggregator_input.context_blocks",
    "first_draft.gaps",
)


def _json_bytes(value: Any) -> int:
    """Size of ``value`` as stored: UTF-8 bytes of its JSON form.

    ``ensure_ascii=False`` because that is what Postgres stores — counting the
    escaped form would overstate an Arabic payload by ~3x and make the ceiling
    meaningless.
    """
    return len(json.dumps(value, ensure_ascii=False, default=str).encode("utf-8"))


def _as_index(value: Any) -> Optional[int]:
    """The shared global sub-query index, or ``None`` for anything unusable."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _cut(text: Any, cap: int) -> tuple[Any, bool]:
    """Cut one string to ``cap`` chars, marked. Non-strings pass through."""
    if not isinstance(text, str) or len(text) <= cap:
        return text, False
    return text[:cap].rstrip() + _TRUNC_MARK, True


def _trim_result_bodies(result: dict, cap: int) -> bool:
    """Cut every source body on ONE result to ``cap``. True if anything was cut."""
    cut_any = False
    for key in _BODY_FIELDS:
        value, cut = _cut(result.get(key), cap)
        if cut:
            result[key] = value
            cut_any = True
    for list_key in _NESTED_BODY_LISTS:
        for entry in result.get(list_key) or []:
            if not isinstance(entry, dict):
                continue
            for body_key in _NESTED_BODY_KEYS:
                value, cut = _cut(entry.get(body_key), cap)
                if cut:
                    entry[body_key] = value
                    cut_any = True
    return cut_any


def _trim_results(ctx: dict, cap: int, tier: Optional[str] = None) -> bool:
    """Cut bodies across the retrieval results, optionally one relevance tier."""
    results = (ctx.get("aggregator_input") or {}).get("results") or []
    cut_any = False
    for result in results:
        if not isinstance(result, dict):
            continue
        if tier is not None and result.get("relevance") != tier:
            continue
        cut_any = _trim_result_bodies(result, cap) or cut_any
    return cut_any


def _drop_reranker_dropped_results(ctx: dict) -> bool:
    """Drop the reranker's REJECTED candidates from each sub-query.

    They are the one part of this object the aggregator provably never saw, so
    they go before anything it did. ``dropped_count`` survives on the sub-query,
    so the shape of the rejection stays legible.
    """
    dropped_any = False
    for sub_query in (ctx.get("aggregator_input") or {}).get("sub_queries") or []:
        if not isinstance(sub_query, dict):
            continue
        if sub_query.get("dropped_results"):
            sub_query["dropped_results"] = []
            dropped_any = True
    return dropped_any


def _stub_results(ctx: dict) -> bool:
    """Last resort before the draft itself: keep result IDENTITY only."""
    agg = ctx.get("aggregator_input") or {}
    results = agg.get("results") or []
    if not results:
        return False
    stubbed = [
        {k: r.get(k) for k in _RESULT_STUB_KEYS if k in r}
        for r in results
        if isinstance(r, dict)
    ]
    if stubbed == results:
        return False
    agg["results"] = stubbed
    return True


def _trim_first_draft(ctx: dict, cap: int) -> bool:
    """Cut the draft body. Unreachable in practice — an article is ~10 kB."""
    draft = ctx.get("first_draft") or {}
    value, cut = _cut(draft.get("content_md"), cap)
    if cut:
        draft["content_md"] = value
    return cut


# Applied in order, stopping the moment the object fits. Bodies first (the
# largest thing), structure last — a context that has lost its shape cannot be
# read at all, while one with shortened bodies still says what was retrieved,
# for which sub-query, and why it was kept.
_CONTEXT_TRIM_LADDER: tuple[tuple[str, Any], ...] = (
    # Medium before high at every width: the reranker already said which tier it
    # trusted, and the aggregator weighted them the same way.
    ("medium_bodies@6000", lambda c: _trim_results(c, 6_000, "medium")),
    ("high_bodies@6000", lambda c: _trim_results(c, 6_000, "high")),
    ("medium_bodies@3000", lambda c: _trim_results(c, 3_000, "medium")),
    ("high_bodies@3000", lambda c: _trim_results(c, 3_000, "high")),
    ("medium_bodies@1200", lambda c: _trim_results(c, 1_200, "medium")),
    ("high_bodies@1200", lambda c: _trim_results(c, 1_200, "high")),
    ("reranker_dropped_results", _drop_reranker_dropped_results),
    ("all_bodies@400", lambda c: _trim_results(c, 400)),
    ("bodies_removed", lambda c: _trim_results(c, 0)),
    ("results_stubbed", _stub_results),
    ("first_draft@4000", lambda c: _trim_first_draft(c, 4_000)),
)


def _fit_generation_context(ctx: dict) -> dict:
    """Bring ``ctx`` under the ceiling, recording exactly what was given up.

    ``truncated`` is the flag a consumer checks; ``truncation.steps`` is the
    audit trail, in the order applied. ``stored_bytes`` is measured before the
    counters themselves are written back, so it is accurate to a few dozen bytes
    — hence ``_BUDGET_HEADROOM_BYTES`` between the loop's target and the ceiling
    the object reports.
    """
    ceiling = _CONTEXT_BUDGET_BYTES - _BUDGET_HEADROOM_BYTES
    steps: list[str] = []
    ctx["truncated"] = False
    ctx["truncation"] = {
        "budget_bytes": _CONTEXT_BUDGET_BYTES,
        "raw_bytes": 0,
        "stored_bytes": 0,
        "within_budget": True,
        "steps": steps,
    }

    raw = _json_bytes(ctx)
    size = raw
    if size > ceiling:
        for label, operation in _CONTEXT_TRIM_LADDER:
            if not operation(ctx):
                continue
            steps.append(label)
            size = _json_bytes(ctx)
            if size <= ceiling:
                break

    ctx["truncated"] = bool(steps)
    ctx["truncation"]["raw_bytes"] = raw
    ctx["truncation"]["stored_bytes"] = size
    ctx["truncation"]["within_budget"] = size <= _CONTEXT_BUDGET_BYTES
    return ctx


async def _build_generation_context(
    supabase: SupabaseClient,
    *,
    job: dict,
    cfg: dict[str, Any],
    wi_id: Optional[str],
    wi: Optional[dict],
    first_draft_md: str,
    first_draft_title: Optional[str],
    references_json: list[dict],
) -> Optional[dict]:
    """Freeze the first draft + the aggregator's whole input, or return ``None``.

    ``None`` — an honest null — whenever the retrieval artifact is missing: no
    workspace item (the chat-only route), no ``retrieval_artifacts`` row (the
    best-effort forensic write did not land), or an empty ``ura_json``. In each
    of those cases the only thing left to store is the article, which is already
    on the row, and an object carrying it under an ``aggregator_input`` key with
    nothing in it would read as "the aggregator had no input".

    ``reranker_runs`` are the one part that IS optional: the URA alone
    reconstructs the aggregator's input — that is precisely what
    ``AggregatorInput.from_ura`` does, rebuilding ``sub_queries`` from
    ``ura.sub_queries`` and filling each one's results from the two tiers. The
    reranker rows only add the per-sub-query keep/drop forensics on top, so when
    they are absent the object records that in ``unavailable`` and stands.
    """
    if not wi_id:
        return None

    artifact = await run_db(_load_retrieval_artifact, supabase, wi_id)
    ura = dict((artifact or {}).get("ura_json") or {})
    if not ura:
        logger.warning(
            "publish: no retrieval artifact for workspace item %s — "
            "generation_context written as null",
            wi_id,
        )
        _logfire.warning(
            "editorial.generation_context_unavailable",
            workspace_item_id=str(wi_id),
            job_id=str(job.get("job_id") or ""),
            reason="no_retrieval_artifact",
        )
        return None

    ura_id = str((artifact or {}).get("ura_id") or "")
    runs: list[dict] = []
    if ura_id:
        try:
            runs = await run_db(_load_reranker_runs, supabase, ura_id)
        except Exception:  # noqa: BLE001
            logger.warning(
                "publish: reranker_runs read failed for ura %s", ura_id, exc_info=True
            )
            runs = []

    wi_metadata: dict = (wi or {}).get("metadata") or {}

    # Merge the URA's own sub-query record with the reranker forensics for the
    # same index. Both use the GLOBAL sub_query_index scheme (reg, then
    # compliance, then case), so the index is the join key and no other one is
    # needed.
    # Both index reads are defensive: a single odd row must not cost the whole
    # snapshot. The alternative is a TypeError inside the builder, which the
    # caller correctly turns into a NULL — losing the other twelve sub-queries
    # over one.
    runs_by_index: dict[int, dict] = {}
    for run in runs:
        index = _as_index(run.get("sub_query_index"))
        if index is None:
            continue
        runs_by_index[index] = run

    sub_queries: list[dict] = []
    for entry in ura.get("sub_queries") or []:
        if not isinstance(entry, dict):
            continue
        merged = dict(entry)
        index = _as_index(merged.get("index"))
        run = runs_by_index.get(index) if index is not None else None
        if run:
            merged["agent_family"] = run.get("agent_family") or ""
            merged["kept_results"] = run.get("kept_results") or []
            merged["dropped_results"] = run.get("dropped_results") or []
        sub_queries.append(merged)

    # One flat list, tier-tagged. The aggregator was handed both buckets; which
    # bucket a result sat in rides on the result itself as ``relevance``, which
    # is also what the trim ladder cuts by.
    results: list[dict] = []
    for bucket, tier in (("high_results", "high"), ("medium_results", "medium")):
        for entry in ura.get(bucket) or []:
            if not isinstance(entry, dict):
                continue
            result = dict(entry)
            if not result.get("relevance"):
                result["relevance"] = tier
            results.append(result)

    # The join key between the article's inline [n] markers and the retrieval
    # above. Tiny, and it is what makes this object readable on its own rather
    # than only alongside the sibling ``references_json`` column.
    used_refs = [
        {"n": ref.get("n"), "ref_id": ref.get("ref_id") or ""}
        for ref in references_json or []
        if isinstance(ref, dict)
    ]

    unavailable = list(_UNAVAILABLE_ALWAYS)
    if not runs:
        unavailable.append("aggregator_input.sub_queries[].kept_results")

    context: dict[str, Any] = {
        "schema_version": GENERATION_CONTEXT_SCHEMA,
        "captured_at": _now_iso(),
        "first_draft": {
            # Deliberately a COPY of what v1 publishes. The v1 row does keep the
            # original body forever, but reaching for it is a lookup, and this
            # object is meant to answer "what was this article when it was
            # generated" without one.
            "title": first_draft_title or (wi or {}).get("title") or "",
            "content_md": first_draft_md or "",
            "confidence": wi_metadata.get("confidence"),
            "prompt_key": wi_metadata.get("prompt_key"),
            "model_used": wi_metadata.get("model_used"),
            "ref_count": wi_metadata.get("ref_count"),
            "cited_count": wi_metadata.get("cited_count"),
            "used_refs": used_refs,
        },
        "aggregator_input": {
            # The query as the retrieval pipeline saw it, NOT the job's stored
            # question — they are written by different steps and are allowed to
            # differ, so both are kept.
            "original_query": ura.get("original_query") or "",
            "question_text": job.get("question") or "",
            "describe_query": (wi or {}).get("describe_query") or "",
            "detail_level": wi_metadata.get("detail_level"),
            # The §5 editorial pin. Which family ran, and whether the planner
            # was allowed to decide, is part of why the article reads as it does.
            "editorial": {
                "type": cfg.get("type") or "",
                "subtype": job.get("subtype"),
                "mode": cfg.get("mode"),
                "support": cfg.get("support"),
                "editorial_voice": cfg.get("editorial_voice"),
                "subjects": list(cfg.get("subjects") or []),
            },
            "ura_schema_version": ura.get("schema_version") or "",
            "produced_by": ura.get("produced_by") or {},
            "produced_at": ura.get("produced_at") or "",
            "log_id": ura.get("log_id") or "",
            "sector_filter": list(ura.get("sector_filter") or []),
            "sub_queries": sub_queries,
            "results": results,
        },
        "captured_from": {
            "workspace_item_id": str(wi_id),
            "ura_id": ura_id,
            "reranker_runs": len(runs),
        },
        "unavailable": unavailable,
    }

    return _fit_generation_context(context)


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
    # Held past the ``if have_wi`` block: ``_build_generation_context`` reads
    # ``describe_query`` off it, and re-reading the row would be a second trip
    # for a value this one already returned.
    wi_row: Optional[dict] = None
    content_md = gen.content_text or ""
    # The FROZEN citation payload — serialized ``Reference`` dicts, each already
    # carrying ``has_source`` and ``library_url``. Empty on the chat-only route
    # (no workspace item ⇒ no citations at all).
    references_json: list[dict] = []

    if have_wi:
        wi_row = await run_db(_load_workspace_item, supabase, wi_id)
        if wi_row:
            wi_metadata = wi_row.get("metadata") or {}
            wi_title = wi_row.get("title")
            content_md = wi_row.get("content_md") or ""
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

    # The provenance snapshot (migration 157). Best-effort in the strongest
    # sense: the article is already written and this column is the only thing
    # that could be lost, so EVERY failure mode — a missing forensic row, a read
    # error, a serialization surprise — lands on a logged NULL rather than on a
    # failed job holding a finished article hostage.
    generation_context: Optional[dict] = None
    try:
        generation_context = await _build_generation_context(
            supabase,
            job=job,
            cfg=cfg,
            wi_id=wi_id,
            wi=wi_row,
            # The draft AS GENERATED, headline included. ``insert_public_blog``
            # strips the H1 out of the published body (§6's H1 contract); the
            # frozen draft keeps it, because that line is what the aggregator
            # actually wrote.
            first_draft_md=content_md,
            first_draft_title=title,
            references_json=references_json,
        )
    except Exception:  # noqa: BLE001
        logger.warning(
            "publish: generation_context capture failed for job %s; writing null",
            job_id or "?",
            exc_info=True,
        )
        _logfire.warning(
            "editorial.generation_context_failed",
            job_id=job_id or "",
            workspace_item_id=str(wi_id or ""),
        )
        generation_context = None

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
            # ⚠ v1 ONLY. Migration 158 carries both columns forward on every
            # later version, so a rewrite must never re-stamp them: they
            # describe the GENERATION, not the current prose.
            #
            # ``review_status`` is left at the service default (``pending``) —
            # nobody has read this article. Nothing filters on it yet, so this
            # changes nothing about what is visible today.
            generation_context=generation_context,
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
