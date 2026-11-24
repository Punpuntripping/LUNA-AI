"""Phase 0 dry run — the citation scan over the real attachments. NUMBERS ONLY.

Plan: ``.claude/plans/completion_search.md`` §7 Phase 0. READ-ONLY: it never writes to
the database and never calls a model. It prints counts; it never prints a file's text,
title, id or owner.

What it answers before anything is built on top of the scan:

- how many attachments cite an article, and how the number is written;
- how each citation is tied to a law (same phrase / back-reference / one law in the
  document / a guess), i.e. the high / medium split of plan §4.6.3;
- how many law names equal a library title exactly (no model needed) and how many
  articles exist under that title;
- where a citation lands in the attachment's segments (by character position);
- how much pinned text a document would carry under the 10,000 / 30,000 limits.

Run (cwd = repo root)::

    python scripts/citation_scan_dryrun.py                    # counts to stdout
    python scripts/citation_scan_dryrun.py --out DIR          # also save JSON (DIR outside git)
    python scripts/citation_scan_dryrun.py --names 3          # + law names not in the library
                                                              #   that 3+ different users wrote

``--names`` prints only names written by at least N different users, so nothing that
belongs to one person's file is shown.
"""
from __future__ import annotations

import argparse
import bisect
import json
import statistics
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agents.tool_repository.citation_scan import (  # noqa: E402
    BASIS_BACK_REF,
    BASIS_NO_LAW,
    BASIS_OTHER_TARGET,
    BASIS_SAME_PHRASE,
    BASIS_SINGLE_LAW,
    KIND_NAMED,
    SCAN_RULE_VERSION,
    TitleIndex,
    scan_citations,
)

PER_ARTICLE_CAP = 10_000      # plan C23
PER_DOCUMENT_NET = 30_000     # plan C23 (safety net)
IN_FORCE = ("in_force", "in_force_amended")

# One citation (a mention × one article number) ends in exactly one of these.
PIN = "pin_by_code"                       # exact title + article exists + checkable link
MEDIUM = "medium_link_is_a_guess"         # exact title + article exists, link not checkable
NEEDS_MODEL_NAME = "name_not_a_library_title"
NEEDS_MODEL_BACKREF = "back_reference_several_laws"
ARTICLE_MISSING = "article_number_not_in_that_law"
LAW_NO_TEXT = "law_has_no_article_text"
NOT_LAW = "belongs_to_something_else"     # «من العقد»
NO_LAW = "no_law_named_anywhere"
BACKREF_NO_LAW = "back_reference_nothing_before"
RANGE_WIDE = "range_too_wide"
OPEN_NAME = "name_may_run_on"
EXEC_UNKNOWN = "executive_regulation_not_found"
NOT_IN_FORCE = "title_matches_only_a_draft_or_cancelled_text"
MODEL_STATUSES = (NEEDS_MODEL_NAME, NEEDS_MODEL_BACKREF, OPEN_NAME, EXEC_UNKNOWN)


def _page(query, size: int):
    start = 0
    while True:
        rows = query.range(start, start + size - 1).execute().data or []
        yield from rows
        if len(rows) < size:
            return
        start += size


def load_library(sb: Any):
    regs: dict[str, dict] = {}
    pairs: list[tuple[str, str]] = []
    q = sb.table("regulations_v2").select(
        "id,title,clean_title,status_class,parent_law_id,doc_type_bucket"
    ).order("id")
    for r in _page(q, 1000):
        regs[r["id"]] = r
        for t in {r.get("title") or "", r.get("clean_title") or ""}:
            if t.strip():
                pairs.append((r["id"], t))
    articles: dict[str, dict[str, str]] = defaultdict(dict)
    q = sb.table("articles_v2").select("id,regulation_id,article_number").order("id")
    for a in _page(q, 1000):
        num = (a.get("article_number") or "").strip()
        if num:
            articles[a["regulation_id"]].setdefault(num, a["id"])
    exec_of: dict[str, list[str]] = defaultdict(list)
    for r in regs.values():
        if r.get("parent_law_id") and r.get("doc_type_bucket") == "executive_regulation":
            exec_of[r["parent_law_id"]].append(r["id"])
    return regs, TitleIndex(pairs), articles, exec_of


def load_segments(sb: Any) -> dict[str, list[tuple[int, int, str]]]:
    out: dict[str, list[tuple[int, int, str]]] = defaultdict(list)
    q = sb.table("attachment_segments").select("item_id,seq,char_start,char_end,value").order("item_id").order("seq")
    for s in _page(q, 1000):
        if s.get("char_start") is None or s.get("char_end") is None:
            out[s["item_id"]].append((-1, -1, s.get("value") or ""))
        else:
            out[s["item_id"]].append((int(s["char_start"]), int(s["char_end"]), s.get("value") or ""))
    for rows in out.values():
        rows.sort()
    return out


def article_keys(number: str) -> tuple[list[str], list[str]]:
    """``(exact keys, fallback keys)`` for one number as the scan wrote it."""
    if "/" in number or "-" in number or "." in number:
        sep = "/" if "/" in number else ("-" if "-" in number else ".")
        a, _, b = number.partition(sep)
        exact = [number, f"{a}-{b}", f"{a}/{b}", f"{b}/{a}", f"{b}-{a}"]
        try:
            base = str(max(int(a), int(b.split()[0])))
        except ValueError:
            base = a
        return list(dict.fromkeys(exact)), [base]
    return [number], []


def pick_regulation(ids, regs, articles):
    """Among regulations sharing one title: in force with article text first."""
    def rank(reg_id: str):
        r = regs.get(reg_id) or {}
        return (0 if r.get("status_class") in IN_FORCE else 1, 0 if articles.get(reg_id) else 1, reg_id)
    return sorted(ids, key=rank)[0] if ids else None


def decide(result, mention, number, titles, regs, articles, exec_of):
    """One citation → ``(status, regulation_id, article_id)``."""
    if mention.basis == BASIS_OTHER_TARGET:
        return NOT_LAW, None, None
    if mention.range_wide:
        return RANGE_WIDE, None, None
    if mention.basis == BASIS_NO_LAW:
        return NO_LAW, None, None
    if mention.law is None:
        return BACKREF_NO_LAW, None, None
    law = result.laws[mention.law]
    if not law.in_library:
        return NEEDS_MODEL_NAME, None, None
    if law.open_end:
        return OPEN_NAME, None, None
    reg_id = pick_regulation(titles.ids(law.key), regs, articles)
    if mention.exec_reg:
        children = exec_of.get(reg_id) or []
        reg_id = pick_regulation(children, regs, articles)
        if not reg_id:
            return EXEC_UNKNOWN, None, None
    have = articles.get(reg_id) or {}
    if not have:
        return LAW_NO_TEXT, reg_id, None
    exact, fallback = article_keys(number)
    article_id = next((have[k] for k in exact if k in have), None)
    via_fallback = False
    if article_id is None:
        article_id = next((have[k] for k in fallback if k in have), None)
        via_fallback = article_id is not None
    if article_id is None:
        return ARTICLE_MISSING, reg_id, None
    if (regs.get(reg_id) or {}).get("status_class") not in IN_FORCE:
        return NOT_IN_FORCE, reg_id, article_id
    # A back-reference is checkable when exactly one law is named in reach before it,
    # or the whole document names one law.
    checkable = (
        mention.basis in (BASIS_SAME_PHRASE, BASIS_SINGLE_LAW)
        or (mention.basis == BASIS_BACK_REF and (mention.laws_before == 1 or len(result.distinct_named()) == 1))
    )
    if mention.basis == BASIS_BACK_REF and not checkable:
        return NEEDS_MODEL_BACKREF, reg_id, article_id
    if not checkable or via_fallback:
        return MEDIUM, reg_id, article_id
    return PIN, reg_id, article_id


def pct(values: list[float], p: float) -> float:
    if not values:
        return 0
    values = sorted(values)
    k = min(len(values) - 1, max(0, int(round(p * (len(values) - 1)))))
    return values[k]


def spread(values: list[float]) -> dict:
    if not values:
        return {"n": 0}
    return {"n": len(values), "p50": pct(values, 0.5), "p90": pct(values, 0.9),
            "p99": pct(values, 0.99), "max": max(values), "mean": round(statistics.mean(values), 1)}


def _inside_git(path: Path) -> bool:
    p = path.resolve()
    return any((q / ".git").exists() for q in [p, *p.parents])


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="", help="folder OUTSIDE every git repository for the JSON")
    ap.add_argument("--names", type=int, default=0, help="print unresolved law names written by at least N users")
    ap.add_argument("--limit", type=int, default=0, help="scan only the first N files (smoke run)")
    args = ap.parse_args()
    out_dir = Path(args.out) if args.out else None
    if out_dir and _inside_git(out_dir):
        raise SystemExit("--out is inside a git repository; choose a folder outside it")

    from shared.db.client import get_supabase_client

    sb = get_supabase_client()
    t0 = time.time()
    regs, titles, articles, exec_of = load_library(sb)
    segments = load_segments(sb)
    print(f"library: {len(regs)} regulations, {len(titles)} distinct titles, "
          f"{sum(len(v) for v in articles.values())} articles in {len(articles)} regulations; "
          f"{len(segments)} segment maps ({time.time() - t0:.0f}s)", file=sys.stderr)

    by_type: dict[str, Counter] = defaultdict(Counter)
    total = Counter()
    form = Counter()
    basis = Counter()
    status = Counter()
    status_by_basis: dict[str, Counter] = defaultdict(Counter)
    law_kinds = Counter()
    seg_value = Counter()
    seg_hits = Counter()
    distinct_laws_per_file: list[float] = []
    pins_per_file: list[float] = []
    pinned_articles_per_file: list[float] = []
    scan_seconds: list[float] = []
    pin_articles: dict[str, set[str]] = {}            # file → article ids
    pin_sections: dict[str, set[tuple[int, str]]] = {}  # file → (segment index, article id)
    unresolved_names: dict[tuple[str, ...], set[str]] = defaultdict(set)
    unresolved_count = Counter()
    unresolved_shape = Counter()
    pin_detail = Counter()
    contract = Counter()

    q = sb.table("user_files").select("file_id,user_id,doc_type,content_chars,content_md") \
        .is_("deleted_at", "null").gt("content_chars", 0).order("file_id")
    n_files = 0
    for row in _page(q, 25):
        n_files += 1
        if args.limit and n_files > args.limit:
            break
        doc_type = row.get("doc_type") or "none"
        text = row.get("content_md") or ""
        t1 = time.time()
        result = scan_citations(text, row.get("doc_type"), titles)
        scan_seconds.append(time.time() - t1)
        citing = result.citing
        total["files"] += 1
        by_type[doc_type]["files"] += 1

        if doc_type == "contract":
            contract["files"] += 1
            contract["mentions"] += len(result.mentions)
            contract["headings"] += sum(1 for m in result.mentions if m.heading)
            contract["citing"] += len(citing)
            contract["same_phrase_named"] += sum(1 for m in citing if m.basis == BASIS_SAME_PHRASE)
            contract["other_target"] += sum(1 for m in citing if m.basis == BASIS_OTHER_TARGET)
            continue

        total["mentions"] += len(result.mentions)
        total["headings"] += len(result.mentions) - len(citing)
        total["citing_mentions"] += len(citing)
        total["line_start_not_heading"] += sum(1 for m in citing if m.line_start)
        if result.mentions:
            total["files_with_a_mention"] += 1
        if citing:
            total["files_citing"] += 1
            by_type[doc_type]["files_citing"] += 1
        for law in result.laws:
            law_kinds[law.kind + ("/in_library" if law.in_library else "")] += 1
            if law.kind == KIND_NAMED and not law.in_library:
                unresolved_names[law.key].add(row["user_id"])
                unresolved_count[law.key] += 1
                starts = {titles.ids(t) for t in titles.starting_with(law.key)}
                shape = ("short_form_of_one_law" if len(starts) == 1 else
                         "short_form_of_several_laws" if starts else "no_title_starts_like_it")
                unresolved_shape[shape] += 1
                unresolved_shape[f"head:{law.key[0]}"] += 1
                unresolved_shape[f"words:{len(law.key)}"] += 1
            if law.open_end:
                law_kinds["open_end"] += 1
        if citing:
            distinct_laws_per_file.append(len(result.distinct_named()))

        segs = segments.get(row["file_id"])
        seg_starts = [s[0] for s in segs] if segs else []
        file_status = Counter()
        file_articles: set[str] = set()
        file_sections: set[tuple[int, str]] = set()
        for m in citing:
            form[m.number_form] += 1
            basis[m.basis] += 1
            total["compound"] += int(m.compound)
            total["plural"] += int(m.plural)
            total["inherited"] += int(m.inherited)
            total["exec_reg"] += int(m.exec_reg)
            for number in m.numbers:
                st, reg_id, article_id = decide(result, m, number, titles, regs, articles, exec_of)
                status[st] += 1
                status_by_basis[m.basis][st] += 1
                file_status[st] += 1
                by_type[doc_type]["citations"] += 1
                by_type[doc_type][st] += 1
                if st == PIN:
                    pin_detail["title_shared_by_several_regulations"] += int(
                        len(titles.ids(result.laws[m.law].key)) > 1)
                    pin_detail[f"tie:{m.basis}"] += 1
                    pin_detail["through_executive_regulation"] += int(m.exec_reg)
                    file_articles.add(article_id)
                    if segs:
                        k = bisect.bisect_right(seg_starts, m.char_start) - 1
                        if k >= 0 and segs[k][0] <= m.char_start < segs[k][1]:
                            seg_hits["inside_one_segment"] += 1
                            seg_value[segs[k][2]] += 1
                            file_sections.add((k, article_id))
                        else:
                            seg_hits["in_a_gap_or_outside"] += 1
                    else:
                        seg_hits["file_has_no_map"] += 1
        if citing:
            total["citations"] += sum(file_status.values())
            needs_model = sum(file_status[k] for k in MODEL_STATUSES)
            total["citations_for_the_model"] += needs_model
            if file_status[PIN]:
                total["files_with_a_pin"] += 1
                by_type[doc_type]["files_with_a_pin"] += 1
                pins_per_file.append(file_status[PIN])
                pinned_articles_per_file.append(len(file_articles))
                pin_articles[row["file_id"]] = file_articles
                pin_sections[row["file_id"]] = file_sections
                if not segs:
                    total["files_with_a_pin_no_map"] += 1
            if needs_model:
                total["files_needing_the_model"] += 1
            else:
                total["files_decided_by_code_alone"] += 1
        if n_files % 100 == 0:
            print(f"  scanned {n_files} files ({time.time() - t0:.0f}s)", file=sys.stderr)

    # ---- size of the pinned text (public law text; lengths only) ----
    need = sorted({a for ids in pin_articles.values() for a in ids})
    length: dict[str, int] = {}
    for i in range(0, len(need), 60):
        rows = sb.table("articles_v2").select("id,content").in_("id", need[i:i + 60]).execute().data or []
        for r in rows:
            length[r["id"]] = len(r.get("content") or "")
    article_lengths = [float(v) for v in length.values()]
    once_per_file: list[float] = []
    delivered_per_file: list[float] = []
    over_net = 0
    for file_id, ids in pin_articles.items():
        once = sum(min(length.get(a, 0), PER_ARTICLE_CAP) for a in ids)
        once_per_file.append(once)
        sections = pin_sections.get(file_id) or set()
        delivered = sum(min(length.get(a, 0), PER_ARTICLE_CAP) for _, a in sections) if sections else once
        delivered_per_file.append(delivered)
        over_net += int(delivered > PER_DOCUMENT_NET)

    report = {
        "scan_rule_version": SCAN_RULE_VERSION,
        "files_with_text": total["files"],
        "contracts_skipped": contract["files"],
        "non_contract": {
            "files": total["files"] - contract["files"],
            "files_with_a_mention": total["files_with_a_mention"],
            "files_citing": total["files_citing"],
            "mentions": total["mentions"],
            "headings_own_clauses": total["headings"],
            "citing_mentions": total["citing_mentions"],
            "citations_mention_x_number": total["citations"],
            "line_start_but_not_heading": total["line_start_not_heading"],
            "plural_or_dual": total["plural"],
            "compound_numbers": total["compound"],
            "shares_next_mentions_law": total["inherited"],
            "executive_regulation_refs": total["exec_reg"],
        },
        "number_written_as": dict(form),
        "tie_to_a_law_per_citing_mention": dict(basis),
        "law_references": dict(law_kinds),
        "names_not_in_the_library": dict(sorted(unresolved_shape.items())),
        "distinct_named_laws_per_citing_file": spread(distinct_laws_per_file),
        "citation_outcome": dict(status),
        "pins_by_code_detail": dict(pin_detail),
        "citation_outcome_by_tie": {k: dict(v) for k, v in status_by_basis.items()},
        "files": {
            "with_at_least_one_pin_by_code": total["files_with_a_pin"],
            "decided_by_code_alone": total["files_decided_by_code_alone"],
            "needing_the_model_for_something": total["files_needing_the_model"],
            "citations_the_model_would_get": total["citations_for_the_model"],
            "with_a_pin_but_no_segment_map": total["files_with_a_pin_no_map"],
        },
        "pins_per_file": spread(pins_per_file),
        "distinct_pinned_articles_per_file": spread(pinned_articles_per_file),
        "pinned_article_length_chars": spread(article_lengths),
        "pinned_chars_per_file_each_article_once": spread(once_per_file),
        "pinned_chars_per_file_as_delivered_under_each_citing_segment": spread(delivered_per_file),
        "files_over_the_30000_net": over_net,
        "where_a_pin_lands": dict(seg_hits),
        "pin_by_segment_value": dict(seg_value),
        "by_doc_type": {k: dict(v) for k, v in sorted(by_type.items())},
        "contracts_reference_only": dict(contract),
        "scan_seconds": {"total": round(sum(scan_seconds), 1), "max_one_file": round(max(scan_seconds or [0]), 2)},
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))

    if args.names:
        rows = sorted(
            ((len(users), unresolved_count[key], " ".join(key)) for key, users in unresolved_names.items()
             if len(users) >= args.names),
            reverse=True,
        )
        print(f"\n# law names not in the library, written by {args.names}+ users: users / times / name (folded)")
        for users, times, name in rows[:150]:
            print(f"{users}\t{times}\t{name}")
        report["unresolved_names_distinct"] = len(unresolved_names)

    if out_dir:
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / "citation_scan_dryrun.json"
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"saved {path}", file=sys.stderr)


if __name__ == "__main__":
    main()
