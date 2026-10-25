"""Decision-model reranker A/B — OpenRouter Decisions API (System One models).

Decision models (``output_modalities=decisions``: Jev, Perplexity Decider,
Liquid d1, Cloudflare Clef, Microsoft-Decision-1, …) don't generate text. They
take a ``state`` + typed questions and return calibrated probabilities, billed
on INPUT tokens only. This replays corpus C (``cases_full.jsonl``) through them
in two shapes:

``percand``  one request per candidate: state = sub-query + one block;
             questions = ``relevance`` (score drop/medium/high) + ``keep`` (noul).
``batch``    one request per sub-query: state = the whole reranker user message;
             one ``score`` question per ``[Cn]`` label. Every question sees every
             candidate, so cross-candidate context survives.

All requests set ``provider.zdr=true`` + ``data_collection=deny``.

Scoring is downstream recall of USED refs (same ground truth as
``reranker_model_ab downstream``), reported both at a fixed cut and at a
**matched keep budget** — the threshold that keeps as many distinct chunks as
production — because a probability output has no natural keep line.

Usage::

    python -m scripts.reranker_decisions_ab run --shape batch --models typesafe/jev-1.14 --limit 3
    python -m scripts.reranker_decisions_ab score
"""

from __future__ import annotations

import argparse
import asyncio
import collections
import json
import time
from pathlib import Path
from typing import Any

import httpx

from scripts.reranker_decider_ab import CASES_PATH, OUT_DIR, PROD_RUNS_PATH, PROD_ARM, _api_key, split_case

URL = "https://openrouter.ai/api/alpha/decisions"
RAW_PATH = OUT_DIR / "decisions_raw.jsonl"
SNAPSHOT = OUT_DIR / "downstream_cases_full.json"

RUBRIC = (
    "Rayhan legal AI (Saudi law). Judge a search candidate against the SUB-QUERY for the aggregator that "
    "writes the final legal analysis. (1) SCOPE FIRST: does the parent law's scope (النظام / نطاق النظام) — or "
    "for a circular/service the entity (الجهة) — actually govern the sub-query's situation? Off-scope = drop, "
    "however closely the words match (Saudi law has many parallel laws with near-identical penalty/definition "
    "chunks). (2) A REPEALED law (حالة النظام: ملغي) is never high. (3) high = the chunk covers the exact "
    "mechanism asked AND is the governing operative rule. medium = on-scope material the aggregator could use: "
    "definitions, procedures, penalties, obligations, adjacent provisions. drop = off-scope or nothing usable "
    "for this sub-query. The keep bar is asymmetric: a weak kept chunk can be ignored, a dropped one can never "
    "be cited — on a medium/drop borderline prefer medium. درجة الصلة is a retrieval rank, not relevance."
)
LEVELS = [
    "drop — off-scope, or carries nothing usable for this sub-query",
    "medium — on-scope supporting material the aggregator could use",
    "high — the governing operative rule on the exact mechanism asked",
]

# 5-level variant: splits the old "medium" so on-topic-but-uncitable chunks get their own bucket ("low").
RUBRIC5 = (
    "Rayhan legal AI (Saudi law). Judge a search candidate against the SUB-QUERY for the aggregator that "
    "writes the final legal analysis. SCOPE FIRST: does the parent law's scope (النظام / نطاق النظام) — or "
    "for a circular/service the entity (الجهة) — actually govern the sub-query's situation? Off-scope = "
    "not relevant, however closely the words match (Saudi law has many parallel laws with near-identical "
    "penalty/definition chunks). A REPEALED law (حالة النظام: ملغي) is never above low. Ask: would a lawyer "
    "answering THIS sub-query actually cite this chunk? Right topic is not enough — a chunk that only shares "
    "keywords or the regulation name, but would not be cited, is low. درجة الصلة is a retrieval rank, not relevance."
)
LEVELS5 = [
    "not relevant — off-scope: wrong sector/entity/law, repealed text, or a different subject",
    "low — right topic or right regulation, but nothing the answer would cite",
    "medium — supporting material the answer would actually cite: a definition, procedure, or cross-reference it relies on",
    "high — directly answers part of the sub-query: a condition, exception, penalty, deadline or obligation asked about",
    "critical — the governing operative rule that directly decides exactly what the sub-query asks",
]
MEDIUM_IDX = {3: 1, 5: 2}

# "gates" shape: per-candidate, the 5-level rubric split into three separate questions that mirror
# the production reranker's gates — scope (keep gate), mechanism (same doctrine, not just same law),
# and how citable it is. Each is answered independently in the same request.
GATE_SCOPE = (
    "Rayhan legal AI (Saudi law). SCOPE GATE ONLY: does the parent law's scope (النظام / نطاق النظام) — or "
    "for a circular/service the issuing/providing entity (الجهة) — actually govern the sub-query's situation: "
    "the parties and their capacity, the sector, the activity, private vs government contracting? Judge "
    "jurisdiction, not wording: Saudi law has many parallel laws with near-identical chunks, and a perfect "
    "word match under an off-scope law is still off-scope. Ignore whether the chunk is useful — only scope."
)
GATE_MECH = (
    "Rayhan legal AI (Saudi law). MECHANISM: does this chunk deal with the specific legal mechanism or "
    "doctrine the sub-query asks about — directly, or as that mechanism's own definition, condition, "
    "procedure, deadline or penalty? Same law, same chapter title or same broad area is NOT enough: a chunk "
    "from a different chapter or a different mechanism of the same law is no (e.g. انفساخ, فسخ اتفاقي and "
    "إبطال are distinct mechanisms; a gift-withdrawal rule does not serve a rescission-for-breach question)."
)


def _requests(case: dict, shape: str, levels: int = 3) -> list[tuple[list[str], dict, dict]]:
    """[(labels covered, state, questions)] for one case."""
    head, blocks = split_case(case["user_message"])
    rubric, crit = (RUBRIC, LEVELS) if levels == 3 else (RUBRIC5, LEVELS5)
    if shape == "batch":
        qs = {
            lab: {"type": "score",
                  "instructions": f"{rubric}\n\nRate ONLY the candidate whose header is `### [{lab}]`.",
                  "criteria": crit}
            for lab, _ in blocks
        }
        return [([l for l, _ in blocks], case["user_message"], qs)]
    out = []
    if shape == "gates":
        qs = {
            "scope": {"type": "noul", "instructions": GATE_SCOPE,
                      "criteria": {"true": "the law's scope / the entity governs the sub-query's situation",
                                   "false": "off-scope: wrong parties, sector, activity or entity"}},
            "mechanism": {"type": "noul", "instructions": GATE_MECH,
                          "criteria": {"true": "serves the exact mechanism asked",
                                       "false": "different mechanism, chapter or subject — only shares the law or words"}},
            "citable": {"type": "score", "instructions": rubric, "criteria": crit},
        }
        return [([lab], f"{head}\n\n---\n\n## Candidate\n\n{blk}", qs) for lab, blk in blocks]
    for lab, blk in blocks:
        qs = {
            "relevance": {"type": "score", "instructions": rubric, "criteria": crit},
            "keep": {"type": "noul", "instructions": RUBRIC + "\n\nShould this candidate be KEPT for the aggregator?",
                     "criteria": {"true": "keep — on-scope and usable (medium or high)",
                                  "false": "drop — off-scope or nothing usable"}},
        }
        out.append(([lab], f"{head}\n\n---\n\n## Candidate\n\n{blk}", qs))
    return out


async def _post(client, sem, model, state, qs) -> dict[str, Any]:
    body = {"model": model, "state": state, "questions": qs,
            "provider": {"zdr": True, "data_collection": "deny"}}
    err = ""
    async with sem:
        for attempt in range(3):
            t0 = time.perf_counter()
            try:
                r = await client.post(URL, json=body, timeout=180)
                dt = time.perf_counter() - t0
                j = r.json()
                if r.status_code == 200 and "answers" in j:
                    return {"ok": True, "duration_s": dt, "answers": j["answers"], "provider": j.get("provider"),
                            "served": j.get("model"), "usage": j.get("usage") or {}}
                err = f"{r.status_code}: {str(j.get('error', j))[:300]}"
                if r.status_code in (400, 404, 413):
                    break
            except (httpx.HTTPError, json.JSONDecodeError) as exc:
                err = f"{type(exc).__name__}: {exc}"[:300]
            await asyncio.sleep(1 + 2 * attempt)
    return {"ok": False, "error": err}


def cmd_run(args) -> None:
    cases = [json.loads(l) for l in CASES_PATH.read_text(encoding="utf-8").splitlines() if l.strip()]
    if args.limit:
        cases = cases[: args.limit]
    headers = {"Authorization": f"Bearer {_api_key()}", "X-Title": "luna-reranker-decisions-ab"}

    async def one_case(client, sem, model, case):
        reqs = _requests(case, args.shape, args.levels)
        prev = done.get((model, case["case_id"]), {})
        reqs = [q for q in reqs if not all(lab in prev for lab in q[0])]
        t0 = time.perf_counter()
        res = await asyncio.gather(*[_post(client, sem, model, s, q) for _, s, q in reqs])
        scores, errors = {}, []
        for (labels, _, _), r in zip(reqs, res):
            if not r["ok"]:
                errors.append(r["error"])
                continue
            a = r["answers"]
            for lab in labels:
                if args.shape == "gates":
                    scores[lab] = {"score": (a.get("citable") or {}).get("score"),
                                   "p": (a.get("citable") or {}).get("probabilities"),
                                   "scope": (a.get("scope") or {}).get("noul"),
                                   "mech": (a.get("mechanism") or {}).get("noul")}
                elif args.shape == "batch":
                    if lab in a:
                        scores[lab] = {"score": a[lab].get("score"), "p": a[lab].get("probabilities")}
                else:
                    scores[lab] = {"score": (a.get("relevance") or {}).get("score"),
                                   "p": (a.get("relevance") or {}).get("probabilities"),
                                   "keep": (a.get("keep") or {}).get("noul")}
        ok = [r for r in res if r["ok"]]
        return {
            "case_id": case["case_id"], "model": model if args.levels == 3 else f"{model}@{args.levels}lvl",
            "shape": args.shape, "levels": args.levels,
            "n_requests": len(reqs), "n_failed": len(errors), "errors": errors[:2],
            "wall_s": time.perf_counter() - t0,
            "slowest_s": max((r["duration_s"] for r in ok), default=0),
            "input_tokens": sum(r["usage"].get("input_tokens", 0) for r in ok),
            "cost": sum(r["usage"].get("cost") or 0 for r in ok),
            "served": dict(collections.Counter(f"{r['served']}|{r['provider']}" for r in ok)),
            "scores": {**prev, **scores},
        }

    # --resume: reuse candidates this arm already scored; only request the missing ones.
    done: dict[tuple[str, str], dict] = {}
    if args.resume and RAW_PATH.exists():
        for l in RAW_PATH.read_text(encoding="utf-8").splitlines():
            r = json.loads(l)
            if r.get("shape") == args.shape and r.get("levels", 3) == args.levels:
                base = r["model"].split("@")[0]
                ok = {k: v for k, v in (r.get("scores") or {}).items() if v.get("score") is not None}
                done.setdefault((base, r["case_id"]), {}).update(ok)

    async def main():
        sem = asyncio.Semaphore(args.concurrency)
        async with httpx.AsyncClient(headers=headers) as client:
            for model in args.models.split(","):
                t0 = time.perf_counter()
                rows = await asyncio.gather(*[one_case(client, sem, model, c) for c in cases])
                with RAW_PATH.open("a", encoding="utf-8") as f:
                    for r in rows:
                        f.write(json.dumps(r, ensure_ascii=False) + "\n")
                errs = collections.Counter(e[:120] for r in rows for e in r["errors"])
                print(f"{model} [{args.shape}]: req failed {sum(r['n_failed'] for r in rows)}/"
                      f"{sum(r['n_requests'] for r in rows)}, ${sum(r['cost'] for r in rows):.4f}, "
                      f"wall {time.perf_counter() - t0:.0f}s {dict(errs.most_common(2)) if errs else ''}", flush=True)

    asyncio.run(main())


# ------------------------------------------------------------------ scoring


def _gold():
    """artifact -> (all candidate refs, used refs), and case -> label->ref."""
    d = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    used = {(w["wi_id"], w["ref_id"].split(":", 1)[-1]) for w in d["refs"] if w.get("used")}
    return used


def _recall(cases, keepsets, used) -> tuple[int, int, int]:
    """(hits, gold, distinct kept) over per-artifact unions — mirrors reranker_model_ab."""
    uni, allr = collections.defaultdict(set), collections.defaultdict(set)
    for c in cases:
        m = c["label_refs"]
        uni[c["artifact_id"]] |= {m[l] for l in keepsets.get(c["case_id"], set()) if l in m}
        allr[c["artifact_id"]] |= set(m.values())
    gold = {(a, r) for a, rs in allr.items() for r in rs if (a, r) in used}
    hits = sum(1 for a, rs in uni.items() for r in rs if (a, r) in gold)
    return hits, len(gold), sum(len(v) for v in uni.values())


def cmd_score(args) -> None:
    cases = [json.loads(l) for l in CASES_PATH.read_text(encoding="utf-8").splitlines() if l.strip()]
    used = _gold()
    prod = {}
    for l in PROD_RUNS_PATH.read_text(encoding="utf-8").splitlines():
        r = json.loads(l)
        if r["model"] == PROD_ARM:
            prod[r["case_id"]] = {str(k["label"]).strip("[] ") for k in (r.get("output") or {}).get("keeps", [])}
    ph, pg, pk = _recall(cases, prod, used)
    budget = pk
    print(f"production {PROD_ARM}: recall {ph}/{pg} = {ph / pg:.2f}, kept {pk} distinct chunks  "
          f"(budget for matched comparison)\n")
    rows = collections.OrderedDict()
    for l in RAW_PATH.read_text(encoding="utf-8").splitlines():
        r = json.loads(l)
        rows[(r["model"], r["shape"], r["case_id"])] = r   # latest wins
    arms = collections.defaultdict(dict)
    for (m, s, cid), r in rows.items():
        arms[(m, s)][cid] = r
    print(f"{'model':<46}{'shape':<9}{'runs':>5}{'fail':>5}{'R@~med':>7}{'kept':>6}{'R@med':>6}{'kept':>6}"
          f"{'R@budget':>9}{'R@1.5xB':>8}{'AUC*':>6}{'$/run':>9}{'p50 s':>7}{'p90 s':>7}  served")
    out = []
    for (m, s), rs in sorted(arms.items()):
        cs = [c for c in cases if c["case_id"] in rs]
        if len(cs) < len(cases) and not args.partial:
            continue
        cand = [(sc.get("score") if sc.get("score") is not None else -1, c["case_id"], lab)
                for c in cs for lab, sc in rs[c["case_id"]]["scores"].items()]
        # score = expected level index (0..n-1). Fixed cuts: rounds to medium+ (>= med-0.5), strictly >= medium.
        med = MEDIUM_IDX[rs[cs[0]["case_id"]].get("levels", 3)]

        def fixed_cut(t):
            ks = collections.defaultdict(set)
            for sc, cid, lab in cand:
                if sc >= t:
                    ks[cid].add(lab)
            return _recall(cs, ks, used)
        fh, fg, fk = fixed_cut(med - 0.5)
        sh, _, sk = fixed_cut(med)

        def at_budget(b):
            # global threshold sweep: lowest cut whose distinct-kept <= b
            best = (0, fg, 0)
            for t in sorted({x[0] for x in cand}, reverse=True):
                ks = collections.defaultdict(set)
                for sc, cid, lab in cand:
                    if sc >= t:
                        ks[cid].add(lab)
                h, g, k = _recall(cs, ks, used)
                if k > b:
                    break
                best = (h, g, k)
            return best
        bh, bg, bk = at_budget(budget)
        b2h, _, _ = at_budget(int(budget * 1.5))
        # crude ranking quality: mean recall over budgets 0.5x..2x
        auc = sum(at_budget(int(budget * f))[0] for f in (0.5, 0.75, 1.0, 1.25, 1.5, 2.0)) / 6 / max(1, fg)
        lat = sorted(rs[c["case_id"]]["slowest_s"] for c in cs)
        cost = sum(rs[c["case_id"]]["cost"] for c in cs) / len(cs)
        fails = sum(rs[c["case_id"]]["n_failed"] for c in cs)
        served = collections.Counter()
        for c in cs:
            served.update(rs[c["case_id"]]["served"])
        print(f"{m:<46}{s:<9}{len(cs):>5}{fails:>5}{fh / max(1, fg):>7.2f}{fk:>6}{sh / max(1, fg):>6.2f}{sk:>6}"
              f"{bh / max(1, bg):>9.2f}"
              f"{b2h / max(1, fg):>8.2f}{auc:>6.2f}{cost:>9.5f}{lat[len(lat) // 2]:>7.1f}"
              f"{lat[int(len(lat) * .9)]:>7.1f}  {','.join(k.split('|')[1] for k, _ in served.most_common(2))}")
        out.append({"model": m, "shape": s, "recall_fixed": fh / max(1, fg), "kept_fixed": fk,
                    "recall_strict": sh / max(1, fg), "kept_strict": sk,
                    "recall_budget": bh / max(1, bg), "kept_budget": bk, "auc": auc, "cost_run": cost,
                    "p50_s": lat[len(lat) // 2]})
    (OUT_DIR / "decisions_scores.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
    print("\nR@~med = keep if expected level rounds to medium+;  R@med = expected level >= medium;  R@budget = recall when keeping <= production's "
          f"{budget} distinct chunks;  AUC* = mean recall over 0.5x-2x budget")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--models", required=True)
    r.add_argument("--shape", choices=["percand", "batch", "gates"], default="batch")
    r.add_argument("--limit", type=int, default=0)
    r.add_argument("--concurrency", type=int, default=24)
    r.add_argument("--levels", type=int, choices=[3, 5], default=3)
    r.add_argument("--resume", action="store_true", help="skip candidates this arm already scored in --raw")
    r.set_defaults(fn=cmd_run)
    s = sub.add_parser("score")
    s.add_argument("--partial", action="store_true", help="also show arms that didn't cover every case")
    s.set_defaults(fn=cmd_score)
    for sp in (r, s):
        sp.add_argument("--cases", default=None, help="alternate cases jsonl under OUT_DIR")
        sp.add_argument("--raw", default=None, help="alternate raw-results jsonl under OUT_DIR")
    a = ap.parse_args()
    global CASES_PATH, RAW_PATH
    if a.cases:
        CASES_PATH = OUT_DIR / a.cases
    if a.raw:
        RAW_PATH = OUT_DIR / a.raw
    a.fn(a)


if __name__ == "__main__":
    main()
