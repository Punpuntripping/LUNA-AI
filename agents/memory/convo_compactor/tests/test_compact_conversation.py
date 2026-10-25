"""``agents.memory.agent.compact_conversation`` — the fail-closed DB contract.

This is where the plan's load-bearing rule (§3.5) actually bites: advancing
``conversations.compacted_through_message_id`` behind a summary that does not
exist permanently deletes that span from every downstream agent's context. So
the assertions that matter in most of these tests are **negative**:

    supabase.inserts_into("workspace_items") == []      # no convo_context row
    supabase.updates_to("conversations")     == []      # cutoff not advanced

Deviations from the plan text, verified against the code and pinned here:

* the attempt marker lives on ``messages.metadata.compaction_attempt`` of the
  OLDEST post-cutoff message — ``conversations`` has no ``metadata`` column;
* ``_load_compaction_context`` returns ``None`` (not ``("", [])``) on query
  failure, which is a distinct fail-closed path;
* ``user_call_name`` is deliberately NOT masked.
"""
from __future__ import annotations

import pytest

import agents.memory.agent as memory_agent
from agents.memory.agent import (
    COMPACTION_ATTEMPT_WINDOW_S,
    DEFAULT_COMPACT_FRACTION,
    DEFAULT_COMPACT_MAX_TOKENS,
    _compaction_attempted_recently,
    _load_compaction_context,
    compact_conversation,
)
from agents.memory.convo_compactor.models import CompactionOutput

from .conftest import (
    LONG_SUMMARY_AR,
    arabic_body,
    make_messages,
    make_wi_row,
)

CONV = "conv-1"
USER = "user-1"


def _iso(minutes_ago: int) -> str:
    from datetime import datetime, timedelta, timezone

    return (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).isoformat()


# ---------------------------------------------------------------------------
# 1. Happy path — the only path allowed to write
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_happy_path_inserts_convo_context_and_advances_cutoff(
    pinned_compactor, make_supabase, compaction_tables, tool_model
):
    msgs = make_messages(6, body_chars=120)
    sb = make_supabase(compaction_tables(messages=msgs, workspace_items=[make_wi_row(3)]))
    model, counter = tool_model(LONG_SUMMARY_AR)

    with pinned_compactor.override(model=model):
        item_id = await compact_conversation(sb, CONV, USER, max_tokens=1)

    assert item_id is not None
    assert counter.count == 1

    inserted = sb.inserts_into("workspace_items")
    assert len(inserted) == 1
    row = inserted[0]
    assert row["item_id"] == item_id
    assert row["kind"] == "convo_context"
    assert row["created_by"] == "agent"
    assert row["content_md"] == LONG_SUMMARY_AR
    assert row["summary"] == LONG_SUMMARY_AR      # pre-set so the trigger no-ops
    assert row["is_visible"] is False
    assert row["conversation_id"] == CONV
    assert row["user_id"] == USER

    # fraction 0.60 over 6 messages → batch = messages[0:3], cutoff = m-002
    conv_updates = sb.updates_to("conversations")
    assert conv_updates == [{"compacted_through_message_id": "m-002"}]


@pytest.mark.asyncio
async def test_only_the_oldest_fraction_reaches_the_model(
    pinned_compactor, make_supabase, compaction_tables, tool_model
):
    """The compacted batch is the oldest ``fraction`` — the rest stay in-window."""
    msgs = make_messages(10, body_chars=60)
    sb = make_supabase(compaction_tables(messages=msgs))
    model, counter = tool_model(LONG_SUMMARY_AR)

    with pinned_compactor.override(model=model):
        await compact_conversation(sb, CONV, USER, max_tokens=1, fraction=DEFAULT_COMPACT_FRACTION)

    prompt = counter.last_prompt
    for i in range(6):                       # int(10 * 0.60) == 6
        assert f"[{i}] " in prompt
    for i in range(6, 10):
        assert f"[{i}] " not in prompt
    assert sb.updates_to("conversations") == [{"compacted_through_message_id": "m-005"}]


@pytest.mark.asyncio
async def test_existing_cutoff_excludes_the_already_compacted_span(
    pinned_compactor, make_supabase, compaction_tables, tool_model
):
    """Only messages strictly after the cutoff row's ``created_at`` are seen."""
    msgs = make_messages(10, body_chars=60)
    sb = make_supabase(compaction_tables(messages=msgs, cutoff="m-003"))
    model, counter = tool_model(LONG_SUMMARY_AR)

    with pinned_compactor.override(model=model):
        await compact_conversation(sb, CONV, USER, max_tokens=1)

    prompt = counter.last_prompt
    for i in range(4):
        assert f"[{i}] " not in prompt, "already-compacted messages must not be re-sent"
    # remaining = m-004..m-009 (6 rows) → batch = first int(6*0.6)=3 → m-004..m-006
    assert "[4] " in prompt and "[6] " in prompt
    assert "[7] " not in prompt
    assert sb.updates_to("conversations") == [{"compacted_through_message_id": "m-006"}]


@pytest.mark.asyncio
async def test_user_call_name_is_loaded_and_not_masked(
    pinned_compactor, make_supabase, compaction_tables, tool_model
):
    """The user's call name reaches the prompt verbatim (deliberately unmasked)."""
    sb = make_supabase(
        compaction_tables(messages=make_messages(4, body_chars=80), preferred_name="أبو محمد")
    )
    model, counter = tool_model(LONG_SUMMARY_AR)

    with pinned_compactor.override(model=model):
        await compact_conversation(sb, CONV, USER, max_tokens=1)

    assert "<user_call_name>أبو محمد</user_call_name>" in counter.last_prompt


# ---------------------------------------------------------------------------
# 2. Fail-closed — the critical block
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_llm_failure_fails_closed(
    pinned_compactor, make_supabase, compaction_tables, raising_model
):
    """Agent raises → None, NO convo_context insert, cutoff UNCHANGED.

    The single most important test in this suite: the failure mode it guards
    against silently destroys conversation history.
    """
    sb = make_supabase(
        compaction_tables(messages=make_messages(6, body_chars=120), cutoff="m-000")
    )
    model, counter = raising_model(RuntimeError("provider 503"))

    with pinned_compactor.override(model=model):
        item_id = await compact_conversation(sb, CONV, USER, max_tokens=1)

    assert item_id is None
    assert counter.count >= 1, "the model really was attempted (not skipped by a guard)"
    assert sb.inserts_into("workspace_items") == []
    assert sb.updates_to("conversations") == []
    # And the cutoff row itself is untouched in the table.
    assert sb.tables["conversations"][0]["compacted_through_message_id"] == "m-000"


@pytest.mark.asyncio
async def test_empty_output_fails_closed(
    pinned_compactor, make_supabase, compaction_tables, tool_model
):
    """A whitespace-only summary is not a summary → no insert, no cutoff move."""
    sb = make_supabase(compaction_tables(messages=make_messages(6, body_chars=120)))
    model, _counter = tool_model("   \n\t  ")

    with pinned_compactor.override(model=model):
        item_id = await compact_conversation(sb, CONV, USER, max_tokens=1)

    assert item_id is None
    assert sb.inserts_into("workspace_items") == []
    assert sb.updates_to("conversations") == []
    assert sb.tables["conversations"][0]["compacted_through_message_id"] is None


@pytest.mark.asyncio
async def test_failed_flag_fails_closed_even_with_a_populated_summary(
    make_supabase, compaction_tables, monkeypatch
):
    """``failed=True`` wins over a non-empty ``summary_md``.

    Guards the fail-closed check itself: it must be
    ``failed or not summary_md``, never "trust the text if there is any".
    """
    async def _fake(*_a, **_k):
        return CompactionOutput(summary_md=LONG_SUMMARY_AR, failed=True)

    monkeypatch.setattr(memory_agent, "run_convo_compaction", _fake)
    sb = make_supabase(compaction_tables(messages=make_messages(6, body_chars=120)))

    item_id = await compact_conversation(sb, CONV, USER, max_tokens=1)

    assert item_id is None
    assert sb.inserts_into("workspace_items") == []
    assert sb.updates_to("conversations") == []


@pytest.mark.asyncio
async def test_compactor_raising_out_of_contract_still_fails_closed(
    make_supabase, compaction_tables, monkeypatch
):
    """The runner promises never to raise; ``compact_conversation`` distrusts it anyway."""
    async def _boom(*_a, **_k):
        raise RuntimeError("contract violated")

    monkeypatch.setattr(memory_agent, "run_convo_compaction", _boom)
    sb = make_supabase(compaction_tables(messages=make_messages(6, body_chars=120)))

    item_id = await compact_conversation(sb, CONV, USER, max_tokens=1)

    assert item_id is None
    assert sb.inserts_into("workspace_items") == []
    assert sb.updates_to("conversations") == []


@pytest.mark.asyncio
async def test_load_context_failure_fails_closed(
    pinned_compactor, make_supabase, compaction_tables, never_called_model
):
    """A ``workspace_items`` read error is NOT 'first compaction, no items'.

    ``_load_compaction_context`` returning ``None`` must abort before the call:
    proceeding with an empty ``prior_summary_md`` would orphan the previous
    compaction (plan §1.3) — the exact bug the input was added to prevent.
    """
    sb = make_supabase(compaction_tables(messages=make_messages(6, body_chars=120)))
    sb.raise_on[("workspace_items", "select")] = RuntimeError("PostgREST 500")
    model, counter = never_called_model()

    with pinned_compactor.override(model=model):
        item_id = await compact_conversation(sb, CONV, USER, max_tokens=1)

    assert item_id is None
    assert counter.count == 0, "no LLM call may be spent once the inputs are unavailable"
    assert sb.inserts_into("workspace_items") == []
    assert sb.updates_to("conversations") == []
    # The attempt marker must not have been stamped either — a read failure is
    # not a compaction attempt, and burning the cooldown would delay recovery.
    assert sb.updates_to("messages") == []


def test_load_compaction_context_returns_none_on_query_failure(make_supabase):
    """Unit: the distinct ``None`` sentinel, not a silent ``("", [])``."""
    sb = make_supabase({"workspace_items": []})
    sb.raise_on[("workspace_items", "select")] = RuntimeError("db down")

    assert _load_compaction_context(sb, CONV, USER) is None


def test_load_compaction_context_partitions_prior_summary_and_items(make_supabase):
    """The most recent ``convo_context`` becomes the prior summary; the rest ground WI-{n}."""
    rows = [
        make_wi_row(1, item_id="wi-a", created_at="2026-08-01T00:00:00+00:00"),
        make_wi_row(
            0, item_id="cc-old", kind="convo_context", title="ملخص قديم",
            content_md="ملخص المرحلة الأولى", created_at="2026-08-02T00:00:00+00:00",
        ),
        make_wi_row(
            0, item_id="cc-new", kind="convo_context", title="ملخص أحدث",
            content_md="ملخص المرحلة الثانية", created_at="2026-08-05T00:00:00+00:00",
        ),
        make_wi_row(2, item_id="wi-b", kind="agent_writing", summary=None,
                    created_at="2026-08-06T00:00:00+00:00"),
    ]
    sb = make_supabase({"workspace_items": rows})

    loaded = _load_compaction_context(sb, CONV, USER)
    assert loaded is not None
    prior, items = loaded

    assert prior == "ملخص المرحلة الثانية"
    assert [i["wi_seq"] for i in items] == [1, 2]
    assert all(i["kind"] != "convo_context" for i in items)
    assert items[1]["summary"] is None            # NULL summaries survive as NULL


def test_load_compaction_context_first_compaction_is_empty_string(make_supabase):
    """No ``convo_context`` yet → ``("", items)``, distinguishable from a failure."""
    sb = make_supabase({"workspace_items": [make_wi_row(4)]})
    loaded = _load_compaction_context(sb, CONV, USER)
    assert loaded == ("", [{"wi_seq": 4, "kind": "agent_search",
                            "title": "بحث نظامي", "summary": "ملخّص العنصر",
                            "doc_type": ""}])


def test_load_compaction_context_is_scoped_to_the_owner(make_supabase):
    """Another user's items never ground this conversation's summary."""
    rows = [
        make_wi_row(1, item_id="mine"),
        make_wi_row(2, item_id="theirs", user_id="user-OTHER"),
    ]
    sb = make_supabase({"workspace_items": rows})
    loaded = _load_compaction_context(sb, CONV, USER)
    assert loaded is not None
    assert [i["wi_seq"] for i in loaded[1]] == [1]


# ---------------------------------------------------------------------------
# 3. Threshold gate
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_below_threshold_no_call(
    pinned_compactor, make_supabase, compaction_tables, never_called_model
):
    """Under ``DEFAULT_COMPACT_MAX_TOKENS`` → no LLM call and no writes at all."""
    sb = make_supabase(compaction_tables(messages=make_messages(6, body_chars=100)))
    model, counter = never_called_model()

    with pinned_compactor.override(model=model):
        item_id = await compact_conversation(sb, CONV, USER)   # default max_tokens

    assert item_id is None
    assert counter.count == 0
    assert sb.inserts == []
    assert sb.updates == []


@pytest.mark.asyncio
async def test_above_default_threshold_fires(
    pinned_compactor, make_supabase, compaction_tables, tool_model
):
    """A genuinely long conversation compacts on the DEFAULT threshold.

    Sized to clear 10k tokens under both counters: ``len//4`` when ``tiktoken``
    is absent (60k chars → 15k) and cl100k_base when it is present (Arabic runs
    ~1.3 chars/token → ~46k).
    """
    msgs = make_messages(10, body_chars=6_000)
    sb = make_supabase(compaction_tables(messages=msgs))
    model, counter = tool_model(LONG_SUMMARY_AR)

    with pinned_compactor.override(model=model):
        item_id = await compact_conversation(sb, CONV, USER)

    assert item_id is not None
    assert counter.count == 1
    assert len(sb.inserts_into("workspace_items")) == 1
    assert DEFAULT_COMPACT_MAX_TOKENS == 10_000        # pin the gate the test sized for


@pytest.mark.asyncio
async def test_no_messages_returns_none(
    pinned_compactor, make_supabase, compaction_tables, never_called_model
):
    sb = make_supabase(compaction_tables(messages=[]))
    model, counter = never_called_model()

    with pinned_compactor.override(model=model):
        assert await compact_conversation(sb, CONV, USER, max_tokens=1) is None
    assert counter.count == 0
    assert sb.inserts == []


@pytest.mark.asyncio
async def test_blank_bodies_abort_before_the_call(
    pinned_compactor, make_supabase, compaction_tables, never_called_model
):
    """Rows exist but carry no text → abort, no call, no writes past the marker."""
    msgs = make_messages(4, body_chars=10)
    for m in msgs:
        m["content"] = "   "
    sb = make_supabase(compaction_tables(messages=msgs))
    model, counter = never_called_model()

    with pinned_compactor.override(model=model):
        item_id = await compact_conversation(sb, CONV, USER, max_tokens=1)

    assert item_id is None
    assert counter.count == 0
    assert sb.inserts_into("workspace_items") == []
    assert sb.updates_to("conversations") == []


# ---------------------------------------------------------------------------
# 4. Attempt marker — the retry-storm guard
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_attempt_marker_cooldown(
    pinned_compactor, make_supabase, compaction_tables, never_called_model
):
    """A recent marker on the OLDEST post-cutoff message skips the LLM entirely."""
    msgs = make_messages(6, body_chars=120)
    msgs[0]["metadata"] = {"kind": "chat", "compaction_attempt": {"at": _iso(1)}}
    sb = make_supabase(compaction_tables(messages=msgs))
    model, counter = never_called_model()

    with pinned_compactor.override(model=model):
        item_id = await compact_conversation(sb, CONV, USER, max_tokens=1)

    assert item_id is None
    assert counter.count == 0
    assert sb.inserts == []
    assert sb.updates == []


@pytest.mark.asyncio
async def test_stale_attempt_marker_does_not_block(
    pinned_compactor, make_supabase, compaction_tables, tool_model
):
    """Past the cooldown the conversation retries — the guard is a throttle, not a latch."""
    msgs = make_messages(6, body_chars=120)
    msgs[0]["metadata"] = {
        "kind": "chat",
        "compaction_attempt": {"at": _iso(COMPACTION_ATTEMPT_WINDOW_S // 60 + 5)},
    }
    sb = make_supabase(compaction_tables(messages=msgs))
    model, counter = tool_model(LONG_SUMMARY_AR)

    with pinned_compactor.override(model=model):
        item_id = await compact_conversation(sb, CONV, USER, max_tokens=1)

    assert item_id is not None
    assert counter.count == 1


@pytest.mark.parametrize(
    "marker",
    [
        {},
        {"compaction_attempt": {}},
        {"compaction_attempt": {"at": "not-a-timestamp"}},
        {"compaction_attempt": {"at": None}},
    ],
)
def test_unparseable_marker_never_wedges_compaction_off(marker):
    """A malformed / missing marker means *proceed*, never *skip forever*."""
    assert _compaction_attempted_recently({"metadata": marker}) is False
    assert _compaction_attempted_recently({"metadata": None}) is False
    assert _compaction_attempted_recently({}) is False


@pytest.mark.asyncio
async def test_attempt_marker_is_stamped_before_the_call_and_preserves_metadata(
    pinned_compactor, make_supabase, compaction_tables, raising_model
):
    """The marker is written BEFORE the LLM, read-modify-write over existing metadata.

    ``metadata.kind`` is what the router's message filter reads
    (``agents/router/context.py``) — clobbering it would drop the message from
    the router's window.
    """
    msgs = make_messages(6, body_chars=120)
    msgs[0]["metadata"] = {"kind": "chat", "custom": "keep-me"}
    sb = make_supabase(compaction_tables(messages=msgs))
    model, _counter = raising_model(RuntimeError("provider down"))

    with pinned_compactor.override(model=model):
        await compact_conversation(sb, CONV, USER, max_tokens=1)

    msg_updates = sb.updates_to("messages")
    assert len(msg_updates) == 1
    md = msg_updates[0]["metadata"]
    assert md["kind"] == "chat"
    assert md["custom"] == "keep-me"
    assert "at" in md["compaction_attempt"]
    # And it landed on the anchor row (messages[0]) so the next turn sees it.
    assert sb.tables["messages"][0]["metadata"]["compaction_attempt"]["at"] == \
        md["compaction_attempt"]["at"]


@pytest.mark.asyncio
async def test_marker_write_failure_does_not_block_compaction(
    pinned_compactor, make_supabase, compaction_tables, tool_model
):
    """The marker is best-effort — a failed write must not cost the compaction."""
    sb = make_supabase(compaction_tables(messages=make_messages(6, body_chars=120)))
    sb.raise_on[("messages", "update")] = RuntimeError("write conflict")
    model, counter = tool_model(LONG_SUMMARY_AR)

    with pinned_compactor.override(model=model):
        item_id = await compact_conversation(sb, CONV, USER, max_tokens=1)

    assert item_id is not None
    assert counter.count == 1


# ---------------------------------------------------------------------------
# 5. وضع السرية — encode before the call, decode before persist
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_masking_roundtrip(
    pinned_compactor, make_supabase, compaction_tables, monkeypatch
):
    """Real identifiers never reach the model; fakes never reach the DB.

    Store-real invariant (``summarize.py:60-77``): every LLM-bound surface is
    encoded, and ``summary_md`` is decoded before it is written to
    ``workspace_items.content_md`` / ``summary``.
    """
    from pydantic_ai.messages import ModelResponse, ToolCallPart, UserPromptPart
    from pydantic_ai.models.function import AgentInfo, FunctionModel
    from shared.privacy import PrivacyCodec

    REAL, FAKE = "1010101010", "5050505050"
    codec = PrivacyCodec(real_to_fake={REAL: FAKE}, fake_to_real={FAKE: REAL})
    monkeypatch.setattr(memory_agent, "_summarize_codec", lambda *_a, **_k: codec)

    order: list[str] = []
    import backend.app.services.masking_service as masking_service

    monkeypatch.setattr(
        masking_service,
        "persist_new_mappings",
        lambda *_a, **_k: order.append("persist") or 0,
    )

    prompts: list[str] = []

    def fn(messages, info: AgentInfo) -> ModelResponse:
        order.append("llm")
        for msg in messages:
            for part in getattr(msg, "parts", []):
                if isinstance(part, UserPromptPart):
                    prompts.append(str(part.content))
        # The model echoes the FAKE back inside its summary, as a real one would.
        body = LONG_SUMMARY_AR + f"\n- رقم السجل التجاري محل النزاع: {FAKE}."
        return ModelResponse(
            parts=[ToolCallPart(tool_name=info.output_tools[0].name,
                                args={"summary_md": body})]
        )

    msgs = make_messages(4, body_chars=80)
    msgs[0]["content"] = f"السجل التجاري للشركة هو {REAL} وأرجو مراجعته."
    wi = make_wi_row(3, title=f"عقد السجل {REAL}", summary=f"ملخّص يذكر {REAL}")
    sb = make_supabase(compaction_tables(messages=msgs, workspace_items=[wi]))

    with pinned_compactor.override(model=FunctionModel(fn)):
        item_id = await compact_conversation(sb, CONV, USER, max_tokens=1)

    assert item_id is not None

    # --- encode: the prompt carries the fake, never the real -------------
    prompt = prompts[-1]
    assert FAKE in prompt
    assert REAL not in prompt, "a real identifier reached the LLM"
    # …across every encoded surface, not just message bodies.
    assert f"عقد السجل {FAKE}" in prompt
    assert f"ملخّص يذكر {FAKE}" in prompt

    # --- mappings are flushed BEFORE the call ----------------------------
    assert order.index("persist") < order.index("llm")

    # --- decode: the DB carries the real, never the fake -----------------
    row = sb.inserts_into("workspace_items")[0]
    assert REAL in row["content_md"]
    assert FAKE not in row["content_md"]
    assert REAL in row["summary"]
    assert FAKE not in row["summary"]


@pytest.mark.asyncio
async def test_prior_summary_is_encoded_too(
    pinned_compactor, make_supabase, compaction_tables, monkeypatch, tool_model
):
    """``prior_summary_md`` is an LLM-bound surface and is masked like the rest."""
    from shared.privacy import PrivacyCodec

    REAL, FAKE = "2020202020", "7070707070"
    codec = PrivacyCodec(real_to_fake={REAL: FAKE}, fake_to_real={FAKE: REAL})
    monkeypatch.setattr(memory_agent, "_summarize_codec", lambda *_a, **_k: codec)

    prior = make_wi_row(
        0, item_id="cc-1", kind="convo_context", title="ملخص سابق",
        content_md=f"كان النزاع حول السجل {REAL}.", summary=None,
    )
    sb = make_supabase(
        compaction_tables(messages=make_messages(4, body_chars=80), workspace_items=[prior])
    )
    model, counter = tool_model(LONG_SUMMARY_AR)

    with pinned_compactor.override(model=model):
        await compact_conversation(sb, CONV, USER, max_tokens=1)

    assert FAKE in counter.last_prompt
    assert REAL not in counter.last_prompt


@pytest.mark.asyncio
async def test_no_codec_is_a_passthrough(
    pinned_compactor, make_supabase, compaction_tables, monkeypatch, tool_model
):
    """A ``None`` codec degrades to passthrough rather than breaking the turn."""
    monkeypatch.setattr(memory_agent, "_summarize_codec", lambda *_a, **_k: None)
    sb = make_supabase(compaction_tables(messages=make_messages(4, body_chars=80)))
    model, counter = tool_model(LONG_SUMMARY_AR)

    with pinned_compactor.override(model=model):
        item_id = await compact_conversation(sb, CONV, USER, max_tokens=1)

    assert item_id is not None
    assert counter.count == 1
    assert sb.inserts_into("workspace_items")[0]["content_md"] == LONG_SUMMARY_AR


# ---------------------------------------------------------------------------
# 6. No mocks left behind
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["_mock_item_summary", "_mock_compaction_summary"])
def test_wave_9_mocks_are_gone(name):
    """The hardcoded Wave-9 summary strings must not exist any more."""
    assert not hasattr(memory_agent, name)


def test_message_safety_clip_is_above_the_prompt_clip():
    """The outer clip must never compound with the prompt's own 2000-char clip."""
    from agents.memory.convo_compactor.prompts import _MAX_MESSAGE_CHARS

    assert memory_agent.MESSAGE_SAFETY_CLIP_CHARS > _MAX_MESSAGE_CHARS
    assert arabic_body(10) is not None       # keep the helper import honest
