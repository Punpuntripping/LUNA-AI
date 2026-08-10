"""Regenerate the mock compaction summaries sitting in production ``convo_context`` rows.

Plan: ``.claude/plans/memory_compaction_agent.md`` §5.2 (step 8 of the build order —
run this LAST, after the real compactor is deployed and validated on a live turn).

WHAT IS BROKEN
--------------
Wave 9's ``compact_conversation`` wrote a hardcoded f-string instead of a summary
(``agents/memory/agent.py`` ``_mock_compaction_summary``)::

    ملخص للمحادثة السابقة: {n} رسالة بين المستخدم والمساعد في المحادثة {uuid}. تمت …

Two count/UUID slots and no content. That string is what the router, the writer and
the attachment summarizer receive **instead of** the compacted turns (plan §1.1).

WHY IT IS REPAIRABLE
--------------------
Compaction never deleted anything. It only advanced
``conversations.compacted_through_message_id`` and inserted the ``convo_context``
row; every message of every compacted span is still in ``messages``. So each mock
summary can be regenerated from rows that still exist — no data was lost, only
never written.

HOW A SPAN IS RECONSTRUCTED
---------------------------
A conversation can hold MORE THAN ONE ``convo_context`` row (plan §1.3 — the second
compaction orphans the first), but ``conversations`` records only the LATEST cutoff.
The older rows' spans are still recoverable, because the mock text embeds its own
message count. Per conversation, ordered by ``created_at``::

    row 1: n1 messages  → span = messages[0 : n1]
    row 2: n2 messages  → span = messages[0 : n1 + n2]      (supersedes row 1)
    …
    row k                → span = messages[0 : Σ n]

and the last row's cumulative total MUST land exactly on
``compacted_through_message_id``. That equality is asserted before anything is
written; when it does not hold the chain is not trustworthy, so only the newest row
is repaired (span = start → cutoff) and the older ones are skipped and reported.

Every span starts at message 0 — which is why ``prior_summary_md`` is always ``""``
here. Superseding matters live, where the earlier turns are gone from the window;
in a backfill the whole span is on the table, so there is nothing "prior" to fold in
and passing a partial summary of the same messages would only confuse the model.

The workspace-item list handed to each row is filtered to items created AT OR BEFORE
that ``convo_context`` row — the compaction could not have been produced by items
that did not exist yet, and letting the model see later items invites it to credit
the span with work it never did.

FAIL-CLOSED (plan §3.5)
-----------------------
``run_convo_compaction`` never raises; it reports ``failed=True``. On failure — or on
an empty ``summary_md`` — the row is SKIPPED and logged. A bad summary is never
written over a bad summary. The row keeps its mock text and stays repairable on the
next run.

COST IS NOT BILLED TO THE USER
------------------------------
This script deliberately does NOT open a ``collect_llm_calls`` scope. That scope both
inserts ``llm_calls`` rows and **quota-settles on exit**
(``agents/utils/usage_sink.py:48-82``) — an operator repair must not appear as a turn
on someone's ledger or spend their points. The cost is computed locally from the
returned token counts and printed instead. If you want it in the ledger, record it as
operator spend, not here.

MASKING (وضع السرية)
--------------------
Runs DETACHED — no turn codec on the ContextVar — so the codec is built explicitly
from each row's ``user_id``, following ``agents/memory/summarize.py:79-97``. LLM-bound
surfaces are encoded, new fakes are persisted BEFORE the call, and ``summary_md`` is
decoded before it is stored (store-real invariant, ``summarize.py:60-77``).

Run from the repo root.

Usage:
  # what would be touched, and whether the span arithmetic checks out — spends nothing
  python scripts/backfill_compaction_summaries.py --no-llm

  # DRY-RUN (the default): generates every summary and prints it, writes NOTHING.
  # ⚠ this DOES call the model and DOES cost money — that is the point of it.
  python scripts/backfill_compaction_summaries.py

  # one conversation, end to end, then commit it
  python scripts/backfill_compaction_summaries.py --conversation-id 4ea14aed-… --live

  # the whole backlog
  python scripts/backfill_compaction_summaries.py --live

Env:
  SUPABASE_URL / SUPABASE_SERVICE_KEY — via shared.config / shared.db.client.
  Whatever ``convo_compactor`` needs for its provider chain (ALIBABA_API_KEY_GLOBAL /
  OPENROUTER_API_KEY) — the same env a chat turn needs.

Exit code: 0 when every target was repaired (or dry-run cleanly), 1 when any target
was skipped — a fail-closed LLM failure or an unreconstructable span.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

# Make the repo root importable when run directly.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Windows consoles default to cp1252, which cannot encode the Arabic summaries this
# script prints — force UTF-8 so the report never dies mid-row.
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001 — older streams / redirected output
    pass

try:
    from dotenv import load_dotenv

    load_dotenv()
except Exception:  # noqa: BLE001 — dotenv is optional; env may already be set
    pass

from shared.db.client import get_supabase_client
from shared.identity import resolve_call_name
from shared.pricing import get_price, load_pricing
from agents.utils.agent_models import cost_usd

logger = logging.getLogger("backfill_compaction")

# The exact literal Wave 9 wrote (agents/memory/agent.py ``_mock_compaction_summary``).
# Both the row selector and the count parser key off it.
MOCK_PREFIX = "ملخص للمحادثة السابقة: "
MOCK_COUNT_RE = re.compile(r"^ملخص للمحادثة السابقة:\s*(\d+)\s")

# ``model_used`` comes back as a real model id when pydantic_ai surfaces the
# FallbackModel head, and as a slot label (``convo_compactor:tier_2``) when it does
# not — see artifact_summarizer/runner.py:157-176. A slot label is not in
# ``model_pricing``, so price those rows at the slot's documented head model
# (_FLASH_MEDIUM → deepseek-v4-flash) and say so in the report.
FALLBACK_PRICING_MODEL = "deepseek-v4-flash"

# Message bodies are passed WHOLE. Input clipping belongs to the compactor's runner
# (plan §3.4); clipping here too would silently diverge from what the live path does.


# ---------------------------------------------------------------------------
# Lazy compactor import
# ---------------------------------------------------------------------------


def _load_compactor():
    """Import ``agents.memory.convo_compactor`` at call time.

    Deliberately not a module-level import: ``--help`` and ``--no-llm`` are useful
    without the agent (and this script may run before the package is deployed), and
    a one-line message beats an import traceback at module load.
    """
    try:
        from agents.memory.convo_compactor import (  # noqa: PLC0415
            CompactionInput,
            build_compactor_deps,
            run_convo_compaction,
        )
    except ImportError as exc:
        raise SystemExit(
            f"agents.memory.convo_compactor is not importable ({exc}).\n"
            "Build/deploy the compactor package first — this script only repairs "
            "data, it does not carry its own summarizer."
        ) from exc
    return CompactionInput, build_compactor_deps, run_convo_compaction


# ---------------------------------------------------------------------------
# Masking (وضع السرية) — detached-codec pattern from summarize.py:79-120
# ---------------------------------------------------------------------------


def _build_codec(supabase, user_id: str):
    """The active turn codec, or one built explicitly for this detached run.

    Mirrors ``agents/memory/summarize.py`` ``_summarize_codec``. In this script
    ``active_codec()`` is always None (no turn on the stack), so the explicit branch
    is the one that runs — it is kept in that shape so the two stay comparable.
    Returns None only when there is no ``user_id`` to build from; encode/decode then
    degrade to passthrough.
    """
    from backend.app.services.masking_service import active_codec, build_turn_codec

    codec = active_codec()
    if codec is not None:
        return codec
    if not user_id:
        return None
    try:
        return build_turn_codec(supabase, user_id)
    except Exception:  # noqa: BLE001
        logger.warning("codec build failed for user_id=%s", user_id, exc_info=True)
        return None


def _enc(codec, text: str) -> str:
    """Encode one LLM-bound surface. Passthrough on None/disabled/error."""
    if codec is None or not text:
        return text
    try:
        return codec.encode(text)
    except Exception:  # noqa: BLE001
        return text


def _dec(codec, text: str) -> str:
    """Decode the produced summary before store (store-real invariant).

    Never gated on the enabled flag — a disabled codec still restores fakes minted
    while masking was previously ON.
    """
    if codec is None or not text:
        return text
    from backend.app.services.masking_service import decode_text

    try:
        return decode_text(codec, text, emit=False)
    except Exception:  # noqa: BLE001
        logger.warning("decode failed; storing the raw model output", exc_info=True)
        return text


def _persist_mappings(supabase, user_id: str, codec) -> None:
    """Flush newly-minted fakes BEFORE the call, so every fake in the prompt is
    decodable afterwards even if this process dies mid-run."""
    if codec is None or not user_id:
        return
    from backend.app.services.masking_service import persist_new_mappings

    try:
        persist_new_mappings(supabase, user_id, codec)
    except Exception:  # noqa: BLE001
        logger.warning("persist_new_mappings failed for user_id=%s", user_id, exc_info=True)


# ---------------------------------------------------------------------------
# Loaders (read-only)
# ---------------------------------------------------------------------------


def fetch_targets(client, conversation_id: str | None) -> list[dict]:
    """Every non-deleted ``convo_context`` row still carrying the Wave 9 mock."""
    q = (
        client.table("workspace_items")
        .select("item_id, conversation_id, user_id, content_md, created_at, wi_seq, metadata")
        .eq("kind", "convo_context")
        .is_("deleted_at", "null")
        .like("content_md", f"{MOCK_PREFIX}%")
        .order("conversation_id")
        .order("created_at")
    )
    if conversation_id:
        q = q.eq("conversation_id", conversation_id)
    resp = q.execute()
    return list(resp.data or [])


def fetch_conversation(client, conversation_id: str) -> dict | None:
    resp = (
        client.table("conversations")
        .select("conversation_id, user_id, compacted_through_message_id, deleted_at")
        .eq("conversation_id", conversation_id)
        .maybe_single()
        .execute()
    )
    return (resp.data or None) if resp else None


def fetch_messages(client, conversation_id: str) -> list[dict]:
    """All messages of one conversation, oldest-first, paginated.

    Ordered ``created_at, message_id`` — the live compactor orders on ``created_at``
    alone, which is not a total order if two rows share a timestamp; the id tiebreak
    makes the reconstruction deterministic across runs.
    """
    rows: list[dict] = []
    page, PAGE = 0, 1000
    while True:
        resp = (
            client.table("messages")
            .select("message_id, role, content, created_at")
            .eq("conversation_id", conversation_id)
            .order("created_at", desc=False)
            .order("message_id", desc=False)
            .range(page * PAGE, page * PAGE + PAGE - 1)
            .execute()
        )
        batch = list(resp.data or [])
        rows.extend(batch)
        if len(batch) < PAGE:
            break
        page += 1
    return rows


def fetch_workspace_items(client, conversation_id: str, user_id: str, before_iso: str) -> list[dict]:
    """The WI list as it stood when this ``convo_context`` row was written.

    ``convo_context`` is excluded (it is the thing being rewritten) and the list is
    cut at ``before_iso`` so the summary cannot credit the compacted span with items
    produced after it.

    The ``.eq("user_id", …)`` filter is load-bearing, exactly as in
    ``agents/router/context.py:_load_workspace_item_summaries``: this client runs as
    ``service_role`` and bypasses RLS, and these titles/summaries go straight into an
    LLM prompt — a foreign row here is a prompt-injection surface, not just a leak.
    """
    resp = (
        client.table("workspace_items")
        .select("wi_seq, kind, title, summary, created_at")
        .eq("conversation_id", conversation_id)
        .eq("user_id", user_id)
        .is_("deleted_at", "null")
        .neq("kind", "convo_context")
        .lte("created_at", before_iso)
        .order("wi_seq")
        .execute()
    )
    return [
        {
            "wi_seq": row.get("wi_seq"),
            "kind": row.get("kind") or "",
            "title": row.get("title") or "",
            "summary": row.get("summary"),
        }
        for row in (resp.data or [])
    ]


def fetch_call_name(client, user_id: str) -> str | None:
    """The name the pipeline addresses this user by — same resolution as the router
    (``shared.identity.resolve_call_name``), so the two can never disagree."""
    if not user_id:
        return None
    try:
        resp = (
            client.table("users")
            .select("preferred_name, full_name_ar")
            .eq("user_id", user_id)
            .maybe_single()
            .execute()
        )
        row = (resp.data or {}) if resp else {}
        return resolve_call_name(row.get("preferred_name"), row.get("full_name_ar"))
    except Exception:  # noqa: BLE001
        logger.warning("call-name load failed for user_id=%s", user_id, exc_info=True)
        return None


# ---------------------------------------------------------------------------
# Span reconstruction
# ---------------------------------------------------------------------------


def _parse_mock_count(content_md: str) -> int | None:
    """The message count Wave 9 baked into the mock, or None if it is not there."""
    m = MOCK_COUNT_RE.match(content_md or "")
    return int(m.group(1)) if m else None


def plan_conversation(rows: list[dict], conversation: dict, messages: list[dict]) -> tuple[list[tuple[dict, int]], list[tuple[dict, str]]]:
    """Assign each mock row the span it was written for.

    Returns ``(repairable, skipped)`` where ``repairable`` is ``[(row, span_len)]``
    in chronological order and ``skipped`` is ``[(row, reason)]``.
    """
    cutoff_id = conversation.get("compacted_through_message_id")
    if not cutoff_id:
        return [], [(r, "conversation has no compacted_through_message_id") for r in rows]

    ids = [m["message_id"] for m in messages]
    try:
        cutoff_count = ids.index(str(cutoff_id)) + 1
    except ValueError:
        return [], [(r, f"cutoff message {cutoff_id} not found in messages") for r in rows]

    # Chain: each row's span ends where its own compaction stopped, which is the
    # running total of the counts baked into the mocks.
    counts = [_parse_mock_count(r.get("content_md") or "") for r in rows]
    cumulative: list[int] = []
    running = 0
    for n in counts:
        if n is None:
            break
        running += n
        cumulative.append(running)

    chain_ok = (
        len(cumulative) == len(rows)
        and cumulative[-1] == cutoff_count
        and all(0 < c <= len(messages) for c in cumulative)
    )
    if chain_ok:
        return list(zip(rows, cumulative)), []

    # Chain does not reconcile — do not guess at the older spans. The newest row is
    # the only one downstream consumers actually read (context.py:221-227 keeps the
    # most recent convo_context), and its span is pinned by the cutoff pointer, so
    # repair that one and report the rest.
    newest = rows[-1]
    reason = (
        f"chain does not reconcile (parsed {cumulative or '[]'} vs cutoff at "
        f"{cutoff_count}) — superseded by the newest row"
    )
    return [(newest, cutoff_count)], [(r, reason) for r in rows[:-1]]


# ---------------------------------------------------------------------------
# Repair
# ---------------------------------------------------------------------------


def _price(model_used: str, tokens_in: int, tokens_out: int, tokens_reasoning: int, tokens_cached: int) -> tuple[float, str]:
    """(cost_usd, model the price came from)."""
    model = model_used or ""
    if get_price(model) is None:
        model = FALLBACK_PRICING_MODEL
    return cost_usd(model, tokens_in, tokens_out, tokens_reasoning, tokens_cached), model


def _write(client, row: dict, summary_md: str, out, span_len: int, wi_count: int) -> None:
    """UPDATE the row with the real summary.

    ``content_md`` and ``summary`` are set to the SAME text, matching what
    ``compact_conversation`` inserts — for ``convo_context`` the item body IS the
    summary.

    ``summary_source_length = len(summary_md)`` keeps the row self-consistent:
    ``content_md == summary`` for ``convo_context``, so the recorded source length is
    the length of that same text — which is exactly what ``compact_conversation``
    writes at insert (``agents/memory/agent.py``). Any other value would leave the
    row looking drifted to a length-drift reader.

    (It is NOT protection against ``resummarize_dirty_items`` — that query filters
    ``.neq("kind", "convo_context")`` and never sees these rows at all.)

    ``metadata.compaction_backfill`` keeps the replaced mock text so the repair is
    reversible and auditable. ``updated_at`` is deliberately left alone — this is a
    content correction, not a user edit.
    """
    metadata = dict(row.get("metadata") or {})
    metadata["compaction_backfill"] = {
        "at": datetime.now(timezone.utc).isoformat(),
        "model_used": out.model_used,
        "tokens_in": out.tokens_in,
        "tokens_out": out.tokens_out,
        "tokens_reasoning": out.tokens_reasoning,
        "tokens_cached": out.tokens_cached,
        "messages_used": span_len,
        "wi_count": wi_count,
        "replaced_content_md": row.get("content_md") or "",
    }
    (
        client.table("workspace_items")
        .update(
            {
                "content_md": summary_md,
                "summary": summary_md,
                "summary_source_length": len(summary_md),
                "summary_updated_at": datetime.now(timezone.utc).isoformat(),
                "metadata": metadata,
            }
        )
        .eq("item_id", row["item_id"])
        .execute()
    )


async def repair_row(client, row: dict, span_len: int, messages: list[dict], live: bool) -> dict:
    """Regenerate one ``convo_context`` row. Returns a result dict for the report."""
    CompactionInput, build_compactor_deps, run_convo_compaction = _load_compactor()

    user_id = str(row.get("user_id") or "")
    span = messages[:span_len]

    wis = fetch_workspace_items(client, str(row["conversation_id"]), user_id, str(row["created_at"]))
    call_name = fetch_call_name(client, user_id)

    # وضع السرية: encode every LLM-bound surface, then flush the new fakes BEFORE the
    # call. ``prior_summary_md`` is "" by construction (see the module docstring), so
    # there is nothing to encode there.
    codec = _build_codec(client, user_id)
    enc_messages = [
        {"role": str(m.get("role") or ""), "content": _enc(codec, m.get("content") or "")}
        for m in span
    ]
    enc_wis = [
        {
            "wi_seq": w["wi_seq"],
            "kind": w["kind"],
            "title": _enc(codec, w["title"]),
            "summary": _enc(codec, w["summary"]) if w["summary"] else w["summary"],
        }
        for w in wis
    ]
    enc_call_name = _enc(codec, call_name) if call_name else None
    _persist_mappings(client, user_id, codec)

    out = await run_convo_compaction(
        CompactionInput(
            messages=enc_messages,
            workspace_items=enc_wis,
            prior_summary_md="",
            user_call_name=enc_call_name,
            conversation_id=row.get("conversation_id"),
        ),
        build_compactor_deps(logger=logger),
    )

    summary_md = _dec(codec, (out.summary_md or "").strip())
    cost, priced_as = _price(
        out.model_used, out.tokens_in, out.tokens_out, out.tokens_reasoning, out.tokens_cached
    )

    result = {
        "row": row,
        "span_len": span_len,
        "wi_count": len(wis),
        "call_name": call_name,
        "masked": bool(codec is not None and getattr(codec, "enabled", False)),
        "out": out,
        "summary_md": summary_md,
        "cost": cost,
        "priced_as": priced_as,
        "written": False,
        "skipped": None,
    }

    # Fail-closed (plan §3.5): a failed call — or an empty summary, which is the same
    # thing — must NEVER overwrite the mock. The row stays repairable next run.
    if out.failed:
        result["skipped"] = "compactor reported failed=True"
        return result
    if not summary_md:
        result["skipped"] = "compactor returned an empty summary"
        return result

    if live:
        _write(client, row, summary_md, out, span_len, len(wis))
        result["written"] = True
    return result


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------


def _short(value) -> str:
    return str(value or "")[:8]


def print_result(idx: int, total: int, result: dict, live: bool) -> None:
    row = result["row"]
    out = result["out"]
    print(
        f"[{idx}/{total}] conversation {_short(row['conversation_id'])}  "
        f"item {_short(row['item_id'])}  wi_seq={row.get('wi_seq')}"
    )
    print(
        f"        span: {result['span_len']} message(s)   workspace items: {result['wi_count']}   "
        f"call name: {result['call_name'] or '—'}   masking: {'on' if result['masked'] else 'off'}"
    )
    priced_note = "" if result["priced_as"] == (out.model_used or "") else f" (priced as {result['priced_as']})"
    print(
        f"        model={out.model_used or '?'}{priced_note}  in={out.tokens_in:,} "
        f"out={out.tokens_out:,} reasoning={out.tokens_reasoning:,} cached={out.tokens_cached:,}  "
        f"${result['cost']:.5f}"
    )
    if result["skipped"]:
        print(f"        SKIPPED — {result['skipped']} (mock text left in place)")
        return
    print(f"        summary ({len(result['summary_md']):,} chars):")
    for line in result["summary_md"].splitlines():
        print(f"          {line}")
    print("        WRITTEN" if live else "        DRY-RUN — nothing written (pass --live to persist)")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Regenerate mock convo_context compaction summaries (plan §5.2).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    mode = p.add_mutually_exclusive_group()
    mode.add_argument(
        "--dry-run",
        action="store_true",
        help="generate every summary and print it, write NOTHING (DEFAULT). "
             "Note: this still calls the model and still costs money.",
    )
    mode.add_argument(
        "--live",
        action="store_true",
        help="actually UPDATE workspace_items with the regenerated summaries",
    )
    p.add_argument(
        "--no-llm",
        action="store_true",
        help="list the targets and their reconstructed spans, then stop. Spends nothing.",
    )
    p.add_argument(
        "--limit",
        type=int,
        default=None,
        metavar="N",
        help="process at most N CONVERSATIONS (not rows) — a conversation's mock rows "
             "form one chain and are always repaired together",
    )
    p.add_argument(
        "--conversation-id",
        default=None,
        metavar="UUID",
        help="restrict to a single conversation (use this for the first live run)",
    )
    return p.parse_args()


async def main() -> int:
    args = parse_args()
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    live = bool(args.live)

    client = get_supabase_client()
    load_pricing(client)

    rows = fetch_targets(client, args.conversation_id)
    if not rows:
        print("No convo_context rows carry the Wave 9 mock. Nothing to repair.")
        return 0

    # Group into chains, preserving the conversation order the query returned.
    by_conversation: dict[str, list[dict]] = {}
    for row in rows:
        by_conversation.setdefault(str(row["conversation_id"]), []).append(row)
    conversation_ids = list(by_conversation)
    if args.limit is not None:
        conversation_ids = conversation_ids[: max(args.limit, 0)]

    mode = "LIVE" if live else "DRY-RUN"
    if args.no_llm:
        mode = "NO-LLM (targets only)"
    print(f"Mode: {mode}")
    print(
        f"Targets: {sum(len(by_conversation[c]) for c in conversation_ids)} mock row(s) "
        f"across {len(conversation_ids)} conversation(s)"
        + (f" (of {len(by_conversation)} found)" if len(conversation_ids) != len(by_conversation) else "")
    )
    print()

    planned: list[tuple[dict, int, list[dict]]] = []
    skipped: list[tuple[dict, str]] = []

    for conversation_id in conversation_ids:
        chain = by_conversation[conversation_id]
        conversation = fetch_conversation(client, conversation_id)
        if not conversation:
            skipped.extend((r, "conversation row not found") for r in chain)
            continue
        if conversation.get("deleted_at"):
            skipped.extend((r, "conversation is soft-deleted") for r in chain)
            continue
        messages = fetch_messages(client, conversation_id)
        if not messages:
            skipped.extend((r, "conversation has no messages") for r in chain)
            continue
        repairable, chain_skipped = plan_conversation(chain, conversation, messages)
        skipped.extend(chain_skipped)
        planned.extend((row, span_len, messages) for row, span_len in repairable)

    if args.no_llm:
        for row, span_len, messages in planned:
            print(
                f"  conversation {_short(row['conversation_id'])}  item {_short(row['item_id'])}  "
                f"span = messages[0:{span_len}] of {len(messages)}"
            )
        for row, reason in skipped:
            print(f"  conversation {_short(row['conversation_id'])}  item {_short(row['item_id'])}  SKIP — {reason}")
        print(f"\n{len(planned)} row(s) reconstructable, {len(skipped)} skipped. No model calls made.")
        return 1 if skipped else 0

    results: list[dict] = []
    for idx, (row, span_len, messages) in enumerate(planned, start=1):
        result = await repair_row(client, row, span_len, messages, live)
        results.append(result)
        print_result(idx, len(planned), result, live)
        print()

    for row, reason in skipped:
        print(
            f"SKIPPED conversation {_short(row['conversation_id'])} item {_short(row['item_id'])} — {reason}"
        )
    if skipped:
        print()

    ok = [r for r in results if not r["skipped"]]
    failed = [r for r in results if r["skipped"]]
    total_in = sum(r["out"].tokens_in for r in results)
    total_out = sum(r["out"].tokens_out for r in results)
    total_reasoning = sum(r["out"].tokens_reasoning for r in results)
    total_cost = sum(r["cost"] for r in results)

    print("-" * 72)
    print(f"  rows repaired      : {len(ok)}" + ("" if live else "  (dry-run — not written)"))
    print(f"  rows fail-closed   : {len(failed)}  (mock text left in place)")
    print(f"  rows unplannable   : {len(skipped)}")
    print(f"  tokens             : in={total_in:,}  out={total_out:,}  reasoning={total_reasoning:,}")
    print(f"  TOTAL COST         : ${total_cost:.5f}   (operator spend — NOT on any user's ledger)")
    if not live and ok:
        print("\n  Re-run with --live to persist these summaries.")
    return 1 if (failed or skipped) else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
