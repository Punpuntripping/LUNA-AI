"""Tests for ``shared/query_cap.py`` — the block and the ceiling.

The two halves of the editorial query cap that both executors share
(`.claude/plans/expander_query_cap.md` §7.1). What is pinned here:

- the block's **exact text**, because it is a prompt and a silent reword is a
  silent behaviour change nothing else would catch;
- the block says **at most** and never asks for a minimum — the cap is a
  ceiling, and an expander that answers in fewer queries answered correctly;
- the clamp truncates **in emitted order** — any other rule makes which angle
  of the question survives a coin toss;
- the sectioned path's ≥2-channel invariant survives a truncation.
"""
from __future__ import annotations

import logging

import pytest

from agents.deep_search_v4.shared.query_cap import clamp_queries, render_cap_block


_LOGGER = "agents.deep_search_v4.shared.query_cap"


# ===========================================================================
# render_cap_block — the exact prompt text
# ===========================================================================


def test_block_text_is_exact() -> None:
    """⚠ This is a PROMPT. Changing the wording changes model behaviour, so the
    text is pinned byte-for-byte rather than probed for keywords."""
    expected = (
        "---\n"
        "## Query count for this run — capped by the editor\n"
        "\n"
        "Produce **at most 2** queries in this run. This caps the \"Number of "
        "queries\" guidance\n"
        "in your instructions for this run only.\n"
        "Order your queries by importance: if you emit more than 2, only the "
        "first 2 are used."
    )
    assert render_cap_block(2) == expected


def test_block_carries_the_number_everywhere_it_matters() -> None:
    block = render_cap_block(9)
    assert "**at most 9**" in block
    # The ordering promise is what makes truncating from the tail fair.
    assert "Order your queries by importance" in block
    assert "if you emit more than 9, only the first 9 are used" in block


def test_block_never_asks_for_a_minimum() -> None:
    """The cap is a CEILING. Any floor language here would make the model pad
    to reach a number, spending real searches on questions it did not think
    worth asking."""
    block = render_cap_block(5).lower()
    assert "no fewer" not in block
    assert "at least" not in block
    assert "between" not in block


def test_block_has_no_leading_or_trailing_blank_line() -> None:
    """Each prompt builder joins it with its own separator; the block must not
    smuggle in a second one."""
    block = render_cap_block(3)
    assert block == block.strip()


# ===========================================================================
# clamp_queries — the ceiling
# ===========================================================================


def test_under_the_ceiling_nothing_is_dropped() -> None:
    items = ["a", "b", "c"]
    kept, dropped = clamp_queries(items, 4)
    assert kept == items
    assert dropped == []


def test_exactly_at_the_ceiling_nothing_is_dropped() -> None:
    items = ["a", "b", "c", "d"]
    kept, dropped = clamp_queries(items, 4)
    assert kept == items
    assert dropped == []


def test_over_the_ceiling_keeps_the_first_n_in_emitted_order() -> None:
    """Emitted order IS priority order — the block told the model so."""
    items = [f"q{i}" for i in range(12)]
    kept, dropped = clamp_queries(items, 4)
    assert kept == ["q0", "q1", "q2", "q3"]
    assert dropped == [f"q{i}" for i in range(4, 12)]
    assert len(kept) + len(dropped) == 12


def test_the_wire_minimum_of_two_still_truncates() -> None:
    """cap=2 is the tightest value the API accepts — the common editorial case."""
    kept, dropped = clamp_queries([f"q{i}" for i in range(9)], 2)
    assert kept == ["q0", "q1"]
    assert len(dropped) == 7


def test_clamp_does_not_mutate_the_input() -> None:
    items = [f"q{i}" for i in range(6)]
    snapshot = list(items)
    clamp_queries(items, 2)
    assert items == snapshot


def test_clamp_logs_the_truncation_at_info(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO, logger=_LOGGER):
        clamp_queries([f"q{i}" for i in range(12)], 4, executor="reg_compliance")
    assert any(
        "reg_compliance expander emitted 12 > 4 — kept 4" in r.getMessage()
        for r in caplog.records
    )


def test_no_log_when_nothing_was_dropped(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO, logger=_LOGGER):
        clamp_queries(["a", "b"], 4, executor="reg_compliance")
    assert caplog.records == []


# ===========================================================================
# clamp_queries — the sectioned ≥2-channel rescue
# ===========================================================================


class _TQ:
    """Stand-in for TypedQuery — only ``.channel`` is read."""

    def __init__(self, text: str, channel: str) -> None:
        self.text = text
        self.channel = channel

    def __repr__(self) -> str:  # pragma: no cover - failure messages only
        return f"_TQ({self.text!r}, {self.channel!r})"


def _channel_of(q: _TQ) -> str:
    return q.channel


def test_sectioned_clamp_rescues_a_second_channel() -> None:
    """A flat head-slice can land entirely in one channel; the prompt forbids it.

    The LAST kept query — least important by the model's own ordering — is the
    one given up, and the count is unchanged.
    """
    items = [
        _TQ("p1", "principle"),
        _TQ("p2", "principle"),
        _TQ("p3", "principle"),
        _TQ("p4", "principle"),
        _TQ("f1", "facts"),
        _TQ("b1", "basis"),
    ]
    kept, dropped = clamp_queries(items, 4, channel_of=_channel_of)

    assert len(kept) == 4
    assert len(dropped) == 2
    assert len({q.channel for q in kept}) >= 2
    # The first dropped query with a NEW channel is the one promoted…
    assert kept[-1].text == "f1"
    # …and the query it displaced is now in the dropped set, not lost.
    assert {q.text for q in kept} | {q.text for q in dropped} == {
        "p1", "p2", "p3", "p4", "f1", "b1"
    }
    assert [q.text for q in kept[:3]] == ["p1", "p2", "p3"]


def test_the_rescue_is_reachable_at_the_wire_minimum() -> None:
    """⚠ This is why the API floors ``cap`` at 2.

    At cap=2 a single-channel head still gets its second channel back. At cap=1
    it could not — which is the whole reason ``cap: 1`` is a 400.
    """
    items = [
        _TQ("p1", "principle"),
        _TQ("p2", "principle"),
        _TQ("f1", "facts"),
    ]
    kept, dropped = clamp_queries(items, 2, channel_of=_channel_of)
    assert len(kept) == 2
    assert len({q.channel for q in kept}) == 2
    assert [q.text for q in kept] == ["p1", "f1"]
    assert [q.text for q in dropped] == ["p2"]


def test_sectioned_clamp_leaves_an_already_mixed_head_alone() -> None:
    """No rescue needed ⇒ no reshuffle: the model's ordering is untouched."""
    items = [
        _TQ("p1", "principle"),
        _TQ("f1", "facts"),
        _TQ("p2", "principle"),
        _TQ("b1", "basis"),
        _TQ("p3", "principle"),
    ]
    kept, dropped = clamp_queries(items, 3, channel_of=_channel_of)
    assert [q.text for q in kept] == ["p1", "f1", "p2"]
    assert [q.text for q in dropped] == ["b1", "p3"]


def test_sectioned_clamp_cannot_invent_a_channel_that_is_not_there() -> None:
    """Single-channel input stays single-channel — nothing to rescue."""
    items = [_TQ(f"p{i}", "principle") for i in range(6)]
    kept, dropped = clamp_queries(items, 2, channel_of=_channel_of)
    assert [q.text for q in kept] == ["p0", "p1"]
    assert len(dropped) == 4


def test_a_ceiling_of_one_keeps_the_top_query_rather_than_reshuffling() -> None:
    """cap=1 never reaches here from the API (it is a 400), but CLI and monitor
    paths can set one by hand. With cap == 1 the ≥2-channel rule is unreachable
    and swapping would only throw away the most important query for nothing."""
    items = [_TQ("p1", "principle"), _TQ("f1", "facts")]
    kept, dropped = clamp_queries(items, 1, channel_of=_channel_of)
    assert [q.text for q in kept] == ["p1"]
    assert [q.text for q in dropped] == ["f1"]


def test_channel_of_is_ignored_on_the_flat_path() -> None:
    """Without ``channel_of`` the clamp is a plain head-slice — the legacy
    case path and the reg path both rely on that."""
    items = [_TQ(f"p{i}", "principle") for i in range(3)] + [_TQ("f1", "facts")]
    kept, _ = clamp_queries(items, 2)
    assert [q.text for q in kept] == ["p0", "p1"]
