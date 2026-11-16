"""The PDF text-layer fallback (agents/memory/ocr_extractor/text_layer.py) —
Arabic cleaning (presentation forms, tatweel, diacritic spaces, flipped
brackets, reversed Arabic-Indic digits), the per-document digit decision, and
``read_pages`` over a pypdf-like reader. Samples are taken from the raw text
layer of the target PDF (pages 81 and 602)."""
from __future__ import annotations

import pytest

from agents.memory.ocr_extractor.text_layer import (
    MIN_LETTERS,
    TextLayerPage,
    clean_text_layer,
    count_letters,
    digits_reversed,
    read_pages,
)

# Raw text layer, page 81 (presentation forms + tatweel).
PAGE_81_RAW = " ﻳﺨــﺮج ﻋــﻦ اﻻﺧﺘﺼــﺎص اﻟﻮﻻﺋــﻲ ﻟﻠﺠــﺎن اﻟﺘﺄﻣﻴﻨﻴــﺔ"
# Raw text layer, page 602 (reversed year «٩٣٤١» = ١٤٣٩, flipped brackets).
PAGE_602_RAW = (
    "٩٣٤١ه) /٩٠/ وحيث حدد موعد استلام نسخة قرار اللجنة الابتدائية محل التظلم "
    "بتاريخ (٨٠من حيث الشكل:"
)


# ---------------------------------------------------------------------------
# Presentation forms, tatweel, diacritic spaces, whitespace
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("ﻳﺨــﺮج ﻋــﻦ اﻻﺧﺘﺼــﺎص", "يخرج عن الاختصاص"),
        ("رﻗــــــــﻢ اﻟــــــﺪﻋــــﻮى", "رقم الدعوى"),
        (PAGE_81_RAW, "يخرج عن الاختصاص الولائي للجان التأمينية"),
    ],
)
def test_presentation_forms_and_tatweel(raw, expected):
    assert clean_text_layer(raw) == expected


def test_tatweel_dropped_from_base_letters():
    assert clean_text_layer("رقــــــم القــرار") == "رقم القرار"


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("المد َّعون", "المدَّعون"),
        ("المد\tَّعون", "المدَّعون"),
        ("المد   َّعون", "المدَّعون"),
        # shadda medial presentation form (tatweel + shadda after NFKC)
        ("المد ﹽعون", "المدّعون"),
    ],
)
def test_space_before_diacritic_closed(raw, expected):
    assert clean_text_layer(raw) == expected


def test_space_before_arabic_indic_digit_kept():
    # Digits are not diacritics: the space before them must survive.
    assert clean_text_layer("بتاريخ ١٤٣٩ه") == "بتاريخ ١٤٣٩ه"


def test_whitespace_collapsed_and_blank_lines_dropped():
    raw = "  رقم    الدعوى  \n\n   \n  الحكم  الابتدائي  "
    assert clean_text_layer(raw) == "رقم الدعوى\nالحكم الابتدائي"


@pytest.mark.parametrize("raw", ["", None, "\n\n", "   \t  "])
def test_empty_input(raw):
    assert clean_text_layer(raw) == ""
    assert clean_text_layer(raw, reverse_digits=True) == ""


# ---------------------------------------------------------------------------
# Brackets
# ---------------------------------------------------------------------------


def test_flipped_brackets_mirrored_on_arabic_page():
    raw = (
        "الدعوى المقامة من )بعض ورثة المورث المتوفى( ضد الشركة\n"
        "وحيث إن المدعين )الورثة( قدموا ]ملحق[ المستندات"
    )
    assert clean_text_layer(raw) == (
        "الدعوى المقامة من (بعض ورثة المورث المتوفى) ضد الشركة\n"
        "وحيث إن المدعين (الورثة) قدموا [ملحق] المستندات"
    )


def test_single_flipped_pair_mirrored():
    assert clean_text_layer(")بعض ورثة المورث المتوفى(") == "(بعض ورثة المورث المتوفى)"


def test_logical_brackets_left_alone():
    raw = "الدعوى المقامة من (بعض ورثة) ضد الشركة\nوفق (المادة الخامسة) من النظام"
    assert clean_text_layer(raw) == raw


def test_bracket_tie_left_alone():
    # One flipped pair and one logical pair: flipped must OUTNUMBER logical.
    raw = "الدعوى المقامة من )بعض الورثة( ضد الشركة\nوفق (المادة الخامسة) من النظام"
    assert clean_text_layer(raw) == raw


def test_latin_dominant_line_never_mirrored():
    raw = (
        "الدعوى المقامة من )بعض ورثة المورث المتوفى( ضد الشركة\n"
        "Case reference )see the annex( page"
    )
    assert clean_text_layer(raw) == (
        "الدعوى المقامة من (بعض ورثة المورث المتوفى) ضد الشركة\n"
        "Case reference )see the annex( page"
    )


def test_latin_only_page_never_mirrored():
    raw = "Case reference )see the annex( page\nAnother )flipped looking( line"
    assert clean_text_layer(raw) == raw


# ---------------------------------------------------------------------------
# Arabic-Indic digits
# ---------------------------------------------------------------------------


def test_reversed_year_put_back_in_arabic_line():
    assert clean_text_layer("بتاريخ ٩٣٤١ه", reverse_digits=True) == "بتاريخ ١٤٣٩ه"


def test_digit_only_line_not_reversed():
    raw = "صدر القرار بتاريخ ٩٣٤١ه\n٦٠٢"
    assert clean_text_layer(raw, reverse_digits=True) == "صدر القرار بتاريخ ١٤٣٩ه\n٦٠٢"


def test_latin_digits_never_change():
    raw = "القرار رقم 410654 بتاريخ ٩٣٤١ه"
    assert clean_text_layer(raw, reverse_digits=True) == "القرار رقم 410654 بتاريخ ١٤٣٩ه"
    assert clean_text_layer(raw) == raw


def test_single_digit_untouched():
    assert clean_text_layer("المادة ٥ من النظام", reverse_digits=True) == "المادة ٥ من النظام"


def test_arabic_indic_digits_in_latin_line_not_reversed():
    raw = "Invoice number ٩٣٤١ was issued"
    assert clean_text_layer(raw, reverse_digits=True) == raw


def test_reverse_digits_false_changes_nothing():
    raw = "بتاريخ ٩٣٤١ه\nالصفحة ٦٢\n٦٠٢\nرقم 410654"
    assert clean_text_layer(raw) == raw
    assert clean_text_layer(raw, reverse_digits=False) == raw


def test_every_run_in_line_reversed_separately():
    assert (
        clean_text_layer("القرار ٥١٠/ر/٩٣٤١ه", reverse_digits=True)
        == "القرار ٠١٥/ر/١٤٣٩ه"
    )


def test_page_602_year_restored():
    out = clean_text_layer(PAGE_602_RAW, reverse_digits=True)
    assert "١٤٣٩ه" in out
    assert "٩٣٤١" not in out


# ---------------------------------------------------------------------------
# digits_reversed — the per-document decision
# ---------------------------------------------------------------------------


def test_digits_reversed_true_for_reversed_years():
    pages = [
        "صدر القرار بتاريخ ٩٣٤١ه",
        "وفي عام ٨٣٤١ه صدر الحكم",
        "الموافق ٠٢٠٢م",
    ]
    assert digits_reversed(pages) is True


def test_digits_reversed_false_for_straight_years():
    assert digits_reversed(["صدر القرار بتاريخ ١٤٣٩ه", "الموافق ٢٠٢٤م"]) is False


@pytest.mark.parametrize(
    "pages",
    [
        [],
        [""],
        [None, ""],
        ["نص بلا أي سنة", "المادة ٥ والفقرة ٢٦"],
    ],
)
def test_digits_reversed_false_without_years(pages):
    assert digits_reversed(pages) is False


def test_digits_reversed_majority_wins():
    straight_two = ["بتاريخ ١٤٣٩ه", "بتاريخ ١٤٣٨ه", "بتاريخ ٩٣٤١ه"]
    reversed_two = ["بتاريخ ٩٣٤١ه", "بتاريخ ٨٣٤١ه", "بتاريخ ١٤٣٩ه"]
    assert digits_reversed(straight_two) is False
    assert digits_reversed(reversed_two) is True


def test_digits_reversed_ignores_non_arabic_lines():
    # A digit-only line and a Latin-dominant line carry no evidence.
    assert digits_reversed(["٩٣٤١", "Year ٩٣٤١ in a Latin line"]) is False


def test_digits_reversed_reads_presentation_forms():
    # The raw line is all presentation forms; NFKC makes it an Arabic line.
    assert digits_reversed(["ﺑﺘﺎرﻳﺦ ٩٣٤١ه"]) is True


def test_digits_reversed_on_page_602_sample():
    assert digits_reversed([PAGE_602_RAW]) is True


# ---------------------------------------------------------------------------
# read_pages over a pypdf-like reader
# ---------------------------------------------------------------------------


class _FakePage:
    def __init__(self, text=None, exc: Exception | None = None):
        self._text = text
        self._exc = exc

    def extract_text(self):
        if self._exc is not None:
            raise self._exc
        return self._text


class _FakeReader:
    def __init__(self, pages):
        self.pages = pages


YEAR_PAGE = "صدر القرار في الدعوى المقامة من الورثة بتاريخ ٩٣٤١ه"
CONTENTS_PAGE = "فهرس المحتويات\nالباب الأول أحكام عامة ٦٢"


@pytest.fixture
def reader():
    return _FakeReader(
        [
            _FakePage(PAGE_81_RAW),                         # 1 usable
            _FakePage(exc=RuntimeError("bad xref")),        # 2 pypdf fails
            _FakePage(YEAR_PAGE),                           # 3 reversed year
            _FakePage(CONTENTS_PAGE),                       # 4 no years
            _FakePage(None),                                # 5 image-only
            _FakePage("ص ٥"),                               # 6 too few letters
        ]
    )


def test_read_pages_numbering_and_order(reader):
    out = read_pages(reader, [3, 1])
    assert [p.page_no for p in out] == [3, 1]
    assert all(isinstance(p, TextLayerPage) for p in out)
    assert out[1].text == "يخرج عن الاختصاص الولائي للجان التأمينية"


def test_read_pages_failing_page_does_not_stop_the_rest(reader):
    out = read_pages(reader, [1, 2, 3])
    assert [p.page_no for p in out] == [1, 2, 3]
    bad = out[1]
    assert bad.text == "" and bad.letters == 0 and not bad.usable
    assert out[0].usable and out[2].usable


def test_read_pages_out_of_range_page_is_empty(reader):
    out = read_pages(reader, [1, 99])
    assert out[1] == TextLayerPage(page_no=99, text="", letters=0)
    assert not out[1].usable
    assert out[0].usable


def test_read_pages_none_text_is_empty(reader):
    (page,) = read_pages(reader, [5])
    assert page == TextLayerPage(page_no=5, text="", letters=0)


def test_read_pages_letters_and_usable(reader):
    out = read_pages(reader, [1, 6])
    for p in out:
        assert p.letters == count_letters(p.text)
    assert out[0].usable
    assert out[1].letters < MIN_LETTERS and not out[1].usable


def test_read_pages_digit_decision_shared_across_pages(reader):
    out = {p.page_no: p for p in read_pages(reader, [3, 4])}
    assert "١٤٣٩ه" in out[3].text
    # Page 4 has no year of its own, but page 3 proves the document reverses.
    assert out[4].text.endswith("أحكام عامة ٢٦")


def test_read_pages_page_alone_keeps_its_digits(reader):
    (page,) = read_pages(reader, [4])
    assert page.text.endswith("أحكام عامة ٦٢")


def test_read_pages_failing_page_does_not_affect_digit_decision(reader):
    out = {p.page_no: p for p in read_pages(reader, [2, 3, 4])}
    assert out[4].text.endswith("أحكام عامة ٢٦")


def test_read_pages_empty_request(reader):
    assert read_pages(reader, []) == []


def test_read_pages_page_zero_is_empty(reader):
    # page 0 is not a 1-based page: it must not wrap to the LAST page.
    (page,) = read_pages(reader, [0])
    assert page.text == "" and not page.usable


# ---------------------------------------------------------------------------
# TextLayerPage / count_letters
# ---------------------------------------------------------------------------


def test_usable_threshold():
    assert MIN_LETTERS == 20
    assert not TextLayerPage(page_no=1, text="", letters=MIN_LETTERS - 1).usable
    assert TextLayerPage(page_no=1, text="", letters=MIN_LETTERS).usable


def test_count_letters_arabic_and_latin_only():
    assert count_letters("abc أبت 123 ٤٥٦ ()") == 6
    assert count_letters("") == 0
