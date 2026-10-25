"""Tests for ``shared/query_cap.py`` — the block and the ceiling.

The two halves of the editorial query cap that both executors share
(`.claude/plans/expander_query_cap.md` §7.1). What is pinned here:

- the block's **exact text**, because it is a prompt and a silent reword is a
  silent behaviour change nothing else would catch;
- the block says **at most** and never asks for a minimum — the cap is a
  ceiling, and an expander that answers in fewer queries answered correctly;
- the clamp truncates **in emitted order**, channel-tagged queries included —
  any other rule makes which angle of the question survives a coin toss.
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
# clamp_queries — channel-tagged queries are a plain head-slice too
# ===========================================================================


class _TQ:
    """Stand-in for TypedQuery — only ``.channel`` matters to the caller."""

    def __init__(self, text: str, channel: str) -> None:
        self.text = text
        self.channel = channel

    def __repr__(self) -> str:  # pragma: no cover - failure messages only
        return f"_TQ({self.text!r}, {self.channel!r})"


def test_a_single_channel_head_is_not_reshuffled() -> None:
    """The sectioned case path's ≥2-channel rescue is gone
    (case_expander_prompt_4.md D8): channels follow the decision points, so a
    head that lands in one channel is the model's own ranking, kept as is."""
    items = [_TQ(f"p{i}", "principle") for i in range(4)] + [_TQ("f1", "facts")]
    kept, dropped = clamp_queries(items, 2)
    assert [q.text for q in kept] == ["p0", "p1"]
    assert [q.text for q in dropped] == ["p2", "p3", "f1"]
