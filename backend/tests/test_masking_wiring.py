"""Phase 3 identifier-masking wiring tests (وضع السرية).

Covers the backend seams added in Phase 3:
  * ``preferences_service.get_privacy_masking`` — default True, only explicit
    False disables, resilient on error.
  * ``masking_service.build_turn_codec`` — enabled = env flag AND preference;
    the mapping table loads EVEN WHEN DISABLED (decode always-on).
  * ``masking_service.persist_new_mappings`` — delta-only watermark so the two
    intake persist points never re-insert the same fake.
  * ``masking_service.decode_for_persist`` — publisher exit-decode via the active
    turn codec (store-real invariant).
  * The message_service SSE relay decode mechanism over a fake event stream —
    incl. a fake split across chunks — plus the persist full-text decode.
  * ``orchestrator._load_recent_messages`` history encode + persist.

NOTE: ``backend/tests/`` is globally gitignored (``.gitignore`` line 14,
``tests/``) with a negation only for ``shared/privacy/tests/``. These tests run
locally but are NOT committed — see the Phase 3 report.
"""
from __future__ import annotations

import pytest

from shared.privacy import PrivacyCodec, StreamDecoder
from backend.app.services import masking_service
from backend.app.services.masking_service import (
    active_codec,
    build_turn_codec,
    decode_for_persist,
    decode_text,
    persist_new_mappings,
    reset_active_codec,
    set_active_codec,
)
from backend.app.services.preferences_service import get_privacy_masking


# ---------------------------------------------------------------------------
# Fake Supabase (records inserts, returns configured read rows per table)
# ---------------------------------------------------------------------------


class _Result:
    def __init__(self, data):
        self.data = data


class _Query:
    def __init__(self, table: str, store: dict):
        self._table = table
        self._store = store
        self._op = "select"
        self._payload = None

    def select(self, *_a, **_k):
        return self

    def eq(self, *_a, **_k):
        return self

    def neq(self, *_a, **_k):
        return self

    def is_(self, *_a, **_k):
        return self

    def order(self, *_a, **_k):
        return self

    def limit(self, *_a, **_k):
        return self

    def maybe_single(self):
        return self

    def insert(self, payload):
        self._op = "insert"
        self._payload = payload
        self._store.setdefault("inserts", {}).setdefault(self._table, []).append(payload)
        return self

    def update(self, payload):
        self._op = "update"
        self._payload = payload
        return self

    def delete(self):
        self._op = "delete"
        return self

    def execute(self):
        if self._op == "insert":
            return _Result(self._payload)
        rows = self._store.get("data", {}).get(self._table, [])
        return _Result(list(rows))


class FakeSupabase:
    def __init__(self, data: dict | None = None):
        self.store: dict = {"data": data or {}, "inserts": {}}

    def table(self, name: str) -> _Query:
        return _Query(name, self.store)

    def inserts(self, table: str) -> list:
        return self.store.get("inserts", {}).get(table, [])


# A deterministic codec: real 10-digit ID ↔ a fixed fake (no RNG involved).
_REAL_ID = "1029384756"
_FAKE_ID = "1029111111"


def _fixed_codec(*, enabled: bool = True) -> PrivacyCodec:
    return PrivacyCodec(
        real_to_fake={_REAL_ID: _FAKE_ID},
        fake_to_real={_FAKE_ID: _REAL_ID},
        enabled=enabled,
    )


@pytest.fixture
def active(request):
    """Publish a codec on the ContextVar for the test body; reset after."""
    codec = request.param if hasattr(request, "param") else _fixed_codec()
    token = set_active_codec(codec)
    try:
        yield codec
    finally:
        reset_active_codec(token)


# ---------------------------------------------------------------------------
# 1. get_privacy_masking — default True
# ---------------------------------------------------------------------------


def test_privacy_masking_default_true_when_no_row():
    sb = FakeSupabase(data={"user_preferences": []})
    assert get_privacy_masking(sb, "u1") is True


def test_privacy_masking_default_true_when_key_missing():
    sb = FakeSupabase(data={"user_preferences": [{"preferences": {"detail_level": "high"}}]})
    assert get_privacy_masking(sb, "u1") is True


def test_privacy_masking_explicit_false_disables():
    sb = FakeSupabase(data={"user_preferences": [{"preferences": {"privacy_masking": False}}]})
    assert get_privacy_masking(sb, "u1") is False


def test_privacy_masking_explicit_true_enabled():
    sb = FakeSupabase(data={"user_preferences": [{"preferences": {"privacy_masking": True}}]})
    assert get_privacy_masking(sb, "u1") is True


def test_privacy_masking_null_value_stays_on():
    sb = FakeSupabase(data={"user_preferences": [{"preferences": {"privacy_masking": None}}]})
    assert get_privacy_masking(sb, "u1") is True


def test_privacy_masking_resilient_on_error():
    class _Boom:
        def table(self, *_a):
            raise RuntimeError("db down")

    assert get_privacy_masking(_Boom(), "u1") is True


# ---------------------------------------------------------------------------
# 2. build_turn_codec — enabled = flag AND pref; mappings load even when disabled
# ---------------------------------------------------------------------------


def _sb_with_pref_and_mappings(pref: bool | None, mappings: list[dict]) -> FakeSupabase:
    prefs_rows = [{"preferences": {"privacy_masking": pref}}] if pref is not None else []
    return FakeSupabase(data={"user_preferences": prefs_rows, "pii_mappings": mappings})


def test_build_turn_codec_enabled_when_pref_true(monkeypatch):
    monkeypatch.setattr(
        masking_service, "get_settings", lambda: type("S", (), {"PRIVACY_MASKING_ENABLED": True})()
    )
    sb = _sb_with_pref_and_mappings(True, [])
    codec = build_turn_codec(sb, "u1")
    assert codec.enabled is True


def test_build_turn_codec_disabled_when_pref_false(monkeypatch):
    monkeypatch.setattr(
        masking_service, "get_settings", lambda: type("S", (), {"PRIVACY_MASKING_ENABLED": True})()
    )
    sb = _sb_with_pref_and_mappings(False, [])
    codec = build_turn_codec(sb, "u1")
    assert codec.enabled is False


def test_build_turn_codec_disabled_by_env_flag(monkeypatch):
    monkeypatch.setattr(
        masking_service, "get_settings", lambda: type("S", (), {"PRIVACY_MASKING_ENABLED": False})()
    )
    sb = _sb_with_pref_and_mappings(True, [])
    codec = build_turn_codec(sb, "u1")
    assert codec.enabled is False


def test_build_turn_codec_loads_mappings_even_when_disabled(monkeypatch):
    # Masking OFF but a mapping table exists (captured while ON earlier). Decode
    # must still restore — the codec must load the mapping even when disabled.
    monkeypatch.setattr(
        masking_service, "get_settings", lambda: type("S", (), {"PRIVACY_MASKING_ENABLED": True})()
    )
    sb = _sb_with_pref_and_mappings(
        False, [{"kind": "number", "real_value": _REAL_ID, "fake_value": _FAKE_ID}]
    )
    codec = build_turn_codec(sb, "u1")
    assert codec.enabled is False
    # encode is a passthrough (disabled) ...
    assert codec.encode(f"هويته {_REAL_ID}") == f"هويته {_REAL_ID}"
    # ... but decode still restores the previously-captured fake.
    assert codec.decode(f"هويته {_FAKE_ID}").text == f"هويته {_REAL_ID}"


# ---------------------------------------------------------------------------
# 3. persist_new_mappings — delta watermark
# ---------------------------------------------------------------------------


def test_persist_new_mappings_delta_only():
    sb = FakeSupabase(data={"pii_mappings": []})
    codec = PrivacyCodec(enabled=True)
    # First encode mints one fake.
    codec.encode("رقم الهوية 5012349876")
    n1 = persist_new_mappings(sb, "u1", codec)
    assert n1 == 1
    assert len(sb.inserts("pii_mappings")) == 1

    # No new mapping since last flush → cheap no-op, no extra insert.
    n2 = persist_new_mappings(sb, "u1", codec)
    assert n2 == 0
    assert len(sb.inserts("pii_mappings")) == 1

    # A second distinct value mints another fake; only the delta is persisted.
    codec.encode("ورقم آخر 6612345432")
    n3 = persist_new_mappings(sb, "u1", codec)
    assert n3 == 1
    assert len(sb.inserts("pii_mappings")) == 2


def test_persist_new_mappings_none_codec_is_noop():
    sb = FakeSupabase()
    assert persist_new_mappings(sb, "u1", None) == 0


# ---------------------------------------------------------------------------
# 4. decode_for_persist — publisher exit-decode via active codec
# ---------------------------------------------------------------------------


def test_decode_for_persist_restores_via_active_codec(active):
    encoded = f"العقد يخص هويته {_FAKE_ID} فقط"
    assert decode_for_persist(encoded) == f"العقد يخص هويته {_REAL_ID} فقط"


def test_decode_for_persist_noop_without_active_codec():
    # No codec published → passthrough (nothing matches).
    encoded = f"هويته {_FAKE_ID}"
    assert decode_for_persist(encoded) == encoded


# ---------------------------------------------------------------------------
# 5. message_service SSE relay decode mechanism (fake event stream)
# ---------------------------------------------------------------------------


def _relay(codec, events):
    """Faithful reproduction of the message_service relay decode path:
    StreamDecoder per token delta + finalize() tail on done + full-text persist
    decode of the accumulated (encoded) content. Returns (streamed, persisted).
    """
    sd = StreamDecoder(codec)
    streamed_parts: list[str] = []
    full_content = ""
    persisted = None
    for ev in events:
        if ev["type"] == "token":
            full_content += ev["text"]  # RAW/encoded
            piece = sd.feed(ev["text"])
            if piece:
                streamed_parts.append(piece)
        elif ev["type"] == "done":
            tail = sd.finalize()
            if tail:
                streamed_parts.append(tail)
            persisted = decode_text(codec, full_content, emit=False)
    return "".join(streamed_parts), persisted


def test_relay_decodes_streamed_tokens_and_persists_real():
    codec = _fixed_codec()
    # The pipeline emits the FAKE; the relay must show the user the REAL value.
    events = [
        {"type": "token", "text": f"تم التحقق من هويته {_FAKE_ID} "},
        {"type": "token", "text": "بنجاح."},
        {"type": "done"},
    ]
    streamed, persisted = _relay(codec, events)
    expected = f"تم التحقق من هويته {_REAL_ID} بنجاح."
    assert streamed == expected
    assert persisted == expected


def test_relay_restores_fake_split_across_chunks():
    codec = _fixed_codec()
    # The fake 1029111111 is split across two SSE chunks.
    events = [
        {"type": "token", "text": "الهوية 10291"},
        {"type": "token", "text": "11111 مؤكدة"},
        {"type": "done"},
    ]
    streamed, persisted = _relay(codec, events)
    expected = f"الهوية {_REAL_ID} مؤكدة"
    assert streamed == expected
    assert persisted == expected


def test_relay_flag_off_byte_identical():
    # Masking disabled AND no fakes in the stream (real pipeline never masked) →
    # decode is a no-op and the streamed/persisted text is byte-identical.
    codec = _fixed_codec(enabled=False)
    raw = f"النص العادي بدون تقنيع {_REAL_ID}."
    events = [{"type": "token", "text": raw}, {"type": "done"}]
    streamed, persisted = _relay(codec, events)
    assert streamed == raw
    assert persisted == raw


# ---------------------------------------------------------------------------
# 6. orchestrator._load_recent_messages — history encode + persist
# ---------------------------------------------------------------------------


def test_load_recent_messages_encodes_history_and_persists(active):
    from agents.orchestrator import _load_recent_messages

    sb = FakeSupabase(
        data={
            "messages": [
                {
                    "role": "user",
                    "content": f"سؤالي عن هويته {_REAL_ID}",
                    "artifact_ids": None,
                    "created_at": "2026-07-02T00:00:00Z",
                }
            ],
            "workspace_items": [],
            "pii_mappings": [],
        }
    )
    snaps = _load_recent_messages(sb, "conv1", user_id="u1")
    assert len(snaps) == 1
    # History content the planner sees carries the FAKE, not the real ID.
    assert _FAKE_ID in snaps[0].content
    assert _REAL_ID not in snaps[0].content


def test_load_recent_messages_passthrough_when_disabled():
    from agents.orchestrator import _load_recent_messages

    token = set_active_codec(_fixed_codec(enabled=False))
    try:
        sb = FakeSupabase(
            data={
                "messages": [
                    {
                        "role": "user",
                        "content": f"سؤالي عن هويته {_REAL_ID}",
                        "artifact_ids": None,
                        "created_at": "2026-07-02T00:00:00Z",
                    }
                ],
                "workspace_items": [],
            }
        )
        snaps = _load_recent_messages(sb, "conv1", user_id="u1")
        # Disabled → history is byte-identical (real value passes through).
        assert snaps[0].content == f"سؤالي عن هويته {_REAL_ID}"
    finally:
        reset_active_codec(token)


def test_load_recent_messages_no_active_codec_is_passthrough():
    from agents.orchestrator import _load_recent_messages

    sb = FakeSupabase(
        data={
            "messages": [
                {
                    "role": "user",
                    "content": f"هويته {_REAL_ID}",
                    "artifact_ids": None,
                    "created_at": "2026-07-02T00:00:00Z",
                }
            ],
            "workspace_items": [],
        }
    )
    snaps = _load_recent_messages(sb, "conv1", user_id="u1")
    assert snaps[0].content == f"هويته {_REAL_ID}"


# ===========================================================================
# Phase 3b — full agent-surface coverage
# ===========================================================================


def _part_text(msg) -> str:
    """Pull the text off a Pydantic AI ModelRequest/ModelResponse first part."""
    return msg.parts[0].content


# ---------------------------------------------------------------------------
# Item 1 + 4: router prior-turn history (messages_to_history) + provenance tags
# ---------------------------------------------------------------------------


def test_messages_to_history_encodes_via_active_codec(active):
    from agents.utils.history import messages_to_history

    rows = [
        {"role": "user", "content": f"سؤالي عن هويته {_REAL_ID}", "artifact_ids": None},
        {"role": "assistant", "content": f"جوابي حول {_REAL_ID}", "artifact_ids": None},
    ]
    hist = messages_to_history(rows)
    joined = "\n".join(_part_text(m) for m in hist)
    # The router LLM history carries the FAKE, never the real identifier.
    assert _FAKE_ID in joined
    assert _REAL_ID not in joined


def test_messages_to_history_encodes_provenance_tag_and_body(active):
    from agents.utils.history import messages_to_history

    rows = [
        {"role": "assistant", "content": f"المتن {_REAL_ID}", "artifact_ids": ["item-1"]},
    ]
    # A WI title carrying the real identifier → the assembled provenance tag also
    # gets encoded (single assembly-point encode covers tag + body).
    wi_prov = {"item-1": (3, "agent_writing", f"خطاب بخصوص {_REAL_ID}")}
    hist = messages_to_history(rows, wi_prov)
    text = _part_text(hist[0])
    assert "WI-3" in text                 # provenance tag survived
    assert _FAKE_ID in text               # title + body masked
    assert _REAL_ID not in text


def test_messages_to_history_passthrough_without_codec():
    from agents.utils.history import messages_to_history

    rows = [{"role": "user", "content": f"هويته {_REAL_ID}", "artifact_ids": None}]
    hist = messages_to_history(rows)
    assert _part_text(hist[0]) == f"هويته {_REAL_ID}"


# ---------------------------------------------------------------------------
# Item 3: unfold_workspace_item output encode + persist
# ---------------------------------------------------------------------------


def test_unfold_encode_output_encodes_via_active_codec(active):
    from agents.tool_repository.unfold_workspace_item import _encode_unfold_output

    sb = FakeSupabase(data={"pii_mappings": []})
    out = _encode_unfold_output(sb, "u1", f"المحتوى يذكر هويته {_FAKE_ID}... خطأ")
    # Fixed codec already knows the mapping — encode masks the REAL id if present.
    out2 = _encode_unfold_output(sb, "u1", f"المحتوى يذكر هويته {_REAL_ID}")
    assert _FAKE_ID in out2
    assert _REAL_ID not in out2


def test_unfold_encode_output_persists_new_fakes():
    codec = PrivacyCodec(enabled=True)
    token = set_active_codec(codec)
    try:
        sb = FakeSupabase(data={"pii_mappings": []})
        out = _unfold_encode(sb, "u1", "رقم الهوية 5012349876 مهم جداً")
        assert "5012349876" not in out          # masked
        assert len(sb.inserts("pii_mappings")) == 1  # minted fake persisted
    finally:
        reset_active_codec(token)


def test_unfold_encode_output_passthrough_without_codec():
    sb = FakeSupabase()
    txt = f"هويته {_REAL_ID}"
    assert _unfold_encode(sb, "u1", txt) == txt


def _unfold_encode(sb, user_id, text):
    from agents.tool_repository.unfold_workspace_item import _encode_unfold_output

    return _encode_unfold_output(sb, user_id, text)


# ---------------------------------------------------------------------------
# Item 6: artifact_editor round-trip — anchors match ENCODED, store REAL
# ---------------------------------------------------------------------------


def test_editor_round_trip_anchors_encoded_store_real():
    from agents.tool_repository.edit_supabase_md import EditPair, apply_edits

    codec = _fixed_codec()
    # NB: no money word (مبلغ/ريال/…) near the id — that would trigger the
    # money-exclusion rule and (correctly) leave the number unmasked.
    real_content = f"العقد يخص هويته {_REAL_ID} والعنوان المسجّل."
    # What the editor saw = ENCODED content.
    enc_content = codec.encode(real_content)
    assert _FAKE_ID in enc_content
    # The editor's anchor quotes the ENCODED text it was shown.
    pairs = [EditPair(old_text=f"هويته {_FAKE_ID}", new_text=f"هوية موكّلي {_FAKE_ID}")]
    new_enc, matches = apply_edits(enc_content, pairs)  # anchors match encoded
    assert len(matches) == 1
    # Store-real: decode the applied result before the write.
    stored = decode_text(codec, new_enc, emit=False)
    assert _REAL_ID in stored
    assert _FAKE_ID not in stored
    assert "هوية موكّلي" in stored


# ---------------------------------------------------------------------------
# Item 7: writer package render encode seam
# ---------------------------------------------------------------------------


def test_writer_render_encode_masks_via_active_codec(active):
    from agents.writer.prompts import _encode_for_llm

    out = _encode_for_llm(f"<template>البند يذكر هويته {_REAL_ID}</template>")
    assert _FAKE_ID in out
    assert _REAL_ID not in out


def test_writer_render_encode_passthrough_without_codec():
    from agents.writer.prompts import _encode_for_llm

    txt = f"<source>هويته {_REAL_ID}</source>"
    assert _encode_for_llm(txt) == txt


# ---------------------------------------------------------------------------
# Item 5 + 8: detached-path explicit codec build (summarizer + ingester)
# ---------------------------------------------------------------------------


def _enable_settings(monkeypatch):
    monkeypatch.setattr(
        masking_service, "get_settings",
        lambda: type("S", (), {"PRIVACY_MASKING_ENABLED": True})(),
    )


def test_summarize_codec_builds_explicitly_when_detached(monkeypatch):
    from agents.memory import summarize as sm

    _enable_settings(monkeypatch)
    sb = _sb_with_pref_and_mappings(True, [])
    codec = sm._summarize_codec(sb, "u1")   # no active codec → build from user
    assert codec is not None
    assert codec.enabled is True


def test_summarize_codec_reuses_active(active):
    from agents.memory import summarize as sm

    assert sm._summarize_codec(FakeSupabase(), "u1") is active


def test_summarize_codec_none_without_user():
    from agents.memory import summarize as sm

    assert sm._summarize_codec(FakeSupabase(), "") is None


def test_summarize_enc_dec_roundtrip():
    from agents.memory import summarize as sm

    codec = _fixed_codec()
    assert sm._enc(codec, f"هويته {_REAL_ID}") == f"هويته {_FAKE_ID}"
    assert sm._dec(codec, f"هويته {_FAKE_ID}") == f"هويته {_REAL_ID}"


def test_ingest_codec_builds_explicitly_when_detached(monkeypatch):
    from agents.memory.template_ingester import runner as ing

    _enable_settings(monkeypatch)
    sb = _sb_with_pref_and_mappings(True, [])
    codec = ing._ingest_codec(sb, "u1")
    assert codec is not None
    assert codec.enabled is True


def test_ingest_codec_reuses_active(active):
    from agents.memory.template_ingester import runner as ing

    assert ing._ingest_codec(FakeSupabase(), "u1") is active


def test_ingest_codec_none_without_user():
    from agents.memory.template_ingester import runner as ing

    assert ing._ingest_codec(FakeSupabase(), "") is None


def test_ingest_enc_dec_roundtrip():
    from agents.memory.template_ingester import runner as ing

    codec = _fixed_codec()
    assert ing._enc(codec, f"هويته {_REAL_ID}") == f"هويته {_FAKE_ID}"
    assert ing._dec(codec, f"هويته {_FAKE_ID}") == f"هويته {_REAL_ID}"


# ===========================================================================
# Post-3b acceptance-run leak fixes (masking_acceptance_2026-07-02.md)
# ===========================================================================


def _snap(**kw):
    """Build a WorkspaceItemSnapshot with sensible defaults for the fixtures."""
    from agents.models import WorkspaceItemSnapshot

    base = dict(item_id="it-1", kind="note", title="t", content_md="", summary="", wi_seq=1)
    base.update(kw)
    return WorkspaceItemSnapshot(**base)


# ---------------------------------------------------------------------------
# GAP 1 — deep_search planner_decider renders a force-attached item's inline
# content_md (+ title) eagerly (<attached_items>), no encode. Fix:
# _encode_attached_for_planner masks LLM-only COPIES; caller persists.
# ---------------------------------------------------------------------------


def test_encode_attached_for_planner_masks_content_title_summary(active):
    from agents.orchestrator import _encode_attached_for_planner

    snap = _snap(
        title=f"مذكرة {_REAL_ID}",
        content_md=f"النص الكامل يذكر هويته {_REAL_ID} صراحةً",
        summary=f"ملخص {_REAL_ID}",
    )
    out = _encode_attached_for_planner([snap])
    assert len(out) == 1
    # The planner-bound copy carries the FAKE across every rendered text field.
    assert _FAKE_ID in out[0].content_md and _REAL_ID not in out[0].content_md
    assert _FAKE_ID in out[0].title and _REAL_ID not in out[0].title
    assert _FAKE_ID in out[0].summary and _REAL_ID not in out[0].summary
    # Alias handles (item_id / wi_seq) are untouched so resolution still works.
    assert out[0].item_id == "it-1" and out[0].wi_seq == 1
    # The SHARED snapshot stays REAL — it also feeds user-facing paths.
    assert snap.content_md == f"النص الكامل يذكر هويته {_REAL_ID} صراحةً"
    assert _REAL_ID in snap.title


def test_encode_attached_for_planner_passthrough_without_codec():
    from agents.orchestrator import _encode_attached_for_planner

    snap = _snap(content_md=f"هويته {_REAL_ID}", title=f"عنوان {_REAL_ID}")
    out = _encode_attached_for_planner([snap])
    # No active codec → byte-identical (real values pass through).
    assert out[0].content_md == f"هويته {_REAL_ID}"
    assert out[0].title == f"عنوان {_REAL_ID}"


def test_encode_attached_for_planner_mints_and_persists():
    # A fresh codec mints a NEW fake for the real id in content_md; the caller
    # persists the delta before the decider LLM runs.
    codec = PrivacyCodec(enabled=True)
    token = set_active_codec(codec)
    try:
        from agents.orchestrator import _encode_attached_for_planner

        snap = _snap(content_md="العقد يذكر الهوية 5012349876 هنا")
        out = _encode_attached_for_planner([snap])
        assert "5012349876" not in out[0].content_md          # masked in the copy
        assert snap.content_md == "العقد يذكر الهوية 5012349876 هنا"  # original real
        sb = FakeSupabase(data={"pii_mappings": []})
        assert persist_new_mappings(sb, "u1", codec) == 1
        assert len(sb.inserts("pii_mappings")) == 1
    finally:
        reset_active_codec(token)


def test_planner_render_attached_items_shows_encoded_content(active):
    # End-to-end: the deep_search planner's own render emits the ENCODED body.
    from agents.deep_search_v4.planner.prompts import _render_attached_items
    from agents.orchestrator import _encode_attached_for_planner

    enc = _encode_attached_for_planner(
        [_snap(content_md=f"هويته {_REAL_ID}", title=f"عنوان {_REAL_ID}")]
    )
    block = _render_attached_items(enc)
    assert block is not None
    assert _FAKE_ID in block
    assert _REAL_ID not in block


# ---------------------------------------------------------------------------
# GAP 2a — eager prior_search comprehension (deep_search planner)
# ---------------------------------------------------------------------------


def test_load_prior_search_summaries_encodes_text_fields(active):
    from agents.orchestrator import _load_prior_search_summaries

    sb = FakeSupabase(
        data={
            "workspace_items": [
                {
                    "item_id": "ws-1",
                    "wi_seq": 2,
                    "title": f"بحث {_REAL_ID}",
                    "describe_query": f"استعلام {_REAL_ID}",
                    "summary": f"الخلاصة: الهوية {_REAL_ID}",
                    "metadata": {"confidence": "high"},
                    "created_at": "2026-07-02T00:00:00Z",
                }
            ],
            "pii_mappings": [],
        }
    )
    out = _load_prior_search_summaries(sb, "conv1", "u1")
    assert len(out) == 1
    assert _FAKE_ID in out[0].title and _REAL_ID not in out[0].title
    assert _FAKE_ID in out[0].describe_query and _REAL_ID not in out[0].describe_query
    assert _FAKE_ID in out[0].summary and _REAL_ID not in out[0].summary
    # Alias handles preserved.
    assert out[0].item_id == "ws-1" and out[0].wi_seq == 2


def test_load_prior_search_summaries_persists_new_fakes():
    codec = PrivacyCodec(enabled=True)
    token = set_active_codec(codec)
    try:
        from agents.orchestrator import _load_prior_search_summaries

        sb = FakeSupabase(
            data={
                "workspace_items": [
                    {
                        "item_id": "ws-1",
                        "wi_seq": 2,
                        "title": "بحث",
                        "describe_query": "استعلام",
                        "summary": "رقم الهوية 5012349876 مذكور",
                        "metadata": {"confidence": "medium"},
                        "created_at": "2026-07-02T00:00:00Z",
                    }
                ],
                "pii_mappings": [],
            }
        )
        out = _load_prior_search_summaries(sb, "conv1", "u1")
        assert "5012349876" not in out[0].summary            # masked
        assert len(sb.inserts("pii_mappings")) == 1          # minted fake persisted
    finally:
        reset_active_codec(token)


def test_load_prior_search_summaries_passthrough_without_codec():
    from agents.orchestrator import _load_prior_search_summaries

    sb = FakeSupabase(
        data={
            "workspace_items": [
                {
                    "item_id": "ws-1",
                    "wi_seq": 2,
                    "title": f"بحث {_REAL_ID}",
                    "describe_query": "",
                    "summary": f"هويته {_REAL_ID}",
                    "metadata": {"confidence": "low"},
                    "created_at": "2026-07-02T00:00:00Z",
                }
            ],
        }
    )
    out = _load_prior_search_summaries(sb, "conv1", "u1")
    assert out[0].title == f"بحث {_REAL_ID}"       # no codec → real preserved
    assert out[0].summary == f"هويته {_REAL_ID}"


# ---------------------------------------------------------------------------
# GAP 2b — case memory brief (deep_search planner <case_brief>)
# ---------------------------------------------------------------------------


def test_load_case_brief_encodes_via_active_codec(active, monkeypatch):
    import agents.router.context as rc

    # Bypass the DB shape mismatch of the fake — assert the encode seam directly.
    monkeypatch.setattr(
        rc, "_load_case_block", lambda sb, cid, uid: (None, f"الموكّل هويته {_REAL_ID}")
    )
    from agents.orchestrator import _load_case_brief

    sb = FakeSupabase(data={"pii_mappings": []})
    out = _load_case_brief(sb, "case1", "u1")
    assert out is not None
    assert _FAKE_ID in out and _REAL_ID not in out


def test_load_case_brief_persists_new_fakes(monkeypatch):
    import agents.router.context as rc

    monkeypatch.setattr(
        rc, "_load_case_block", lambda sb, cid, uid: (None, "الموكّل هويته 5012349876")
    )
    codec = PrivacyCodec(enabled=True)
    token = set_active_codec(codec)
    try:
        from agents.orchestrator import _load_case_brief

        sb = FakeSupabase(data={"pii_mappings": []})
        out = _load_case_brief(sb, "case1", "u1")
        assert "5012349876" not in out
        assert len(sb.inserts("pii_mappings")) == 1
    finally:
        reset_active_codec(token)


# ---------------------------------------------------------------------------
# GAP 2c — router eager context (summaries/titles + case memory + compaction)
# ---------------------------------------------------------------------------


def test_load_router_context_encodes_summaries_and_compaction(active):
    from agents.router.context import load_router_context

    sb = FakeSupabase(
        data={
            "workspace_items": [
                {
                    "item_id": "ws-1",
                    "wi_seq": 1,
                    "kind": "agent_search",
                    "title": f"بحث {_REAL_ID}",
                    "summary": f"ملخص هويته {_REAL_ID}",
                    "content_md": "",
                    "created_at": "2026-07-02T00:00:00Z",
                },
                {
                    "item_id": "ws-2",
                    "wi_seq": None,
                    "kind": "convo_context",
                    "title": "",
                    "summary": "",
                    "content_md": f"سياق مضغوط يذكر {_REAL_ID}",
                    "created_at": "2026-07-02T00:01:00Z",
                },
            ],
            "messages": [],
            "conversations": [],
            "user_preferences": [],
            "pii_mappings": [],
        }
    )
    ctx = load_router_context(sb, "u1", "conv1", None)  # case_id=None → skip case block
    s = ctx.workspace_item_summaries[0]
    assert _FAKE_ID in s["title"] and _REAL_ID not in s["title"]
    assert _FAKE_ID in s["summary"] and _REAL_ID not in s["summary"]
    # Alias handles untouched.
    assert s["item_id"] == "ws-1" and s["wi_seq"] == 1
    # Compaction summary (convo_context content_md) is encoded too.
    assert ctx.compaction_summary_md is not None
    assert _FAKE_ID in ctx.compaction_summary_md
    assert _REAL_ID not in ctx.compaction_summary_md


def test_load_router_context_encodes_case_memory(active, monkeypatch):
    import agents.router.context as rc

    monkeypatch.setattr(
        rc,
        "_load_case_block",
        lambda sb, cid, uid: ({"case_name": "x"}, f"ذاكرة القضية هويته {_REAL_ID}"),
    )
    sb = FakeSupabase(
        data={
            "workspace_items": [],
            "messages": [],
            "conversations": [],
            "user_preferences": [],
            "pii_mappings": [],
        }
    )
    ctx = rc.load_router_context(sb, "u1", "conv1", "case1")
    assert ctx.case_memory_md is not None
    assert _FAKE_ID in ctx.case_memory_md and _REAL_ID not in ctx.case_memory_md


def test_load_router_context_passthrough_without_codec():
    from agents.router.context import load_router_context

    sb = FakeSupabase(
        data={
            "workspace_items": [
                {
                    "item_id": "ws-1",
                    "wi_seq": 1,
                    "kind": "agent_search",
                    "title": f"بحث {_REAL_ID}",
                    "summary": f"ملخص {_REAL_ID}",
                    "content_md": "",
                    "created_at": "2026-07-02T00:00:00Z",
                }
            ],
            "messages": [],
            "conversations": [],
            "user_preferences": [],
        }
    )
    ctx = load_router_context(sb, "u1", "conv1", None)
    # No active codec → real values pass through untouched.
    assert ctx.workspace_item_summaries[0]["title"] == f"بحث {_REAL_ID}"
    assert ctx.workspace_item_summaries[0]["summary"] == f"ملخص {_REAL_ID}"


# ---------------------------------------------------------------------------
# GAP 2d — writer_planner render surfaces (attached_items + prior_artifacts)
# ---------------------------------------------------------------------------


def test_writer_planner_render_attached_items_encodes(active):
    from agents.writer_planner.prompts import _render_attached_items

    block = _render_attached_items(
        [_snap(kind="attachment", title=f"عقد {_REAL_ID}", summary=f"ملخص {_REAL_ID}", word_count=5)]
    )
    assert _FAKE_ID in block and _REAL_ID not in block


def test_writer_planner_render_prior_artifacts_encodes(active):
    from agents.writer_planner.prompts import _render_prior_artifacts
    from backend.app.services.writer_planner_context import ArtifactSummaryView

    view = ArtifactSummaryView(
        item_id="it-2",
        kind="note",
        title=f"مذكرة {_REAL_ID}",
        summary=f"الملخص هويته {_REAL_ID}",
        word_count=10,
        created_at="2026-07-02",
        wi_seq=3,
    )
    block = _render_prior_artifacts([view])
    assert _FAKE_ID in block and _REAL_ID not in block
    assert "WI-3" in block  # alias handle survives the encode


def test_writer_planner_render_prior_artifacts_passthrough_without_codec():
    from agents.writer_planner.prompts import _render_prior_artifacts
    from backend.app.services.writer_planner_context import ArtifactSummaryView

    view = ArtifactSummaryView(
        item_id="it-2",
        kind="note",
        title="مذكرة",
        summary=f"هويته {_REAL_ID}",
        word_count=10,
        created_at="2026-07-02",
        wi_seq=3,
    )
    block = _render_prior_artifacts([view])
    assert _REAL_ID in block  # no codec → real passes through


def test_writer_planner_premint_persists_prior_artifact_fakes():
    # The runner pre-mints build_writer_planner_instructions to mint the
    # attached/prior render fakes into the codec, then persists BEFORE the LLM.
    codec = PrivacyCodec(enabled=True)
    token = set_active_codec(codec)
    try:
        from agents.writer_planner.prompts import build_writer_planner_instructions
        from agents.writer_planner.deps import build_writer_planner_deps
        from backend.app.services.writer_planner_context import ArtifactSummaryView

        view = ArtifactSummaryView(
            item_id="it-2",
            kind="note",
            title="مذكرة",
            summary="رقم الهوية 5012349876 مذكور",
            word_count=10,
            created_at="2026-07-02",
            wi_seq=3,
        )
        deps = build_writer_planner_deps(
            supabase=FakeSupabase(),
            user_id="u1",
            conversation_id="c1",
            intent="اكتب مذكرة",
            prior_artifacts=[view],
        )
        text = build_writer_planner_instructions(deps)   # pre-mint render
        assert "5012349876" not in text                  # rendered masked
        sb = FakeSupabase(data={"pii_mappings": []})
        assert persist_new_mappings(sb, "u1", codec) == 1
        assert len(sb.inserts("pii_mappings")) == 1
    finally:
        reset_active_codec(token)
