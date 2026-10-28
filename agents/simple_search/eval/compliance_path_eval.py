"""Compliance-path eval — the services half of ``simple_search``, live, minus the
publish (plan ``.claude/plans/simple_search_compliance_path.md`` §10).

WHAT IT RUNS
------------
Per case, the same loop ``runner._answer_loop`` runs on a services turn, built
from the same parts:

* the REAL searcher (``create_searcher_agent`` — prompt, tools, validator,
  ``SEARCHER_LIMITS``), on the raw question;
* on ``service_queries``: the REAL :func:`run_service_search` (one embedding
  batch → ``search_topics(['service'])`` ×≤3 → RRF → dedup → enrich) and the
  REAL :func:`run_compliance` (pick → ``open_service`` → artifact, or a reason);
* on ``sufficient=false``: :func:`runner.services_revert_note` +
  ``services_reverted`` — the runner's own helper, so the eval cannot drift
  from what production feeds the next cycle — inside the shared
  ``MAX_CYCLES`` pool.

WHAT IT NEVER DOES
------------------
* No ``_finalise``, no responder, no publisher: not one ``workspace_items`` or
  ``workspace_item_references`` row is written, whatever the outcome.
* No ``collect_llm_calls`` scope — that one FLUSHES into prod ``llm_calls`` on
  exit. Cost comes from :func:`_local_ledger`, a throwaway buffer bound to the
  same ContextVar, so every per-call row (tokens priced by ``run_tracked``, and
  the 0.1-pt search fees) is summed here and discarded.
* No pause row: an ``ask_user`` ends the case and is counted as one.

THE CASES
---------
15 service questions with a GOLD primary ``services.id`` each (looked up
read-only on the live ``services`` table, 2026-10-28 — ids and titles below),
93fafee7 first; and the 6 regulatory / multi-service negatives of
``fixtures_routing_services`` (the router fixtures), which the searcher must
ABORT — a negative answered on the services path is the failure the plan's
"done" bar counts.

METRICS
-------
service cases: primary hit@2 (a gold id among the ≤2 ``primary``), gold in the
candidate list at all (retrieval recall@15) and its rank, ``ask_user`` rate,
cycles, wall-clock, cost, artifact words vs question words.
negatives: aborted by the searcher · answered on the services path (must be 0).
Plan bar: hit@2 ≥ 13/15 with 0 negatives answered here.

RUN
---
Live (costs real model calls + embeddings)::

    .venv/Scripts/python.exe agents/simple_search/eval/compliance_path_eval.py
    .venv/Scripts/python.exe agents/simple_search/eval/compliance_path_eval.py --only q01,neg-reg-mandatory
    .venv/Scripts/python.exe agents/simple_search/eval/compliance_path_eval.py --limit 5

Prerequisites: ``ALIBABA_API_KEY`` for the 1024-d embeddings (rotated 10-24 — the
local ``.env`` copy is stale and 401s; the run stops at the first auth error
rather than recording 21 identical failures), a working ``SUPABASE_URL`` +
service key, and the model provider keys behind the three slots.

Offline harness self-test (no network: fake embedder, in-memory DB, scripted
models — every metric must come out perfect, anything else is a HARNESS bug)::

    .venv/Scripts/python.exe agents/simple_search/eval/compliance_path_eval.py --dry-run
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import statistics
import sys
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Iterator

_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

try:  # Windows console — the corpus is Arabic
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    sys.stderr.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
except Exception:  # noqa: BLE001
    pass

from dotenv import load_dotenv  # noqa: E402

load_dotenv(_ROOT / ".env")

from pydantic_ai import DeferredToolRequests  # noqa: E402
from pydantic_ai.exceptions import UsageLimitExceeded  # noqa: E402

import agents.simple_search.compliance as compliance_mod  # noqa: E402
import agents.simple_search.searcher as searcher_mod  # noqa: E402
from agents.simple_search.compliance import (  # noqa: E402
    ServiceCandidate,
    run_compliance,
    run_service_search,
)
from agents.simple_search.eval.fixtures_routing_services import (  # noqa: E402
    SERVICE_ROUTE_FIXTURES,
)
from agents.simple_search.prompts import build_searcher_user_message  # noqa: E402
from agents.simple_search.runner import (  # noqa: E402
    MAX_CYCLES,
    _deferred_question,
    services_revert_note,
)
from agents.simple_search.searcher import (  # noqa: E402
    SEARCHER_LIMITS,
    SEARCHER_SLOT,
    SearcherDecision,
    SearcherDeps,
    create_searcher_agent,
)
from agents.utils.tracking import run_tracked  # noqa: E402


# =========================================================================== #
# The cases.
# =========================================================================== #


@dataclass(frozen=True)
class EvalCase:
    """One question. ``gold`` = acceptable PRIMARY service ids (any one hits);
    ``related`` = ids that are a correct second main or supporting service but
    do not count toward hit@2. Empty ``gold`` ⇒ a negative (must abort)."""

    cid: str
    question: str
    gold: tuple[str, ...] = ()
    gold_title: str = ""
    related: tuple[str, ...] = ()
    why: str = ""

    @property
    def negative(self) -> bool:
        return not self.gold


# Gold ids: live ``services`` table, read-only, 2026-10-28. Titles are
# ``service_name_ar`` verbatim («{الجهة} - {اسم الخدمة}»).
SERVICE_CASES: list[EvalCase] = [
    EvalCase(
        "q01-93fafee7",
        "بطلع العنوان الوطني لمؤسستي من فين؟ وكيف اقدر اثبت ان العنوان الوطني "
        "للمؤسسة رقم (رقم السجل التجاري)",
        gold=("9cb3055c-3cc2-441e-896a-aea578f9b51b",),
        gold_title="مؤسسة البريد السعودي - تسجيل العنوان الوطني للأعمال",
        related=("760dd43b-8900-4c84-ac54-9ed94f388fb9",),   # إدارة العنوان الوطني (طباعة الإثبات)
        why="Prod convo 93fafee7 verbatim — two needs: register (business) + prove.",
    ),
    EvalCase(
        "q02-address-register", "وين أسجّل العنوان الوطني لمنشأتي الجديدة؟",
        gold=("9cb3055c-3cc2-441e-896a-aea578f9b51b",),
        gold_title="مؤسسة البريد السعودي - تسجيل العنوان الوطني للأعمال",
        why="«للأعمال» not «للأفراد» — the فرد/منشأة pick the compliance prompt names.",
    ),
    EvalCase(
        "q03-address-proof", "كيف أطبع مستند يثبت عنوان سكني من العنوان الوطني؟",
        gold=("760dd43b-8900-4c84-ac54-9ed94f388fb9",),
        gold_title="مؤسسة البريد السعودي - إدارة العنوان الوطني",
        why="The proof document lives under «إدارة العنوان الوطني» (its intro says so), "
            "not under a service named «طباعة».",
    ),
    EvalCase(
        "q04-cr-annual", "وش المطلوب عشان أأكد بيانات السجل التجاري لمؤسستي الفردية هالسنة؟",
        gold=("7d38c728-5a74-49a3-9f85-561e6b88cbc4",),
        gold_title="وزارة التجارة - التأكيد السنوي لبيانات السجل التجاري لمؤسسة فردية",
        why="CR renewal became the annual confirmation; three variants by entity type.",
    ),
    EvalCase(
        "q05-absher-exit-reentry", "كيف أطلع تأشيرة خروج وعودة للعاملة المنزلية عن طريق أبشر؟",
        gold=("f90f9c1f-1013-4335-8359-95cc5839d215",),
        gold_title="وزارة الداخلية - إصدار تأشيرة الخروج والعودة أو الخروج النهائي",
        why="Absher individuals; decoys: the MISA investor visa, the status inquiry.",
    ),
    EvalCase(
        "q06-name-reserve", "أبغى أحجز اسم تجاري لمؤسستي، وش الخطوات؟",
        gold=("2d17908b-7654-4277-b78e-dcb3c8cc172d",),
        gold_title="وزارة التجارة - حجز اسم تجاري",
        why="Decoy: «تمديد حجز اسم تجاري».",
    ),
    EvalCase(
        "q07-cr-issue", "كيف أطلع سجل تجاري لمؤسسة فردية جديدة؟",
        gold=("b07db3ba-3ccc-48b1-8dc3-890c660222d8",),
        gold_title="وزارة التجارة - قيد سجل تجاري لمؤسسة فردية",
        why="The user says «أطلع سجل»; the catalogue says «قيد».",
    ),
    EvalCase(
        "q08-cr-strike", "كيف أشطب السجل التجاري لمؤسستي الفردية؟",
        gold=("bdaa3d3b-0b63-4572-8306-f3d5e375d10d",),
        gold_title="وزارة التجارة - شطب سجل تجاري لمؤسسة فردية",
        why="Decoy: «شطب السجل التجاري الفرعي لشركة».",
    ),
    EvalCase(
        "q09-ejar-contracts", "كيف أشوف عقود الإيجار المسجلة باسمي في منصة إيجار؟",
        gold=("1b0af55f-3c72-42e9-a3da-5058a7c47023",),
        gold_title="الهيئة العامة للعقار - الاستعلام عن عقود الإيجار",
        why="Ejar-integrated inquiry; the catalogue has no Ejar contract-documentation service.",
    ),
    EvalCase(
        "q10-qiwa-remote-contract", "كيف أوثق عقد عمل عن بعد لموظف عندي؟",
        gold=("6f49a298-8bf5-453c-8e0e-dec350f8988d",),
        gold_title="وزارة الموارد البشرية والتنمية الاجتماعية - إنشاء و توثيق عقد عمل عن بعد",
        why="Qiwa-style contract documentation.",
    ),
    EvalCase(
        "q11-qiwa-transfer", "كيف أنقل خدمات عامل وافد لمنشأتي عن طريق قوى؟",
        # Both MHRSD transfer services are correct golds. «لمنشأتي» is the
        # ESTABLISHMENT asking, which is exactly «طلب نقل العمالة الوافدة»
        # (694ca604: the منشأة requests the employee from another employer);
        # «نقل الخدمات» (a687115e) is the worker-initiated Qiwa transfer without
        # the current employer's consent. The 2026-10-28 live run picked
        # 694ca604 as primary with a687115e as supporting — the label was too
        # narrow, not the answer.
        gold=(
            "694ca604-80a0-42ef-aba3-5dd72c7020cb",
            "a687115e-482a-4e1f-82d7-3a7d726f1dd4",
        ),
        gold_title="وزارة الموارد البشرية والتنمية الاجتماعية - طلب نقل العمالة الوافدة / نقل الخدمات",
        why="Qiwa transfer; decoy: MISA «نقل خدمات وافد» (investors).",
    ),
    EvalCase(
        "q12-najiz-poa", "أبغى أطلع وكالة لأخوي إلكترونيًا بدون ما أروح كتابة العدل، كيف؟",
        gold=("bf16eb31-b2b2-485e-86a7-8a5f866d94ab",),
        gold_title="وزارة العدل - إصدار وكالة فردية",
        why="Najiz e-POA; decoys: multi-party POA, POA draft, commercial agency.",
    ),
    EvalCase(
        "q13-balady-renew", "كيف أجدد رخصة المحل التجاري من بلدي؟",
        gold=("a07f013d-ffa4-47ed-b205-e110ac514854",),
        gold_title="وزارة البلديات والإسكان - تجديد رخصة تجارية",
        why="Balady; decoys: the same service under AlUla / regional أمانات.",
    ),
    EvalCase(
        "q14-gosi-register", "كيف أسجل منشأتي الجديدة في التأمينات الاجتماعية؟",
        gold=("a6358c9e-9267-49c3-8ae0-8f6c6e2e50cc",),
        gold_title="المؤسسة العامة للتأمينات الاجتماعية - خدمة تسجيل منشأة",
        why="GOSI establishment registration.",
    ),
    EvalCase(
        "q15-absher-id-renew", "كيف أجدد هويتي الوطنية عن طريق أبشر؟",
        gold=("3a7da35d-b43f-4293-a35b-0595b78187a5",),
        gold_title="وزارة الداخلية - تجديد الهوية الوطنية",
        why="Absher individuals, a one-service question.",
    ),
]

#: The router fixtures' deep_search side — the same strings the router A/B
#: scores, so a negative here is the SAME question that should never have
#: reached simple_search; if the router misroutes it, the searcher must abort.
NEGATIVE_CASES: list[EvalCase] = [
    EvalCase(f"neg-{f.rid}", f.paraphrases[0], why=f.why)
    for f in SERVICE_ROUTE_FIXTURES
    if f.expect == "deep_search"
]

ALL_CASES: list[EvalCase] = SERVICE_CASES + NEGATIVE_CASES


# =========================================================================== #
# Results.
# =========================================================================== #


@dataclass
class CycleTrace:
    cycle: int
    queries: list[str] = field(default_factory=list)
    candidates: list[dict] = field(default_factory=list)     # top of the list, n/id/title
    gold_rank: int | None = None                             # 1-based, None = not retrieved
    opened: list[int] = field(default_factory=list)
    sufficient: bool | None = None
    reason: str = ""


@dataclass
class CaseResult:
    cid: str
    negative: bool
    question: str
    outcome: str = ""            # artifact | aborted | ask_user | resolved:<levels> | fallback:<code> | error
    detail: str = ""             # abort reason / question / error text
    cycles: int = 0
    primary_ids: list[str] = field(default_factory=list)
    supporting_ids: list[str] = field(default_factory=list)
    title: str = ""
    artifact_words: int = 0
    question_words: int = 0
    hit_at_2: bool = False
    gold_retrieved: bool = False
    gold_best_rank: int | None = None
    related_used: bool = False
    wall_s: float = 0.0
    cost_usd: float = 0.0
    llm_calls: int = 0
    trace: list[CycleTrace] = field(default_factory=list)


_AUTH_RE = re.compile(r"\b401\b|unauthori[sz]ed|invalid api[- ]?key|AuthenticationError", re.I)


class AuthStop(RuntimeError):
    """Raised to end the whole run on the first credential failure.

    Carries the stopped case's partial :class:`CaseResult` (``result``) so its
    trace — e.g. the ``service_queries`` the searcher wrote before the embedder
    refused the key — still lands in the report.
    """

    result: "CaseResult | None" = None


def _is_auth_error(exc: BaseException) -> bool:
    return bool(_AUTH_RE.search(f"{type(exc).__name__}: {exc}"))


@contextmanager
def _local_ledger() -> Iterator[list[dict[str, Any]]]:
    """A ``collect_llm_calls`` buffer that is NEVER flushed to prod.

    ``run_tracked`` and ``record_search_fee`` append to whatever list the
    ``usage_sink`` ContextVar holds; binding a private list here captures every
    row the case produces (priced, fees included) without the insert the real
    scope performs on exit.
    """
    from agents.utils import usage_sink

    buf: list[dict[str, Any]] = []
    tok = usage_sink._buffer.set(buf)
    try:
        yield buf
    finally:
        usage_sink._buffer.reset(tok)


def _words(text: str) -> int:
    return len((text or "").split())


# =========================================================================== #
# One case — the runner's services loop, with no publish.
# =========================================================================== #


async def run_case(case: EvalCase, supabase: Any, *, embed_many: Any = None) -> CaseResult:
    res = CaseResult(
        cid=case.cid, negative=case.negative, question=case.question,
        question_words=_words(case.question),
    )
    deps = SearcherDeps(supabase=supabase, user_id="", conversation_id="", case_id=None)
    t0 = time.perf_counter()
    with _local_ledger() as ledger:
        try:
            await _loop(case, res, deps, supabase, embed_many)
        except AuthStop as stop:
            res.outcome, res.detail = "stopped:auth", str(stop)[:300]
            stop.result = res
            raise
        except Exception as exc:  # noqa: BLE001 — record, keep going
            res.outcome, res.detail = "error", f"{type(exc).__name__}: {exc}"[:300]
        finally:
            res.wall_s = round(time.perf_counter() - t0, 2)
            res.cost_usd = round(sum(float(r.get("cost_usd") or 0.0) for r in ledger), 6)
            res.llm_calls = sum(1 for r in ledger if r.get("model"))
    if not case.negative:
        ranks = [t.gold_rank for t in res.trace if t.gold_rank]
        res.gold_retrieved = bool(ranks)
        res.gold_best_rank = min(ranks) if ranks else None
        res.hit_at_2 = res.outcome == "artifact" and bool(set(case.gold) & set(res.primary_ids))
        res.related_used = bool(set(case.related) & set(res.primary_ids + res.supporting_ids))
    return res


async def _loop(
    case: EvalCase, res: CaseResult, deps: SearcherDeps, supabase: Any, embed_many: Any
) -> None:
    for cycle in range(1, MAX_CYCLES + 1):
        res.cycles = cycle
        try:
            run = await run_tracked(
                create_searcher_agent(),
                build_searcher_user_message(case.question),
                deps=deps,
                stage="simple_search.search",
                slot=SEARCHER_SLOT,
                agent_family="simple_search",
                usage_limits=SEARCHER_LIMITS,
            )
        except UsageLimitExceeded as exc:
            res.outcome, res.detail = "fallback:searcher_budget", str(exc)[:300]
            return
        except Exception as exc:  # noqa: BLE001
            if _is_auth_error(exc):
                raise AuthStop(f"searcher: {exc}") from exc
            res.outcome, res.detail = "fallback:searcher_error", f"{type(exc).__name__}: {exc}"[:300]
            return

        out = run.output
        if isinstance(out, DeferredToolRequests) or not isinstance(out, SearcherDecision):
            res.outcome, res.detail = "ask_user", _deferred_question(out)
            return
        if out.aborted:
            res.outcome, res.detail = "aborted", out.abort_reason
            return
        if not out.service_queries:
            # The old resolve path — a handle or an object. Not this eval's
            # subject; recorded so a services question that never reached the
            # compliance agent is visible as such.
            levels = sorted({o.level for o in out.resolved}) or ["none"]
            res.outcome = "resolved:" + "+".join(levels)
            res.detail = "; ".join(o.title or o.primary_id() for o in out.resolved)[:300]
            return

        queries = list(out.service_queries)
        trace = CycleTrace(cycle=cycle, queries=queries)
        res.trace.append(trace)
        try:
            cands: list[ServiceCandidate] = await run_service_search(
                supabase, queries, embed_many=embed_many,
            )
            trace.candidates = [
                {"n": c.n, "id": c.service_id, "title": c.title} for c in cands[:8]
            ]
            trace.gold_rank = next(
                (c.n for c in cands if c.service_id in case.gold), None
            )
            verdict = await run_compliance(
                case.question, cands,
                supabase=supabase, conversation_id="", case_id=None,
                rejection_notes=list(deps.rejection_notes),
            )
        except Exception as exc:  # noqa: BLE001 — the runner's compliance_error
            if _is_auth_error(exc):
                raise AuthStop(f"services path: {exc}") from exc
            res.outcome = "fallback:compliance_error"
            res.detail = f"{type(exc).__name__}: {exc}"[:300]
            return

        trace.opened = list(verdict.opened)
        trace.sufficient = bool(verdict.sufficient)
        trace.reason = verdict.reason
        if verdict.sufficient:
            res.outcome = "artifact"
            res.primary_ids = [o.service_id for o in verdict.primary_objects]
            res.supporting_ids = [o.service_id for o in verdict.supporting_objects]
            res.title = verdict.title
            res.artifact_words = _words(verdict.artifact_md)
            return
        deps.rejection_notes.append(services_revert_note(verdict, queries, cands))
        deps.services_reverted = True

    res.outcome = "fallback:no_confirmed_source"


# =========================================================================== #
# Dry run — the harness against itself, fully offline.
# =========================================================================== #


def _service_row(sid: str, title: str) -> dict[str, Any]:
    provider = title.split(" - ", 1)[0] if " - " in title else ""
    return {
        "id": sid, "service_ref": sid[:8], "service_name_ar": title,
        "provider_name": provider, "service_context": "", "intro_title": "",
        "intro_description": f"وصف {title}.",
        "steps": ["الدخول إلى المنصة", "تعبئة الطلب", "تأكيد الطلب"],
        "requirements": ["حساب فعّال"], "required_documents": ["الهوية"],
        "service_url": f"https://example.gov.sa/{sid[:8]}", "url": "",
    }


def _dry_catalogue() -> dict[str, list[dict]]:
    """Every gold service + one decoy per case sharing its words, so the fake
    ranking has something to beat."""
    rows: list[dict] = []
    for i, case in enumerate(SERVICE_CASES, 1):
        rows.append(_service_row(case.gold[0], case.gold_title))
        rows.append(_service_row(
            f"dec0{i:04d}-0000-4000-8000-000000000000",
            case.gold_title.split(" - ")[0] + " - الاستعلام عن طلب سابق",
        ))
    return {"services": rows, "service_guides": [], "service_guide_images": []}


def _tokens(text: str) -> set[str]:
    return {t for t in re.split(r"[\s\-–—،,؟?()«»]+", text or "") if len(t) > 2}


def _dry_supabase(registry: dict[float, str]) -> Any:
    from agents.simple_search.tests.conftest import FakeSupabase

    class _DrySupabase(FakeSupabase):
        def rpc(self, name: str, params: dict) -> Any:
            text = registry.get(params["p_query_embedding"][0], "")
            q = _tokens(text)
            scored = sorted(
                ((len(q & _tokens(r["service_name_ar"])) / (len(q) or 1), r)
                 for r in self.tables["services"]),
                key=lambda x: -x[0],
            )
            rows = [
                {"source_type": "service", "doc_id": r["id"], "source_id": r["id"],
                 "score": round(s, 4), "title": r["service_name_ar"], "topic_text": ""}
                for s, r in scored[: params.get("p_per_type", 15)] if s > 0
            ]
            return SimpleNamespace(execute=lambda: SimpleNamespace(data=rows))

    return _DrySupabase(_dry_catalogue())


def _dry_embedder(registry: dict[float, str]):
    async def _embed(texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for text in texts:
            key = float(len(registry))
            registry[key] = text
            out.append([key])
        return out

    return _embed


def _install_dry_models(case: EvalCase) -> None:
    """Scripted searcher + compliance agent for ONE case.

    Services: the searcher expands to the gold title's own words; the compliance
    agent opens [1] and writes. q01 additionally reverts once (insufficient, then
    sufficient on the next cycle), so the revert plumbing is exercised too.
    Negatives: the searcher aborts.
    """
    from pydantic_ai.messages import ModelResponse, ToolCallPart

    from agents.simple_search.compliance import ComplianceOutput
    from agents.simple_search.tests._fmodels import ScriptedModel

    def _searcher(messages, info):
        tool = info.output_tools[0]
        if case.negative:
            decision = SearcherDecision(aborted=True, abort_reason="سؤال نظامي (تشغيل تجريبي)")
        else:
            need = case.gold_title.split(" - ", 1)[-1]
            decision = SearcherDecision(data_type="services", service_queries=[need])
        return ModelResponse(parts=[ToolCallPart(tool_name=tool.name, args=decision.model_dump())])

    def _compliance(messages, info):
        text = "\n".join(
            str(getattr(p, "content", ""))
            for m in messages for p in (getattr(m, "parts", None) or [])
        )
        tool = info.output_tools[0]
        if case.cid.startswith("q01") and "<previous_attempts>" not in text:
            out = ComplianceOutput(
                sufficient=False,
                insufficient_reason="تشغيل تجريبي: المطلوب خدمة الأعمال لا الأفراد",
            )
            return ModelResponse(parts=[ToolCallPart(tool_name=tool.name, args=out.model_dump())])
        if "## [1]" not in text:
            return ModelResponse(parts=[ToolCallPart(
                tool_name="open_service", args={"numbers": [1]},
            )])
        out = ComplianceOutput(
            sufficient=True, primary=[1], artifact_title=case.gold_title.split(" - ", 1)[-1],
            artifact_md="الخطوات: الدخول إلى المنصة ثم تعبئة الطلب [1].", used_refs=[1],
        )
        return ModelResponse(parts=[ToolCallPart(tool_name=tool.name, args=out.model_dump())])

    searcher_model = ScriptedModel(_searcher).model
    compliance_model = ScriptedModel(_compliance).model
    searcher_mod.get_agent_model = lambda *_a, **_k: searcher_model  # type: ignore[assignment]
    compliance_mod.get_agent_model = lambda *_a, **_k: compliance_model  # type: ignore[assignment]


# =========================================================================== #
# Report.
# =========================================================================== #


def _hr(title: str) -> None:
    print("\n" + "=" * 88)
    print(title)
    print("=" * 88)


def _short(text: str, n: int = 90) -> str:
    t = " ".join((text or "").split())
    return t if len(t) <= n else t[: n - 1] + "…"


def _summarize(results: list[CaseResult], *, dry_run: bool) -> dict[str, Any]:
    svc = [r for r in results if not r.negative]
    neg = [r for r in results if r.negative]
    walls = [r.wall_s for r in results]
    summary: dict[str, Any] = {
        "dry_run": dry_run,
        "cases": len(results),
        "service": {
            "n": len(svc),
            "hit_at_2": sum(r.hit_at_2 for r in svc),
            "gold_retrieved": sum(r.gold_retrieved for r in svc),
            "ask_user": sum(r.outcome == "ask_user" for r in svc),
            "aborted": sum(r.outcome == "aborted" for r in svc),
            "fallback": sum(r.outcome.startswith("fallback") for r in svc),
            "mean_cycles": round(statistics.mean(r.cycles for r in svc), 2) if svc else 0,
            "mean_artifact_words": round(statistics.mean(
                r.artifact_words for r in svc if r.outcome == "artifact"
            ), 1) if any(r.outcome == "artifact" for r in svc) else 0,
        },
        "negative": {
            "n": len(neg),
            "aborted_by_searcher": sum(r.outcome == "aborted" for r in neg),
            "answered_on_services_path": sum(r.outcome == "artifact" for r in neg),
        },
        "wall_s": {
            "p50": round(statistics.median(walls), 2) if walls else 0,
            "max": max(walls) if walls else 0,
        },
        "cost_usd": {
            "total": round(sum(r.cost_usd for r in results), 4),
            "mean_service": round(statistics.mean(r.cost_usd for r in svc), 5) if svc else 0,
        },
    }

    _hr("PER CASE" + ("  (DRY RUN — measures the harness, not the system)" if dry_run else ""))
    for r in results:
        mark = ("HIT " if r.hit_at_2 else "miss") if not r.negative else (
            "ok  " if r.outcome == "aborted" else ("FAIL" if r.outcome == "artifact" else "~   ")
        )
        rank = f"gold@{r.gold_best_rank}" if r.gold_best_rank else ("gold@-" if not r.negative else "")
        print(f"{mark} {r.cid:<26} {r.outcome:<28} cyc={r.cycles} {rank:<8} "
              f"{r.wall_s:>6.1f}s ${r.cost_usd:.4f}  {_short(r.title or r.detail, 60)}")

    s, n = summary["service"], summary["negative"]
    _hr("SUMMARY")
    if s["n"]:
        print(f"service  primary hit@2        {s['hit_at_2']}/{s['n']}   (plan bar ≥ 13/15)")
        print(f"         gold retrieved@15    {s['gold_retrieved']}/{s['n']}")
        print(f"         ask_user             {s['ask_user']}/{s['n']}")
        print(f"         aborted / fallback   {s['aborted']} / {s['fallback']}")
        print(f"         mean cycles          {s['mean_cycles']}")
        print(f"         mean artifact words  {s['mean_artifact_words']}")
    if n["n"]:
        print(f"negative aborted by searcher  {n['aborted_by_searcher']}/{n['n']}")
        print(f"         answered here        {n['answered_on_services_path']}   (plan bar: 0)")
    print(f"wall-clock p50 / max          {summary['wall_s']['p50']}s / {summary['wall_s']['max']}s")
    print(f"cost total / mean per service ${summary['cost_usd']['total']} / "
          f"${summary['cost_usd']['mean_service']}")
    return summary


# =========================================================================== #
# Entry point.
# =========================================================================== #


def _select(only: str | None, limit: int | None) -> list[EvalCase]:
    cases = ALL_CASES
    if only:
        wanted = [w.strip() for w in only.split(",") if w.strip()]
        cases = [c for c in cases if any(c.cid == w or c.cid.startswith(w) for w in wanted)]
    if limit:
        cases = cases[:limit]
    return cases


def _service_client() -> Any:
    """A sync SERVICE-ROLE client — the anon key hits RLS and returns nothing."""
    from supabase import create_client

    url = os.environ["SUPABASE_URL"]
    key = os.environ.get("SUPABASE_SERVICE_KEY") or os.environ["SUPABASE_SERVICE_ROLE_KEY"]
    return create_client(url, key)


async def _main(args: argparse.Namespace) -> int:
    cases = _select(args.only, args.limit)
    if not cases:
        print("no case matches --only/--limit")
        return 2
    if os.environ.get("LUNA_TRACK_DISABLE", "").strip().lower() in ("1", "true", "yes"):
        print("WARNING: LUNA_TRACK_DISABLE is set — run_tracked records no usage, cost reads $0.")

    print(f"COMPLIANCE PATH EVAL — {len(cases)} case(s)"
          f"{' — DRY RUN (offline)' if args.dry_run else ' — LIVE'}")
    results: list[CaseResult] = []
    registry: dict[float, str] = {}
    supabase = _dry_supabase(registry) if args.dry_run else _service_client()
    embed_many = _dry_embedder(registry) if args.dry_run else None
    real_searcher_model = searcher_mod.get_agent_model
    real_compliance_model = compliance_mod.get_agent_model
    try:
        for case in cases:
            if args.dry_run:
                _install_dry_models(case)
            try:
                r = await run_case(case, supabase, embed_many=embed_many)
            except AuthStop as stop:
                print(f"\nSTOPPED at {case.cid}: a credential was rejected — {stop}")
                print("Refresh the key in .env (ALIBABA_API_KEY for embeddings) and re-run.")
                if stop.result is not None:
                    results.append(stop.result)
                    for t in stop.result.trace:
                        print(f"  cycle {t.cycle} service_queries: {t.queries}")
                break
            results.append(r)
            print(f"  {case.cid:<26} → {r.outcome:<28} {r.wall_s:>6.1f}s")
    finally:
        searcher_mod.get_agent_model = real_searcher_model  # type: ignore[assignment]
        compliance_mod.get_agent_model = real_compliance_model  # type: ignore[assignment]

    if not results:
        return 1
    summary = _summarize(results, dry_run=args.dry_run)
    default_name = (
        "compliance_path_eval_dryrun.json" if args.dry_run else "compliance_path_eval_results.json"
    )
    path = Path(args.json) if args.json else Path(__file__).with_name(default_name)
    path.write_text(
        json.dumps({"summary": summary, "results": [asdict(r) for r in results]},
                   ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"\nwrote {path}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    ap.add_argument("--only", default=None,
                    help="comma-separated case ids or id prefixes (q01,q05,neg-reg)")
    ap.add_argument("--limit", type=int, default=None, help="run the first N selected cases")
    ap.add_argument("--dry-run", action="store_true",
                    help="offline harness self-test: fake embedder + in-memory DB + scripted models")
    ap.add_argument("--json", default=None, help="results path (default: next to this file)")
    return asyncio.run(_main(ap.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
