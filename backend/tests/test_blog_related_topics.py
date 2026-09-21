"""«اقرأ تاليًا» on the `public_blogs` wing, plus the grounding the same slug feeds.

Plan: `.claude/plans/blog_mobile_parity.md` Waves A + B.

WHAT THIS PINS
--------------
Two separate things that both hang off addressing a public blog by its ARABIC
SLUG rather than a 32-hex token:

1. **The relatedness rule** — «اقرأ تاليًا» groups articles by the أنظمة they
   both cite, read off the frozen `references_json`. Not by `type` (23 of the 25
   live rows read `judicial_research`, so it groups nothing) and not by subject
   chip (an editorial shelf, not a topic).

2. **The image strip** — a published blog carries its marketing cards inline as
   markdown images. Every text surface over this wing has to remove them; this
   one feeds a model, where they are storage URLs spent out of a context budget.

Everything here is a PURE function over a frozen payload — no DB, no fixtures.
The two-table resolution these support (`ask_service._ground_blog`,
`library_item_service._title_blog`) is exercised against the live wing rather
than a stub, because a stub of PostgREST is what would make a false pass.
"""
from __future__ import annotations

from backend.app.services.public_blog_service import (
    _fold_topic,
    _strip_markdown_images,
    _topic_keys,
)


def _reg(title: str, doc_type: str = "نظام") -> dict:
    return {"domain": "regulations", "doc_type": doc_type, "regulation_title": title}


# ---------------------------------------------------------------------------
# §1 The fold — one نظام, however the corpus spells it
# ---------------------------------------------------------------------------


def test_the_fold_unifies_alef_ya_and_ta_marbuta() -> None:
    # The corpus splits the same نظام across alef spellings. Left unfolded these
    # are two topics and the strip silently comes back short — the quiet cousin
    # of the `fetch_article` pin bug, where a bare-alef corpus title answered a
    # hamza query with the WRONG law.
    assert _fold_topic("نظام الإثبات") == _fold_topic("نظام الاثبات")
    assert _fold_topic("نظام المرافعات الشرعيّة") == _fold_topic(
        "نظام المرافعات الشرعية"
    )
    # Tatweel and doubled whitespace are noise, not distinctions.
    assert _fold_topic("نظام   العمل") == _fold_topic("نظام العمل")


def test_the_fold_keeps_distinct_regulations_distinct() -> None:
    # A نظام and its لائحة are different documents and must stay different keys,
    # or every article about either relates to every article about the other.
    assert _fold_topic("نظام التنفيذ") != _fold_topic("اللائحة التنفيذية لنظام التنفيذ")


# ---------------------------------------------------------------------------
# §2 Topic extraction — what counts as a topic at all
# ---------------------------------------------------------------------------


def test_a_cases_entry_is_not_a_topic() -> None:
    """⚠ THE COURTHOUSE GUARD, and the whole strip depends on it.

    On a `cases` entry `regulation_title` holds the COURT that issued the
    judgment — «وزارة العدل», «ديوان المظالم» — which nearly every judicial
    article cites. Counting those would relate all of them to all of them
    through the building the judgment came out of.
    """
    entries = [
        {"domain": "cases", "doc_type": "", "regulation_title": "وزارة العدل"},
        {"domain": "cases", "doc_type": "", "regulation_title": "ديوان المظالم"},
        {"domain": "compliance", "doc_type": "", "regulation_title": "وزارة العدل"},
        _reg("نظام العمل"),
    ]
    assert set(_topic_keys(entries)) == {_fold_topic("نظام العمل")}


def test_a_binding_instrument_outweighs_a_guidance_booklet() -> None:
    """The measured failure that put `doc_type` in the key (live, 2026-09-21).

    Ranking on citation rarity alone put «تقادم الديون» — a commercial-debt
    article — ABOVE «تشغيل عمال دون نقل خدماتهم» in the strip of a LABOUR claim,
    because the booklet the debt piece happened to share («دليل الخدمات المقدمة
    للوافدين») was rarer than نظام العمل is. Rarity measures how unusual a
    citation is; only the instrument class says whether it is about anything.
    """
    topics = _topic_keys(
        [_reg("نظام العمل"), _reg("دليل الخدمات المقدمة للوافدين", "دليل")]
    )
    assert topics[_fold_topic("نظام العمل")] == 1.0
    assert topics[_fold_topic("دليل الخدمات المقدمة للوافدين")] < 1.0


def test_every_binding_instrument_family_is_recognised() -> None:
    # SAMA ضوابط/تعليمات/قواعد are binding regulator instruments and make just as
    # real a topic as a نظام — «ضوابط التمويل الاستهلاكي» is the subject of an
    # article, not background reading.
    for doc_type in ("نظام", "لائحة تنفيذية", "ضوابط", "تعليمات", "قواعد"):
        topics = _topic_keys([_reg("وثيقة ما", doc_type)])
        assert topics[_fold_topic("وثيقة ما")] == 1.0, doc_type


def test_a_title_cited_under_two_doc_types_keeps_the_stronger_reading() -> None:
    # The corpus labels the same document inconsistently. Taking the max means an
    # inconsistency can cost precision but can never silently demote a real نظام.
    topics = _topic_keys([_reg("نظام العمل", "دليل"), _reg("نظام العمل", "نظام")])
    assert topics[_fold_topic("نظام العمل")] == 1.0


def test_malformed_payloads_yield_no_topics_instead_of_raising() -> None:
    # `references_json` is a frozen jsonb column read on a PUBLIC route. Every
    # shape it can hold has to degrade to "this article has no topics".
    assert _topic_keys(None) == {}
    assert _topic_keys("not a list") == {}
    assert _topic_keys([None, 7, "x"]) == {}
    assert _topic_keys([{"domain": "regulations"}]) == {}  # no doc_type, no title
    assert _topic_keys([_reg("", "نظام")]) == {}  # blank title


# ---------------------------------------------------------------------------
# §3 The marketing cards — stripped before a model ever sees them
# ---------------------------------------------------------------------------


def test_marketing_cards_are_stripped_from_the_grounding_body() -> None:
    """Measured: the cover image URL landed in the FIRST 120 characters of the
    grounding context, ahead of the article's own lede."""
    body = (
        "![غلاف المقال](https://x.supabase.co/storage/v1/object/public/blog-cards/a/1.png)\n"
        "\n"
        "يواجه كثير من العمال امتناع صاحب العمل.\n"
        "\n"
        "![أبرز النقاط](https://x.supabase.co/storage/v1/object/public/blog-cards/a/2.png)\n"
        "\n"
        "وتمتد مهلة التسوية 21 يوماً.\n"
    )
    out = _strip_markdown_images(body)
    assert "supabase.co" not in out
    assert "غلاف المقال" not in out
    assert out.startswith("يواجه كثير من العمال")
    assert "وتمتد مهلة التسوية 21 يوماً." in out
    # An image-only line leaves no blank where a paragraph appeared to be.
    assert "\n\n\n" not in out


def test_a_markdown_LINK_survives_the_strip() -> None:
    # `[نص](url)` is prose with an address attached; only the `!` form is a card.
    text = "راجع [نظام العمل](/regulations/نظام-العمل) لمزيد من التفصيل."
    assert _strip_markdown_images(text) == text


def test_a_body_with_no_images_is_returned_unchanged() -> None:
    # No blank-line normalisation is applied to bodies this has nothing to do
    # with — the byte-for-byte guarantee the frontend helper also makes.
    text = "فقرة أولى.\n\n\nفقرة ثانية بعد ثلاثة أسطر."
    assert _strip_markdown_images(text) is text
