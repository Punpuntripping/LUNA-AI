"""Derived naming for the /judgments wing (public SEO library).

The judgments corpus (``public.cases``, ~30.5k rows) is pipeline-owned and has
NO title and NO slug column — every other wing reads a real ``title`` off its
corpus row. So both are DERIVED here, deterministically, from the row's own
summary fields, and this module is the single source of truth for that
derivation:

  * ``scripts/build_judgment_slugs.py`` calls :func:`judgment_slug_base` to write
    the PERMANENT slug into the ``seo_item_meta`` sidecar; and
  * ``backend/app/services/library_service.py`` calls :func:`judgment_subject` /
    :func:`judgment_display_title` at read time to render the H1 and card title.

Both must agree: if the read path derived a different subject than the one the
slug was cut from, a published URL would resolve to a page whose H1 no longer
matches its own address. Never fork this logic — import it.

Why derive rather than store: ``cases`` is re-ingested by the user's pipeline and
must not be ALTERed (same rule as the ``*_v2`` corpus VIEWS), and a stored title
would drift silently the moment a summary is regenerated. The slug is the one
thing that MUST be frozen (URLs are permanent), which is exactly why it lives in
the sidecar and is never rewritten.
"""
from __future__ import annotations

import hashlib
import re
import unicodedata
from typing import Any, Mapping, Optional

# ── slug alphabet ──────────────────────────────────────────────────────────
# Deliberately identical to ``scripts/build_seo_slugs.py``'s ``slugify_ar`` so
# judgment slugs sit in the same URL namespace style as every other wing. It is
# duplicated rather than imported because that script's copy governs slugs that
# are ALREADY published and frozen — this module must never be able to change
# them by accident.
_TASHKEEL = frozenset(range(0x064B, 0x0653))
_TATWEEL = 0x0640


def _is_slug_char(ch: str) -> bool:
    """True for a Latin alphanumeric or an Arabic-block letter/digit."""
    if ("a" <= ch <= "z") or ("A" <= ch <= "Z") or ("0" <= ch <= "9"):
        return True
    o = ord(ch)
    if 0x0600 <= o <= 0x06FF or 0x0750 <= o <= 0x077F:
        return unicodedata.category(ch)[0] in ("L", "N")
    return False


def slugify_ar(text: str) -> str:
    """Build a URL-safe Arabic slug fragment from ``text``."""
    if not text:
        return ""
    s = unicodedata.normalize("NFC", str(text))
    s = "".join(ch for ch in s if ord(ch) not in _TASHKEEL and ord(ch) != _TATWEEL)
    s = s.lower()
    s = "".join(ch if _is_slug_char(ch) else " " for ch in s)
    s = re.sub(r"\s+", "-", s.strip())
    return re.sub(r"-+", "-", s).strip("-")


# ── court level ────────────────────────────────────────────────────────────
COURT_LEVEL_LABELS: dict[str, str] = {
    "first_instance": "ابتدائي",
    "appeal": "استئناف",
    "supreme": "المحكمة العليا",
}


def court_level_label(level: Optional[str]) -> Optional[str]:
    """Arabic label for a ``cases.court_level`` value (``None`` when unknown)."""
    if not level:
        return None
    return COURT_LEVEL_LABELS.get(str(level).strip())


# ── dates ──────────────────────────────────────────────────────────────────
_ARABIC_INDIC = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")


def hijri_year(date_hijri: Optional[str]) -> Optional[str]:
    """Extract the 4-digit Hijri year from a free-text date («22 رَجب 1444»).

    Tolerates tashkeel and Arabic-Indic digits. Returns ``None`` when no 4-digit
    year in the plausible Hijri range (1300–1500) is present.
    """
    if not date_hijri:
        return None
    s = str(date_hijri).translate(_ARABIC_INDIC)
    for match in re.findall(r"\d{4}", s):
        if 1300 <= int(match) <= 1500:
            return match
    return None


# ── subject line (the derived H1) ──────────────────────────────────────────
# Trailing tokens that make a truncated Arabic title read as cut mid-thought.
_DANGLING = {
    "و", "في", "من", "على", "عن", "إلى", "الى", "مع", "بين", "بعد", "قبل",
    "التي", "الذي", "الذين", "ما", "أن", "إن", "أو", "او", "ثم", "حيث",
    "بشأن", "بسبب", "لدى", "ضد", "عند", "منذ", "خلال", "حول", "نحو", "لأن",
}
_SUBJECT_MAX = 90
# Markdown/bullet noise the summary fields carry («- », «## الملخص», «**x**»).
_BULLET_RE = re.compile(r"^[\s\-*•·—–]+")
_HEADING_RE = re.compile(r"^#{1,6}\s*")
_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
_TRAILING_PUNCT = " \t.،؛:؟!-–—*"


def _clean_line(raw: str) -> str:
    """Strip heading/bold/bullet markers and trailing punctuation from a line.

    ORDER MATTERS. Bold is unwrapped BEFORE the bullet prefix is stripped:
    ``_BULLET_RE`` treats ``*`` as a bullet character, so on «* **نزاع تجاري** حول…»
    a bullet-first pass would swallow the OPENING ``**`` and strand the closing
    one in the title. The trailing ``replace`` is the belt-and-braces catch for
    genuinely unmatched markers already present in the source text.
    """
    line = _HEADING_RE.sub("", (raw or "").strip())
    line = _BOLD_RE.sub(r"\1", line)
    line = _BULLET_RE.sub("", line).replace("**", "")
    line = re.sub(r"\s+", " ", line).strip()
    return line.strip(_TRAILING_PUNCT).strip()


def _first_meaningful_line(text: Optional[str]) -> str:
    """First content line of a markdown-ish block, skipping headings/blanks.

    Section headings («## الملخص») are navigation, not content — they are skipped
    so the summary's own first bullet wins.
    """
    if not text:
        return ""
    for raw in str(text).splitlines():
        if not raw.strip():
            continue
        if _HEADING_RE.match(raw.strip()):
            continue
        cleaned = _clean_line(raw)
        if len(cleaned) >= 12:
            return cleaned
    return ""


def _truncate_words(text: str, limit: int = _SUBJECT_MAX) -> str:
    """Cut ``text`` to ``limit`` chars, preferring a natural clause boundary.

    Summary first lines are often one long sentence whose opening clause is
    already a complete thought («استئناف ضريبي أمام اللجنة الاستئنافية بشأن الربط
    الزكوي لعام 2007م، يتعلق بحسم…»). Cutting at the «،» yields a real title;
    cutting purely on word count strands a dangling verb. So: take the last
    clause separator inside the limit when it lands past 40% of it, else fall
    back to a word-boundary cut with dangling connectors dropped.

    No «…» is appended — the subject is a title, not a snippet, and the full
    sentence stays available in the lead summary right below the H1.
    """
    text = text.strip()
    if len(text) <= limit:
        return text

    window = text[:limit]
    boundary = max(window.rfind(sep) for sep in ("،", "؛", ".", ":", "؟", "!"))
    if boundary >= int(limit * 0.4):
        clause = window[:boundary].strip(_TRAILING_PUNCT).strip()
        if clause:
            return clause

    words = window.split(" ")
    if len(words) > 1:
        words.pop()  # the word the cut landed inside
    while words and (words[-1] in _DANGLING or len(words[-1]) <= 1):
        words.pop()
    return " ".join(words).strip(_TRAILING_PUNCT).strip() or text[:limit].strip()


def judgment_subject(row: Mapping[str, Any]) -> str:
    """The derived subject line — what the judgment is ABOUT (the page H1).

    Source preference: ``short_summary`` → ``summary`` → ``facts`` → ``ruling``.
    The first line of ``short_summary`` is a one-sentence statement of the
    dispute («نزاع تجاري حول عقد توريد مستلزمات ومعدات طبية») — genuinely the best
    available title, and present on 29.5k of 30.5k rows.

    Falls back to «حكم {court}» (+ case number) for rows with no usable summary,
    so this NEVER returns an empty string.
    """
    for field in ("short_summary", "summary", "facts", "ruling"):
        line = _first_meaningful_line(row.get(field))
        if line:
            return _truncate_words(line)

    court = (row.get("court") or "").strip()
    number = (row.get("case_number") or row.get("judgment_number") or "").strip()
    if court and number:
        return _truncate_words(f"حكم {court} رقم {number}")
    if court:
        return _truncate_words(f"حكم {court}")
    return "حكم قضائي"


def judgment_display_title(row: Mapping[str, Any]) -> str:
    """Subject + court context — the card title and ``<title>`` base.

    «نزاع تجاري حول عقد توريد مستلزمات طبية — المحكمة التجارية 1445هـ». The court
    and year are what make otherwise similar dispute subjects distinguishable in
    a hub grid and in a search result.
    """
    subject = judgment_subject(row)
    court = (row.get("court") or "").strip()
    year = hijri_year(row.get("date_hijri"))

    tail_parts = [p for p in (court, f"{year}هـ" if year else None) if p]
    if not tail_parts:
        return subject
    return f"{subject} — {' '.join(tail_parts)}"


# ── slug ───────────────────────────────────────────────────────────────────
_SLUG_WORDS = 9
_REF_MAX = 24


def _stable_ref(row: Mapping[str, Any]) -> str:
    """A short, stable, per-judgment discriminator for the slug tail.

    Subjects repeat heavily across 30k judgments («نزاع تجاري حول عقد مقاولة»), so
    the slug needs a real discriminator rather than relying on ``-2``/``-3``
    collision suffixes, which would be assigned by insertion order and are
    therefore meaningless to a reader.

    ``case_ref`` («17642_ap_447264886») is the corpus's own unique key; its
    entity prefix is dropped because it is identical for the whole corpus. It is
    preferred over ``case_number`` because it also encodes the instance variant
    (``ap``/``fi``) — the first-instance and appeal rows of one dispute can share
    a ``case_number``, so that field alone is NOT unique.

    Some refs are long Arabic titles rather than ids
    («17486_الأحكام_التجارية_1428هـ_مجموعة_الاحكام_الادارية_-_الجزء_5_37…»); those
    would swamp the slug, so anything over ``_REF_MAX`` collapses to a short
    deterministic digest of the ref. blake2s (not ``hash()``) because the value
    must be identical across runs, machines and Python versions — the slug it
    produces is permanent.
    """
    case_ref = (row.get("case_ref") or "").strip()
    if case_ref:
        tail = re.sub(r"^\d+_", "", case_ref)
        slug_tail = slugify_ar(tail.replace("_", "-"))
        if slug_tail and len(slug_tail) <= _REF_MAX:
            return slug_tail
        return hashlib.blake2s(case_ref.encode("utf-8"), digest_size=4).hexdigest()
    for field in ("case_number", "judgment_number"):
        value = (row.get(field) or "").strip()
        if value:
            slug_value = slugify_ar(value)
            if slug_value:
                return slug_value[:_REF_MAX]
    return slugify_ar(str(row.get("id") or ""))[:8] or "case"


def judgment_slug_base(row: Mapping[str, Any]) -> str:
    """PERMANENT slug base for a judgment: ``{subject-words}-{stable-ref}``.

    The caller (``scripts/build_judgment_slugs.py``) is responsible for collision
    dedupe against the sidecar; this returns the deterministic base only. Slugs
    already written are never recomputed — URLs are permanent.
    """
    words = [w for w in slugify_ar(judgment_subject(row)).split("-") if w]
    words = words[:_SLUG_WORDS]
    # A slug ending on a connector («…-تعاقدي-بين-ap-447264886») reads as noise;
    # dropping it costs no meaning since the tail ref follows anyway.
    while words and words[-1] in _DANGLING:
        words.pop()
    base = "-".join(words)
    ref = _stable_ref(row)
    if not base:
        return f"حكم-{ref}" if ref else "حكم"
    return f"{base}-{ref}" if ref else base
