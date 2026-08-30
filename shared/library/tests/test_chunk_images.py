"""``shared.library.chunk_images`` — the span walk, the counter and the agent body.

Three failure modes are worth more than all the others put together, and most
of what is asserted here is one of them:

* **a raw ``![img-1.jpeg](images/…)`` span reaching a reader or a model.**
  Unlike its sibling this is not a defensive test — it is TODAY'S BUG. 168
  published أنظمة print that literal string right now, and **656 chunks carry
  markup with no row at all** (298 of those spans on published pages), so the
  "unresolved" branch is the one real data takes. The literal ``](images/`` is
  asserted absent from the output of every function, on every path.
* **«الصورة {N}» disagreeing with what a reader can count.** ``meta->>'n'`` has
  gaps in 120 of 418 regulations (worst: 383), ``n_in_chunk`` restarts every
  chunk, and a figure cited twice must not get two numbers. The counter is
  minted by the renderer and nothing else, and that is asserted from three
  directions.
* **a URL built from ``image_ref + ".jpeg"``.** 575 of 5,347 rows are PNG.

The fixtures mirror the real shape rather than the minimum that passes:
``storage_path`` is ``{regulation_ref}/{image_ref}.{ext}``, ``origin``/``n``/
``n_in_chunk``/``width``/``height`` live inside ``meta`` exactly as PostgREST
hands them over, ``description`` is a real Arabic sentence of corpus length, and
one fixture carries a regulation ref with Arabic in it (four do, live).
"""
from __future__ import annotations

import pytest

from shared.library.chunk_images import (
    AGENT_FIGURE_BUDGET,
    IMAGE_SPAN,
    IMAGE_TOKEN,
    ChunkImage,
    image_weight,
    images_by_chunk,
    place_images,
    render_for_agent,
)
from shared.library.chunk_tables import split_body, tables_by_ref

# --- fixtures that mirror the corpus -----------------------------------------

BASE_URL = "https://dwgghvxogtwyaxmbgjod.supabase.co"
BUCKET_PREFIX = f"{BASE_URL}/storage/v1/object/public/regulation-images"

CHUNK = "3f1c0c9a-1111-4111-8111-000000000001"
OTHER_CHUNK = "3f1c0c9a-1111-4111-8111-000000000002"
REG_REF = "17387_reg_017"

PROSE_A = "المادة الأولى: تسري أحكام هذه اللائحة على جميع المنشآت الخاضعة لنظام العمل."
PROSE_B = "المادة الثانية: يلتزم صاحب العمل بالاشتراطات المبيَّنة في المخطط أعلاه."
PROSE_C = "المادة الثالثة: يُعمل بهذه اللائحة من تاريخ نشرها في الجريدة الرسمية."

# Real span shape (REFERENCE.md §3.1): mean 41 chars, p50 44, max 68.
SPAN_1 = "![img-1.jpeg](images/page_005_img_001.jpeg)"
SPAN_2 = "![img-2.png](images/page_012_img_003.png)"
GHOST_SPAN = "![img-9.jpeg](images/page_099_img_009.jpeg)"  # the 656-chunk case

DESC_1 = (
    "مخطط تدفق يوضح مراحل إصدار الترخيص بدءًا من تقديم الطلب عبر البوابة "
    "الإلكترونية ومرورًا بالفحص الفني وانتهاءً بتسليم الرخصة للمنشأة."
)
DESC_2 = (
    "لوحة إرشادية تبيّن أبعاد مواقف السيارات المخصصة لذوي الإعاقة والمسافات "
    "الفاصلة بينها بالأمتار."
)
TRANSCRIPT_1 = "تقديم الطلب ← الفحص الفني ← الموافقة ← إصدار الرخصة"


def row(
    *,
    image_ref: str,
    basename: str = "",
    n: int = 1,
    origin: str = "cited",
    ext: str = "jpeg",
    title: str = "مخطط تدفق إجراءات الترخيص",
    description: str = DESC_1,
    transcribed_text: str = "",
    contains_text: bool | None = None,
    uploaded_at: str | None = "2026-08-29T09:14:22.117+00:00",
    chunk_id: str = CHUNK,
    reg_ref: str = REG_REF,
    n_in_chunk: int = 1,
    width: int = 1240,
    height: int = 880,
    storage_path: str | None = None,
) -> dict:
    """One ``chunk_images`` row, exactly as PostgREST hands it over.

    ``origin``/``n``/``n_in_chunk``/``width``/``height`` sit inside ``meta``
    because the plan's batched read (§3.2) selects ``meta`` whole.
    """
    if contains_text is None:
        contains_text = bool(transcribed_text)
    return {
        "chunk_id": chunk_id,
        "image_ref": image_ref,
        "source_basename": basename,
        "title": title,
        "description": description,
        "contains_text": contains_text,
        "transcribed_text": transcribed_text,
        "storage_path": storage_path or f"{reg_ref}/{image_ref}.{ext}",
        "mime_type": "image/png" if ext == "png" else "image/jpeg",
        "uploaded_at": uploaded_at,
        "meta": {
            "origin": origin,
            "zone": "real_content",
            "position_source": "exact" if origin == "cited" else "predicted",
            "image_type": "diagram",
            "n": n,
            "n_in_chunk": n_in_chunk,
            "page": 5,
            "width": width,
            "height": height,
        },
    }


CITED_1 = row(
    image_ref=f"{REG_REF}_img_5",
    basename="page_005_img_001.jpeg",
    n=5,
    transcribed_text=TRANSCRIPT_1,
)
CITED_2 = row(
    image_ref=f"{REG_REF}_img_12",
    basename="page_012_img_003.png",
    n=12,
    ext="png",
    n_in_chunk=2,
    title="أبعاد مواقف ذوي الإعاقة",
    description=DESC_2,
)


def orphan_row(*, ref_n: int, n: int, **kwargs) -> dict:
    return row(
        image_ref=f"{REG_REF}_img_{ref_n}",
        basename="",
        n=n,
        origin="orphan",
        **kwargs,
    )


def chunk_images(rows: list[dict], chunk_id: str = CHUNK) -> list[ChunkImage]:
    return images_by_chunk(rows, base_url=BASE_URL).get(chunk_id, [])


def texts(segments: list[dict]) -> str:
    """Everything a text segment would put on the page, concatenated."""
    return "\n".join(s.get("text", "") for s in segments if s.get("kind") == "text")


def kinds(segments: list[dict]) -> list[str]:
    return [s["kind"] for s in segments]


# --- 1. the 96.7% case --------------------------------------------------------


def test_a_chunk_with_no_images_is_untouched():
    # 46,831 of 48,429 chunks carry no figure. That is the normal case, so it
    # must not need a branch, an allocation or an apology.
    content = f"{PROSE_A}\n\n{PROSE_B}\n\n{PROSE_C}"
    segments = split_body(content, {})

    placed, next_index = place_images(segments, [])

    assert placed == segments
    assert placed[0] is segments[0]  # identity, not a normalised copy
    assert next_index == 1
    assert render_for_agent(content, []) == content


def test_a_table_segment_rides_through_untouched():
    # 433 chunks carry both, and no image span was ever swallowed into a TBL_
    # token (799 of 799 basenames survive into content_display — §9.3).
    ref = "TBL_17387_reg_017_chunk_002_1"
    tables = tables_by_ref(
        [
            {
                "table_ref": ref,
                "table_html": "<table><tr><td>أ</td></tr></table>",
                "table_md": "الجدول (١): بيان الفئات والأجور المقررة لها نظامًا.",
            }
        ]
    )
    body = f"{PROSE_A}\n\n{ref}\n\n{SPAN_1}\n\n{PROSE_B}"
    segments = split_body(body, tables)

    placed, next_index = place_images(segments, chunk_images([CITED_1]))

    assert kinds(placed) == ["text", "table", "image", "text"]
    assert placed[1] is segments[1]  # the table segment is not rebuilt
    assert next_index == 2


# --- 2. the whole point: no raw markup, anywhere -----------------------------


def test_an_unresolved_span_emits_nothing():
    # THIS ONE FIRES ON REAL DATA: 656 chunks carry markup with no row at all,
    # 298 of those spans on published pages. The vision pass judged those
    # figures decorative or they sit in front matter — they have no image and
    # never will, so deleting them IS the fix.
    body = f"{PROSE_A}\n\n{GHOST_SPAN}\n\n{PROSE_B}"

    placed, next_index = place_images(split_body(body, {}), [])
    agent = render_for_agent(body, [])

    assert kinds(placed) == ["text"]
    assert "](images/" not in texts(placed)
    assert "](images/" not in agent
    assert next_index == 1
    # ...and no scar where it stood: one paragraph break, not two.
    assert placed[0]["text"] == f"{PROSE_A}\n\n{PROSE_B}"
    assert agent == f"{PROSE_A}\n\n{PROSE_B}"


@pytest.mark.parametrize(
    "body,expected",
    [
        (f"{GHOST_SPAN}\n\n{PROSE_A}", PROSE_A),                    # first line
        (f"{PROSE_A}\n\n{GHOST_SPAN}", PROSE_A),                    # last line
        (f"{PROSE_A}\n{GHOST_SPAN}\n{PROSE_B}", f"{PROSE_A}\n{PROSE_B}"),
        (f"{PROSE_A}\n\n{GHOST_SPAN}\n\n{PROSE_B}", f"{PROSE_A}\n\n{PROSE_B}"),
        (f"  {GHOST_SPAN}\t", ""),                                  # nothing else
        (GHOST_SPAN, ""),                                           # the whole body
        (f"{PROSE_A} {GHOST_SPAN} {PROSE_B}", f"{PROSE_A} {PROSE_B}"),  # inline
        (f"{PROSE_A}{GHOST_SPAN}{PROSE_B}", f"{PROSE_A}{PROSE_B}"),     # glued
        (f"{PROSE_A}\n\n{GHOST_SPAN}\n{GHOST_SPAN}\n\n{PROSE_B}",
         f"{PROSE_A}\n\n{PROSE_B}"),                                # two in a row
    ],
)
def test_no_span_shape_survives_the_walk(body, expected):
    placed, _ = place_images(split_body(body, {}), [])

    assert "](images/" not in texts(placed)
    assert "](images/" not in render_for_agent(body, [])
    assert kinds(placed) == (["text"] if expected else [])
    assert texts(placed) == expected
    assert render_for_agent(body, []) == expected


def test_a_row_for_another_chunk_never_resolves_this_chunk_s_span():
    # images_by_chunk keys on chunk_id, so a document-wide batched read cannot
    # let one section's figure resolve another's span.
    by_chunk = images_by_chunk(
        [row(image_ref=f"{REG_REF}_img_5", basename="page_005_img_001.jpeg",
             chunk_id=OTHER_CHUNK)],
        base_url=BASE_URL,
    )
    body = f"{PROSE_A}\n\n{SPAN_1}"

    placed, _ = place_images(split_body(body, {}), by_chunk.get(CHUNK, []))

    assert kinds(placed) == ["text"]
    assert "](images/" not in texts(placed)


# --- 3. replace the SPAN, never the line -------------------------------------


def test_an_inline_span_keeps_its_sentence():
    # 47 of the 3,677 cited spans sit inline inside a prose sentence. A
    # whole-line rule silently drops every one of them and leaves the sentence
    # looking finished.
    head = "يجب أن تُركَّب اللوحة الإرشادية"
    tail = "وفق الأبعاد المبيَّنة في المواصفة القياسية."
    body = f"{head} {SPAN_1} {tail}"

    placed, next_index = place_images(split_body(body, {}), chunk_images([CITED_1]))

    assert kinds(placed) == ["text", "image", "text"]
    assert placed[0]["text"] == head
    assert placed[2]["text"] == tail
    assert next_index == 2
    assert "](images/" not in texts(placed)

    # ...and the agent gets the same three parts in the same order.
    agent = render_for_agent(body, chunk_images([CITED_1]))
    assert agent.index(head) < agent.index(DESC_1) < agent.index(tail)


def test_a_whole_line_span_gives_the_identical_result():
    # 3,630 of 3,677 are whole-line, so the span rule has to be a no-op there —
    # that is what makes it strictly safer than a line rule and never worse.
    body = f"{PROSE_A}\n\n{SPAN_1}\n\n{PROSE_B}"

    placed, _ = place_images(split_body(body, {}), chunk_images([CITED_1]))

    assert kinds(placed) == ["text", "image", "text"]
    assert placed[0]["text"] == PROSE_A
    assert placed[2]["text"] == PROSE_B


def test_the_image_segment_is_the_wire_contract():
    images = chunk_images([CITED_1])
    placed, _ = place_images(split_body(SPAN_1, {}), images)

    assert placed == [
        {
            "kind": "image",
            "image_ref": f"{REG_REF}_img_5",
            "n": 1,
            "title": "مخطط تدفق إجراءات الترخيص",
            "description": DESC_1,
            "url": f"{BUCKET_PREFIX}/{REG_REF}/{REG_REF}_img_5.jpeg",
            "width": 1240,
            "height": 880,
            # D10: max(len(span), len(caption) + len(transcribed_text))
            "weight": image_weight(images[0]),
            "span_len": len(SPAN_1),
        }
    ]
    # The transcription is CHARGED but never shipped to the reader (D9).
    assert TRANSCRIPT_1 not in str(placed)
    assert placed[0]["weight"] > placed[0]["span_len"]


def test_a_removed_span_is_still_charged():
    """D10's invariant, stated the way the gate needs it.

    Every character ``content`` spent on a span is still spent, plus whatever
    the figure adds on top — by construction, not by measurement. A figure whose
    words are shorter than the markup it replaced (a 4-char title, no
    transcription, against a 68-char span) must not make the budget looser than
    it is today.
    """
    tiny = row(image_ref=f"{REG_REF}_img_5", basename="page_005_img_001.jpeg",
               title="شعار", description="", transcribed_text="")
    placed, _ = place_images(split_body(SPAN_1, {}), chunk_images([tiny]))

    assert placed[0]["span_len"] == len(SPAN_1)
    assert placed[0]["weight"] == len(SPAN_1)  # the max() floor, not the caption
    assert image_weight(chunk_images([tiny])[0]) < len(SPAN_1)


def test_an_orphan_charges_only_what_it_renders():
    # An orphan removed no markup, so there is no span to floor its weight at.
    images = chunk_images([orphan_row(ref_n=7, n=7, transcribed_text=TRANSCRIPT_1)])
    placed, _ = place_images(split_body(PROSE_A, {}), images)

    assert placed[1]["span_len"] == 0
    assert placed[1]["weight"] == image_weight(images[0])
    assert placed[1]["weight"] > len(TRANSCRIPT_1)  # the caption rides on top


# --- 4. no bytes, no figure ---------------------------------------------------


def test_uploaded_at_null_is_unresolved():
    # 0 rows today. A URL for absent bytes is a 404 inside a statute, which is
    # the exact thing this module exists to stop — so it is code, not a comment.
    missing = row(image_ref=f"{REG_REF}_img_5", basename="page_005_img_001.jpeg",
                  uploaded_at=None)
    body = f"{PROSE_A}\n\n{SPAN_1}\n\n{PROSE_B}"

    assert images_by_chunk([missing], base_url=BASE_URL) == {}

    placed, next_index = place_images(split_body(body, {}), chunk_images([missing]))
    # ...behaves EXACTLY like a row that does not exist.
    assert placed == place_images(split_body(body, {}), [])[0]
    assert kinds(placed) == ["text"]
    assert next_index == 1
    assert "](images/" not in texts(placed)
    assert render_for_agent(body, chunk_images([missing])) == f"{PROSE_A}\n\n{PROSE_B}"


@pytest.mark.parametrize(
    "broken",
    [
        {"chunk_id": ""},        # nothing to key on
        {"image_ref": ""},       # no React key, no counter key
        {"storage_path": ""},    # no path, no URL (D7)
        {"uploaded_at": ""},     # D6
    ],
)
def test_a_row_missing_a_key_is_dropped_like_one_that_never_existed(broken):
    payload = row(image_ref=f"{REG_REF}_img_5", basename="page_005_img_001.jpeg")
    payload.update(broken)
    assert images_by_chunk([payload], base_url=BASE_URL) == {}


def test_no_base_url_resolves_nothing_rather_than_building_a_relative_one():
    # A blank base would build "/storage/v1/object/public/…" — a relative URL
    # against the app origin, i.e. exactly today's broken-image bug wearing a
    # different filename. Fail soft to prose without figures instead.
    assert images_by_chunk([CITED_1], base_url="") == {}
    assert images_by_chunk([CITED_1], base_url=None) == {}


# --- 5. the URL comes from storage_path --------------------------------------


def test_the_url_uses_storage_path():
    # 575 of 5,347 rows are PNG. `storage_path` already carries the right
    # extension; `image_ref` never does.
    png = chunk_images([CITED_2])[0]

    assert png.url == f"{BUCKET_PREFIX}/{REG_REF}/{REG_REF}_img_12.png"
    assert png.url.endswith(".png")
    assert ".jpeg" not in png.url
    assert f"{png.image_ref}.jpeg" not in png.url
    # ...and the jpeg row is not a lucky coincidence of the same rule.
    assert chunk_images([CITED_1])[0].url.endswith(f"{REG_REF}_img_5.jpeg")


def test_an_arabic_regulation_ref_is_percent_encoded():
    # Four regulations carry Arabic in their ref (17645_reg_الانظمة_002). A raw
    # path is a URL only a browser could guess at, and never one a server fetch
    # or a crawler can follow.
    arabic_ref = "17645_reg_الانظمة_002"
    image = chunk_images(
        [row(image_ref=f"{arabic_ref}_img_1", basename="page_001_img_001.jpeg",
             reg_ref=arabic_ref)]
    )[0]

    assert "الانظمة" not in image.url
    assert "%D8%A7%D9%84%D8%A7%D9%86%D8%B8%D9%85%D8%A9" in image.url
    assert image.url.startswith(f"{BUCKET_PREFIX}/")
    assert image.url.count("/storage/v1/object/public/") == 1


@pytest.mark.parametrize("base", [BASE_URL, f"{BASE_URL}/", f"{BASE_URL}///"])
def test_a_trailing_slash_on_the_base_never_doubles(base):
    image = images_by_chunk([CITED_1], base_url=base)[CHUNK][0]
    assert image.url == f"{BUCKET_PREFIX}/{REG_REF}/{REG_REF}_img_5.jpeg"


# --- 6. «الصورة {N}» is render order, and nothing else -----------------------


def test_the_number_is_render_order():
    """D8, from all three directions it can be got wrong.

    ``meta->>'n'`` is 47 here and ``n_in_chunk`` is 3, and the figure is the
    first one on the page, so it is «الصورة 1». Live, 120 of 418 regulations
    have gaps in ``n`` — worst case a نظام whose 31 figures are numbered 1, 47,
    …, 414, where a reader would see «الصورة 402» and conclude 401 figures were
    missing.
    """
    odd = row(image_ref=f"{REG_REF}_img_47", basename="page_005_img_001.jpeg",
              n=47, n_in_chunk=3)
    images = chunk_images([odd])

    placed, next_index = place_images(split_body(f"{PROSE_A}\n\n{SPAN_1}", {}), images)

    assert placed[1]["n"] == 1
    assert next_index == 2
    assert images[0].n == 47          # the ordering field is preserved...
    assert placed[1]["n"] != images[0].n  # ...and is never the label
    assert "الصورة 1: " in render_for_agent(f"{PROSE_A}\n\n{SPAN_1}", images)


def test_a_repeated_image_ref_reuses_its_number():
    # 26 basenames appear more than once in a single chunk — genuinely the same
    # figure cited twice. It must not become «الصورة 3» and «الصورة 7».
    body = f"{PROSE_A}\n\n{SPAN_1}\n\n{PROSE_B}\n\n{SPAN_1}\n\n{PROSE_C}"
    images = chunk_images([CITED_1])

    placed, next_index = place_images(split_body(body, {}), images)
    figures = [s for s in placed if s["kind"] == "image"]

    assert len(figures) == 2
    assert [f["n"] for f in figures] == [1, 1]
    assert next_index == 2  # ONE number was consumed
    # The reader sees the figure twice, where the statute cited it twice, and
    # the gate is charged twice — a reader cannot scroll past the second one.
    assert all(f["weight"] == figures[0]["weight"] for f in figures)


def test_the_counter_threads_across_two_sections():
    # /regulations/{slug} threads ONE number through its sections in reading
    # order (D8); a مادة page starts again at 1 (D17). Both are this signature.
    first, next_index = place_images(
        split_body(f"{PROSE_A}\n\n{SPAN_1}", {}), chunk_images([CITED_1])
    )
    second, final_index = place_images(
        split_body(f"{PROSE_B}\n\n{SPAN_2}", {}),
        chunk_images([CITED_2]),
        start_index=next_index,
    )

    assert [s["n"] for s in first if s["kind"] == "image"] == [1]
    assert [s["n"] for s in second if s["kind"] == "image"] == [2]
    assert (next_index, final_index) == (2, 3)

    # D17: the same figure, numbered from 1 on its own page.
    page, _ = place_images(split_body(SPAN_2, {}), chunk_images([CITED_2]))
    assert page[0]["n"] == 1


def test_the_number_is_latin_digits():
    # The number is app chrome, so project_latin_numerals_policy applies to it;
    # the title beside it is corpus text and is the carve-out.
    agent = render_for_agent(SPAN_1, chunk_images([CITED_1]))

    assert "الصورة 1:" in agent
    assert "الصورة ١" not in agent


# --- 7. orphans ---------------------------------------------------------------


def test_orphans_append_in_n_order_after_the_last_line():
    # 1,670 figures on 484 chunks have no markup at all — recovered from the
    # source PDF and placed by line provenance. Median 2 per chunk, max 31.
    rows = [
        orphan_row(ref_n=9, n=9, title="الشكل الثالث"),
        orphan_row(ref_n=3, n=3, title="الشكل الأول"),
        orphan_row(ref_n=6, n=6, title="الشكل الثاني"),
    ]
    body = f"{PROSE_A}\n\n{PROSE_B}"

    placed, next_index = place_images(split_body(body, {}), chunk_images(rows))

    assert kinds(placed) == ["text", "image", "image", "image"]
    assert [s["title"] for s in placed[1:]] == [
        "الشكل الأول", "الشكل الثاني", "الشكل الثالث",
    ]
    assert [s["n"] for s in placed[1:]] == [1, 2, 3]  # render order, not meta n
    assert [s["span_len"] for s in placed[1:]] == [0, 0, 0]
    assert next_index == 4


def test_orphans_follow_the_cited_figures_in_the_numbering():
    body = f"{PROSE_A}\n\n{SPAN_1}\n\n{PROSE_B}"
    rows = [CITED_1, orphan_row(ref_n=30, n=30, title="لوحة ملحقة")]

    placed, next_index = place_images(split_body(body, {}), chunk_images(rows))

    assert kinds(placed) == ["text", "image", "text", "image"]
    assert [s["n"] for s in placed if s["kind"] == "image"] == [1, 2]
    assert placed[-1]["title"] == "لوحة ملحقة"
    assert next_index == 3


def test_the_agent_body_labels_its_orphan_block():
    # Their position in the prose is `predicted`; presenting a guessed position
    # as a certain one is a claim we cannot make to a model that will cite it.
    images = chunk_images([orphan_row(ref_n=3, n=3, title="الشكل الأول")])
    agent = render_for_agent(f"{PROSE_A}\n\n{PROSE_B}", images)

    assert agent.startswith(f"{PROSE_A}\n\n{PROSE_B}")
    assert "**صور مرفقة بهذا المقطع:**" in agent
    assert agent.index(PROSE_B) < agent.index("صور مرفقة بهذا المقطع")


# --- 8. the two populations are disjoint --------------------------------------


def test_cited_and_orphan_never_double_show():
    """The corpus invariant, re-asserted as a rendering property.

    Every cited row's ``source_basename`` appears in its chunk's ``content``;
    every orphan's does not — that is precisely WHY it is an orphan (it was
    recovered from the source PDF, not from the markup). So the span walk can
    never consume a figure the orphan pass will also append, and no figure can
    show twice.

    ⚠ This is asserted here over a FIXTURE that encodes the invariant, because
    this module is pure and has no database. **The live re-assertion — every
    cited basename present in ``chunks_v2.content``, every orphan basename
    absent — belongs to the backend suite**, which has a Supabase client; the
    plan's §6 item 8 asks for it "over a live sample" and that sample is not
    reachable from here.
    """
    cited = [CITED_1, CITED_2]
    orphans = [
        orphan_row(ref_n=20, n=20, title="لوحة ملحقة أولى"),
        orphan_row(ref_n=21, n=21, title="لوحة ملحقة ثانية"),
    ]
    body = f"{PROSE_A}\n\n{SPAN_1}\n\n{PROSE_B}\n\n{SPAN_2}\n\n{PROSE_C}"

    # The invariant itself, stated over the fixture.
    for image in chunk_images(cited):
        assert image.source_basename and image.source_basename in body
    for image in chunk_images(orphans):
        assert image.source_basename == ""
        assert image.image_ref not in body

    placed, next_index = place_images(split_body(body, {}), chunk_images(cited + orphans))
    figures = [s for s in placed if s["kind"] == "image"]

    assert len(figures) == 4
    assert len({f["image_ref"] for f in figures}) == 4  # each shows exactly once
    assert [f["n"] for f in figures] == [1, 2, 3, 4]
    assert next_index == 5

    agent = render_for_agent(body, chunk_images(cited + orphans))
    for image in chunk_images(cited + orphans):
        assert agent.count(image.title) == 1


# --- 9. the agent body --------------------------------------------------------


def test_the_agent_body_carries_the_transcription():
    # `transcribed_text` is the answer to "what does the diagram say" — it is
    # where a spec table's numbers live, on 4,156 of 5,347 rows.
    images = chunk_images([CITED_1])
    body = f"{PROSE_A}\n\n{SPAN_1}\n\n{PROSE_B}"

    agent = render_for_agent(body, images)

    assert "> 🖼 **الصورة 1: مخطط تدفق إجراءات الترخيص**" in agent
    assert f"> {DESC_1}" in agent
    assert f"> **نص الصورة:** {TRANSCRIPT_1}" in agent
    assert PROSE_A in agent and PROSE_B in agent

    # NOTHING about the image itself travels: the model cannot open it, so
    # shipping any of this is pure cost.
    assert "](images/" not in agent
    assert "http" not in agent
    assert "supabase" not in agent
    assert "regulation-images" not in agent
    assert "storage/v1" not in agent
    assert images[0].image_ref not in agent
    assert ".jpeg" not in agent and ".png" not in agent


def test_a_figure_with_no_readable_text_gets_no_transcription_line():
    # contains_text is false on 1,191 of 5,347 rows, and a transcription is
    # never fabricated for them.
    quiet = row(image_ref=f"{REG_REF}_img_5", basename="page_005_img_001.jpeg",
                transcribed_text="لا شيء", contains_text=False)
    images = chunk_images([quiet])

    assert images[0].transcribed_text == ""
    agent = render_for_agent(SPAN_1, images)
    assert "نص الصورة" not in agent
    assert "لا شيء" not in agent
    # ...and the gate is not charged for text no reader's eye will ever get.
    assert image_weight(images[0]) == len("الصورة ") + len(": ") + len(images[0].title)


def test_a_multi_line_description_cannot_break_the_blockquote():
    # A blank line inside a description would TERMINATE the quote, and the
    # continuation would then read as the statute's own prose — the exact
    # confusion the 🖼 label exists to prevent.
    messy = row(
        image_ref=f"{REG_REF}_img_5",
        basename="page_005_img_001.jpeg",
        description="السطر الأول\n\nالسطر الثاني\n\tالسطر الثالث",
        transcribed_text="قيمة ١\nقيمة ٢",
    )
    agent = render_for_agent(SPAN_1, chunk_images([messy]))

    assert "> السطر الأول السطر الثاني السطر الثالث" in agent
    assert "> **نص الصورة:** قيمة ١ قيمة ٢" in agent
    for line in agent.split("\n"):
        assert line.startswith(">")  # every emitted line is still in the quote


def test_a_figure_cited_twice_is_described_once():
    # The same 2,008-char description twice against a 4,000-char ceiling is
    # pure cost; the second occurrence keeps its caption, and its number.
    body = f"{PROSE_A}\n\n{SPAN_1}\n\n{PROSE_B}\n\n{SPAN_1}"
    agent = render_for_agent(body, chunk_images([CITED_1]))

    assert agent.count(DESC_1) == 1
    assert agent.count("> 🖼 **الصورة 1: مخطط تدفق إجراءات الترخيص**") == 2
    assert "لم تُدرج" not in agent  # nothing was WITHHELD — it is verbatim above


# --- 10. the ceiling ----------------------------------------------------------


def test_the_agent_body_is_capped():
    """D13, on the shape of the worst chunk in the corpus (+28,318 chars).

    Uncapped substitution adds a mean of 2,114 chars to a chunk whose mean
    ``content`` is 3,076, and on 452 chunks (28%) the figure text is longer than
    the statute text. Past the ceiling the remaining figures collapse to their
    captions and a «(+N صورة أخرى)» line — the model is TOLD, never silently
    shorted.
    """
    descriptions = [f"وصف الصورة رقم {i} " + ("ب" * 1000) for i in range(30)]
    rows = [
        row(
            image_ref=f"{REG_REF}_img_{i}",
            basename=f"page_{i:03d}_img_001.jpeg",
            n=i,
            title=f"شكل رقم {i}",
            description=descriptions[i],
        )
        for i in range(30)
    ]
    spans = "\n\n".join(f"![img-{i}.jpeg](images/page_{i:03d}_img_001.jpeg)"
                        for i in range(30))
    body = f"{PROSE_A}\n\n{spans}\n\n{PROSE_B}"
    images = chunk_images(rows)

    agent = render_for_agent(body, images)

    # Every figure is still NAMED — all 30 captions, in render order.
    for i in range(30):
        assert f"الصورة {i + 1}: شكل رقم {i}" in agent

    described = [i for i in range(30) if descriptions[i] in agent]
    assert described == [0, 1, 2]  # the ceiling bites at the 4th
    assert sum(len(descriptions[i]) for i in described) <= AGENT_FIGURE_BUDGET
    # ...and the first skip closes the channel for the rest, so the sequence
    # stays honest rather than filling in around the holes.
    assert agent.endswith(f"(+{30 - len(described)} صورة أخرى لم تُدرج)")
    assert "](images/" not in agent
    assert PROSE_A in agent and PROSE_B in agent


def test_a_smaller_ceiling_is_honoured():
    images = chunk_images([CITED_1, CITED_2])
    body = f"{SPAN_1}\n\n{SPAN_2}"

    agent = render_for_agent(body, images, max_chars=10)

    assert DESC_1 not in agent and DESC_2 not in agent
    assert "الصورة 1: مخطط تدفق إجراءات الترخيص" in agent
    assert "الصورة 2: أبعاد مواقف ذوي الإعاقة" in agent
    assert agent.endswith("(+2 صورة أخرى لم تُدرج)")


def test_the_ceiling_does_not_touch_the_display_path():
    # The gate is the backend's job and it is a DIFFERENT budget: this module
    # only computes and attaches `weight`/`span_len` and never truncates.
    rows = [
        row(image_ref=f"{REG_REF}_img_{i}", basename=f"page_{i:03d}_img_001.jpeg",
            n=i, description="ب" * 2000)
        for i in range(10)
    ]
    body = "\n\n".join(f"![img-{i}.jpeg](images/page_{i:03d}_img_001.jpeg)"
                       for i in range(10))

    placed, next_index = place_images(split_body(body, {}), chunk_images(rows))

    assert len([s for s in placed if s["kind"] == "image"]) == 10
    assert next_index == 11
    assert all(s["description"] for s in placed)


# --- the two regexes are two regexes -----------------------------------------


def test_the_span_regex_is_the_corpus_contract():
    # REFERENCE.md §3.1, byte for byte. It matches anywhere on a line, which is
    # the whole reason this walk cannot join chunk_tables' line walk.
    assert IMAGE_SPAN.findall(f"{PROSE_A} {SPAN_1} {PROSE_B}") == [
        "page_005_img_001.jpeg"
    ]
    assert IMAGE_SPAN.findall(f"  {SPAN_2}  ") == ["page_012_img_003.png"]
    assert IMAGE_SPAN.findall("![](images/bare.jpeg)") == ["bare.jpeg"]
    assert IMAGE_SPAN.findall("![x](other/page.jpeg)") == []
    assert IMAGE_SPAN.findall("images/page_005_img_001.jpeg") == []


def test_the_token_regex_is_ours_and_is_a_whole_line():
    # D14: IMG_{N} is minted by the server, is ASCII by construction, and is
    # the caption number. Verified corpus-wide: 0 chunks contain a whole-line
    # IMG_\d+; the 8 that contain one inline are never matched.
    assert IMAGE_TOKEN.findall("IMG_3") == ["IMG_3"]
    assert IMAGE_TOKEN.findall(f"{PROSE_A}\n  IMG_12\t\n{PROSE_B}") == ["IMG_12"]
    assert IMAGE_TOKEN.findall("راجع IMG_3 أعلاه") == []
    assert IMAGE_TOKEN.findall(f"IMG_{REG_REF}") == []

    # The two are DIFFERENT regexes for different jobs and must not be merged:
    # neither one can see the other's shape.
    assert IMAGE_TOKEN.search(SPAN_1) is None
    assert IMAGE_SPAN.search("IMG_3") is None

    # And this module never emits one — projection is the backend's job.
    assert IMAGE_TOKEN.search(render_for_agent(SPAN_1, chunk_images([CITED_1]))) is None


# --- construction edges -------------------------------------------------------


def test_images_by_chunk_groups_and_orders_by_n():
    by_chunk = images_by_chunk(
        [
            row(image_ref=f"{REG_REF}_img_9", basename="b.jpeg", n=9),
            row(image_ref=f"{REG_REF}_img_2", basename="a.jpeg", n=2),
            row(image_ref=f"{REG_REF}_img_4", basename="c.jpeg", n=4,
                chunk_id=OTHER_CHUNK),
        ],
        base_url=BASE_URL,
    )

    assert set(by_chunk) == {CHUNK, OTHER_CHUNK}
    assert [i.n for i in by_chunk[CHUNK]] == [2, 9]
    assert len(by_chunk[OTHER_CHUNK]) == 1


def test_the_aliased_query_shape_reads_the_same():
    # REFERENCE.md §7 selects `meta->>'origin' as origin, (meta->>'n')::int as
    # n`; the plan's batched read selects `meta` whole. A consumer that used
    # either one must not silently get zeros.
    aliased = {
        "chunk_id": CHUNK,
        "image_ref": f"{REG_REF}_img_5",
        "source_basename": "page_005_img_001.jpeg",
        "title": "مخطط",
        "description": DESC_1,
        "contains_text": False,
        "transcribed_text": None,
        "storage_path": f"{REG_REF}/{REG_REF}_img_5.jpeg",
        "uploaded_at": "2026-08-29T09:14:22Z",
        "origin": "orphan",
        "n": 42,
        "width": 800,
        "height": 600,
    }
    image = images_by_chunk([aliased], base_url=BASE_URL)[CHUNK][0]

    assert (image.origin, image.n, image.width, image.height) == ("orphan", 42, 800, 600)


def test_an_unknown_origin_falls_back_to_cited():
    # Fail-soft direction: a cited misread resolves its span if it has a
    # basename and renders nowhere if it does not. The other default would
    # append a figure on a position nobody predicted (D5/D16).
    weird = row(image_ref=f"{REG_REF}_img_5", basename="page_005_img_001.jpeg",
                origin="???")
    images = chunk_images([weird])

    assert images[0].origin == "cited"
    placed, _ = place_images(split_body(f"{PROSE_A}\n\n{SPAN_1}", {}), images)
    assert kinds(placed) == ["text", "image"]

    # ...and with no basename it renders NOWHERE rather than guessing a spot.
    homeless = chunk_images([row(image_ref=f"{REG_REF}_img_6", basename="",
                                 origin="???")])
    assert place_images(split_body(PROSE_A, {}), homeless)[0] == [
        {"kind": "text", "text": PROSE_A}
    ]


def test_dimensions_default_to_zero_rather_than_raising():
    # A select list that forgot `meta` must degrade, not 500 — and the renderer
    # is then responsible for omitting the attribute, not emitting width="0".
    bare = {
        "chunk_id": CHUNK,
        "image_ref": f"{REG_REF}_img_5",
        "source_basename": "page_005_img_001.jpeg",
        "title": "مخطط",
        "description": DESC_1,
        "storage_path": f"{REG_REF}/{REG_REF}_img_5.jpeg",
        "uploaded_at": "2026-08-29T09:14:22Z",
    }
    image = images_by_chunk([bare], base_url=BASE_URL)[CHUNK][0]

    assert (image.width, image.height, image.n) == (0, 0, 0)
    assert image.origin == "cited"
    assert image.transcribed_text == ""


def test_empty_inputs_produce_nothing():
    assert images_by_chunk([], base_url=BASE_URL) == {}
    assert images_by_chunk(None, base_url=BASE_URL) == {}
    assert place_images([], []) == ([], 1)
    assert place_images(None, chunk_images([CITED_1])) == ([], 1)
    assert render_for_agent("", []) == ""
    assert render_for_agent(None, []) == ""
