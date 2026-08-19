"""LLM-named English slug population for the /compliance service-guides wing.

Reads the guide corpus (``service_guides`` ⋈ ``services``, canonical rows only —
the same set migration 142's ``library_compliance_v`` serves, but read from the
base tables so this pass does not depend on that migration; see the
``_CORPUS_SELECT`` note) and writes a permanent,
SHORT ENGLISH kebab-case ``slug`` into the ``seo_item_meta`` SIDECAR under
``content_type='compliance'``, keyed ``content_id = service_guides.id::text``,
with ``rank = most_used_rank`` and ``indexable = true``.

Sibling of ``scripts/build_seo_slugs.py`` — read that file's header first; every
discipline it states applies here verbatim. What is DIFFERENT is only the slug
ALGORITHM: the other wings transliterate the Arabic title into an Arabic slug
with a pure function, and this one asks a tier_2 flash model for an English one.
The rest — permanence, MERGE-upsert, collision suffixes, dry-run default, the
committed reverse — is deliberately identical, because those rules are about
what a published URL IS, not about how its text was derived.

WHY A MODEL AND NOT A SLUGIFIER
-------------------------------
Every other wing's slug is the item's own Arabic title, normalised. That works
because the title IS the distinguishing text. Guide titles are not: all 169 of
them start with the literal prefix «الدليل الشامل: » and most end in «في
السعودية», so a mechanical slugifier would emit 169 URLs that share a 14-character
head and a common tail and differ only in the middle. Worse, the wing's whole
reason to exist is SEO, and its audience searches in both languages — an English
URL is the one part of an Arabic page a search engine and a human can both read
at a glance. So the prefix and the trailing «في السعودية» are stripped BEFORE the
model ever sees the title (they carry no distinguishing information), and the
model is asked for a 2-6 word English name for the SERVICE — the action and the
thing it acts on: ``renew-e-services-subscription``,
``citizen-account-registration``, ``report-work-injury``.

The model is the only non-deterministic thing in this script, and it is fenced on
both sides: its output is normalised, then validated against
``^[a-z0-9]+(-[a-z0-9]+)*$`` and a 2-6 word band (one automatic ModelRetry on a
shape failure), and anything that still does not pass falls back to the
deterministic ``service-{service_ref}``. A run can therefore be poor, but it
cannot be invalid, and it cannot 500 the wing.

PERMANENCE — WHY A RE-RUN NEVER TOUCHES A SLUGGED ROW
-----------------------------------------------------
Slugs are PERMANENT. A published URL is a promise: it is in Google's index, in
the sitemap, in the reference dialog's «افتح الدليل الشامل للخدمة في ريحان» exit,
and possibly in somebody's notes. This script therefore FILLS BLANKS ONLY — a row
that already carries a slug is skipped whole, and that includes its ``rank`` and
``indexable``. Refreshing ``rank`` on a re-run would be harmless in isolation, but
"this script only ever adds" is a much easier invariant to trust than "this
script rewrites some columns and not others", and the model call it would burn
buys nothing. If the ingest ever re-derives ``most_used_rank`` and the wing's
ordering must follow, that is a separate, explicit pass.

The write is a MERGE-upsert on the composite PK ``(content_type, content_id)``
carrying ONLY ``slug`` / ``rank`` / ``indexable`` / ``updated_at``, exactly like
``scripts/set_gate.py`` and ``build_seo_slugs.py``: any ``seo_tier`` or
``gate_override`` a future gating pass puts on the row survives untouched. The
corpus side is a pipeline-owned view — this script writes to the sidecar and
NOTHING else.

⚠ THE ``'service'`` ROWS ARE OFF-LIMITS
---------------------------------------
The sidecar already holds 4,717 rows under ``content_type='service'`` (100 of
them slugged, with ARABIC slugs) — leftovers from the RETIRED wing, keyed by
``services.id``. That is a different key space pointing at a different corpus,
and the two must never meet: a compliance row is keyed by ``service_guides.id``,
so the same real-world service has DIFFERENT ids in the two spaces and a
cross-space read would silently mis-key every URL it produced. Every query in
this file pins ``content_type='compliance'``, so the ``'service'`` rows are never
read, never written, and cannot collide — the sidecar's unique index is
``(content_type, slug)``, per-type, so an Arabic ``'service'`` slug is not even in
the same namespace as an English ``'compliance'`` one.

COLLISIONS
----------
Within ``content_type='compliance'`` only. Rows are processed in stable ``id``
order — the LLM calls run concurrently, but the dedupe walk happens afterwards
over the id-sorted list, so the suffix a given guide receives does not depend on
which call returned first. A duplicate base gets a deterministic ``-2``, ``-3``…
Slugs already in the sidecar count as taken, which is what makes an incremental
run (``--limit 5`` today, ``--limit 40`` next month) stable rather than a
re-shuffle.

THE PILOT
---------
``--limit N`` takes the N LOWEST ``most_used_rank`` guides — the most-used ones.
This wing ships that way on purpose: the first run is ``--limit 5 --apply``, five
guides live and 164 left unslugged. An unslugged guide is INVISIBLE — the hub
lister pages over ``_published_ids('compliance')``, the doc route resolves a slug
to a ``content_id``, and the sitemap section joins on ``slug is not null and
indexable``. There is no separate "published" flag to forget to set; the slug IS
the flag, which is also why the reverse below is what it is.

REVERSIBILITY — ``--unpublish --ids-file``
------------------------------------------
Publishing a guide IS writing its slug, so un-publishing it is writing that slug
back to ``NULL``. It is an UPDATE, never a DELETE. The sidecar row survives with
its ``seo_tier`` / ``gate_override`` intact, so a later re-publish restores the
guide's GATING rather than silently resetting it to the wing default — throwing
that away is the one thing this path exists to prevent. The clear is scoped to
``content_type='compliance'`` AND the given ids, so it cannot reach another wing
even if the file holds foreign ids, and it is ``--apply``-gated exactly like
publishing.

There is deliberately **no ``--unpublish-all``**, for the same reason
``build_seo_slugs.py`` has none: retiring a surface that has real inbound links
should take an explicit id list somebody had to produce on purpose. The one
rollback this library has ever done was performed with ad-hoc SQL that was never
committed; publishing in bulk without a committed reverse is not a position worth
being in.

Run from the repo root:
  python scripts/build_compliance_slugs.py                    # dry-run, all 169
  python scripts/build_compliance_slugs.py --limit 5          # dry-run, the pilot set
  python scripts/build_compliance_slugs.py --limit 5 --apply  # THE PILOT — write 5
  python scripts/build_compliance_slugs.py --apply            # write the rest

  # publish a CHOSEN set (one service_guides.id per line)
  python scripts/build_compliance_slugs.py --ids-file ids.txt --apply

  # reverse exactly that set
  python scripts/build_compliance_slugs.py --unpublish --ids-file ids.txt
  python scripts/build_compliance_slugs.py --unpublish --ids-file ids.txt --apply

⚠ The dry-run DOES call the model (that is the point — the slugs are the thing
being reviewed) and does cost a few cents. It writes nothing. The calls run at
``temperature=0`` so the preview matches what ``--apply`` will write, but a
reasoning cell is never bit-exact: read a dry-run as indicative, not as a
contract. The binding slug is the one in the sidecar, and it is written once.

Env: SUPABASE_URL / SUPABASE_SERVICE_KEY (service role — the sidecar is deny-all
for anon/authenticated) + the agent provider keys (ALIBABA_API_KEY_GLOBAL /
OPENROUTER_API_KEY), via ``shared.config`` / ``shared.db.client`` / python-dotenv.
"""
from __future__ import annotations

import argparse
import asyncio
import re
import sys
import time
from datetime import datetime, timezone
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

from pydantic_ai import Agent, ModelRetry
from pydantic_ai.usage import UsageLimits

from agents.utils.agent_models import ModelPolicy, build_fallback_model, cost_usd
from shared import pricing
from shared.db.client import get_supabase_client

# Best-effort observability — the agent spans land in Logfire if it's configured.
try:
    from shared.observability import configure_logfire

    configure_logfire()
except Exception:  # noqa: BLE001
    pass


# The sidecar namespace this script owns. Pinned on EVERY query — see the
# docstring's note on the retired wing's `'service'` rows.
CONTENT_TYPE = "compliance"

# ⚠ THE CORPUS READ IS DELIBERATELY NOT `library_compliance_v` (migration 142).
# The wing's read view is the right surface for the BACKEND, but this script sits
# upstream of the backend: the rollout order is migration → slugs → deploy, and a
# naming pass that cannot run until a migration has been applied is a coupling
# that buys nothing. So the two predicates the view defines — canonical rows
# only, and an INNER join to `services` (a guide with no service row has no
# provider, no outbound link and no sector facet) — are stated here explicitly
# instead, via a PostgREST embedded resource over
# `service_guides_service_id_fkey`. ⚠ THOSE TWO LINES MUST KEEP AGREEING WITH
# MIGRATION 142: this script decides which guides get URLs and the view decides
# which guides the wing serves, and a guide slugged here but filtered out there
# would be a 404 in the sitemap.
_CORPUS_TABLE = "service_guides"
_CORPUS_SELECT = "id, service_ref, title, most_used_rank, services!inner(provider_name)"

# Read-page size (PostgREST caps a single response at 1000 rows by default). The
# corpus is 169 rows, but the sidecar side is 4,717 `'service'` rows + whatever
# `'compliance'` has, so the paging is not decorative.
_READ_PAGE = 1000
# Upsert batch size on --apply.
_WRITE_BATCH = 500
# Chunk size for `.in_("content_id", [...])` FILTERS — deliberately not
# _WRITE_BATCH. An upsert body carries its rows in the POST body, but an
# `in.(...)` filter is a query STRING: 500 uuids is ~19 KB of URL, which proxies
# truncate or 414 long before PostgREST sees it. Same value and same reason as
# build_seo_slugs.py:_ID_CHUNK.
_ID_CHUNK = 100

# --- the model ------------------------------------------------------------
# tier_2, deepseek-primary — the same cheap/fast cell the other OFFLINE script
# slots run on (`sharh_generator`, `template_ingester`). Built straight from a
# ``ModelPolicy`` rather than through ``get_agent_model(slot)`` because this is
# not an agent: it is a one-shot naming pass that runs a handful of times in the
# wing's whole lifetime, and registering it in ``AGENT_MODELS`` would put a
# non-agent in the pipeline's per-agent control surface (and in every cost
# report keyed off it). The plumbing underneath is identical — the same
# FallbackModel chain, the same providers, the same tier lock. If this ever
# becomes a recurring job, give it a real slot then.
_POLICY = ModelPolicy("tier_2", primary="deepseek")
# Provenance / cost-report fallback: the policy's primary (happy-path) model. The
# ACTUAL fired model is read off each run (a FallbackModel cell other than the
# primary may answer during a provider blip).
_PRIMARY_MODEL = "deepseek-v4-flash"

# Polite concurrency — 169 short calls, no reason to hammer the provider.
_CONCURRENCY = 6
# Wall-clock ceiling per call. A wedged provider must not hang a 169-row run; a
# timeout is just another route to the deterministic fallback.
_LLM_TIMEOUT_S = 60.0
# A slug is ~10 output tokens. ⚠ THE CEILING IS NOT SIZED FOR THE ANSWER — it is
# sized for the THINKING. `output_tokens_limit` counts reasoning tokens, and this
# cell reasons by provider default: measured over the 169-row corpus it spends
# ~480 reasoning tokens on a call whose answer is three words, with a long tail
# past 2,500. At 2,000 that tail became three spurious `service-{ref}` fallbacks
# — the model had named them fine and the LIMIT threw the answer away. 8,000
# leaves the ceiling doing its real job (stopping a cell that has run away)
# without deciding the outcome. request_limit=2 covers the single ModelRetry.
_LIMITS = UsageLimits(output_tokens_limit=8_000, request_limit=2)

# --- slug shape -----------------------------------------------------------
SLUG_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
_MIN_WORDS = 2
_MAX_WORDS = 6

# --- Arabic title trimming ------------------------------------------------
# Every one of the 169 titles starts with this exact prefix (verified live
# 2026-08-19) and it is the wing's own label, not the service's name — feeding it
# to the model would push "guide"/"comprehensive" into every slug.
_TITLE_PREFIX = "الدليل الشامل:"
# Trailing country tails. Ordered LONGEST-FIRST so the fully spelled-out form is
# matched before the short one. Bare «السعودية» is deliberately NOT in this list:
# it is often a real adjective on the preceding noun («التأمينات الاجتماعية
# السعودية»), and stripping it there would damage the phrase. Leaving a stray
# country word in costs nothing — the model is instructed to drop it.
_TITLE_TAILS = (
    "في المملكة العربية السعودية",
    "بالمملكة العربية السعودية",
    "في السعودية",
    "بالسعودية",
)


_SYSTEM_PROMPT = """\
You name URLs for a Saudi legal-services library. Given one government service \
and the entity that provides it, output ONE short English slug for that \
service's how-to guide page.

Rules:
- Lowercase ASCII letters, digits and single hyphens ONLY: ^[a-z0-9]+(-[a-z0-9]+)*$
- Between 2 and 6 words.
- Name the SERVICE: the action a person performs and the thing it acts on.
  Good: renew-e-services-subscription · citizen-account-registration ·
  report-work-injury · social-insurance-subscriber-registration
- Drop filler that EVERY guide would share: no "guide", "how", "to", "the",
  "saudi", "ksa", "service".
- Do NOT drop a word that says what the service acts on, even if it sounds
  generic — "electronic services", "commercial register", "subscription" and
  the like are the object, not filler.
- Do NOT put the provider/ministry name in the slug.
- Translate to natural English terms; transliterate a proper noun only when
  there is no English equivalent.
- Two different services must not get the same slug — keep the words that make
  this one specific (e.g. "optional" / "temporary" / "renewal" vs "issuance").

Output the slug ALONE. No quotes, no backticks, no explanation, no trailing
period, no surrounding text.
"""


def strip_guide_title(title: str) -> str:
    """The distinguishing part of a guide title — what the model is shown.

    Removes the «الدليل الشامل: » prefix and any trailing country tail. Both are
    constant across the corpus, so neither can distinguish one guide from
    another; leaving them in would make every slug start and end the same way.
    A title that somehow lacks the prefix is returned as-is (minus the tail):
    inventing structure for an unknown title shape is worse than leaving it.
    """
    t = (title or "").strip()
    if t.startswith(_TITLE_PREFIX):
        t = t[len(_TITLE_PREFIX):].strip()
    for tail in _TITLE_TAILS:
        if t.endswith(tail):
            t = t[: -len(tail)].strip()
            break
    return t.strip(" \t-–—:،.")


def normalize_slug(raw: str) -> str:
    """Coerce a model's answer into slug shape — WITHOUT inventing content.

    Handles the noise a flash cell adds around an otherwise-correct answer:
    markdown fences, surrounding quotes/backticks, a leading "slug:" label, a
    trailing period, spaces or underscores where hyphens belong, stray casing.
    Everything outside ``[a-z0-9-]`` becomes a separator, runs of hyphens
    collapse, and the ends are trimmed.

    It deliberately does NOT shorten, translate or re-word: a result that is
    still wrong after this is a genuine model failure and must fail validation
    rather than be quietly repaired into something plausible.
    """
    s = (raw or "").strip()
    # ```slug``` / ```\nslug\n```
    s = re.sub(r"(?is)^\s*```(?:\w+)?\s*(.*?)\s*```\s*$", r"\1", s)
    # a leading label the model sometimes keeps from the prompt
    s = re.sub(r"(?i)^\s*(?:slug|answer|output)\s*[:\-]\s*", "", s)
    s = s.strip().strip("`\"'“”«»").strip()
    # the first line only — a chatty cell puts the slug first and prose after
    s = s.splitlines()[0] if s else ""
    s = s.lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    s = re.sub(r"-+", "-", s).strip("-")
    return s


def slug_shape_error(slug: str) -> Optional[str]:
    """``None`` when ``slug`` is acceptable, else why it is not (also the text
    fed back to the model on the single ModelRetry)."""
    if not slug:
        return "empty output"
    if not SLUG_RE.match(slug):
        return (
            f"'{slug}' is not lowercase-kebab-case "
            f"(^[a-z0-9]+(-[a-z0-9]+)*$)"
        )
    n = len(slug.split("-"))
    if n < _MIN_WORDS:
        return f"'{slug}' is only {n} word — use {_MIN_WORDS}-{_MAX_WORDS} words"
    if n > _MAX_WORDS:
        return f"'{slug}' is {n} words — use at most {_MAX_WORDS}"
    return None


def _build_agent() -> Agent[None, str]:
    """The slug namer: plain ``str`` output plus one shape-validating retry.

    The validator is what converts "the model was chatty / verbose / shouty" into
    a second attempt with the reason attached, instead of straight into the
    ``service-{ref}`` fallback. One retry only (``retries=1`` +
    ``request_limit=2``): a cell that misses the shape twice on a prompt this
    small is not going to get it on the third try, and the fallback is fine.
    """
    agent: Agent[None, str] = Agent(
        build_fallback_model(_POLICY),
        name="compliance_slugger",
        instructions=_SYSTEM_PROMPT,
        retries=1,
        # temperature=0 so the dry-run is a PREVIEW of the apply and not merely
        # a sample of it. The review workflow here is "eyeball 169 URLs, then
        # write them", and at the default temperature the same guide answered
        # `renew-e-services-subscription` on one run and
        # `renew-electronic-services-subscriptions` on the next — which makes the
        # eyeballing theatre. It is not a guarantee (a reasoning cell is never
        # bit-exact, and a provider blip can move the answer to a different
        # FallbackModel cell entirely), so treat a dry-run as indicative; the
        # only slug that is ever binding is the one written to the sidecar,
        # which is written once and never rewritten.
        model_settings={"temperature": 0.0},
    )

    @agent.output_validator
    def _validate(text: str) -> str:
        cleaned = normalize_slug(text)
        err = slug_shape_error(cleaned)
        if err:
            raise ModelRetry(
                f"{err}. Reply with ONLY the slug — lowercase words joined by "
                f"single hyphens, {_MIN_WORDS}-{_MAX_WORDS} words, nothing else."
            )
        return cleaned

    return agent


def _render_user_msg(service_title: str, provider: str) -> str:
    return (
        f"Service (Arabic): {service_title}\n"
        f"Provided by: {provider or '(unknown)'}\n\n"
        "Slug:"
    )


def _model_from_result(result: Any) -> Optional[str]:
    """The model that actually responded (FallbackModel may pick a fallback
    cell) — the last ModelResponse's ``model_name``. Mirrors
    agents/utils/tracking._model_from_result."""
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


def _usage_of(result: Any) -> tuple[int, int, int]:
    """``(input, output, reasoning)`` tokens for one run; zeros on anything odd."""
    try:
        u = result.usage()
        details = getattr(u, "details", None) or {}
        return (
            int(getattr(u, "input_tokens", 0) or 0),
            int(getattr(u, "output_tokens", 0) or 0),
            int(details.get("reasoning_tokens", 0) or 0),
        )
    except Exception:  # noqa: BLE001
        return (0, 0, 0)


# ===========================================================================
# Supabase I/O
# ===========================================================================


def _load_existing(client) -> tuple[dict[str, Optional[str]], set[str]]:
    """``(content_id -> slug, {taken slugs})`` for ``content_type='compliance'``.

    Pinned to this content_type: the sidecar's unique index is
    ``(content_type, slug)``, so only compliance slugs can collide with a
    compliance slug. Reading the whole sidecar would drag in 4,717 ``'service'``
    rows and make Arabic slugs from the retired wing look "taken" here.
    """
    existing: dict[str, Optional[str]] = {}
    taken: set[str] = set()
    offset = 0
    while True:
        res = (
            client.table("seo_item_meta")
            .select("content_id, slug")
            .eq("content_type", CONTENT_TYPE)
            .order("content_id")
            .range(offset, offset + _READ_PAGE - 1)
            .execute()
        )
        batch = res.data or []
        for r in batch:
            cid = r.get("content_id")
            if cid is None:
                continue
            slug = r.get("slug")
            existing[str(cid)] = slug
            if slug:
                taken.add(slug)
        if len(batch) < _READ_PAGE:
            break
        offset += _READ_PAGE
    return existing, taken


def _load_corpus(client) -> list[dict]:
    """Every canonical guide + its provider, flattened, in stable ``id`` order.

    `provider_name` lives on `services` and is half the model's input, so the
    join is not optional — see the ``_CORPUS_SELECT`` note on why it is spelled
    out here rather than taken from the wing's view. `guide_md` is deliberately
    NOT selected: this script names URLs, and dragging 169 full guide bodies
    across the wire to do it would be pure waste.
    """
    rows: list[dict] = []
    offset = 0
    while True:
        res = (
            client.table(_CORPUS_TABLE)
            .select(_CORPUS_SELECT)
            .eq("is_canonical", True)
            .order("id")
            .range(offset, offset + _READ_PAGE - 1)
            .execute()
        )
        batch = res.data or []
        for r in batch:
            # PostgREST nests the embedded row; `service_id` is UNIQUE on
            # service_guides and the FK targets the services PK, so this is
            # always a single object, never a list.
            svc = r.pop("services", None) or {}
            if isinstance(svc, list):  # defensive: an embed can come back as a list
                svc = svc[0] if svc else {}
            r["provider_name"] = svc.get("provider_name")
            rows.append(r)
        if len(batch) < _READ_PAGE:
            break
        offset += _READ_PAGE
    return rows


def _upsert(client, payloads: list[dict]) -> int:
    """MERGE-upsert on ``(content_type, content_id)``.

    Writes ONLY slug / rank / indexable / updated_at, so a ``seo_tier`` or
    ``gate_override`` set later by a gating pass survives a re-run of this
    script untouched.
    """
    written = 0
    for i in range(0, len(payloads), _WRITE_BATCH):
        batch = payloads[i : i + _WRITE_BATCH]
        client.table("seo_item_meta").upsert(
            batch, on_conflict="content_type,content_id"
        ).execute()
        written += len(batch)
    return written


# ===========================================================================
# Selection
# ===========================================================================


def _dedupe(base: str, taken: set[str]) -> str:
    """Return ``base`` or the first free ``base-{n}`` (n>=2). Adds nothing to
    ``taken`` — the caller records the winner."""
    if base not in taken:
        return base
    n = 2
    while f"{base}-{n}" in taken:
        n += 1
    return f"{base}-{n}"


def _rank_key(row: dict) -> tuple[int, str]:
    """Sort key for ``--limit``: lowest ``most_used_rank`` first (lower = more
    used), ``id`` as the tiebreak. A NULL rank sorts LAST — the corpus has none
    today (verified live 2026-08-19), but an unranked guide arriving later must
    queue behind the ranked ones rather than jump to the head of the pilot."""
    r = row.get("most_used_rank")
    return (int(r) if r is not None else 10**9, str(row.get("id")))


def select_targets(
    corpus: list[dict], limit: Optional[int], only_ids: Optional[set[str]]
) -> list[dict]:
    """The rows this run considers, in stable ``id`` order.

    ``--limit`` picks by rank and ``--ids-file`` picks by id, but BOTH return an
    id-sorted list: the collision walk must not depend on the selection order,
    or the same guide could get ``-2`` in one run and the bare slug in another.
    """
    if only_ids is not None:
        rows = [r for r in corpus if str(r.get("id")) in only_ids]
    elif limit is not None:
        rows = sorted(corpus, key=_rank_key)[:limit]
    else:
        rows = list(corpus)
    return sorted(rows, key=lambda r: str(r.get("id")))


# ===========================================================================
# Generation
# ===========================================================================


async def _propose_one(
    agent: Agent[None, str], sem: asyncio.Semaphore, row: dict
) -> dict:
    """One LLM call → one proposed base slug for one guide.

    Never raises: every failure route (timeout, provider error, a shape the
    model missed twice) ends at the deterministic ``service-{service_ref}``
    fallback, because a 169-row run must not die on one bad response.
    """
    cid = str(row.get("id"))
    service_ref = (row.get("service_ref") or "").strip()
    clean_title = strip_guide_title(row.get("title") or "")
    fallback = normalize_slug(f"service-{service_ref or cid[:8]}") or f"service-{cid[:8]}"

    if not clean_title:
        return {
            "content_id": cid,
            "base": fallback,
            "fallback": True,
            "reason": "empty title after trimming",
            "tokens": (0, 0, 0),
            "model": None,
        }

    async with sem:
        try:
            result = await asyncio.wait_for(
                agent.run(
                    _render_user_msg(clean_title, row.get("provider_name") or ""),
                    usage_limits=_LIMITS,
                ),
                timeout=_LLM_TIMEOUT_S,
            )
        except Exception as e:  # noqa: BLE001
            return {
                "content_id": cid,
                "base": fallback,
                "fallback": True,
                "reason": f"{type(e).__name__}: {e}",
                "tokens": (0, 0, 0),
                "model": None,
            }

    # The output_validator already normalised + shape-checked; re-run both here
    # so this function's contract holds even if the validator is ever relaxed.
    base = normalize_slug(result.output)
    err = slug_shape_error(base)
    if err:
        return {
            "content_id": cid,
            "base": fallback,
            "fallback": True,
            "reason": err,
            "tokens": _usage_of(result),
            "model": _model_from_result(result),
        }
    return {
        "content_id": cid,
        "base": base,
        "fallback": False,
        "reason": None,
        "tokens": _usage_of(result),
        "model": _model_from_result(result),
    }


async def build_plan(
    client, rows: list[dict], existing: dict[str, Optional[str]], taken: set[str]
) -> tuple[list[dict], dict]:
    """Compute the final slug for every row that needs one.

    Two phases on purpose. The LLM calls fan out concurrently (phase 1) because
    they are independent and 169 sequential round-trips is minutes of nothing;
    the collision walk (phase 2) is then strictly sequential over the id-sorted
    list, so which call returned first cannot change which guide keeps the bare
    slug and which gets ``-2``.

    Returns ``(payloads, stats)``. ``payloads`` rows are ready to upsert and
    carry a ``_display`` block the caller prints.
    """
    stats = {
        "considered": 0,
        "already": 0,
        "new": 0,
        "collision": 0,
        "fallback": 0,
        "in": 0,
        "out": 0,
        "reasoning": 0,
    }
    models_seen: dict[str, int] = {}

    todo: list[dict] = []
    for row in rows:
        stats["considered"] += 1
        cid = str(row.get("id"))
        # Never rewrite an existing slug — URLs are permanent. The whole row is
        # skipped, rank and indexable included; see the module docstring.
        if existing.get(cid):
            stats["already"] += 1
            continue
        todo.append(row)

    if not todo:
        return [], stats

    agent = _build_agent()
    sem = asyncio.Semaphore(_CONCURRENCY)
    proposals = await asyncio.gather(*(_propose_one(agent, sem, r) for r in todo))
    by_id = {p["content_id"]: p for p in proposals}

    payloads: list[dict] = []
    now_iso = datetime.now(timezone.utc).isoformat()
    for row in todo:  # already id-sorted by select_targets
        cid = str(row.get("id"))
        p = by_id[cid]
        ti, to, tr = p["tokens"]
        stats["in"] += ti
        stats["out"] += to
        stats["reasoning"] += tr
        if p["model"]:
            models_seen[p["model"]] = models_seen.get(p["model"], 0) + 1
        if p["fallback"]:
            stats["fallback"] += 1

        final = _dedupe(p["base"], taken)
        if final != p["base"]:
            stats["collision"] += 1
        taken.add(final)
        stats["new"] += 1

        payloads.append(
            {
                "content_type": CONTENT_TYPE,
                "content_id": cid,
                "slug": final,
                "rank": row.get("most_used_rank"),
                "indexable": True,
                "updated_at": now_iso,
                # Print-only; stripped before the upsert.
                "_display": {
                    "rank": row.get("most_used_rank"),
                    "service_ref": row.get("service_ref") or "",
                    "title": strip_guide_title(row.get("title") or ""),
                    "fallback": p["fallback"],
                    "reason": p["reason"],
                },
            }
        )

    stats["models"] = models_seen  # type: ignore[assignment]
    return payloads, stats


# ===========================================================================
# Report
# ===========================================================================


def _print_table(payloads: list[dict]) -> None:
    """The reviewable artifact: one line per proposed URL, lowest rank first.

    Column order is rank · service_ref · slug · TITLE, with the Arabic LAST.
    The plan lists the title before the slug, but a terminal applies bidi
    reordering to a line that mixes scripts, and an Arabic column in the middle
    visually scrambles everything to its right — which is exactly the column a
    reviewer is here to read. Arabic at the end of the line keeps the ASCII
    columns aligned and readable.
    """
    print(
        f"\n  {'rank':>5}  {'service_ref':<12}  {'proposed slug':<48}  title"
    )
    print(f"  {'-' * 5}  {'-' * 12}  {'-' * 48}  {'-' * 40}")
    for p in sorted(
        payloads,
        key=lambda x: (
            x["_display"]["rank"] if x["_display"]["rank"] is not None else 10**9,
            x["content_id"],
        ),
    ):
        d = p["_display"]
        rank = d["rank"] if d["rank"] is not None else "-"
        # The '*' goes INSIDE the padded field, not after it — appended, it
        # shifts the title column on exactly the rows a reviewer most wants to
        # line up against the others.
        cell = p["slug"] + (" *" if d["fallback"] else "")
        print(
            f"  {str(rank):>5}  {d['service_ref']:<12}  {cell:<48}  "
            f"{d['title'][:64]}"
        )


def _print_failures(payloads: list[dict]) -> None:
    """Why each ``service-{ref}`` fallback happened — a fallback is a model
    failure worth reading, not a normal outcome."""
    bad = [p for p in payloads if p["_display"]["fallback"]]
    if not bad:
        return
    print(f"\n  * {len(bad)} deterministic fallback(s):")
    for p in bad:
        print(f"    {p['slug']:<40}  <-  {p['_display']['reason']}")


# ===========================================================================
# Modes
# ===========================================================================


async def run_publish(
    apply: bool, limit: Optional[int], only_ids: Optional[set[str]], ids_file: str | None
) -> None:
    client = get_supabase_client()
    try:
        pricing.load_pricing(client)
    except Exception:  # noqa: BLE001
        pass

    mode = "APPLY" if apply else "DRY-RUN"
    scope = (
        f"{len(only_ids)} ids from {ids_file}"
        if only_ids is not None
        else (f"the {limit} most-used guides" if limit else "ALL canonical guides")
    )
    print("=" * 78)
    print(f"build_compliance_slugs — mode={mode}, content_type='{CONTENT_TYPE}'")
    print(f"  scope: {scope}")
    print("=" * 78)

    existing, taken = _load_existing(client)
    corpus = _load_corpus(client)
    rows = select_targets(corpus, limit, only_ids)
    print(
        f"  corpus (service_guides): {len(corpus)} canonical guides\n"
        f"  sidecar rows           : {len(existing)} "
        f"({len(taken)} already slugged)\n"
        f"  selected this run      : {len(rows)}"
    )
    if only_ids is not None:
        missing = only_ids - {str(r.get("id")) for r in corpus}
        if missing:
            print(
                f"  ⚠ {len(missing)} id(s) from {ids_file} are not canonical guides "
                f"— ignored (sample: {sorted(missing)[:3]})"
            )
    if not rows:
        print("\n  nothing selected. Done.\n")
        return

    t0 = time.perf_counter()
    payloads, stats = await build_plan(client, rows, existing, taken)
    dt = time.perf_counter() - t0

    print(
        f"\n  considered         : {stats['considered']}\n"
        f"  already slugged    : {stats['already']}   (skipped — URLs are permanent)\n"
        f"  new slugs          : {stats['new']}\n"
        f"    - collisions     : {stats['collision']}\n"
        f"    - LLM fallbacks  : {stats['fallback']}"
    )
    if payloads:
        _print_table(payloads)
        _print_failures(payloads)
        cost = cost_usd(
            _PRIMARY_MODEL, stats["in"], stats["out"], stats["reasoning"]
        )
        models = stats.get("models") or {}
        model_str = (
            ", ".join(f"{m} x{n}" for m, n in sorted(models.items()))
            or f"{_PRIMARY_MODEL} (no usage reported)"
        )
        print(
            f"\n  model              : {model_str}\n"
            f"  tokens             : in={stats['in']} out={stats['out']} "
            f"reasoning={stats['reasoning']}\n"
            f"  cost               : ~${cost:.4f} at {_PRIMARY_MODEL} rates\n"
            f"  latency            : {dt:.1f}s ({_CONCURRENCY}-way concurrency)"
        )

    if apply and payloads:
        # Strip the print-only block; the sidecar gets exactly four columns + PK.
        clean = [{k: v for k, v in p.items() if k != "_display"} for p in payloads]
        written = _upsert(client, clean)
        print(
            f"\n  APPLIED: upserted {written} '{CONTENT_TYPE}' row(s) "
            f"(slug/rank/indexable/updated_at only — seo_tier + gate_override "
            f"untouched)."
        )
        print(
            "  Next: purge the ISR cache for /compliance, or the baked pages "
            "keep serving the empty wing."
        )
    elif payloads:
        print(
            f"\n  DRY-RUN: would upsert {len(payloads)} row(s). "
            f"Nothing was written — re-run with --apply to persist."
        )
    else:
        print("\n  nothing to write (every selected guide already has a slug).")
    print()


def run_unpublish(ids: set[str], apply: bool, ids_file: str) -> None:
    """Clear ``slug`` on the given guide ids — the reverse of a publish. See the
    module docstring for why this is an UPDATE and not a DELETE.

    The filter is pinned to ``content_type='compliance'`` AND ``content_id in
    (...)``, so the blast radius is exactly the intersection — no other wing can
    be caught by it even if the file holds foreign ids, and the retired wing's
    ``'service'`` rows are unreachable from here by construction.

    Ids are bucketed three ways so the dry-run is honest: ids that carry a slug
    today (the ones that get cleared), ids whose sidecar row exists but is
    already unpublished, and ids with no sidecar row at all (a typo, or an id
    from the wrong corpus). Only the first bucket is written, which makes a
    re-run of the same file a no-op rather than an error.
    """
    client = get_supabase_client()
    existing, _taken = _load_existing(client)

    to_clear = sorted(cid for cid in ids if existing.get(cid))
    already = sorted(cid for cid in ids if cid in existing and not existing.get(cid))
    unknown = sorted(cid for cid in ids if cid not in existing)

    mode = "APPLY" if apply else "DRY-RUN"
    print("=" * 78)
    print(
        f"build_compliance_slugs — mode={mode}, action=UNPUBLISH, "
        f"content_type='{CONTENT_TYPE}'"
    )
    print(f"  {len(ids)} ids from {ids_file}")
    print("=" * 78)
    print(
        f"  currently published  : {len(to_clear)}   <- slug would be cleared\n"
        f"  already unpublished  : {len(already)}   (sidecar row exists, slug NULL)\n"
        f"  no sidecar row       : {len(unknown)}   (never published / wrong corpus)"
    )
    if unknown:
        print("  sample unknown ids:")
        for cid in unknown[:5]:
            print(f"    {cid}")
    if to_clear:
        print("  slugs to clear:")
        for cid in to_clear[:20]:
            print(f"    {existing[cid]!r:<46}  <-  {cid}")

    if apply and to_clear:
        now_iso = datetime.now(timezone.utc).isoformat()
        cleared = 0
        for i in range(0, len(to_clear), _ID_CHUNK):
            chunk = to_clear[i : i + _ID_CHUNK]
            (
                client.table("seo_item_meta")
                .update({"slug": None, "updated_at": now_iso})
                .eq("content_type", CONTENT_TYPE)
                .in_("content_id", chunk)
                .execute()
            )
            cleared += len(chunk)
        print(
            f"\n  APPLIED: cleared {cleared} '{CONTENT_TYPE}' slug(s). "
            f"seo_tier / gate_override untouched, so a re-publish restores the "
            f"guide's gating."
        )
        print("  Next: purge the ISR cache — a baked page outlives its slug.")
    elif to_clear:
        print(
            f"\n  DRY-RUN: would clear {len(to_clear)} slug(s) "
            f"(pass --apply to write)."
        )
    else:
        print("\n  nothing to clear (none of these ids is published right now).")
    print()


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Publish (or un-publish) seo_item_meta slugs for the "
        "/compliance service-guides wing."
    )
    ap.add_argument(
        "--apply",
        action="store_true",
        help="actually write (DEFAULT is a dry-run that writes nothing)",
    )
    ap.add_argument(
        "--limit",
        type=int,
        metavar="N",
        help="take the N guides with the LOWEST most_used_rank (= most used). "
        "This is how the wing ships incrementally: unslugged guides are "
        "invisible to every endpoint and to the sitemap.",
    )
    ap.add_argument(
        "--ids-file",
        metavar="PATH",
        help="publish (or, with --unpublish, un-publish) ONLY these "
        "service_guides ids (one per line; blank lines and '#' comments "
        "ignored).",
    )
    ap.add_argument(
        "--unpublish",
        action="store_true",
        help="REVERSE a publish: clear the slug on the --ids-file ids so their "
        "pages 404 again. Requires --ids-file. Never a DELETE — seo_tier / "
        "gate_override survive, so a re-publish restores the guide's gating. "
        "There is deliberately no --unpublish-all.",
    )
    args = ap.parse_args()

    if args.unpublish and not args.ids_file:
        ap.error(
            "--unpublish requires --ids-file. There is deliberately no "
            "--unpublish-all for this wing — retiring published URLs takes an "
            "explicit id list."
        )
    if args.unpublish and args.limit is not None:
        ap.error(
            "--limit is a publish-side selector (N most-used guides) and means "
            "nothing when un-publishing. Name the ids in --ids-file."
        )
    if args.ids_file and args.limit is not None:
        ap.error(
            "--ids-file and --limit are two different ways to choose the same "
            "thing. Pass one: --limit N for the N most-used, --ids-file for an "
            "explicit set."
        )

    only_ids: Optional[set[str]] = None
    if args.ids_file:
        raw = Path(args.ids_file).read_text(encoding="utf-8").splitlines()
        only_ids = {
            ln.strip() for ln in raw if ln.strip() and not ln.lstrip().startswith("#")
        }
        if not only_ids:
            ap.error(f"--ids-file {args.ids_file} contained no ids")

    if args.unpublish:
        # Guaranteed non-empty: --unpublish requires --ids-file, and the parse
        # above errors out on a file that yielded no ids.
        assert only_ids is not None
        run_unpublish(only_ids, args.apply, args.ids_file)
        return

    limit = args.limit if (args.limit is not None and args.limit > 0) else None
    asyncio.run(run_publish(args.apply, limit, only_ids, args.ids_file))


if __name__ == "__main__":
    main()
