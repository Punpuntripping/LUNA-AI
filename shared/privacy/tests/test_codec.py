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
