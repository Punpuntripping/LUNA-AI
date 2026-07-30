"""Rebuild the ``seo_articles`` per-مادة index for the SEO public library (Phase 3).

Derives one row per (regulation, article number) for the highest-value template
of the library — ``/regulations/{slug}/{article-slug}`` («المادة {N} من {نظام}»,
~50k long-tail pages). Every row carries the isolated per-مادة text and points at
the owning ``chunks_v2`` chunk (used as the fallback body + context title).

PRIMARY SOURCE — ``articles_v2`` (rewrite 2026-07-23):
  ``articles_v2`` is a pipeline-owned VIEW (READ ONLY) with one ROW PER مادة already
  sliced by the ingest pipeline — the clean, ready per-مادة corpus. Columns:
  ``id, article_ref, chunk_parent_id, regulation_id, article_number (TEXT),
  content, ingested_at`` (live 2026-07-23: 51,791 rows / 1,806 regulations).
  Per row:
    * ``article_number`` → ``article_no`` when it is a plain integer string.
      NON-numeric refs (compound «36-3», «2-34», …; 487 live) are SKIPPED + counted.
    * ``article_label`` = «المادة {N}», ``slug`` = «المادة-{N}» (WESTERN digits —
      matches migration 097's examples, the plan's SEO-title pattern, URL sanity,
      and dual «المادة 80»/«المادة ٨٠» search intent).
    * ``chunk_id`` = ``chunk_parent_id``. When that is NULL (350 live rows across 5
      regs) it falls back to the owns-based owner chunk (lowest-position ``chunks_v2``
      whose ``owns.MADDA`` lists the number). ``seo_articles.chunk_id`` is NOT NULL,
      so a row with neither a parent nor an owns owner is SKIPPED + counted.
    * ``article_text`` = ``content`` when it has real body (>= ``MIN_ARTICLE_TEXT_CHARS``
      = 20 chars), ``extraction_status='extracted'``. A tiny/placeholder ``content``
      (87 live, e.g. «(مادة ١١)») is treated as MISSING → ``article_text=NULL``,
      ``extraction_status='chunk_fallback'`` (the reader renders the owning chunk).
    * «(ملغاة)» repeal markers inside ``content`` are KEPT verbatim (honest display
      of a repealed مادة — 202 live rows).

FALLBACK SOURCE — chunk extraction (regs ABSENT from ``articles_v2`` entirely):
  For a regulation with NO ``articles_v2`` rows, the legacy path slices the مواد
  straight out of ``chunks_v2.content`` using ``owns.MADDA`` + the Arabic
  ordinal-word header parser below. (Live 2026-07-23 every owns-MADDA regulation is
  already present in ``articles_v2``, so this path is future-proofing — but it is
  fully retained and exercised whenever a chunk-only regulation appears.)

WHAT IT WRITES (the ONLY table this script mutates):
  ``public.seo_articles`` (migration 097) — DERIVED + fully rebuildable. Per-
  regulation DELETE-then-INSERT: on ``--apply`` every target regulation's existing
  rows are deleted and fresh rows inserted (the table holds no source-of-truth
  data). A regulation that now yields ZERO rows still has its stale rows purged on
  ``--apply`` (clean rebuild). ``created_at``/``updated_at`` are set at insert time
  (097 adds no UPDATE trigger on purpose).

  ``extraction_status`` CHECK allows ('extracted','chunk_fallback') only —
  'extracted' for an articles_v2 row with real body OR a good chunk slice;
  'chunk_fallback' when the body is missing/tiny and the whole owning chunk stands
  in.

ORDINAL PARSER (fallback path only) — designed from live نظام العمل content:
  مادة headers in chunk bodies are markdown headings in Arabic ORDINAL-WORD form —
  «### المادة الثمانون:», «المادة الحادية والثمانون بعد المائة:» — occasionally
  carrying a trailing Arabic-Indic footnote marker or a «^{…}» superscript. The
  ORDINAL WORD is authoritative (it matches ``owns.MADDA``); trailing digits are
  noise. Digit-form headers («المادة 80» / «المادة ٨٠») are a fallback.
  ``arabic_ordinal_to_int`` converts 1–400.

CLI (``--dry-run`` is the DEFAULT — writes NOTHING):
  python scripts/build_seo_article_index.py                 # dry-run, 20 sample regs (نظام العمل pinned) + stats
  python scripts/build_seo_article_index.py --sample 50     # dry-run over 50 sample regs
  python scripts/build_seo_article_index.py --reg <uuid>    # limit scope to one regulation
  python scripts/build_seo_article_index.py --apply         # WRITE all regs (batched, delete+rebuild)
  python scripts/build_seo_article_index.py --apply --reg <uuid>   # rebuild one regulation

Env: SUPABASE_URL / SUPABASE_SERVICE_KEY (via shared.config / shared.db.client).
"""
from __future__ import annotations

import argparse
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# Make the repo root importable when run directly.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Windows consoles default to cp1252, which can't encode Arabic — force UTF-8.
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

try:
    from dotenv import load_dotenv

    load_dotenv()
except Exception:  # noqa: BLE001
    pass

from shared.db.client import get_supabase_client

# نظام العمل — pinned first in the default dry-run sample (232 مواد, its
# ordinal-word headers + amendments are the toughest case, so it anchors QA).
LABOR_LAW_REG = "da51024f-a713-48e7-af87-b6a541f055e4"

# PostgREST caps a single response at 1000 rows by default.
_READ_PAGE = 1000
# Insert batch size on --apply.
_WRITE_BATCH = 500
# Default number of regulations a dry-run walks when no --reg is given.
_DEFAULT_SAMPLE = 20
# A sliced مادة segment (fallback path) shorter than this is an extraction failure.
MIN_SEGMENT_CHARS = 30
# An articles_v2 `content` shorter than this is treated as missing → chunk_fallback.
MIN_ARTICLE_TEXT_CHARS = 20
# Highest article number the ordinal converter accepts (plan spec: 1–400).
_MAX_ARTICLE_NO = 400

# --------------------------------------------------------------------------
# Arabic normalization + ordinal-word → int conversion (FALLBACK PATH ONLY).
# --------------------------------------------------------------------------
# Tashkeel U+064B..U+0652 + tatweel U+0640 — stripped before matching.
_TASHKEEL_RE = re.compile("[" + "".join(chr(c) for c in range(0x064B, 0x0653)) + "ـ]")
# Arabic-Indic digits → ASCII, for digit-form headers + trailing markers.
_AR_INDIC = {ord(c): str(i) for i, c in enumerate("٠١٢٣٤٥٦٧٨٩")}


def _norm_ar(s: str) -> str:
    """Fold an Arabic string for dictionary matching: drop tashkeel/tatweel,
    unify alef variants → ا, ى → ي, ة → ه."""
    s = _TASHKEEL_RE.sub("", s)
    s = s.translate(str.maketrans("أإآٱ", "اااا"))
    return s.replace("ى", "ي").replace("ة", "ه")


# Ordinal units 1–10 (feminine — مادة is feminine — plus masculine forms), folded.
_UNITS = {
    "اولي": 1, "اول": 1, "حاديه": 1, "حادي": 1, "واحده": 1, "واحد": 1,
    "ثانيه": 2, "ثاني": 2,
    "ثالثه": 3, "ثالث": 3,
    "رابعه": 4, "رابع": 4,
    "خامسه": 5, "خامس": 5,
    "سادسه": 6, "سادس": 6,
    "سابعه": 7, "سابع": 7,
    "ثامنه": 8, "ثامن": 8,
    "تاسعه": 9, "تاسع": 9,
    "عاشره": 10, "عاشر": 10,
}
# Teens suffix: a unit followed by one of these = unit + 10 («حاديه عشره» = 11).
_TEENS = {"عشره", "عشر"}
# Tens 20–90, folded (both ون / ين spellings + the corpus «تسعيون» misspelling).
_TENS = {
    "عشرون": 20, "عشرين": 20,
    "ثلاثون": 30, "ثلاثين": 30,
    "اربعون": 40, "اربعين": 40,
    "خمسون": 50, "خمسين": 50,
    "ستون": 60, "ستين": 60,
    "سبعون": 70, "سبعين": 70,
    "ثمانون": 80, "ثمانين": 80,
    "تسعون": 90, "تسعين": 90, "تسعيون": 90,
}
# Hundreds words (standalone «المائة» = 100, or the base after «بعد …»), folded,
# incl. the corpus «الماتتين» misspelling of المائتين (200).
_HUNDREDS = {
    "مايه": 100, "مائه": 100, "مئه": 100,
    "مائتان": 200, "مائتين": 200, "مئتان": 200, "مئتين": 200,
    "ماتتين": 200, "ماتتان": 200, "ماتين": 200,
    "ثلاثمايه": 300, "ثلاثمائه": 300, "ثلاثمئه": 300,
    "اربعمايه": 400, "اربعمائه": 400, "اربعمئه": 400,
}


def arabic_ordinal_to_int(phrase: str) -> Optional[int]:
    """Convert an Arabic ordinal-word phrase to an int in 1..400, or ``None``.

    Handles units (feminine + masculine), teens («… عشرة»), tens («… ون/ين»),
    standalone hundreds («المائة»/«المائتين»/«الثلاثمائة»/«الأربعمائة»), and the
    «[units] و[tens] بعد ال[hundreds]» composition (e.g. «الحادية والثمانون بعد
    المائة» = 1+80+100 = 181). Leading «ال»/«و» prefixes, «مكرر», superscript
    footnote markers «^{…}», and stray digits are stripped before parsing.
    Unknown tokens are ignored. Returns ``None`` when nothing recognisable is
    found or the total falls outside 1..400.
    """
    s = _norm_ar(phrase)
    s = re.sub(r"\^?\{[^}]*\}", " ", s)   # superscript footnote markers ^{..}
    s = s.replace("مكرر", " ")            # bis marker (shares its base number)
    s = re.sub(r"[^؀-ۿ\s]", " ", s)  # keep Arabic letters only
    words = [w for w in s.split() if w]

    # Strip leading و / ال (the conjunction and article attach as prefixes).
    toks: list[str] = []
    for w in words:
        if w.startswith("وال"):
            w = w[3:]
        elif w.startswith("ال"):
            w = w[2:]
        elif w.startswith("و") and len(w) > 1:
            w = w[1:]
        if w:
            toks.append(w)
    if not toks:
        return None

    # «بعد ال[hundreds]» → hundreds base; everything before «بعد» is the 1..99 part.
    hundreds = 0
    if "بعد" in toks:
        idx = toks.index("بعد")
        for w in toks[idx + 1:]:
            if w in _HUNDREDS:
                hundreds += _HUNDREDS[w]
        toks = toks[:idx]

    sub = 0
    pending = 0  # a held unit awaiting a teens suffix (or end-of-phrase).
    matched = bool(hundreds)
    for w in toks:
        if w in _UNITS:
            if pending:
                sub += pending
            pending = _UNITS[w]
            matched = True
        elif w in _TEENS:
            sub += 10 + pending
            pending = 0
            matched = True
        elif w in _TENS:
            sub += _TENS[w]
            matched = True
        elif w in _HUNDREDS:  # standalone hundreds without «بعد»
            sub += _HUNDREDS[w]
            matched = True
        # unknown token → ignore
    sub += pending

    total = hundreds + sub
    if not matched or not (0 < total <= _MAX_ARTICLE_NO):
        return None
    return total


# مادة header: a line-anchored «المادة <label>» heading. Real headers across the
# corpus are markdown headings — «### المادة الثمانون:», «##### المادة الأولى»
# (leading space, NO trailing colon), «# المادة التاسعة عشرة:» — so a hash prefix
# is allowed with the colon OPTIONAL. A hashLESS line must carry a trailing colon
# to count (so a prose sentence that merely starts a line with «المادة الأولى …»
# is NOT mistaken for a header). group(1)=hashes, group(2)=label (ordinal words or
# digits ± a trailing footnote marker), group(3)=terminator (':'/'：' or '' at EOL).
_HEADER_RE = re.compile(
    r"(?m)^[ \t]*(#{1,6})?[ \t]*المادة[ \t]+([^\n:：]{0,70}?)[ \t]*([:：]|$)"
)


def _is_header_match(m: "re.Match") -> bool:
    """A regex hit is a real مادة header when it has a markdown hash prefix OR a
    colon terminator — never a bare hashless/colonless prose line."""
    return bool(m.group(1)) or m.group(3) in (":", "：")


def parse_article_no(label: str) -> Optional[int]:
    """Resolve a header's «المادة …» label to an article number.

    Ordinal-word form wins (it is authoritative in this corpus — trailing digits
    are edit markers). If no ordinal words parse, fall back to a leading digit
    run («المادة 80» / «المادة ٨٠»). Returns ``None`` when neither yields a value
    in 1..400.
    """
    ordv = arabic_ordinal_to_int(label)
    if ordv is not None:
        return ordv
    dm = re.search(r"\d+", label.translate(_AR_INDIC))
    if dm:
        v = int(dm.group())
        if 0 < v <= _MAX_ARTICLE_NO:
            return v
    return None


def extract_chunk_articles(content: str, owns_madda: set[int]) -> dict[int, str]:
    """Slice a chunk's ``content`` into per-مادة segments (fallback path).

    Returns ``{article_no: segment_text}`` ONLY for article numbers in
    ``owns_madda`` that have a usable (>= ``MIN_SEGMENT_CHARS``) segment. ALL
    مادة headers act as segment boundaries (even those parsing to a non-owned or
    unparseable number); a segment runs from its header line to the next header.
    On a duplicate article number the longest segment wins.
    """
    matches = [m for m in _HEADER_RE.finditer(content or "") if _is_header_match(m)]
    if not matches:
        return {}
    out: dict[int, str] = {}
    for i, m in enumerate(matches):
        n = parse_article_no(m.group(2))
        if n is None or n not in owns_madda:
            continue
        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(content)
        seg = (content[start:end] or "").strip()
        if len(seg) < MIN_SEGMENT_CHARS:
            continue
        if n not in out or len(seg) > len(out[n]):
            out[n] = seg
    return out


# --------------------------------------------------------------------------
# Row building — normalized stats shape shared by both source paths:
#   {extracted, chunk_fallback, skipped_nonnumeric, skipped_no_chunk,
#    tiny_content, duplicate}
# --------------------------------------------------------------------------


def _empty_stats() -> dict:
    return {
        "extracted": 0,
        "chunk_fallback": 0,
        "skipped_nonnumeric": 0,
        "skipped_no_chunk": 0,
        "tiny_content": 0,
        "duplicate": 0,
    }


def _owns_owner_map(chunks: list[dict]) -> dict[int, str]:
    """Map ``article_no -> owning chunk id`` from ``chunks_v2.owns.MADDA``.

    ``chunks`` are position-ordered (``_load_regulation_chunks``), so the FIRST
    chunk seen listing an article number wins (= lowest position). Used to recover
    ``chunk_id`` for an articles_v2 row whose ``chunk_parent_id`` is NULL.
    """
    owner: dict[int, str] = {}
    for ch in chunks:
        for x in (ch.get("owns") or {}).get("MADDA") or []:
            if isinstance(x, (int, float)) or (isinstance(x, str) and str(x).isdigit()):
                no = int(x)
                if no not in owner:
                    owner[no] = ch.get("id")
    return owner


def build_rows_from_articles_v2(
    reg_id: str, av2_rows: list[dict], owner_map: dict[int, str], now_iso: str
) -> tuple[list[dict], dict]:
    """Turn one regulation's ``articles_v2`` rows into ``seo_articles`` rows.

    ``owner_map`` (may be empty) recovers ``chunk_id`` when ``chunk_parent_id`` is
    NULL. Rules per row: non-numeric ``article_number`` → skipped; no resolvable
    ``chunk_id`` → skipped (chunk_id is NOT NULL); ``content`` >= 20 chars →
    ``extracted`` (article_text = content, «(ملغاة)» kept); shorter → ``chunk_
    fallback`` (article_text NULL). Duplicate ``article_no`` keeps the longer body.
    Returns ``(rows_sorted_by_no, stats)``.
    """
    stats = _empty_stats()
    best: dict[int, tuple[int, dict]] = {}  # article_no -> (content_len, row)

    for r in av2_rows:
        an = (r.get("article_number") or "").strip()
        if not an.isdigit():
            stats["skipped_nonnumeric"] += 1
            continue
        no = int(an)
        if no <= 0:
            stats["skipped_nonnumeric"] += 1
            continue

        chunk_id = r.get("chunk_parent_id") or owner_map.get(no)
        if not chunk_id:
            stats["skipped_no_chunk"] += 1
            continue

        content = r.get("content") or ""
        if len(content.strip()) >= MIN_ARTICLE_TEXT_CHARS:
            article_text: Optional[str] = content
            status = "extracted"
        else:
            article_text = None
            status = "chunk_fallback"

        row = {
            "regulation_id": reg_id,
            "article_no": no,
            "article_label": f"المادة {no}",   # western digits (see docstring)
            "slug": f"المادة-{no}",             # western digits for URL sanity
            "chunk_id": chunk_id,
            "article_text": article_text,
            "extraction_status": status,
            "updated_at": now_iso,
            "created_at": now_iso,
        }
        clen = len(content)
        if no in best:
            stats["duplicate"] += 1
            if clen <= best[no][0]:
                continue  # keep the existing (longer-or-equal) body
        best[no] = (clen, row)

    rows: list[dict] = []
    for no in sorted(best):
        row = best[no][1]
        if row["extraction_status"] == "extracted":
            stats["extracted"] += 1
        else:
            stats["chunk_fallback"] += 1
            stats["tiny_content"] += 1
        rows.append(row)
    return rows, stats


def build_regulation_rows_from_chunks(
    chunks: list[dict], now_iso: str
) -> tuple[list[dict], dict]:
    """FALLBACK: slice one regulation's مواد out of ``chunks_v2`` (legacy path).

    Used ONLY for a regulation with no ``articles_v2`` rows. ``chunks`` = list of
    {id, regulation_id, position, title, content, owns} ORDERED BY (position, id).
    Produces exactly one row per distinct owned article number.

    One-chunk-per-مادة is resolved in TWO passes because ``owns.MADDA`` OVER-CLAIMS
    on some regulations (a first chunk lists 1..15 but physically carries only 1–2):
      * Pass 1 (extraction): the LOWEST-position chunk that actually HAS a usable
        header segment claims the article as ``extracted``.
      * Pass 2 (fallback): every still-unclaimed owned article is bound
        ``chunk_fallback`` to the LOWEST-position chunk that lists it in owns.
    Returns ``(rows, stats)`` in the normalized stats shape.
    """
    reg_id = chunks[0].get("regulation_id") if chunks else None
    extracted_claim: dict[int, tuple[dict, str]] = {}  # article_no -> (chunk, text)
    fallback_owner: dict[int, dict] = {}               # article_no -> lowest-pos chunk
    owns_pairs = 0

    for ch in chunks:
        owns_madda = {
            int(x)
            for x in ((ch.get("owns") or {}).get("MADDA") or [])
            if isinstance(x, (int, float)) or (isinstance(x, str) and str(x).isdigit())
        }
        if not owns_madda:
            continue
        owns_pairs += len(owns_madda)
        segments = extract_chunk_articles(ch.get("content") or "", owns_madda)
        for no in owns_madda:
            if no not in fallback_owner:          # lowest-position owner (chunks pre-sorted)
                fallback_owner[no] = ch
            if no in segments and no not in extracted_claim:  # lowest-position header
                extracted_claim[no] = (ch, segments[no])

    stats = _empty_stats()
    rows: list[dict] = []
    for no in sorted(fallback_owner):
        if no in extracted_claim:
            ch, text = extracted_claim[no]
            extraction_status, article_text = "extracted", text
            stats["extracted"] += 1
        else:
            ch, article_text = fallback_owner[no], None
            extraction_status = "chunk_fallback"
            stats["chunk_fallback"] += 1
        rows.append(
            {
                "regulation_id": reg_id,
                "article_no": no,
                "article_label": f"المادة {no}",   # western digits (see docstring)
                "slug": f"المادة-{no}",             # western digits for URL sanity
                "chunk_id": ch.get("id"),
                "article_text": article_text,
                "extraction_status": extraction_status,
                "updated_at": now_iso,
                "created_at": now_iso,
            }
        )

    stats["duplicate"] = owns_pairs - len(fallback_owner)
    return rows, stats


# --------------------------------------------------------------------------
# DB helpers (READ-ONLY corpus; writes only to seo_articles).
# --------------------------------------------------------------------------


def _load_articles_v2(client, reg_id: str) -> list[dict]:
    """Fetch every ``articles_v2`` row of one regulation (paged)."""
    rows: list[dict] = []
    offset = 0
    while True:
        res = (
            client.table("articles_v2")
            .select("id, article_ref, chunk_parent_id, article_number, content")
            .eq("regulation_id", reg_id)
            .order("article_number")
            .range(offset, offset + _READ_PAGE - 1)
            .execute()
        )
        batch = res.data or []
        rows.extend(batch)
        if len(batch) < _READ_PAGE:
            break
        offset += _READ_PAGE
    return rows


def _load_regulation_chunks(client, reg_id: str) -> list[dict]:
    """Fetch every chunk of one regulation (paged, position-ordered)."""
    rows: list[dict] = []
    offset = 0
    while True:
        res = (
            client.table("chunks_v2")
            .select("id, regulation_id, position, title, content, owns")
            .eq("regulation_id", reg_id)
            .order("position")
            .order("id")  # deterministic tiebreak when chunks share a position
            .range(offset, offset + _READ_PAGE - 1)
            .execute()
        )
        batch = res.data or []
        rows.extend(batch)
        if len(batch) < _READ_PAGE:
            break
        offset += _READ_PAGE
    return rows


def _discover_articles_v2_reg_ids(client) -> list[str]:
    """Distinct ``regulation_id``s present in ``articles_v2`` (first-seen order).

    Pages the view selecting only ``regulation_id`` — the primary-source regulation
    universe. First-seen order (ordered by regulation_id) is stable across runs so
    a dry-run sample is reproducible.
    """
    seen: dict[str, None] = {}
    offset = 0
    while True:
        res = (
            client.table("articles_v2")
            .select("regulation_id")
            .order("regulation_id")
            .range(offset, offset + _READ_PAGE - 1)
            .execute()
        )
        batch = res.data or []
        for r in batch:
            rid = r.get("regulation_id")
            if rid and rid not in seen:
                seen[rid] = None
        if len(batch) < _READ_PAGE:
            break
        offset += _READ_PAGE
    return list(seen)


def _discover_chunk_madda_reg_ids(client) -> list[str]:
    """Distinct ``regulation_id``s with at least one مادة-owning chunk (fallback
    universe). Pages ``chunks_v2`` selecting only (regulation_id, owns)."""
    seen: dict[str, None] = {}
    offset = 0
    while True:
        res = (
            client.table("chunks_v2")
            .select("regulation_id, owns")
            .order("regulation_id")
            .order("position")
            .range(offset, offset + _READ_PAGE - 1)
            .execute()
        )
        batch = res.data or []
        for r in batch:
            rid = r.get("regulation_id")
            madda = (r.get("owns") or {}).get("MADDA") or []
            if rid and madda and rid not in seen:
                seen[rid] = None
        if len(batch) < _READ_PAGE:
            break
        offset += _READ_PAGE
    return list(seen)


def _delete_regulation(client, reg_id: str) -> None:
    """Purge one regulation's ``seo_articles`` rows."""
    client.table("seo_articles").delete().eq("regulation_id", reg_id).execute()


def _rebuild_regulation(client, reg_id: str, rows: list[dict]) -> None:
    """DELETE-then-INSERT one regulation's ``seo_articles`` rows (batched)."""
    _delete_regulation(client, reg_id)
    for i in range(0, len(rows), _WRITE_BATCH):
        client.table("seo_articles").insert(rows[i : i + _WRITE_BATCH]).execute()


# --------------------------------------------------------------------------
# Driver.
# --------------------------------------------------------------------------


def _build_one(client, reg_id: str, now_iso: str):
    """Build one regulation's rows from the best available source.

    Probes ``articles_v2`` FIRST (the primary source); a regulation with no
    articles_v2 rows falls back to chunk extraction. Returns ``(rows, stats,
    source)`` where ``source`` ∈ {'articles_v2','chunks','none'}.
    """
    av2_rows = _load_articles_v2(client, reg_id)
    if av2_rows:
        owner_map: dict[int, str] = {}
        if any(not r.get("chunk_parent_id") for r in av2_rows):
            owner_map = _owns_owner_map(_load_regulation_chunks(client, reg_id))
        rows, stats = build_rows_from_articles_v2(reg_id, av2_rows, owner_map, now_iso)
        return rows, stats, "articles_v2"

    chunks = _load_regulation_chunks(client, reg_id)
    if not chunks:
        return [], _empty_stats(), "none"
    rows, stats = build_regulation_rows_from_chunks(chunks, now_iso)
    return rows, stats, "chunks"


def process(client, reg_ids: list[str], apply: bool) -> dict:
    """Process a list of regulations; print progress + collect stats/samples."""
    now_iso = datetime.now(timezone.utc).isoformat()
    grand = {
        "regulations": 0,
        "av2_regs": 0,
        "chunk_regs": 0,
        "rows": 0,
        "av2_rows": 0,
        "chunk_rows": 0,
        "extracted": 0,
        "chunk_fallback": 0,
        "skipped_nonnumeric": 0,
        "skipped_no_chunk": 0,
        "tiny_content": 0,
        "duplicate": 0,
        "purged_empty": 0,
    }
    samples: list[tuple[str, int, str]] = []  # (status, article_no, preview)

    for reg_id in reg_ids:
        rows, stats, source = _build_one(client, reg_id, now_iso)

        # Aggregate per-reg counters ALWAYS (a reg whose every row was skipped —
        # e.g. all-non-numeric or no resolvable chunk_id — still contributes its
        # skip counts to the report even though it produces zero rows).
        for k in (
            "extracted", "chunk_fallback", "skipped_nonnumeric",
            "skipped_no_chunk", "tiny_content", "duplicate",
        ):
            grand[k] += stats[k]

        if not rows:
            # No rows from any source. On --apply still purge any stale rows so the
            # rebuild is clean (a reg that used to yield rows but no longer does).
            if apply:
                _delete_regulation(client, reg_id)
                grand["purged_empty"] += 1
            if stats["skipped_nonnumeric"] or stats["skipped_no_chunk"]:
                tag = "APPLIED" if apply else "dry-run"
                print(
                    f"  [{tag}] --- {reg_id}  rows=   0  (all skipped: "
                    f"nn={stats['skipped_nonnumeric']} nc={stats['skipped_no_chunk']})"
                )
            continue

        grand["regulations"] += 1
        grand["rows"] += len(rows)
        if source == "articles_v2":
            grand["av2_regs"] += 1
            grand["av2_rows"] += len(rows)
        else:
            grand["chunk_regs"] += 1
            grand["chunk_rows"] += len(rows)

        # Collect a spread of extraction samples (dry-run report).
        for r in rows:
            if len(samples) >= 10:
                break
            if any(s[1] == r["article_no"] for s in samples):
                continue
            preview = (r["article_text"] or "(chunk_fallback → whole chunk)")
            preview = re.sub(r"\s+", " ", preview)[:90]
            samples.append((r["extraction_status"], r["article_no"], preview))

        if apply:
            _rebuild_regulation(client, reg_id, rows)

        # Per-reg one-liner (helps watch --apply progress).
        rate = (stats["extracted"] / len(rows) * 100) if rows else 0.0
        tag = "APPLIED" if apply else "dry-run"
        src = "av2" if source == "articles_v2" else "chk"
        print(
            f"  [{tag}] {src} {reg_id}  rows={len(rows):>4}  "
            f"extracted={stats['extracted']:>4} ({rate:5.1f}%)  "
            f"fallback={stats['chunk_fallback']:>4}  "
            f"skip(nn/nc)={stats['skipped_nonnumeric']}/{stats['skipped_no_chunk']}"
        )

    # Summary --------------------------------------------------------------
    rows_total = grand["rows"]
    ext_rate = (grand["extracted"] / rows_total * 100) if rows_total else 0.0
    fb_rate = (grand["chunk_fallback"] / rows_total * 100) if rows_total else 0.0
    print("\n" + "=" * 68)
    print(f"  regulations processed : {grand['regulations']}"
          f"  (articles_v2={grand['av2_regs']}, chunk-fallback={grand['chunk_regs']})")
    print(f"  seo_articles rows     : {rows_total}")
    print(f"    - from articles_v2  : {grand['av2_rows']}")
    print(f"    - from chunk-extract: {grand['chunk_rows']}")
    print(f"    - extracted (all)   : {grand['extracted']} ({ext_rate:.1f}%)")
    print(f"    - chunk_fallback    : {grand['chunk_fallback']} ({fb_rate:.1f}%)")
    print(f"  skipped non-numeric   : {grand['skipped_nonnumeric']}")
    print(f"  skipped no-chunk_id   : {grand['skipped_no_chunk']}")
    print(f"  tiny content (<20 ch) : {grand['tiny_content']} (→ chunk_fallback)")
    print(f"  duplicate مادة kept   : {grand['duplicate']} (longest body wins)")
    if apply:
        print(f"  regs purged (0 rows)  : {grand['purged_empty']}")

    if samples:
        print("\n  sample extractions (status / article_no / preview):")
        for status, no, preview in samples[:10]:
            flag = "EXT " if status == "extracted" else "FALL"
            print(f"    [{flag}] المادة {no:<4} {preview}")
    print("=" * 68)

    return grand


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Rebuild the seo_articles per-مادة index from articles_v2 "
                    "(chunk extraction fallback for regs absent from articles_v2)."
    )
    ap.add_argument(
        "--apply",
        action="store_true",
        help="actually write (DEFAULT is a dry-run that writes nothing)",
    )
    ap.add_argument(
        "--reg",
        default=None,
        help="limit scope to a single regulation_id (uuid)",
    )
    ap.add_argument(
        "--sample",
        type=int,
        default=_DEFAULT_SAMPLE,
        help=(
            f"dry-run only: number of regulations to walk when no --reg is given "
            f"(default {_DEFAULT_SAMPLE}; نظام العمل is pinned first). Ignored on --apply."
        ),
    )
    args = ap.parse_args()

    client = get_supabase_client()

    if args.reg:
        # Single regulation — _build_one probes articles_v2 directly, so no
        # (slow) full-universe discovery is needed.
        reg_ids = [args.reg]
        scope = f"single regulation {args.reg}"
    elif args.apply:
        # The primary-source universe is articles_v2; a chunk-only regulation
        # (none live today, but future-proofed) is picked up via owns-MADDA.
        av2_ids = _discover_articles_v2_reg_ids(client)
        av2_set = set(av2_ids)
        chunk_only = [r for r in _discover_chunk_madda_reg_ids(client) if r not in av2_set]
        reg_ids = av2_ids + chunk_only
        scope = (f"ALL {len(reg_ids)} regulations "
                 f"(articles_v2={len(av2_ids)}, chunk-only={len(chunk_only)})")
    else:
        # Pin نظام العمل first, then fill up to --sample distinct regs.
        av2_ids = _discover_articles_v2_reg_ids(client)
        ordered = [LABOR_LAW_REG] + [r for r in av2_ids if r != LABOR_LAW_REG]
        reg_ids = ordered[: max(1, args.sample)]
        scope = f"{len(reg_ids)} sample regulations (نظام العمل pinned)"

    mode = "APPLY" if args.apply else "DRY-RUN"
    print(f"build_seo_article_index — mode={mode}, scope={scope}\n")

    process(client, reg_ids, args.apply)

    if not args.apply:
        print("\n(no rows written — re-run with --apply to persist)")
    print()


if __name__ == "__main__":
    main()
