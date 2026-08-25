"""``shared.library.chunk_tables`` — the display walk and its sanitizer.

Two failure modes are worth more than all the others put together, and most of
what is asserted here is one of them:

* **a raw ``TBL_…`` token reaching a reader.** It is the single thing this
  design exists to prevent, so the literal string is asserted absent from the
  output of every function, on every path, including the malformed ones.
* **markup outside the allowlist reaching ``dangerouslySetInnerHTML``.** The
  corpus is clean today (0 ``<script>``, 0 ``<iframe>``, 0 ``javascript:``
  across 24,511 rows), which is exactly why the tests feed it dirty input: the
  guarantee has to come from the re-serializer, not from a snapshot.

The fixtures mirror the real shape rather than the minimum that passes:
``table_md`` is Arabic prose (never empty in the corpus, min 27 chars), the raw
HTML carries the ``class``/``style``/``<br>``/merged-cell mix that the real rows
carry, and one ref uses the hashed form produced for a ``chunk_ref`` containing
Arabic.
"""
from __future__ import annotations

import re

import pytest

from shared.library.chunk_tables import (
    TABLE_PLACEHOLDER,
    ChunkTable,
    display_body,
    render_text_only,
    sanitize_table_html,
    split_body,
    table_weight,
    tables_by_ref,
    visible_text,
)

# --- fixtures that mirror the corpus -----------------------------------------

# The real rows are a bare <table>…</table> fragment carrying presentation junk
# (style= on 55 rows, class everywhere), merged cells (34.1% of rows) and <br>
# (8,641 rows).
RAW_HTML_1 = (
    '<table class="ocr-tbl" style="width:100%" width="640">'
    '<tr><th colspan="2">الفئة</th><th>الأجر</th></tr>'
    '<tr><td rowspan="2">الأولى</td><td>عامل ماهر</td><td>٤٠٠٠<br>ريال</td></tr>'
    "<tr><td>عامل عادي</td><td>٣٠٠٠ ريال</td></tr>"
    "</table>"
)
RAW_HTML_2 = (
    "<table><tr><th>المدة</th><th>الإشعار</th></tr>"
    "<tr><td>أقل من سنة</td><td>شهر واحد</td></tr></table>"
)

# table_md is the prose the table was flattened into — a real Arabic sentence
# list, byte-identical to the block it replaced in `content`.
MD_1 = "الجدول (١): الحد الأدنى للأجور. الفئة الأولى — عامل ماهر: ٤٠٠٠ ريال. الفئة الأولى — عامل عادي: ٣٠٠٠ ريال."
MD_2 = "الجدول (٢): مدد الإشعار. أقل من سنة: شهر واحد."

PROSE_A = "المادة الأولى: تسري أحكام هذه اللائحة على جميع المنشآت الخاضعة لنظام العمل."
PROSE_B = "المادة الثانية: يلتزم صاحب العمل بالحد الأدنى المبيّن في الجدول أعلاه."
PROSE_C = "المادة الثالثة: يُعمل بهذه اللائحة من تاريخ نشرها في الجريدة الرسمية."

REF_1 = "TBL_17261_reg_501_chunk_003_1"
REF_2 = "TBL_17261_reg_501_chunk_003_2"
GHOST_REF = "TBL_17261_reg_501_chunk_003_9"  # ingested nowhere — the D3 case


def rows() -> list[dict[str, str]]:
    """Raw ``chunk_tables_v2`` rows, exactly as PostgREST hands them over."""
    return [
        {"table_ref": REF_1, "table_html": RAW_HTML_1, "table_md": MD_1},
        {"table_ref": REF_2, "table_html": RAW_HTML_2, "table_md": MD_2},
    ]


def tags_in(html: str) -> set[str]:
    return {t.lower() for t in re.findall(r"</?([A-Za-z0-9]+)", html)}


def attrs_in(html: str) -> set[str]:
    names: set[str] = set()
    for attr_blob in re.findall(r"<[A-Za-z0-9]+([^>]*)>", html):
        names.update(n.lower() for n in re.findall(r"([A-Za-z-]+)\s*=", attr_blob))
    return names


ALLOWLIST = {
    "table", "thead", "tbody", "tfoot", "tr", "th", "td", "caption",
    "br", "b", "strong", "i", "em", "sup", "sub",
}


def normalized(text: str) -> str:
    """Whitespace-collapsed, comment-free text — the shape a reader compares.

    The pipeline's own ``<!-- converted table -->`` markers survive in
    ``content`` (an upstream ingestion artifact, 1,796 appendix chunks) and are
    stripped on the display path by ``_strip_html_comments`` in
    ``library_service``. Stripping them here keeps the comparison about the LAW
    rather than about a marker neither view is supposed to show.
    """
    return re.sub(r"\s+", " ", re.sub(r"<!--.*?-->", " ", text, flags=re.S)).strip()


# --- 1. the 82% case ----------------------------------------------------------


@pytest.mark.parametrize("display", [None, "", "   \n\t "])
def test_a_chunk_with_no_display_renders_its_content(display):
    # 39,535 of 48,390 chunks have nothing to swap. That is the normal case, so
    # it must not need a table read, a branch, or an apology.
    chunk = {"content": f"{PROSE_A}\n\n{PROSE_B}", "content_display": display}

    body = display_body(chunk)
    assert body == chunk["content"]

    segments = split_body(body, {})
    assert [s["kind"] for s in segments] == ["text"]
    assert segments[0]["text"] == chunk["content"]
    assert render_text_only(body, {}) == chunk["content"]


def test_a_missing_column_degrades_instead_of_raising():
    # A select list that forgot content_display must not 500 the page.
    assert display_body({"content": PROSE_A}) == PROSE_A
    assert display_body({}) == ""
    assert display_body({"content": None, "content_display": None}) == ""


def test_the_display_body_wins_when_it_has_something_to_say():
    chunk = {"content": f"{PROSE_A}\n{MD_1}", "content_display": f"{PROSE_A}\n{REF_1}"}
    assert display_body(chunk) == chunk["content_display"]


# --- 2. the whole point -------------------------------------------------------


def test_an_unresolved_token_emits_nothing():
    # 0 unresolvable tokens corpus-wide today, but the local corpus runs ahead
    # of the DB and re-ingests recur. A raw token on a statute page is the
    # failure this module exists to prevent.
    body = f"{PROSE_A}\n\n{GHOST_REF}\n\n{PROSE_B}"

    segments = split_body(body, {})
    text_only = render_text_only(body, {})

    assert [s["kind"] for s in segments] == ["text"]
    assert "TBL_" not in segments[0]["text"]
    assert "TBL_" not in text_only
    # ...and no scar where it stood: exactly one paragraph break, not two.
    assert segments[0]["text"] == f"{PROSE_A}\n\n{PROSE_B}"
    assert text_only == f"{PROSE_A}\n\n{PROSE_B}"


@pytest.mark.parametrize(
    "body",
    [
        f"{GHOST_REF}\n\n{PROSE_A}",                       # first line
        f"{PROSE_A}\n\n{GHOST_REF}",                       # last line
        f"{PROSE_A}\n{GHOST_REF}\n{PROSE_B}",              # no blank lines
        f"{PROSE_A}\n\n\n{GHOST_REF}\n\n\n{PROSE_B}",      # blank runs
        f"  {GHOST_REF}\t",                                # leading/trailing space
        f"{PROSE_A}\r\n\r\n{GHOST_REF}\r\n\r\n{PROSE_B}",  # CRLF body
        GHOST_REF,                                         # nothing but the token
    ],
)
def test_no_token_shape_survives_the_walk(body):
    # The CRLF row is the sharp one: a trailing \r defeats the contract regex's
    # end anchor, so a naive walk would ship the token as prose.
    assert "TBL_" not in render_text_only(body, {})
    for segment in split_body(body, {}):
        assert "TBL_" not in segment.get("text", "")
        assert segment["kind"] == "text"


def test_a_text_segment_is_never_empty_and_never_holds_a_token():
    tables = tables_by_ref(rows())
    body = f"\n\n{REF_1}\n\n\n{GHOST_REF}\n\n{PROSE_A}\n\n{REF_2}\n\n"

    segments = split_body(body, tables)

    assert [s["kind"] for s in segments] == ["table", "text", "table"]
    for segment in segments:
        if segment["kind"] == "text":
            assert segment["text"].strip()
            assert TABLE_PLACEHOLDER.search(segment["text"]) is None


# --- 3. resolve by table_ref, never derive it ---------------------------------


def test_a_derived_token_is_never_used():
    # Four regulations carry Arabic in their chunk_ref (one a space), so the
    # token stem is a SANITIZED ref plus a hash. Deriving is right 99.96% of the
    # time, which is the worst possible hit rate.
    chunk_ref = "17645_reg_الانظمة_002_chunk_001"
    hashed_ref = "TBL_17645_reg_002_chunk_001_3f9a2c_1"
    derived_ref = f"TBL_{chunk_ref}_1"

    # The contract regex cannot even see the derived form — [A-Za-z0-9_]+ stops
    # at the first Arabic letter, so a derived lookup would never match a line.
    assert TABLE_PLACEHOLDER.match(derived_ref) is None
    assert TABLE_PLACEHOLDER.match(hashed_ref) is not None

    tables = tables_by_ref(
        [{"table_ref": hashed_ref, "table_html": RAW_HTML_1, "table_md": MD_1}]
    )
    assert set(tables) == {hashed_ref}

    body = f"{PROSE_A}\n\n{hashed_ref}\n\n{PROSE_B}"
    segments = split_body(body, tables)

    assert [s["kind"] for s in segments] == ["text", "table", "text"]
    assert segments[1]["ref"] == hashed_ref
    assert derived_ref not in render_text_only(body, tables)
    assert chunk_ref not in "".join(str(s) for s in segments)


def test_position_is_not_a_key():
    # `position` is document order, not render order, and a chunk can hold
    # tables that were skipped as unsure. Rows carrying it must resolve by ref
    # regardless of what order they arrive in.
    shuffled = [
        {"table_ref": REF_2, "table_html": RAW_HTML_2, "table_md": MD_2, "position": 2},
        {"table_ref": REF_1, "table_html": RAW_HTML_1, "table_md": MD_1, "position": 1},
    ]
    tables = tables_by_ref(shuffled)
    body = f"{REF_2}\n\n{REF_1}"

    refs = [s["ref"] for s in split_body(body, tables) if s["kind"] == "table"]
    assert refs == [REF_2, REF_1]


# --- 4. the sanitizer keeps the grid ------------------------------------------


def test_the_sanitizer_keeps_merged_cells():
    # 8,363 of 24,511 rows (34.1%) carry a merged cell. Losing rowspan/colspan
    # would mangle a third of the corpus while claiming to restore its
    # structure — which is also why this is not a markdown table.
    out = sanitize_table_html('<td rowspan="2" colspan="3" style="x" class="y">أ</td>')

    assert 'rowspan="2"' in out
    assert 'colspan="3"' in out
    assert attrs_in(out) == {"rowspan", "colspan"}


@pytest.mark.parametrize("value", ["0", "999", "abc", "", "  ", "-1", "2.5", "101"])
def test_an_impossible_span_is_dropped_not_clamped(value):
    # rowspan="0" means "to the end of the section" in the spec and is an OCR
    # artifact here; 999 would stretch one cell over the whole page. The cell
    # survives, the attribute does not.
    out = sanitize_table_html(f'<table><tr><td rowspan="{value}">أ</td></tr></table>')

    assert "rowspan" not in out
    assert "أ" in out


@pytest.mark.parametrize("value,expected", [("1", "1"), ("100", "100"), (" 4 ", "4")])
def test_a_legal_span_survives_normalised(value, expected):
    out = sanitize_table_html(f'<table><tr><td colspan="{value}">أ</td></tr></table>')
    assert f'colspan="{expected}"' in out


def test_a_line_break_survives():
    # 8,641 real tables carry one; inside a cell it is the difference between
    # two values and one run-on string.
    for markup in ("<br>", "<br/>", "<BR />"):
        out = sanitize_table_html(f"<table><tr><td>٤٠٠٠{markup}ريال</td></tr></table>")
        assert "<br>" in out
        assert out.count("<br") == 1


def test_presentation_attributes_are_dropped_but_the_text_is_not():
    out = sanitize_table_html(RAW_HTML_1)

    assert attrs_in(out) == {"rowspan", "colspan"}
    assert "style" not in out and "class" not in out and "width" not in out
    for word in ("الفئة", "الأجر", "عامل ماهر", "٤٠٠٠", "ريال"):
        assert word in out


def test_an_unwrapped_element_loses_its_tag_and_keeps_its_text():
    # `a` (42 rows), `p` and `span` are dropped, their text kept — a statute's
    # grid has no business carrying an outbound link, but the label is content.
    out = sanitize_table_html(
        '<table><tr><td><a href="https://x.example">المرجع</a>'
        '<p>فقرة</p><span class="s">مقطع</span></td></tr></table>'
    )

    assert tags_in(out) <= ALLOWLIST
    assert "href" not in out and "x.example" not in out
    for word in ("المرجع", "فقرة", "مقطع"):
        assert word in out


# --- 5. the sanitizer drops everything else -----------------------------------


def test_the_sanitizer_drops_active_markup():
    raw = (
        '<table onclick="steal()"><caption>ج</caption>'
        "<tr><td><script>alert(1)</script>قيمة</td>"
        '<td><img src="https://evil.example/x.png" onerror="steal()">صورة</td></tr>'
        '<tr><td><iframe src="https://evil.example"></iframe>'
        '<a href="javascript:steal()">اضغط</a></td>'
        '<td><form><input name="a"><button>go</button></form>حقل</td></tr>'
        "</table>"
    )

    out = sanitize_table_html(raw)

    for banned in (
        "script", "iframe", "img", "form", "input", "button",
        "onclick", "onerror", "javascript:", "evil.example", "alert(",
    ):
        assert banned not in out
    assert tags_in(out) <= ALLOWLIST
    # Dropped-with-content vs dropped-tag-only, both proven in one pass:
    assert "قيمة" in out and "صورة" in out and "حقل" in out  # siblings kept
    assert "اضغط" in out                                      # <a> unwrapped
    assert "go" not in out                                    # <button> content gone


@pytest.mark.parametrize(
    "raw",
    [
        '<table><tr><img src="a.png"><img src="b.png"></tr></table>',
        "<table><tr><script>alert(1)</script></tr></table>",
        "<div><span>مجرد نص</span></div>",
        "<table></table>",
        "",
        "   ",
        "<img src=x onerror=alert(1)>",
    ],
)
def test_a_table_with_no_surviving_cell_returns_empty(raw):
    assert sanitize_table_html(raw) == ""


def test_a_table_of_only_images_never_reaches_a_segment():
    # An empty sanitize is OMITTED from the dict, so the token takes the one
    # well-tested path (dropped) instead of a second "resolved but blank" state.
    tables = tables_by_ref(
        [
            {
                "table_ref": REF_1,
                "table_html": '<table><tr><img src="scan.png"><script>x</script></tr></table>',
                "table_md": MD_1,
            }
        ]
    )
    assert tables == {}

    body = f"{PROSE_A}\n\n{REF_1}\n\n{PROSE_B}"
    assert split_body(body, tables) == [{"kind": "text", "text": f"{PROSE_A}\n\n{PROSE_B}"}]
    assert "TBL_" not in render_text_only(body, tables)


@pytest.mark.parametrize(
    "raw",
    [
        "<table><tr><td>أ",                                    # nothing closed
        "<table><tr><td>أ</table>",                            # closed too early
        "</td></tr></table><table><tr><td>أ</td></tr></table>",  # stray closes
        "<table><tr><td>أ<script>x",                           # unclosed script
        "<table><tr><td><b>أ</table></b>",                     # crossed nesting
        '<table><tr><td rowspan=2 colspan="3">أ<td>ب',         # unquoted attr
        "<table><tr><td>&lt;script&gt;alert(1)&lt;/script&gt;</td></tr></table>",
        "<table><tr><td><svg><foreignObject><b>أ</b></foreignObject></svg>ب</td></tr>",
        "<table><tr><td><!-- <script>x</script> -->أ</td></tr></table>",
        "<table <tr><td>أ</td></tr></table>",                  # broken start tag
    ],
)
def test_malformed_markup_cannot_escape_the_allowlist(raw):
    # A regex strip can be defeated by markup a regex cannot parse. A
    # re-serializer emits only tags it decided to emit, so the output is a
    # function of the allowlist and not of the input's shape.
    out = sanitize_table_html(raw)

    assert tags_in(out) <= ALLOWLIST
    assert attrs_in(out) <= {"rowspan", "colspan"}
    # Substrings, not words: an escaped "&lt;script&gt;" in a cell is inert TEXT
    # the statute actually printed, and deleting it would be data loss. What may
    # never appear is an unescaped tag.
    for banned in ("<script", "<svg", "<foreignobject", "<iframe", "<!--", "onerror"):
        assert banned not in out.lower()
    # Whatever survived is balanced — every open tag has its close.
    for tag in tags_in(out):
        if tag != "br":
            assert out.count(f"<{tag}") == out.count(f"</{tag}>")


def test_smuggled_entities_stay_escaped_instead_of_becoming_tags():
    # convert_charrefs resolves "&lt;script&gt;" to text on the way in, and the
    # re-serializer escapes every text node on the way out — so a payload that
    # tries to become a tag by hiding inside entities comes back out as the
    # inert characters the cell actually contained.
    out = sanitize_table_html(
        "<table><tr><td>&lt;script&gt;alert(1)&lt;/script&gt;</td></tr></table>"
    )

    assert "&lt;script&gt;" in out
    assert "<script" not in out
    assert tags_in(out) <= ALLOWLIST


# --- 6. the two views carry the same law --------------------------------------


def test_render_text_only_reproduces_the_prose():
    # `table_md` is literally the block the table was flattened into, so
    # substituting it back must reconstruct `content`. This is the strongest
    # available proof that the user view and the agent view are the same law,
    # and it is the check to run over a live sample if this ever drifts.
    chunk = {
        "content": "\n\n".join(
            [
                PROSE_A,
                f"<!-- converted table -->\n{MD_1}\n<!-- end table -->",
                PROSE_B,
                f"<!-- converted table -->\n{MD_2}\n<!-- end table -->",
                PROSE_C,
            ]
        ),
        "content_display": "\n\n".join([PROSE_A, REF_1, PROSE_B, REF_2, PROSE_C]),
    }
    tables = tables_by_ref(rows())

    body = display_body(chunk)
    assert body == chunk["content_display"]

    assert normalized(render_text_only(body, tables)) == normalized(chunk["content"])

    # ...and the segmented view carries exactly the same law, which is what
    # lets the gate charge a table at len(md) and stay neutral (plan D8).
    segments = split_body(body, tables)
    assert [s["kind"] for s in segments] == ["text", "table", "text", "table", "text"]
    rebuilt = "\n\n".join(
        s["text"] if s["kind"] == "text" else s["md"] for s in segments
    )
    assert normalized(rebuilt) == normalized(chunk["content"])
    assert sum(len(s["md"]) for s in segments if s["kind"] == "table") == len(MD_1) + len(MD_2)


def test_a_table_segment_carries_sanitized_html_and_its_prose():
    tables = tables_by_ref(rows())
    segment = split_body(f"{PROSE_A}\n{REF_1}", tables)[1]

    assert segment == {
        "kind": "table",
        "ref": REF_1,
        "html": tables[REF_1].html,
        "md": MD_1,
        "weight": table_weight(tables[REF_1]),
    }
    assert "style" not in segment["html"]      # already sanitized, not raw
    assert segment["md"] == MD_1               # alt text / copy text
    assert len(segment["md"]) >= 27            # corpus minimum
    # `weight` is the GATE's number and `md` is the copy button's; they are not
    # the same value and the 244 conversion-error rows are why (table_weight).
    assert segment["weight"] >= len(segment["md"])


def test_tables_by_ref_skips_rows_it_cannot_key():
    tables = tables_by_ref(
        [
            {"table_ref": None, "table_html": RAW_HTML_1, "table_md": MD_1},
            {"table_ref": "   ", "table_html": RAW_HTML_1, "table_md": MD_1},
            {"table_html": RAW_HTML_1, "table_md": MD_1},
            {"table_ref": REF_1, "table_html": None, "table_md": MD_1},
            {"table_ref": REF_2, "table_html": RAW_HTML_2, "table_md": None},
        ]
    )

    assert set(tables) == {REF_2}
    assert tables[REF_2].md == ""             # never happens in the corpus...
    assert tables[REF_2].html.startswith("<table>")


def test_a_table_with_no_prose_still_renders_but_says_nothing_in_text():
    # ...and if it ever did, the grid still renders and the text-only channel
    # simply loses that block. A degradation, never a raw token.
    tables = {REF_1: ChunkTable(table_ref=REF_1, html="<table><tr><td>أ</td></tr></table>", md="")}
    body = f"{PROSE_A}\n\n{REF_1}\n\n{PROSE_B}"

    assert [s["kind"] for s in split_body(body, tables)] == ["text", "table", "text"]
    assert render_text_only(body, tables) == f"{PROSE_A}\n\n{PROSE_B}"


def test_an_empty_body_produces_nothing():
    assert split_body("", {}) == []
    assert render_text_only("", {}) == ""
    assert split_body("\n\n\n", {}) == []


# --- the gate weight, and the corpus rows that make it necessary -------------


def test_visible_text_measures_the_grid_not_the_markup():
    """Tags and whitespace runs are not law; the cell text is."""
    html = '<table><tr><th rowspan="2">م</th><td>غرامة</td></tr></table>'
    assert visible_text(html) == "م غرامة"
    assert visible_text("") == ""


def test_a_table_whose_prose_conversion_FAILED_is_charged_for_what_it_renders():
    """The 244-row hole: `md` is an error string, the grid is a fines table.

    `17405_reg_603_chunk_019` is the real shape — two 3.2 KB violation-fine
    grids whose entire prose form is «[خطأ في التحويل - انتهت المهلة]», 31
    chars. Charging `len(md)` would serve a complete penalty schedule to an
    anonymous reader for 31 characters of a 600-character budget.
    """
    conversion_error = "[خطأ في التحويل - انتهت المهلة]"
    grid = (
        "<table><tr><th>م</th><th>المخالفة</th><th>الغرامة</th></tr>"
        + "".join(
            f"<tr><td>{n}</td><td>مخالفة رقم {n} من اللائحة التنفيذية</td>"
            f"<td>{n * 1000} ريال</td></tr>"
            for n in range(1, 40)
        )
        + "</table>"
    )
    table = ChunkTable(
        table_ref="TBL_x_1", html=sanitize_table_html(grid), md=conversion_error
    )

    assert len(table.md) == 31
    assert table_weight(table) > 1000
    assert table_weight(table) == len(visible_text(table.html))


def test_the_weight_never_undercharges_a_table():
    """max(), so the 97.8% where `md` already dominates are unaffected."""
    verbose_prose = "بند " * 500
    table = ChunkTable(
        table_ref="TBL_x_1",
        html=sanitize_table_html("<table><tr><td>أ</td></tr></table>"),
        md=verbose_prose,
    )
    assert table_weight(table) == len(verbose_prose)
