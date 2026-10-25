"""A/B replay — case expander prompt_3 vs prompt_4 on real prod inputs.

Plan: .claude/plans/case_expander_prompt_4.md §5.

Each input is the EXACT user message a prod case expander received (pulled
from Logfire `pydantic_ai.all_messages[0]`), so both prompts see byte-identical
input. Per input and prompt:

  Phase A (every input) — expander → batch-embed → `search_case_section` per
  query, i.e. the same calls `SectionedSearchNode` makes. Recorded: the queries,
  channels used, law / article mentions absent from the input, statute-lookup
  shapes, near-duplicate query pairs (embedding cosine), and the rulings each
  query retrieves — so "how many NEW rulings does each extra query add" can be
  read directly.

  Phase B (first --rerank N inputs) — the per-query reranker on each query's
  own candidates (`run_reranker_for_query`, planner_brief threaded the way the
  loop threads it). Recorded: the rulings kept. This is the number that says
  whether fewer queries lost real rulings or only duplicates.

Inputs carry client names: keep them and the outputs OUT of git
(agents_reports/ is gitignored).

Keys: local `.env` is stale since the 2026-10-22/24 rotations. `--railway-env`
copies an allowlist of provider + Supabase variables from
`railway variables --service luna-backend --kv` into this process's env
(never printed, never written). LOGFIRE_TOKEN is deliberately NOT copied, so the
replay does not land in prod telemetry.

Usage (repo root):
    python scripts/case_expander_ab.py --railway-env \\
        --inputs agents_reports/case_expander_ab/inputs.json \\
        --out agents_reports/case_expander_ab --rerank 15
"""
from __future__ import annotations

import argparse
import asyncio
import html
import json
import math
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

_RAILWAY_ALLOW = re.compile(r"^(SUPABASE_|ALIBABA_|BASETEN_|OPENROUTER_)")

PROMPTS = ("prompt_3", "prompt_4")

_LAW = re.compile(r"(?:نظام|لائحة|اللائحة التنفيذية لنظام)\s+(\S+(?:\s+\S+)?)")
_ARTICLE = re.compile(r"(?:المادة|المادتان|المادتين)\s+(\S+)")
_STATUTE_SHAPE = re.compile(r"المواد المتعلقة|مواد نظام|أحكام .{0,60} في نظام|تطبيق المادة|وفق نظام")
_CAP = re.compile(r"Produce \*\*at most (\d+)\*\* queries")
_BRIEF = re.compile(r'<block label="planner_brief">\s*(.*?)\s*</block>', re.S)

DUP_COSINE = 0.85


def load_railway_env() -> None:
    exe = shutil.which("railway") or "railway"  # Windows: an npm .cmd shim
    out = subprocess.run(
        [exe, "variables", "--service", "luna-backend", "--kv"],
        capture_output=True, text=True, encoding="utf-8", check=True,
    ).stdout
    n = 0
    for line in out.splitlines():
        key, sep, value = line.partition("=")
        if sep and _RAILWAY_ALLOW.match(key):
            os.environ[key] = value
            n += 1
    print(f"[env] copied {n} allowlisted variables from Railway (values not shown)")


def invented_mentions(text: str, user_msg: str) -> list[str]:
    """Law / article mentions in a query whose head token is not in the input."""
    found = []
    for m in _LAW.finditer(text):
        if m.group(1).split()[0] not in user_msg:
            found.append(m.group(0))
    for m in _ARTICLE.finditer(text):
        if m.group(1) not in user_msg:
            found.append(m.group(0))
    return found


def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


async def run_one(item: dict, prompt_key: str, deps, rerank: bool) -> dict:
    from agents.deep_search_v4.case_search.expander import EXPANDER_LIMITS, create_expander_agent
    from agents.deep_search_v4.case_search.reranker import run_reranker_for_query
    from agents.deep_search_v4.case_search.search import search_case_section
    from agents.deep_search_v4.case_search.unfold_reranker import format_bucket_for_reranker
    from agents.utils.embeddings import embed_regulation_queries_alibaba

    msg = item["user_msg"]
    rec: dict = {"trace_id": item["trace_id"], "prompt": prompt_key}
    t0 = time.perf_counter()
    try:
        result = await create_expander_agent(prompt_key=prompt_key).run(
            msg, usage_limits=EXPANDER_LIMITS
        )
    except Exception as e:  # noqa: BLE001 — record and move on
        rec["error"] = f"expander: {type(e).__name__}: {e}"[:300]
        return rec
    queries = list(result.output.queries)
    cap = _CAP.search(msg)
    if cap:  # editorial runs: the loop keeps the first N in emitted order
        queries = queries[: int(cap.group(1))]
    usage = result.usage()
    rec["expander_s"] = round(time.perf_counter() - t0, 1)
    rec["expander_tokens_out"] = usage.output_tokens
    rec["queries"] = [
        {
            "text": q.text,
            "channel": q.channel,
            "rationale": q.rationale,
            "invented": invented_mentions(q.text, msg),
            "statute_shape": bool(_STATUTE_SHAPE.search(q.text)),
        }
        for q in queries
    ]
    if not queries:
        return rec

    # DashScope text-embedding-v4 rejects batches > 10 (400 InvalidParameter),
    # while embeddings.MAX_EMBED_BATCH is 25 — chunk here so an 11+ query
    # run is measured instead of crashing the replay.
    texts = [q.text for q in queries]
    embs = []
    for k in range(0, len(texts), 10):
        embs += await embed_regulation_queries_alibaba(texts[k : k + 10])
    rec["dup_pairs"] = [
        [i, j, round(cosine(embs[i], embs[j]), 3)]
        for i in range(len(queries))
        for j in range(i + 1, len(queries))
        if cosine(embs[i], embs[j]) >= DUP_COSINE
    ]
    outcomes = await asyncio.gather(*[
        search_case_section(query=q, deps=deps, precomputed_embedding=e)
        for q, e in zip(queries, embs)
    ])
    for qrec, o in zip(rec["queries"], outcomes):
        qrec["retrieved"] = [c.case_id for c in o.candidates]
        qrec["max_score"] = round(o.max_score, 4)
        qrec["search_error"] = o.error

    if rerank:
        brief_m = _BRIEF.search(msg)
        brief = html.unescape(brief_m.group(1)) if brief_m else None

        async def _rerank(q, cands):
            if not cands:
                return []
            md, _ = format_bucket_for_reranker(cands, bucket_label=q.channel)
            _res, _usage, log = await run_reranker_for_query(
                query=q.text,
                rationale=f"[{q.channel}] {q.rationale}",
                raw_markdown=md,
                max_keep=deps.reranker_max_keep,
                planner_brief=brief,
                engine="llm",  # pin qwen, whatever CASE_RERANKER_ENGINE says
            )
            keeps = [d for d in log if d.get("action") == "keep"]
            return [
                cands[d["position"] - 1].case_id
                for d in keeps
                if 1 <= d.get("position", 0) <= len(cands)
            ]

        # The loop reranks only each query's head (SectionedRerankerNode._TOP_N_PER_QUERY).
        from agents.deep_search_v4.case_search.loop import SectionedRerankerNode

        top_n = SectionedRerankerNode._TOP_N_PER_QUERY
        kept_lists = await asyncio.gather(
            *[_rerank(q, o.candidates[:top_n]) for q, o in zip(queries, outcomes)],
            return_exceptions=True,
        )
        for qrec, kept in zip(rec["queries"], kept_lists):
            qrec["kept"] = kept if isinstance(kept, list) else []
            if not isinstance(kept, list):
                qrec["rerank_error"] = f"{type(kept).__name__}: {kept}"[:200]
    return rec


def summarise(results: list[dict], inputs: list[dict], reranked_ids: set[str]) -> str:
    by = {(r["trace_id"], r["prompt"]): r for r in results}
    lines = ["# Case expander A/B — prompt_3 vs prompt_4", ""]

    def agg(prompt: str, only: set[str] | None = None) -> dict:
        rs = [by.get((i["trace_id"], prompt)) for i in inputs]
        rs = [r for r in rs if r and "queries" in r and (only is None or r["trace_id"] in only)]
        qs = [q for r in rs for q in r["queries"]]
        n = len(rs) or 1
        retrieved = [set(c for q in r["queries"] for c in q.get("retrieved", [])) for r in rs]
        per_query = [len(q.get("retrieved", [])) for q in qs]
        kept = [set(c for q in r["queries"] for c in q.get("kept", [])) for r in rs]
        return {
            "runs": len(rs),
            "queries/run": round(len(qs) / n, 2),
            "runs using all 3 channels": sum(len({q["channel"] for q in r["queries"]}) == 3 for r in rs),
            "queries with invented law/article": sum(bool(q["invented"]) for q in qs),
            "statute-lookup shaped queries": sum(q["statute_shape"] for q in qs),
            "near-duplicate pairs (cos≥0.85)": sum(len(r.get("dup_pairs", [])) for r in rs),
            "distinct rulings retrieved/run": round(sum(map(len, retrieved)) / n, 1),
            "retrieval redundancy (hits ÷ distinct)": round(sum(per_query) / max(1, sum(map(len, retrieved))), 2),
            "distinct rulings KEPT/run": round(sum(map(len, kept)) / n, 1) if only else None,
            "expander errors": sum(1 for i in inputs if "error" in by.get((i["trace_id"], prompt), {})),
        }

    for title, only in (("All runs (Phase A)", None), (f"Reranked subset (Phase B, {len(reranked_ids)} runs)", reranked_ids)):
        a, b = agg("prompt_3", only), agg("prompt_4", only)
        lines += [f"## {title}", "", "| metric | prompt_3 | prompt_4 |", "|---|---|---|"]
        for k in a:
            if a[k] is None:
                continue
            lines.append(f"| {k} | {a[k]} | {b[k]} |")
        lines.append("")

    # Phase B overlap: what each prompt kept that the other did not.
    lines += ["## Kept-ruling overlap (Phase B)", "", "| trace | kept p3 | kept p4 | both | only p3 | only p4 |", "|---|---|---|---|---|---|"]
    for i in inputs:
        if i["trace_id"] not in reranked_ids:
            continue
        k3 = set(c for q in by.get((i["trace_id"], "prompt_3"), {}).get("queries", []) for c in q.get("kept", []))
        k4 = set(c for q in by.get((i["trace_id"], "prompt_4"), {}).get("queries", []) for c in q.get("kept", []))
        lines.append(f"| {i['trace_id'][:10]} | {len(k3)} | {len(k4)} | {len(k3 & k4)} | {len(k3 - k4)} | {len(k4 - k3)} |")
    lines.append("")

    lines += ["## Side by side", ""]
    for i in inputs:
        focus = i["user_msg"].split("User context:")[0].replace("Focus instructions:", "").strip()
        lines += [f"### {i['trace_id'][:10]} — {focus[:160]}", ""]
        for p in PROMPTS:
            r = by.get((i["trace_id"], p), {})
            lines.append(f"**{p}**" + (f" — ERROR {r['error']}" if "error" in r else ""))
            for q in r.get("queries", []):
                flag = " ⚠invented:" + "، ".join(q["invented"]) if q["invented"] else ""
                kept = f" · kept {len(q['kept'])}" if "kept" in q else ""
                lines.append(f"- `{q['channel']}` {q['text']} — {len(q.get('retrieved', []))} rulings{kept}{flag}")
            lines.append("")
    return "\n".join(lines)


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--inputs", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--rerank", type=int, default=15, help="run the reranker on the first N inputs")
    ap.add_argument("--limit", type=int, default=0, help="only the first N inputs (0 = all)")
    ap.add_argument("--concurrency", type=int, default=4)
    ap.add_argument("--railway-env", action="store_true")
    args = ap.parse_args()

    if args.railway_env:
        load_railway_env()

    from agents.deep_search_v4.case_search.models import CaseSearchDeps
    from agents.utils.embeddings import embed_regulation_query
    from shared.db.client import get_supabase_client

    inputs = json.loads(Path(args.inputs).read_text(encoding="utf-8"))
    if args.limit:
        inputs = inputs[: args.limit]
    reranked_ids = {i["trace_id"] for i in inputs[: args.rerank]}
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    raw_path = out / "results.jsonl"
    done = set()
    if raw_path.exists():  # resume
        for line in raw_path.read_text(encoding="utf-8").splitlines():
            r = json.loads(line)
            if "error" not in r:
                done.add((r["trace_id"], r["prompt"]))

    deps = CaseSearchDeps(supabase=get_supabase_client(), embedding_fn=embed_regulation_query)
    sem = asyncio.Semaphore(args.concurrency)

    async def guarded(item: dict, p: str) -> None:
        if (item["trace_id"], p) in done:
            return
        async with sem:
            try:
                rec = await run_one(item, p, deps, item["trace_id"] in reranked_ids)
            except Exception as e:  # noqa: BLE001 — one bad run must not sink the rest
                rec = {"trace_id": item["trace_id"], "prompt": p,
                       "error": f"run: {type(e).__name__}: {e}"[:300]}
        with raw_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        status = rec.get("error") or f"{len(rec.get('queries', []))} queries"
        print(f"[{p}] {item['trace_id'][:10]} {status}", flush=True)

    await asyncio.gather(*[guarded(i, p) for i in inputs for p in PROMPTS])

    results = {}
    for line in raw_path.read_text(encoding="utf-8").splitlines():
        r = json.loads(line)
        key = (r["trace_id"], r["prompt"])
        if key not in results or "error" in results[key]:
            results[key] = r
    report = summarise(list(results.values()), inputs, reranked_ids)
    (out / "report.md").write_text(report, encoding="utf-8")
    print(f"\nreport → {out / 'report.md'}")


if __name__ == "__main__":
    asyncio.run(main())
