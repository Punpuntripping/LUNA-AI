"""Replay the artifact_analyzer on real conversations with the real model.

Unit tests prove the rules. They cannot show whether the model judges well, and
a false «sufficient» gives a user a wrong legal answer with no search behind it.
Spec: ``.claude/plans/artifact_analyzer.md`` §7.

    python scripts/eval_artifact_analyzer.py --env-file /path/to/.env --out run.json
    python scripts/eval_artifact_analyzer.py --env-file /path/to/.env --sample 10 --out run.json

Safety:
  * Read-only. ``ReadOnlyClient`` refuses every write, and masking is inactive
    so no fake is minted.
  * ``--env-file`` loads only LLM keys and the Supabase URL and service key.
  * Each case sees the conversation as it stood at that message
    (``reports_before``).
  * This file holds ids and invented questions, never user content. ``--out`` does
    hold user content: write it outside the repo.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any

sys.dont_write_bytecode = True

try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
except Exception:  # noqa: BLE001
    pass

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

CONVO = "9c647ef6-7d1e-4450-94c1-1aafe88d39f5"
USER = "0d0a97d4-dc22-4ebc-8520-26b4314b4783"
_LAST_MESSAGE = "d657fc7d-5492-42d1-8c80-ee5212ace184"

# A follow-up asking whether a rule reaches bodies the search never looked for.
# Production said «sufficient» from the rule's silence (artifact_analyzer_scope_full_text.md §1).
_SCOPE_CONVO = "bc48e2ed-5501-40bc-b117-b5f93a95761e"
_SCOPE_USER = "c5f4cff0-0517-43f0-af59-a9905deab22c"
_SCOPE_MESSAGE = "ef1a1294-0e5a-4fce-a1ef-d55399e7f0de"

# Demo account. A report citing a ruling whose full text holds a detail its
# summary leaves out (artifact_analyzer_scope_full_text.md §6).
_RULING_CONVO = "039bba3f-48e7-4c07-9bb0-fd80eaef9c36"
_RULING_USER = "32c10d30-9d2a-4d50-966a-f397c1e3efae"
_RULING_MESSAGE = "af1daaae-fccb-4008-8524-8144ea506e70"

_LOADED_NAMES = re.compile(r"^(.*_API_KEY|SUPABASE_URL|SUPABASE_SERVICE_KEY|SUPABASE_SERVICE_ROLE_KEY)$")
_DUMMIES = {
    "SUPABASE_ANON_KEY": "unused",
    "SUPABASE_JWT_SECRET": "unused",
    "REDIS_URL": "redis://localhost:6379",
    "ENVIRONMENT": "test",
}
# Claims about what a search would do (D10).
_PREDICTS = re.compile(r"سيجد|سيوضح|سيكشف|سيؤكد|قد يكشف|قد يوضح|البحث الجديد|بحث جديد قد")

_RATE_IN, _RATE_CACHED, _RATE_OUT = 0.13, 0.028, 0.26  # V4 Flash on Baseten, $/M


@dataclass
class Case:
    id: str
    conversation_id: str
    user_id: str
    message_id: str | None = None
    #: Replace the real message. Invented, for cases no conversation holds yet.
    question: str | None = None
    #: Verdicts that pass. Empty = no expectation, for the owner to label.
    expected: tuple[str, ...] = ()
    note: str = ""


GOLD = [
    Case("turn4", CONVO, USER, "58dad650-7bd1-4392-9a8e-fa90e557da9b", expected=("sufficient",),
         note="the non-نفقة rule is already in WI-2"),
    Case("turn5", CONVO, USER, "8ea31a58-ea8b-4bf1-9c36-5e19460ae559",
         expected=("partial", "insufficient"), note="a date that changes the governing law"),
    Case("turn6", CONVO, USER, _LAST_MESSAGE, expected=("sufficient",),
         note="WI-4 already holds the salary-after-the-order branch"),
    Case("new_issue", CONVO, USER, _LAST_MESSAGE,
         question="كم غرامة عدم تسجيل العمالة في التأمينات الاجتماعية؟",
         expected=("insufficient",), note="unrelated legal issue"),
    Case("gap_question", CONVO, USER, _LAST_MESSAGE,
         question="كم مدة التظلم من الحجز في النظام القديم؟",
         expected=("partial", "insufficient"), note="a reported gap; never sufficient"),
    Case("other_branch", CONVO, USER, _LAST_MESSAGE,
         question="وإذا كان معاش تقاعد بدل الراتب، كم النسبة المستثناة؟",
         expected=("sufficient", "partial"),
         note="the pension rule is the new law's, so partial is a fair answer on an old-law matter"),
    Case("repealed_text", CONVO, USER, _LAST_MESSAGE,
         question="وش تقول المادة 60 من النظام القديم عن حجز الحساب الجاري؟",
         expected=("sufficient",), note="should open the chunk and keep the repeal status"),
    Case("scope_other_body", _SCOPE_CONVO, _SCOPE_USER, _SCOPE_MESSAGE,
         expected=("partial", "insufficient"),
         note="asks about bodies the search never looked for; silence is not an answer"),
    Case("ruling_detail", _RULING_CONVO, _RULING_USER, _RULING_MESSAGE,
         question=(
             "في الحكم الذي رُفض فيه دفاع صاحب العمل بالمادة 80، متى جرى التحقيق مع "
             "العامل، وهل قال وكيل الشركة إن قرار الفصل وصل إلى العامل؟"
         ),
         expected=("sufficient",),
         note="the answer is in the ruling's full text, not its summary: run with --full-text"),
]


def load_cases_file(path: str) -> list[Case]:
    """Cases from a JSON list of {id, conversation_id, user_id, message_id[, question, expected, note]}.

    For replaying real production turns. The file holds ids only; keep it out of the repo.
    """
    rows = json.loads(Path(path).read_text(encoding="utf-8"))
    return [
        Case(
            str(r["id"]), r["conversation_id"], r["user_id"], r["message_id"],
            question=r.get("question"),
            expected=tuple(r.get("expected") or ()), note=str(r.get("note") or ""),
        )
        for r in rows
    ]


class _ReadOnlyQuery:
    _BLOCKED = {"insert", "update", "upsert", "delete"}

    def __init__(self, query: Any) -> None:
        self._query = query

    def __getattr__(self, name: str) -> Any:
        if name in self._BLOCKED:
            raise RuntimeError(f"eval is read-only: {name}() refused")
        attr = getattr(self._query, name)
        if not callable(attr):
            return attr

        def call(*args: Any, **kwargs: Any) -> Any:
            result = attr(*args, **kwargs)
            return result if name == "execute" else _ReadOnlyQuery(result)

        return call


class ReadOnlyClient:
    def __init__(self, client: Any) -> None:
        self._client = client

    def table(self, name: str) -> _ReadOnlyQuery:
        return _ReadOnlyQuery(self._client.table(name))


def load_env(path: str) -> None:
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        name, sep, value = line.strip().partition("=")
        if sep and _LOADED_NAMES.match(name):
            os.environ[name] = value.strip().strip("'\"")
    for name, value in _DUMMIES.items():
        os.environ.setdefault(name, value)


def _message_at(client: Any, message_id: str) -> dict:
    return (
        client.table("messages").select("role, content, created_at, conversation_id")
        .eq("message_id", message_id).execute().data
    )[0]


def _rows_up_to(client: Any, conversation_id: str, created_at: str) -> list[SimpleNamespace]:
    rows = (
        client.table("messages").select("role, content, created_at")
        .eq("conversation_id", conversation_id).lte("created_at", created_at)
        .order("created_at", desc=True).limit(30).execute().data
    ) or []
    kept = [r for r in reversed(rows) if (r.get("content") or "").strip()][-7:]
    return [SimpleNamespace(role=r["role"], content=r["content"]) for r in kept]


def sample_followups(client: Any, n: int, since: str) -> list[Case]:
    """Later reports of a conversation: real turns that re-ran a search."""
    rows = (
        client.table("workspace_items")
        .select("conversation_id, user_id, message_id, created_at")
        .eq("kind", "agent_search").gte("created_at", since)
        .order("created_at", desc=True).limit(500).execute().data
    ) or []
    by_convo: dict[str, list[dict]] = {}
    for row in rows:
        if row.get("conversation_id") and row["conversation_id"] != CONVO:
            by_convo.setdefault(row["conversation_id"], []).append(row)

    cases: list[Case] = []
    for convo, reports in by_convo.items():
        if len(reports) < 2 or not reports[0].get("message_id") or len(cases) >= n:
            continue
        reply = _message_at(client, reports[0]["message_id"])
        users = (
            client.table("messages").select("message_id")
            .eq("conversation_id", convo).eq("role", "user")
            .lt("created_at", reply["created_at"])
            .order("created_at", desc=True).limit(1).execute().data
        ) or []
        if users:
            cases.append(Case(
                f"sample{len(cases) + 1}", convo, reports[0]["user_id"], users[0]["message_id"],
            ))
    return cases


async def run_case(case: Case, client: Any, gate: asyncio.Semaphore) -> dict:
    from pydantic_ai import capture_run_messages
    from pydantic_ai.messages import ModelResponse, ToolCallPart

    from agents.memory.artifact_analyzer import AnalyzerCall, analyze

    message = _message_at(client, case.message_id)
    recent = _rows_up_to(client, case.conversation_id, message["created_at"])
    call = AnalyzerCall(
        question=case.question or message["content"],
        user_id=case.user_id,
        conversation_id=case.conversation_id,
        recent_messages=recent,
        # Only what existed then, and a synthetic case keeps the same state.
        reports_before=message["created_at"] if case.question is None else None,
    )
    async with gate:
        started = time.perf_counter()
        with capture_run_messages() as messages:
            result = await analyze(call, client)
        seconds = round(time.perf_counter() - started, 1)

    usage: dict[str, int] = {"in": 0, "cached": 0, "out": 0, "requests": 0}
    for m in messages:
        if isinstance(m, ModelResponse):
            usage["in"] += m.usage.input_tokens or 0
            usage["cached"] += m.usage.cache_read_tokens or 0
            usage["out"] += m.usage.output_tokens or 0
            usage["requests"] += 1
    cost = (
        (usage["in"] - usage["cached"]) * _RATE_IN
        + usage["cached"] * _RATE_CACHED
        + usage["out"] * _RATE_OUT
    ) / 1e6

    # The points as the model last listed them, with the query it named for each:
    # what a reviewer needs to tell a real match from a stretch.
    points: list[dict] = []
    for m in messages:
        if isinstance(m, ModelResponse):
            for part in m.parts:
                args = part.args_as_dict() if isinstance(part, ToolCallPart) else {}
                if isinstance(args.get("points"), list):
                    points = args["points"]

    said = " ".join([result.restatement, *result.missing])
    return {
        "id": case.id,
        "note": case.note,
        "question": call.question,
        "expected": list(case.expected),
        "passed": (result.verdict in case.expected) if case.expected else None,
        "verdict": result.verdict,
        "before_rules": result.verdict_before_rules,
        "degraded": result.degraded,
        "restatement": result.restatement,
        "quotes": [
            {"text": q.text, "source": q.source_name, "status": q.status, "uncited": q.uncited,
             "domain": q.domain}
            for q in result.quotes
        ],
        "missing": result.missing,
        "outside_search": result.outside_search,
        "points": points,
        "full_read_n": result.full_read_n,
        "reading_errors": [
            {"report_text": c.report_text, "corrected_text": c.corrected_text, "source": c.source_name}
            for c in result.reading_errors
        ],
        "reading_errors_dropped": result.reading_errors_dropped,
        "ask_user": result.ask_user,
        "predicts": bool(_PREDICTS.search(said)),
        "quotes_dropped": result.quotes_dropped,
        "reports_n": result.reports_n,
        "sources_n": result.sources_n,
        "opened_n": result.opened_n,
        "seconds": seconds,
        "usage": usage,
        "cost_usd": round(cost, 5),
    }


# What the real router should do on the state of CONVO. (message, what it should
# emit). Invented messages: no user content.
ROUTER_CASES = [
    ("بعد الأمر، الراتب ينزل كل شهر بعد ما صدر الأمر", "check_reports"),
    ("طيب والبنك لو رفض يرجع المبلغ وش أسوي؟", "check_reports"),
    ("فصّل أكثر في طريقة التظلم", "check_reports"),
    ("ابحث مرة ثانية عن استرداد المبلغ المحجوز", "dispatch:deep_search"),
    ("دور لي أحكام قضائية في حجز الراتب المستثنى", "dispatch:deep_search"),
    ("وش عقوبة الشيك بدون رصيد؟", "dispatch:deep_search"),
    ("اكتب لي خطاب للبنك أطالب فيه برد المبلغ", "dispatch:writing"),
    ("شكرًا جزاك الله خير", "chat"),
    ("كم سعر الاشتراك؟", "chat"),
]


async def run_router_cases(client: Any, repeat: int, concurrency: int) -> int:
    from agents.router.context import load_router_context
    from agents.router.router import run_router

    gate = asyncio.Semaphore(concurrency)

    async def one(message: str) -> str:
        ctx = load_router_context(client, USER, CONVO, None, wi_lock_policy=None)
        async with gate:
            result = await run_router(
                question=message,
                supabase=client,
                user_id=USER,
                conversation_id=CONVO,
                case_id=None,
                case_memory_md=ctx.case_memory_md,
                case_metadata=ctx.case_metadata,
                user_preferences=ctx.user_preferences,
                message_history=ctx.message_history,
                workspace_item_summaries=ctx.workspace_item_summaries,
                compaction_summary_md=ctx.compaction_summary_md,
            )
        output = result.output
        family = getattr(output, "agent_family", None)
        return f"{output.type}:{family}" if family else output.type

    failed = 0
    for message, expected in ROUTER_CASES:
        got = await asyncio.gather(*(one(message) for _ in range(repeat)))
        ok = sum(g == expected for g in got)
        failed += repeat - ok
        print(f"{ok}/{repeat}  want {expected:<22} got {sorted(set(got))}  {message[:42]}")
    print(f"\nrouter: {len(ROUTER_CASES) * repeat - failed}/{len(ROUTER_CASES) * repeat}")
    return 1 if failed else 0


async def main_async(args: argparse.Namespace) -> int:
    from shared.db.client import get_supabase_client

    client = ReadOnlyClient(get_supabase_client())
    if args.router:
        return await run_router_cases(client, args.repeat, args.concurrency)
    cases = [] if args.cases_file and not args.only else list(GOLD)
    if args.only:
        wanted = set(args.only.split(","))
        cases = [c for c in cases if c.id in wanted]
    if args.cases_file:
        cases += load_cases_file(args.cases_file)
    if args.sample:
        from datetime import datetime, timedelta, timezone

        since = (datetime.now(timezone.utc) - timedelta(days=14)).isoformat()
        cases += sample_followups(client, args.sample, since)

    gate = asyncio.Semaphore(args.concurrency)
    results = await asyncio.gather(*(
        run_case(c, client, gate) for c in cases for _ in range(args.repeat)
    ))

    if args.out:
        Path(args.out).write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"{'case':<15}{'verdict':<14}{'rules from':<14}{'ok':<6}{'quotes':<8}{'open':<6}{'full':<6}{'fix':<5}{'sec':<7}{'$':<9}pred")
    for r in results:
        ok = {True: "yes", False: "NO", None: "-"}[r["passed"]]
        before = r["before_rules"] if r["before_rules"] != r["verdict"] else ""
        print(
            f"{r['id']:<15}{r['verdict']:<14}{str(before):<14}{ok:<6}{len(r['quotes']):<8}"
            f"{r['opened_n']:<6}{r['full_read_n']:<6}{len(r['reading_errors']):<5}"
            f"{r['seconds']:<7}{r['cost_usd']:<9}{'YES' if r['predicts'] else ''}"
            f"{'  DEGRADED' if r['degraded'] else ''}"
        )
    graded = [r for r in results if r["passed"] is not None]
    failed = [r["id"] for r in graded if not r["passed"]]
    false_sufficient = [
        r["id"] for r in graded if r["verdict"] == "sufficient" and "sufficient" not in r["expected"]
    ]
    print(f"\npassed {len(graded) - len(failed)}/{len(graded)}  failed: {failed or 'none'}")
    print(f"false sufficient: {false_sufficient or 'none'}")
    return 1 if failed else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--env-file", required=True)
    parser.add_argument("--out", help="JSON of full results. Holds user content: keep it out of the repo.")
    parser.add_argument("--only", help="Comma-separated gold case ids.")
    parser.add_argument("--sample", type=int, default=0, help="Add N recent real follow-ups, unlabelled.")
    parser.add_argument(
        "--cases-file",
        help="JSON list of real turns to replay (ids only). Alone, it runs just these; "
             "with --only, the named gold cases too.",
    )
    parser.add_argument("--concurrency", type=int, default=3)
    parser.add_argument("--router", action="store_true", help="Grade the real router's routing instead.")
    parser.add_argument(
        "--full-text", action="store_true",
        help="Turn read_full_text on (ARTIFACT_ANALYZER_FULL_TEXT_ENABLED) for this run.",
    )
    parser.add_argument(
        "--repeat", type=int, default=1,
        help="Run each case N times. Verdicts vary between runs, so one pass proves little.",
    )
    args = parser.parse_args()
    load_env(args.env_file)
    # Before the first settings read: they are cached for the process.
    os.environ["ARTIFACT_ANALYZER_FULL_TEXT_ENABLED"] = "true" if args.full_text else "false"
    os.environ.pop("ARTIFACT_ANALYZER_FULL_TEXT_USERS", None)
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
