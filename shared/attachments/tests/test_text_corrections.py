"""Text corrections (migration 234, attachment_agent.md §3.3): the pure overlay,
the checks before a fix is stored, decoding masked fakes, and the store round
trip on a fake client. Synthetic text only."""
from __future__ import annotations

import random
from dataclasses import FrozenInstanceError

import pytest

from agents.memory.angle_mapper.tests.fake_supabase import FakeSupabase
from agents.memory.attachment_segmenter.models import StoredSegment
from agents.utils.attachment_delivery import Section
from shared.attachments import text_corrections as tc
from shared.attachments.text_corrections import (
    FixCheck,
    TextCorrection,
    apply_text_corrections,
    apply_to_sections,
    check_text_fix,
    corrections_for_items,
    decode_for_store,
    load_text_corrections,
    revert_text_correction,
    save_text_correction,
)
from shared.privacy.codec import PrivacyCodec

FILE = "f1f1f1f1-0000-4000-8000-00000000000a"
FILE_2 = "f2f2f2f2-0000-4000-8000-00000000000b"
USER = "user-7"


@pytest.fixture(autouse=True)
def _no_backoff():
    tc.reset_backoff()
    yield
    tc.reset_backoff()


def fix(wrong: str, right: str, at: str, i: int = 1) -> TextCorrection:
    return TextCorrection(id=f"c{i}", file_id=FILE, wrong=wrong, right=right, occurrences=1, created_at=at)


# ── apply ─────────────────────────────────────────────────────────────────────


def test_apply_replaces_every_occurrence():
    text = "حضر محمود. وقال محمود إن محمود سدد."
    assert apply_text_corrections(text, [fix("محمود", "محمد", "2026-11-27T10:00:00+00:00")]) == (
        "حضر محمد. وقال محمد إن محمد سدد."
    )


def test_apply_in_created_at_order_not_list_order():
    a = fix("أ", "ب", "2026-11-27T10:00:00+00:00", 1)
    b = fix("ب", "ج", "2026-11-27T10:05:00+00:00", 2)
    # a then b: أ → ب → ج; the list order does not matter
    assert apply_text_corrections("أ", [b, a]) == "ج"
    # the other way round in time: ب → ج first, then أ → ب
    a2 = fix("أ", "ب", "2026-11-27T10:10:00+00:00", 3)
    assert apply_text_corrections("أ", [b, a2]) == "ب"


def test_apply_no_corrections_is_a_no_op():
    text = "نص بلا تصحيح"
    assert apply_text_corrections(text, []) is text
    assert apply_text_corrections(text, None) is text
    assert apply_text_corrections("", [fix("أ", "ب", "x")]) == ""


def test_apply_accepts_rows_and_dicts():
    rows = [{"wrong_text": "عمرو", "right_text": "عمر", "created_at": "1"}, {"wrong": "زيد", "right": "يزيد", "created_at": "2"}]
    assert apply_text_corrections("عمرو وزيد", rows) == "عمر ويزيد"


def test_apply_to_sections_every_shape_and_input_untouched():
    c = [fix("محمود", "محمد", "1")]
    sec = Section(seq=1, value="core", what="w", text="قال محمود", char_start=0, char_end=9)
    seg = StoredSegment(seq=2, from_p=1, to_p=1, char_start=10, char_end=20, label="x", value="core",
                        content_md="شهد محمود")
    d = {"seq": 3, "content_md": "محمود هنا", "text": "محمود"}
    out = apply_to_sections([sec, seg, d, 42], c)
    assert out[0].text == "قال محمد" and out[0].char_start == 0 and out[0].char_end == 9
    assert out[1].content_md == "شهد محمد" and out[1].char_end == 20
    assert out[2] == {"seq": 3, "content_md": "محمد هنا", "text": "محمد"}
    assert out[3] == 42
    # originals unchanged
    assert sec.text == "قال محمود" and seg.content_md == "شهد محمود" and d["content_md"] == "محمود هنا"
    with pytest.raises(FrozenInstanceError):
        out[0].text = "x"  # still the frozen dataclass


def test_apply_to_sections_without_corrections_returns_the_same_objects():
    sec = Section(seq=1, value="core", what="w", text="t")
    assert apply_to_sections([sec], [])[0] is sec


# ── check_text_fix ────────────────────────────────────────────────────────────

TEXT = "حضر المدعي محمود بن علي. وقال محمود: سدّدت. ومحمود لم يحضر بعدها. والمحمود مقبول.\nسطر آخر"


def test_check_ok_counts_every_occurrence_including_proclitics():
    r = check_text_fix(TEXT, None, "محمود", "محمد")
    assert r == FixCheck(True, 4, "")


@pytest.mark.parametrize(
    "wrong,right,reason",
    [
        ("", "محمد", tc.REASON_EMPTY_WRONG),
        ("   ", "محمد", tc.REASON_EMPTY_WRONG),
        ("محمود", "", tc.REASON_EMPTY_RIGHT),
        ("محمود", "محمود", tc.REASON_SAME),
        ("يحضر بعدها.\nسطر", "x", tc.REASON_NEWLINE),
        ("محمود", "محمد\nعلي", tc.REASON_NEWLINE),
        ("محمود\r", "محمد", tc.REASON_NEWLINE),
        ("خالد", "محمد", tc.REASON_NOT_FOUND),
        ("م" * (tc.MAX_FIX_CHARS + 1), "محمد", tc.REASON_TOO_LONG),
    ],
)
def test_check_refusals(wrong, right, reason):
    r = check_text_fix(TEXT, None, wrong, right)
    assert not r.ok and r.reason == reason


def test_check_refuses_a_match_inside_a_longer_word():
    # «علي» is also the start of «عليه»: fixing every occurrence would break it
    r = check_text_fix("قال علي إن الدين عليه", None, "علي", "عالي")
    assert not r.ok and r.reason == tc.REASON_INSIDE_WORD and r.count == 2
    # a letter in front that is not a proclitic: «حمد» inside «محمد»
    assert check_text_fix("محمد", None, "حمد", "حامد").reason == tc.REASON_INSIDE_WORD
    # digits too: «1234» inside «12345»
    assert check_text_fix("رقم 12345 و 1234", None, "1234", "1235").reason == tc.REASON_INSIDE_WORD


def test_check_whole_word_with_tashkeel_after_it_is_fine():
    r = check_text_fix("جاء محمودٌ ثم محمودُ", None, "محمود", "محمد")
    assert r.ok and r.count == 2


def test_check_refuses_a_match_across_a_section_join():
    full = "الطرف الأول شركة النور للتجارة"
    # the section cut falls inside «النور للتجارة»
    cut = full.index(" للتجارة")
    r = check_text_fix(full, [(0, cut), (cut, len(full))], "النور للتجارة", "النور للتجارة والمقاولات")
    assert not r.ok and r.reason == tc.REASON_CROSSES_JOIN and r.count == 1


def test_check_counts_match_across_sections():
    full = "قال محمود.\nثم محمود."
    r = check_text_fix(full, [(0, 10), (11, len(full))], "محمود", "محمد")
    assert r.ok and r.count == 2


def test_check_allows_a_match_outside_every_section():
    # a page letterhead that a joined section leaves out still holds the word
    full = "مكتب محمود للمحاماة\nالبند الأول: يلتزم محمود\nمكتب محمود للمحاماة\nالبند الثاني"
    first = full.index("البند الأول")
    second_end = full.index("\nمكتب", first)
    bounds = [(first, second_end), (full.index("البند الثاني"), len(full))]
    r = check_text_fix(full, bounds, "محمود", "محمد")
    assert r.ok and r.count == 3


def test_check_edges_touching_a_match_are_not_a_crossing():
    full = "محمود يلتزم"
    assert check_text_fix(full, [(0, 5), (5, len(full))], "محمود", "محمد").ok      # ends at an edge
    assert not check_text_fix(full, [(0, 3), (3, len(full))], "محمود", "محمد").ok  # an edge inside


def test_reasons_never_quote_the_text():
    secret = "1098765432"
    for args in [(secret, "1"), ("x" + secret, secret + "\n")]:
        assert secret not in check_text_fix("نص " + secret, None, *args).reason


# ── decode_for_store ──────────────────────────────────────────────────────────

REAL = "1098765432"


def _codec(enabled: bool = True) -> PrivacyCodec:
    c = PrivacyCodec(rng=random.Random(7), enabled=True)
    c.encode(f"هوية {REAL}")
    c.enabled = enabled
    return c


def _damaged(fake: str) -> str:
    """Same length and kept prefix as the fake, another tail: no real value."""
    for d in "0123456789":
        cand = fake[:-1] + d
        if cand not in (fake, REAL):
            return cand
    raise AssertionError


def test_decode_restores_a_fake():
    codec = _codec()
    fake = codec.real_to_fake[REAL]
    assert fake != REAL
    assert decode_for_store(f"رقم الهوية {fake}", codec=codec) == f"رقم الهوية {REAL}"


def test_decode_refuses_a_damaged_fake():
    codec = _codec()
    bad = _damaged(codec.real_to_fake[REAL])
    assert decode_for_store(f"رقم الهوية {bad}", codec=codec) is None


def test_decode_passthrough_without_codec():
    assert decode_for_store("محمد 1098765432", codec=None) == "محمد 1098765432"
    assert decode_for_store("", codec=_codec()) == ""


def test_decode_with_masking_off_still_decodes_and_does_not_refuse():
    codec = _codec(enabled=False)
    fake = codec.real_to_fake[REAL]
    assert decode_for_store(f"هوية {fake}", codec=codec) == f"هوية {REAL}"
    assert decode_for_store(f"هوية {_damaged(fake)}", codec=codec) is not None


def test_decode_uses_the_active_turn_codec():
    from backend.app.services.masking_service import reset_active_codec, set_active_codec

    codec = _codec()
    fake = codec.real_to_fake[REAL]
    token = set_active_codec(codec)
    try:
        assert decode_for_store(f"هوية {fake}") == f"هوية {REAL}"
        assert decode_for_store(f"هوية {_damaged(fake)}") is None
    finally:
        reset_active_codec(token)
    assert decode_for_store(f"هوية {fake}") == f"هوية {fake}"  # no turn codec: unchanged


def test_decode_error_refuses():
    class Broken:
        enabled = True

        def leaves_fake(self, *a):
            raise RuntimeError("boom")

    assert decode_for_store("x", codec=Broken()) is None


# ── store round trip ──────────────────────────────────────────────────────────


def _save(db, wrong="محمود", right="محمد", file_id=FILE, **kw):
    return save_text_correction(
        db, file_id=file_id, user_id=kw.pop("user_id", USER), wrong=wrong, right=right, occurrences=kw.pop("n", 3),
        evidence="الاسم محمد مو محمود", conversation_id="convo-9", message_id="msg-1", **kw,
    )


def test_save_load_revert_round_trip():
    db = FakeSupabase()
    cid = _save(db)
    assert cid
    row = db.tables[tc.TABLE][0]
    assert row["wrong_text"] == "محمود" and row["right_text"] == "محمد" and row["occurrences"] == 3
    assert row["file_id"] == FILE and row["user_id"] == USER and row["evidence"]
    assert row["conversation_id"] == "convo-9" and row["message_id"] == "msg-1"

    got = load_text_corrections(db, [FILE, FILE_2])
    assert list(got) == [FILE]
    assert got[FILE][0] == TextCorrection(id=cid, file_id=FILE, wrong="محمود", right="محمد", occurrences=3,
                                         created_at=None)

    assert revert_text_correction(db, cid, "user-8") is False      # not the owner
    assert revert_text_correction(db, cid, USER) is True
    assert db.tables[tc.TABLE][0]["reverted_at"]
    assert load_text_corrections(db, [FILE]) == {}
    assert revert_text_correction(db, cid, USER) is False          # already reverted


def test_load_oldest_first_and_owner_filter():
    db = FakeSupabase({tc.TABLE: [
        {"id": "b", "file_id": FILE, "user_id": USER, "wrong_text": "ب", "right_text": "ج",
         "occurrences": 1, "created_at": "2026-11-27T10:05:00+00:00", "reverted_at": None},
        {"id": "a", "file_id": FILE, "user_id": USER, "wrong_text": "أ", "right_text": "ب",
         "occurrences": 1, "created_at": "2026-11-27T10:00:00+00:00", "reverted_at": None},
        {"id": "x", "file_id": FILE, "user_id": "user-8", "wrong_text": "س", "right_text": "ص",
         "occurrences": 1, "created_at": "2026-11-27T09:00:00+00:00", "reverted_at": None},
    ]})
    got = load_text_corrections(db, [FILE], user_id=USER)
    assert [c.id for c in got[FILE]] == ["a", "b"]
    assert apply_text_corrections("أ", got[FILE]) == "ج"


@pytest.mark.parametrize("kw", [
    {"wrong": ""}, {"right": " "}, {"wrong": "محمد", "right": "محمد"}, {"wrong": "أ\nب"}, {"file_id": ""},
    {"user_id": ""},
])
def test_save_refuses_bad_shapes(kw):
    db = FakeSupabase()
    assert _save(db, **kw) is None
    assert db.writes() == []


def test_save_and_revert_never_raise():
    db = FakeSupabase(fail={tc.TABLE})
    assert _save(db) is None
    assert revert_text_correction(db, "c1", USER) is False


def test_load_failure_returns_empty_and_backs_off():
    db = FakeSupabase(fail={tc.TABLE})
    assert load_text_corrections(db, [FILE]) == {}
    n = len(db.ops(tc.TABLE))
    assert load_text_corrections(db, [FILE]) == {}      # within the pause: no request
    assert len(db.ops(tc.TABLE)) == n
    tc.reset_backoff()
    db.fail.clear()
    assert load_text_corrections(db, [FILE]) == {}
    assert len(db.ops(tc.TABLE)) == n + 1


def test_load_batches_ids():
    db = FakeSupabase()
    ids = [f"00000000-0000-4000-8000-{i:012d}" for i in range(tc.ID_BATCH + 5)]
    assert load_text_corrections(db, ids) == {}
    assert len(db.ops(tc.TABLE)) == 2


@pytest.fixture
def agent_on(monkeypatch):
    """The attachment agent's flag on for everyone (the readers' gate)."""
    from shared.config import get_settings

    monkeypatch.setattr(get_settings(), "ATTACHMENT_AGENT_ENABLED", True)
    monkeypatch.setattr(get_settings(), "ATTACHMENT_AGENT_USERS", None)


def test_corrections_for_items_flag_off_or_no_user_makes_no_request(monkeypatch):
    from shared.config import get_settings

    db = FakeSupabase({"workspace_items": [{"item_id": FILE_2, "file_id": FILE_2, "user_id": USER}]})
    _save(db, file_id=FILE_2)
    before = len(db.ops("workspace_items")) + len(db.ops(tc.TABLE))
    monkeypatch.setattr(get_settings(), "ATTACHMENT_AGENT_ENABLED", False)
    assert corrections_for_items(db, [FILE_2], user_id=USER) == {}
    monkeypatch.setattr(get_settings(), "ATTACHMENT_AGENT_ENABLED", True)
    monkeypatch.setattr(get_settings(), "ATTACHMENT_AGENT_USERS", None)
    assert corrections_for_items(db, [FILE_2]) == {}                     # no user: skipped
    assert len(db.ops("workspace_items")) + len(db.ops(tc.TABLE)) == before


def test_corrections_for_items_checks_the_backoff_before_the_lookup(agent_on):
    db = FakeSupabase({"workspace_items": [{"item_id": FILE_2, "file_id": FILE_2, "user_id": USER}]})
    tc._backoff.failed()
    before = len(db.ops("workspace_items"))
    assert corrections_for_items(db, [FILE_2], user_id=USER) == {}
    assert len(db.ops("workspace_items")) == before


def test_corrections_for_items_keys_by_conversation_item(agent_on):
    item_g, item_first = "99999999-7777-4000-8000-000000000007", FILE_2
    db = FakeSupabase({"workspace_items": [
        {"item_id": item_g, "file_id": FILE, "user_id": USER},          # a library file in another conversation
        {"item_id": item_first, "file_id": FILE_2, "user_id": USER},    # the file's first item: file_id = item_id
    ]})
    _save(db)
    _save(db, wrong="عمرو", right="عمر", file_id=FILE_2)
    got = corrections_for_items(db, [item_g, item_first, "no-such-item"], user_id=USER)
    assert set(got) == {item_g, item_first}
    assert got[item_g][0].wrong == "محمود" and got[item_first][0].wrong == "عمرو"


def test_corrections_for_items_file_lookup_failure_falls_back_to_item_ids(agent_on):
    db = FakeSupabase(fail={("workspace_items", "select")})
    _save(db, file_id=FILE_2)
    assert set(corrections_for_items(db, [FILE_2], user_id=USER)) == {FILE_2}
