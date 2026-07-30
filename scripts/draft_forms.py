"""AI drafter for the نماذج (legal-form templates) wing — SEO Public Library Phase 3.

Drafts formal, placeholder'd Saudi legal-form templates INTO the ``public.forms``
base table (migration 098). Each drafted row is written as a DRAFT
(``review_status='draft'``, ``is_published=false``, ``docx_path=NULL``) — this
script NEVER approves or publishes anything. A human legal reviewer flips the
publish flags after review; only then does a form serve publicly (the liability
hard gate enforced in ``library_service`` + the migration-098 header).

One tier_1 LLM call per form (slot ``form_drafter`` in
``agents/utils/agent_models.py``): a pydantic_ai Agent whose structured
``FormDraft`` output is paired with the shared ``TextOutput`` JSON salvager (same
pattern as the template_ingester / deep_search aggregator) so a fallback cell
that emits JSON-as-text doesn't eat a retry.

Per drafted form the model produces:
    title_ar     — عنوان النموذج
    category     — one of: عمل | تقاضي | تجاري | عام
    use_case_md  — متى تستخدم هذا النموذج + نصائح (150–300 words) — the FREE SEO layer
    intro_md     — وصف موجز (2–3 جمل)
    body_md      — the actual template with {حقول} placeholders, formal Saudi
                   legal drafting, references to relevant أنظمة
    legal_basis  — [{label}] citations (labels only, e.g. «المادة 74 من نظام العمل»)

Slug: derived from the PILOT title (deterministic — so ``--dry-run`` can print the
exact slugs and re-runs are idempotent) via ``slugify_ar`` (reused from
``build_seo_slugs.py`` when importable, else a local copy). Slugs are permanent.

CLI (run from the repo root):
    python scripts/draft_forms.py --dry-run          # print the pilot list (no LLM, no DB)
    python scripts/draft_forms.py --apply            # draft + INSERT drafts (skips existing slugs)
    python scripts/draft_forms.py --slug <slug>      # re-draft ONE (overwrites that draft row)

Env: SUPABASE_URL / SUPABASE_SERVICE_KEY (service role — bypasses the forms
deny-all RLS) + the agent provider keys (ALIBABA_API_KEY_GLOBAL / OpenRouter),
all read from ``.env`` via ``shared.config`` / ``python-dotenv``.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
import time
from pathlib import Path

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

from pydantic import BaseModel, Field
from pydantic_ai import Agent, TextOutput
from pydantic_ai.usage import UsageLimits

from agents.utils.agent_models import get_agent_model
from agents.utils.structured_output import make_json_salvager
from shared.db.client import get_supabase_client

# Best-effort observability — the agent spans land in Logfire if it's configured.
try:
    from shared.observability import configure_logfire

    configure_logfire()
except Exception:  # noqa: BLE001
    pass


# --- slugify: reuse build_seo_slugs.slugify_ar, else a local copy ----------
try:
    from scripts.build_seo_slugs import slugify_ar  # type: ignore
except Exception:  # noqa: BLE001 — scripts/ may not import as a package in all envs
    import re
    import unicodedata

    _TASHKEEL = set(range(0x064B, 0x0653))
    _TATWEEL = 0x0640

    def _is_slug_char(ch: str) -> bool:
        if ("a" <= ch <= "z") or ("A" <= ch <= "Z") or ("0" <= ch <= "9"):
            return True
        o = ord(ch)
        if 0x0600 <= o <= 0x06FF or 0x0750 <= o <= 0x077F:
            return unicodedata.category(ch)[0] in ("L", "N")
        return False

    def slugify_ar(text: str) -> str:  # type: ignore[misc]
        """Local copy of build_seo_slugs.slugify_ar (import fell through)."""
        if not text:
            return ""
        s = unicodedata.normalize("NFC", str(text))
        s = "".join(ch for ch in s if ord(ch) not in _TASHKEEL and ord(ch) != _TATWEEL)
        s = s.lower()
        s = "".join(ch if _is_slug_char(ch) else " " for ch in s)
        s = re.sub(r"\s+", "-", s.strip())
        s = re.sub(r"-+", "-", s).strip("-")
        return s


# ===========================================================================
# Pilot list — pinned. Draft EXACTLY these 10 (order = draft order).
# ===========================================================================
PILOT_TITLES: tuple[str, ...] = (
    "عقد عمل محدد المدة",
    "عقد عمل غير محدد المدة",
    "خطاب استقالة",
    "إنذار بالسداد (مطالبة مالية)",
    "صحيفة دعوى عمالية",
    "لائحة اعتراضية على حكم (استئناف)",
    "اتفاقية عدم إفصاح (سرية المعلومات)",
    "إقرار وتعهد",
    "عقد إيجار تجاري",
    "مذكرة تفاهم تجارية",
)

FORM_CATEGORIES: tuple[str, ...] = ("عمل", "تقاضي", "تجاري", "عام")
_DEFAULT_CATEGORY = "عام"


# ===========================================================================
# LLM output schema + salvage
# ===========================================================================


class LegalBasisItem(BaseModel):
    """One الأساس النظامي citation — a display LABEL only (no ids)."""

    label: str


class FormDraft(BaseModel):
    """The drafter's structured output for one legal-form template."""

    title_ar: str
    category: str = Field(description="واحدة من: عمل | تقاضي | تجاري | عام")
    use_case_md: str = Field(description="متى تستخدم هذا النموذج + نصائح، 150–300 كلمة")
    intro_md: str = Field(description="وصف موجز، 2–3 جمل")
    body_md: str = Field(description="نص النموذج مع حقول {اسم_الحقل}")
    legal_basis: list[LegalBasisItem] = Field(default_factory=list)


_SYSTEM_PROMPT_AR = """\
أنت محامٍ سعودي متمرّس تصيغ نماذج قانونية احترافية بصيغة ماركداون، مع حقول قابلة \
للتعبئة بين قوسين معقوفين مثل {اسم_الموظف} و{التاريخ}، متوافقة مع الأنظمة السعودية \
الحالية (نظام العمل، نظام المرافعات الشرعية، نظام المعاملات المدنية، الأنظمة التجارية \
ذات العلاقة). صياغتك رسمية ودقيقة وخالية من الحشو.

لكل نموذج مطلوب، أعِد كائن JSON واحدًا بالحقول التالية فقط:
- title_ar: عنوان النموذج بالعربية.
- category: واحدة حصريًا من: عمل | تقاضي | تجاري | عام.
- use_case_md: فقرة «متى تستخدم هذا النموذج» مع نصائح عملية، بين 150 و300 كلمة، بصيغة \
ماركداون. هذه هي الطبقة المجانية التعريفية.
- intro_md: وصف موجز في جملتين إلى ثلاث جمل.
- body_md: نص النموذج القانوني الفعلي بصيغة ماركداون، مع حقول {اسم_الحقل} في المواضع \
المتغيّرة، وبصياغة سعودية رسمية، مع الإشارة إلى الأنظمة والمواد ذات الصلة داخل النص \
عند الاقتضاء.
- legal_basis: مصفوفة من كائنات {label} فقط (بدون معرّفات)، مثل \
{"label": "المادة 74 من نظام العمل"}.

تنبيه إلزامي: كل نموذج استرشادي عام ولا يُغني عن مراجعة مختصٍّ قانوني قبل الاعتماد؛ \
اجعل الصياغة عامة قابلة للتكييف ولا تُقدّمها كاستشارة نهائية.
"""

_RETRY_MSG = (
    "أعِد المخرجات ككائن JSON صالح مطابق للمخطط (title_ar, category, use_case_md, "
    "intro_md, body_md, legal_basis) فقط — دون أي نص أو وسم <thinking> خارج JSON."
)

# Generous caps: a full template body + a 150–300-word use-case + reasoning tokens
# can add up. request_limit=2 covers the single permitted retry.
_LIMITS = UsageLimits(output_tokens_limit=24_000, request_limit=2)

# Wall-clock ceiling per drafting call — a wedged provider must not hang the run.
_LLM_TIMEOUT_S = 150.0


def _build_agent() -> Agent[None, FormDraft]:
    """Build the form_drafter agent (tier_1 via ``get_agent_model``).

    ``output_type`` pairs the structured ``FormDraft`` with the shared
    ``TextOutput`` salvager so a plain-text JSON emission is recovered without a
    costly validation retry.
    """
    return Agent(
        get_agent_model("form_drafter"),
        name="form_drafter",
        output_type=[
            FormDraft,
            TextOutput(make_json_salvager(FormDraft, retry_msg=_RETRY_MSG)),
        ],
        instructions=_SYSTEM_PROMPT_AR,
        retries=1,
    )


def _render_user_msg(form_title: str) -> str:
    return (
        f"اكتب نموذجًا قانونيًا سعوديًا احترافيًا بعنوان: «{form_title}».\n"
        "التزم بالحقول والقيود الموضّحة في التعليمات، وأدرج حقول {اسم_الحقل} في "
        "المواضع المتغيّرة، واذكر الأساس النظامي بتسميات المواد فقط."
    )


async def _draft_one(agent: Agent[None, FormDraft], form_title: str) -> FormDraft:
    """One tier_1 LLM call → a validated ``FormDraft`` (category clamped)."""
    result = await asyncio.wait_for(
        agent.run(_render_user_msg(form_title), usage_limits=_LIMITS),
        timeout=_LLM_TIMEOUT_S,
    )
    draft: FormDraft = result.output
    if draft.category not in FORM_CATEGORIES:
        draft = draft.model_copy(update={"category": _DEFAULT_CATEGORY})
    return draft


# ===========================================================================
# Supabase I/O
# ===========================================================================


def _existing_slugs(client) -> set[str]:
    """All slugs already present in ``forms`` (for idempotent skipping)."""
    try:
        res = client.table("forms").select("slug").execute()
    except Exception as e:  # noqa: BLE001
        print(f"[draft_forms] WARN: could not read existing forms slugs: {e}")
        return set()
    return {r.get("slug") for r in (res.data or []) if r.get("slug")}


def _row_payload(slug: str, draft: FormDraft) -> dict:
    """Build the ``forms`` INSERT/UPDATE payload — always DRAFT, never published."""
    return {
        "slug": slug,
        "title_ar": (draft.title_ar or "").strip(),
        "category": draft.category,
        "use_case_md": draft.use_case_md,
        "intro_md": draft.intro_md,
        "body_md": draft.body_md,
        "legal_basis": [{"label": lb.label} for lb in draft.legal_basis if lb.label],
        "docx_path": None,
        "review_status": "draft",
        "is_published": False,
    }


def _insert_draft(client, slug: str, draft: FormDraft) -> None:
    client.table("forms").insert(_row_payload(slug, draft)).execute()


def _overwrite_draft(client, slug: str, draft: FormDraft, exists: bool) -> None:
    """Upsert one form by slug (re-draft path). Stays a DRAFT — never flips the
    publish flags even if the reviewer had touched them (a re-draft needs re-review)."""
    payload = _row_payload(slug, draft)
    if exists:
        client.table("forms").update(payload).eq("slug", slug).execute()
    else:
        client.table("forms").insert(payload).execute()


# ===========================================================================
# Modes
# ===========================================================================


def _pilot_slugs() -> list[tuple[str, str]]:
    """``[(title, slug), ...]`` for the pilot, deduping any (unlikely) collision."""
    out: list[tuple[str, str]] = []
    taken: set[str] = set()
    for title in PILOT_TITLES:
        base = slugify_ar(title) or f"form-{len(out) + 1}"
        slug = base
        n = 2
        while slug in taken:
            slug = f"{base}-{n}"
            n += 1
        taken.add(slug)
        out.append((title, slug))
    return out


def run_dry_run() -> None:
    print("=" * 78)
    print("[draft_forms] DRY-RUN — pilot list (no LLM calls, no DB writes)")
    print("=" * 78)
    for i, (title, slug) in enumerate(_pilot_slugs(), 1):
        print(f"  {i:>2}. {title}")
        print(f"      slug: {slug}")
    print(f"\n  {len(PILOT_TITLES)} forms would be drafted (review_status='draft', "
          "is_published=false).")
    print("  Run with --apply to generate + insert drafts.\n")


async def run_apply() -> None:
    client = get_supabase_client()
    existing = _existing_slugs(client)
    agent = _build_agent()

    print("=" * 78)
    print("[draft_forms] APPLY — drafting the pilot (tier_1 form_drafter)")
    print("=" * 78)

    total_t0 = time.perf_counter()
    drafted = 0
    skipped = 0
    failed = 0

    for i, (title, slug) in enumerate(_pilot_slugs(), 1):
        if slug in existing:
            print(f"  {i:>2}. SKIP  {title}  (slug '{slug}' already exists)")
            skipped += 1
            continue
        t0 = time.perf_counter()
        try:
            draft = await _draft_one(agent, title)
        except Exception as e:  # noqa: BLE001
            print(f"  {i:>2}. FAIL  {title}  — {type(e).__name__}: {e}")
            failed += 1
            continue
        try:
            _insert_draft(client, slug, draft)
        except Exception as e:  # noqa: BLE001
            print(f"  {i:>2}. FAIL  {title}  — insert error: {e}")
            failed += 1
            continue
        dt = time.perf_counter() - t0
        print(
            f"  {i:>2}. OK    «{draft.title_ar.strip()}»  [{draft.category}]\n"
            f"        slug={slug}  body_md={len(draft.body_md)} chars  "
            f"legal_basis={len(draft.legal_basis)}  latency={dt:.1f}s"
        )
        drafted += 1
        existing.add(slug)

    total = time.perf_counter() - total_t0
    print("\n" + "-" * 78)
    print(
        f"[draft_forms] done: drafted={drafted}  skipped={skipped}  failed={failed}"
        f"  total_latency={total:.1f}s"
    )
    print("  All rows written review_status='draft', is_published=false (pending "
          "human legal review).\n")


async def run_regenerate(target_slug: str) -> None:
    target_slug = (target_slug or "").strip()
    pilot = _pilot_slugs()
    match = next(((t, s) for (t, s) in pilot if s == target_slug), None)
    if match is None:
        print(f"[draft_forms] slug '{target_slug}' is not in the pilot list. "
              "Known slugs:")
        for _, s in pilot:
            print(f"    {s}")
        sys.exit(2)

    title, slug = match
    client = get_supabase_client()
    exists = slug in _existing_slugs(client)
    agent = _build_agent()

    print(f"[draft_forms] REGENERATE «{title}» (slug={slug}, exists={exists})")
    t0 = time.perf_counter()
    try:
        draft = await _draft_one(agent, title)
    except Exception as e:  # noqa: BLE001
        print(f"[draft_forms] FAIL — {type(e).__name__}: {e}")
        sys.exit(1)
    _overwrite_draft(client, slug, draft, exists)
    dt = time.perf_counter() - t0
    print(
        f"[draft_forms] OK «{draft.title_ar.strip()}» [{draft.category}]  "
        f"body_md={len(draft.body_md)} chars  legal_basis={len(draft.legal_basis)}  "
        f"latency={dt:.1f}s  (review_status='draft', is_published=false)"
    )


def main() -> None:
    ap = argparse.ArgumentParser(
        description="AI-draft the نماذج (legal-form templates) pilot into public.forms."
    )
    g = ap.add_mutually_exclusive_group()
    g.add_argument(
        "--dry-run",
        action="store_true",
        help="print the pilot list (no LLM calls, no DB writes) — the default",
    )
    g.add_argument(
        "--apply",
        action="store_true",
        help="generate + INSERT drafts (skips slugs that already exist)",
    )
    g.add_argument(
        "--slug",
        metavar="SLUG",
        help="re-draft ONE pilot form by slug (overwrites that draft row)",
    )
    args = ap.parse_args()

    if args.apply:
        asyncio.run(run_apply())
    elif args.slug:
        asyncio.run(run_regenerate(args.slug))
    else:
        # Default (and explicit --dry-run) → dry run.
        run_dry_run()


if __name__ == "__main__":
    main()
