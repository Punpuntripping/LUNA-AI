"""Rebuild ``adv_routing_results.json`` from the run-1 stdout log.

Why this exists: the first cut of :class:`Results` flushed its fresh empty
document **before** reading the file back, so launching the addendum pass
truncated the completed 54-probe result set. One router call (``cmp-01#0``) had
already been re-fired into the empty file when the process was killed.

The stdout log is a complete record of that run — every probe printed its id,
rep, verdict, expectation, outcome, latency, query, tool lines and either the
dispatch JSON or the reply (flattened, capped at 300 chars by ``short()``,
alongside the reply's TRUE character count). This parses it back into the same
schema, marking every recovered chat row with ``message_truncated`` so nobody
mistakes a 300-char preview for the whole reply.

Recovered rows carry ``recovered_from_log: true``. The verdicts are exact — the
truncation touches reply previews only, never id / expect / got / ok.

    .venv/Scripts/python.exe agents/simple_search/eval/adv_routing_recover.py
"""
from __future__ import annotations

import ast
import json
import re
from pathlib import Path

from adv_routing_common import RESULTS_PATH, S_CASES, S_HEIRS, S_ONECASE  # noqa: E402

LOG = Path(__file__).with_name("adv_routing_run.log")

_HEAD = re.compile(
    r"^\[([a-z0-9\-]+)#(\d+)\] (PASS|FAIL)\s+expect=(\S+) got=(\S+)\s+\(([\d.]+)s\)$"
)
_CHAT = re.compile(r"^\s+→ chat \((\d+) chars\): (.*)$")

# id → (block, context, why) — the fixture table, kept in step with adv_routing.PROBES.
from adv_routing import PROBES  # noqa: E402

_META = {p[0]: (p[1], p[2], p[5]) for p in PROBES}


def parse(text: str) -> list[dict]:
    rows: list[dict] = []
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        m = _HEAD.match(lines[i].strip())
        if not m:
            i += 1
            continue
        pid, rep, _verdict, expect, got, secs = m.groups()
        block, ctx, why = _META.get(pid, ("?", "?", ""))
        row: dict = {
            "id": pid, "block": block, "rep": int(rep), "context": ctx,
            "q": "", "expect": expect, "why": why,
            "elapsed_s": float(secs), "tools": [], "got": got,
            "ok": got == expect, "recovered_from_log": True,
        }
        i += 1
        while i < len(lines) and not _HEAD.match(lines[i].strip()):
            ln = lines[i]
            s = ln.strip()
            if s.startswith("Q: "):
                row["q"] = s[3:]
            elif s.startswith("tools: "):
                try:
                    row["tools"] = ast.literal_eval(s[len("tools: "):])
                except Exception:  # noqa: BLE001
                    row["tools"] = [s[len("tools: "):]]
            elif (c := _CHAT.match(ln)):
                row["message_chars"] = int(c.group(1))
                row["message"] = c.group(2)
                row["message_truncated"] = int(c.group(1)) > len(c.group(2))
            elif s.startswith("→ {"):
                try:
                    row["dispatch"] = json.loads(s[2:])
                except Exception:  # noqa: BLE001
                    row["dispatch_raw"] = s[2:]
                row["message"] = ""
            i += 1
        rows.append(row)
    return rows


def main() -> int:
    rows = parse(LOG.read_text(encoding="utf-8", errors="replace"))
    # Dedup on (id, rep) keeping the first — run 1 fired each exactly once.
    seen: set[tuple[str, int]] = set()
    probes: list[dict] = []
    for r in rows:
        key = (r["id"], r["rep"])
        if key in seen:
            continue
        seen.add(key)
        probes.append(r)

    doc = json.loads(RESULTS_PATH.read_text(encoding="utf-8"))
    doc["probes"] = probes
    doc["contexts"] = {"cases": S_CASES, "heirs": S_HEIRS, "onecase": S_ONECASE,
                       "empty": None, "ss_card": doc["conversation_id"]}
    doc["recovery"] = {
        "reason": "Results() truncated the file before reading it back on resume; "
                  "run-1 probes rebuilt from adv_routing_run.log",
        "source": LOG.name,
        "probes_recovered": len(probes),
        "chat_previews_capped_at": 300,
        "verdicts_are_exact": True,
    }
    doc["done"] = True
    RESULTS_PATH.write_text(json.dumps(doc, ensure_ascii=False, indent=2),
                            encoding="utf-8")

    ok = sum(1 for p in probes if p["ok"])
    print(f"recovered {len(probes)} probes → {RESULTS_PATH.name}   {ok}/{len(probes)} correct")
    missing = [(p[0], r) for p in PROBES for r in range(3)
               if (p[0], r) not in seen]
    print(f"not present in the log (still to run): {missing}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
