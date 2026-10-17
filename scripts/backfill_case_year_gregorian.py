"""Backfill ``cases.year_gregorian`` for the rows with no ``date_gregorian``.

Migration 178 added ``year_gregorian`` + ``year_source`` so the aggregator can show
each ruling's age («سنة القضية: 1989»). The 19,112 rows with a real
``date_gregorian`` were filled in SQL; this script derives a year for the rest
(~12.7k, mostly the old 1402–1435هـ volumes) from the best source each row has.

Sources, best precision first (``year_source`` value in brackets):

1. a full Hijri date in ``date_hijri`` — exact Umm al-Qura conversion  [date_hijri]
2. a bare Hijri year in ``date_hijri`` («1434»)                        [date_hijri_year]
3. a Gregorian year in the decision number / folder («IR-2020-86»,
   ``source.year`` «2020م», ``40046_2020_…``)                          [gregorian_ref]
4. the Hijri year in ``source.printed.ids`` («1178/ا/1446ه») or the
   case-number year closing a Diwan ``case_ref`` («…_1901_1439»)       [decision_number]
5. the published volume's single year (``source.hijri_year``,
   ``source_year``, «الأحكام_التجارية_1429هـ», ``…_1434_v04…``)         [volume_year]
6. the appeal decision's date                                          [appeal_date]
7. the latest full Hijri date in the ruling text                       [content_date]
8. the midpoint of a multi-year volume («1402-1426هـ», «2020_2021»)   [volume_range]

Every Hijri candidate must also fit the volume it was published in: a ruling in
the «1440هـ» volume is from 1430–1441, so a ``date_hijri`` of «14/6/1343» there is
a date quoted from the text, not the ruling's, and is skipped (42 such rows).

A Hijri year alone spans two Gregorian years, so it is converted at mid-year
(1 رجب) — ±1 year, which is all the age signal needs. The script never touches
``date_gregorian``: that column is published on /judgments and must stay a real date.

Writes are one small UPDATE per row through the service client (``--apply``):
each updated row re-enters the HNSW embedding index, and one big UPDATE held the
table for minutes. ``--sql-dir`` instead emits ``UPDATE … FROM (VALUES …)`` chunk
files for the Supabase MCP. Only rows still NULL are touched, so reruns are safe.

Usage (repo root)::

    python scripts/backfill_case_year_gregorian.py              # dry run: report only
    python scripts/backfill_case_year_gregorian.py --apply
    python scripts/backfill_case_year_gregorian.py --sql-dir OUT --chunk 1500
"""

from __future__ import annotations

import argparse
import re
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from hijridate import Hijri  # noqa: E402

from shared.db.client import get_supabase_client  # noqa: E402

MAX_YEAR = 2026
MIN_YEAR = 1900
# hijridate's Umm al-Qura table starts at 1343هـ; older years use the mean ratio.
_UMM_AL_QURA_MIN = 1343

_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")
_TASHKEEL = re.compile(r"[ً-ْـ]")
_MONTHS = {
    "محرم": 1, "صفر": 2,
    "ربيع الاول": 3, "ربيع الثاني": 4, "ربيع الاخر": 4,
    "جمادي الاولي": 5, "جمادي الاول": 5,
    "جمادي الاخره": 6, "جمادي الاخر": 6, "جمادي الثانيه": 6, "جمادي الثاني": 6,
    "رجب": 7, "شعبان": 8, "رمضان": 9, "شوال": 10,
    "ذو القعده": 11, "ذي القعده": 11, "ذو الحجه": 12, "ذي الحجه": 12,
}
_MONTH_RE = re.compile(
    r"(\d{1,2})\s+(" + "|".join(sorted(_MONTHS, key=len, reverse=True)) + r")\s+(\d{4})"
)
_DMY = re.compile(r"(?<!\d)(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{4})(?!\d)")
_YMD = re.compile(r"(?<!\d)(\d{4})[/\-.](\d{1,2})[/\-.](\d{1,2})(?!\d)")
_HIJRI_YEAR = re.compile(r"(?<!\d)(1[34]\d\d)(?!\d)")
_GREG_DECISION = re.compile(r"(?:[A-Z]+|\d+)-((?:19|20)\d\d)-\d+")
_GREG_RANGE = re.compile(r"_((?:19|20)\d\d)_((?:19|20)\d\d)_")
# Only the year segment right after the entity («40046_2020_…»); a later
# «_1901_» is a case number («…Volume_4_1901_1439»).
_GREG_FOLDER = re.compile(r"^\d+_((?:19|20)\d\d)_")
_CASE_NO_YEAR = re.compile(r"_(1[34]\d\d)$")
_GREG_YEAR = re.compile(r"(?<!\d)((?:19|20)\d\d)(?!\d)")
_HIJRI_FOLDER = re.compile(r"_(1[34]\d\d)(?:ه|_)")  # «هـ» loses its tatweel in _norm
_HIJRI_RANGE = re.compile(r"(1[34]\d\d)-(1[34]\d\d)")

_SELECT = (
    "id, case_ref, date_hijri, appeal_date_hijri, appeal_date_gregorian, source, content"
)


def _norm(text: str) -> str:
    s = _TASHKEEL.sub("", str(text).translate(_DIGITS))
    s = re.sub("[أإآ]", "ا", s).replace("ى", "ي").replace("ة", "ه")
    return re.sub(r"\s+", " ", s)


def _ok(year: int) -> Optional[int]:
    return year if MIN_YEAR <= year <= MAX_YEAR else None


def hijri_to_year(h_year: int, month: int = 7, day: int = 1) -> Optional[int]:
    """Gregorian year of a Hijri date (mid-year when only the year is known)."""
    if not 1300 <= h_year <= 1500 or not 1 <= month <= 12 or not 1 <= day <= 30:
        return None
    if h_year < _UMM_AL_QURA_MIN:
        return _ok(round(h_year * 0.970229 + 621.5643))
    for d in (day, 29):  # a 30th in a 29-day month → the 29th, same year
        try:
            return _ok(Hijri(h_year, month, d).to_gregorian().year)
        except (ValueError, OverflowError):
            continue
    return None


def full_hijri_dates(text: str) -> list[tuple[int, int, int]]:
    """Every plausible full Hijri date in ``text`` as (year, month, day)."""
    s = _norm(text)
    out: list[tuple[int, int, int]] = []
    for d, m, y in _DMY.findall(s):
        d, m = int(d), int(m)
        if m > 12 >= d:  # «4/22/1431» is month/day
            d, m = m, d
        out.append((int(y), m, d))
    for y, m, d in _YMD.findall(s):
        out.append((int(y), int(m), int(d)))
    for d, name, y in _MONTH_RE.findall(s):
        out.append((int(y), _MONTHS[name], int(d)))
    return [
        (y, m, d) for y, m, d in out if 1300 <= y <= 1448 and 1 <= m <= 12 and 1 <= d <= 30
    ]


def _first_hijri_year(text: Any) -> Optional[int]:
    for y in _HIJRI_YEAR.findall(_norm(text or "")):
        return int(y)
    return None


def _decision_year(text: Any) -> Optional[int]:
    """The year in «1395/ا/1444ه» is the one marked «ه» — 1395 is the serial."""
    s = _norm(text or "")
    marked = re.search(r"(1[34]\d\d)\s*ه", s)
    return int(marked.group(1)) if marked else _first_hijri_year(s)


def _volume_bounds(group: str, src: dict) -> Optional[tuple[int, int]]:
    """Plausible Hijri years for a ruling published in this volume, if known."""
    g = _norm(group)
    if rng := _HIJRI_RANGE.search(g):
        return int(rng.group(1)) - 1, int(rng.group(2)) + 1
    m = _HIJRI_FOLDER.search(g)
    year = m.group(1) if m else str(src.get("source_year") or "")
    if year.isdigit() and 1300 <= int(year) <= 1500:
        return int(year) - 10, int(year) + 1
    return None


def derive_year(row: dict) -> tuple[Optional[int], str]:
    """(gregorian_year, year_source) for one ``cases`` row without date_gregorian."""
    src = row.get("source") or {}
    printed = src.get("printed") or {}
    ids = printed.get("ids") or {}
    case_ref = row.get("case_ref") or ""
    group = src.get("group") or ""
    rng = _HIJRI_RANGE.search(_norm(group))
    bounds = _volume_bounds(group, src)

    def fits(h_year: Optional[int]) -> bool:
        return h_year is not None and (not bounds or bounds[0] <= h_year <= bounds[1])

    # 1–2. date_hijri
    dh = row.get("date_hijri") or ""
    full = [d for d in full_hijri_dates(dh) if fits(d[0])]
    if full and (y := hijri_to_year(*full[0])):
        return y, "date_hijri"
    hy = _first_hijri_year(dh)
    if fits(hy) and (y := hijri_to_year(hy)):
        return y, "date_hijri_year"

    # 3. Gregorian year in a decision number or source folder
    for text, rx in (
        (case_ref, _GREG_DECISION),
        (src.get("title") or "", _GREG_DECISION),
        (str(src.get("year") or ""), _GREG_YEAR),
        (case_ref, _GREG_FOLDER),
    ):
        m = rx.search(text)
        if m and (y := _ok(int(m.group(1)))):
            return y, "gregorian_ref"

    # 4. Hijri year printed in the decision number
    m = _CASE_NO_YEAR.search(case_ref)
    for text in (ids.get("appeal_decision"), ids.get("first_instance_decision"),
                 m.group(1) if m else None):
        hy = _decision_year(text)
        if fits(hy) and (y := hijri_to_year(hy)):
            return y, "decision_number"

    # 5. the published volume's single year (a range's source_year is its START)
    candidates = [src.get("hijri_year"), src.get("case_year_admin")]
    if not rng:
        candidates.append(src.get("source_year"))
        m = _HIJRI_FOLDER.search(_norm(group))
        candidates.append(m.group(1) if m else None)
    m = _HIJRI_FOLDER.search(case_ref)
    candidates.append(m.group(1) if m else None)
    for c in candidates:
        hy = _first_hijri_year(c)
        if fits(hy) and (y := hijri_to_year(hy)):
            return y, "volume_year"

    # 6. appeal decision date
    ag = row.get("appeal_date_gregorian")
    if ag and (y := _ok(int(str(ag)[:4]))):
        return y, "appeal_date"
    adh = row.get("appeal_date_hijri") or ""
    full = full_hijri_dates(adh)
    if full and (y := hijri_to_year(*full[0])):
        return y, "appeal_date"
    hy = _first_hijri_year(adh)
    if hy and (y := hijri_to_year(hy)):
        return y, "appeal_date"

    # 7. latest full Hijri date in the ruling text (inside the volume range, if any)
    dates = [d for d in full_hijri_dates(row.get("content") or "") if fits(d[0])]
    if dates and (y := hijri_to_year(*max(dates))):
        return y, "content_date"

    # 8. midpoint of a multi-year volume
    if rng:
        mid = (int(rng.group(1)) + int(rng.group(2))) // 2
        if y := hijri_to_year(mid):
            return y, "volume_range"
    if m := _GREG_RANGE.search(case_ref):  # «zakat_tax_code_2020_2021»
        if y := _ok((int(m.group(1)) + int(m.group(2))) // 2):
            return y, "volume_range"

    return None, "none"


def fetch_rows(client, page: int = 200) -> list[dict]:
    rows: list[dict] = []
    start = 0
    while True:
        res = (
            client.table("cases")
            .select(_SELECT)
            .is_("year_gregorian", "null")
            .order("id")
            .range(start, start + page - 1)
            .execute()
        )
        rows.extend(res.data or [])
        if len(res.data or []) < page:
            return rows
        start += page


def write_sql(results: list[tuple[str, int, str]], out_dir: Path, chunk: int) -> int:
    out_dir.mkdir(parents=True, exist_ok=True)
    n = 0
    for i in range(0, len(results), chunk):
        values = ",\n".join(f"('{cid}',{y},'{s}')" for cid, y, s in results[i : i + chunk])
        sql = (
            "UPDATE public.cases c SET year_gregorian = v.y::smallint, year_source = v.s\n"
            f"FROM (VALUES\n{values}\n) AS v(id, y, s)\n"
            "WHERE c.id = v.id::uuid AND c.year_gregorian IS NULL;\n"
        )
        n += 1
        (out_dir / f"chunk_{n:03d}.sql").write_text(sql, encoding="utf-8")
    return n


def apply(client, results: list[tuple[str, int, str]], workers: int = 8) -> int:
    def one(item: tuple[str, int, str]) -> int:
        cid, year, source = item
        res = (
            client.table("cases")
            .update({"year_gregorian": year, "year_source": source})
            .eq("id", cid)
            .is_("year_gregorian", "null")
            .execute()
        )
        return len(res.data or [])

    with ThreadPoolExecutor(max_workers=workers) as pool:
        return sum(pool.map(one, results))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--sql-dir", type=Path, help="write UPDATE chunk files here")
    ap.add_argument("--chunk", type=int, default=1500)
    ap.add_argument("--apply", action="store_true", help="write the years to the DB")
    args = ap.parse_args()

    client = get_supabase_client()
    rows = fetch_rows(client)
    results: list[tuple[str, int, str]] = []
    by_source: Counter[str] = Counter()
    unresolved: list[dict] = []
    for row in rows:
        year, source = derive_year(row)
        by_source[source] += 1
        if year is None:
            unresolved.append(row)
        else:
            results.append((row["id"], year, source))

    print(f"rows without year_gregorian: {len(rows)}")
    for source, count in by_source.most_common():
        print(f"  {source:<16} {count}")
    years = Counter(y for _, y, _ in results)
    if years:
        print(f"year range: {min(years)}–{max(years)}")
    for row in unresolved[:15]:
        print(f"  UNRESOLVED {row['case_ref']}  date_hijri={row.get('date_hijri')!r}")

    if args.sql_dir:
        n = write_sql(results, args.sql_dir, args.chunk)
        print(f"wrote {n} chunk files to {args.sql_dir}")
    if args.apply:
        print(f"updated {apply(client, results)} rows")


if __name__ == "__main__":
    main()
