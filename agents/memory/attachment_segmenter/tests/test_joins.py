"""seg-v5.2 — page-break joins and section owners (owner, 2026-11-18)."""
from __future__ import annotations

from agents.memory.attachment_segmenter.joins import (
    PAGE_JOIN_MAX_CHARS,
    SHORT_CORE_MIN_CHARS,
    join_page_breaks,
    merge_short_cores,
)
from agents.memory.attachment_segmenter.runner import _join_rows
from agents.memory.attachment_segmenter.models import OWNER_CLASSES, Segment, SegmentMap
from agents.memory.attachment_segmenter.paragraphs import paragraphize
from agents.memory.attachment_segmenter.repair import stitch
from agents.utils.attachment_delivery import DEEP_SEARCH_VALUES, Section, render_sections, sections_from_rows


def row(seq, f, t, value="core", label="defense", what="", text="x", owner_class="respondent", owner="المدعى عليه"):
    return {
        "seq": seq, "from_p": f, "to_p": t, "page_from": f, "page_to": t,
        "char_start": f * 100, "char_end": t * 100 + 50, "label": label, "value": value,
        "what": what, "content_md": text, "owner_class": owner_class, "owner": owner, "brought_by": "",
    }


def letterhead(seq, f, t, value="boilerplate"):
    return row(seq, f, t, value=value, label="page_letterhead", what="ترويسة صفحة متكررة",
               text="وزارة العدل", owner_class="court", owner="المحكمة")


DOCS = [{"from_p": 1, "to_p": 100, "name": "مذكرة"}]


# ── the join ────────────────────────────────────────────────────────────────


def test_core_cut_by_a_letterhead_is_joined_without_the_letterhead():
    rows = [
        row(1, 56, 56, what="الدفع الثامن وخلاصة النقاط", text="٦. شروط الترقية"),
        letterhead(2, 57, 64),
        row(3, 65, 65, what="استكمال الدفع الثامن", text="تشترط الانتقال إلى المستوى الذي يلي"),
    ]
    out, joins = join_page_breaks(rows, DOCS)
    assert joins == 1 and len(out) == 1
    j = out[0]
    assert (j["seq"], j["from_p"], j["to_p"], j["page_to"]) == (1, 56, 65, 65)
    assert (j["char_start"], j["char_end"]) == (5600, 6550)
    assert j["content_md"] == "٦. شروط الترقية\nتشترط الانتقال إلى المستوى الذي يلي"
    assert "وزارة العدل" not in j["content_md"]
    assert j["what"] == "الدفع الثامن وخلاصة النقاط؛ استكمال الدفع الثامن"


def test_several_letterheads_and_a_repeat_between():
    rows = [row(1, 1, 3, label="table_rows", text="صفوف 1-8"), letterhead(2, 4, 4),
            letterhead(3, 5, 5, value="repeat"), row(4, 6, 9, label="table_rows", text="صفوف 9-20")]
    out, joins = join_page_breaks(rows, DOCS)
    assert joins == 1 and [r["from_p"] for r in out] == [1]


def test_a_chain_over_several_pages():
    rows = []
    for k in range(4):
        rows += [row(len(rows) + 1, 10 * k + 1, 10 * k + 8, label="table_rows", text="ص" * 500)]
        rows += [letterhead(len(rows) + 1, 10 * k + 9, 10 * k + 10)]
    out, joins = join_page_breaks(rows, DOCS)
    assert joins == 3
    assert [r["value"] for r in out] == ["core", "boilerplate"]  # the trailing letterhead stays
    assert [r["seq"] for r in out] == [1, 2]


def test_stops_at_the_size_limit():
    big = "ب" * 3_000
    rows = [row(1, 1, 5, text=big), letterhead(2, 6, 6), row(3, 7, 11, text=big)]
    out, joins = join_page_breaks(rows, DOCS)
    assert joins == 0 and len(out) == 3
    out, joins = join_page_breaks(rows, DOCS, max_chars=10_000)
    assert joins == 1
    assert PAGE_JOIN_MAX_CHARS == 5_000


def test_no_join_without_a_letterhead_between():
    rows = [row(1, 1, 2, text="أ"), row(2, 3, 4, text="ب")]
    assert join_page_breaks(rows, DOCS)[1] == 0


def test_no_join_across_other_sections():
    rows = [row(1, 1, 2), row(2, 3, 3, value="boilerplate", label="signatures"), row(3, 4, 5)]
    assert join_page_breaks(rows, DOCS)[1] == 0
    rows = [row(1, 1, 2), letterhead(2, 3, 3), row(3, 4, 4, value="supporting", label="attachment"),
            letterhead(4, 5, 5), row(5, 6, 7)]
    assert join_page_breaks(rows, DOCS)[1] == 0


def test_no_join_when_label_owner_or_document_differ():
    base = [row(1, 1, 2), letterhead(2, 3, 3)]
    assert join_page_breaks(base + [row(3, 4, 5, label="claim")], DOCS)[1] == 0
    assert join_page_breaks(base + [row(3, 4, 5, owner="المدعى عليه الثاني")], DOCS)[1] == 0
    assert join_page_breaks(base + [row(3, 4, 5, owner_class="witness")], DOCS)[1] == 0
    two_docs = [{"from_p": 1, "to_p": 3, "name": "أ"}, {"from_p": 4, "to_p": 9, "name": "ب"}]
    assert join_page_breaks(base + [row(3, 4, 5)], two_docs)[1] == 0
    assert join_page_breaks(base + [row(3, 4, 5, value="supporting")], DOCS)[1] == 0


def test_rows_without_owner_keys_still_join_and_gain_no_keys():
    """Older maps (seg-v5.1) have no owner columns — a backfill must not add them."""
    def old(seq, f, t, **kw):
        r = row(seq, f, t, **kw)
        for k in ("owner_class", "owner", "brought_by"):
            r.pop(k)
        return r
    rows = [old(1, 1, 2, text="أ"), old(2, 3, 3, value="boilerplate", label="page_letterhead"), old(3, 4, 5, text="ب")]
    out, joins = join_page_breaks(rows, DOCS)
    assert joins == 1 and "owner" not in out[0] and "brought_by" not in out[0]


def test_unchanged_rows_keep_their_order():
    rows = [row(1, 1, 2, value="metadata", label="header_parties"), row(2, 3, 9)]
    out, joins = join_page_breaks(rows, DOCS)
    assert joins == 0 and out == rows


# ── the owner fields ────────────────────────────────────────────────────────


def seg(**kw):
    base = dict(from_p=1, to_p=2, label="claim", what="طلبات", value="core")
    return Segment(**{**base, **kw})


def test_owner_fields_are_lenient():
    assert seg(owner_class=" Respondent ").owner_class == "respondent"
    assert seg(owner_class="third-party").owner_class == "third_party"
    assert seg(owner_class="the plaintiff").owner_class == "unknown"
    assert seg(owner="  المدعى   عليه الثاني ").owner == "المدعى عليه الثاني"
    assert seg(brought_by="None").brought_by == ""
    assert seg().owner_class == "unknown" and seg().owner == "" and seg().brought_by == ""
    assert "unknown" in OWNER_CLASSES and "mixed" in OWNER_CLASSES and len(OWNER_CLASSES) == 21


def test_seam_merges_only_the_same_owner():
    paras = paragraphize("\n".join(f"فقرة {i}" for i in range(1, 11)))
    m1 = SegmentMap(documents_in_file=[], key_paragraphs=[],
                    segments=[seg(from_p=1, to_p=5, owner_class="claimant", owner="المدعي")])
    same = SegmentMap(documents_in_file=[], key_paragraphs=[],
                      segments=[seg(from_p=6, to_p=10, owner_class="claimant", owner="المدعي")])
    other = SegmentMap(documents_in_file=[], key_paragraphs=[],
                       segments=[seg(from_p=6, to_p=10, owner_class="respondent", owner="المدعى عليه")])
    assert len(stitch(m1, same, paras)[0].segments) == 1
    assert len(stitch(m1, other, paras)[0].segments) == 2


# ── delivery shows the owner ────────────────────────────────────────────────


def test_tag_carries_owner_and_brought_by():
    s = Section(seq=1, value="core", what="شهادة على التسليم", text="نص", owner="الشاهد الأول", brought_by="المدعي")
    assert s.tag() == "شهادة على التسليم — الشاهد الأول، قدّمه المدعي"
    assert Section(seq=1, value="core", what="طلبات", text="نص", owner="المدعي").tag() == "طلبات — المدعي"
    assert Section(seq=1, value="core", what="طلبات", text="نص").tag() == "طلبات"
    text, _ = render_sections([s], DEEP_SEARCH_VALUES)
    assert text.startswith("〔شهادة على التسليم — الشاهد الأول، قدّمه المدعي〕\nنص")


def test_sections_from_rows_reads_the_owner_and_old_rows_still_work():
    new = sections_from_rows([{"seq": 1, "value": "core", "what": "w", "content_md": "t", "owner": "المحكمة", "brought_by": None}])
    assert new[0].owner == "المحكمة" and new[0].brought_by == ""
    old = sections_from_rows([{"seq": 1, "value": "core", "what": "w", "content_md": "t"}])
    assert old[0].owner == "" and old[0].tag() == "w"


# ── second step: no core section under 100 chars (owner, 2026-11-18) ────────

LONG = "ن" * 300


def test_short_core_merges_into_the_next_core_with_both_descriptions():
    rows = [
        row(1, 1, 2, value="metadata", label="header", text=LONG),
        row(2, 3, 4, label="other:attendance", what="الحضور عن جهة الخبرة", text="ثالثاً: الحضور\n١. عن شركة الخبرة:",
            owner_class="expert", owner="الخبير"),
        row(3, 5, 9, label="other:attendance", what="جدول حضور فريق الخبرة", text=LONG, owner_class="expert", owner="الخبير"),
    ]
    out, merges = merge_short_cores(rows)
    assert merges == 1 and len(out) == 2
    m = out[1]
    assert (m["seq"], m["from_p"], m["to_p"], m["char_start"], m["char_end"]) == (2, 3, 9, 300, 950)
    assert m["content_md"] == "ثالثاً: الحضور\n١. عن شركة الخبرة:\n" + LONG
    assert m["what"] == "الحضور عن جهة الخبرة؛ جدول حضور فريق الخبرة"


def test_short_core_merges_back_when_the_next_is_not_core():
    rows = [row(1, 1, 5, what="الدفع الأول", text=LONG), row(2, 6, 6, what="خاتمة الدفع", text="وعليه نطلب الرد."),
            letterhead(3, 7, 7)]
    out, merges = merge_short_cores(rows)
    assert merges == 1 and [r["value"] for r in out] == ["core", "boilerplate"]
    assert out[0]["to_p"] == 6 and out[0]["content_md"].endswith("\nوعليه نطلب الرد.")
    assert out[0]["what"] == "الدفع الأول؛ خاتمة الدفع"


def test_short_core_stays_when_no_neighbour_is_core():
    rows = [letterhead(1, 1, 1), row(2, 2, 2, text="قصير"), row(3, 3, 3, value="supporting", label="attachment", text=LONG)]
    out, merges = merge_short_cores(rows)
    assert merges == 0 and out == rows


def test_the_next_core_wins_over_the_previous():
    rows = [row(1, 1, 3, what="قبل", text=LONG), row(2, 4, 4, what="عنوان", text="عنوان"), row(3, 5, 8, what="بعد", text=LONG)]
    out, _ = merge_short_cores(rows)
    assert [r["from_p"] for r in out] == [1, 4] and out[1]["what"] == "عنوان؛ بعد"


def test_a_run_of_short_cores_folds_forward():
    rows = [row(1, 1, 1, what="أ", text="أ"), row(2, 2, 2, what="ب", text="ب"), row(3, 3, 6, what="ج", text=LONG)]
    out, merges = merge_short_cores(rows)
    assert merges == 2 and len(out) == 1
    assert out[0]["content_md"] == "أ\nب\n" + LONG and out[0]["what"] == "أ؛ ب؛ ج"


def test_only_core_counts_and_100_is_not_short():
    assert SHORT_CORE_MIN_CHARS == 100
    rows = [row(1, 1, 1, text="ق" * 100), row(2, 2, 3, text=LONG)]
    assert merge_short_cores(rows)[1] == 0
    rows = [row(1, 1, 1, value="metadata", label="header", text="قصير"), row(2, 2, 3, text=LONG)]
    assert merge_short_cores(rows)[1] == 0


def test_page_join_runs_first_then_the_short_merge():
    """The short heading's neighbour only becomes one section after the page join."""
    rows = [
        row(1, 1, 1, what="عنوان الدفع", text="ثامناً:"),
        row(2, 2, 4, what="الدفع الثامن", text=LONG),
        letterhead(3, 5, 5),
        row(4, 6, 8, what="استكمال الدفع الثامن", text=LONG),
    ]
    smap = SegmentMap(documents_in_file=[], segments=[], key_paragraphs=[])
    out, joins, merges = _join_rows(rows, smap)
    assert (joins, merges, len(out)) == (1, 1, 1)
    assert out[0]["what"] == "عنوان الدفع؛ الدفع الثامن؛ استكمال الدفع الثامن"
    assert out[0]["content_md"] == "ثامناً:\n" + LONG + "\n" + LONG
    assert "وزارة العدل" not in out[0]["content_md"]


def test_short_merge_on_rows_without_owner_keys():
    def old(seq, f, t, **kw):
        r = row(seq, f, t, **kw)
        for k in ("owner_class", "owner", "brought_by"):
            r.pop(k)
        return r
    out, merges = merge_short_cores([old(1, 1, 1, text="قصير"), old(2, 2, 4, text=LONG)])
    assert merges == 1 and "brought_by" not in out[0] and "owner" not in out[0]
