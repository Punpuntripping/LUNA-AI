"""Reversible identifier-masking codec — PURE logic, no I/O, no network.

Part of the ``تقنيع المعرّفات`` (identifier masking / وضع السرية) feature.
Design source: ``.claude/plans/identifier_masking.md`` (Locked rules — implemented
here EXACTLY, not re-litigated) and memory ``project_pdpl_number_masking.md``.

The codec masks numeric identifiers (Saudi IDs, phones, IBANs, case numbers, …)
and email addresses in user text *before* it reaches an external LLM, and
restores the real values in anything the user sees. The per-user swap table
(real ↔ fake) lives in Postgres (see ``store.py``); this module is pure and only
manipulates in-memory mapping dicts injected by the caller. Newly-created fakes
are accumulated in ``PrivacyCodec.new_mappings`` for the wiring layer to persist.

--------------------------------------------------------------------------------
Detection pipeline (per "Locked rules"), all indices computed on the *normalized*
copy of the text (digit-normalization is 1:1 per char, so indices line up with
the original string — we detect on ``norm`` and splice replacements into the
ORIGINAL so untouched content stays byte-identical, including Arabic-Indic digits
inside excluded dates/money):

    NORMALIZE  Arabic-Indic (U+0660–0669) + Extended Arabic-Indic (U+06F0–06F9)
               digits → ASCII.  1:1, length-preserving.
    EMAILS     detected first; the whole address is swapped (own @ rule).
    DATES      (BEFORE dash-joining) 2–3 digit groups joined by a single
               consistent '/' or '-' where exactly one group is a 4-digit year
               (prefix 13|14 Hijri or 19|20 Gregorian) and every other group is
               1–2 digits with value 1..31  →  protected (untouched). The prefix
               test NEVER runs on unseparated runs (an ID can start 1446).
    MONEY      a money marker (مبلغ|ريال|ر.س|SAR|دولار) within a small window of
               the run  →  protected. (Comma-grouped amounts like 750,000 are
               inherently safe: commas split groups and are NOT join separators,
               so each grouped piece is <5 digits and never a candidate.)
    JOIN       remaining digit groups linked ONLY by dashes/spaces → one run.
    MASK       any run of ≥5 digits → keep first 4 digits (first 3 if the run is
               5–7 digits long), RANDOM digits for the rest, same length. Random
               per value (NOT a substitution cipher); the table remembers.

--------------------------------------------------------------------------------
Ambiguities resolved (documented per task instruction):

1.  ENCODE is *position-preserving*: separators inside a run are kept and only
    the digit characters are substituted in place (a masked phone still looks
    like a phone to the LLM, and char-length is preserved). The stored
    ``fake_value`` is the UNSEPARATED concatenation of the substituted digits, so
    it matches however the LLM re-emits it. DECODE is separator-collapsing: it
    emits the stored (unseparated) real value — "formatting loss accepted on
    decode" per the plan.
2.  "Phones … collapse to consistent masking" (test cat. 2) is read as *same
    underlying value → same fake*, NOT as collapsing separators on encode.
3.  Date rule: non-year groups must be length 1–2 with integer value 1..31
    (covers both day ≤31 and month ≤12); separators within one date must be a
    single consistent character ('/' or '-').
4.  Money proximity window = 16 chars either side of the run. Markers matched as
    substrings; "SAR" matched case-insensitively.
5.  keep = 3 for runs of length 5–7, else 4 (length ≥ 8).
6.  JOIN is dash/space only (never '/'). Slash-separated dates are therefore
    inherently safe (their pieces are <5 digits); the date exclusion mainly
    matters for DASH-separated dates like ``15-9-1446`` / ``2024-05-12`` whose
    pieces WOULD otherwise join into a ≥5-digit run.
7.  The damaged-fake tripwire may also fire on a real value that leaks into LLM
    output (a real shares its fake's kept prefix + length) or on a coincidental
    prefix collision. This is intentional: it is log-only in v1, carries only
    (kind, prefix, length) — never a value — and NEVER changes decode output
    (we never decode by prefix; two-clients-same-prefix is a swap hazard).
8.  Fake email domain is fixed to ``example.com`` (RFC 2606 reserved, neutral,
    non-identifying). The real domain is never kept.
9.  Residual (accepted, per join rule): a dash year-range like ``2020-2021``
    joins to an 8-digit run and IS masked; compact dates like ``20240512`` mask
    but keep-4 preserves the year.
10. The pure fake-generation primitives live here (``generate_number_fake`` /
    ``generate_email_fake``); ``store.py`` reuses them to regenerate on a fake
    collision. Keeping them here preserves the "all pure logic in codec" split.
"""
from __future__ import annotations

import random
import re
from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# Digit normalization (Arabic-Indic + Extended Arabic-Indic → ASCII), 1:1
# ---------------------------------------------------------------------------

_ARABIC_INDIC = {0x0660 + i: str(i) for i in range(10)}        # ٠-٩
_EXT_ARABIC_INDIC = {0x06F0 + i: str(i) for i in range(10)}    # ۰-۹
_DIGIT_TRANSLATE: dict[int, str] = {**_ARABIC_INDIC, **_EXT_ARABIC_INDIC}

# Codepoint ranges for "is this char a digit in any supported script?"
_DIGIT_RANGES = (
    (0x0030, 0x0039),  # ASCII 0-9
    (0x0660, 0x0669),  # Arabic-Indic
    (0x06F0, 0x06F9),  # Extended Arabic-Indic
)


def normalize_digits(text: str) -> str:
    """Map Arabic-Indic and Extended Arabic-Indic digits to ASCII.

    1:1 per character (each digit → exactly one ASCII digit), so character
    indices are preserved between ``text`` and its normalized form. This is the
    property that lets us detect on the normalized copy and splice replacements
    into the original string.
    """
    return text.translate(_DIGIT_TRANSLATE)


def _is_digit_char(ch: str) -> bool:
    o = ord(ch)
    for lo, hi in _DIGIT_RANGES:
        if lo <= o <= hi:
            return True
    return False


# ---------------------------------------------------------------------------
# Public data classes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TripwireEvent:
    """A damaged-fake signal. Carries NO real/fake values — only shape.

    Emitted on decode when a run matches a known fake's kept prefix AND length
    but differs in the tail. Log-only in v1 (the codec returns these; Logfire
    emission happens in the wiring phase).
    """

    kind: str      # 'number' | 'email'
    prefix: str    # the kept prefix of the fake (first 3/4 digits) — shape only
    length: int    # length of the run


@dataclass
class DecodeResult:
    """Result of a decode pass."""

    text: str
    tripwires: list[TripwireEvent] = field(default_factory=list)
    restored_count: int = 0


@dataclass(frozen=True)
class AuditHit:
    """A leak candidate found by re-running detection on encoded text.

    Carries only shape info (kind + length), NEVER the value.
    """

    kind: str      # 'number' | 'email'
    length: int


@dataclass(frozen=True)
class NewMapping:
    """A newly-created real↔fake pair to be persisted by the wiring layer."""

    kind: str          # 'number' | 'email'
    real_value: str    # normalized ASCII form
    fake_value: str    # unseparated fake (digits) / fake email address


# ---------------------------------------------------------------------------
# Regexes / constants
# ---------------------------------------------------------------------------

# Digit group = maximal ASCII digit run (norm is already ASCII-normalized).
_DIGIT_GROUP_RE = re.compile(r"[0-9]+")

# Email address. Local part then @ then a dotted domain.
_EMAIL_RE = re.compile(
    r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)*\.[A-Za-z]{2,}"
)

# Date shape: 2 or 3 groups joined by a single, consistent '/' or '-'.
# Bounded by non-digits so we never grab part of a longer unseparated run.
_DATE_RE = re.compile(
    r"(?<![0-9])([0-9]{1,4})([/-])([0-9]{1,4})(?:\2([0-9]{1,4}))?(?![0-9])"
)

_YEAR_PREFIXES = frozenset({"13", "14", "19", "20"})

# Money markers. Arabic markers + case-sensitive/insensitive Latin ones.
_MONEY_MARKERS = ("مبلغ", "ريال", "ر.س", "دولار")
_MONEY_MARKERS_CI = ("sar",)
_MONEY_WINDOW = 16

# Chars that may join two digit groups into one run (dash/space ONLY).
_JOIN_CHARS = frozenset("- ")

# Fast-reject pattern: any digit (any script) or an '@'. If absent, encode has
# nothing to do (used only when masking is ENABLED; the disabled path returns
# before touching any regex at all).
_HAS_WORK_RE = re.compile(r"[0-9٠-٩۰-۹@]")

_DIGITS = "0123456789"
_FAKE_EMAIL_DOMAIN = "example.com"
_EMAIL_LOCAL_ALPHA = "abcdefghijklmnopqrstuvwxyz"
_EMAIL_LOCAL_ALNUM = "abcdefghijklmnopqrstuvwxyz0123456789"


def _keep_len(length: int) -> int:
    """Digits to keep from the front: 3 for 5–7 length runs, else 4."""
    return 3 if length <= 7 else 4


# ---------------------------------------------------------------------------
# Fake generators (pure — injected RNG). Reused by store.py on collision retry.
# ---------------------------------------------------------------------------


def generate_number_fake(
    real_digits: str,
    rng: random.Random,
    avoid: set[str],
) -> str:
    """Random, keep-prefix, length-preserving fake for a digit run.

    Keeps the first ``_keep_len`` real digits; the tail is random. Guaranteed to
    differ from ``real_digits`` and to avoid every string in ``avoid`` (existing
    fakes). ``avoid`` should be the user's known fake set.
    """
    length = len(real_digits)
    keep = _keep_len(length)
    prefix = real_digits[:keep]
    n_rand = length - keep  # always ≥ 1 for length ≥ 5

    for _ in range(128):
        tail = "".join(rng.choice(_DIGITS) for _ in range(n_rand))
        fake = prefix + tail
        if fake != real_digits and fake not in avoid:
            return fake

    # Deterministic fallback: walk the numeric space around the prefix.
    base = int(prefix + "0" * n_rand) if n_rand else int(prefix)
    span = 10 ** n_rand
    real_int = int(real_digits)
    for delta in range(span):
        candidate_int = base + delta
        fake = str(candidate_int).zfill(length)
        if len(fake) == length and fake != real_digits and fake not in avoid \
                and fake[:keep] == prefix and candidate_int != real_int:
            return fake
    raise ValueError("fake number space exhausted for run of length %d" % length)


def generate_email_fake(
    real_addr: str,
    rng: random.Random,
    avoid: set[str],
) -> str:
    """Random plausible local part + neutral fake domain. Never the real domain."""
    for _ in range(128):
        n = rng.randint(6, 12)
        first = rng.choice(_EMAIL_LOCAL_ALPHA)
        rest = "".join(rng.choice(_EMAIL_LOCAL_ALNUM) for _ in range(n - 1))
        fake = f"{first}{rest}@{_FAKE_EMAIL_DOMAIN}"
        if fake != real_addr and fake not in avoid:
            return fake
    # Fallback: deterministic suffix.
    i = 0
    while True:
        fake = f"user{i}@{_FAKE_EMAIL_DOMAIN}"
        if fake != real_addr and fake not in avoid:
            return fake
        i += 1


# ---------------------------------------------------------------------------
# Detection helpers (pure)
# ---------------------------------------------------------------------------


def _overlaps(span: tuple[int, int], spans: list[tuple[int, int]]) -> bool:
    s, e = span
    for a, b in spans:
        if s < b and a < e:
            return True
    return False


def _find_email_spans(norm: str) -> list[tuple[int, int, str]]:
    return [(m.start(), m.end(), m.group(0)) for m in _EMAIL_RE.finditer(norm)]


def _is_valid_date(groups: list[str]) -> bool:
    """Exactly one group is a valid 4-digit year; the rest are 1–2 digit 1..31."""
    year_count = 0
    others_ok = True
    for g in groups:
        if len(g) == 4 and g[:2] in _YEAR_PREFIXES:
            year_count += 1
            continue
        # non-year group: must be a plausible day/month
        if 1 <= len(g) <= 2 and 1 <= int(g) <= 31:
            continue
        others_ok = False
    return year_count == 1 and others_ok


def _find_date_spans(
    norm: str, email_spans: list[tuple[int, int]]
) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    for m in _DATE_RE.finditer(norm):
        groups = [g for g in (m.group(1), m.group(3), m.group(4)) if g is not None]
        if len(groups) < 2:
            continue
        if _overlaps((m.start(), m.end()), email_spans):
            continue
        if _is_valid_date(groups):
            spans.append((m.start(), m.end()))
    return spans


def _find_number_runs(
    norm: str, protected: list[tuple[int, int]]
) -> list[tuple[int, int, str]]:
    """Join digit groups separated ONLY by dash/space into runs.

    ``protected`` spans (dates, emails) act as barriers: groups inside them are
    dropped, and a run never joins across a protected region.
    Returns ``(start, end, digit_str)`` where ``digit_str`` is the run's digits
    with separators stripped and ``[start, end)`` is the full run span.
    """
    groups = [
        (m.start(), m.end())
        for m in _DIGIT_GROUP_RE.finditer(norm)
        if not _overlaps((m.start(), m.end()), protected)
    ]
    runs: list[tuple[int, int, str]] = []
    i = 0
    n = len(groups)
    while i < n:
        start, end = groups[i]
        j = i + 1
        while j < n:
            nxt_start, nxt_end = groups[j]
            gap = norm[end:nxt_start]
            if (
                gap
                and all(c in _JOIN_CHARS for c in gap)
                and not _overlaps((end, nxt_start), protected)
            ):
                end = nxt_end
                j += 1
            else:
                break
        digit_str = "".join(_DIGIT_GROUP_RE.findall(norm[start:end]))
        runs.append((start, end, digit_str))
        i = j
    return runs


def _is_money_context(norm: str, start: int, end: int) -> bool:
    pre = norm[max(0, start - _MONEY_WINDOW):start]
    post = norm[end:end + _MONEY_WINDOW]
    ctx = pre + "\n" + post
    if any(marker in ctx for marker in _MONEY_MARKERS):
        return True
    low = ctx.lower()
    return any(marker in low for marker in _MONEY_MARKERS_CI)


def _detect(
    norm: str,
) -> tuple[list[tuple[int, int, str]], list[tuple[int, int, str]]]:
    """Shared encode/audit detector.

    Returns ``(emails, candidates)`` where ``candidates`` are number runs of ≥5
    digits that are neither date- nor money-excluded. Both are expressed in
    ``norm`` coordinates (== original coordinates).
    """
    emails = _find_email_spans(norm)
    email_spans = [(s, e) for (s, e, _a) in emails]
    date_spans = _find_date_spans(norm, email_spans)
    protected = email_spans + date_spans

    candidates: list[tuple[int, int, str]] = []
    for (s, e, digits) in _find_number_runs(norm, protected):
        if len(digits) < 5:
            continue
        if _is_money_context(norm, s, e):
            continue
        candidates.append((s, e, digits))
    return emails, candidates


def _apply(text: str, repls: list[tuple[int, int, str]]) -> str:
    """Splice ``repls`` (non-overlapping) into ``text``."""
    if not repls:
        return text
    repls = sorted(repls, key=lambda r: r[0])
    out: list[str] = []
    cursor = 0
    for s, e, replacement in repls:
        if s < cursor:  # defensive: skip overlaps
            continue
        out.append(text[cursor:s])
        out.append(replacement)
        cursor = e
    out.append(text[cursor:])
    return "".join(out)


def _render_positional(original_span: str, fake_digits: str) -> str:
    """Place ``fake_digits`` onto the digit positions of ``original_span``.

    Non-digit chars (separators) are kept in place; each digit char (ASCII or
    Arabic-Indic) is replaced by the next fake digit (ASCII). Position-preserving
    → same char length, separators retained.
    """
    out: list[str] = []
    k = 0
    for ch in original_span:
        if _is_digit_char(ch):
            out.append(fake_digits[k])
            k += 1
        else:
            out.append(ch)
    return "".join(out)


# ---------------------------------------------------------------------------
# The codec
# ---------------------------------------------------------------------------


class PrivacyCodec:
    """Stateful per-user codec over an in-memory mapping.

    Construct with the user's existing mapping (loaded by ``store.py``). Fakes
    created during ``encode`` are appended to :attr:`new_mappings` for the wiring
    layer to persist. Pure: no I/O.
    """

    def __init__(
        self,
        real_to_fake: dict[str, str] | None = None,
        fake_to_real: dict[str, str] | None = None,
        *,
        rng: random.Random | None = None,
        enabled: bool = True,
    ) -> None:
        self.real_to_fake: dict[str, str] = dict(real_to_fake or {})
        self.fake_to_real: dict[str, str] = dict(fake_to_real or {})
        self.rng: random.Random = rng if rng is not None else random.SystemRandom()
        self.enabled = enabled
        self._new_mappings: list[NewMapping] = []
        # Index of (length, kept-prefix) over NUMBER fakes for the tripwire.
        self._number_fake_prefixes: set[tuple[int, str]] = set()
        for f in self.fake_to_real:
            self._index_fake(f)

    # -- introspection -----------------------------------------------------

    @property
    def new_mappings(self) -> list[NewMapping]:
        """Fakes created since construction (to be persisted by the caller)."""
        return list(self._new_mappings)

    @property
    def fake_values(self) -> set[str]:
        """The user's known fake set (for :func:`audit`)."""
        return set(self.fake_to_real.keys())

    # -- internal bookkeeping ---------------------------------------------

    def _index_fake(self, fake: str) -> None:
        if "@" in fake:
            return
        length = len(fake)
        self._number_fake_prefixes.add((length, fake[:_keep_len(length)]))

    def _record(self, kind: str, real: str, fake: str) -> None:
        self.real_to_fake[real] = fake
        self.fake_to_real[fake] = real
        self._index_fake(fake)
        self._new_mappings.append(NewMapping(kind, real, fake))

    def _fake_for_number(self, digits: str) -> str | None:
        """Fake for a number run, or ``None`` if it should be left untouched.

        Idempotence: a run that is already a known fake is left as-is.
        """
        if digits in self.fake_to_real:      # already a fake → leave untouched
            return None
        existing = self.real_to_fake.get(digits)
        if existing is not None:
            return existing
        fake = generate_number_fake(digits, self.rng, set(self.fake_to_real.keys()))
        self._record("number", digits, fake)
        return fake

    def _fake_for_email(self, addr: str) -> str | None:
        if addr in self.fake_to_real:        # already a fake → leave untouched
            return None
        existing = self.real_to_fake.get(addr)
        if existing is not None:
            return existing
        fake = generate_email_fake(addr, self.rng, set(self.fake_to_real.keys()))
        self._record("email", addr, fake)
        return fake

    # -- encode ------------------------------------------------------------

    def encode(self, text: str) -> str:
        """Mask identifiers + emails. Byte-identical no-op when disabled.

        Position-preserving: separators inside a masked run are retained and only
        digit characters are substituted; the stored fake is the unseparated
        digit concatenation.
        """
        if not self.enabled:
            return text  # passthrough: zero regex work
        if not _HAS_WORK_RE.search(text):
            return text
        norm = normalize_digits(text)
        emails, candidates = _detect(norm)
        if not emails and not candidates:
            return text

        repls: list[tuple[int, int, str]] = []
        for (s, e, addr) in emails:
            fake = self._fake_for_email(addr)
            if fake is not None:
                repls.append((s, e, fake))
        for (s, e, digits) in candidates:
            fake = self._fake_for_number(digits)
            if fake is not None:
                repls.append((s, e, _render_positional(text[s:e], fake)))
        return _apply(text, repls)

    # -- decode ------------------------------------------------------------

    def decode(self, text: str) -> DecodeResult:
        """Restore real values. ALWAYS active (never gated by ``enabled``).

        Detection mirrors encode's mechanics (normalize digits, join dash/space
        groups) but applies NO date/money exclusions — exact membership in the
        fake table is the sole decode criterion. A run that matches a known
        fake's kept prefix + length but differs in the tail raises a tripwire
        (never decoded by prefix).
        """
        norm = normalize_digits(text)
        repls: list[tuple[int, int, str]] = []
        tripwires: list[TripwireEvent] = []
        restored = 0

        # Emails first (exact match only; no prefix tripwire for emails).
        email_spans_raw = _find_email_spans(norm)
        for (s, e, addr) in email_spans_raw:
            real = self.fake_to_real.get(addr)
            if real is not None and "@" in real:
                repls.append((s, e, real))
                restored += 1

        # Numbers: join runs with NO date/money exclusion, skipping email spans.
        email_spans = [(s, e) for (s, e, _a) in email_spans_raw]
        for (s, e, digits) in _find_number_runs(norm, email_spans):
            if len(digits) < 5:
                continue
            real = self.fake_to_real.get(digits)
            if real is not None and "@" not in real:
                repls.append((s, e, real))
                restored += 1
            else:
                tw = self._tripwire_for_number(digits)
                if tw is not None:
                    tripwires.append(tw)

        return DecodeResult(_apply(text, repls), tripwires, restored)

    def _tripwire_for_number(self, digits: str) -> TripwireEvent | None:
        if digits in self.fake_to_real:  # exact fake → not a tripwire (decoded)
            return None
        length = len(digits)
        prefix = digits[:_keep_len(length)]
        if (length, prefix) in self._number_fake_prefixes:
            return TripwireEvent("number", prefix, length)
        return None

    # -- audit (detector-as-auditor, Layer 4) -----------------------------

    def audit(self, text: str) -> list[AuditHit]:
        """Convenience: :func:`audit` against this codec's own fake set."""
        return audit(text, self.fake_values)


# ---------------------------------------------------------------------------
# Module-level convenience API
# ---------------------------------------------------------------------------


def encode(text: str, codec: PrivacyCodec) -> str:
    """Mask ``text`` using ``codec`` (see :meth:`PrivacyCodec.encode`)."""
    return codec.encode(text)


def decode(text: str, codec: PrivacyCodec) -> DecodeResult:
    """Restore ``text`` using ``codec`` (see :meth:`PrivacyCodec.decode`)."""
    return codec.decode(text)


def audit(text: str, known_fakes: set[str]) -> list[AuditHit]:
    """Re-run the encode detector on (already-encoded) ``text``.

    Any candidate whose value is NOT a known fake is a leak candidate. Returns
    shape-only hits (kind + length), never values. Excluded dates/money are not
    candidates, so a correctly-encoded text yields ``[]``.
    """
    norm = normalize_digits(text)
    emails, candidates = _detect(norm)
    hits: list[AuditHit] = []
    for (_s, _e, addr) in emails:
        if addr not in known_fakes:
            hits.append(AuditHit("email", len(addr)))
    for (_s, _e, digits) in candidates:
        if digits not in known_fakes:
            hits.append(AuditHit("number", len(digits)))
    return hits
