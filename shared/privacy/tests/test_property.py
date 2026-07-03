# -*- coding: utf-8 -*-
"""Layer-2 property/fuzz tests — invariants over generated messages.

A generator composes random Arabic/English messages from PII-shaped values
(IDs, phones ±dashes/spaces/Arabic-Indic, IBANs, emails), legal numbers
(amounts + money words, Hijri/Gregorian dates, article/case refs) and noise.
The generator knows each segment's rendered form AND its post-round-trip
canonical form, so assertions are independent of the codec internals.

Six invariants asserted per message (plan Layer 2):
  1. Completeness  — audit(encode(m)) finds only known fakes (no real survives).
  2. Round-trip    — decode(encode(m)) == the generator's canonical expectation.
  3. Exclusion safety — each date/money/ref segment is byte-identical in encode.
  4. Consistency   — same value → one fake (checked via the codec mapping).
  5. Shape         — length preserved + prefix rule (4 / 3) respected.
  6. Idempotence   — encode(encode(m)) == encode(m).

Segments are separated by a >16-char digit/marker-free filler so that a money
marker or date in one segment can never reach a PII number in an adjacent one
(the codec's money-proximity window is 16 chars — a real property, not a bug).

Runtime target: a few thousand cases well under ~60s (single internal loop).
"""
import random

from shared.privacy.codec import PrivacyCodec, audit, normalize_digits

_N_CASES = 3000

_AR_WORDS = [
    "العميل", "الطرف", "القضية", "العقد", "المحكمة", "المستفيد", "الجلسة",
    "الرقم", "الملف", "الطلب", "المذكرة", "الحساب", "التاريخ", "الوكيل",
    "بشأن", "حول", "لدى", "وبعد", "ثم", "كذلك", "وأيضا", "بخصوص",
]
_EN_WORDS = ["ref", "note", "case", "party", "file", "client", "regarding"]
_EMAIL_DOMAINS = ["acme-corp.com", "law-firm.sa", "company.net", "gmail.com", "x.io"]
_EMAIL_LOCALS = ["ahmad.ali", "s.mohammed", "info", "contact", "n_alfaisal", "legal99"]


def _to_arabic_indic(s: str) -> str:
    return "".join(chr(0x0660 + int(c)) if c.isdigit() else c for c in s)


def _digits(rng, n):
    return "".join(rng.choice("0123456789") for _ in range(n))


class _Seg:
    __slots__ = ("rendered", "canonical", "excluded")

    def __init__(self, rendered, canonical, excluded):
        self.rendered = rendered
        self.canonical = canonical
        self.excluded = excluded


def _pii_id(rng):
    d = "1" + _digits(rng, 9)  # 10-digit ID
    if rng.random() < 0.3:
        return _Seg(_to_arabic_indic(d), d, False)
    return _Seg(d, d, False)


def _pii_phone(rng):
    core = "05" + _digits(rng, 8)  # 10-digit local
    style = rng.randint(0, 4)
    if style == 0:
        rendered, canonical = core, core
    elif style == 1:
        rendered = f"{core[:3]}-{core[3:6]}-{core[6:]}"
        canonical = core
    elif style == 2:
        rendered = f"{core[:3]} {core[3:6]} {core[6:]}"
        canonical = core
    elif style == 3:
        rendered = _to_arabic_indic(core)
        canonical = core
    else:
        tail = "966" + core[1:]  # run = 966 + local without leading 0
        rendered = f"+966 {core[1:3]} {core[3:6]} {core[6:]}"
        canonical = "+" + tail
    return _Seg(rendered, canonical, False)


def _pii_iban(rng):
    groups = ["44"] + [_digits(rng, 4) for _ in range(5)]
    rendered = "SA" + groups[0] + " " + " ".join(groups[1:])
    canonical = "SA" + "".join(groups)
    return _Seg(rendered, canonical, False)


def _pii_email(rng):
    addr = f"{rng.choice(_EMAIL_LOCALS)}@{rng.choice(_EMAIL_DOMAINS)}"
    return _Seg(addr, addr, False)


def _excl_date(rng):
    kind = rng.randint(0, 3)
    sep = rng.choice(["/", "-"])
    if kind == 0:
        y = rng.choice(["1446", "1440", "1355", "1400"])
        s = f"{rng.randint(1,28):02d}{sep}{rng.randint(1,12):02d}{sep}{y}"
    elif kind == 1:
        y = rng.choice(["1446", "1445"])
        s = f"{y}{sep}{rng.randint(1,12):02d}{sep}{rng.randint(1,28):02d}"
    elif kind == 2:
        y = rng.choice(["2024", "2035", "1998", "2001"])
        s = f"{y}{sep}{rng.randint(1,12):02d}{sep}{rng.randint(1,28):02d}"
    else:
        y = rng.choice(["1446", "2024"])
        s = f"{y}{sep}{rng.randint(1,12):02d}"
    return _Seg(s, s, True)


def _excl_money(rng):
    amount = _digits(rng, rng.randint(5, 7))
    word = rng.choice(["ريال", "دولار", "SAR"])
    s = f"مبلغ {amount} {word}" if rng.random() < 0.5 else f"{amount} {word}"
    return _Seg(s, s, True)


def _excl_ref(rng):
    s = f"المادة {rng.randint(1, 999)}"  # ≤3 digits, <5 → untouched
    return _Seg(s, s, True)


def _noise_word(rng):
    return rng.choice(_AR_WORDS + _EN_WORDS)


def _noise_seg(rng):
    w = _noise_word(rng)
    return _Seg(w, w, True)


def _filler(rng):
    """A digit-free, money-marker-free separator of > 16 chars."""
    while True:
        f = " " + " ".join(_noise_word(rng) for _ in range(rng.randint(4, 6))) + " "
        if len(f) > 18:
            return f


_PII_MAKERS = [_pii_id, _pii_phone, _pii_iban, _pii_email]
_EXCL_MAKERS = [_excl_date, _excl_money, _excl_ref]


def _make_message(rng):
    """Return (message, expected_roundtrip, segments)."""
    n = rng.randint(1, 6)
    segs: list[_Seg] = []
    repeatable: list[_Seg] = []
    for _ in range(n):
        r = rng.random()
        if r < 0.45:
            if repeatable and rng.random() < 0.35:
                seg = rng.choice(repeatable)
            else:
                seg = rng.choice(_PII_MAKERS)(rng)
                repeatable.append(seg)
        elif r < 0.75:
            seg = rng.choice(_EXCL_MAKERS)(rng)
        else:
            seg = _noise_seg(rng)
        segs.append(seg)

    rendered_parts: list[str] = []
    canonical_parts: list[str] = []
    for seg in segs:
        f = _filler(rng)
        rendered_parts.append(f)
        canonical_parts.append(f)
        rendered_parts.append(seg.rendered)
        canonical_parts.append(seg.canonical)
    tail = _filler(rng)
    rendered_parts.append(tail)
    canonical_parts.append(tail)
    return "".join(rendered_parts), "".join(canonical_parts), segs


def _keep(length):
    return 3 if length <= 7 else 4


def test_invariants_bulk():
    failures = []
    for case in range(_N_CASES):
        rng = random.Random(1_000_000 + case)
        codec = PrivacyCodec(rng=random.Random(9_000_000 + case))
        message, expected_roundtrip, segs = _make_message(rng)
        encoded = codec.encode(message)

        # 1. Completeness
        leaks = audit(encoded, codec.fake_values)
        if leaks:
            failures.append(f"[{case}] LEAK {leaks} enc={encoded!r}")
            continue

        # 2. Round-trip
        decoded = codec.decode(encoded).text
        if decoded != expected_roundtrip:
            failures.append(
                f"[{case}] ROUNDTRIP got={decoded!r} exp={expected_roundtrip!r}"
            )
            continue

        # 3. Exclusion safety
        for seg in segs:
            if seg.excluded and seg.rendered.strip() and seg.rendered not in encoded:
                failures.append(f"[{case}] EXCL altered {seg.rendered!r} enc={encoded!r}")
                break

        # 5. Shape
        for real, fake in codec.real_to_fake.items():
            if "@" in real:
                if not (fake.endswith("@example.com") and fake != real):
                    failures.append(f"[{case}] EMAIL shape {real!r}->{fake!r}")
            else:
                k = _keep(len(real))
                if not (len(fake) == len(real) and fake[:k] == real[:k] and fake != real):
                    failures.append(f"[{case}] NUM shape {real!r}->{fake!r}")

        # 6. Idempotence
        if codec.encode(encoded) != encoded:
            failures.append(f"[{case}] IDEMPOTENCE enc={encoded!r}")

    assert not failures, "invariant failures (first 10):\n" + "\n".join(failures[:10])


def test_consistency_same_value_one_fake_bulk():
    """Invariant 4: a value repeated (any script/format) → a single fake, and
    the mapping stays a bijection."""
    codec = PrivacyCodec(rng=random.Random(555))
    real = "1055667788"
    variants = [
        real,
        _to_arabic_indic(real),
        f"{real[:3]}-{real[3:6]}-{real[6:]}",
        f"{real[:3]} {real[3:6]} {real[6:]}",
    ]
    fakes = set()
    for v in variants:
        enc = codec.encode(f"القيمة بشأن ولدى وبعد ذلك {v} ثم انتهى")
        fakes.add(codec.real_to_fake[real])
        assert normalize_digits(real) in codec.decode(enc).text
    assert len(fakes) == 1
    assert len(set(codec.real_to_fake.values())) == len(codec.real_to_fake)
