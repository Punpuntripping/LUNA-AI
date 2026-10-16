"""Contracts for the style_profiler (Layer-4 memory agent).

The profile describes HOW a user writes — never WHAT they write about. It is
stored (``user_style_profiles``, migration 176) and injected into the unified
responder's dynamic tail, so it must be identifier-free by construction
(PDPL + وضع السرية, plan ``unified_responder.md`` §7):

* ``terms`` / ``notes`` are sanitised by validators that DROP offending
  entries (digits in any script, emails, URLs, @handles, name-like tokens)
  instead of failing the whole output — a flash model that slips one name into
  ``terms`` should cost that one term, not the refresh.
* The three enums are coerced from common variants ("بيضاء" → "عربية بيضاء",
  "قصير" → "short") with a safe default, for the same reason.
"""
from __future__ import annotations

import re
import warnings
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

Register = Literal["فصحى رسمية", "عربية بيضاء", "سعودية دارجة", "مختلط"]
LengthPref = Literal["short", "medium", "long"]
WritesAs = Literal["counsel", "party", "unknown"]

MAX_TERMS = 8
MAX_TERM_CHARS = 30
MAX_NOTES_CHARS = 200

# ---------------------------------------------------------------------------
# Identifier detection
# ---------------------------------------------------------------------------

# Latin, Arabic-Indic and Extended Arabic-Indic (Persian) digits.
_DIGIT_RE = re.compile(r"[0-9٠-٩۰-۹]")
_EMAIL_RE = re.compile(r"\S+@\S+")
_URL_RE = re.compile(r"(?i)(https?://|www\.|\.com\b|\.sa\b|\.net\b|\.org\b)")
_HANDLE_RE = re.compile(r"@\w+")
# Two or more consecutive Capitalised Latin words ("John Smith", "Acme Trading").
_LATIN_PROPER_RE = re.compile(r"\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)+\b")

# Whole-word Arabic markers that only occur inside a personal / family name or
# directly before one (honorifics). "المحامي" is deliberately NOT here: «يكتب
# كمحامٍ» is a legitimate style note.
_NAME_MARKERS = (
    "بن", "بنت", "ابن", "أبو", "ابو", "آل",
    "الأستاذ", "الاستاذ", "الأستاذة", "الاستاذة", "السيد", "السيدة", "الشيخ",
    "المهندس", "الدكتور", "الدكتورة", "سمو", "معالي",
)
# Common Saudi given names that are not also ordinary words. Words that double
# as vocabulary (علي «عليّ», عمر «عُمر», سلطان «سلطة», صالح «صالح للتنفيذ», هند,
# تركي) are intentionally left out — false positives would strip real terms.
_GIVEN_NAMES = (
    "محمد", "أحمد", "احمد", "خالد", "فهد", "سعود", "فيصل", "ناصر", "يوسف",
    "إبراهيم", "ابراهيم", "ماجد", "نايف", "منصور", "بندر", "عبدالعزيز",
    "عبدالرحمن", "عبدالله", "سارة", "ساره", "نورة", "نوره", "فاطمة", "فاطمه",
    "مريم", "عائشة", "خديجة", "لطيفة", "حصة", "ريم", "عبير", "مشاعل",
)
_AR_WORD_SPLIT = re.compile(r"[\s،؛.,;:!?()\[\]«»\"'/\\-]+")
# Arabic diacritics + tatweel, removed before comparisons.
_AR_NOISE = re.compile(r"[ؐ-ًؚ-ٰٟۖ-ۭـ]")


def normalize_ar(text: str) -> str:
    """Strip diacritics/tatweel and collapse whitespace (for comparisons only)."""
    return re.sub(r"\s+", " ", _AR_NOISE.sub("", text or "")).strip()


def looks_like_identifier(text: str) -> bool:
    """True when ``text`` carries something that could identify a person/matter.

    Conservative by design: a false positive costs one style term; a false
    negative stores an identifier in a profile that is read on every turn.
    """
    if not text:
        return False
    if _DIGIT_RE.search(text) or _EMAIL_RE.search(text) or _URL_RE.search(text):
        return True
    if _HANDLE_RE.search(text) or _LATIN_PROPER_RE.search(text):
        return True
    words = [w for w in _AR_WORD_SPLIT.split(normalize_ar(text)) if w]
    for w in words:
        if w in _NAME_MARKERS or w in _GIVEN_NAMES:
            return True
        # «عبد» compounds written apart or joined (عبد الله، عبدالكريم).
        if w == "عبد" or (w.startswith("عبد") and len(w) > 4 and not w.startswith("عبدي")):
            return True
    return False


def _clean_term(raw: Any) -> str | None:
    if not isinstance(raw, str):
        return None
    term = raw.strip().strip("«»\"'“”‘’").strip()
    term = re.sub(r"\s+", " ", term)
    if not term or len(term) > MAX_TERM_CHARS:
        return None
    if looks_like_identifier(term):
        return None
    return term


def sanitize_terms(raw: Any) -> list[str]:
    """Drop offending/oversized terms, dedupe (normalised), cap at MAX_TERMS."""
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, (list, tuple)):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for item in raw:
        term = _clean_term(item)
        if term is None:
            continue
        key = normalize_ar(term)
        if key in seen:
            continue
        seen.add(key)
        out.append(term)
        if len(out) >= MAX_TERMS:
            break
    return out


_SEGMENT_SPLIT = re.compile(r"(?<=[.؛،;!?\n])\s*")


def sanitize_notes(raw: Any) -> str:
    """Drop note segments that carry identifiers; truncate to MAX_NOTES_CHARS."""
    if not isinstance(raw, str):
        return ""
    segments = [s.strip() for s in _SEGMENT_SPLIT.split(raw.strip()) if s and s.strip()]
    kept = [s for s in segments if not looks_like_identifier(s)]
    notes = re.sub(r"\s+", " ", " ".join(kept)).strip()
    if len(notes) > MAX_NOTES_CHARS:
        cut = notes[:MAX_NOTES_CHARS]
        space = cut.rfind(" ")
        notes = (cut[:space] if space > MAX_NOTES_CHARS // 2 else cut).rstrip(" ،,؛;") + "…"
        notes = notes[:MAX_NOTES_CHARS]
    return notes


def _coerce_register(v: Any) -> str:
    s = normalize_ar(str(v or ""))
    if s in ("فصحى رسمية", "عربية بيضاء", "سعودية دارجة", "مختلط"):
        return s
    if "مختلط" in s or "mixed" in s.lower():
        return "مختلط"
    if "بيضاء" in s or "white" in s.lower():
        return "عربية بيضاء"
    if any(k in s for k in ("دارج", "عامي", "سعودي", "لهج")) or "dialect" in s.lower():
        return "سعودية دارجة"
    if "فصح" in s or "رسمي" in s or "formal" in s.lower() or "msa" in s.lower():
        return "فصحى رسمية"
    return "مختلط"


def _coerce_length(v: Any) -> str:
    s = str(v or "").strip().lower()
    if s in ("short", "medium", "long"):
        return s
    if any(k in s for k in ("قصير", "موجز", "مختصر", "brief", "concise")):
        return "short"
    if any(k in s for k in ("طويل", "مفصل", "مفصّل", "detailed")):
        return "long"
    return "medium"


def _coerce_writes_as(v: Any) -> str:
    s = str(v or "").strip().lower()
    if s in ("counsel", "party", "unknown"):
        return s
    if any(k in s for k in ("lawyer", "attorney", "محام", "وكيل", "مستشار")):
        return "counsel"
    if any(k in s for k in ("طرف", "individual", "client", "فرد", "مدعي", "مدعى")):
        return "party"
    return "unknown"


# ``register`` (the spec's field name, also the stored jsonb key) shadows the
# ABCMeta ``register`` classmethod on BaseModel. Harmless here — nothing calls
# ``StyleProfile.register(...)`` as an ABC — so the warning is silenced locally.
warnings.filterwarnings("ignore", message='Field name "register" in "StyleProfile"')


class StyleProfile(BaseModel):
    """The structured output the LLM produces AND the stored ``profile`` jsonb."""

    register: Register = Field(
        description="The register the user writes in: فصحى رسمية / عربية بيضاء / سعودية دارجة / مختلط.",
    )
    length_pref: LengthPref = Field(
        description="Answer length the user seems to prefer: short / medium / long.",
    )
    writes_as: WritesAs = Field(
        description="counsel = writes as a lawyer for a client (موكلي); party = writes about their own matter; unknown.",
    )
    terms: list[str] = Field(
        default_factory=list,
        description=(
            f"Up to {MAX_TERMS} legal terms the user uses VERBATIM (each ≤{MAX_TERM_CHARS} chars). "
            "No names, numbers, dates, places, company or case identifiers."
        ),
    )
    notes: str = Field(
        default="",
        description=f"≤{MAX_NOTES_CHARS} chars on style only (tone, structure). No facts or identifiers.",
    )

    @field_validator("register", mode="before")
    @classmethod
    def _v_register(cls, v: Any) -> str:
        return _coerce_register(v)

    @field_validator("length_pref", mode="before")
    @classmethod
    def _v_length(cls, v: Any) -> str:
        return _coerce_length(v)

    @field_validator("writes_as", mode="before")
    @classmethod
    def _v_writes_as(cls, v: Any) -> str:
        return _coerce_writes_as(v)

    @field_validator("terms", mode="before")
    @classmethod
    def _v_terms(cls, v: Any) -> list[str]:
        return sanitize_terms(v)

    @field_validator("notes", mode="before")
    @classmethod
    def _v_notes(cls, v: Any) -> str:
        return sanitize_notes(v)


@dataclass
class StyleProfilerInput:
    """LLM-bound input. ``messages`` are the user's own messages, oldest-first,
    ALREADY passed through the masking codec by the runner.

    ``previous_profile`` is the stored ``profile`` jsonb (identifier-free by
    construction) or ``None`` on the first run.
    """

    messages: list[str]
    previous_profile: dict | None = None
    conversation_id: str | None = None  # telemetry only — never rendered
