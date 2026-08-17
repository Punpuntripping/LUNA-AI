"""Roll two ``run_resolution --json`` dumps into ONE before/after table.

The prior report (`agents_reports/simple_search_eval_resolution.md` §2) and the
fix lane's §13e table quote head-line percentages whose denominators are not
written down anywhere. Rather than argue about them, this module re-derives
every metric from the RAW dumps with a single stated definition and applies it
identically to both sides, so the delta is apples-to-apples even where an
absolute number disagrees with a published one.

Definitions (stated once, applied to both dumps)::

    committed        the leg returned status == "resolved"
    refusal precision   over fixtures labeled refuse|ask that the leg ran:
                        (n - committed) / n
    resolve recall      over fixtures labeled resolve that the leg ran:
                        verdict == PASS / n
    wrong-doc commits   verdicts == WRONG_DOC  (a commit to a document the
                        label forbids — the failure that reaches a user)

Run::

    python agents/simple_search/eval/rerun_metrics.py BEFORE.json AFTER.json
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
except Exception:  # noqa: BLE001
    pass

from agents.simple_search.eval.fixtures_resolution import FIXTURES  # noqa: E402

_EXPECT = {f.fid: f.expect for f in FIXTURES}
_CLS = {f.fid: f.cls for f in FIXTURES}


def load(path: str) -> dict[tuple[str, str], dict]:
    """{(fid, leg): leg_result} from a run_resolution --json dump."""
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    return {(fr["fid"], leg["leg"]): leg for fr in raw for leg in fr["legs"]}


def metrics(dump: dict[tuple[str, str], dict], leg: str) -> dict:
    rows = [(fid, r) for (fid, lg), r in dump.items() if lg == leg]
    refuse = [(fid, r) for fid, r in rows if _EXPECT[fid] in ("refuse", "ask")]
    resolve = [(fid, r) for fid, r in rows if _EXPECT[fid] == "resolve"]
    committed = [fid for fid, r in refuse if r["status"] == "resolved"]
    recalled = [fid for fid, r in resolve if r["verdict"] == "PASS"]
    wrong = [fid for fid, r in rows if r["verdict"] == "WRONG_DOC"]
    return {
        "refusal_n": len(refuse),
        "refusal_ok": len(refuse) - len(committed),
        "refusal_pct": 100.0 * (len(refuse) - len(committed)) / max(len(refuse), 1),
        "committed_on": sorted(committed),
        "recall_n": len(resolve),
        "recall_ok": len(recalled),
        "recall_pct": 100.0 * len(recalled) / max(len(resolve), 1),
        "recall_missed": sorted(fid for fid, r in resolve if r["verdict"] != "PASS"),
        "wrong_doc": sorted(wrong),
    }


def main() -> int:
    before_p, after_p = sys.argv[1], sys.argv[2]
    before, after = load(before_p), load(after_p)

    print(f"BEFORE  {before_p}")
    print(f"AFTER   {after_p}\n")
    print(f"{'metric':26s}{'det before':>13s}{'det after':>12s}"
          f"{'manual before':>16s}{'manual after':>14s}")
    print("-" * 81)
    mb = {leg: metrics(before, leg) for leg in ("det", "manual")}
    ma = {leg: metrics(after, leg) for leg in ("det", "manual")}
    rows = [
        ("refusal precision", "refusal_pct", "refusal_ok", "refusal_n"),
        ("resolve recall", "recall_pct", "recall_ok", "recall_n"),
    ]
    for label, pct, ok, n in rows:
        def cell(m: dict) -> str:
            return f"{m[pct]:.1f}% ({m[ok]}/{m[n]})"
        print(f"{label:26s}{cell(mb['det']):>13s}{cell(ma['det']):>12s}"
              f"{cell(mb['manual']):>16s}{cell(ma['manual']):>14s}")
    print(f"{'wrong-doc commits':26s}{len(mb['det']['wrong_doc']):>13d}"
          f"{len(ma['det']['wrong_doc']):>12d}"
          f"{len(mb['manual']['wrong_doc']):>16d}"
          f"{len(ma['manual']['wrong_doc']):>14d}")

    for leg in ("det", "manual"):
        print(f"\n--- {leg} ---")
        print(f"  committed on a refuse/ask label : "
              f"{mb[leg]['committed_on']} → {ma[leg]['committed_on']}")
        print(f"  missed a resolve label          : "
              f"{mb[leg]['recall_missed']} → {ma[leg]['recall_missed']}")
        print(f"  WRONG_DOC                       : "
              f"{mb[leg]['wrong_doc']} → {ma[leg]['wrong_doc']}")

    # Per-class strict PASS rate, both dumps, both legs.
    print("\n" + "=" * 81)
    print("PER-CLASS strict PASS  (before → after)")
    print("=" * 81)
    classes = sorted({_CLS[f] for f in _CLS})
    for cls in classes:
        for leg in ("det", "manual"):
            b = [r for (fid, lg), r in before.items() if lg == leg and _CLS[fid] == cls]
            a = [r for (fid, lg), r in after.items() if lg == leg and _CLS[fid] == cls]
            if not a and not b:
                continue
            def line(rs: list[dict]) -> str:
                if not rs:
                    return "—"
                p = sum(1 for r in rs if r["verdict"] == "PASS")
                w = sum(1 for r in rs if r["verdict"] == "WRONG_DOC")
                return f"{p}/{len(rs)} pass, {w} wrong-doc"
            print(f"{cls:16s} {leg:7s} {line(b):26s} → {line(a)}")

    # Per-fixture verdict changes, so nothing hides inside a rolled-up number.
    print("\n" + "=" * 81)
    print("PER-FIXTURE VERDICT CHANGES")
    print("=" * 81)
    changed = 0
    for key in sorted(set(before) | set(after)):
        b = before.get(key, {}).get("verdict", "—")
        a = after.get(key, {}).get("verdict", "—")
        if b != a:
            changed += 1
            fid, leg = key
            print(f"  {fid:8s} {leg:7s} {b:9s} → {a:9s}   "
                  f"{after.get(key, {}).get('status', '')}/"
                  f"{after.get(key, {}).get('gate', '')}")
    print(f"  ({changed} leg-evaluations changed verdict)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
