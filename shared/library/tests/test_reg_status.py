"""``shared.library.reg_status`` — the repeal vocabulary.

The whole point of this module is that it stays SILENT for everything except a
repeal. Most tests here assert an empty string, because a false «ملغي» on a law
still in force is the failure mode that would do the most damage.
"""
from __future__ import annotations

import pytest

from shared.library.reg_status import (
    REPEALED_LABEL_AR,
    is_repealed,
    status_line,
)


# Both live corpus spellings of the raw status map to the SAME status_class,
# which is exactly why nothing switches on status_raw (2026-08-24: «لاغي» 24
# rows, «ملغي» 4 rows).
@pytest.mark.parametrize("raw", ["ملغي", "لاغي", "", None])
def test_cancelled_is_repealed_whatever_the_raw_spelling(raw):
    assert is_repealed("cancelled") is True
    assert REPEALED_LABEL_AR in status_line("cancelled", raw)


# Every OTHER live status_class must render nothing at all — including the
# never-enacted consultation states, which are deliberately out of scope here.
@pytest.mark.parametrize(
    "status_class",
    [
        "in_force",
        "in_force_amended",
        "consultation_ended",
        "under_consultation",
        "in_progress",
    ],
)
def test_non_repealed_states_render_nothing(status_class):
    assert is_repealed(status_class) is False
    assert status_line(status_class, "أي نص") == ""


# An unknown / missing status must NOT be read as a repeal: inventing «ملغي»
# for a law that is merely unclassified is worse than staying silent.
@pytest.mark.parametrize("status_class", [None, "", "   ", "some_future_state"])
def test_unknown_status_never_claims_repeal(status_class):
    assert is_repealed(status_class) is False
    assert status_line(status_class, "ملغي") == ""


def test_raw_status_is_appended_when_it_adds_something():
    line = status_line("cancelled", "لاغي")
    assert line == f"{REPEALED_LABEL_AR} (النص الأصلي: لاغي)"


def test_raw_status_is_not_echoed_when_already_in_the_label():
    # «ملغي» is a substring of the label, so appending it would just stutter.
    assert status_line("cancelled", "ملغي") == REPEALED_LABEL_AR


def test_whitespace_is_tolerated():
    assert is_repealed("  cancelled  ") is True
