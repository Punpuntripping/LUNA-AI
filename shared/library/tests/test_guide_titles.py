"""The channel title rewrite — the pure half, exercised without a model.

Every case here is drawn from a real corpus value measured 2026-08-25, because
the failure mode this rewrite risks is not a crash: it is a published `<title>`
that reads «… في الموقع الإلكتروني» or names a portal the guide never mentions.
Those are the cases worth pinning.
"""
from __future__ import annotations

import pytest

from shared.library.guide_titles import (
    brand_already_in_title,
    canonicalize_channels,
    channel_brand,
    channel_is_grounded,
    channel_shape_error,
    compose_guide_title,
    normalize_channel,
    strip_locale_tail,
)


# ── normalize_channel: the live «قناة التقديم» values ────────────────────────
@pytest.mark.parametrize(
    "raw, expected",
    [
        ("بوابة ناجز الإلكترونية (najiz.sa).", "بوابة ناجز"),
        ("بوابة ناجز الإلكترونية", "بوابة ناجز"),
        ("بوابة ناجز الإلكترونية.", "بوابة ناجز"),
        ('منصة "بلدي" الإلكترونية', "منصة بلدي"),
        ("منصة بلدي الإلكترونية (balady.gov.sa).", "منصة بلدي"),
        ("منصة بلدي الإلكترونية (وزارة البلديات والإسكان)", "منصة بلدي"),
        ("منصة اعتماد الإلكترونية", "منصة اعتماد"),
        ("تطبيق صحتي", "تطبيق صحتي"),
        ('تطبيق "صحتي" (iOS/Android)', "تطبيق صحتي"),
        ("بوابة ناجز (نفاذ)", "بوابة ناجز"),
        ("  منصة هدف الإلكترونية.  ", "منصة هدف"),
        ("", ""),
        (None, ""),
    ],
)
def test_normalize_channel_strips_gloss_quotes_and_punctuation(raw, expected) -> None:
    assert normalize_channel(raw) == expected


def test_normalize_channel_does_not_maul_a_generic_phrase_into_a_fake_brand() -> None:
    """«الخدمات الإلكترونية» must stay generic enough for the denylist to catch.

    Stripping the trailing «الإلكترونية» unconditionally would leave «الخدمات»,
    which is not on the denylist by that spelling — the phrase would sneak
    through as a "brand" and publish «… في الخدمات»."""
    cleaned = normalize_channel("الخدمات الإلكترونية")
    assert channel_shape_error(cleaned) is not None


# ── channel_shape_error: what must never reach a title ───────────────────────
@pytest.mark.parametrize(
    "channel",
    ["بوابة ناجز", "منصة بلدي", "منصة اعتماد", "تطبيق صحتي", "منصة مساند", "ناجز"],
)
def test_a_real_brand_passes(channel) -> None:
    assert channel_shape_error(channel) is None


@pytest.mark.parametrize(
    "channel, why",
    [
        ("", "empty"),
        ("الموقع الرسمي", "generic"),
        ("البوابة الإلكترونية", "generic"),
        ("الموقع الإلكتروني", "generic"),
        ("بوابة الخدمات الإلكترونية", "generic"),
        ("إلكتروني", "generic"),
        ("بوابة الموقع الرسمي", "classifier glued onto a generic phrase"),
        ("najiz.sa", "Latin"),
        ("منصة GOSI أعمال", "Latin"),
        ("بوابة ناجز الإلكترونية للخدمات القضائية الشاملة", "too many words"),
    ],
)
def test_a_non_brand_is_refused(channel, why) -> None:
    assert channel_shape_error(channel) is not None, why


@pytest.mark.parametrize(
    "vague",
    [
        "المنصة الإلكترونية للبرنامج",
        "البوابة الإلكترونية للوزارة",
        "الموقع الرسمي للصندوق",
        "الموقع الرسمي للهيئة",
        "بوابة الخدمات الإلكترونية",
        "المنصة الإلكترونية للهيئة",
        "بوابة الخدمات الذاتية",
        "الموقع الإلكتروني للمركز",
        "منصة الخدمات الحكومية",
    ],
)
def test_a_VAGUE_POSSESSIVE_names_nothing_and_is_refused(vague) -> None:
    """«المنصة الإلكترونية للبرنامج» describes whose the portal is; it does not
    name it. That family is one member per government body, so the explicit
    denylist cannot cover it — the structural rule must: strip the classifier,
    and if every remaining token is common administrative vocabulary, nothing
    was named."""
    assert channel_shape_error(normalize_channel(vague)) is not None


@pytest.mark.parametrize(
    "brand", ["بوابة ناجز", "منصة بلدي", "منصة اعتماد", "تطبيق صحتي", "منصة مساند",
              "منصة قوى", "بوابة أبشر", "منصة هدف", "منصة سكني"]
)
def test_the_structural_rule_does_not_eat_a_REAL_brand(brand) -> None:
    """The counterweight to the test above: the rule rejects labels made only of
    common words, so a proper noun must survive it. If this ever fails, the
    stoplist has swallowed a name."""
    assert channel_shape_error(normalize_channel(brand)) is None


def test_a_channel_may_not_smuggle_the_locale_tail_back_in() -> None:
    """LIVE dry-run regression: the model answered «بوابة ادرس في السعودية»,
    which composed to «… في بوابة ادرس في السعودية» — the exact tail this whole
    rewrite removes, reintroduced one field to the left."""
    assert normalize_channel("بوابة ادرس في السعودية") == "بوابة ادرس"
    assert (
        compose_guide_title(
            "الدليل الشامل: طلب منحة داخلية في السعودية",
            normalize_channel("بوابة ادرس في السعودية"),
        )
        == "الدليل الشامل: طلب منحة داخلية في بوابة ادرس"
    )


def test_a_common_word_in_the_NAME_SLOT_is_refused() -> None:
    """LIVE dry-run regression: «منصة الخدمات التجارية» passed an all-tokens
    test because «التجارية» is not itself a common word. But a brand follows its
    classifier IMMEDIATELY — «الخدمات» sitting in the name slot is already the
    tell."""
    assert channel_shape_error(normalize_channel("منصة الخدمات التجارية")) is not None
    assert channel_shape_error(normalize_channel("بوابة الخدمات الحكومية")) is not None
    # …and the counterweight: a brand in the name slot survives.
    assert channel_shape_error(normalize_channel("منصة مدينتي")) is None


def test_the_denylist_is_diacritic_and_alef_insensitive() -> None:
    """«الالكتروني» and «الإلكتروني» are the same non-answer."""
    assert channel_shape_error("الموقع الالكتروني") is not None
    assert channel_shape_error("الموقع الإلكتروني") is not None


# ── grounding: the gate that makes this safe to publish ──────────────────────
def test_a_brand_the_body_names_is_grounded() -> None:
    body = "تتيح وزارة العدل عبر بوابة ناجز خدمة الاطلاع على قضايا المنشأة إلكترونيًا."
    assert channel_is_grounded("بوابة ناجز", body) is True


def test_a_brand_the_body_never_mentions_is_REJECTED() -> None:
    """THE hallucination case. A model that answers «بوابة أبشر» for a service
    whose body never says أبشر has invented it, and an invented portal in a
    published `<title>` is a factual error on an indexed page."""
    body = "تتيح وزارة العدل عبر بوابة ناجز خدمة الاطلاع على قضايا المنشأة."
    assert channel_is_grounded("بوابة أبشر", body) is False


def test_grounding_ignores_the_classifier_we_chose_ourselves() -> None:
    """The body says «موقع بلدي»; we normalise to «منصة بلدي». The BRAND is what
    has to be present — requiring our classifier would reject a right answer."""
    assert channel_is_grounded("منصة بلدي", "ادخل إلى موقع بلدي ثم اختر الخدمة") is True


def test_grounding_survives_harakah_and_alef_spelling() -> None:
    assert channel_is_grounded("بوابة ناجز", "عبر بوابة نَاجِز الإلكترونية") is True


def test_grounding_of_an_empty_channel_is_false_not_an_exception() -> None:
    assert channel_is_grounded("", "أي نص") is False
    assert channel_is_grounded("بوابة ناجز", None) is False


# ── strip_locale_tail ────────────────────────────────────────────────────────
def test_the_two_live_locale_tails_are_stripped() -> None:
    assert (
        strip_locale_tail("الدليل الشامل: إصدار رخصة بناء في السعودية")
        == "الدليل الشامل: إصدار رخصة بناء"
    )
    assert (
        strip_locale_tail("الدليل الشامل: طلب التقاعد في المملكة العربية السعودية")
        == "الدليل الشامل: طلب التقاعد"
    )


def test_a_MID_STRING_locale_phrase_is_left_alone() -> None:
    """One live title reads «… للمحامي في السعودية: عرض قائمة …». The phrase is
    part of the sentence there, not a tail, and cutting it would maim the
    title."""
    title = "الدليل الشامل: المتدربون لدي (للمحامي) في السعودية: عرض قائمة بالمتدربين"
    assert strip_locale_tail(title) == title


def test_a_title_with_no_locale_tail_is_unchanged() -> None:
    assert strip_locale_tail("الدليل الشامل: تسجيل وقف") == "الدليل الشامل: تسجيل وقف"


# ── compose_guide_title: the whole point ─────────────────────────────────────
def test_the_channel_replaces_the_locale_tail() -> None:
    assert (
        compose_guide_title(
            "الدليل الشامل: الاطلاع على قضايا المنشأة في السعودية", "بوابة ناجز"
        )
        == "الدليل الشامل: الاطلاع على قضايا المنشأة في بوابة ناجز"
    )


def test_a_title_with_no_tail_gets_the_label_APPENDED() -> None:
    assert (
        compose_guide_title("الدليل الشامل: تسجيل وقف", "الهيئة العامة للأوقاف")
        == "الدليل الشامل: تسجيل وقف في الهيئة العامة للأوقاف"
    )


def test_the_corpus_prefix_is_never_touched() -> None:
    """`guideDisplayTitle` on the client rewrites «الدليل الشامل:» to the
    «بالصور» form by PREFIX MATCH. Normalising it here would silently stop that
    match and every guide would lose its «بالصور»."""
    out = compose_guide_title(
        "الدليل الشامل: إصدار رخصة بناء في السعودية", "منصة بلدي"
    )
    assert out.startswith("الدليل الشامل:")


def test_no_label_leaves_the_title_completely_alone() -> None:
    """Including its locale tail: a title ending in a dangling «في» or nothing at
    all is worse than the generic tail."""
    original = "الدليل الشامل: إصدار رخصة بناء في السعودية"
    assert compose_guide_title(original, None) == original
    assert compose_guide_title(original, "") == original
    assert compose_guide_title(original, "   ") == original


def test_a_title_that_already_names_its_entity_is_not_doubled() -> None:
    """Live case: «حجز موعد إلكتروني في وزارة الموارد البشرية والتنمية
    الاجتماعية» with the entity as its fallback label."""
    title = "الدليل الشامل: حجز موعد إلكتروني في وزارة الموارد البشرية والتنمية الاجتماعية"
    assert (
        compose_guide_title(title, "وزارة الموارد البشرية والتنمية الاجتماعية") == title
    )


def test_a_title_ending_in_the_label_under_another_preposition_is_not_doubled() -> None:
    title = "الدليل الشامل: الخدمات التي تقدمها منصة بلدي"
    assert compose_guide_title(title, "منصة بلدي") == title


def test_composition_is_idempotent() -> None:
    """Re-running the build over an already-composed title must be a no-op —
    the script is re-runnable and a second pass must not produce
    «… في بوابة ناجز في بوابة ناجز»."""
    once = compose_guide_title(
        "الدليل الشامل: الاطلاع على قضايا المنشأة في السعودية", "بوابة ناجز"
    )
    assert compose_guide_title(once, "بوابة ناجز") == once


def test_an_empty_title_survives_an_empty_corpus_row() -> None:
    assert compose_guide_title("", "بوابة ناجز") == ""
    assert compose_guide_title(None, "بوابة ناجز") == ""


def test_channel_brand_drops_only_the_classifier() -> None:
    assert channel_brand("بوابة ناجز") == "ناجز"
    assert channel_brand("منصة بلدي") == "بلدي"
    assert channel_brand("ناجز") == "ناجز"
    assert channel_brand("") == ""


# ── canonicalize_channels: one portal, one spelling ──────────────────────────
def test_one_brand_under_several_classifiers_collapses_to_the_majority() -> None:
    """The live 2026-08-25 shape: بلدي arrived as «منصة بلدي» ×125, «بوابة بلدي»
    ×8, «تطبيق بلدي» ×2."""
    labels = ["منصة بلدي"] * 125 + ["بوابة بلدي"] * 8 + ["تطبيق بلدي"] * 2
    mapping = canonicalize_channels(labels)
    assert mapping["بوابة بلدي"] == "منصة بلدي"
    assert mapping["تطبيق بلدي"] == "منصة بلدي"
    assert mapping["منصة بلدي"] == "منصة بلدي"


def test_a_harakah_only_variant_is_merged() -> None:
    """«منصة مُعين» and «منصة معين» are one system. The vote folds diacritics, so
    the damma cannot split a brand into two portals."""
    mapping = canonicalize_channels(["منصة معين"] * 3 + ["منصة مُعين"] * 2 + ["نظام معين"] * 2)
    assert len({mapping[k] for k in mapping}) == 1


def test_a_genuine_SUB_BRAND_keeps_its_own_identity() -> None:
    """«بلدي أعمال» is not «بلدي». The vote is by BRAND, and those two fold
    differently, so merging them would rename a real portal."""
    mapping = canonicalize_channels(["منصة بلدي"] * 10 + ["منصة بلدي أعمال"] * 2)
    assert mapping["منصة بلدي أعمال"] == "منصة بلدي أعمال"
    assert mapping["منصة بلدي"] == "منصة بلدي"


def test_the_vote_is_deterministic_on_a_tie() -> None:
    """A dry-run must be a PREVIEW of the apply, so an even split cannot resolve
    differently between two runs."""
    a = canonicalize_channels(["بوابة ناجز", "منصة ناجز"])
    b = canonicalize_channels(["منصة ناجز", "بوابة ناجز"])
    assert a == b


def test_canonicalizing_an_empty_or_blank_run_is_not_an_error() -> None:
    assert canonicalize_channels([]) == {}
    assert canonicalize_channels(["", "   ", None]) == {}


def test_stacked_classifiers_are_peeled_to_the_brand() -> None:
    """LIVE regression: «بوابة نظام معين» survived canonicalisation as its own
    portal because only one classifier was stripped, leaving «نظام معين»."""
    assert channel_brand("بوابة نظام معين") == "معين"
    mapping = canonicalize_channels(["منصة معين"] * 7 + ["بوابة نظام معين"])
    assert mapping["بوابة نظام معين"] == "منصة معين"


def test_peeling_never_empties_the_brand_slot() -> None:
    """«منصة الخدمات» must keep something for the generic gate to judge."""
    assert channel_brand("منصة خدمة") != ""
    assert channel_shape_error(normalize_channel("منصة خدمة")) is not None


# ── the recycled-service-name gate (live apply regression, 27 titles) ────────
@pytest.mark.parametrize(
    "title, channel",
    [
        ("الدليل الشامل: إصدار ترخيص صناعي في السعودية", "منصة صناعي"),
        ("الدليل الشامل: خدمة إصدار رخصة فال لإدارة الأملاك في السعودية", "منصة فال"),
        ("الدليل الشامل: القبول الموحد في الجامعات في السعودية", "منصة قبول"),
        ("الدليل الشامل: منصة خبير للتدريب التعاوني", "منصة خبير"),
        ("الدليل الشامل: بلدي أعمال", "منصة بلدي أعمال"),
    ],
)
def test_a_channel_INVENTED_FROM_THE_TITLE_is_caught(title, channel) -> None:
    """Grounding cannot catch these: the service's own name is always in the
    body, so a model that recycles it as a portal passes that check."""
    assert brand_already_in_title(channel, title) is True


def test_a_real_channel_absent_from_the_title_is_not_caught() -> None:
    assert (
        brand_already_in_title(
            "بوابة ناجز", "الدليل الشامل: الاطلاع على قضايا المنشأة في السعودية"
        )
        is False
    )


def test_a_title_that_already_names_its_channel_is_not_doubled() -> None:
    """LIVE regression: «… في السعودية عبر ناجز» became «… عبر ناجز في بوابة
    ناجز». The brand sat one preposition before the end, so an ``endswith``
    check missed it."""
    title = "الدليل الشامل: التحقق من معاملة في السعودية عبر ناجز"
    assert compose_guide_title(title, "بوابة ناجز") == title


def test_the_ENTITY_fallback_is_anti_stuttered_too() -> None:
    """«لوحة التحكم في وزارة البلديات والإسكان» must not gain its own ministry
    a second time."""
    title = "الدليل الشامل: لوحة التحكم في وزارة البلديات والإسكان"
    assert compose_guide_title(title, "وزارة البلديات والإسكان") == title


def test_a_mid_string_label_blocks_the_append() -> None:
    title = "الدليل الشامل: تدقيق بيانات ومتغيرات الوظائف في مسار"
    assert compose_guide_title(title, "منصة مسار") == title
