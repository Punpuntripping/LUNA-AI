"""Repeat detection (P5): later near-identical copies become ``repeat``."""
from __future__ import annotations

import random
import time

from agents.memory.attachment_segmenter.models import Segment
from agents.memory.attachment_segmenter.paragraphs import paragraphize
from agents.memory.attachment_segmenter.repeats import find_repeats, normalize_words

_rng = random.Random(7)
_VOCAB = [
    "المدعي", "المدعى", "عليه", "الدائرة", "وحيث", "إن", "قرر", "التحقيق", "الشاهد", "أفاد",
    "بأنه", "شاهد", "المتهم", "في", "يوم", "الموقع", "وقام", "بسؤال", "وأجاب", "أنكر",
    "التهمة", "المنسوبة", "إليه", "وطلب", "مهلة", "لإحضار", "بينته", "فأمهل", "الجلسة", "التالية",
]


def _sentence(n: int) -> str:
    return " ".join(_rng.choice(_VOCAB) for _ in range(n)) + f" {_rng.randint(1000, 99999)}"


def _block(lines: int, words: int = 14) -> list[str]:
    return [_sentence(words) for _ in range(lines)]


def seg(f, t, value="core"):
    return Segment(from_p=f, to_p=t, label="evidence", value=value, what="")


def test_normalize_folds_ocr_variants():
    assert normalize_words("أَحْمَد ـــ إلى ٢٠٢٤") == normalize_words("احمد الي 2024")


def test_second_copy_of_a_section_is_a_repeat():
    investigation = _block(8)  # ~800 chars
    lines = _block(4) + investigation + _block(4) + investigation + _block(3)
    paras = paragraphize("\n".join(lines))
    segs = [seg(1, 4, "metadata"), seg(5, 12), seg(13, 16, "supporting"), seg(17, 24), seg(25, 27)]
    assert find_repeats(segs, paras) == [3]


def test_ocr_noise_still_matches():
    investigation = _block(8)
    noisy = [line.replace("المتهم", "المتهـم").replace("إليه", "اليه") for line in investigation]
    noisy[3] = noisy[3] + " كلمةزائدة"
    lines = _block(4) + investigation + _block(4) + noisy
    paras = paragraphize("\n".join(lines))
    segs = [seg(1, 4), seg(5, 12), seg(13, 16), seg(17, 24)]
    assert find_repeats(segs, paras) == [3]


def test_a_copy_split_over_several_short_segments_is_one_run():
    investigation = _block(8)
    lines = _block(4) + investigation + _block(4) + investigation
    paras = paragraphize("\n".join(lines))
    # second copy split into 4 two-paragraph segments (~200 chars each, < 500)
    segs = [seg(1, 4), seg(5, 12), seg(13, 16), seg(17, 18), seg(19, 20), seg(21, 22), seg(23, 24)]
    assert find_repeats(segs, paras) == [3, 4, 5, 6]


def test_short_repeats_are_left_alone():
    header = ["المحكمة الجزائية بالرياض", "الدائرة الثالثة"]
    lines = header + _block(6) + header + _block(6)
    paras = paragraphize("\n".join(lines))
    segs = [seg(1, 2, "metadata"), seg(3, 8), seg(9, 10, "boilerplate"), seg(11, 16)]
    assert find_repeats(segs, paras) == []


def test_partly_repeated_segment_is_kept():
    investigation = _block(8)
    lines = _block(4) + investigation + investigation + _block(10)
    paras = paragraphize("\n".join(lines))
    # the later segment holds the repeat AND as much new text
    segs = [seg(1, 4), seg(5, 12), seg(13, 30)]
    assert find_repeats(segs, paras) == []


def test_a_table_of_lookalike_rows_is_not_its_own_repeat():
    rows = ["| قسط شهري | 1,500 | مدفوع |"] * 60
    paras = paragraphize("\n".join(_block(3) + rows))
    segs = [seg(1, 3, "metadata"), seg(4, 63, "supporting")]
    assert find_repeats(segs, paras) == []


def test_a_higher_rated_later_copy_is_not_demoted():
    ruling = _block(7)
    lines = ruling + _block(10) + ruling
    paras = paragraphize("\n".join(lines))
    # the first copy was rated metadata, the later one core: keep the core one
    segs = [seg(1, 7, "metadata"), seg(8, 17, "supporting"), seg(18, 24, "core")]
    assert find_repeats(segs, paras) == []
    # same rating → the later copy is the repeat
    segs = [seg(1, 7, "core"), seg(8, 17, "supporting"), seg(18, 24, "core")]
    assert find_repeats(segs, paras) == [2]


def test_linear_enough_for_an_80k_document():
    lines = _block(700, words=20)  # ~95k chars
    paras = paragraphize("\n".join(lines + lines[:100]))
    n = len(paras)
    segs = [seg(i, min(i + 9, n)) for i in range(1, n + 1, 10)]
    t0 = time.perf_counter()
    out = find_repeats(segs, paras)
    assert time.perf_counter() - t0 < 2.0
    assert out and all(segs[i].from_p > 700 for i in out)


def test_empty_inputs():
    assert find_repeats([], []) == []
    paras = paragraphize("نص قصير")
    assert find_repeats([seg(1, 1)], paras) == []
