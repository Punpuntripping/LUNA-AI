"""Attachment flow — what the summary may not claim from extracted text (2026-11-27, owner):
an empty signature field proves nothing (OCR does not read handwriting or stamps), and a
document an email only forwards or mentions is not in the file."""
from __future__ import annotations

import pytest

from agents.memory.artifact_summarizer.prompts import SYSTEM_PROMPT_AR, SYSTEM_PROMPT_ATTACHMENT_AR


@pytest.mark.parametrize("phrase", [
    # signatures
    "OCR does not extract handwriting, signatures or stamps, so an empty signature or stamp field says "
    "nothing about whether the document is signed.",
    "Never write «غير موقّع» from the extracted text alone; say a document is signed or unsigned only when "
    "its own words say so.",
    # a document an email only forwards or mentions
    "A document that an email or letter only forwards, mentions or asks the reader to see",
    "and whose own text is not in the extracted text, is not part of the file",
    "never state its contents, date or consequences.",
    # the title
    "Name only documents whose own text appears: never put in the title a document that an email or letter "
    "merely forwards, mentions or attaches.",
])
def test_attachment_prompt_rule(phrase):
    assert SYSTEM_PROMPT_ATTACHMENT_AR.count(phrase) == 1


def test_rules_stay_in_their_sections():
    p = SYSTEM_PROMPT_ATTACHMENT_AR
    title, summary, link = p.index("### 1) The title"), p.index("### 2) Content summary"), p.index("### 3)")
    assert title < p.index("Name only documents whose own text appears") < summary
    assert summary < p.index("Signatures and stamps:") < link
    assert summary < p.index("A document that an email or letter only forwards") < link


def test_the_generic_flow_is_untouched():
    assert "Signatures and stamps:" not in SYSTEM_PROMPT_AR
