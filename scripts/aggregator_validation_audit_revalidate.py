"""Re-run the aggregator postvalidator gates against captured Logfire data.

For each retried aggregator, this loads:
  - The primary run's `synthesis_md`, `used_refs`, `gaps`
  - The primary run's `pydantic_ai.all_messages` (which contains the input
    `<reference cite="[N]">` block and `<sub_query index="..." sufficient="...">` tags)

And then re-applies the four hard gates from
agents.deep_search_v4.aggregator.postvalidator to attribute each retry to one
or more gate failures.

Run:  python scripts/aggregator_validation_audit_revalidate.py
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

# Make repo root importable
REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from agents.deep_search_v4.aggregator.postvalidator import (  # noqa: E402
    check_arabic_only,
    check_structure,
    extract_cited_numbers,
    _LATIN_SENTENCE_RE,
    strip_thinking_block,
)

AUDIT_DIR = REPO_ROOT / "agents_reports" / "aggregator_validation_audit"

# Map: agg_id -> primary run_id (idx=1 in raw_retries.json)
RETRIES = [
    {
        "agg_id": "3db7d9c8d896dea7",
        "primary_run_id": "79d186e73666c21f",
        "fallback_run_id": "1628204aaa45b7b7",
        "prompt_key": "prompt_mode_reg",
        "model_used": "gemini-3-flash",  # final outcome label
        "msg_file": "msgs_79d186e7_primary_3db7d9c8.json",
        "start_timestamp": "2026-05-22T09:37:44Z",
    },
    {
        "agg_id": "cc86f2aa34ebe24c",
        "primary_run_id": "ebe88aa78619e587",
        "fallback_run_id": "a94762c20b0c13e1",
        "prompt_key": "prompt_mode_reg",
        "model_used": "gemini-3-flash",
        "msg_file": "msgs_ebe88aa7_primary_cc86f2aa.json",
        "start_timestamp": "2026-05-28T12:51:24Z",
    },
    {
        "agg_id": "bba48cdd2f86c5c7",
        "primary_run_id": "5c22f76cad3d0e07",
        "fallback_run_id": "55f6b23c2e3eb4e7",
        "prompt_key": "prompt_mode_compliance",
        "model_used": "gemini-3-flash",
        "msg_file": "msgs_5c22f76c_primary_bba48cdd.json",
        "start_timestamp": "2026-05-30T12:39:02Z",
    },
    {
        "agg_id": "f9a41800b1f47180",
        "primary_run_id": "818b84c33768fc31",
        "fallback_run_id": "580e5078dc97109d",
        "prompt_key": "prompt_mode_reg",
        "model_used": "gemini-3-flash",
        "msg_file": "msgs_818b84c3_primary_f9a41800.json",
        "start_timestamp": "2026-05-31T12:25:16Z",
    },
]


def load_raw_retries() -> dict[tuple[str, int], dict]:
    """Return mapping (agg_id, run_idx) -> row dict from raw_retries.json."""
    raw = json.loads((AUDIT_DIR / "raw_retries.json").read_text(encoding="utf-8"))
    out: dict[tuple[str, int], dict] = {}
    for r in raw["rows"]:
        out[(r["agg_id"], int(r["run_idx"]))] = r
    return out


def load_messages_json(msg_file: str) -> str:
    """Logfire's `pydantic_ai.all_messages` attribute (str of JSON list)."""
    payload = json.loads((AUDIT_DIR / msg_file).read_text(encoding="utf-8"))
    return payload["rows"][0]["messages_json"]


def extract_user_text_from_messages(msgs_json_str: str) -> str:
    """Pull out the user message text from the all_messages dump.

    Logfire stores `pydantic_ai.all_messages` as a JSON-encoded list of
    `{role, parts: [{type, content}]}` dicts. The user message holds the
    aggregator input (including the `<reference cite="[N]">` block and
    `<sub_query>` tags).
    """
    try:
        msgs = json.loads(msgs_json_str)
    except json.JSONDecodeError:
        return msgs_json_str  # last resort

    pieces: list[str] = []
    for m in msgs:
        role = m.get("role") or m.get("kind") or m.get("type")
        # Modern pydantic-ai shape: parts is a list of {type/part_kind, content}
        for p in (m.get("parts") or []):
            pk = p.get("part_kind") or p.get("kind") or p.get("type") or ""
            if role == "user" or pk in ("user-prompt", "user", "text"):
                c = p.get("content")
                if isinstance(c, str):
                    pieces.append(c)
        # Older shape: direct content on the message
        if role in ("user", "human") and isinstance(m.get("content"), str):
            pieces.append(m["content"])
    return "\n\n".join(pieces)


def extract_valid_ref_set(user_text: str) -> set[int]:
    """Parse <reference cite="[N]"> tags from the user message body."""
    nums: set[int] = set()
    # Each tag looks like: <reference cite="[3]" source_type="..." ...>
    for m in re.finditer(r'cite="\[([0-9,\s،]+)\]"', user_text):
        for part in re.split(r"[,،]", m.group(1)):
            part = part.strip()
            if part.isdigit():
                nums.add(int(part))
    if not nums:
        # Bare-quote fallback
        for m in re.finditer(r"cite=\[([0-9,\s،]+)\]", user_text):
            for part in re.split(r"[,،]", m.group(1)):
                part = part.strip()
                if part.isdigit():
                    nums.add(int(part))
    return nums


def extract_sub_query_sufficient(user_text: str) -> list[tuple[int, str]]:
    """Return list of (index, sufficient_value) for each <sub_query> tag.

    `sufficient` is Arabic 'كافٍ' / 'غير كافٍ' or possibly bool string.
    """
    out: list[tuple[int, str]] = []
    pattern = re.compile(
        r'<sub_query[^>]*index="(\d+)"[^>]*sufficient="([^"]*)"',
        re.IGNORECASE,
    )
    for m in pattern.finditer(user_text):
        out.append((int(m.group(1)), m.group(2)))
    if not out:
        # Alternative attribute order
        for m in re.finditer(
            r'<sub_query[^>]*sufficient="([^"]*)"[^>]*index="(\d+)"',
            user_text,
        ):
            out.append((int(m.group(2)), m.group(1)))
    return out


def find_latin_offenders(synth: str) -> list[str]:
    """Pull each Latin-sentence match so we can categorize the trigger.

    Returns up to 10 distinct samples.
    """
    body = strip_thinking_block(synth)
    body = re.sub(r"```.*?```", " ", body, flags=re.DOTALL)
    body = re.sub(r"`[^`]*`", " ", body)
    seen: list[str] = []
    for m in _LATIN_SENTENCE_RE.finditer(body):
        # Pull ~40 chars of surrounding context so we can see if it's
        # parenthetical / URL / acronym.
        start = max(0, m.start() - 40)
        end = min(len(body), m.end() + 40)
        ctx = body[start:end].replace("\n", " ")
        if ctx not in seen:
            seen.append(ctx)
        if len(seen) >= 10:
            break
    return seen


def heading_outline(synth: str) -> list[str]:
    """Return the H2/H3 headings (raw text) in order."""
    body = strip_thinking_block(synth)
    out: list[str] = []
    for line in body.splitlines():
        m = re.match(r"^(#{2,4})\s*(.+?)\s*$", line)
        if m:
            out.append(f"{m.group(1)} {m.group(2)}")
    return out


def revalidate_one(retry: dict, raw: dict) -> dict:
    primary = raw[(retry["agg_id"], 1)]
    fallback = raw[(retry["agg_id"], 2)]
    msgs_str = load_messages_json(retry["msg_file"])
    user_text = extract_user_text_from_messages(msgs_str)

    synth = primary["synth"] or ""
    used_refs = json.loads(primary["used_refs_str"]) if primary.get("used_refs_str") else []
    gaps = json.loads(primary["gaps_str"]) if primary.get("gaps_str") else []

    # Re-run gates against primary
    cited = extract_cited_numbers(synth)
    valid_refs = extract_valid_ref_set(user_text)
    dangling = sorted(n for n in cited if n not in valid_refs) if valid_refs else []
    citation_ok = len(dangling) == 0

    arabic_ok = check_arabic_only(synth)
    latin_samples = [] if arabic_ok else find_latin_offenders(synth)

    structure_ok, structure_notes = check_structure(synth, retry["prompt_key"])

    sub_query_status = extract_sub_query_sufficient(user_text)
    any_insufficient = any(
        s.startswith("غير") or s.startswith("Insuff") or s == "false"
        for _, s in sub_query_status
    )
    gap_honesty_ok = not (any_insufficient and not gaps)

    passed = citation_ok and arabic_ok and structure_ok and gap_honesty_ok

    # Fallback: side-by-side
    fb_synth = fallback["synth"] or ""
    fb_used_refs = json.loads(fallback["used_refs_str"]) if fallback.get("used_refs_str") else []
    fb_gaps = json.loads(fallback["gaps_str"]) if fallback.get("gaps_str") else []

    fb_cited = extract_cited_numbers(fb_synth)
    fb_dangling = sorted(n for n in fb_cited if n not in valid_refs) if valid_refs else []
    fb_citation_ok = len(fb_dangling) == 0
    fb_arabic_ok = check_arabic_only(fb_synth)
    fb_structure_ok, fb_structure_notes = check_structure(fb_synth, "prompt_1")
    fb_latin_samples = [] if fb_arabic_ok else find_latin_offenders(fb_synth)
    fb_gap_honesty_ok = not (any_insufficient and not fb_gaps)
    fb_passed = (
        fb_citation_ok and fb_arabic_ok and fb_structure_ok and fb_gap_honesty_ok
    )

    return {
        "agg_id": retry["agg_id"],
        "start_timestamp": retry["start_timestamp"],
        "prompt_key": retry["prompt_key"],
        "valid_ref_set_size": len(valid_refs),
        "sub_query_count": len(sub_query_status),
        "any_insufficient": any_insufficient,
        "primary": {
            "synth_len": len(synth),
            "synth_first_1500": synth[:1500],
            "used_refs": used_refs,
            "cited_numbers": cited,
            "dangling": dangling,
            "gaps": gaps,
            "citation_ok": citation_ok,
            "arabic_ok": arabic_ok,
            "structure_ok": structure_ok,
            "structure_notes": structure_notes,
            "gap_honesty_ok": gap_honesty_ok,
            "passed": passed,
            "latin_samples": latin_samples,
            "headings": heading_outline(synth),
        },
        "fallback": {
            "synth_len": len(fb_synth),
            "synth_first_1500": fb_synth[:1500],
            "used_refs": fb_used_refs,
            "cited_numbers": fb_cited,
            "dangling": fb_dangling,
            "gaps": fb_gaps,
            "citation_ok": fb_citation_ok,
            "arabic_ok": fb_arabic_ok,
            "structure_ok": fb_structure_ok,
            "structure_notes": fb_structure_notes,
            "gap_honesty_ok": fb_gap_honesty_ok,
            "passed": fb_passed,
            "latin_samples": fb_latin_samples,
            "headings": heading_outline(fb_synth),
        },
    }


def main() -> None:
    raw = load_raw_retries()
    results = [revalidate_one(r, raw) for r in RETRIES]
    out_file = AUDIT_DIR / "revalidation_results.json"
    out_file.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote {out_file}")
    # Brief stdout summary
    for r in results:
        p = r["primary"]
        gates = [
            ("citation_ok", p["citation_ok"]),
            ("arabic_ok", p["arabic_ok"]),
            ("structure_ok", p["structure_ok"]),
            ("gap_honesty_ok", p["gap_honesty_ok"]),
        ]
        failed = [name for name, ok in gates if not ok]
        print(
            f"{r['agg_id']}  prompt={r['prompt_key']}  "
            f"valid_refs={r['valid_ref_set_size']}  cited={p['cited_numbers']}  "
            f"failed={failed}  notes={p['structure_notes'][:2]}"
        )


if __name__ == "__main__":
    main()
