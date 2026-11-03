# -*- coding: utf-8 -*-
"""Layer-1 fixture tests (byte-exact) + targeted unit tests for the codec.

Covers Test-plan categories 1–14 (15–17 are wiring tests for later phases).

The fixture (``masking_fixtures.json``) is a *golden* regression lock: it was
generated once with a fixed seed and hand-verified against the "Locked rules".
The test replays the same seeded RNG over the same ordered messages with a single
shared codec (one simulated user across turns) and asserts byte-exact output.
Any rule drift changes an output and fails loudly.
"""
import json
import os
import random

import pytest

from shared.privacy.codec import (
    AuditHit,
    PrivacyCodec,
    TripwireEvent,
    audit,
    normalize_digits,
)

_FIXTURE_PATH = os.path.join(os.path.dirname(__file__), "masking_fixtures.json")


@pytest.fixture(scope="module")
def fixtures():
    with open(_FIXTURE_PATH, encoding="utf-8") as fh:
        return json.load(fh)


@pytest.fixture(scope="module")
def replayed(fixtures):
    """Replay all fixture messages through ONE seeded codec, in order.

    Returns (codec, per-message actual encode/decode outputs).
    """
    codec = PrivacyCodec(rng=random.Random(fixtures["seed"]))
    actual = []
    for msg in fixtures["messages"]:
        enc = codec.encode(msg["input"])
        dec = codec.decode(enc)
        actual.append({"encode": enc, "roundtrip": dec.text})
    return codec, actual


# ---------------------------------------------------------------------------
# Layer 1 — byte-exact fixture assertions (categories 1–14)
# ---------------------------------------------------------------------------


def test_fixture_encode_byte_exact(fixtures, replayed):
    _codec, actual = replayed
    for msg, got in zip(fixtures["messages"], actual):
        assert got["encode"] == msg["expected_encode"], (
            f"[{msg['id']}] {msg['note']}"
        )


def test_fixture_roundtrip_restores(fixtures, replayed):
    _codec, actual = replayed
    for msg, got in zip(fixtures["messages"], actual):
        assert got["roundtrip"] == msg["expected_roundtrip"], (
            f"[{msg['id']}] {msg['note']}"
        )


def test_fixture_excluded_are_byte_identical(fixtures, replayed):
    """Cat 3/4/5/6 exclusions: encode output identical to input (byte-for-byte)."""
    _codec, actual = replayed
    for msg, got in zip(fixtures["messages"], actual):
        if msg["excluded"]:
            assert got["encode"] == msg["input"], f"[{msg['id']}] {msg['note']}"


def test_fixture_no_leaks_after_encode(fixtures, replayed):
    """Completeness: re-running the detector on each encoded output finds only
    known fakes (zero leak candidates)."""
    codec, actual = replayed
    for msg, got in zip(fixtures["messages"], actual):
        hits = audit(got["encode"], codec.fake_values)
        assert hits == [], f"[{msg['id']}] leak candidates: {hits}"


def test_fixture_final_mapping_matches(fixtures, replayed):
    codec, _actual = replayed
    assert codec.real_to_fake == fixtures["final_real_to_fake"]


def test_fixture_consistency_same_value_one_fake(fixtures, replayed):
    """Cat 11: the same underlying value (across scripts/formats) → one fake."""
    codec, _actual = replayed
    # phone 0501234567 written 3 ways (plain / dashed / Arabic-Indic) → one fake
    assert "0501234567" in codec.real_to_fake
    # ID 1023456789 written ASCII + Arabic-Indic + repeated → one fake
    assert "1023456789" in codec.real_to_fake
    # one fake per real, one real per fake (bijection)
    assert len(set(codec.real_to_fake.values())) == len(codec.real_to_fake)


# ---------------------------------------------------------------------------
# Shape rule assertions over the fixture mapping
# ---------------------------------------------------------------------------


def _keep(length):
    return 3 if length <= 7 else 4


def test_number_fakes_preserve_shape(replayed):
    codec, _actual = replayed
    for real, fake in codec.real_to_fake.items():
        if "@" in real:
            continue
        assert len(fake) == len(real), (real, fake)
        keep = _keep(len(real))
        assert fake[:keep] == real[:keep], (real, fake)  # kept prefix
        assert fake != real
        assert fake.isdigit()


def test_email_fakes_use_neutral_domain(replayed):
    codec, _actual = replayed
    for real, fake in codec.real_to_fake.items():
        if "@" not in real:
            continue
        assert fake.endswith("@example.com")
        real_domain = real.split("@", 1)[1]
        assert not fake.endswith("@" + real_domain)
        assert fake != real


# ---------------------------------------------------------------------------
# Category 13 — flag OFF passthrough
# ---------------------------------------------------------------------------


def test_passthrough_byte_identical_when_disabled():
    codec = PrivacyCodec(enabled=False)
    msg = "هوية 1023456789 وايميل a.b@corp.io ومبلغ 500000"
    assert codec.encode(msg) == msg
    assert codec.new_mappings == []  # nothing recorded
    assert codec.real_to_fake == {}


def test_decode_active_even_when_disabled():
    """Decode must ALWAYS run (never gated by the flag)."""
    codec = PrivacyCodec(
        real_to_fake={"1023456789": "1023835840"},
        fake_to_real={"1023835840": "1023456789"},
        enabled=False,
    )
    out = codec.decode("الناتج 1023835840 هنا")
    assert out.text == "الناتج 1023456789 هنا"
    assert out.restored_count == 1


# ---------------------------------------------------------------------------
# Category 11/14 — idempotence
# ---------------------------------------------------------------------------


def test_encode_idempotent():
    codec = PrivacyCodec(rng=random.Random(1))
    msg = "هوية 1023456789 جوال 0501234567 ايميل x.y@firm.com مبلغ 861234"
    once = codec.encode(msg)
    twice = codec.encode(once)
    assert once == twice


def test_mixed_real_and_encoded_single_pass():
    """Cat 14: a prompt assembled from real WI content + an already-encoded
    question → one encode pass masks the reals and leaves existing fakes."""
    codec = PrivacyCodec(rng=random.Random(2))
    question = codec.encode("سؤالي عن الهوية 1099887766")
    # question now contains a fake; assemble with fresh real WI content
    real_wi = "محتوى القضية يذكر الآيبان SA80 1000 0000 1111 2222 3333"
    prompt = real_wi + "\n\n" + question
    encoded = codec.encode(prompt)
    # the previously-encoded fake is untouched (idempotent)
    assert question in encoded
    # and a second pass is a no-op
    assert codec.encode(encoded) == encoded
    # the real WI IBAN got masked (no leak)
    assert audit(encoded, codec.fake_values) == []


# ---------------------------------------------------------------------------
# Category 12 — decode specifics
# ---------------------------------------------------------------------------


def test_decode_arabic_indic_output_digits():
    """The model may re-emit a fake in Arabic-Indic digits — decode normalizes."""
    codec = PrivacyCodec(
        real_to_fake={"1023456789": "1023835840"},
        fake_to_real={"1023835840": "1023456789"},
    )
    # fake 1023835840 written with Arabic-Indic digits
    ai = "".join(chr(0x0660 + int(d)) for d in "1023835840")
    out = codec.decode(f"الرقم {ai} انتهى")
    assert out.text == "الرقم 1023456789 انتهى"
    assert out.restored_count == 1


def test_decode_separated_fake_collapses_to_real():
    codec = PrivacyCodec(
        real_to_fake={"0501234567": "0501532865"},
        fake_to_real={"0501532865": "0501234567"},
    )
    out = codec.decode("اتصل على 050-153-2865 اليوم")
    assert out.text == "اتصل على 0501234567 اليوم"  # formatting loss accepted
    assert out.restored_count == 1


def test_tripwire_on_mangled_fake_no_prefix_decode():
    codec = PrivacyCodec(
        real_to_fake={"1023456789": "1023835840"},
        fake_to_real={"1023835840": "1023456789"},
    )
    mangled = "1023835841"  # same 4-prefix + length, tail differs
    out = codec.decode(f"القيمة {mangled} مشوّهة")
    assert out.restored_count == 0
    assert mangled in out.text  # NOT decoded by prefix
    assert len(out.tripwires) == 1
    tw = out.tripwires[0]
    assert isinstance(tw, TripwireEvent)
    assert tw.kind == "number"
    assert tw.prefix == "1023"
    assert tw.length == 10


def test_exact_fake_decodes_no_tripwire():
    codec = PrivacyCodec(
        real_to_fake={"1023456789": "1023835840"},
        fake_to_real={"1023835840": "1023456789"},
    )
    out = codec.decode("صحيح 1023835840 تمام")
    assert out.restored_count == 1
    assert out.tripwires == []


# ---------------------------------------------------------------------------
# decode honours dates the way encode does
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "real",
    [
        pytest.param("القضية 4471234 12/3/1446", id="before-a-slash-date"),
        pytest.param("القضية 12/3/1446 4471234", id="after-a-slash-date"),
        pytest.param("القضية 4471234 12-3-1446", id="before-a-dash-date"),
        pytest.param("القضية 12-3-1446 4471234", id="after-a-dash-date"),
        pytest.param("القضية 4471234 2024-05-12", id="before-a-gregorian-date"),
        pytest.param("القضية 4471234-12-3-1446", id="dashed-onto-a-date"),
        pytest.param("بين 4471234 12/3/1446 4471999", id="between-two-numbers"),
        pytest.param("القضية 4471234 12/3/1446 ثم 4471999 1/4/1446", id="two-of-each"),
    ],
)
def test_fake_beside_a_date_round_trips(real):
    codec = PrivacyCodec(rng=random.Random(7))
    encoded = codec.encode(real)
    assert encoded != real
    out = codec.decode(encoded)
    assert out.text == real
    assert out.restored_count == len(codec.new_mappings)
    assert out.tripwires == []


def test_separated_fake_beside_a_date_collapses_to_real():
    codec = PrivacyCodec(rng=random.Random(7))
    encoded = codec.encode("الجوال 0551-234-567 12/3/1446")
    assert codec.decode(encoded).text == "الجوال 0551234567 12/3/1446"


def test_arabic_indic_date_beside_a_fake_stays_as_written():
    codec = PrivacyCodec(rng=random.Random(7))
    encoded = codec.encode("القضية 4471234 ١٢/٣/١٤٤٦")
    assert codec.decode(encoded).text == "القضية 4471234 ١٢/٣/١٤٤٦"


def test_a_fake_shaped_like_a_date_still_decodes():
    """``15-9-1500`` is no date, so it masks, and its random tail can make the fake
    look like one. The date barrier comes second, or this stays a fake."""
    codec = PrivacyCodec(
        real_to_fake={"1591500": "1591446"}, fake_to_real={"1591446": "1591500"}
    )
    assert codec.decode("الرقم 15-9-1446").text == "الرقم 1591500"


def test_text_with_no_fake_is_byte_identical_beside_a_date():
    codec = PrivacyCodec(
        real_to_fake={"4471234": "4479876"}, fake_to_real={"4479876": "4471234"}
    )
    text = "حكم 12/3/1446 7654321 و 12-3-1446 9998887 2020-2021 1/4/1446"
    out = codec.decode(text)
    assert out.text == text
    assert out.restored_count == 0


def test_a_mangled_fake_beside_a_date_is_not_decoded_by_prefix():
    codec = PrivacyCodec(
        real_to_fake={"4471234": "4479876"}, fake_to_real={"4479876": "4471234"}
    )
    text = "القضية 4479870 12/3/1446"
    assert codec.decode(text).text == text


# ---------------------------------------------------------------------------
# leaves_fake: the check before a store-real write
# ---------------------------------------------------------------------------

_ONE_NUMBER = {"real_to_fake": {"4471234": "4479876"}, "fake_to_real": {"4479876": "4471234"}}


def test_leaves_fake_is_false_on_all_that_decode_restores():
    codec = PrivacyCodec(rng=random.Random(7))
    encoded = codec.encode("القضية 4471234 12/3/1446 والبريد a.b@client.sa والهوية 1023456789")
    assert not codec.leaves_fake(encoded, encoded)


@pytest.mark.parametrize(
    "written",
    [
        pytest.param("القضية 4479876 25 يوماً", id="a-small-number-after-it"),
        pytest.param("البند 5 4479876", id="a-small-number-before-it"),
        pytest.param("القضية 4479876 9999999", id="another-number-after-it"),
        pytest.param("القضية 4479876 5000 ريال", id="joined-to-an-amount"),
    ],
)
def test_leaves_fake_finds_a_fake_with_digits_joined_to_it(written):
    assert PrivacyCodec(**_ONE_NUMBER).leaves_fake(written)


def test_leaves_fake_finds_the_pieces_of_a_run_masked_as_one():
    """Two numbers a space apart are one run to encode, so one fake. Written apart,
    neither piece decodes."""
    codec = PrivacyCodec(rng=random.Random(7))
    source = codec.encode("القضيتان 4471234 4471235")
    first, second = source.split()[-2:]

    assert codec.leaves_fake(f"القضية {first} والقضية {second}", source)
    assert codec.leaves_fake(f"القضية {second}", source)
    assert not codec.leaves_fake(f"القضيتان {first} {second}", source)


def test_leaves_fake_finds_a_damaged_tail():
    codec = PrivacyCodec(**_ONE_NUMBER)
    assert codec.leaves_fake("القضية 4479870", "القضية 4479876")


def test_leaves_fake_finds_a_damaged_fake_email():
    codec = PrivacyCodec(rng=random.Random(7))
    source = codec.encode("البريد a.b@client.sa")
    assert codec.leaves_fake("البريد wrong123@example.com", source)


@pytest.mark.parametrize(
    "written",
    [
        pytest.param("المبلغ 4475000 ريال", id="an-amount-of-a-fakes-shape"),
        pytest.param("دفع 4475000 ر.س", id="the-short-currency-mark"),
        pytest.param("تسلّم 4,475,000 ريال", id="a-grouped-amount"),
        pytest.param("تسلّم 4,475,000", id="a-grouped-amount-with-no-currency"),
        pytest.param("مبلغ ٤٤٧٥٠٠٠ ريال", id="arabic-indic-digits"),
        pytest.param("بتاريخ 12/3/1446", id="a-hijri-date"),
        pytest.param("بتاريخ 15-9-1446", id="a-dash-date"),
        pytest.param("بتاريخ 2024-05-12", id="a-gregorian-date"),
        pytest.param("سنة 1446 الموافق 2024", id="bare-years"),
        pytest.param("المادة 77 من النظام", id="an-article"),
        pytest.param("نسبة 15% من 2000", id="small-figures"),
        pytest.param("الرقم 1234567", id="an-unrelated-long-number"),
    ],
)
def test_leaves_fake_refuses_no_ordinary_figure(written):
    assert not PrivacyCodec(**_ONE_NUMBER).leaves_fake(written)


def test_leaves_fake_never_refuses_a_known_real_value():
    """A real shares its fake's kept prefix and length, and it would otherwise read
    as a damaged fake once decoded."""
    codec = PrivacyCodec(**_ONE_NUMBER)
    assert not codec.leaves_fake("الرقم 4471234")
    assert not codec.leaves_fake("الرقم 4479876")


def test_leaves_fake_lets_a_number_copied_from_the_input_through():
    """It was unmasked next to «ريال», and the model dropped the word. The shape
    alone would refuse it, which is why the check reads what the model read."""
    codec = PrivacyCodec(**_ONE_NUMBER)
    assert codec.leaves_fake("قيمة العقد 4475000")
    assert not codec.leaves_fake("قيمة العقد 4475000", "الثمن 4475000 ريال")


def test_leaves_fake_ignores_short_pieces_of_a_masked_run():
    """An IBAN's groups are ordinary numbers on their own."""
    codec = PrivacyCodec(rng=random.Random(7))
    source = codec.encode("الآيبان SA03 8000 0000 6080 1016 7519")
    piece = source.split()[-1]
    assert len(piece) == 4
    assert not codec.leaves_fake(f"دفع {piece} ريال", source)
    assert not codec.leaves_fake(f"البند {piece}", source)


# ---------------------------------------------------------------------------
# audit() helper (Layer 4 detector-as-auditor)
# ---------------------------------------------------------------------------


def test_audit_flags_unknown_number_as_leak():
    known = {"1023835840"}
    hits = audit("رقم مسرّب 5566778899 هنا", known)
    assert hits == [AuditHit("number", 10)]


def test_audit_ignores_known_fakes_and_exclusions():
    known = {"1023835840"}
    text = "فيه فيك 1023835840 وتاريخ 1446/09/15 ومبلغ 500000 ريال"
    assert audit(text, known) == []


def test_audit_flags_leaked_email():
    hits = audit("تسريب real.person@company.net فقط", set())
    assert hits == [AuditHit("email", len("real.person@company.net"))]


# ---------------------------------------------------------------------------
# normalize_digits basics
# ---------------------------------------------------------------------------


def test_normalize_digits_length_preserved_and_1to1():
    src = "abc ١٢٣ ۴۵۶ 789 !@"
    out = normalize_digits(src)
    assert len(out) == len(src)  # index preservation
    assert out == "abc 123 456 789 !@"


def test_new_mappings_tracks_created_fakes():
    codec = PrivacyCodec(rng=random.Random(3))
    codec.encode("هوية 1023456789 وايميل z@z.io")
    kinds = sorted(m.kind for m in codec.new_mappings)
    assert kinds == ["email", "number"]
    assert len(codec.new_mappings) == 2
