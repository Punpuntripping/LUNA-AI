"""Generate ``shared/library/case_volume_registry.py`` from the ingestion pipeline logs.

ONE-SHOT GENERATOR, run by hand — not part of any request path. The judgments corpus
carries 9,860 rulings that were parsed out of bound PDF volumes (ديوان المظالم مجلدات,
مدونات لجان الزكاة والضريبة, مدونة السوابق التأمينية). ``cases.source`` records WHICH
volume and WHICH pages, but not where that volume lives on the publisher's site — that
mapping only ever existed in the scraper logs under ``agentic_for_ministry/``.

Those volumes are finished historical publications: ~100 keys that will not change. So the
mapping is baked into a Python module in this repo rather than a table — no migration, no
join on the judgment read path, no drift between a migration file and prod.

Inputs (all OUTSIDE this repo, hence the generator rather than a runtime read):
  cases/17486/scraping/download_log.json             pre-1438 مجلدات → bog.gov.sa PDF URL
  cases/17486/scraping/pdf_folder_references.ndjson  collection → landing page
  cases/17486/scripts/download_post1438.py           post-1438 URL template (reimplemented)
  cases/4004{5,6}/scraping/download_log.ndjson       sha1 → gstc/idc PDF URL
  cases/4004{5,6}/scraping/key_urls.ndjson           folder → landing page

Usage:  python scripts/build_case_volume_registry.py [--pipeline <dir>] [--check]
        --check verifies the emitted module still matches the logs (no write).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.parse
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

PIPELINE = Path(r"C:\Programming\agentic_for_ministry\cases")
OUT = (
    Path(__file__).resolve().parents[1] / "shared" / "library" / "case_volume_registry.py"
)

BOG = "https://www.bog.gov.sa"

# ---------------------------------------------------------------------------
# ديوان المظالم (17486)
# ---------------------------------------------------------------------------
# The disk group folder is NOT the publisher's collection: «الأحكام_التجارية_1428هـ»
# holds that year's إدارية, تجارية AND جزائية volumes alike. So the group maps to a
# SharePoint site path, and the collection's real Arabic title comes from
# pdf_folder_references.ndjson keyed on that path — never from the folder name.
BOG_GROUP_SITE_PATH = {
    "الأحكام_الإدارية_1402-1426هـ": "AA1402-1426",
    "الأحكام_الإدارية_1440هـ": "1440",
    "الأحكام_الإدارية_1441هـ": "1441",
    "الأحكام_الإدارية_1442هـ": "1442",
    "الأحكام_الإدارية_1443هـ": "1443",
    "الأحكام_الإدارية_1444هـ": "1444",
    "الأحكام_التجارية_1424-1427هـ": "1424-1427",
    "الأحكام_التجارية_1428هـ": "Blog1428",
    "الأحكام_التجارية_1429هـ": "1429",
    "الأحكام_التجارية_1430هـ": "1430",
    "الأحكام_التجارية_1431هـ": "1431",
    "الأحكام_الجزائية_1402-1427هـ": "1402-1427",
    "السوابق_القضائية_الإدارية_1402-1436هـ": "A1402-1436",
}

# post-1438 collections publish «Volume_N.pdf» under a "complete set" folder whose name
# differs by one word between years — 1444 uses «المجموعة الكاملة», the rest
# «المجموعة كاملة». Reimplemented from cases/17486/scripts/download_post1438.py so this
# generator has no import dependency on the pipeline.
BOG_POST1438_FOLDER = {
    "1440": "المجموعة كاملة (PDF)",
    "1441": "المجموعة كاملة (PDF)",
    "1442": "المجموعة كاملة (PDF)",
    "1443": "المجموعة كاملة (PDF)",
    "1444": "المجموعة الكاملة (PDF)",
}

# Volume rows for these two entities key on `source.source_volume` (a sha1), so the
# registry key is that sha1 alone — no group prefix. Landing page is per `source_folder`.
#
# ONLY the bound مدونات belong here. gstc's download_log also carries the 4,322 SINGLE
# decision PDFs (`kind='decision_pdf'`), and those need no registry at all — their row
# already stores its own `source.pdf_url`. Sweeping them in would inflate the module 45×
# with entries nothing reads.
SHA_ENTITIES = ("40045", "40046")
VOLUME_FOLDERS = frozenset(
    {"judicial_codes", "zakat_tax_code_1434_1439", "zakat_tax_code_2020_2021"}
)

# `key_urls.ndjson` titles the idc folder «المكتبة الإلكترونية» — that is the portal PAGE
# hosting the مدونة, not a collection the مدونة belongs to, and citing it would read as
# «مدونة السوابق القضائية التأمينية — المكتبة الإلكترونية». Blanked here so the citation
# falls back to the row's own `volume_title`, which is the real publication name. The
# landing URL is untouched: that page IS where the مدونة is published.
COLLECTION_BLANK = frozenset({"judicial_codes"})

# The app's chrome renders Latin digits only (see the latin-numerals product rule); the
# carve-out is corpus BODY text, not a title we compose into a metadata grid. bog titles
# them «لعام ١٤٤٠ هـ».
_ARABIC_INDIC = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")

# Bidi control marks. Four of the scraped volume filenames start with a stray U+200E that
# the parser stripped before writing `cases.source.volume` — so «‎المجلد الثاني.pdf» on disk
# has to key as «المجلد الثاني» or the lookup silently misses 27 rulings.
_BIDI = str.maketrans({c: None for c in "‎‏‪‫‬‭‮⁦⁧⁨⁩"})


def _norm(name: str) -> str:
    """Volume name as ``cases.source`` stores it — bidi marks out, whitespace collapsed."""
    return " ".join(name.translate(_BIDI).split())


def _latin(text: str) -> str:
    """Publication title with Arabic-Indic digits rewritten Latin."""
    return text.translate(_ARABIC_INDIC)


def _quote(path: str) -> str:
    return urllib.parse.quote(path, safe="/:@!$&'()*+,;=")


def _bog_site_path_of(url: str) -> str:
    m = re.search(r"/JudicialBlogs/([^/]+)/", urllib.parse.unquote(url))
    return m.group(1) if m else ""


def build_bog(pipeline: Path) -> dict[str, dict[str, str]]:
    """``{'<group>/<volume>': {collection, landing, pdf}}`` for the ديوان المظالم مجلدات."""
    root = pipeline / "17486" / "scraping"

    # collection title + landing page, keyed on SharePoint site path
    landing: dict[str, tuple[str, str]] = {}
    with (root / "pdf_folder_references.ndjson").open(encoding="utf-8") as fh:
        for line in fh:
            ref = json.loads(line)
            sp = _bog_site_path_of(ref["source_url"])
            if sp:
                landing[sp] = (_latin(ref["source_title"]), ref["source_url"])

    # pre-1438 direct PDF URLs, keyed on (site path, filename) — `file` alone is ambiguous
    # («المجلد الأول.pdf» exists in five collections).
    direct: dict[tuple[str, str], str] = {}
    for entry in json.loads((root / "download_log.json").read_text(encoding="utf-8")):
        if entry.get("status") != "ok":
            continue
        sp = _bog_site_path_of(entry["url"])
        if sp:
            direct[(sp, entry["file"])] = entry["url"]

    out: dict[str, dict[str, str]] = {}
    pdf_root = root / "pdf"
    for group in sorted(os.listdir(pdf_root)):
        if group not in BOG_GROUP_SITE_PATH:
            continue
        site_path = BOG_GROUP_SITE_PATH[group]
        collection, landing_url = landing.get(site_path, ("", ""))
        for fname in sorted(os.listdir(pdf_root / group)):
            if not fname.lower().endswith(".pdf"):
                continue
            volume = _norm(fname[:-4])
            pdf = direct.get((site_path, fname), "")
            if not pdf and volume.startswith("Volume_"):
                folder = BOG_POST1438_FOLDER.get(site_path, "")
                if folder:
                    pdf = BOG + _quote(
                        f"/ScientificContent/JudicialBlogs/{site_path}"
                        f"/Documents/{folder}/{fname}"
                    )
            out[f"{group}/{volume}"] = {
                "collection": collection,
                "landing": landing_url,
                "pdf": pdf,
            }
    return out


def build_sha(pipeline: Path, entity: str) -> dict[str, dict[str, str]]:
    """``{'<sha1>': {collection, landing, pdf}}`` for the gstc / idc bound مدونات."""
    root = pipeline / entity / "scraping"

    folder_landing: dict[str, tuple[str, str]] = {}
    with (root / "key_urls.ndjson").open(encoding="utf-8") as fh:
        for line in fh:
            d = json.loads(line)
            if d.get("folder"):
                folder_landing[d["folder"]] = (_latin(d.get("title", "")), d["url"])

    out: dict[str, dict[str, str]] = {}
    with (root / "download_log.ndjson").open(encoding="utf-8") as fh:
        for line in fh:
            d = json.loads(line)
            folder = d.get("source_folder", "")
            if d.get("status") != "success" or folder not in VOLUME_FOLDERS:
                continue
            sha = Path(d["pdf_path"]).stem
            collection, landing_url = folder_landing.get(folder, ("", ""))
            if folder in COLLECTION_BLANK:
                collection = ""
            out[sha] = {
                "collection": collection,
                "landing": landing_url,
                "pdf": d["pdf_url"],
            }
    return out


HEADER = '''"""Bound-volume provenance for the rulings parsed out of published PDF مجلدات.

GENERATED — do not hand-edit. Regenerate with::

    python scripts/build_case_volume_registry.py

``cases.source`` records which volume a ruling was parsed from and at which pages, but
never where that volume lives on the publisher's site. This module supplies the missing
half, recovered once from the ingestion scraper logs.

Two key shapes, because the two parsers keyed their volumes differently:

* **ديوان المظالم** — ``"<group>/<volume>"``, matching ``source['volume']`` verbatim
  (e.g. ``"الأحكام_الإدارية_1440هـ/Volume_2"``).
* **لجان الزكاة والضريبة · لجان التأمين** — the volume's sha1, matching
  ``source['source_volume']`` (e.g. ``"b7d5e9e7b27586bb"``).

Look them up through :func:`volume_source` rather than indexing directly — it takes a
``cases.source`` dict and knows which key shape that row uses.

``collection`` is the publisher's own title for the bound set — NOT the group folder name,
which is unreliable (``الأحكام_التجارية_1428هـ`` holds that year's إدارية, تجارية and
جزائية volumes alike). ``pdf`` is ``""`` for the volumes whose direct file URL the scrape
never resolved; ``landing`` is always present and is the honest fallback.

⚠ These URLs are the CROSSWALK, and D-CROSSWALK
(``.claude/plans/access_tiers_gating_DECISIONS.md``) puts them behind the unlock — one
volume PDF holds every ruling in the set, gated ones included. Only ``collection`` and the
page range may render on an anonymous page. Callers:
``shared.library.case_sources.judgment_provenance`` (free) and
``library_service.official_sources_for_item`` (metered).
"""

from __future__ import annotations

from typing import Any, Optional, TypedDict


class VolumeSource(TypedDict):
    """Where a bound volume was published. ``pdf`` may be ``""``; ``landing`` is set."""

    collection: str
    landing: str
    pdf: str


'''

FOOTER = '''

def volume_source(source: Any) -> Optional[VolumeSource]:
    """Resolve a ``cases.source`` dict to its published volume, or ``None``.

    Tries the sha1 key (``source_volume``) before the path key (``volume``): a row carries
    one or the other, never both, and the ديوان المظالم rows set ``source_volume`` to an
    int INDEX rather than a sha, so checking it first costs nothing and reading it as a
    registry key would be wrong. Non-dict input, an unknown volume, and a row with no
    volume at all all return ``None`` — a caller never has to pre-check the shape.
    """
    if not isinstance(source, dict):
        return None
    sha = source.get("source_volume")
    if isinstance(sha, str) and sha in CASE_VOLUME_SOURCES:
        return CASE_VOLUME_SOURCES[sha]
    volume = source.get("volume")
    if isinstance(volume, str) and volume in CASE_VOLUME_SOURCES:
        return CASE_VOLUME_SOURCES[volume]
    return None
'''


def live_volume_keys() -> dict[str, int]:
    """``{registry key: ruling count}`` for every volume-parsed ruling in the live corpus.

    Coverage is the whole point of this generator, and it cannot be proven from the
    scraper logs alone: the logs describe what was DOWNLOADED, the corpus describes what
    was INGESTED, and the two diverged (volumes scraped but never parsed, filenames
    normalised on the way in). So the check runs against prod.

    Requires ``SUPABASE_URL`` / ``SUPABASE_SERVICE_KEY``. Returns ``{}`` when the client
    can't be built, and the caller reports the check as skipped rather than passing it.
    """
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    try:
        from shared.db.client import get_supabase_client
    except Exception as e:  # noqa: BLE001
        print(f"   (coverage check skipped — {e})")
        return {}

    try:
        client = get_supabase_client()
    except Exception as e:  # noqa: BLE001
        print(f"   (coverage check skipped — {e})")
        return {}

    counts: dict[str, int] = {}
    page, size = 0, 1000
    while True:
        rows = (
            client.table("cases")
            .select("source")
            .range(page * size, page * size + size - 1)
            .execute()
        ).data or []
        if not rows:
            break
        for row in rows:
            src = row.get("source") or {}
            if not isinstance(src, dict) or src.get("kind") == "decision_pdf":
                continue
            sha = src.get("source_volume")
            key = sha if isinstance(sha, str) and len(sha) == 16 else src.get("volume")
            if isinstance(key, str) and key:
                counts[key] = counts.get(key, 0) + 1
        page += 1
    return counts


def render(registry: dict[str, dict[str, str]]) -> str:
    parts = [HEADER, "CASE_VOLUME_SOURCES: dict[str, VolumeSource] = {\n"]
    for key in sorted(registry):
        v = registry[key]
        parts.append(f"    {key!r}: {{\n")
        parts.append(f"        \"collection\": {v['collection']!r},\n")
        parts.append(f"        \"landing\": {v['landing']!r},\n")
        parts.append(f"        \"pdf\": {v['pdf']!r},\n")
        parts.append("    },\n")
    parts.append("}\n")
    parts.append(FOOTER)
    return "".join(parts)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pipeline", type=Path, default=PIPELINE)
    ap.add_argument("--check", action="store_true", help="verify only, do not write")
    args = ap.parse_args()

    if not args.pipeline.exists():
        print(f"pipeline dir not found: {args.pipeline}", file=sys.stderr)
        return 2

    registry: dict[str, dict[str, str]] = {}
    registry.update(build_bog(args.pipeline))
    for entity in SHA_ENTITIES:
        registry.update(build_sha(args.pipeline, entity))

    no_pdf = sorted(k for k, v in registry.items() if not v["pdf"])
    no_landing = sorted(k for k, v in registry.items() if not v["landing"])
    print(
        f"{len(registry)} volumes  ·  {len(no_pdf)} without a direct PDF URL  ·  "
        f"{len(no_landing)} without a landing page"
    )
    for k in no_pdf:
        print(f"   no pdf: {k}")
    for k in no_landing:
        print(f"   no landing: {k}")

    live = live_volume_keys()
    if live:
        total = sum(live.values())
        missing = {k: n for k, n in live.items() if k not in registry}
        unresolved = sum(n for k, n in live.items() if registry.get(k, {}).get("pdf") == "")
        covered = total - sum(missing.values())
        print(
            f"coverage: {covered}/{total} volume-parsed rulings resolve to a published "
            f"volume ({covered * 100 // total}%)  ·  {unresolved} land on the collection "
            f"page rather than a direct PDF"
        )
        for k, n in sorted(missing.items(), key=lambda kv: -kv[1]):
            print(f"   UNMAPPED ({n} rulings): {k}")
        unused = sorted(set(registry) - set(live))
        if unused:
            print(f"   {len(unused)} registry volumes have no rulings in the corpus")

    text = render(registry)
    if args.check:
        current = OUT.read_text(encoding="utf-8") if OUT.exists() else ""
        if current != text:
            print(
                "STALE — regenerate with python scripts/build_case_volume_registry.py",
                file=sys.stderr,
            )
            return 1
        print("registry is current")
        return 0

    OUT.write_text(text, encoding="utf-8")
    print(f"wrote {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
