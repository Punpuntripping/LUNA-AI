"""Per-candidate "decider" A/B — replace one batched reranker call with N small calls.

Production reranks a whole sub-query's candidate set (~12 blocks, ~14k input
tokens, ~3.8k output, ~30s) in ONE qwen3.7-flash call. This experiment asks a
different question: what if each candidate got its OWN tiny call — sub-query
context + one block → keep/drop + tier — fanned out in parallel on a cheap
model? ~12 calls per sub-query, ~100+ per deep_search turn.

It replays corpus C (``cases_full.jsonl``: 96 reg runs, 11 conversations,
complete per-conversation unions) so results score with the SAME downstream
ground truth as the qwen3.5 → 3.7 decision (``reranker_model_ab downstream``).

Every call goes through OpenRouter with ``provider.zdr=true`` +
``data_collection=deny``: only zero-data-retention endpoints are eligible, so
no prompt is stored or trained on. The serving provider is recorded per call.

Usage::

    python -m scripts.reranker_decider_ab run --models google/gemini-2.5-flash-lite --limit 3
    python -m scripts.reranker_decider_ab run --models a,b,c
    python -m scripts.reranker_decider_ab compare
    python -m scripts.reranker_model_ab downstream --cases cases_full.jsonl \\
        --runs runs_decider_merged.jsonl --baseline qwen3.7-flash@live_v2 --candidate decider:<model>
"""

from __future__ import annotations

import argparse
import asyncio
import collections
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any

import httpx

REPO = Path(__file__).resolve().parents[1]
OUT_DIR = REPO / "agents_reports" / "reranker_model_ab"
CASES_PATH = OUT_DIR / "cases_full.jsonl"
PROD_RUNS_PATH = OUT_DIR / "runs_full.jsonl"
RUNS_PATH = OUT_DIR / "runs_decider.jsonl"
CALLS_PATH = OUT_DIR / "calls_decider.jsonl"
MERGED_PATH = OUT_DIR / "runs_decider_merged.jsonl"
PROD_ARM = "qwen3.7-flash@live_v2"

OR_URL = "https://openrouter.ai/api/v1/chat/completions"

DECIDER_PROMPT = """\
You are a legal search-result decider within the Rayhan legal AI platform (Saudi law).
You receive ONE sub-query and ONE search candidate. Decide whether the candidate is kept for the aggregator, which writes the final legal analysis from the kept candidates.

## Candidate kinds
- **مقطع نظام** (law/regulation chunk): header `### [Cn] <title>` (a **(ملحق)** tag = appendix of the same law), then **النظام** (parent law), optional **حالة النظام: ملغي — لم يعد سارياً** (the law is REPEALED), **نطاق النظام** (scope of application), **درجة الصلة** (a retrieval rank — NOT a relevance judgment), then a summary and possibly surrounding-context lines.
- **تعميم** (circular): **الجهة** (issuing entity) + opening text.
- **خدمة** (e-government service): **الجهة** (providing entity) + description; ignore the URL.
Truncated text ending in `...` is still classifiable.

## Gate 1 — scope (decide this FIRST)
Does the parent law's scope (or, for a circular/service, the entity's authority) actually govern the situation the sub-query raises?
- Scope limited to a category, sector, profession or activity that the sub-query does not concern → **drop**, however closely the wording matches. Saudi law has many parallel laws with near-identical chunks (penalties, definitions, closing provisions); a word match is worthless when the scope does not apply.
- Contracting regime is part of scope: a government-only or sector-authority regime does not govern a purely private matter.

## Gate 2 — repeal
If **حالة النظام: ملغي** is present: never `high`. Keep as `medium` only when the sub-query is about that historical instrument or it plausibly is the only material on the point; otherwise drop.

## Relevance tiers
- `high` needs BOTH: (A) ON-MECHANISM — the chunk covers the specific doctrine/mechanism the sub-query asks about, not merely the same law or broad area; (B) OPERATIVE — it is the governing rule that decides the issue, not a definition, scope clause, procedure, penalty table or closing provision. `high` is scarce.
- `medium` is the GENEROUS band: on-scope material the aggregator could use to state, qualify or situate the answer — definitions, scope clauses, procedures, penalties, obligations, adjacent provisions. Failing a high gate means downgrade to `medium`, not drop.
- `drop`: off-scope, or on-scope but carrying nothing usable for this sub-query (e.g. a different chapter on an unrelated mechanism of the same general law).
- Distinct mechanisms are not interchangeable: انفساخ ≠ فسخ اتفاقي ≠ إبطال.
- Services: `high` only if the service performs the specific operative act the need requires; inquiry/tracking/appointment/portal services are never `high`.

**The keep bar is asymmetric**: the aggregator can ignore a weak candidate it received, but it cannot cite one you dropped. On a genuine medium/drop borderline, choose `medium`.

## Output
Return ONLY a JSON object:
{"scope_applies": true|false, "relevance": "high"|"medium"|"drop", "score": <integer 0-10, how useful this candidate is for answering the sub-query>, "reason": "<one short Arabic sentence: the scope verdict and the mechanism covered vs. asked>"}
"""

SCHEMA = {
    "type": "json_schema",
    "json_schema": {
        "name": "decision",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "scope_applies": {"type": "boolean"},
                "relevance": {"type": "string", "enum": ["high", "medium", "drop"]},
                "score": {"type": "integer"},
                "reason": {"type": "string"},
            },
            "required": ["scope_applies", "relevance", "score", "reason"],
            "additionalProperties": False,
        },
    },
}

_BLOCK_RE = re.compile(r"(?m)^(?=### \[C\d+\])")
_LABEL_RE = re.compile(r"^### \[(C\d+)\]")


def _api_key() -> str:
    key = os.environ.get("OPENROUTER_API_KEY")
    if key:
        return key
    for line in (REPO / ".env").read_text(encoding="utf-8").splitlines():
        if line.startswith("OPENROUTER_API_KEY="):
            return line.split("=", 1)[1].strip().strip('"')
    raise SystemExit("OPENROUTER_API_KEY not set")


def split_case(user_message: str) -> tuple[str, list[tuple[str, str]]]:
    """(sub-query header, [(label, block), ...]) from a reranker user message."""
    i = user_message.find("## Search results")
    head = user_message[:i].rstrip().rstrip("-").rstrip()
    blocks = []
    for b in _BLOCK_RE.split(user_message[i:]):
        m = _LABEL_RE.match(b)
        if m:
            blocks.append((m.group(1), b.strip()))
    return head, blocks


def _parse(text: str) -> dict[str, Any] | None:
    text = (text or "").strip()
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return None
    try:
        d = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    rel = str(d.get("relevance", "")).strip().lower()
    if rel not in ("high", "medium", "drop"):
        return None
    d["relevance"] = rel
    return d


async def _decide(client: httpx.AsyncClient, sem: asyncio.Semaphore, model: str, head: str,
                  label: str, block: str, args: argparse.Namespace) -> dict[str, Any]:
    body: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": DECIDER_PROMPT},
            # Sub-query first, candidate last: the system + sub-query prefix is
            # shared by every call of the run, so providers can prefix-cache it.
            {"role": "user", "content": f"{head}\n\n---\n\n## Candidate\n\n{block}"},
        ],
        "temperature": 0,
        "max_tokens": args.max_tokens,
        "usage": {"include": True},
        "provider": {"zdr": True, "data_collection": "deny", "allow_fallbacks": True},
    }
    if not args.no_schema:
        body["response_format"] = SCHEMA
    if args.reasoning == "off":
        body["reasoning"] = {"enabled": False}
    elif args.reasoning != "default":
        body["reasoning"] = {"effort": args.reasoning}
    if args.provider:
        body["provider"]["order"] = args.provider.split("+")
    out: dict[str, Any] = {"label": label, "ok": False}
    async with sem:
        for attempt in range(3):
            t0 = time.perf_counter()
            try:
                r = await client.post(OR_URL, json=body, timeout=120)
                dt = time.perf_counter() - t0
                j = r.json()
                if r.status_code != 200 or "choices" not in j:
                    out["error"] = f"{r.status_code}: {str(j.get('error', j))[:300]}"
                    if r.status_code == 400 and "response_format" in body and attempt == 0:
                        body.pop("response_format")  # endpoint lacks structured outputs
                    await asyncio.sleep(1 + attempt * 2)
                    continue
                msg = j["choices"][0]["message"]
                u = j.get("usage") or {}
                d = _parse(msg.get("content") or "")
                out.update(
                    duration_s=dt, provider=j.get("provider"), routed_model=j.get("model"),
                    input_tokens=u.get("prompt_tokens", 0), output_tokens=u.get("completion_tokens", 0),
                    reasoning_tokens=(u.get("completion_tokens_details") or {}).get("reasoning_tokens", 0),
                    cached_tokens=(u.get("prompt_tokens_details") or {}).get("cached_tokens", 0),
                    cost=u.get("cost", 0.0), attempts=attempt + 1,
                )
                if d is None:
                    out["error"] = f"unparseable: {(msg.get('content') or '')[:200]}"
                    continue
                out.update(ok=True, decision=d)
                out.pop("error", None)
                return out
            except (httpx.HTTPError, json.JSONDecodeError, KeyError, IndexError) as exc:
                out["error"] = f"{type(exc).__name__}: {exc}"[:300]
                await asyncio.sleep(1 + attempt * 2)
    return out


async def _run_case(client, sem, model, case, args) -> tuple[dict, list[dict]]:
    head, blocks = split_case(case["user_message"])
    t0 = time.perf_counter()
    calls = await asyncio.gather(*[_decide(client, sem, model, head, l, b, args) for l, b in blocks])
    wall = time.perf_counter() - t0
    keeps = [{"label": c["label"], "relevance": c["decision"]["relevance"],
              "score": c["decision"].get("score")}
             for c in calls if c["ok"] and c["decision"]["relevance"] != "drop"]
    arm = f"decider:{model}" + (f"@{args.tag}" if args.tag else "")
    run = {
        "case_id": case["case_id"], "model": arm,
        "ok": all(c["ok"] for c in calls), "n_failed": sum(not c["ok"] for c in calls),
        "n_calls": len(calls), "wall_s": wall,
        "slowest_call_s": max((c.get("duration_s", 0) for c in calls), default=0),
        "input_tokens": sum(c.get("input_tokens", 0) for c in calls),
        "output_tokens": sum(c.get("output_tokens", 0) for c in calls),
        "reasoning_tokens": sum(c.get("reasoning_tokens", 0) for c in calls),
        "cached_tokens": sum(c.get("cached_tokens", 0) for c in calls),
        "cost": sum(c.get("cost") or 0 for c in calls),
        "providers": dict(collections.Counter(str(c.get("provider")) for c in calls)),
        "routed_models": dict(collections.Counter(str(c.get("routed_model")) for c in calls)),
        "output": {"keeps": keeps},
    }
    for c in calls:
        c.update(case_id=case["case_id"], model=arm)
    return run, calls


def cmd_run(args: argparse.Namespace) -> None:
    cases = [json.loads(l) for l in CASES_PATH.read_text(encoding="utf-8").splitlines() if l.strip()]
    if args.limit:
        cases = cases[: args.limit]
    headers = {"Authorization": f"Bearer {_api_key()}", "X-Title": "luna-reranker-decider-ab"}

    async def main():
        sem = asyncio.Semaphore(args.concurrency)
        async with httpx.AsyncClient(headers=headers) as client:
            for model in args.models.split(","):
                t0 = time.perf_counter()
                res = await asyncio.gather(*[_run_case(client, sem, model, c, args) for c in cases])
                with RUNS_PATH.open("a", encoding="utf-8") as fr, CALLS_PATH.open("a", encoding="utf-8") as fc:
                    for run, calls in res:
                        fr.write(json.dumps(run, ensure_ascii=False) + "\n")
                        for c in calls:
                            fc.write(json.dumps(c, ensure_ascii=False) + "\n")
                runs = [r for r, _ in res]
                print(f"{model}: {len(runs)} runs, calls failed {sum(r['n_failed'] for r in runs)}"
                      f"/{sum(r['n_calls'] for r in runs)}, cost ${sum(r['cost'] for r in runs):.4f}, "
                      f"wall {time.perf_counter() - t0:.0f}s")

    asyncio.run(main())
    _merge()


def _merge() -> None:
    """Production arms + latest decider arm rows, in the format reranker_model_ab reads."""
    rows = {}
    for p in (PROD_RUNS_PATH, RUNS_PATH):
        if p.exists():
            for line in p.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    r = json.loads(line)
                    rows[(r["case_id"], r["model"])] = r
    MERGED_PATH.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows.values()),
                           encoding="utf-8")


def cmd_compare(args: argparse.Namespace) -> None:
    """Keep-set agreement with production + cost/latency, per decider arm."""
    _merge()
    cases = {json.loads(l)["case_id"]: json.loads(l)
             for l in CASES_PATH.read_text(encoding="utf-8").splitlines() if l.strip()}
    rows = [json.loads(l) for l in MERGED_PATH.read_text(encoding="utf-8").splitlines() if l.strip()]
    by = collections.defaultdict(dict)
    for r in rows:
        by[r["model"]][r["case_id"]] = r
    prod = by[PROD_ARM]
    print(f"{'arm':<58}{'runs':>5}{'fail':>6}{'keeps/run':>10}{'hi':>5}{'agree':>7}"
          f"{'P(prod)':>8}{'R(prod)':>8}{'$/run':>9}{'p50 s':>7}{'max s':>7}")
    for arm, rs in sorted(by.items()):
        ids = [i for i in rs if i in prod]
        if not ids:
            continue
        keeps = [{str(k["label"]).strip("[] ") for k in (rs[i].get("output") or {}).get("keeps", [])} for i in ids]
        pk = [{str(k["label"]).strip("[] ") for k in (prod[i].get("output") or {}).get("keeps", [])} for i in ids]
        hi = sum(1 for i in ids for k in (rs[i].get("output") or {}).get("keeps", []) if k.get("relevance") == "high")
        inter = sum(len(a & b) for a, b in zip(keeps, pk))
        agree = sum(1 for i, a, b in zip(ids, keeps, pk)
                    for l in cases[i]["labels"] if (l in a) == (l in b)) / max(1, sum(len(cases[i]["labels"]) for i in ids))
        if arm.startswith("decider:"):
            cost = sum(rs[i]["cost"] for i in ids) / len(ids)
            lat = sorted(rs[i]["wall_s"] for i in ids)
            fails = sum(rs[i]["n_failed"] for i in ids)
        else:
            cost = float("nan")
            lat = sorted(rs[i]["duration_s"] for i in ids)
            fails = sum(not rs[i]["ok"] for i in ids)
        print(f"{arm:<58}{len(ids):>5}{fails:>6}{sum(map(len, keeps)) / len(ids):>10.2f}{hi:>5}"
              f"{agree:>7.2f}{inter / max(1, sum(map(len, keeps))):>8.2f}{inter / max(1, sum(map(len, pk))):>8.2f}"
              f"{cost:>9.5f}{lat[len(lat) // 2]:>7.1f}{lat[-1]:>7.1f}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--models", required=True)
    r.add_argument("--limit", type=int, default=0)
    r.add_argument("--concurrency", type=int, default=32)
    r.add_argument("--max-tokens", type=int, default=2000)
    r.add_argument("--reasoning", default="off", help="off | default | low | medium | high")
    r.add_argument("--provider", default="", help="pin providers, e.g. DeepInfra+Parasail")
    r.add_argument("--no-schema", action="store_true")
    r.add_argument("--tag", default="")
    r.set_defaults(fn=cmd_run)
    c = sub.add_parser("compare")
    c.set_defaults(fn=cmd_compare)
    args = ap.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
