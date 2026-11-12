"""Paragraphizer rule v2 — reproducible numbering, exact offsets, page attribution."""
from __future__ import annotations

import random
import re

import pytest

from agents.memory.attachment_segmenter import paragraphs as P
from agents.memory.attachment_segmenter.paragraphs import PageRow, paragraphize, pid
from agents.memory.ocr_extractor.mistral_ocr import join_pages

# ── the tested rule, verbatim from experiments/make_paras.py (Phase 0) ──────
_MAX_BY_TYPE = {"judgment": 500, "hearing_minutes": 500}
_SENT = re.compile(r"[.؟!:؛](?=\s)")
_CLAUSE = re.compile(r"[،,;](?=\s)")


def _last_break(line, rx, lo, hi):
    best = -1
    for m in rx.finditer(line, 0, hi):
        end = m.end()
        if lo < end <= hi:
            best = end
    return best


def _split_line(line, mx):
    out = []
    while len(line) > mx:
        cut = _last_break(line, _SENT, int(mx * 0.4), mx)
        if cut < 0:
            cut = _last_break(line, _CLAUSE, int(mx * 0.4), mx)
        if cut < 0:
            sp = line.rfind(" ", 0, mx)
            cut = sp if sp > mx // 2 else mx
        out.append(line[:cut].strip())
        line = line[cut:].strip()
    if line:
        out.append(line)
    return [p for p in out if p]


def reference(text: str, doc_type: str = "") -> list[str]:
    mx = _MAX_BY_TYPE.get(doc_type, 1200)
    out = []
    for line in text.split("\n"):
        line = line.strip()
        if line:
            out.extend(_split_line(line, mx))
    return out


# ── fixtures ─────────────────────────────────────────────────────────────────

_W = ["وحيث", "إن", "المدعي", "طلب", "إلزام", "المدعى", "عليه", "بسداد", "المبلغ", "المتبقي"]


def words(n: int, rng: random.Random | None = None) -> str:
    rng = rng or random.Random(n)
    return " ".join(rng.choice(_W) for _ in range(n))


DOC = (
    "  المملكة العربية السعودية  \n"
    "\n"
    "وزارة العدل\r\n"
    "\t\n"
    + " ".join(f"{words(30)}." if i % 3 == 0 else f"{words(25)}،" for i in range(12))
    + "\n\n"
    + "ح" * 1300
    + "\n"
    "الحكم: رفض الدعوى؛ وإلزام المدعي بالمصاريف!   \n"
)


def assert_exact(text: str, paras) -> None:
    for p in paras:
        assert text[p.char_start:p.char_end] == p.text
        assert p.text == p.text.strip() and p.text
    assert [p.n for p in paras] == list(range(1, len(paras) + 1))
    for a, b in zip(paras, paras[1:]):
        assert a.char_end <= b.char_start


# ── the rule ─────────────────────────────────────────────────────────────────


def test_version_and_maxima():
    assert P.PARAGRAPH_RULE_VERSION == "v2-sentence-500j-1200"
    assert P.max_paragraph_chars("judgment") == 500
    assert P.max_paragraph_chars("hearing_minutes") == 500
    for t in ("contract", "memo", "claim", "expert_report", "letter", "financial", "chat", "", None):
        assert P.max_paragraph_chars(t) == 1200


@pytest.mark.parametrize("doc_type", ["judgment", "hearing_minutes", "contract", "letter", ""])
def test_matches_the_tested_rule_with_exact_offsets(doc_type):
    paras = paragraphize(DOC, doc_type=doc_type)
    assert [p.text for p in paras] == reference(DOC, doc_type)
    assert_exact(DOC, paras)
    assert all(len(p.text) <= P.max_paragraph_chars(doc_type) for p in paras)


def test_fuzz_against_the_tested_rule():
    rng = random.Random(11)
    toks = _W + ["طلب.", "الحكم؟", "نعم!", "التالي:", "أولاً؛", "ثانياً،", "a,", "b;", ".", "،", "؛", ":",
                 "  ", "\t", " ", "ـــ", "٣٢١"]
    for _ in range(400):
        text = "\n".join(
            rng.choice(["", " ", "\t"]) + rng.choice([" ", "", "  "]).join(
                rng.choice(toks) for _ in range(rng.choice([0, 3, 60, 200, 700]))
            ) + rng.choice(["", " ", "\r"])
            for _ in range(rng.randint(1, 5))
        )
        for doc_type in ("judgment", "contract"):
            paras = paragraphize(text, doc_type=doc_type)
            assert [p.text for p in paras] == reference(text, doc_type)
            assert_exact(text, paras)


def test_sentence_end_first_and_it_stays_left():
    left = words(40)            # ~230 chars, inside [200, 500]
    line = f"{left}، {words(10)}. {words(60)}"
    paras = paragraphize(line, doc_type="judgment")
    assert paras[0].text.endswith(".")
    assert "،" in paras[0].text  # the clause break before it was not used
    assert line[paras[1].char_start - 1] == " " and line[paras[1].char_start] != " "
    assert_exact(line, paras)


@pytest.mark.parametrize("mark", ["؟", "!", ":", "؛"])
def test_every_arabic_sentence_end(mark):
    line = f"{words(45)}{mark} {words(60)}"
    first = paragraphize(line, doc_type="judgment")[0]
    assert first.text.endswith(mark)
    assert first.char_end == line.index(mark) + 1


def test_clause_break_when_no_sentence_end():
    line = f"{words(45)}، {words(60)}"
    paras = paragraphize(line, doc_type="judgment")
    assert paras[0].text.endswith("،")
    assert_exact(line, paras)


def test_space_when_no_punctuation_and_hard_cut_without_space():
    line = words(120)
    paras = paragraphize(line, doc_type="judgment")
    assert all(250 < len(p.text) <= 500 for p in paras[:-1])
    assert_exact(line, paras)
    hard = paragraphize("ح" * 1300, doc_type="judgment")
    assert [len(p.text) for p in hard] == [500, 500, 300]


def test_a_break_outside_the_window_is_not_used():
    # a sentence end at 10% of the max is too early: fall back to a space
    line = f"{words(8)}. {words(100)}"
    first = paragraphize(line, doc_type="judgment")[0]
    assert not first.text.endswith(".")
    assert len(first.text) > 250


def test_punctuation_without_whitespace_after_is_not_a_break():
    line = f"{words(45)}.{words(60)}"  # «.» glued to the next word
    assert not paragraphize(line, doc_type="judgment")[0].text.endswith(".")


def test_other_types_keep_long_paragraphs():
    line = f"{words(45)}. {words(60)}"  # ~620 chars
    assert len(paragraphize(line, doc_type="contract")) == 1
    assert len(paragraphize(line, doc_type="judgment")) == 2


def test_reproducible_from_content_alone():
    a = paragraphize(DOC, doc_type="judgment")
    assert a == paragraphize(DOC, doc_type="judgment")
    pages = [PageRow(1, 0, DOC[:40])]
    c = paragraphize(DOC, doc_type="judgment", pages=pages)
    assert [(p.char_start, p.char_end) for p in c] == [(p.char_start, p.char_end) for p in a]


def test_per_type_max_is_one_constant(monkeypatch):
    monkeypatch.setitem(P.MAX_PARAGRAPH_CHARS_BY_TYPE, "letter", 500)
    assert all(len(p.text) <= 500 for p in paragraphize(DOC, doc_type="letter"))


def test_pid_format():
    assert pid(1) == "P001"
    assert pid(42) == "P042"
    assert pid(1234) == "P1234"


# ── pages ────────────────────────────────────────────────────────────────────


def test_pages_from_ocr_offsets():
    md = ["  صفحة أولى\nسطر ثان", "صفحة ثانية", "", "صفحة رابعة\n\n"]
    content, offsets = join_pages(md)
    rows = [PageRow(i + 1, off, m) for i, (off, m) in enumerate(zip(offsets, md))]
    paras = paragraphize(content, pages=rows)
    assert [(p.text, p.page) for p in paras] == [
        ("صفحة أولى", 1),
        ("سطر ثان", 1),
        ("صفحة ثانية", 2),
        ("صفحة رابعة", 4),
    ]


def test_split_paragraphs_keep_their_page():
    md = [f"{words(45)}. {words(60)}", f"{words(45)}؛ {words(60)}"]
    content, offsets = join_pages(md)
    rows = [PageRow(i + 1, off, m) for i, (off, m) in enumerate(zip(offsets, md))]
    paras = paragraphize(content, doc_type="judgment", pages=rows)
    assert [p.page for p in paras] == [1, 1, 2, 2]
    assert_exact(content, paras)


def test_stale_pages_are_ignored():
    content = "سطر أول\n\nسطر ثان"
    stale = [PageRow(1, 0, "نص قديم مختلف"), PageRow(2, 5, "شيء آخر")]
    assert all(p.page is None for p in paragraphize(content, pages=stale))
    out_of_range = [PageRow(1, 0, "سطر أول"), PageRow(2, 999, "x")]
    assert all(p.page is None for p in paragraphize(content, pages=out_of_range))


def test_no_pages_means_no_page_numbers():
    assert all(p.page is None for p in paragraphize(DOC))


def test_coerce_pages_drops_bad_rows():
    rows = P.coerce_pages([
        {"page_no": 1, "char_start": 0, "content_md": "a"},
        {"page_no": "x", "char_start": 0},
        {"char_start": 3},
        PageRow(3, 10, "c"),
    ])
    assert [r.page_no for r in rows] == [1, 3]


def test_empty_content():
    assert paragraphize("") == []
    assert paragraphize("\n \n\t") == []
