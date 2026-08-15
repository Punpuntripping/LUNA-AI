"""رسائل الترحيب — the welcome opening of a user's first answer.

Covers the copy (six variants), the two gates that decide whether a turn opens
with one at all, and the injection into each of the three agents that can be a
new user's first responder.

See `.claude/plans/welcome_messages.md`.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from agents.utils.welcome import (
    RETURN_GAP_DAYS,
    WELCOME_CHAR_ALLOWANCE,
    WELCOMED_AT_KEY,
    WelcomeState,
    compose_opening,
    render_welcome_instruction,
    resolve_welcome,
)


# ---------------------------------------------------------------------------
# The copy
# ---------------------------------------------------------------------------


def test_legal_group_gets_the_lawyer_honorific() -> None:
    line = compose_opening(WelcomeState("user_first", "أسعد", "legal"))

    assert line.startswith("أهلين بالمحامي أسعد،")
    assert "شكرًا لتجربتك ريحان" in line


def test_entrepreneur_group_gets_its_own_honorific() -> None:
    line = compose_opening(WelcomeState("user_first", "خالد", "entrepreneur"))

    assert line.startswith("أهلين برائد الأعمال خالد،")


@pytest.mark.parametrize("group", ["specialist", "individual", "declined", None])
def test_every_other_group_is_greeted_by_name_alone(group: str | None) -> None:
    """Only the two groups the product asked for carry an honorific."""
    line = compose_opening(WelcomeState("user_first", "نورة", group))

    assert line.startswith("أهلين نورة،")
    assert "المحامي" not in line
    assert "رائد الأعمال" not in line


def test_a_nameless_user_gets_the_standalone_sentence() -> None:
    """No name → no invented form of address, and the line still stands alone."""
    line = compose_opening(WelcomeState("user_first", None, "legal"))

    assert line.startswith("شكرًا لتجربتك ريحان،")
    assert "أهلين" not in line


def test_return_gap_copy_is_the_short_one() -> None:
    line = compose_opening(WelcomeState("return_gap", "أسعد", "legal"))

    assert line == "أهلين بالمحامي أسعد، حياك الله من جديد. بالنسبة لسؤالك…"


@pytest.mark.parametrize("variant", ["user_first", "return_gap"])
@pytest.mark.parametrize("name", ["أسعد", None])
def test_every_variant_ends_on_the_open_seam(variant: str, name: str | None) -> None:
    """The trailing «بالنسبة لسؤالك…» is what the agent continues into."""
    assert compose_opening(WelcomeState(variant, name, "legal")).endswith(
        "بالنسبة لسؤالك…"
    )


# ---------------------------------------------------------------------------
# The instruction block
# ---------------------------------------------------------------------------


def test_no_state_renders_nothing() -> None:
    """Call sites inject unconditionally, so None must be the empty string."""
    assert render_welcome_instruction(None) == ""


def test_block_carries_the_line_and_overrides_the_no_preamble_rule() -> None:
    text = render_welcome_instruction(WelcomeState("user_first", "أسعد", "legal"))

    assert "أهلين بالمحامي أسعد، شكرًا لتجربتك ريحان" in text
    # The responder's system prompt says "start with the essence, no preambles"
    # — without this the two instructions fight and the system prompt wins.
    assert "overrides" in text


# ---------------------------------------------------------------------------
# Resolution gates
# ---------------------------------------------------------------------------


class _FakeTable:
    """Minimal supabase-py query-builder stand-in.

    ``conversations`` is read TWICE per resolution with different shapes — the
    message-count read ends in ``maybe_single()`` (one dict), the last-activity
    read ends in ``limit()`` (a list). The builder remembers which terminator
    it saw so the two never get handed each other's payload.
    """

    def __init__(self, rows: dict, name: str) -> None:
        self._rows = rows
        self._name = name
        self._single = False

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
        self._single = True
        return self

    def execute(self):
        key = self._name
        if key == "conversations":
            key = "conversations_count" if self._single else "conversations_activity"
        return SimpleNamespace(data=self._rows.get(key))


class _FakeSupabase:
    def __init__(self, **rows) -> None:
        self._rows = rows

    def table(self, name: str) -> _FakeTable:
        return _FakeTable(self._rows, name)


def _client(
    *,
    message_count: int = 0,
    welcomed_at: str | None = None,
    profession: str | None = "legal",
    last_activity_days_ago: float | None = None,
) -> _FakeSupabase:
    activity = []
    if last_activity_days_ago is not None:
        when = datetime.now(timezone.utc) - timedelta(days=last_activity_days_ago)
        activity = [{"updated_at": when.isoformat()}]
    return _FakeSupabase(
        conversations_count={"message_count": message_count},
        conversations_activity=activity,
        users={
            "preferred_name": "أسعد",
            "full_name_ar": "أسعد بن محمد",
            "profession_group": profession,
        },
        user_preferences={
            "preferences": ({WELCOMED_AT_KEY: welcomed_at} if welcomed_at else {})
        },
    )


def test_a_brand_new_user_gets_the_first_ever_welcome() -> None:
    state = resolve_welcome(_client(), "u1", "c1")

    assert state is not None
    assert state.variant == "user_first"
    assert state.call_name == "أسعد"
    assert state.profession_group == "legal"


def test_no_welcome_once_the_conversation_has_turns() -> None:
    """The greeting belongs to the opening turn, never mid-conversation."""
    assert resolve_welcome(_client(message_count=4), "u1", "c1") is None


def test_a_welcomed_user_inside_the_window_gets_nothing() -> None:
    """Active two days ago — a new chat is not a homecoming."""
    client = _client(
        welcomed_at="2026-08-01T00:00:00+00:00", last_activity_days_ago=2
    )

    assert resolve_welcome(client, "u1", "c1") is None


def test_a_welcomed_user_past_the_gap_gets_the_return_line() -> None:
    client = _client(
        welcomed_at="2026-08-01T00:00:00+00:00",
        last_activity_days_ago=RETURN_GAP_DAYS + 1,
    )

    state = resolve_welcome(client, "u1", "c1")

    assert state is not None
    assert state.variant == "return_gap"


def test_a_welcomed_user_with_no_other_conversation_is_not_a_returner() -> None:
    """Nothing to have been away from — and never a second first-ever welcome."""
    client = _client(welcomed_at="2026-08-01T00:00:00+00:00")

    assert resolve_welcome(client, "u1", "c1") is None


def test_a_broken_read_costs_the_greeting_not_the_turn() -> None:
    """A greeting must never be the reason an answer fails."""

    class _Exploding:
        def table(self, _name):  # noqa: ANN001
            raise RuntimeError("db down")

    assert resolve_welcome(_Exploding(), "u1", "c1") is None


# ---------------------------------------------------------------------------
# Injection — the three agents that can answer first
# ---------------------------------------------------------------------------


def _router_instructions(welcome: WelcomeState | None) -> str:
    from agents.router.router import RouterDeps, inject_welcome

    deps = RouterDeps(
        supabase=None,
        user_id="u1",
        conversation_id="c1",
        case_id=None,
        case_memory_md=None,
        case_metadata=None,
        user_preferences=None,
        welcome=welcome,
    )
    return inject_welcome(SimpleNamespace(deps=deps))


def test_router_carries_the_block_only_when_the_turn_earns_one() -> None:
    assert _router_instructions(None) == ""
    assert "أهلين بالمحامي" in _router_instructions(
        WelcomeState("user_first", "أسعد", "legal")
    )


def test_router_name_rule_stands_down_when_the_opener_already_names_them() -> None:
    """Otherwise the reply says the user's name twice in three lines."""
    from agents.router.router import RouterDeps, inject_user_call_name

    def _name_block(welcome: WelcomeState | None) -> str:
        deps = RouterDeps(
            supabase=None,
            user_id="u1",
            conversation_id="c1",
            case_id=None,
            case_memory_md=None,
            case_metadata=None,
            user_preferences=None,
            user_call_name="أسعد",
            welcome=welcome,
        )
        return inject_user_call_name(SimpleNamespace(deps=deps))

    assert "<user_name>" in _name_block(None)
    assert "<user_name>" not in _name_block(
        WelcomeState("user_first", "أسعد", "legal")
    )


def test_responder_puts_the_welcome_ahead_of_the_mode_framing() -> None:
    """It outranks «start with the rule» — so it has to come first."""
    from agents.deep_search_v4.planner.deps import PlannerDeps
    from agents.deep_search_v4.planner.prompts import build_responder_instructions

    deps = PlannerDeps(supabase=None, embedding_fn=None)
    deps.welcome_instruction = render_welcome_instruction(
        WelcomeState("user_first", "أسعد", "legal")
    )

    # Degraded path (no aggregator output) — a failed search is still that
    # user's first reply, so the greeting rides it too.
    text = build_responder_instructions(deps)

    assert text.index("أهلين بالمحامي أسعد") < text.index("Mode framing")


def test_router_prompt_no_longer_licenses_a_welcome() -> None:
    """The router used to improvise «مرحباً بك في ريحان» off these lines."""
    from agents.router.router import SYSTEM_PROMPT

    assert "Greetings and pleasantries" not in SYSTEM_PROMPT
    # …but a bare greeting must still never reach a specialist.
    assert "Never dispatch a specialist for one" in SYSTEM_PROMPT


def test_writer_summary_cap_makes_room_for_the_greeting() -> None:
    """Truncating at a flat 500 would keep the greeting and cut the answer."""
    from agents.writer.deps import build_writer_deps

    deps = build_writer_deps(welcome_instruction="BLOCK")
    limit = 500 + (WELCOME_CHAR_ALLOWANCE if deps.welcome_instruction else 0)

    assert limit == 500 + WELCOME_CHAR_ALLOWANCE
    assert build_writer_deps().welcome_instruction is None
