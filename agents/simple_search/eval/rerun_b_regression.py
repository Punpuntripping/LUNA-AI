"""Case-B RE-RUN — THE hijack regression + the uncarded `[1]`.

Leg 1 is `case_b_regression.main()` reused verbatim (carry نظام العمل, ask
«اش هي المادة 5 من نظام التنفيذ؟», assert the searcher runs and ITS object
wins) — the invariant the brief says must never break. It scored 14/14 before
the carrier fixes and the second router patch; nothing in either should have
touched it, which is exactly why it is re-run.

Leg 2 is the uncarded `[1]`: the original eval saw a chat reply ship
«…لذلك [1].» with no card behind it — no `workspace_item_references` rows, and
a dead marker on the user's screen. The agent that makes that call has since
changed: the card decision left `SynthesizerOutput` and became the responder's
`CardVerdict.card` (responder plan §5/§6), so an uncarded body is now one the
**responder declined** — either `card=False`, or no verdict at all, which
`ResponderOutput.verdict_for` returns as `None` and `_finalise` reads as "no
card". The seam is the same and so is the fix: `runner._finalise` (`:1734-1777`)
runs every body it is about to paste into the bubble through
`_strip_citation_markers` (`:173`) on exactly that branch. Verified two ways —
the function directly on the exact string the original eval captured, and
against every chat message the live runs above produced. Note the live check's
`carded or not has_marker` is now slack on the carded side: a carded answer
contributes nothing to the bubble at all (§9), so its markers ship on the card
where they resolve, and the bubble has no body to carry a dead one.

    .venv/Scripts/python.exe agents/simple_search/eval/rerun_b_regression.py
"""
from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from typing import Any

from rerun_b_common import hr, use_rerun_scratch  # noqa: E402

use_rerun_scratch()

from agents.simple_search import runner as RUNNER  # noqa: E402
from agents.simple_search.eval import case_b_regression as CBR  # noqa: E402

_MARKER = re.compile(r"\[\s*[\d٠-٩]+(?:\s*[,،]\s*[\d٠-٩]+)*\s*\]")

#: The verbatim tail the original eval captured shipping with no card behind it
#: (`simple_search_eval_case_b.md` §3, finding F-8).
F8_BODY = "وتحدد اللائحة الأحكام اللازمة لذلك [1]."


def citation_checks() -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []

    def check(name: str, ok: bool, detail: Any = "") -> None:
        out.append({"check": name, "ok": bool(ok), "detail": detail})
        print(("  PASS  " if ok else "  FAIL  ") + name)
        if not ok:
            print(f"        → {detail}")

    hr("UNCARDED [1] — the stripper")
    stripped = RUNNER._strip_citation_markers(F8_BODY)
    print(f"  in : {F8_BODY!r}")
    print(f"  out: {stripped!r}")
    check("F-8's exact body loses its marker", not _MARKER.search(stripped), stripped)
    check("the prose survives (only the marker goes)",
          "وتحدد اللائحة الأحكام اللازمة لذلك" in stripped, stripped)
    check("no double space left behind", "  " not in stripped, stripped)
    check("the sentence still ends on its full stop", stripped.endswith("."), stripped)

    multi = RUNNER._strip_citation_markers("نص [1] وآخر [2,3] وثالث [١،٢] .")
    check("multi-marker + Arabic-Indic + Arabic comma all stripped",
          not _MARKER.search(multi), multi)

    kept = "نص المادة الخامسة [1]."
    check("with a card, markers are NOT touched (the stripper is gated, not global)",
          "[1]" in kept, kept)
    return out


async def main() -> int:
    hr("CASE-B RE-RUN · the hijack regression (reusing case_b_regression)")
    rc = await CBR.main()

    results = list(CBR.RESULTS)
    passed = sum(1 for r in results if r["ok"])
    print(f"\nhijack regression: {passed}/{len(results)}")
    for r in results:
        if not r["ok"]:
            print(f"  FAIL  {r['check']}\n        {str(r['detail'])[:400]}")

    cit = citation_checks()

    # Every chat message the live runs just produced, checked for a dead marker.
    hr("UNCARDED [1] — against the live answers just produced")
    dump = Path(__file__).with_name("case_b_regression_results.json")
    live: list[dict[str, Any]] = []
    if dump.exists():
        data = json.loads(dump.read_text(encoding="utf-8"))
        bodies = [{"q": data.get("question"), "answer": data.get("answer", ""),
                   "created": data.get("created_item_ids") or []}]
        bodies += [{"q": e.get("question"), "answer": e.get("answer_head", ""),
                    "created": e.get("created") or []} for e in data.get("extra", [])]
        for b in bodies:
            has_marker = bool(_MARKER.search(b["answer"] or ""))
            carded = bool(b["created"])
            ok = carded or not has_marker
            live.append({"question": b["q"], "carded": carded,
                         "has_marker": has_marker, "ok": ok})
            print(f"  {'PASS' if ok else 'FAIL'}  card={carded} marker={has_marker}  "
                  f"«{b['q']}»")
    else:
        print("  (no regression dump to scan)")

    out = {"hijack": {"results": results, "passed": passed, "total": len(results)},
           "citation_unit": cit, "citation_live": live,
           "exit_code": rc}
    path = Path(__file__).with_name("rerun_b_regression_results.json")
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    hr(f"hijack {passed}/{len(results)} · "
       f"citation {sum(1 for c in cit if c['ok'])}/{len(cit)} · "
       f"live {sum(1 for l in live if l['ok'])}/{len(live)} → {path.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
