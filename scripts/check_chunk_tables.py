"""Post-ingest integrity check for the chunk-table display layer.

RUN THIS AFTER EVERY CORPUS RE-INGEST. It is the only thing that notices when
the two halves of the layer drift apart, and every failure it catches is
SILENT in the product — the reader gets a نظام with a table quietly missing
from the middle of it, and nothing anywhere says so.

    python scripts/check_chunk_tables.py            # full corpus, ~90s
    python scripts/check_chunk_tables.py --quick    # 1,000 chunks, ~10s

Exit 0 = clean, 1 = something drifted, 2 = could not run.

--------------------------------------------------------------------------
WHY THIS EXISTS

`chunks_v2.content_display` and `chunk_tables_v2` are written by a pipeline in
a DIFFERENT repo (`agentic_for_ministry`: resolve_chunk_tables.py ->
build_table_views.py -> ingest_chunk_tables.py). LUNA only reads them. Measured
2026-08-26: the chunk corpus has been loaded on 4 separate days between
2026-05-13 and 2026-08-03; the table layer once, on 2026-08-24. So the two are
reloaded on independent schedules that this repo does not control.

Three ways that goes wrong, all of which lose law without an error:

C1  A token in `content_display` with no `chunk_tables_v2` row. The renderer
    does what it is designed to do — drops the unresolvable token and emits
    NOTHING (rule 2). A partial ingest, or the FK skip their ingester writes to
    `chunk_tables_orphans.txt`, produces exactly this.

C2  Table markup whose text the sanitizer eats. The allowlist drops any element
    it does not recognise ALONG WITH ITS CONTENT — correct for `<img>`,
    catastrophic for a layout wrapper. This already cost **177,060 characters
    across 325 tables** on this corpus (`<ul>` alone was 149,116) and 24 tables
    rendered as a visible blank grid. A future OCR run producing markup the
    allowlist has not seen does it again. Any tag reported here needs adding to
    `_UNWRAPPED_ELEMENTS` in `shared/library/chunk_tables.py`.

C3  Markup escaping the allowlist into `dangerouslySetInnerHTML`. The corpus is
    clean today (0 script / iframe / javascript: / event handlers across all
    24,511 rows) which is exactly why this is checked at TAG level rather than
    by grepping the string: the guarantee has to come from the re-serializer,
    not from a snapshot of the input.

C4  `table_md` missing. It is the gate weight's floor, the copy-button text and
    the text-only fallback; the corpus minimum is 27 chars.
"""
from __future__ import annotations

import os
import re
import sys
from collections import Counter
from html.parser import HTMLParser

sys.stdout.reconfigure(encoding="utf-8")  # Arabic refs die on cp1252 otherwise

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.library.chunk_tables import (  # noqa: E402
    _ALLOWED_ATTRS,
    _ALLOWED_ELEMENTS,
    TABLE_PLACEHOLDER,
    sanitize_table_html,
    visible_text,
)

PAGE = 1000
QUICK_CAP = 1000


def _client():
    try:
        from dotenv import load_dotenv
        from supabase import create_client
    except ImportError as e:  # pragma: no cover
        sys.exit(f"[2] missing dependency: {e}")
    load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))
    url, key = os.environ.get("SUPABASE_URL"), os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    if not (url and key):
        sys.exit("[2] SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY not set")
    return create_client(url, key)


def _page_all(sb, table, columns, order, cap=None, **filters):
    """Read a whole table, paged. PostgREST caps at 1000 rows per request.

    ⚠ Asserts the row count against an independent count='exact'. Without that
    a short read looks exactly like a clean corpus — the check would pass by
    examining a tenth of it.
    """
    q = sb.table(table).select(columns, count="exact").limit(1)
    for col, val in filters.items():
        q = q.not_.is_(col, val)
    total = q.execute().count or 0
    want = min(total, cap) if cap else total
    rows = []
    for lo in range(0, want, PAGE):
        b = sb.table(table).select(columns).order(order)
        for col, val in filters.items():
            b = b.not_.is_(col, val)
        rows += b.range(lo, min(lo + PAGE, want) - 1).execute().data
    if cap is None and len(rows) != total:
        sys.exit(f"[2] short read on {table}: {len(rows)} of {total} — pagination is broken")
    return rows, total


class _TagAudit(HTMLParser):
    """Collect element/attribute names actually EMITTED as markup.

    Deliberately parses rather than greps: sanitized output legitimately
    contains escaped text like `&lt;img src=...` when the source markup was
    malformed, and that is inert. A regex cannot tell those apart; a parser can.
    """

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.bad_tags: set[str] = set()
        self.bad_attrs: set[str] = set()

    def handle_starttag(self, tag, attrs):
        if tag.lower() not in _ALLOWED_ELEMENTS:
            self.bad_tags.add(tag.lower())
        for name, _ in attrs:
            if name.lower() not in _ALLOWED_ATTRS:
                self.bad_attrs.add(name.lower())

    handle_startendtag = handle_starttag


def main() -> int:
    quick = "--quick" in sys.argv
    sb = _client()

    cap = QUICK_CAP if quick else None
    chunks, n_chunks = _page_all(
        sb, "chunks_v2", "id, chunk_ref, content_display", "id",
        cap=cap, content_display="null",
    )
    ids = {c["id"] for c in chunks}
    tables, n_tables = _page_all(
        sb, "chunk_tables_v2", "table_ref, chunk_id, table_html, table_md", "table_ref",
        cap=None if not quick else None,
    )
    if quick:
        tables = [t for t in tables if t["chunk_id"] in ids]

    print(f"chunk-table integrity — {len(chunks)} chunks / {len(tables)} tables"
          f"{'  (QUICK sample)' if quick else f'  (full corpus: {n_chunks} / {n_tables})'}\n")

    by_chunk: dict[str, set[str]] = {}
    for t in tables:
        by_chunk.setdefault(t["chunk_id"], set()).add(t["table_ref"])

    # --- C1: every token resolves, every row is reachable -------------------
    orphan_tokens: list[tuple[str, str]] = []
    unreached_rows: list[str] = []
    for c in chunks:
        have = by_chunk.get(c["id"], set())
        toks = {m.group(1) for m in TABLE_PLACEHOLDER.finditer(c["content_display"] or "")}
        for tok in toks - have:
            orphan_tokens.append((c["chunk_ref"], tok))
        for ref in have - toks:
            unreached_rows.append(ref)

    # --- C2/C3/C4: the sanitizer, per table ---------------------------------
    eaten_detail: list[tuple[str, int, float]] = []
    blank_out: list[str] = []
    bad_tags: list[tuple[str, list[str]]] = []
    bad_attrs: list[tuple[str, list[str]]] = []
    thin_md: list[str] = []
    eaten_total = 0
    culprits: Counter[str] = Counter()

    for t in tables:
        raw, md = t["table_html"] or "", t["table_md"] or ""
        out = sanitize_table_html(raw)
        v_in, v_out = visible_text(raw), visible_text(out)
        if not out:
            if v_in:
                blank_out.append(t["table_ref"])  # had text, produced nothing
            continue
        lost = len(v_in) - len(v_out)
        if lost > 0:
            eaten_detail.append((t["table_ref"], lost, lost / max(len(v_in), 1)))
            eaten_total += lost
            for tag in set(re.findall(r"<\s*([A-Za-z][A-Za-z0-9]*)", raw)):
                if tag.lower() not in _ALLOWED_ELEMENTS:
                    culprits[tag.lower()] += 1
        a = _TagAudit()
        a.feed(out)
        a.close()
        if a.bad_tags:
            bad_tags.append((t["table_ref"], sorted(a.bad_tags)))
        if a.bad_attrs:
            bad_attrs.append((t["table_ref"], sorted(a.bad_attrs)))
        if len(md.strip()) < 20:
            thin_md.append(t["table_ref"])

    def report(label, items, detail=None, fatal=True):
        ok = not items
        print(f"  [{'PASS' if ok else 'FAIL'}] {label:<46} {len(items)}")
        if not ok:
            for x in items[:6]:
                print(f"           {x}")
            if len(items) > 6:
                print(f"           ... and {len(items) - 6} more")
            if detail:
                print(f"           {detail}")
        return ok if fatal else True

    ok = True
    ok &= report("C1  tokens with no table row", orphan_tokens,
                 "-> the reader loses this table silently. Re-run the ingest.")
    ok &= report("C1  table rows no token points at", unreached_rows,
                 "-> content_display is stale relative to chunk_tables_v2.")
    ok &= report("C3  non-allowlisted TAGS emitted", bad_tags,
                 "-> markup is escaping into dangerouslySetInnerHTML. STOP.")
    ok &= report("C3  non-allowlisted ATTRS emitted", bad_attrs,
                 "-> same. STOP.")
    ok &= report("C2  tables sanitized to nothing", blank_out,
                 "-> had visible text, produced none. Allowlist gap.")

    # C2 text loss is a threshold, not a binary — and the threshold is MEASURED,
    # not guessed. Baseline on the clean corpus (2026-08-26, all 24,511 rows):
    # 73 tables lose any text at all, the worst by **30 chars** and by **3.41%**
    # of their visible text. That is alt-text on a dropped `<img>` and similar
    # noise. So >30 absolute, or >5% relative on anything non-trivial, is
    # outside anything this corpus does today.
    #
    # BOTH conditions are needed. Absolute alone misses a small table gutted
    # entirely (a first draft of this check used >40 and let a 39-char total
    # loss through); relative alone misses a large table quietly losing a
    # column. An unlisted `<ul>` averaged 684 chars per table when this last
    # happened — it would trip either one.
    heavy = [(r, n) for r, n, ratio in eaten_detail if n > 30 or (n >= 5 and ratio > 0.05)]
    print(f"  [{'PASS' if not heavy else 'FAIL'}] "
          f"{'C2  text loss beyond the measured floor':<46} {len(heavy)}")
    if heavy:
        ok = False
        for r, n in sorted(heavy, key=lambda x: -x[1])[:6]:
            print(f"           -{n:>6}  {r}")
        if culprits:
            print(f"           likely culprits: {dict(culprits.most_common(6))}")
        print("           -> add these to _UNWRAPPED_ELEMENTS in shared/library/chunk_tables.py")
    print(f"  [INFO] {'C2  total chars lost (all tables)':<46} {eaten_total}")
    print(f"  [INFO] {'C4  table_md under 20 chars':<46} {len(thin_md)}")

    print()
    if ok:
        print("  CLEAN — the display layer matches the corpus.")
        return 0
    print("  DRIFT — see above. Every failure here is invisible in the product.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
