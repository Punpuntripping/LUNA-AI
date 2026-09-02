"""Byte-identity guard for the ``PROMPT_MODE_*`` body extraction + the
editorial variants built on top of it (plan: ``.claude/plans/blog_subjects.md``
§6).

The editorial (public-blog) path needs the three mode bodies as standalone
constants so ``_EDITORIAL_FORM_AR`` can be spliced in ahead of the citation
footer. Extracting them was purely mechanical — **the in-app prompts must be
unchanged, byte for byte**, because a silent reflow of any of these three
strings would degrade every production search with no error and no log line.

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
    PROMPT_EDITORIAL_CASE,
    PROMPT_EDITORIAL_FULL,
    PROMPT_EDITORIAL_REG,
    PROMPT_MODE_CASE,
    PROMPT_MODE_FULL,
    PROMPT_MODE_REG,
    _CITATION_RULES_AR,
    _COT_TEMPLATE_AR,
    _EDITORIAL_FORM_AR,
    _MODE_CASE_BODY_AR,
    _MODE_FULL_BODY_AR,
    _MODE_REG_BODY_AR,
    _SHARED_ROLE_AR,
    get_aggregator_prompt,
)


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# The pin — captured from the pre-extraction prompts.py
# ---------------------------------------------------------------------------

# (constant, expected length in characters, expected sha256 of the UTF-8 bytes)
PRE_REFACTOR: list[tuple[str, str, int, str]] = [
    (
        "PROMPT_MODE_CASE",
        PROMPT_MODE_CASE,
        13384,
        "e58ed219446410c68c5235586145a248bb44b66bdcd4fc4548f53db9f692fe3f",
    ),
    (
        "PROMPT_MODE_REG",
        PROMPT_MODE_REG,
        16218,
        "393726c6504d119f64c3ce1e590a9d0a1ee188e46d75fd7a2ddb2af75c64146b",
    ),
    (
        "PROMPT_MODE_FULL",
        PROMPT_MODE_FULL,
        14386,
        "a07c868656c468ac6e7e8e59511baebadc46e924b7bb658bc07dd15a9e6bc5f5",
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


# ---------------------------------------------------------------------------
# Editorial variants
# ---------------------------------------------------------------------------


EDITORIAL_KEYS = {
    "prompt_editorial_case": PROMPT_EDITORIAL_CASE,
    "prompt_editorial_reg_compliance": PROMPT_EDITORIAL_REG,
    "prompt_editorial_full": PROMPT_EDITORIAL_FULL,
}


@pytest.mark.parametrize("key", sorted(EDITORIAL_KEYS))
def test_editorial_keys_registered(key: str) -> None:
    assert key in AGGREGATOR_PROMPTS
    assert get_aggregator_prompt(key) is EDITORIAL_KEYS[key]


def test_editorial_twins_cover_every_mode() -> None:
    """``EDITORIAL_PROMPT_KEYS`` must resolve to keys that actually exist.

    The map lives in the pure planner layer, which cannot import the prompt
    registry — so this is where the two halves are checked against each other.
    """
    from agents.deep_search_v4.planner.apply import (
        EDITORIAL_PROMPT_KEYS,
        MODE_PROFILES,
    )

    for profile in MODE_PROFILES.values():
        in_app = profile["aggregator_prompt_key"]
        assert in_app in AGGREGATOR_PROMPTS
        assert in_app in EDITORIAL_PROMPT_KEYS, f"{in_app} has no editorial twin"
        assert EDITORIAL_PROMPT_KEYS[in_app] in AGGREGATOR_PROMPTS


@pytest.mark.parametrize(
    "editorial,in_app",
    [
        (PROMPT_EDITORIAL_CASE, PROMPT_MODE_CASE),
        (PROMPT_EDITORIAL_REG, PROMPT_MODE_REG),
        (PROMPT_EDITORIAL_FULL, PROMPT_MODE_FULL),
    ],
    ids=["case", "reg", "full"],
)
def test_editorial_is_the_mode_prompt_plus_the_form_block(
    editorial: str, in_app: str
) -> None:
    """The ONLY difference is the spliced-in editorial block.

    Removing it must yield the in-app prompt exactly — that is what keeps the
    two paths from drifting apart as the mode bodies evolve.
    """
    assert editorial.count(_EDITORIAL_FORM_AR) == 1
    assert editorial.replace(_EDITORIAL_FORM_AR + "\n", "", 1) == in_app


@pytest.mark.parametrize("key", sorted(EDITORIAL_KEYS))
def test_citation_footer_is_last_and_editorial_block_precedes_it(key: str) -> None:
    """⚠ ``_CITATION_RULES_AR`` goes LAST in every variant — grounding rules at
    the end of the prompt is a deliberate attention-bias choice, and the
    editorial block must never be appended after it."""
    prompt = AGGREGATOR_PROMPTS[key]
    assert prompt.endswith(_CITATION_RULES_AR + "\n")
    assert prompt.count(_CITATION_RULES_AR) == 1
    assert prompt.index(_EDITORIAL_FORM_AR) < prompt.index(_CITATION_RULES_AR)


@pytest.mark.parametrize("key", sorted(EDITORIAL_KEYS))
def test_editorial_part_order(key: str) -> None:
    prompt = AGGREGATOR_PROMPTS[key]
    offsets = [
        prompt.index(part)
        for part in (
            _SHARED_ROLE_AR,
            _EDITORIAL_FORM_AR,
            _COT_TEMPLATE_AR,
            _CITATION_RULES_AR,
        )
    ]
    assert offsets == sorted(offsets)


# ---------------------------------------------------------------------------
# _EDITORIAL_FORM_AR content invariants
# ---------------------------------------------------------------------------


def test_editorial_block_forbids_a_references_section() -> None:
    """«المراجع» is appended programmatically — the model must never write it."""
    assert "Do not write a «المراجع» section" in _EDITORIAL_FORM_AR


def test_editorial_block_uses_western_digits_only() -> None:
    """Arabic-Indic digits inside a `[n]` tag break the clickable reference
    link, so the whole prompt corpus is Western-digits-only."""
    import re

    assert not re.findall(r"[٠-٩۰-۹]", _EDITORIAL_FORM_AR)


def test_editorial_block_overrides_the_no_h1_rule() -> None:
    """Every mode body ends by forbidding an H1. The editorial article needs
    one as its first line, so the override has to be explicit — otherwise the
    model obeys whichever instruction it saw last."""
    assert "main title (H1)" in _EDITORIAL_FORM_AR
    assert "overrides" in _EDITORIAL_FORM_AR


def test_editorial_block_bans_second_person_address() -> None:
    """Rhetorical de-identification — the first of the block's two jobs."""
    for forbidden in ("«سؤالك»", "«حالتك»", "«السائل»"):
        assert forbidden in _EDITORIAL_FORM_AR
