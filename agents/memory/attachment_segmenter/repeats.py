"""Repeat detection in code (P5): a later near-identical copy → ``value='repeat'``.

One hearing record held its whole investigation section twice (97–100%
identical) and the model only sometimes noticed (plan §2.2). Delivery never sends
``repeat`` sections, so the second copy stops spending the caps.

Method — linear in the document's words (an 80k-char document is ~15k words):

1. Words are normalised (Arabic-Indic digits, diacritics, tatweel, alef / yaa /
   taa-marbuta forms) so OCR spelling noise does not break a match.
2. Every word 8-gram is indexed at its FIRST occurrence (8 = Phase 0's measured
   detector, ``phase0_results.md`` §8: long enough that formulaic legal phrases do
   not match by chance).
3. A word of segment S is *matched* when some 8-gram containing it first
   occurred entirely before S starts. Similarity of S = matched words / words.
   One OCR slip unmatches ~1 word, not the whole passage.
4. Consecutive segments with similarity ≥ ``REPEAT_MIN_SIMILARITY`` form a run;
   a run of ≥ ``REPEAT_MIN_CHARS`` characters is a repeat, and its segments
   become ``repeat`` — the LATER copy only: a segment is compared with text
   before it, never with itself, so a table whose rows look alike is not a
   repeat of itself.

Guard: a later copy is NOT re-rated when it is rated higher than the earlier
text it repeats (rank core > supporting = metadata > boilerplate) — measured as
a share (``REPEAT_SOURCE_SHARE``) of its shingles, so one chance match in early
text cannot veto a real repeat. Otherwise a judgment whose operative part is
restated at the end (rated ``core``) after a first copy rated ``metadata`` would
lose the only copy deep search receives.

Phase 0 (§8) found exactly that case — the hearing record's formal charge (core) is
a later near-copy of the investigation's conclusion (not core in one run) — and
proposed the mirror fix: drop the copy and PROMOTE its source to core. Both deliver
the text once; this guard keeps the model's ratings untouched (D11: no code
re-ranking) and the text stays where the model rated it core. Copies of copies
resolve to the original by construction (every shingle points at its FIRST
occurrence).
"""
from __future__ import annotations

import re
import unicodedata
from typing import Protocol, Sequence

from .paragraphs import Paragraph

REPEAT_MIN_SIMILARITY = 0.95
REPEAT_MIN_CHARS = 500
SHINGLE_WORDS = 8
#: Guard: at least this share of a later copy's matched shingles must first
#: occur in text rated at least as high as the copy.
REPEAT_SOURCE_SHARE = 0.9

#: Delivery rank: deep search reads core; the writer reads core, supporting and
#: metadata (until the metadata agent exists); nobody reads boilerplate.
VALUE_RANK = {"core": 2, "supporting": 1, "metadata": 1, "boilerplate": 0, "repeat": 0}

_WORD_RE = re.compile(r"\w+", re.UNICODE)
_DIACRITICS_RE = re.compile("[\u0610-\u061a\u064b-\u065f\u0670\u06d6-\u06ed\u0640]")
_FOLD = str.maketrans({
    "أ": "ا", "إ": "ا", "آ": "ا", "ٱ": "ا",
    "ى": "ي", "ئ": "ي", "ؤ": "و", "ة": "ه",
    **{chr(0x0660 + i): str(i) for i in range(10)},
    **{chr(0x06F0 + i): str(i) for i in range(10)},
})


class _Ranged(Protocol):
    from_p: int
    to_p: int
    value: str


def normalize_words(text: str) -> list[str]:
    text = unicodedata.normalize("NFKC", text or "")
    text = _DIACRITICS_RE.sub("", text).translate(_FOLD).lower()
    return _WORD_RE.findall(text)


def find_repeats(segments: Sequence[_Ranged], paragraphs: Sequence[Paragraph]) -> list[int]:
    """Indices of ``segments`` (a full, ordered, gap-free map) to re-rate ``repeat``."""
    if not segments or not paragraphs:
        return []
    k = SHINGLE_WORDS
    words: list[str] = []
    seg_word_start: list[int] = []
    seg_word_end: list[int] = []
    seg_chars: list[int] = []
    word_seg: list[int] = []
    for i, seg in enumerate(segments):
        seg_word_start.append(len(words))
        chars = 0
        for p in range(seg.from_p, seg.to_p + 1):
            toks = normalize_words(paragraphs[p - 1].text)
            words.extend(toks)
            word_seg.extend([i] * len(toks))
            chars += len(paragraphs[p - 1].text)
        seg_word_end.append(len(words))
        seg_chars.append(chars)
    w = len(words)
    if w < k:
        return []

    # first occurrence of every k-gram, and for each gram START the first
    # occurrence's start (== itself when this IS the first occurrence)
    first_seen: dict[tuple[str, ...], int] = {}
    origin: list[int] = []
    for i in range(w - k + 1):
        gram = tuple(words[i:i + k])
        origin.append(first_seen.setdefault(gram, i))

    # for each word j: the earliest END of a prior occurrence of any gram
    # containing it (a word is matched in S when that end lies before S)
    best_end = [w + k] * w
    for i, o in enumerate(origin):
        if o == i:
            continue  # first occurrence: matches nothing before it
        end = o + k - 1
        for j in range(i, i + k):
            if end < best_end[j]:
                best_end[j] = end

    similarity: list[float] = []
    source_ok: list[bool] = []
    for i, seg in enumerate(segments):
        ws, we = seg_word_start[i], seg_word_end[i]
        if we <= ws:
            similarity.append(0.0)
            source_ok.append(False)
            continue
        matched = sum(1 for j in range(ws, we) if best_end[j] < ws)
        similarity.append(matched / (we - ws))
        # the guard: where do this segment's grams first occur? A stray chance
        # match in earlier text must not decide it, so it is a share, not a min.
        rank = VALUE_RANK.get(seg.value, 0)
        ok = total = 0
        for g in range(ws, min(we, len(origin))):
            o = origin[g]
            if o != g and o + k - 1 < ws:
                total += 1
                ok += VALUE_RANK.get(segments[word_seg[o]].value, 0) >= rank
        source_ok.append(total > 0 and ok >= REPEAT_SOURCE_SHARE * total)

    marked: list[int] = []
    run: list[int] = []

    def close_run() -> None:
        if run and sum(seg_chars[i] for i in run) >= REPEAT_MIN_CHARS:
            marked.extend(i for i in run if source_ok[i])
        run.clear()

    for i in range(len(segments)):
        if similarity[i] >= REPEAT_MIN_SIMILARITY:
            run.append(i)
        else:
            close_run()
    close_run()
    return marked


__all__ = ["REPEAT_MIN_CHARS", "REPEAT_MIN_SIMILARITY", "REPEAT_SOURCE_SHARE", "VALUE_RANK", "find_repeats", "normalize_words"]
