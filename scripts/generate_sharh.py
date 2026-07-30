"""AI شرح generator for the مادة (article) pages — SEO Public Library Phase 3.

Pregenerates the cached AI شرح (explanation) shown as the gated value-add on مادة
pages (/regulations/{slug}/{article}). One row per مادة in ``public.seo_sharh``,
keyed ``(regulation_id, article_no)`` (migration 100 — the stable pair, NOT the
delete+rebuilt seo_articles uuid).

Policy (migration 100 header + .claude/plans/seo_public_library.md § Phase 3):
    * PREGENERATE open-tier regulations ONLY — ``seo_item_meta.content_type=
      'regulation' AND seo_tier='open'`` (curated ~54 regs). Never batch-generate
      all ~52k articles.
    * Long-tail شرح is generated on demand later (NOT here, NOT in the anon path).
    * Anon مادة pages render the شرح teaser only when a cached row exists — this
      script is the thing that fills that cache. There is NO LLM call on read.

One tier_2 flash call per مادة (slot ``sharh_generator`` in
``agents/utils/agent_models.py`` = deepseek-v4-flash). The output is a SINGLE
free-text Arabic field (150–300 words), so — unlike the multi-field
draft_forms.py drafter — there is no JSON schema to salvage: a plain ``str``-output
pydantic_ai Agent suffices. A light cleanup strips any leading ``<thinking>`` /
markdown-fence noise a reasoning cell might inline into the text.

Per مادة the model writes:
    شرح مبسّط للمادة، تطبيقها العملي، نقاط يغفل عنها الناس، وينتهي بتنبيه استرشادي.

CLI (run from the repo root):
    python scripts/generate_sharh.py                       # dry-run: counts + cost estimate (default)
    python scripts/generate_sharh.py --dry-run             # same
    python scripts/generate_sharh.py --apply               # generate + INSERT for ALL open-tier regs
    python scripts/generate_sharh.py --apply --reg <uuid>  # generate + INSERT for ONE regulation
    python scripts/generate_sharh.py --apply --limit 50    # cap total مواد generated this run
    python scripts/generate_sharh.py --reg <uuid> --limit 5  # dry-run for one reg

Existing seo_sharh rows are SKIPPED (idempotent re-runs). Generation is batched
politely (small concurrency, default 4 parallel calls).

Env: SUPABASE_URL / SUPABASE_SERVICE_KEY (service role — bypasses the seo_sharh
deny-all RLS) + the agent provider keys (ALIBABA_API_KEY_GLOBAL / OpenRouter),
read from ``.env`` via ``shared.config`` / ``python-dotenv``.
"""
from __future__ import annotations

import argparse
import asyncio
import math
import re
import sys
import time
from pathlib import Path
from typing import Any, Optional

# Make the repo root importable when run directly.
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# Windows consoles default to cp1252, which can't encode Arabic — force UTF-8.
try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
except Exception:  # noqa: BLE001
    pass

try:
    from dotenv import load_dotenv

    load_dotenv()
except Exception:  # noqa: BLE001
    pass

from pydantic_ai import Agent
from pydantic_ai.usage import UsageLimits

from agents.utils.agent_models import cost_usd, get_agent_model
from shared import pricing
from shared.db.client import get_supabase_client

# Best-effort observability — the agent spans land in Logfire if it's configured.
try:
    from shared.observability import configure_logfire

    configure_logfire()
except Exception:  # noqa: BLE001
    pass


SLOT = "sharh_generator"
# Provenance / cost-estimate fallback: the slot's primary (happy-path) model. The
# ACTUAL fired model is read off the run result and stored per-row (a FallbackModel
# cell other than the primary may answer during a provider blip).
_PRIMARY_MODEL = "deepseek-v4-flash"

# Small polite concurrency so a batch of open-tier regs doesn't hammer the provider.
_CONCURRENCY = 4
# Wall-clock ceiling per call — a wedged provider must not hang the whole run.
_LLM_TIMEOUT_S = 120.0
# A 150–300 word Arabic explanation + light reasoning fits comfortably; request_limit=2
# covers the single permitted retry.
_LIMITS = UsageLimits(output_tokens_limit=4_000, request_limit=2)


_SYSTEM_PROMPT_AR = """\
أنت محامٍ سعودي خبير تكتب شرحًا تعريفيًا مبسّطًا لمادة نظامية، موجّهًا لغير المختصّ، \
بصيغة ماركداون وبالعربية الفصحى الواضحة.

اكتب شرحًا واحدًا متماسكًا بين 150 و300 كلمة يتضمّن بالترتيب:
1. شرح مبسّط لمعنى المادة ومضمونها بلغة سهلة (لا تُعِد نسخ نصّها حرفيًا).
2. تطبيقها العملي: كيف تُطبَّق في الواقع ومتى تظهر أهميتها، بمثال أو حالة موجزة عند اللزوم.
3. نقاط يغفل عنها الناس أو يُساء فهمها بشأن هذه المادة.
4. تختم دائمًا بتنبيه استرشادي مختصر: أن هذا الشرح تعريفي عام لا يُغني عن استشارة \
مختصّ قانوني في الحالة المحددة.

قيود:
- لا تخترع أرقام مواد أو أنظمة أخرى ولا تنسب للمادة حكمًا لا يحتمله نصّها.
- لا تُخرِج أي عناوين ميتا مثل «شرح المادة» في أول السطر؛ ابدأ بالمحتوى مباشرة.
- أعِد نص الشرح فقط دون أي مقدّمات أو أوسمة أو JSON.
"""


# --- text cleanup ----------------------------------------------------------
# The output is plain text, but a reasoning-mode fallback cell can occasionally
# inline a leading <thinking>…</thinking> block or wrap the body in a markdown
# fence. Strip only a LEADING thinking prefix + surrounding fences; never touch
# the body content.
_THINK_PREFIX = re.compile(r"(?is)^\s*<think(?:ing)?\s*>.*?</think(?:ing)?\s*>")
_THINK_OPEN = re.compile(r"(?is)^\s*<think(?:ing)?\s*>")
_FENCE_WRAP = re.compile(r"(?is)^\s*```(?:\w+)?\s*(.*?)\s*```\s*$")


def _clean_sharh(text: str) -> str:
    """Strip any leading <thinking> prefix + surrounding markdown fence, trim."""
    s = (text or "").strip()
    m = _THINK_PREFIX.match(s)
    if m:
        s = s[m.end():].strip()
    else:
        m = _THINK_OPEN.match(s)
        if m:
            s = s[m.end():].strip()
    fence = _FENCE_WRAP.match(s)
    if fence:
        s = fence.group(1).strip()
    return s


def _word_count(text: str) -> int:
    return len((text or "").split())


def _model_from_result(result: Any) -> Optional[str]:
    """The model that actually responded (FallbackModel may pick a fallback cell) —
    the last ModelResponse's ``model_name``. Falls back to None → caller uses the
    slot primary. Mirrors agents/utils/tracking._model_from_result."""
    try:
        msgs = result.all_messages()
    except Exception:  # noqa: BLE001
        return None
    model = None
    for m in msgs or []:
        mn = getattr(m, "model_name", None)
        if mn:
            model = mn
    return model


def _build_agent() -> Agent[None, str]:
    """Build the sharh_generator agent (tier_2 flash via ``get_agent_model``).

    Default ``output_type`` is ``str`` — the شرح is a single free-text field, so
    no structured output / JSON salvager is needed."""
    return Agent(
        get_agent_model(SLOT),
        name="sharh_generator",
        instructions=_SYSTEM_PROMPT_AR,
        retries=1,
    )


def _render_user_msg(reg_title: str, article_label: str, article_text: str) -> str:
    return (
        f"النظام: {reg_title}\n"
        f"المادة: {article_label}\n\n"
        f"نص المادة:\n{article_text}\n\n"
        "اكتب شرحًا تعريفيًا لهذه المادة وفق التعليمات (150–300 كلمة، ماركداون، "
        "وينتهي بتنبيه استرشادي)."
    )


# ===========================================================================
# Supabase I/O (read-only except the seo_sharh insert on --apply)
# ===========================================================================


def _open_tier_reg_ids(client) -> list[str]:
    """All open-tier regulation content_ids from the sidecar
    (content_type='regulation' AND seo_tier='open')."""
    try:
        res = (
            client.table("seo_item_meta")
            .select("content_id")
            .eq("content_type", "regulation")
            .eq("seo_tier", "open")
            .execute()
        )
    except Exception as e:  # noqa: BLE001
        print(f"[generate_sharh] ERROR reading open-tier regs: {e}")
        return []
    return [r["content_id"] for r in (res.data or []) if r.get("content_id")]


def _reg_title(client, reg_id: str) -> str:
    """clean_title (falling back to title) for a regulation, for the prompt."""
    try:
        res = (
            client.table("regulations_v2")
            .select("clean_title, title")
            .eq("id", reg_id)
            .limit(1)
            .execute()
        )
        rows = res.data or []
    except Exception:  # noqa: BLE001
        return ""
    if not rows:
        return ""
    return (rows[0].get("clean_title") or rows[0].get("title") or "").strip()


def _existing_sharh_article_nos(client, reg_id: str) -> set[int]:
    """The article_no values that already have a cached seo_sharh row for this reg."""
    out: set[int] = set()
    offset = 0
    page = 1000
    try:
        while True:
            res = (
                client.table("seo_sharh")
                .select("article_no")
                .eq("regulation_id", reg_id)
                .range(offset, offset + page - 1)
                .execute()
            )
            batch = res.data or []
            for r in batch:
                if r.get("article_no") is not None:
                    out.add(int(r["article_no"]))
            if len(batch) < page:
                break
            offset += page
    except Exception as e:  # noqa: BLE001
        print(f"[generate_sharh] WARN reading existing seo_sharh ({reg_id}): {e}")
    return out


def _extracted_articles(client, reg_id: str) -> list[dict]:
    """All extraction_status='extracted' seo_articles rows for a regulation
    (article_no, article_label, article_text), ordered by article_no."""
    rows: list[dict] = []
    offset = 0
    page = 1000
    try:
        while True:
            res = (
                client.table("seo_articles")
                .select("article_no, article_label, article_text")
                .eq("regulation_id", reg_id)
                .eq("extraction_status", "extracted")
                .order("article_no")
                .range(offset, offset + page - 1)
                .execute()
            )
            batch = res.data or []
            rows.extend(batch)
            if len(batch) < page:
                break
            offset += page
    except Exception as e:  # noqa: BLE001
        print(f"[generate_sharh] ERROR reading seo_articles ({reg_id}): {e}")
    return rows


def _articles_to_generate(client, reg_id: str) -> list[dict]:
    """Extracted articles for a reg MINUS those already cached in seo_sharh.

    Only rows with non-empty article_text are eligible (nothing to explain
    otherwise). Returns ``[{article_no, article_label, article_text}, ...]``."""
    have = _existing_sharh_article_nos(client, reg_id)
    todo: list[dict] = []
    for r in _extracted_articles(client, reg_id):
        no = r.get("article_no")
        if no is None:
            continue
        no = int(no)
        if no in have:
            continue
        text = (r.get("article_text") or "").strip()
        if not text:
            continue
        todo.append(
            {
                "article_no": no,
                "article_label": (r.get("article_label") or f"المادة {no}").strip(),
                "article_text": text,
            }
        )
    return todo


def _insert_sharh(client, reg_id: str, article_no: int, sharh_md: str, model: str) -> None:
    """Idempotent write of one seo_sharh row (upsert on the composite PK)."""
    client.table("seo_sharh").upsert(
        {
            "regulation_id": reg_id,
            "article_no": article_no,
            "sharh_md": sharh_md,
            "model": model,
        },
        on_conflict="regulation_id,article_no",
    ).execute()


# ===========================================================================
# Target resolution
# ===========================================================================


def _resolve_targets(client, reg: Optional[str]) -> list[str]:
    """The regulation ids to process: the single ``--reg`` when given, else every
    open-tier regulation."""
    if reg:
        return [reg.strip()]
    return _open_tier_reg_ids(client)


def _collect_plan(
    client, reg_ids: list[str], limit: Optional[int]
) -> tuple[list[dict], list[tuple[str, str, int]]]:
    """Build the full generation plan across ``reg_ids``.

    Returns ``(jobs, per_reg)`` where ``jobs`` is a flat list of
    ``{regulation_id, reg_title, article_no, article_label, article_text}`` (capped
    at ``limit`` total) and ``per_reg`` is ``[(reg_id, reg_title, todo_count), ...]``
    for the report (todo_count is BEFORE the limit cap)."""
    jobs: list[dict] = []
    per_reg: list[tuple[str, str, int]] = []
    for reg_id in reg_ids:
        title = _reg_title(client, reg_id)
        todo = _articles_to_generate(client, reg_id)
        per_reg.append((reg_id, title, len(todo)))
        for art in todo:
            if limit is not None and len(jobs) >= limit:
                break
            jobs.append(
                {
                    "regulation_id": reg_id,
                    "reg_title": title,
                    "article_no": art["article_no"],
                    "article_label": art["article_label"],
                    "article_text": art["article_text"],
                }
            )
        if limit is not None and len(jobs) >= limit:
            break
    return jobs, per_reg


# ===========================================================================
# Cost estimate (dry-run)
# ===========================================================================

# Rough per-call token assumptions for the dry-run estimate (Arabic ≈ 3 chars/token).
_CHARS_PER_TOKEN = 3.0
_EST_OUTPUT_TOKENS = 360    # ~250 words × ~1.4 tokens/word
_EST_REASONING_TOKENS = 250  # deepseek-flash default (light) reasoning


def _estimate_cost(jobs: list[dict]) -> float:
    """Rough USD estimate for generating ``jobs``. Loads live pricing when
    available; returns 0.0 when the model is unknown to the pricing table."""
    sys_len = len(_SYSTEM_PROMPT_AR)
    total = 0.0
    for j in jobs:
        chars = (
            sys_len
            + len(j["article_text"])
            + len(j["reg_title"])
            + len(j["article_label"])
            + 120  # user-message scaffolding
        )
        est_in = math.ceil(chars / _CHARS_PER_TOKEN)
        total += cost_usd(
            _PRIMARY_MODEL, est_in, _EST_OUTPUT_TOKENS, _EST_REASONING_TOKENS
        )
    return total


# ===========================================================================
# Modes
# ===========================================================================


def run_dry_run(reg: Optional[str], limit: Optional[int]) -> None:
    client = get_supabase_client()
    try:
        pricing.load_pricing(client)
    except Exception:  # noqa: BLE001
        pass

    reg_ids = _resolve_targets(client, reg)
    print("=" * 78)
    scope = f"reg {reg}" if reg else "ALL open-tier regulations"
    print(f"[generate_sharh] DRY-RUN — {scope} (no LLM calls, no DB writes)")
    print("=" * 78)
    if not reg_ids:
        print("  No target regulations found.\n")
        return

    jobs, per_reg = _collect_plan(client, reg_ids, limit)
    total_todo = sum(c for _, _, c in per_reg)
    for reg_id, title, count in per_reg:
        label = title or reg_id
        print(f"  {count:>4} مادة to generate  —  {label}")

    print("-" * 78)
    est = _estimate_cost(jobs)
    capped = " (capped by --limit)" if limit is not None and len(jobs) < total_todo else ""
    print(
        f"  regulations: {len(per_reg)}   articles needing شرح: {total_todo}   "
        f"would generate now: {len(jobs)}{capped}"
    )
    print(
        f"  est. cost ≈ ${est:.4f} at {_PRIMARY_MODEL} rates "
        f"(~{math.ceil((len(_SYSTEM_PROMPT_AR)) / _CHARS_PER_TOKEN)} sys tokens + "
        f"article + ~{_EST_OUTPUT_TOKENS}+{_EST_REASONING_TOKENS} out/reasoning per call)"
    )
    print("  Run with --apply to generate + insert into seo_sharh.\n")


async def _generate_one(
    agent: Agent[None, str], sem: asyncio.Semaphore, client, job: dict
) -> dict:
    """Generate + insert one مادة's شرح. Returns a status dict for the report."""
    async with sem:
        t0 = time.perf_counter()
        try:
            result = await asyncio.wait_for(
                agent.run(
                    _render_user_msg(
                        job["reg_title"], job["article_label"], job["article_text"]
                    ),
                    usage_limits=_LIMITS,
                ),
                timeout=_LLM_TIMEOUT_S,
            )
        except Exception as e:  # noqa: BLE001
            return {
                "ok": False,
                "article_no": job["article_no"],
                "label": job["article_label"],
                "error": f"{type(e).__name__}: {e}",
                "dt": time.perf_counter() - t0,
            }

        sharh = _clean_sharh(result.output)
        if not sharh:
            return {
                "ok": False,
                "article_no": job["article_no"],
                "label": job["article_label"],
                "error": "empty output after cleanup",
                "dt": time.perf_counter() - t0,
            }
        model = _model_from_result(result) or _PRIMARY_MODEL

        try:
            await asyncio.to_thread(
                _insert_sharh,
                client,
                job["regulation_id"],
                job["article_no"],
                sharh,
                model,
            )
        except Exception as e:  # noqa: BLE001
            return {
                "ok": False,
                "article_no": job["article_no"],
                "label": job["article_label"],
                "error": f"insert error: {e}",
                "dt": time.perf_counter() - t0,
            }

        return {
            "ok": True,
            "article_no": job["article_no"],
            "label": job["article_label"],
            "words": _word_count(sharh),
            "chars": len(sharh),
            "model": model,
            "dt": time.perf_counter() - t0,
        }


async def run_apply(reg: Optional[str], limit: Optional[int]) -> None:
    client = get_supabase_client()
    reg_ids = _resolve_targets(client, reg)

    print("=" * 78)
    scope = f"reg {reg}" if reg else "ALL open-tier regulations"
    print(f"[generate_sharh] APPLY — {scope} (tier_2 flash '{SLOT}')")
    print("=" * 78)
    if not reg_ids:
        print("  No target regulations found.\n")
        return

    jobs, per_reg = _collect_plan(client, reg_ids, limit)
    total_todo = sum(c for _, _, c in per_reg)
    print(
        f"  regulations: {len(per_reg)}   articles needing شرح: {total_todo}   "
        f"generating now: {len(jobs)}"
        + (" (capped by --limit)" if limit is not None and len(jobs) < total_todo else "")
    )
    if not jobs:
        print("  Nothing to generate (all cached). Done.\n")
        return

    agent = _build_agent()
    sem = asyncio.Semaphore(_CONCURRENCY)
    total_t0 = time.perf_counter()

    results = await asyncio.gather(
        *(_generate_one(agent, sem, client, job) for job in jobs)
    )

    total = time.perf_counter() - total_t0
    written = [r for r in results if r["ok"]]
    failed = [r for r in results if not r["ok"]]

    for r in sorted(written, key=lambda x: x["article_no"]):
        print(
            f"  OK    {r['label']}  ({r['words']} words, {r['chars']} chars, "
            f"{r['model']}, {r['dt']:.1f}s)"
        )
    for r in sorted(failed, key=lambda x: x["article_no"]):
        print(f"  FAIL  {r['label']}  — {r['error']}")

    print("\n" + "-" * 78)
    print(
        f"[generate_sharh] done: written={len(written)}  failed={len(failed)}  "
        f"total_latency={total:.1f}s  ({_CONCURRENCY}-way concurrency)"
    )

    # Full sample: the article-80 شرح when it was generated this run.
    sample = next((r for r in written if r["article_no"] == 80), None)
    if sample is None and written:
        sample = min(written, key=lambda x: x["article_no"])
    if sample is not None:
        try:
            res = (
                client.table("seo_sharh")
                .select("sharh_md")
                .eq("regulation_id", jobs[0]["regulation_id"])
                .eq("article_no", sample["article_no"])
                .limit(1)
                .execute()
            )
            rows = res.data or []
            if rows:
                print("\n" + "=" * 78)
                print(f"  SAMPLE شرح — {sample['label']}")
                print("=" * 78)
                print(rows[0].get("sharh_md", ""))
                print("=" * 78)
        except Exception as e:  # noqa: BLE001
            print(f"  (could not fetch sample شرح: {e})")
    print()


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Pregenerate the AI شرح cache (seo_sharh) for open-tier مواد."
    )
    ap.add_argument(
        "--apply",
        action="store_true",
        help="generate + INSERT into seo_sharh (default is a dry-run)",
    )
    ap.add_argument(
        "--reg",
        metavar="UUID",
        help="restrict to ONE regulation id (else every open-tier regulation)",
    )
    ap.add_argument(
        "--limit",
        type=int,
        metavar="N",
        help="cap the total number of مواد generated this run",
    )
    args = ap.parse_args()

    limit = args.limit if (args.limit is not None and args.limit > 0) else None

    if args.apply:
        asyncio.run(run_apply(args.reg, limit))
    else:
        run_dry_run(args.reg, limit)


if __name__ == "__main__":
    main()
