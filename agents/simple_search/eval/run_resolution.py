"""Axis 1 scorer — runs :mod:`fixtures_resolution` against the LIVE corpus.

Zero LLM calls. Both identity legs are exercised per fixture and scored against
the hand label, then the three live thresholds are re-measured against the same
set so a mis-calibration shows up as numbers rather than an opinion.

Run from the repo root::

    python -m agents.simple_search.eval.run_resolution
    python -m agents.simple_search.eval.run_resolution --json out.json
    python -m agents.simple_search.eval.run_resolution --only fp_absent
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
except Exception:  # noqa: BLE001 — non-Windows consoles are already UTF-8
    pass

from dotenv import load_dotenv

load_dotenv()

from agents.simple_search.eval.fixtures_resolution import FIXTURES, Fixture  # noqa: E402
from agents.simple_search.manual_search import (  # noqa: E402
    _MIN_TITLE_COVERAGE,
    coverage,
    decide,
    manual_search_core,
)
from agents.tool_repository.fetch_article import (  # noqa: E402
    _AMBIGUITY_MARGIN,
    _MIN_MATCH_SCORE,
    _fetch_article_content,
    _rank_candidates,
    _fetch_reg_candidates,
    resolve_regulation_id,
)
from shared.db.client import get_supabase_client  # noqa: E402

# Verdicts a "refuse"/"ask" label accepts. `not_found` is the ideal shape for a
# refusal; `candidates`/`ambiguous` mean the resolver did not COMMIT, which is
# also acceptable (the searcher can still ask_user) but is tracked separately so
# an over-broad candidate table stays visible in the report.
_NON_COMMITTAL = {"not_found", "candidates", "ambiguous"}


@dataclass
class LegResult:
    """What one resolver leg did with one fixture."""

    leg: str
    status: str            # resolved | candidates | ambiguous | not_found | error
    gate: str = ""         # which rule fired (manual leg only)
    reg_id: str = ""
    title: str = ""
    confidence: str = ""
    top_coverage: float = 0.0
    top_score: float = 0.0
    second_coverage: float = 0.0
    second_score: float = 0.0
    n_candidates: int = 0
    article_found: bool | None = None
    verdict: str = ""      # PASS | FAIL | WRONG_DOC | SOFT (non-committal on a refuse label)
    note: str = ""


@dataclass
class FixtureResult:
    fid: str
    cls: str
    query: str
    expect: str
    legs: list[LegResult] = field(default_factory=list)


# --------------------------------------------------------------------------- #
# Leg 1 — the deterministic resolver (fetch_article).
# --------------------------------------------------------------------------- #


def _run_det(supabase, f: Fixture) -> LegResult:
    """``resolve_regulation_id`` + (for article fixtures) the exact-key fetch.

    Also re-derives the ranked candidate list so the raw difflib scores that
    ``_MIN_MATCH_SCORE`` / ``_AMBIGUITY_MARGIN`` act on are recorded, not just
    the resolver's verdict.
    """
    try:
        rows = _fetch_reg_candidates(supabase, f.query)
        ranked = _rank_candidates(f.query, rows)
        res = resolve_regulation_id(supabase, f.query)
    except Exception as exc:  # noqa: BLE001
        return LegResult(leg="det", status="error", note=repr(exc)[:200])

    out = LegResult(
        leg="det",
        status="resolved" if res.reg_id else ("ambiguous" if res.ambiguous else "not_found"),
        reg_id=res.reg_id,
        title=res.display,
        confidence=("high" if res.exact else "medium") if res.reg_id else "",
        n_candidates=len(ranked),
        top_score=round(ranked[0].score, 4) if ranked else 0.0,
        second_score=round(ranked[1].score, 4) if len(ranked) > 1 else 0.0,
        gate=("exact" if res.exact else "floor+margin") if res.reg_id else "",
    )
    if ranked:
        out.top_coverage = round(coverage(f.query, ranked[0].display), 4)
        if len(ranked) > 1:
            out.second_coverage = round(coverage(f.query, ranked[1].display), 4)

    # Article fixtures: a resolved PARENT is not a resolved article. The
    # searcher's resolve_article does exactly this second hop.
    if f.data_type == "article" and res.reg_id:
        try:
            body = _fetch_article_content(supabase, res.reg_id, f.article_number)
        except Exception as exc:  # noqa: BLE001
            body = None
            out.note = repr(exc)[:120]
        out.article_found = bool(body)
        if not body:
            out.status = "not_found"
    return out


# --------------------------------------------------------------------------- #
# Leg 2 — the manual_search ladder + gates.
# --------------------------------------------------------------------------- #


async def _run_manual(supabase, f: Fixture) -> LegResult:
    """``manual_search_core`` (live ladder) → ``decide`` (pure gates)."""
    dtype = f.data_type
    try:
        cands = await manual_search_core(supabase, f.query, dtype)
    except Exception as exc:  # noqa: BLE001
        return LegResult(leg="manual", status="error", note=repr(exc)[:200])

    d = decide(cands, dtype)
    out = LegResult(
        leg="manual",
        status=d.status,
        gate=d.gate,
        confidence=d.confidence,
        n_candidates=len(cands),
        reg_id=str((d.winner or {}).get("id") or ""),
        title=str((d.winner or {}).get("title") or ""),
    )
    if cands:
        out.top_coverage = round(float(cands[0].get("coverage", 0.0)), 4)
        out.top_score = round(float(cands[0].get("score", 0.0)), 4)
        if len(cands) > 1:
            out.second_coverage = round(float(cands[1].get("coverage", 0.0)), 4)
            out.second_score = round(float(cands[1].get("score", 0.0)), 4)

    if dtype == "article" and d.status == "resolved" and out.reg_id:
        try:
            body = await asyncio.to_thread(
                _fetch_article_content, supabase, out.reg_id, f.article_number
            )
        except Exception as exc:  # noqa: BLE001
            body = None
            out.note = repr(exc)[:120]
        out.article_found = bool(body)
        if not body:
            out.status = "not_found"
    return out


# --------------------------------------------------------------------------- #
# Grading.
# --------------------------------------------------------------------------- #


def _grade(f: Fixture, r: LegResult) -> LegResult:
    """Attach PASS / FAIL / WRONG_DOC / SOFT to one leg result."""
    if r.status == "error":
        r.verdict = "FAIL"
        return r

    if f.expect == "resolve":
        if r.status != "resolved":
            r.verdict = "FAIL"
            r.note = (r.note + f" | expected a resolve, got {r.status}/{r.gate}").strip(" |")
            return r
        if f.expect_reg_id and r.reg_id != f.expect_reg_id:
            r.verdict = "WRONG_DOC"
            r.note = (r.note + f" | resolved «{r.title}» ({r.reg_id})").strip(" |")
            return r
        if r.reg_id in f.forbid_reg_ids:
            r.verdict = "WRONG_DOC"
            r.note = (r.note + f" | landed on a FORBIDDEN lookalike «{r.title}»").strip(" |")
            return r
        # An article label additionally needs the body to exist.
        if f.data_type == "article" and r.article_found is False:
            r.verdict = "FAIL"
            r.note = (r.note + " | parent resolved but the article key missed").strip(" |")
            return r
        r.verdict = "PASS"
        return r

    # expect in {refuse, ask}
    if r.status == "resolved":
        r.verdict = "WRONG_DOC" if f.expect == "refuse" else "FAIL"
        r.note = (r.note + f" | COMMITTED to «{r.title}» ({r.reg_id}) "
                           f"cov={r.top_coverage} score={r.top_score}").strip(" |")
        return r
    if f.expect == "ask":
        # ask wants a candidate table or an ambiguous payload, not an empty miss.
        r.verdict = "PASS" if r.status in ("candidates", "ambiguous") else "SOFT"
        if r.verdict == "SOFT":
            r.note = (r.note + " | refused outright where an ask was wanted").strip(" |")
        return r
    # expect == refuse
    if r.status == "not_found":
        r.verdict = "PASS"
    elif r.status in _NON_COMMITTAL:
        r.verdict = "SOFT"
        r.note = (r.note + f" | did not commit ({r.status}) but surfaced "
                           f"{r.n_candidates} candidates").strip(" |")
    else:
        r.verdict = "FAIL"
    return r


async def run_all(only: str | None = None) -> list[FixtureResult]:
    supabase = get_supabase_client()
    fixtures = [f for f in FIXTURES if not only or f.cls == only]
    results: list[FixtureResult] = []
    for f in fixtures:
        fr = FixtureResult(fid=f.fid, cls=f.cls, query=f.query, expect=f.expect)
        if "det" in f.legs and f.data_type in ("regs", "article"):
            fr.legs.append(_grade(f, await asyncio.to_thread(_run_det, supabase, f)))
        if "manual" in f.legs:
            fr.legs.append(_grade(f, await _run_manual(supabase, f)))
        results.append(fr)
        for leg in fr.legs:
            print(
                f"[{fr.fid:8s}] {fr.cls:14s} {leg.leg:6s} "
                f"{leg.verdict:9s} {leg.status:10s} gate={leg.gate or '-':18s} "
                f"cov={leg.top_coverage:<5} score={leg.top_score:<9} "
                f"n={leg.n_candidates:<3} «{leg.title[:44]}» {leg.note}"
            )
    return results


def summarize(results: list[FixtureResult]) -> None:
    """Per-class precision/recall + the three-threshold re-measurement."""
    print("\n" + "=" * 100)
    print("PER-CLASS × LEG")
    print("=" * 100)
    buckets: dict[tuple[str, str], list[LegResult]] = {}
    for fr in results:
        for leg in fr.legs:
            buckets.setdefault((fr.cls, leg.leg), []).append(leg)
    for (cls, leg), rs in sorted(buckets.items()):
        n = len(rs)
        p = sum(1 for r in rs if r.verdict == "PASS")
        w = sum(1 for r in rs if r.verdict == "WRONG_DOC")
        s = sum(1 for r in rs if r.verdict == "SOFT")
        fl = sum(1 for r in rs if r.verdict == "FAIL")
        print(f"{cls:16s} {leg:7s} n={n:<3} PASS={p:<3} SOFT={s:<3} "
              f"FAIL={fl:<3} WRONG_DOC={w:<3}  ({100.0 * p / n:.0f}% strict)")

    # --- threshold re-measurement -------------------------------------------
    print("\n" + "=" * 100)
    print("THRESHOLD RE-MEASUREMENT")
    print(f"  _MIN_TITLE_COVERAGE = {_MIN_TITLE_COVERAGE}   "
          f"_MIN_MATCH_SCORE = {_MIN_MATCH_SCORE}   _AMBIGUITY_MARGIN = {_AMBIGUITY_MARGIN}")
    print("=" * 100)
    fx = {f.fid: f for f in FIXTURES}
    should_resolve_cov: list[float] = []
    should_refuse_cov: list[float] = []
    should_resolve_score: list[float] = []
    should_refuse_score: list[float] = []
    for fr in results:
        f = fx[fr.fid]
        for leg in fr.legs:
            if leg.status == "error" or leg.n_candidates == 0:
                continue
            if leg.leg == "manual":
                (should_resolve_cov if f.expect == "resolve" else should_refuse_cov).append(
                    leg.top_coverage
                )
            else:
                (should_resolve_score if f.expect == "resolve" else should_refuse_score).append(
                    leg.top_score
                )
    def _span(name: str, xs: list[float]) -> None:
        if not xs:
            print(f"  {name:34s} (no samples)")
            return
        xs = sorted(xs)
        print(f"  {name:34s} n={len(xs):<3} min={xs[0]:.3f} "
              f"p50={xs[len(xs) // 2]:.3f} max={xs[-1]:.3f}")
    _span("manual: top coverage | LABEL=resolve", should_resolve_cov)
    _span("manual: top coverage | LABEL=refuse", should_refuse_cov)
    _span("det:    top score    | LABEL=resolve", should_resolve_score)
    _span("det:    top score    | LABEL=refuse", should_refuse_score)
    if should_resolve_cov and should_refuse_cov:
        print(f"\n  Coverage separation: lowest correct-resolve = {min(should_resolve_cov):.3f} · "
              f"highest must-refuse = {max(should_refuse_cov):.3f} → "
              f"{'SEPARABLE' if min(should_resolve_cov) > max(should_refuse_cov) else 'OVERLAPPING'}")
    if should_resolve_score and should_refuse_score:
        print(f"  Score separation:    lowest correct-resolve = {min(should_resolve_score):.3f} · "
              f"highest must-refuse = {max(should_refuse_score):.3f} → "
              f"{'SEPARABLE' if min(should_resolve_score) > max(should_refuse_score) else 'OVERLAPPING'}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default=None, help="score one fixture class only")
    ap.add_argument("--json", default=None, help="dump raw results to this path")
    args = ap.parse_args()

    results = asyncio.run(run_all(args.only))
    summarize(results)
    if args.json:
        Path(args.json).write_text(
            json.dumps([asdict(r) for r in results], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"\nwrote {args.json}")


if __name__ == "__main__":
    main()
