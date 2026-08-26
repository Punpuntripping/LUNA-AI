"""LLM channel extraction for the /compliance service-guide titles.

Reads the guide corpus (``service_guides`` ⋈ ``services``, canonical rows only)
and writes, per guide, the DELIVERY CHANNEL a reader would search for — «بوابة
ناجز», «منصة بلدي», «منصة اعتماد» — into the app-owned sidecar
``service_guide_channels``, together with the composed title that
``library_compliance_v`` then serves in place of the corpus one.

Sibling of ``scripts/build_compliance_slugs.py``: same model policy, same
concurrency, same dry-run-by-default discipline, same "never raise on one bad
row" contract. Read that file's header first. What is DIFFERENT is what the
answer IS and what happens when it is wrong.

WHY A MODEL, WHEN THE BODY HAS A «قناة التقديم» FIELD
-----------------------------------------------------
Because the field is present on 247 of 533 guides and its values are prose, not
data: «بوابة ناجز الإلكترونية (najiz.sa).», «منصة "بلدي" الإلكترونية», «منصة
بلدي (النظام الموحد لوزارة البلديات والإسكان والمجتمعات)», and — on a large
minority — non-answers that name no brand at all: «الموقع الرسمي للصندوق»,
«إلكترونيًا عبر موقع الهيئة», «إلكتروني (افتراضي)». Meanwhile 124 of وزارة
العدل's 130 guides name ناجز somewhere in the body while only 84 carry the
field. Deciding "is there a brand here, and what is it called" is judgement,
and a regex that tried would publish «… في الموقع الرسمي» on an indexed page.

⚠ AND NOT ``services.service_url``, WHICH IS THE TRAP
-----------------------------------------------------
The obvious source is the host, and it is wrong in the single most common case:
114 of the 124 ناجز guides sit on `moj.gov.sa`, the ministry's own domain. Host
derivation names the ministry on all of them and finds ناجز on 10.

THE THREE GATES, AND WHY A BAD ANSWER IS CHEAP
-----------------------------------------------
Every proposal passes ``shared/library/guide_titles`` before it is written:

  1. ``normalize_channel`` — parenthetical gloss, quotes, trailing full stop and
     the redundant «الإلكترونية» removed.
  2. ``channel_shape_error`` — ≤4 words, ≤32 chars, no Latin, no digits, and NOT
     a generic phrase («الموقع الرسمي», «البوابة الإلكترونية», …).
  4. ``brand_already_in_title`` — the brand must NOT already be in the guide's
     own title. Grounding asks "is this word in the body?", and a service's own
     name always is, so a model that recycles it as a portal sails through gate
     3: «إصدار ترخيص صناعي» → «منصة صناعي», «رخصة فال» → «منصة فال». Same gate
     stops «… عبر ناجز» gaining «في بوابة ناجز».
  3. ``channel_is_grounded`` — THE ONE THAT MATTERS. The brand must appear in
     OUR OWN guide body. A model asked "which portal delivers this?" will answer
     «بوابة أبشر» for a service it has merely seen near أبشر in training data;
     an invented portal in a published `<title>` is a factual error on an
     indexed page. Ungrounded ⇒ discarded.

A guide that fails any gate is not a failure — it falls back to its issuing
entity («… في الهيئة العامة للأوقاف»), which is always true and still beats the
«في السعودية» it replaces. So the worst case of a bad run is a duller title,
never a wrong one.

⚠ THE TITLE IS NOT PERMANENT — THE SLUG IS
-------------------------------------------
This is the opposite of ``build_compliance_slugs.py``, and the distinction is
the whole reason the two are separate scripts. A slug is a URL: written once,
never rewritten, a promise to Google and to whoever bookmarked it. A title is
COPY. Re-running this script re-derives every title, and that is intended —
which is why the write is an UPSERT over all columns rather than a fill-blanks
merge, and why there is a ``--reset`` that clears the sidecar instead of an
``--unpublish``. Nothing here can change a URL: this script never touches
``seo_item_meta``.

Run from the repo root:
  python scripts/build_guide_channels.py                    # dry-run, all 533
  python scripts/build_guide_channels.py --limit 40         # dry-run, a sample
  python scripts/build_guide_channels.py --apply            # write all
  python scripts/build_guide_channels.py --ids-file ids.txt --apply
  python scripts/build_guide_channels.py --reset --apply    # clear the sidecar
  python scripts/build_guide_channels.py --report out.md    # dry-run + a file

Env: SUPABASE_URL / SUPABASE_SERVICE_KEY (service role — the sidecar is deny-all
for anon/authenticated) + the agent provider keys (ALIBABA_API_KEY_GLOBAL /
OPENROUTER_API_KEY), via ``shared.config`` / ``shared.db.client`` / python-dotenv.

⚠ AFTER ``--apply``: refresh the BM25 corpus and purge ISR. This script does
neither. `select public.refresh_search_index('compliance');` then
`select public.refresh_bm25_stats('compliance');`, then POST
`{frontend}/api/revalidate` for `/compliance`, `/compliance/page/N` and
`/sitemaps/compliance`. The DETAIL pages re-render on demand and need no purge.
"""
from __future__ import annotations

import argparse
import asyncio
import json
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
from shared.library.guide_titles import (
    attested_channels,
    brand_already_in_title,
    canonicalize_channels,
    channel_is_grounded,
    channel_shape_error,
    compose_guide_title,
    normalize_channel,
)

try:
    from shared.observability import configure_logfire

    configure_logfire()
except Exception:  # noqa: BLE001
    pass


SIDECAR = "service_guide_channels"


def _fold_brand(channel: str) -> str:
    from shared.library.guide_titles import _fold, channel_brand

    return _fold(channel_brand(channel or ""))

# Same cell as the slug namer: this is a short, mechanical extraction over text
# we already own, not a reasoning task.
_POLICY = ModelPolicy("tier_2", primary="deepseek")
_LIMITS = UsageLimits(output_tokens_limit=4_000, request_limit=2)
_CONCURRENCY = 6
_LLM_TIMEOUT_S = 90

# How much body the model sees. The channel is always named in the abstract and
# the «قناة التقديم» block, both of which sit in the first screenful; sending
# the whole guide would multiply the token bill by ~20 for no extra signal.
_BODY_CHARS = 1_600

_HOLE_RE = re.compile(r"^[ \t]*\d+_\d+[ \t]*$", re.M)
_CHANNEL_FIELD_RE = re.compile(r"\*\*قناة التقديم:\*\*[ \t]*([^\n]+)")

_SYSTEM_PROMPT = """\
You identify the ELECTRONIC CHANNEL through which a Saudi government service is \
submitted, from an Arabic guide about that service.

Reply with ONLY a JSON object, nothing else:
  {"channel": "بوابة ناجز"}   or   {"channel": null}

RULES
1. The channel is the PORTAL / PLATFORM / APP the citizen actually uses, as \
Saudis know it by name: بوابة ناجز · منصة بلدي · منصة اعتماد · منصة مساند · \
منصة قوى · بوابة أبشر · تطبيق صحتي · منصة هدف.
2. Include the classifier: "بوابة ناجز", not "ناجز".
3. 2-3 words. No domain names, no parentheses, no Latin letters, no digits, no \
trailing punctuation, and never the word "الإلكترونية".
4. It must be a NAMED BRAND — a PROPER NOUN. A phrase that merely DESCRIBES a portal is not a channel, however official it sounds. Answer {"channel": null} for "الموقع الرسمي" · "البوابة الإلكترونية" · "المنصة الإلكترونية للبرنامج" · "البوابة الإلكترونية للوزارة" · "الموقع الرسمي للصندوق" · "بوابة الخدمات الإلكترونية" · "منصة الخدمات التجارية" · "إلكترونيًا عبر موقع الهيئة" · "حضوري". THE TEST: drop the classifier (بوابة/منصة/موقع/تطبيق). If the very next word is a common word — الخدمات، الإلكترونية، الرسمي، الوطني، للوزارة، للبرنامج — then nothing was NAMED and the answer is null. A brand follows the classifier IMMEDIATELY: ناجز، بلدي، اعتماد، مساند، قوى، أبشر، صحتي، هدف، مدينتي.
5. NEVER add a country or a place to the name. "بوابة ادرس", never "بوابة ادرس في السعودية".
6. The name MUST appear in the text you are given. Never infer a portal from \
general knowledge about Saudi e-government. If the text does not name one, \
answer {"channel": null}.
7. If the text names several, choose the one THIS service is submitted through \
— usually the one in "قناة التقديم" or the one the steps tell the user to open.

Answering null is a perfectly good answer and is much better than guessing.\
"""


def _strip_holes(md: str) -> str:
    return _HOLE_RE.sub("", md or "")


def _build_agent() -> Agent[None, str]:
    """The channel extractor: plain ``str`` output, one shape-validating retry.

    ``temperature=0`` for the same reason the slug namer sets it — the workflow
    is "read a dry-run table, then write it", and a temperature that moves the
    answer between the preview and the apply makes the review theatre.
    """
    agent: Agent[None, str] = Agent(
        build_fallback_model(_POLICY),
        name="guide_channel_extractor",
        instructions=_SYSTEM_PROMPT,
        retries=1,
        model_settings={"temperature": 0.0},
    )

    @agent.output_validator
    def _validate(text: str) -> str:
        raw = (text or "").strip()
        # Cells sometimes fence the JSON. Accept that, reject prose.
        fenced = re.search(r"\{.*\}", raw, re.S)
        if not fenced:
            raise ModelRetry(
                'Reply with ONLY a JSON object: {"channel": "بوابة ناجز"} or '
                '{"channel": null}.'
            )
        try:
            parsed = json.loads(fenced.group(0))
        except Exception:  # noqa: BLE001
            raise ModelRetry(
                'That was not valid JSON. Reply with exactly {"channel": "..."} '
                'or {"channel": null}.'
            )
        if "channel" not in parsed:
            raise ModelRetry('The JSON must have a "channel" key.')
        value = parsed["channel"]
        if value is None:
            return ""
        if not isinstance(value, str):
            raise ModelRetry('"channel" must be a string or null.')

        cleaned = normalize_channel(value)
        if not cleaned:
            return ""
        err = channel_shape_error(cleaned)
        if err:
            # A generic phrase is not a retry-worthy mistake — it is the model
            # correctly reporting "no brand here" in the wrong format. Convert
            # it to null rather than burning a second call on it.
            if err.startswith("generic phrase"):
                return ""
            raise ModelRetry(
                f"{err}. Reply with a short branded portal name of 2-3 words, "
                f'or {{"channel": null}} if there is no branded portal.'
            )
        return cleaned

    return agent


def _render_user_msg(row: dict) -> str:
    body = _strip_holes(row.get("guide_md") or "")
    field = _CHANNEL_FIELD_RE.search(body)
    field_line = field.group(1).strip() if field else "(غير مذكورة)"
    return (
        f"الخدمة: {row.get('title') or ''}\n"
        f"الجهة: {row.get('provider_name') or '(غير معروفة)'}\n"
        f"قناة التقديم كما وردت في الدليل: {field_line}\n\n"
        f"--- بداية الدليل ---\n{body[:_BODY_CHARS]}\n--- نهاية المقتطف ---"
    )


def _usage_of(result: Any) -> tuple[int, int, int]:
    try:
        u = result.usage()
        return (
            int(getattr(u, "input_tokens", 0) or 0),
            int(getattr(u, "output_tokens", 0) or 0),
            int(getattr(u, "details", {}).get("reasoning_tokens", 0) or 0),
        )
    except Exception:  # noqa: BLE001
        return (0, 0, 0)


def _model_from_result(result: Any) -> Optional[str]:
    try:
        return getattr(result, "_model_name", None) or getattr(
            result.response, "model_name", None
        )
    except Exception:  # noqa: BLE001
        return None


async def _extract_one(agent: Agent[None, str], sem: asyncio.Semaphore, row: dict) -> dict:
    """One LLM call → one channel (or none) for one guide.

    Never raises. Every failure route — timeout, provider error, a shape missed
    twice, a brand the body does not contain — ends at ``channel=None``, which
    the caller turns into the entity fallback. A 533-row run must not die on one
    row, and there is no such thing as a fatal answer here.
    """
    cid = str(row.get("id"))
    corpus_title = (row.get("title") or "").strip()
    provider = (row.get("provider_name") or "").strip()
    base = {
        "content_id": cid,
        "in_title": False,
        "slug": row.get("slug"),
        "corpus_title": corpus_title,
        "provider": provider,
        "tokens": (0, 0, 0),
        "model": None,
    }

    if not corpus_title:
        return {**base, "channel": None, "reason": "empty corpus title"}

    async with sem:
        try:
            result = await asyncio.wait_for(
                agent.run(_render_user_msg(row), usage_limits=_LIMITS),
                timeout=_LLM_TIMEOUT_S,
            )
        except Exception as e:  # noqa: BLE001
            return {**base, "channel": None, "reason": f"{type(e).__name__}: {e}"}

    tokens = _usage_of(result)
    model = _model_from_result(result)
    proposal = normalize_channel(result.output)

    if not proposal:
        return {**base, "channel": None, "reason": "no branded channel", "tokens": tokens, "model": model}

    err = channel_shape_error(proposal)
    if err:
        return {**base, "channel": None, "reason": err, "tokens": tokens, "model": model}

    # ⚠ THE GROUNDING GATE. See the header — this is what keeps a hallucinated
    # portal out of 533 indexable <title> tags.
    if not channel_is_grounded(proposal, row.get("guide_md")):
        return {
            **base,
            "channel": None,
            "reason": f"NOT GROUNDED in the body: «{proposal}»",
            "tokens": tokens,
            "model": model,
        }

    # ⚠ GATE 4 — the one grounding cannot be. The service's OWN NAME is always
    # in the body, so a model that recycles it as a portal passes gate 3:
    # «إصدار ترخيص صناعي» → «منصة صناعي». It also catches the honest case of a
    # title that already names its channel, which must not have it appended
    # twice. 27 of 533 titles needed this on the first apply.
    # ⚠ GATE 4 FLAGS, IT DOES NOT DECIDE. Whether a brand that appears in its own
    # title is invented («منصة صناعي») or merely already-stated («… عبر ناجز»)
    # cannot be known from ONE guide — it takes the whole run's counts. The
    # second pass in `_settle_in_title` decides; destroying the proposal here
    # would throw away the evidence it needs.
    return {
        **base,
        "channel": proposal,
        "in_title": brand_already_in_title(proposal, corpus_title),
        "reason": None,
        "tokens": tokens,
        "model": model,
    }


# ─── Corpus read ──────────────────────────────────────────────────────────────
def _load_corpus(client, ids: Optional[list[str]], limit: Optional[int]) -> list[dict]:
    """Canonical guides ⋈ services ⋈ their published slug, paged.

    The two predicates ``library_compliance_v`` defines are stated here
    explicitly rather than read off the view — same reasoning as
    ``build_compliance_slugs.py``: this script sits upstream of the backend and
    must not depend on a migration having been applied.
    """
    rows: list[dict] = []
    page, size = 0, 500
    while True:
        q = (
            client.table("service_guides")
            .select("id, title, guide_md, service_ref, is_canonical, services!inner(provider_name)")
            .eq("is_canonical", True)
            .order("id")
            .range(page * size, page * size + size - 1)
        )
        res = q.execute()
        batch = res.data or []
        if not batch:
            break
        rows.extend(batch)
        if len(batch) < size:
            break
        page += 1

    flat: list[dict] = []
    for r in rows:
        flat.append(
            {
                "id": r.get("id"),
                "title": r.get("title"),
                "guide_md": r.get("guide_md"),
                "service_ref": r.get("service_ref"),
                "provider_name": (r.get("services") or {}).get("provider_name"),
            }
        )

    # Attach the published slug — display only, so the operator reviewing a
    # dry-run can open the page a proposed title belongs to.
    slugs: dict[str, str] = {}
    all_ids = [str(x["id"]) for x in flat]
    for i in range(0, len(all_ids), 150):
        chunk = all_ids[i : i + 150]
        got = (
            client.table("seo_item_meta")
            .select("content_id, slug")
            .eq("content_type", "compliance")
            .in_("content_id", chunk)
            .execute()
        )
        for row in got.data or []:
            if row.get("slug"):
                slugs[str(row["content_id"])] = row["slug"]
    for x in flat:
        x["slug"] = slugs.get(str(x["id"]))

    if ids:
        wanted = {i.strip() for i in ids if i.strip()}
        flat = [x for x in flat if str(x["id"]) in wanted]
    if limit is not None:
        flat = flat[:limit]
    return flat


def _read_ids_file(path: str) -> list[str]:
    out: list[str] = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if s and not s.startswith("#"):
            out.append(s)
    return out


# ─── Report ───────────────────────────────────────────────────────────────────
def _render_report(results: list[dict]) -> str:
    with_channel = [r for r in results if r.get("channel")]
    without = [r for r in results if not r.get("channel")]

    by_channel: dict[str, int] = {}
    for r in with_channel:
        by_channel[r["channel"]] = by_channel.get(r["channel"], 0) + 1

    lines: list[str] = []
    lines.append("# Guide title channel rewrite — proposal\n")
    lines.append(
        f"- guides: **{len(results)}**\n"
        f"- with a branded channel: **{len(with_channel)}**\n"
        f"- falling back to the entity: **{len(without)}**\n"
    )

    lines.append("\n## Channels found\n")
    lines.append("| channel | guides |")
    lines.append("|---|---:|")
    for ch, n in sorted(by_channel.items(), key=lambda kv: (-kv[1], kv[0])):
        lines.append(f"| {ch} | {n} |")

    lines.append("\n## Titles — with a channel\n")
    lines.append("| slug | before | after |")
    lines.append("|---|---|---|")
    for r in sorted(with_channel, key=lambda r: r.get("channel") or ""):
        lines.append(
            f"| `{r.get('slug') or '—'}` | {r['corpus_title']} | **{r['new_title']}** |"
        )

    lines.append("\n## Titles — entity fallback\n")
    lines.append("| slug | after | why no channel |")
    lines.append("|---|---|---|")
    for r in without:
        lines.append(
            f"| `{r.get('slug') or '—'}` | {r['new_title']} | {r.get('reason') or 'no branded channel'} |"
        )

    ungrounded = [r for r in without if (r.get("reason") or "").startswith("NOT GROUNDED")]
    if ungrounded:
        lines.append(
            f"\n## ⚠ Rejected as ungrounded ({len(ungrounded)})\n\n"
            "The model named a portal that does not appear in our own guide body. "
            "These fell back to the entity.\n"
        )
        for r in ungrounded:
            lines.append(f"- `{r.get('slug') or '—'}` — {r['reason']}")

    return "\n".join(lines) + "\n"


# ─── Write ────────────────────────────────────────────────────────────────────
def _apply(client, results: list[dict]) -> int:
    """UPSERT the sidecar. Full overwrite per row — a title is copy, not a URL.

    Chunked because a 533-row payload carrying titles is comfortably past what
    PostgREST wants in one request.
    """
    now = datetime.now(timezone.utc).isoformat()
    payload = [
        {
            "guide_id": r["content_id"],
            "channel": r.get("channel"),
            "display_title": r["new_title"],
            "source": "llm" if r.get("channel") else "entity_fallback",
            "reason": r.get("reason"),
            "built_at": now,
        }
        for r in results
    ]
    written = 0
    for i in range(0, len(payload), 100):
        chunk = payload[i : i + 100]
        client.table(SIDECAR).upsert(chunk, on_conflict="guide_id").execute()
        written += len(chunk)
    return written


def _settle_in_title(results: list[dict]) -> None:
    """Decide the gate-4 flagged rows using the corpus's own vote.

    A brand attested by ``MIN_ATTESTATIONS`` other guides — ones that did NOT
    have it in their own title — is a real portal, so the guide KEEPS it and
    ``compose_guide_title`` leaves the title alone (it already names it).
    Anything else was invented out of the title and falls back to the entity.
    """
    attested = attested_channels(
        [r["channel"] for r in results if r.get("channel") and not r.get("in_title")]
    )
    kept = dropped = 0
    for r in results:
        if not (r.get("channel") and r.get("in_title")):
            continue
        if _fold_brand(r["channel"]) in attested:
            r["reason"] = "already named in the title — left as written"
            kept += 1
        else:
            r["reason"] = f"invented from the title: «{r['channel']}»"
            r["channel"] = None
            dropped += 1
    print(f"  in-title proposals: {kept} kept as real portals, {dropped} dropped as invented")


def _recompose(client) -> None:
    """Rebuild every ``display_title`` from the STORED channel — no LLM call.

    Exists because the composition rules are code and code changes: when
    ``compose_guide_title`` or a gate is fixed, the 533 titles already in the
    sidecar are stale, and re-running the extraction to fix formatting would pay
    the model bill again for answers we already have. Re-applies the gates too,
    so a channel that a newly-added rule now rejects falls back to its entity.
    """
    stored: dict[str, dict] = {}
    page = 0
    while True:
        res = (
            client.table(SIDECAR)
            .select("guide_id, channel, source")
            .range(page * 500, page * 500 + 499)
            .execute()
        )
        batch = res.data or []
        if not batch:
            break
        for row in batch:
            stored[str(row["guide_id"])] = row
        if len(batch) < 500:
            break
        page += 1

    corpus = _load_corpus(client, None, None)
    results: list[dict] = []
    dropped = 0
    for row in corpus:
        cid = str(row["id"])
        prior = stored.get(cid) or {}
        channel = (prior.get("channel") or "").strip() or None
        reason = None
        if channel and brand_already_in_title(channel, row.get("title")):
            channel, reason = None, "brand already in the title"
            dropped += 1
        results.append(
            {
                "content_id": cid,
                "slug": row.get("slug"),
                "corpus_title": (row.get("title") or "").strip(),
                "provider": (row.get("provider_name") or "").strip(),
                "channel": channel,
                "reason": reason,
                "tokens": (0, 0, 0),
                "model": None,
            }
        )

    canon = canonicalize_channels([r["channel"] for r in results if r.get("channel")])
    for r in results:
        if r.get("channel"):
            r["channel"] = canon.get(r["channel"], r["channel"])
        r["new_title"] = compose_guide_title(
            r["corpus_title"], r.get("channel") or r.get("provider") or ""
        )

    written = _apply(client, results)
    with_channel = sum(1 for r in results if r.get("channel"))
    print(f"RECOMPOSED {written} title(s) from stored channels — no model call.")
    print(f"  with a channel  : {with_channel}")
    print(f"  entity fallback : {len(results) - with_channel}")
    print(f"  channels dropped by the title gate: {dropped}")


async def _run(args) -> None:
    client = get_supabase_client()
    # The pricing registry is DB-backed and starts empty, so `cost_usd` silently
    # returns 0.0 until it is loaded — a run that reports "~$0.0000" has simply
    # not been told the rates.
    try:
        pricing.load_pricing(client)
    except Exception:  # noqa: BLE001
        pass

    if args.recompose:
        if not args.apply:
            print("DRY-RUN: --recompose rewrites every display_title. Add --apply.")
            return
        _recompose(client)
        return

    if args.reset:
        if not args.apply:
            print("DRY-RUN: would clear every row in", SIDECAR)
            return
        client.table(SIDECAR).delete().neq(
            "guide_id", "00000000-0000-0000-0000-000000000000"
        ).execute()
        print(f"CLEARED {SIDECAR}. Titles fall back to the corpus until you re-run.")
        return

    ids = _read_ids_file(args.ids_file) if args.ids_file else None
    rows = _load_corpus(client, ids, args.limit)
    if not rows:
        print("No guides matched. Nothing to do.")
        return

    print(f"Extracting channels for {len(rows)} guide(s) at {_CONCURRENCY}-way concurrency…")
    agent = _build_agent()
    sem = asyncio.Semaphore(_CONCURRENCY)
    started = time.monotonic()
    results = await asyncio.gather(*(_extract_one(agent, sem, r) for r in rows))
    elapsed = time.monotonic() - started

    # ⚠ SECOND PASS — decide the flagged proposals using the WHOLE run.
    _settle_in_title(results)

    # ⚠ CANONICALISE BEFORE COMPOSING. The extractor sees one guide at a time,
    # so one portal comes back under several classifiers; this vote settles on
    # one spelling per brand across the whole run. Must happen before titles are
    # built, or the wing publishes «منصة بلدي» and «بوابة بلدي» side by side.
    canon = canonicalize_channels([r["channel"] for r in results if r.get("channel")])
    renamed = 0
    for r in results:
        if r.get("channel"):
            winner = canon.get(r["channel"], r["channel"])
            if winner != r["channel"]:
                renamed += 1
                r["channel"] = winner

    # Compose every title through the ONE shared function — including the
    # fallback rows, so a guide with no channel still loses «في السعودية».
    for r in results:
        label = r.get("channel") or r.get("provider") or ""
        r["new_title"] = compose_guide_title(r["corpus_title"], label)

    with_channel = sum(1 for r in results if r.get("channel"))
    changed = sum(1 for r in results if r["new_title"] != r["corpus_title"])
    ungrounded = sum(
        1 for r in results if (r.get("reason") or "").startswith("NOT GROUNDED")
    )
    tin = sum(r["tokens"][0] for r in results)
    tout = sum(r["tokens"][1] for r in results)
    treason = sum(r["tokens"][2] for r in results)
    model = next((r["model"] for r in results if r.get("model")), None)

    by_channel: dict[str, int] = {}
    for r in results:
        if r.get("channel"):
            by_channel[r["channel"]] = by_channel.get(r["channel"], 0) + 1

    print("\n  ── sample ──")
    for r in results[:20]:
        mark = "✓" if r.get("channel") else "·"
        print(f"  {mark} {r['corpus_title']}")
        print(f"      → {r['new_title']}")

    print("\n  ── channels ──")
    for ch, n in sorted(by_channel.items(), key=lambda kv: (-kv[1], kv[0]))[:25]:
        print(f"  {n:>4}  {ch}")

    print(
        f"\n  guides             : {len(results)}"
        f"\n  with a channel     : {with_channel}"
        f"\n  entity fallback    : {len(results) - with_channel}"
        f"\n  title actually new : {changed}"
        f"\n  rejected ungrounded: {ungrounded}"
        f"\n  model              : {model or '(none)'} x{len(results)}"
        f"\n  tokens             : in={tin} out={tout} reasoning={treason}"
    )
    try:
        print(f"  cost               : ~${cost_usd(model, tin, tout):.4f}")
    except Exception:  # noqa: BLE001
        pass
    print(f"  latency            : {elapsed:.1f}s ({_CONCURRENCY}-way concurrency)")

    if args.report:
        Path(args.report).write_text(_render_report(results), encoding="utf-8")
        print(f"  report             : {args.report}")

    if not args.apply:
        print(
            "\n  DRY-RUN — nothing written. Re-run with --apply to write "
            f"{len(results)} row(s) to {SIDECAR}."
        )
        return

    written = _apply(client, results)
    print(f"\n  APPLIED: upserted {written} row(s) into {SIDECAR}.")
    print(
        "  Next: select public.refresh_search_index('compliance'); "
        "select public.refresh_bm25_stats('compliance');  then purge ISR for "
        "/compliance and /sitemaps/compliance."
    )


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Extract the delivery channel per service guide and compose "
        "the public title from it."
    )
    ap.add_argument(
        "--apply",
        action="store_true",
        help="actually write (DEFAULT is a dry-run that writes nothing)",
    )
    ap.add_argument("--limit", type=int, metavar="N", help="only the first N guides (by id)")
    ap.add_argument(
        "--ids-file",
        metavar="PATH",
        help="only these service_guides ids (one per line; '#' comments ignored)",
    )
    ap.add_argument(
        "--report",
        metavar="PATH",
        help="write a full before/after markdown table to PATH (works in dry-run)",
    )
    ap.add_argument(
        "--recompose",
        action="store_true",
        help="rebuild every display_title from the ALREADY-STORED channel, "
        "re-applying the gates. No LLM call, no cost. Use after changing a "
        "composition rule. Requires --apply.",
    )
    ap.add_argument(
        "--reset",
        action="store_true",
        help="clear the sidecar so every title falls back to the corpus. "
        "Requires --apply. This is the reverse of a run.",
    )
    args = ap.parse_args()

    if args.recompose and (args.limit is not None or args.ids_file or args.reset):
        ap.error("--recompose rebuilds the whole sidecar; it takes no other selector.")
    if args.reset and (args.limit is not None or args.ids_file):
        ap.error("--reset clears the whole sidecar; it takes no row selector.")
    if args.ids_file and args.limit is not None:
        ap.error("pass one of --ids-file / --limit, not both.")

    asyncio.run(_run(args))


if __name__ == "__main__":
    main()
