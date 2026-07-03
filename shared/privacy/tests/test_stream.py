# -*- coding: utf-8 -*-
"""StreamDecoder tests — fakes split across 2–3 SSE chunks must restore.

Only the text API is exercised here; heartbeat/non-text passthrough is the
wiring phase's concern.
"""
import random

from shared.privacy.codec import PrivacyCodec
from shared.privacy.stream import StreamDecoder


def _fresh():
    return PrivacyCodec(
        real_to_fake={"1023456789": "1023835840"},
        fake_to_real={"1023835840": "1023456789"},
    )


def _drive(sd, chunks):
    out = ""
    for ch in chunks:
        out += sd.feed(ch)
    out += sd.finalize()
    return out


def test_fake_split_across_two_chunks():
    c = _fresh()
    sd = StreamDecoder(c)
    # fake 1023835840 split "10238" | "35840"
    out = _drive(sd, ["الهوية 10238", "35840 انتهى"])
    assert out == "الهوية 1023456789 انتهى"
    assert sd.restored_count == 1


def test_fake_split_across_three_chunks():
    c = _fresh()
    sd = StreamDecoder(c)
    out = _drive(sd, ["قبل ", "1023", "8358", "40 بعد"])
    assert out == "قبل 1023456789 بعد"
    assert sd.restored_count == 1


def test_fake_split_on_separator_boundary():
    """Model re-emits the fake with dashes, split lands on a dash."""
    c = _fresh()
    sd = StreamDecoder(c)
    # 1023-835-840 joins to 1023835840
    out = _drive(sd, ["رقم 1023-835-", "840 تم"])
    assert out == "رقم 1023456789 تم"
    assert sd.restored_count == 1


def test_no_holdback_when_ending_on_nonrun_char():
    c = _fresh()
    sd = StreamDecoder(c)
    first = sd.feed("النص كامل هنا.")  # ends on non-run char → nothing held
    assert sd.pending == ""
    assert first == "النص كامل هنا."


def test_trailing_digits_held_until_flush():
    c = _fresh()
    sd = StreamDecoder(c)
    emitted = sd.feed("العدد 1023")  # trailing digits → held back
    assert "1023" not in emitted
    assert "1023" in sd.pending  # digits held (a leading space may ride along)
    rest = sd.finalize()
    assert (emitted + rest) == "العدد 1023"  # reconstitutes with no loss


def test_arabic_indic_fake_split_across_chunks():
    c = _fresh()
    sd = StreamDecoder(c)
    ai = "".join(chr(0x0660 + int(d)) for d in "1023835840")
    out = _drive(sd, ["قيمة " + ai[:4], ai[4:] + " انتهى"])
    assert out == "قيمة 1023456789 انتهى"


def test_holdback_cap_does_not_corrupt_long_nonfake_run():
    c = _fresh()
    sd = StreamDecoder(c, holdback_cap=8)
    long_run = "9" * 40  # not a fake; longer than the cap
    out = _drive(sd, ["س ", long_run[:20], long_run[20:], " ن"])
    assert out == "س " + long_run + " ن"  # passes through intact
    assert sd.restored_count == 0


def test_multiple_fakes_one_stream():
    c = PrivacyCodec(
        real_to_fake={"1023456789": "1023835840", "5560001111": "5569998888"},
        fake_to_real={"1023835840": "1023456789", "5569998888": "5560001111"},
    )
    sd = StreamDecoder(c)
    out = _drive(sd, ["أول 10238", "35840 وثاني 55699", "98888 نهاية"])
    assert out == "أول 1023456789 وثاني 5560001111 نهاية"
    assert sd.restored_count == 2


def test_empty_and_incremental_feeds():
    c = _fresh()
    sd = StreamDecoder(c)
    assert sd.feed("") == ""
    parts = list("الهوية 1023835840 ثم")
    out = "".join(sd.feed(ch) for ch in parts) + sd.finalize()
    assert out == "الهوية 1023456789 ثم"
