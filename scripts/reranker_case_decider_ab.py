"""Case reranker A/B: OpenRouter decision models vs the live case reranker.

    extract  one user's case_search reranker runs → re-run retrieval on today's
             corpus → the exact reranker input (sectioned case_topics markdown)
    run      arms: ``qwen`` (the live ``run_reranker_for_query``, qwen3.5-flash)
             or decision models (one batch request per sub-query, 5-level score)
    score    agreement vs the qwen arm and vs the keeps recorded in prod, cost,
             latency

The planner brief is not stored in ``reranker_runs``, so every arm runs without
it. Retrieval is re-run because ``reranker_runs`` keeps the candidates but not
their matched topics, which ARE the reranker input.

    python scripts/reranker_case_decider_ab.py extract --email someone@example.com
    python scripts/reranker_case_decider_ab.py run --arm qwen
    python scripts/reranker_case_decider_ab.py run --arm liquid/d1-ultra
    python scripts/reranker_case_decider_ab.py score
"""
from __future__ import annotations

import argparse
import asyncio
import collections
import json
import re
import sys
import time
from pathlib import Path
from typing import Any

import httpx

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from scripts.reranker_decider_ab import _api_key as _main_key  # noqa: E402


def _api_key() -> str:
    """The dedicated reranker key (plan D6) when set, else the main OpenRouter key."""
    import os

    key = os.environ.get("OPENROUTER_RERANKER_API_KEY")
    if key:
        return key
    for line in (REPO / ".env").read_text(encoding="utf-8").splitlines():
        if line.startswith("OPENROUTER_RERANKER_API_KEY="):
            return line.split("=", 1)[1].strip().strip('"')
    return _main_key()

OUT_DIR = REPO / "agents_reports" / "reranker_model_ab" / "case_ab"
CASES_PATH = OUT_DIR / "cases.jsonl"
RAW_PATH = OUT_DIR / "raw.jsonl"
URL = "https://openrouter.ai/api/alpha/decisions"
TOP_N = 15          # mirrors SectionedRerankerNode._TOP_N_PER_QUERY
MAX_KEEP = 7        # mirrors loop.MAX_KEEP_PER_SUBQUERY
_CHANNEL_RE = re.compile(r"^\[(\w+)\]\s*")

# Distilled from case_search prompt_2 (the live case reranker prompt): the same
# evidence model (matched topics + summary, nothing else), the bidirectional
# موقف المحكمة, النوع as subject not merits, court level as authority only, the
# procedural rule and the mechanism forcing-function.
RUBRIC = (
    "Rayhan legal AI (Saudi law). Judge one candidate COURT RULING against the SUB-QUERY, for the "
    "aggregator that writes the final legal analysis. You do not see the ruling text: only its matched "
    "topics (الموضوعات المطابقة) and an optional summary (الملخص). Topic tags: [اسانيد · الطرف · موقف المحكمة] "
    "= a ground a party relied on and how the court treated it; [مبدأ · النوع] = a principle the ruling lays "
    "down (موضوعي/شكلي is the principle's subject, NOT a merits test; never demote شكلي); [وقائع] = facts. "
    "Judge only what is shown. The legal MECHANISM must match: a ruling on a different mechanism of the same "
    "family (انفساخ ≠ فسخ ≠ إبطال) is low at most. موقف المحكمة is bidirectional: when the sub-query asks what "
    "courts refuse, a rejected ground on exactly that point is valuable; a missing label is neutral. Court "
    "level (ابتدائي/استئناف/عليا) is authority, never relevance. A ruling decided only on jurisdiction, form or "
    "standing, with no reasoning on the merits, is low unless the sub-query is about that procedural issue. "
    "If several candidates restate the same holding, only the strongest deserves the top levels. A candidate "
    "showing (لا توجد بيانات لهذا الحكم) is not relevant."
)
LEVELS = [
    "not relevant — a different dispute or mechanism, or nothing usable is shown",
    "low — same area or same law, but nothing shown would be cited for this sub-query",
    "medium — on point but partial: a secondary aspect, an applicable principle, or thin operative evidence",
    "high — the court decides this sub-query's issue: a ground it relied on, accepted or rejected on exactly "
    "this point, or a principle it lays down on it",
    "critical — the operative holding (ratio) that directly decides exactly what the sub-query asks",
]
KEEP_MIN, HIGH_MIN = 1.5, 2.5

# v2 (2026-10-24): one rubric per search channel, the planner brief in the state, and a
# per-ruling "same mechanism?" yes/no beside the score. v1's main loss in the answer A/B was
# rulings on a neighbouring mechanism, concentrated in the principle channel (AUC 0.80).
RUBRIC_V2_HEAD = (
    "Rayhan legal AI (Saudi law). Judge one candidate COURT RULING against the SUB-QUERY, for the "
    "aggregator that writes the final legal analysis. When a <planner_brief> opens the message, read it "
    "first: it tells you the user's situation and which side of the dispute the user is on; it is "
    "background, never a keyword list. You do not see the ruling text: only its matched topics "
    "(الموضوعات المطابقة) and an optional summary (الملخص). Tags: [اسانيد · الطرف · موقف المحكمة] = a "
    "ground a party relied on and how the court treated it (أساس الحكم = the ground the court decided on; "
    "رُفض/لم يُعتد به = rejected; أُثير ولم يُبنَ عليه or لم تُناقَش = not engaged); [مبدأ · النوع] = a "
    "principle the ruling lays down (موضوعي/شكلي is its subject, never a reason to demote); [وقائع] = facts. "
    "Judge only what is shown. Court level (ابتدائي/استئناف/عليا) is authority, never relevance. موقف المحكمة "
    "is bidirectional: a rejected ground on exactly this point is valuable when the user needs to know what "
    "courts refuse. If several candidates restate the same holding, only the strongest deserves the top "
    "levels. A candidate showing (لا توجد بيانات لهذا الحكم) is not relevant."
)
RUBRIC_V2_CHANNEL = {
    "principle": (
        "This sub-query asks for a LEGAL RULE or doctrine. The ruling is relevant only if the principle it "
        "lays down, or the ground it decided on, is the SAME legal mechanism the sub-query names, not a "
        "neighbouring one in the same family (انفساخ ≠ فسخ ≠ إبطال; كفالة غرمية ≠ كفالة حضورية; ترك الخصومة ≠ "
        "سقوط الخصومة ≠ سبق الفصل). A ruling on the same law but a different mechanism is low at most. A ruling "
        "where the court actually decided on the rule beats one that only mentions it."
    ),
    "facts": (
        "This sub-query describes a FACT PATTERN. The ruling is relevant when its facts match the dispute's "
        "shape: the same kind of parties, transaction, and breach or event. Shared words are not enough; a "
        "ruling with similar facts but decided on an unrelated issue is medium at most."
    ),
    "basis": (
        "This sub-query asks about a STATUTORY or PROCEDURAL GROUND. The ruling is relevant when the same "
        "ground (the same article, rule or procedural defence) was raised and the court ruled on it, "
        "accepting or rejecting it. A ruling that only cites the same law for a different point is low."
    ),
}
LEVELS_V2 = [
    "not relevant — a different dispute or mechanism, or nothing usable is shown",
    "low — same area or same law, but nothing shown would be cited for this sub-query",
    "medium — on point but partial: a secondary aspect, an applicable principle, or thin operative evidence",
    "high — the court decides this sub-query's issue on exactly this mechanism: a ground it relied on, "
    "accepted or rejected, or a principle it lays down on it",
    "critical — the operative holding that directly decides exactly what the sub-query asks",
]
MECH_Q = (
    "Does this ruling deal with the SAME specific legal mechanism, ground or fact pattern the sub-query asks "
    "about — not merely the same law, chapter or area of law?"
)
BRIEFS_PATH = OUT_DIR / "briefs.txt"


def _briefs() -> dict[str, str]:
    """Turn id (first 8 chars of ura_id) → the planner brief production gave the reranker."""
    if not BRIEFS_PATH.exists():
        return {}
    out: dict[str, str] = {}
    for block in BRIEFS_PATH.read_text(encoding="utf-8").split("### ")[1:]:
        head, _, body = block.partition("\n")
        out[head.strip()] = body.strip()
    return out


def _sb():
    from shared.db.client import get_supabase_client

    return get_supabase_client()


def _jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


# -- extract --------------------------------------------------------------------


def cmd_extract(args) -> None:
    from agents.deep_search_v4.case_search.models import CaseSearchDeps, TypedQuery
    from agents.deep_search_v4.case_search.search import search_case_section
    from agents.deep_search_v4.case_search.unfold_reranker import format_bucket_for_reranker
    from agents.utils.embeddings import (
        embed_regulation_queries_alibaba,
        embed_regulation_query_alibaba,
    )

    sb = _sb()
    q = sb.table("retrieval_artifacts").select("ura_id,conversation_id,created_at,user_id").is_("deleted_at", "null")
    if args.email:
        user = sb.table("users").select("user_id").eq("email", args.email).single().execute().data
        q = q.eq("user_id", user["user_id"])
    if args.since:
        q = q.gte("created_at", args.since)
    uras = q.execute().data
    if args.exclude:
        drop = {u["user_id"] for u in sb.table("users").select("user_id").in_("email", args.exclude.split(",")).execute().data}
        uras = [u for u in uras if u["user_id"] not in drop]
    conv_of = {u["ura_id"]: u["conversation_id"] for u in uras}
    runs: list[dict] = []
    ids = list(conv_of)
    for i in range(0, len(ids), 50):
        runs += (sb.table("reranker_runs")
                   .select("run_id,ura_id,sub_query_index,sub_query_text,sub_query_rationale,"
                           "kept_results,dropped_results,sufficient,created_at")
                   .in_("ura_id", ids[i:i + 50]).eq("agent_family", "case_search").execute().data)
    runs.sort(key=lambda r: (r["created_at"], r["sub_query_index"]))
    print(f"{len(runs)} case_search runs across {len({r['ura_id'] for r in runs})} turns")

    async def main() -> list[dict]:
        texts = [r["sub_query_text"] for r in runs]
        embs: list[list[float]] = []
        for i in range(0, len(texts), 10):  # DashScope rejects batches over 10
            embs += await embed_regulation_queries_alibaba(texts[i:i + 10])
        deps = CaseSearchDeps(supabase=sb, embedding_fn=embed_regulation_query_alibaba)
        sem = asyncio.Semaphore(6)

        async def one(run: dict, emb: list[float]) -> dict | None:
            rationale = run["sub_query_rationale"] or ""
            m = _CHANNEL_RE.match(rationale)
            if not m:
                return None
            q = TypedQuery(text=run["sub_query_text"], channel=m.group(1), rationale=rationale[m.end():])
            async with sem:
                outcome = await search_case_section(q, deps, precomputed_embedding=emb)
            cands = outcome.candidates[:TOP_N]
            if outcome.error or not cands:
                return {"skip": outcome.error or "no_candidates"}
            md, _ = format_bucket_for_reranker(cands, bucket_label=f"q{run['sub_query_index'] + 1}_{q.channel}")
            hist_kept = {c["ref_id"]: c.get("relevance") or "medium" for c in run["kept_results"] or []}
            hist_all = set(hist_kept) | {c["ref_id"] for c in run["dropped_results"] or []}
            now_ids = [c.case_id for c in cands]
            return {
                "case_id": f"run:{run['run_id'][:8]}",
                "ura_id": run["ura_id"],
                "convo": conv_of[run["ura_id"]],
                "sub_query_index": run["sub_query_index"],
                "channel": q.channel,
                "query": q.text,
                "rationale": rationale,
                "raw_markdown": md,
                "positions": {str(i + 1): cid for i, cid in enumerate(now_ids)},
                "hist_kept": hist_kept,
                "hist_sufficient": run["sufficient"],
                "hist_overlap": round(len(hist_all & set(now_ids)) / max(1, len(hist_all)), 3),
                "created_at": run["created_at"],
            }

        return await asyncio.gather(*[one(r, e) for r, e in zip(runs, embs)])

    rows = asyncio.run(main())
    skipped = collections.Counter(r["skip"] if r and "skip" in r else "no_channel"
                                  for r in rows if not r or "skip" in r)
    cases = [r for r in rows if r and "skip" not in r]
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    CASES_PATH.write_text("".join(json.dumps(c, ensure_ascii=False) + "\n" for c in cases), encoding="utf-8")
    ov = sorted(c["hist_overlap"] for c in cases)
    print(f"wrote {len(cases)} cases -> {CASES_PATH.relative_to(REPO)}; skipped {dict(skipped)}")
    if ov:
        print(f"candidate overlap with the prod run: median {ov[len(ov) // 2]:.2f}, "
              f"≥0.8 on {sum(o >= 0.8 for o in ov)}/{len(ov)}")


# -- briefs ---------------------------------------------------------------------

_BRIEF_SQL = """
SELECT regexp_match(attributes->'gen_ai.input.messages'->0->'parts'->0->>'content', '## Sub-query\\n([^\\n]+)')[1] AS subq,
       max(regexp_match(attributes->'gen_ai.input.messages'->0->'parts'->0->>'content',
                        '(?s)<planner_brief>\\n  (.*?)\\n</planner_brief>')[1]) AS brief
FROM records
WHERE span_name LIKE 'chat %' AND attributes->>'gen_ai.agent.name' = 'case_search_reranker'
  AND deployment_environment = 'production'
GROUP BY regexp_match(attributes->'gen_ai.input.messages'->0->'parts'->0->>'content', '## Sub-query\\n([^\\n]+)')[1]
"""


def cmd_briefs(args) -> None:
    """Recover each turn's planner brief from the reranker prompts Logfire kept (~30 days).

    The brief is not stored in the database. Needs a read token in LOGFIRE_READ_TOKEN
    (`logfire --region eu read-tokens --project punpuntripping/rihan create`).
    """
    import datetime as dt
    import os

    from logfire.query_client import LogfireQueryClient

    token = os.environ["LOGFIRE_READ_TOKEN"]
    found: dict[str, str] = {}
    end = dt.datetime.now(dt.timezone.utc)
    with LogfireQueryClient(read_token=token, base_url="https://logfire-eu.pydantic.dev", timeout=120) as client:
        for _ in range(3):   # 14-day windows back past the retention edge
            start = end - dt.timedelta(days=14)
            res = client.query_json_rows(sql=_BRIEF_SQL, min_timestamp=start, max_timestamp=end, limit=10000)
            for row in res["rows"]:
                if row.get("subq") and row.get("brief"):
                    found.setdefault(row["subq"], row["brief"])
            end = start
    cases = _jsonl(CASES_PATH)
    turns: dict[str, list[dict]] = collections.defaultdict(list)
    for c in cases:
        turns[c["ura_id"][:8]].append(c)
    out, missing = [], []
    for tid, cs in turns.items():
        brief = next((found[c["query"]] for c in cs if c["query"] in found), None)
        if brief:
            out.append(f"### {tid}\n{brief}\n")
        else:
            missing.append(tid)
    BRIEFS_PATH.write_text("".join(out), encoding="utf-8")
    print(f"briefs: {len(out)}/{len(turns)} turns recovered ({len(found)} sub-queries seen in Logfire); "
          f"none for {missing}")


# -- forum ----------------------------------------------------------------------

# v3 (2026-10-25): which court would hear each sub-query's dispute. One `choice` question per
# sub-query (state = sub-query + brief, no candidates: ~$0.00001 each). The answer A/B turns it
# into a soft court-fit factor per candidate (scripts/reranker_case_answer_ab.py, d1u_v3).
FORUM_Q = ("Which Saudi court would decide the dispute this sub-query is about? Choose `any` when the "
           "sub-query asks about a rule of evidence or procedure that every court applies.")
FORUM_CRITERIA = {
    "personal": "personal status court: marriage, divorce, custody, maintenance, guardianship, inheritance",
    "labour": "labour court: employment contracts, wages, end-of-service, work injuries",
    "commercial": "commercial court: companies, partners, merchants, commercial contracts, cheques and promissory notes, bankruptcy",
    "criminal": "criminal court: public prosecution, crimes and penalties, narcotics, criminal procedure",
    "administrative": "administrative court (Board of Grievances): government decisions, civil servants, government contracts, nationality",
    "general": "general court: real estate ownership, civil disputes between individuals, traffic, family property and endowments",
    "any": "any court: a rule of evidence or procedure that every court applies",
}


def cmd_forum(args) -> None:
    from agents.deep_search_v4.case_search.prompts import build_reranker_user_message

    cases = _jsonl(CASES_PATH)
    done = {r["case_id"] for r in _jsonl(RAW_PATH) if r["arm"] == "forum" and r.get("ok")}
    todo = [c for c in cases if c["case_id"] not in done]
    briefs = _briefs()
    print(f"forum: running {len(todo)} ({len(done)} already done)")

    async def one(client, sem, c):
        state = build_reranker_user_message(c["query"], c["rationale"], "", planner_brief=briefs.get(c["ura_id"][:8]))
        body = {"model": "liquid/d1-ultra", "state": state,
                "questions": {"FORUM": {"type": "choice", "instructions": FORUM_Q, "criteria": FORUM_CRITERIA}},
                "provider": {"zdr": True, "data_collection": "deny"}}
        async with sem:
            for attempt in range(3):
                try:
                    r = await client.post(URL, json=body, timeout=60)
                    j = r.json()
                    if r.status_code == 200 and "answers" in j:
                        return {"case_id": c["case_id"], "arm": "forum", "ok": True,
                                "probs": j["answers"]["FORUM"]["probabilities"],
                                "cost": (j.get("usage") or {}).get("cost") or 0.0}
                except (httpx.HTTPError, json.JSONDecodeError, KeyError):
                    pass
                await asyncio.sleep(1 + 2 * attempt)
        return {"case_id": c["case_id"], "arm": "forum", "ok": False}

    async def main():
        sem = asyncio.Semaphore(args.concurrency)
        async with httpx.AsyncClient(headers={"Authorization": f"Bearer {_api_key()}"}) as client:
            rows = await asyncio.gather(*[one(client, sem, c) for c in todo])
        with RAW_PATH.open("a", encoding="utf-8") as fh:
            fh.writelines(json.dumps(r, ensure_ascii=False) + "\n" for r in rows)
        print(f"forum: {sum(r['ok'] for r in rows)}/{len(rows)} ok, cost ${sum(r.get('cost', 0) for r in rows):.4f}")

    asyncio.run(main())


# -- run ------------------------------------------------------------------------


async def _run_qwen(case: dict, sem: asyncio.Semaphore, arm: str = "qwen", brief: str | None = None) -> dict:
    from agents.deep_search_v4.case_search.reranker import run_reranker_for_query
    from agents.utils.agent_models import cost_usd

    async with sem:
        t0 = time.perf_counter()
        try:
            rqr, usage, log = await run_reranker_for_query(
                query=case["query"], rationale=case["rationale"],
                raw_markdown=case["raw_markdown"], max_keep=MAX_KEEP, planner_brief=brief,
                engine="llm",  # the qwen arm, whatever CASE_RERANKER_ENGINE says
            )
        except Exception as exc:  # noqa: BLE001 - one dead call must not kill the sweep
            return {"case_id": case["case_id"], "arm": arm, "ok": False, "error": str(exc)[:300]}
    keeps = {str(d["position"]): d.get("relevance") or "medium" for d in log if d.get("action") == "keep"}
    return {
        "case_id": case["case_id"], "arm": arm, "ok": not rqr.summary_note.startswith("خطأ في التصنيف"),
        "duration_s": time.perf_counter() - t0, "keeps": keeps, "sufficient": rqr.sufficient,
        "requests": sum(u.get("requests", 0) for u in usage),
        "input_tokens": sum(u.get("input_tokens", 0) for u in usage),
        "output_tokens": sum(u.get("output_tokens", 0) for u in usage),
        "cost": sum(cost_usd(u.get("model"), u.get("input_tokens", 0), u.get("output_tokens", 0)) for u in usage),
        "model": usage[0].get("model") if usage else None,
    }


async def _run_decider(client: httpx.AsyncClient, arm: str, case: dict, sem: asyncio.Semaphore,
                       brief: str | None = None) -> dict:
    """``arm`` is ``<model>`` (v1 rubric, no brief) or ``<model>@v2`` (channel rubric + brief + mechanism)."""
    from agents.deep_search_v4.case_search.prompts import build_reranker_user_message

    model, _, ver = arm.partition("@")
    v2 = ver == "v2"
    state = build_reranker_user_message(case["query"], case["rationale"], case["raw_markdown"],
                                        planner_brief=brief if v2 else None)
    if v2:
        rubric = f"{RUBRIC_V2_HEAD}\n\n{RUBRIC_V2_CHANNEL.get(case['channel'], '')}"
        questions = {}
        for p in case["positions"]:
            questions[f"R{p}"] = {"type": "score", "criteria": LEVELS_V2,
                                  "instructions": f"{rubric}\n\nRate ONLY the ruling whose header is `### [{p}]`."}
            questions[f"M{p}"] = {"type": "noul",
                                  "instructions": f"{MECH_Q}\n\nAnswer ONLY for the ruling whose header is `### [{p}]`.",
                                  "criteria": {"true": "the same mechanism, ground or fact pattern",
                                               "false": "only the same law, area or wording"}}
    else:
        questions = {
            f"R{p}": {"type": "score", "criteria": LEVELS,
                      "instructions": f"{RUBRIC}\n\nRate ONLY the ruling whose header is `### [{p}]`."}
            for p in case["positions"]
        }
    body = {"model": model, "state": state, "questions": questions,
            "provider": {"zdr": True, "data_collection": "deny"}}
    err = ""
    async with sem:
        for attempt in range(3):
            t0 = time.perf_counter()
            try:
                r = await client.post(URL, json=body, timeout=60)
                dt = time.perf_counter() - t0
                j = r.json()
                if r.status_code == 200 and "answers" in j:
                    a = j["answers"]
                    scores = {p: (a.get(f"R{p}") or {}).get("score") for p in case["positions"]}
                    mech = {p: (a.get(f"M{p}") or {}).get("noul") for p in case["positions"]} if v2 else None
                    usage = j.get("usage") or {}
                    return {"case_id": case["case_id"], "arm": arm, "ok": None not in scores.values(),
                            "duration_s": dt, "scores": scores, "mech": mech, "served": j.get("model"),
                            "requests": attempt + 1, "input_tokens": usage.get("input_tokens", 0),
                            "cost": usage.get("cost") or 0.0}
                err = f"{r.status_code}: {str(j.get('error', j))[:300]}"
                if r.status_code in (400, 402, 404, 413):
                    break
            except (httpx.HTTPError, json.JSONDecodeError) as exc:
                err = f"{type(exc).__name__}: {exc}"[:300]
            await asyncio.sleep(1 + 2 * attempt)
    return {"case_id": case["case_id"], "arm": arm, "ok": False, "error": err}


def cmd_run(args) -> None:
    cases = _jsonl(CASES_PATH)
    rows = _jsonl(RAW_PATH)
    done = {(r["arm"], r["case_id"]) for r in rows if r.get("ok")}
    briefs = _briefs()
    brief_of = lambda c: briefs.get(c["ura_id"][:8])  # noqa: E731
    if args.arm == "qwen_b":
        # qwen WITH the planner brief: only turns that have one need a new call; the others are
        # the plain qwen run unchanged (same input), copied under the new arm name.
        qwen = {r["case_id"]: r for r in rows if r["arm"] == "qwen" and r.get("ok")}
        copied = [{**qwen[c["case_id"]], "arm": "qwen_b", "copied_from": "qwen"} for c in cases
                  if not brief_of(c) and ("qwen_b", c["case_id"]) not in done and c["case_id"] in qwen]
        with RAW_PATH.open("a", encoding="utf-8") as fh:
            fh.writelines(json.dumps(r, ensure_ascii=False) + "\n" for r in copied)
        done |= {("qwen_b", r["case_id"]) for r in copied}
        print(f"qwen_b: copied {len(copied)} brief-less sub-queries from the qwen arm")
    pending = [c for c in cases if (args.arm, c["case_id"]) not in done]
    todo = pending[: args.limit or None]
    print(f"{args.arm}: running {len(todo)} ({len(cases) - len(pending)} already done)")
    if args.arm.startswith("qwen"):  # cost_usd reads the pricing cache, which only FastAPI startup fills
        from shared.pricing.registry import load_pricing

        load_pricing(_sb())

    async def main() -> None:
        sem = asyncio.Semaphore(args.concurrency)
        headers = {"Authorization": f"Bearer {_api_key()}", "X-Title": "luna-case-reranker-decider-ab"}
        async with httpx.AsyncClient(headers=headers) as client:
            tasks = [_run_qwen(c, sem, args.arm, brief_of(c) if args.arm == "qwen_b" else None)
                     if args.arm.startswith("qwen") else _run_decider(client, args.arm, c, sem, brief_of(c))
                     for c in todo]
            n = 0
            with RAW_PATH.open("a", encoding="utf-8") as fh:
                for fut in asyncio.as_completed(tasks):
                    res = await fut
                    fh.write(json.dumps(res, ensure_ascii=False) + "\n")
                    fh.flush()
                    n += 1
                    if not res.get("ok"):
                        print(f"  fail {res['case_id']}: {res.get('error', 'missing scores')}", flush=True)
                    if n % 25 == 0:
                        print(f"  {n}/{len(todo)}", flush=True)

    asyncio.run(main())


# -- score ----------------------------------------------------------------------


def _auc(pos: list[float], neg: list[float]) -> float:
    if not pos or not neg:
        return float("nan")
    allv = sorted([(v, 1) for v in pos] + [(v, 0) for v in neg])
    rank_sum, i = 0.0, 0
    while i < len(allv):  # average ranks over ties
        j = i
        while j < len(allv) and allv[j][0] == allv[i][0]:
            j += 1
        rank_sum += sum(1 for k in range(i, j) if allv[k][1]) * (i + j + 1) / 2
        i = j
    return (rank_sum - len(pos) * (len(pos) + 1) / 2) / (len(pos) * len(neg))


def _vs(ref_keep: dict[str, dict[str, str]], scores: dict[str, dict[str, float]]) -> dict[str, Any]:
    """Decider scores against a reference keep set, per sub-query."""
    pos, neg, top_hit, ref_n, thr_keep, thr_hit, hi_ref, hi_hit = [], [], 0, 0, 0, 0, 0, 0
    for cid, ref in ref_keep.items():
        sc = scores.get(cid)
        if not sc:
            continue
        for p, v in sc.items():
            (pos if p in ref else neg).append(v)
        ranked = sorted(sc, key=lambda p: -sc[p])
        top_hit += len(set(ranked[: len(ref)]) & set(ref))
        ref_n += len(ref)
        kept = {p for p, v in sc.items() if v >= KEEP_MIN}
        thr_keep += len(kept)
        thr_hit += len(kept & set(ref))
        hi = {p for p, r in ref.items() if r == "high"}
        hi_ref += len(hi)
        hi_hit += len(hi & kept)
    return {"auc": round(_auc(pos, neg), 3), "ref_keeps": ref_n,
            "recall_at_ref_count": round(top_hit / max(1, ref_n), 3),
            "keeps_at_1.5": thr_keep, "recall_at_1.5": round(thr_hit / max(1, ref_n), 3),
            "precision_at_1.5": round(thr_hit / max(1, thr_keep), 3),
            "ref_high_kept": f"{hi_hit}/{hi_ref}"}


def cmd_score(args) -> None:
    cases = {c["case_id"]: c for c in _jsonl(CASES_PATH)}
    raw: dict[str, dict[str, dict]] = collections.defaultdict(dict)
    for r in _jsonl(RAW_PATH):
        if r.get("ok"):
            raw[r["arm"]][r["case_id"]] = r
    qwen = raw.get("qwen_b") or raw.get("qwen", {})   # the brief-fed run is the fairer reference
    qwen_keep = {cid: r["keeps"] for cid, r in qwen.items()}
    # Prod's recorded keeps, mapped onto today's positions (case uuid -> position).
    hist_keep = {}
    for cid, c in cases.items():
        pos_of = {v: k for k, v in c["positions"].items()}
        hist_keep[cid] = {pos_of[u]: rel for u, rel in c["hist_kept"].items() if u in pos_of}
    report: dict[str, Any] = {"cases": len(cases)}

    # Noise ceiling: the live qwen re-run vs prod's own recorded keeps on the same candidates.
    both = [cid for cid in qwen_keep if cid in hist_keep and cases[cid]["hist_overlap"] >= 0.8]
    agree = sum(len(set(qwen_keep[c]) & set(hist_keep[c])) for c in both)
    report["qwen_rerun_vs_prod"] = {
        "sub_queries": len(both), "prod_keeps": sum(len(hist_keep[c]) for c in both),
        "rerun_keeps": sum(len(qwen_keep[c]) for c in both),
        "rerun_recall_of_prod": round(agree / max(1, sum(len(hist_keep[c]) for c in both)), 3),
    }
    for arm, rows in sorted(raw.items()):
        stats: dict[str, Any] = {
            "ok": len(rows), "failed": sum(1 for r in _jsonl(RAW_PATH) if r["arm"] == arm and not r.get("ok")),
            "cost_total": round(sum(r.get("cost") or 0 for r in rows.values()), 4),
            "cost_per_sub_query": round(sum(r.get("cost") or 0 for r in rows.values()) / max(1, len(rows)), 6),
        }
        durs = sorted(r["duration_s"] for r in rows.values() if r.get("duration_s"))
        if durs:
            stats["latency_p50_s"] = round(durs[len(durs) // 2], 2)
            stats["latency_p90_s"] = round(durs[int(len(durs) * 0.9)], 2)
        turns = collections.defaultdict(float)
        for cid, r in rows.items():
            turns[cases[cid]["ura_id"]] += r.get("cost") or 0
        stats["cost_per_turn"] = round(sum(turns.values()) / max(1, len(turns)), 5)
        if not arm.startswith("qwen"):
            scores = {cid: {p: float(v) for p, v in r["scores"].items()} for cid, r in rows.items()}
            stats["vs_qwen_rerun"] = _vs(qwen_keep, scores)
            stats["vs_prod_recorded"] = _vs({c: k for c, k in hist_keep.items()
                                             if cases[c]["hist_overlap"] >= 0.8}, scores)
        else:
            stats["keeps_total"] = sum(len(r["keeps"]) for r in rows.values())
        report[arm] = stats
    out = OUT_DIR / "scores.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dir", default="case_ab", help="test-set folder under agents_reports/reranker_model_ab/")
    sub = ap.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("extract")
    e.add_argument("--email", help="one user's turns (else every user, see --since/--exclude)")
    e.add_argument("--since", help="only turns created on/after this date (YYYY-MM-DD)")
    e.add_argument("--exclude", help="comma-separated emails to leave out")
    r = sub.add_parser("run")
    r.add_argument("--arm", required=True, help="qwen | an OpenRouter decision model id")
    r.add_argument("--concurrency", type=int, default=8)
    r.add_argument("--limit", type=int, default=0)
    sub.add_parser("score")
    sub.add_parser("briefs")
    f = sub.add_parser("forum")
    f.add_argument("--concurrency", type=int, default=8)
    args = ap.parse_args()
    _use_dir(args.dir)
    {"extract": cmd_extract, "briefs": cmd_briefs, "forum": cmd_forum, "run": cmd_run,
     "score": cmd_score}[args.cmd](args)


def _use_dir(name: str) -> None:
    """Point every path at one test-set folder (case_ab = the single-user set, case_ab2 = the multi-user set)."""
    global OUT_DIR, CASES_PATH, RAW_PATH, BRIEFS_PATH
    OUT_DIR = REPO / "agents_reports" / "reranker_model_ab" / name
    CASES_PATH, RAW_PATH, BRIEFS_PATH = OUT_DIR / "cases.jsonl", OUT_DIR / "raw.jsonl", OUT_DIR / "briefs.txt"


if __name__ == "__main__":
    main()
