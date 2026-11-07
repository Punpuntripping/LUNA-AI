"""Byte-identity guard for the ``PROMPT_MODE_*`` body extraction.

The three mode bodies were pulled out into standalone constants so the
editorial (public-blog) variants could splice a form block in ahead of the
citation footer. Those variants have since been deleted — article shaping
moved to the marketing side — but the extraction stays, and so does this
guard: **the in-app prompts must be unchanged, byte for byte**, because a
silent reflow of any of these three strings would degrade every production
search with no error and no log line.

The expected values are pinned as SHA-256 digests captured from the
pre-extraction file (commit 2ec6c5c). If a digest fails here, one of two
things happened:

* the extraction was not neutral (whitespace, ordering, an f-string seam), or
* someone deliberately reworded a mode prompt.

The second is legitimate — but it is a real prompt change with real cost and
quality consequences, so it must be a deliberate act: re-capture the digest in
the same commit that changes the wording, and say so in the message. Never
"fix" a red test here by pasting in whatever the code now produces.
"""
from __future__ import annotations

import hashlib

import pytest

from agents.deep_search_v4.aggregator.prompts import (
    AGGREGATOR_PROMPTS,
    PROMPT_MODE_CASE,
    PROMPT_MODE_FULL,
    PROMPT_MODE_REG,
    _CITATION_RULES_AR,
    _COT_TEMPLATE_AR,
    _MODE_CASE_BODY_AR,
    _MODE_FULL_BODY_AR,
    _MODE_REG_BODY_AR,
    _SHARED_ROLE_AR,
    get_aggregator_prompt,
)


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# The pin — captured from the pre-extraction prompts.py; re-captured for the
# deliberate «سنة القضية» ruling-age rule added to _SHARED_ROLE_AR, again for
# the `case_document` rule (case_documents_forwarding.md §5.4), and again for
# the `<conversation_memory>` exception bullet (conversation_memory.md §5): that
# one bullet, 362 characters, is the only change, and each length below grew by
# exactly that. Re-captured 2026-11-05 for the «focus follows what the user
# asked» bullet in _SHARED_ROLE_AR (substance vs procedure, owner rule): 393
# characters, and each length below grew by exactly that.
#
# Re-captured 2026-11-07, deliberately, for official judicial principles
# (.claude/plans/official_principles_refs.md §3.5): _SHARED_ROLE_AR gained the
# `official_principle` type bullet, the principle rule (cite with body + date;
# a contradicting statute governs) and the Article 40 objection rule — +2,671
# characters in every mode prompt. Each mode body gained its own sentence on top:
# case +478 (a principle leads the rulings), reg +330 (principles are allowed
# despite «no court rulings»), full +291 (the bridge between statute and
# precedent). Old → new: case 16957 → 20106, reg 19791 → 22792, full 17959 → 20921.
# ---------------------------------------------------------------------------

# (constant, expected length in characters, expected sha256 of the UTF-8 bytes)
PRE_REFACTOR: list[tuple[str, str, int, str]] = [
    (
        "PROMPT_MODE_CASE",
        PROMPT_MODE_CASE,
        20106,
        "7c5775c4bdd000ba486a61dc8a5af76af4ce9a788946c6fc44e5b6d8e04bf241",
    ),
    (
        "PROMPT_MODE_REG",
        PROMPT_MODE_REG,
        22792,
        "2e106e45e4cffb2e486995713bf4f289e69e6fff333f4582a5a8752f7a783da3",
    ),
    (
        "PROMPT_MODE_FULL",
        PROMPT_MODE_FULL,
        20921,
        "0c223a9a5c90e24d37b6dc319364c96ea7aaf54545167b8aed2eff52380e7c26",
    ),
]


@pytest.mark.parametrize(
    "name,value,expected_len,expected_sha",
    PRE_REFACTOR,
    ids=[row[0] for row in PRE_REFACTOR],
)
def test_mode_prompt_byte_identical_after_body_extraction(
    name: str, value: str, expected_len: int, expected_sha: str
) -> None:
    assert len(value) == expected_len, (
        f"{name} changed length: {len(value)} != {expected_len}. "
        "The body extraction must be neutral — see this module's docstring."
    )
    assert _sha(value) == expected_sha, (
        f"{name} is no longer byte-identical to its pre-extraction value. "
        "See this module's docstring before touching the digest."
    )


@pytest.mark.parametrize(
    "key,value",
    [
        ("prompt_mode_case", PROMPT_MODE_CASE),
        ("prompt_mode_reg_compliance", PROMPT_MODE_REG),
        ("prompt_mode_full", PROMPT_MODE_FULL),
    ],
)
def test_registry_still_serves_the_same_object(key: str, value: str) -> None:
    """The registry is what the aggregator actually reads — pin it too."""
    assert get_aggregator_prompt(key) == value


# ---------------------------------------------------------------------------
# Recomposition — the mode prompt is exactly its four parts
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value,body",
    [
        (PROMPT_MODE_CASE, _MODE_CASE_BODY_AR),
        (PROMPT_MODE_REG, _MODE_REG_BODY_AR),
        (PROMPT_MODE_FULL, _MODE_FULL_BODY_AR),
    ],
    ids=["case", "reg", "full"],
)
def test_mode_prompt_is_exactly_its_parts(value: str, body: str) -> None:
    expected = (
        f"{_SHARED_ROLE_AR}\n{body}\n{_COT_TEMPLATE_AR}\n\n{_CITATION_RULES_AR}\n"
    )
    assert value == expected
