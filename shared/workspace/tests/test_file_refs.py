"""The [F1·S7] / [F1] grammar and its text form — shared/workspace/file_refs.py
(aggregator_file_refs.md §4.1 / §4.7, D11–D12: F + the file's WI number). The same
cases run against the web grammar in frontend/lib/contracts/__tests__/file-refs.test.ts.
All text invented."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from shared.workspace import file_refs as fr


@pytest.mark.parametrize(
    "raw, canon",
    [
        ("[F1·S7]", "F1.S7"),
        ("F1.S7", "F1.S7"),
        ("[F-1·S7]", "F1.S7"),
        ("f1.s07", "F1.S7"),
        ("f-3-s07", "F3.S7"),
        ("[F 1 S7]", "F1.S7"),
        ("[F1S7]", "F1.S7"),
        ("[ F1 · S7 ]", "F1.S7"),
        ("F1، S7", "F1.S7"),
        ("F_1·S7", "F1.S7"),
        ("[F2]", "F2"),
        ("F02", "F2"),
        ("F12", "F12"),
        ("[F١·S٧]", "F1.S7"),          # Arabic-Indic digits read as Western
        ("[F۳·S۷]", "F3.S7"),          # Extended Arabic-Indic too
    ],
)
def test_parse_any_spelling(raw, canon):
    assert fr.parse_file_ref(raw) == canon


@pytest.mark.parametrize(
    "raw",
    [
        "[4]", "4", "S7", "[F0]", "[F1·S0]", "", None, "F1.S7.S8",
        "[D1·S7]", "D2", "[WI-1·S7]", "WI-1", "F", "FF1", "[PDF 3]",
    ],
)
def test_parse_rejects_non_refs(raw):
    assert fr.parse_file_ref(raw) is None


def test_format_and_split():
    assert fr.format_file_ref(1, 7) == "F1.S7"
    assert fr.format_file_ref(2) == "F2"
    assert fr.format_file_tag(3, 7) == "F3·S7"
    assert fr.format_file_tag(4) == "F4"
    assert fr.split_file_ref("F1.S7") == (1, 7)
    assert fr.split_file_ref("[F2]") == (2, None)
    assert fr.split_file_ref("x") is None
    # The tag the aggregator is shown parses back to its canonical form.
    assert fr.parse_file_ref(fr.format_file_tag(3, 7)) == fr.format_file_ref(3, 7)


def test_tags_in_order_of_first_mention_and_never_library_numbers():
    text = "نص [3] «اقتباس» [F1·S7]، ثم [F2] و[4,17] ثم [F1.S7] و[f-1-s9][F1·S7]"
    assert fr.file_tags(text) == ["F1.S7", "F2", "F1.S9"]
    assert fr.file_tags("[4] [4,17] [٤]") == []
    # The old forms and loose words never read as file tags.
    assert fr.file_tags("[D1·S7] [D2] [WI-3·S7] [WI-3] F3 بلا أقواس [PDF 3]") == []
    assert not fr.has_file_tags("لا وسوم [12]")


def test_several_refs_in_one_bracket():
    assert fr.file_tags("انظر [F1·S7، F1·S9, f1-s07]") == ["F1.S7", "F1.S9"]
    assert fr.file_tags("انظر [F1، F2]") == ["F1", "F2"]


def test_library_citation_regex_never_matches_a_file_tag():
    from agents.deep_search_v4.aggregator.postvalidator import extract_cited_numbers

    text = "«…» [F1·S7] وفق المادة 77 [3] و[F2][1,2]"
    assert extract_cited_numbers(text) == [1, 2, 3]


def test_label_forms():
    assert fr.file_ref_label({"section_seq": 7, "doc_title": "الحكم المرفق"}) == "(القسم 7 من الحكم المرفق)"
    assert fr.file_ref_label({"section_seq": None, "doc_title": "عقد الإيجار"}) == "(عقد الإيجار)"
    assert fr.file_ref_label({"section_seq": 2, "doc_title": "  "}) == f"(القسم 2 من {fr.DEFAULT_DOC_TITLE})"


ROWS = [
    {"ref_id": "F1.S7", "section_seq": 7, "doc_title": "الحكم المرفق"},
    {"ref_id": "F2", "section_seq": None, "doc_title": "العقد"},
]


def test_replace_turns_tags_into_words_and_drops_unknown_ones():
    text = "«لم يقدّم ما يثبت السداد» [F1·S7]، وفي [F2] والقسم [F9·S1] غير موجود [3]."
    out = fr.replace_file_tags(text, ROWS)
    assert out == "«لم يقدّم ما يثبت السداد» (القسم 7 من الحكم المرفق)، وفي (العقد) والقسم غير موجود [3]."


def test_replace_a_bracket_with_several_refs():
    out = fr.replace_file_tags("انظر [F1·S7، F2] و[F9، F2].", ROWS)
    assert out == "انظر (القسم 7 من الحكم المرفق) (العقد) و(العقد)."


def test_side_by_side_tags_keep_a_space():
    rows = ROWS + [{"ref_id": "F1.S9", "section_seq": 9, "doc_title": "الحكم المرفق"}]
    out = fr.replace_file_tags("«…» [F1·S7][F1·S9].", rows)
    assert out == "«…» (القسم 7 من الحكم المرفق) (القسم 9 من الحكم المرفق)."


def test_strip_removes_tags_and_the_space_before():
    assert fr.strip_file_tags("«…» [F1·S7]، و[F2] [5].") == "«…»، و [5]."
    same = "بلا وسوم [1]"
    assert fr.strip_file_tags(same) is same
    assert fr.replace_file_tags(same, ROWS) is same


class _Table:
    def __init__(self, rows, log):
        self._rows, self._log, self._f = rows, log, {}

    def select(self, cols):
        self._log.append(("select", cols))
        return self

    def eq(self, col, val):
        self._f[col] = val
        return self

    def order(self, col):
        self._order = col
        return self

    def execute(self):
        rows = [r for r in self._rows if all(r.get(k) == v for k, v in self._f.items())]
        return SimpleNamespace(data=sorted(rows, key=lambda r: r[self._order]))


class _Sb:
    def __init__(self, rows):
        self.rows, self.log = rows, []

    def table(self, name):
        assert name == fr.FILE_REFS_TABLE
        self.log.append(("table", name))
        return _Table(self.rows, self.log)


def test_labelled_report_text_reads_only_when_there_is_a_tag():
    sb = _Sb([dict(r, wi_id="wi", n=i) for i, r in enumerate(ROWS, 1)])
    assert fr.labelled_report_text(sb, "wi", "بلا وسوم [1]") == "بلا وسوم [1]"
    assert sb.log == []
    assert fr.labelled_report_text(sb, "wi", "«…» [F1·S7]") == "«…» (القسم 7 من الحكم المرفق)"
    assert ("select", fr.FILE_REF_COLUMNS) in sb.log


def test_load_rows_in_n_order():
    sb = _Sb([dict(ROWS[1], wi_id="wi", n=2), dict(ROWS[0], wi_id="wi", n=1)])
    assert [r["ref_id"] for r in fr.load_file_ref_rows(sb, "wi")] == ["F1.S7", "F2"]


def test_load_rows_never_raises():
    class Boom:
        def table(self, name):
            raise RuntimeError("relation does not exist")

    assert fr.load_file_ref_rows(Boom(), "wi") == []
    assert fr.labelled_report_text(Boom(), "wi", "«…» [F1·S7] [2]") == "«…» [2]"
