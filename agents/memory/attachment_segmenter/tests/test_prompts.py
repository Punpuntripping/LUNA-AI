"""Prompt v4.4 — byte-identity with the Phase 0 tested text, split mode, messages, schema."""
from __future__ import annotations

import hashlib
import json

import pytest

from agents.memory.attachment_segmenter import prompts as P
from agents.memory.attachment_segmenter.models import SegmentMap
from agents.memory.attachment_segmenter.paragraphs import paragraphize

#: sha256 of ``experiments/run_flash_segmenter.py::instructions_for(t)`` (v4.4),
#: computed from the experiment file on 2026-11-11. ``build_instructions(t)`` must
#: produce exactly these bytes — the per-type text is what Phase 0 tested.
TESTED_INSTRUCTIONS_SHA256 = {
    "claim": "d4cfa70a6ee3c38818eab5c1643da28293addde4efc4bcacf3ba7a727c61d2a9",
    "contract": "94e9eec0c2b5e03ddcc86dfbb65329dac2f8dd15b86b09989e1a4f7cad86748e",
    "expert_report": "cc26c7015bf28aa95cf691eff7876c68660dc8ec3b82a5607c56b4064c217938",
    "financial": "99b0b1fdeaca151f1e759dcaf23ab88f622eb1edff934694ab71a96b374af6b5",
    "generic": "8bd4a4851090696bdcb047663303959688579303b9380a85c79562a0d1d39b37",
    "hearing_minutes": "053cd4d686b209be2bc0a51ea28ccf8dea32d74a8ea2b293ac69cae3590f5e5a",
    "judgment": "7ea250caa88421a7baebd9b710b2f978cb4db192ef36a1d43f3291dc4eda673e",
    "letter": "556bbe3b508b6b10b5be175b03d150530fd9cddbfa7d0a211003ed5c3a886530",
    "memo": "8c163cfd5be8c83b38f0a5cfcbc5e2c3f50425cf33285fdd5ea0f12fb23e053a",
    "official_record": "0a9f23b7f0b08875df4a97135b2423a048bf7353f7db2aea2f9ba4ccd37ce395",
    "reference": "5558caf5831c00ddc07cb16e6bacba374f1a7823241bc81fdb3d7472750247a3",
    "": "d794aa3602a54b7947bf300dac6d3afbb876a88954bb65e64ef8f5a42560e052",
    "chat": "b95d8ab7cfb562699980509084622523c8b16755438e3060209611f1c27d507a",
    "statement": "7b4dea56a6e6bc840e13781965a2d0e6eef3d3cd8844cc778aeaf5d3fc9ae430",
    "user_notes": "7e5203bc7d124eb215b1b252a537e10d717d1cc993f7194d8ff62537d9ecb4b0",
    "other": "c601a115d9b7a73ce2afa2423d639372c9d87231024fe192b453da81c2d7d86d",
}
#: sha256 of the experiment's ``SegmentMap.model_json_schema()`` (json, sort_keys).
TESTED_SCHEMA_SHA256 = "480afcc0c93f033b53199509f14a864f391ae8caef35826df082fe98c56284c8"


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def test_prompt_version():
    assert P.PROMPT_VERSION == "seg-v5.1"


@pytest.mark.parametrize("doc_type", sorted(TESTED_INSTRUCTIONS_SHA256))
def test_whole_document_instructions_are_the_pinned_bytes(doc_type):
    """v5.0 pins (owner principle, 2026-11-12). A wording change must be deliberate:
    re-run scripts/segmentation_preview.py, bump PROMPT_VERSION, then re-pin."""
    assert _sha(P.build_instructions(doc_type)) == TESTED_INSTRUCTIONS_SHA256[doc_type]


def test_none_doc_type_is_unknown_generic():
    assert P.build_instructions(None) == P.build_instructions("")


def test_every_label_list_has_its_type_notes():
    assert set(P.LABELS) == set(P.TYPE_NOTES)
    assert set(P.LABELS) == {
        "judgment", "hearing_minutes", "expert_report", "contract", "memo", "claim",
        "reference", "letter", "financial", "official_record", "generic",
    }
    for t in P.LABELS:
        text = P.build_instructions(t)
        assert P.LABELS[t] in text, t
        assert P.TYPE_NOTES[t] in text, t
        assert f"## Labels for this document type ({t})" in text
        assert "{labels}" not in text and "{type_notes}" not in text and "{doc_type}" not in text


def test_label_relations():
    assert P.LABELS["claim"] == P.LABELS["memo"]
    assert P.LABELS["hearing_minutes"].startswith(P.LABELS["judgment"])
    assert "`attendance`" in P.LABELS["hearing_minutes"] and "`attendance`" not in P.LABELS["judgment"]
    for label in ("`claim_requests`", "`defense_pleas`", "`answers`"):
        assert label in P.LABELS["judgment"]
    assert P.TYPE_NOTES["judgment"] == P.TYPE_NOTES["hearing_minutes"]
    assert "the speaker rule" in P.TYPE_NOTES["judgment"]
    # contract files carry the letter and financial rules for embedded documents
    assert P.TYPE_NOTES["contract"].endswith(P.TYPE_NOTES["financial"])
    assert P.TYPE_NOTES["letter"] in P.TYPE_NOTES["contract"]
    # the criminal-case rule of v4.3 is NOT in v4.4
    assert "complainant" not in P.build_instructions("hearing_minutes").lower()


@pytest.mark.parametrize("doc_type", ["chat", "statement", "user_notes", "other", "", None])
def test_unlisted_types_get_the_generic_list(doc_type):
    text = P.build_instructions(doc_type)
    assert P.type_key(doc_type) == "generic"
    assert P.GENERIC_LABELS in text and P.TYPE_NOTES["generic"] in text
    shown = doc_type or "unknown"
    assert f"## Labels for this document type ({shown})" in text


def test_output_schema_is_the_tested_one():
    schema = SegmentMap.model_json_schema()
    assert _sha(json.dumps(schema, sort_keys=True)) == TESTED_SCHEMA_SHA256
    seg = schema["$defs"]["Segment"]
    assert list(seg["properties"]) == ["from_p", "to_p", "label", "what", "value"]  # what BEFORE value
    assert schema["required"] == ["documents_in_file", "segments", "key_paragraphs"]
    assert list(schema["$defs"]["EmbeddedDoc"]["properties"]) == ["from_p", "to_p", "name"]


# ── split mode ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize("doc_type", sorted(P.LABELS))
def test_part_instructions_do_not_contradict_the_part(doc_type):
    whole = P.build_instructions(doc_type)
    for part, first_end in ((1, None), (2, 7)):
        text = P.build_instructions(doc_type, part=part, a=101 if part == 2 else 1,
                                    b=200 if part == 2 else 100, first_end=first_end)
        assert "covering EVERY paragraph of YOUR PART exactly once" in text
        assert "the first segment starts at the first paragraph of YOUR PART" in text
        assert "the last `to_p` = the last paragraph of YOUR PART" in text
        assert "the first segment starts at 1" not in text
        assert "paragraph count in the first line" not in text
        assert f"## You are mapping ONE PART of a longer document ({part} of 2)" in text
        # everything else is the tested text
        assert P.LABELS[doc_type] in text and P.TYPE_NOTES[doc_type] in text
        assert len(text) > len(whole)
    two = P.build_instructions(doc_type, part=2, a=101, b=200, first_end=7)
    assert "Map ONLY paragraphs 101–200" in two
    assert "first page (paragraphs 1–7)" in two and "is `boilerplate`, not `metadata`" in two
    one = P.build_instructions(doc_type, part=1, a=1, b=100)
    assert "Your part starts at the beginning of the document" in one


def test_split_anchor_must_exist():
    with pytest.raises(ValueError):
        P._replace_once("no anchor here", "covering EVERY paragraph from 1", "x")
    for old, _ in P._PART_COVERAGE:
        assert P.PROMPT.count(old) == 1


# ── messages ─────────────────────────────────────────────────────────────────

CONTENT = "رأس الحكم\nوحيث إن المدعي طلب.\nحكمت الدائرة برفض الدعوى."
PARAS = paragraphize(CONTENT, doc_type="judgment")


def test_whole_message_is_the_tested_layout():
    msg = P.build_user_message(PARAS, doc_type="judgment", title="حكم\nابتدائي", summary="ملخص الحكم", chars=len(CONTENT))
    assert msg == (
        f"# doc_type: judgment | title: حكم ابتدائي | {len(CONTENT)} chars | 3 paragraphs\n"
        "\n"
        "## Summary of the document (context only — never map it)\n"
        "ملخص الحكم\n"
        "\n"
        "## Paragraphs\n"
        "[P001] رأس الحكم\n"
        "[P002] وحيث إن المدعي طلب.\n"
        "[P003] حكمت الدائرة برفض الدعوى.\n"
    )


def test_missing_summary_and_type():
    msg = P.build_user_message(PARAS, doc_type=None, title="x", summary="  ", chars=10)
    assert msg.startswith("# doc_type: unknown | title: x | 10 chars | 3 paragraphs\n")
    assert f"\n{P.NO_SUMMARY}\n" in msg


def test_part_messages():
    paras = paragraphize("\n".join(f"سطر {i}" for i in range(1, 41)))
    one = P.build_part_message(paras, part=1, a=1, b=20, first_end=3, overlap=5,
                               doc_type="memo", title="مذكرة", summary="ملخص", chars=999)
    assert one.splitlines()[0] == "# doc_type: memo | title: مذكرة | 999 chars | 40 paragraphs | part 1 of 2 — you map P001–P020"
    assert P.SUMMARY_HEADING in one and "[P020] " in one and "[P025] " in one and "[P026] " not in one
    two = P.build_part_message(paras, part=2, a=21, b=40, first_end=3, overlap=5,
                               doc_type="memo", title="مذكرة", summary="ملخص", chars=999)
    assert "## First page of the document (context only — already mapped)\n[P001] " in two
    assert "## Context before your part (read only — do not map)\n[P016] " in two
    assert "[P004] " not in two and two.endswith("[P040] سطر 40\n")


# ---------------------------------------------------------------------------
# v5.0 — one principle for the value, the user's words as context
# ---------------------------------------------------------------------------


def test_labels_carry_no_value_arrows():
    """The value comes from the principle in PROMPT, never from a label line."""
    for t, text in P.LABELS.items():
        for value in ("core", "supporting", "metadata", "boilerplate"):
            assert f"→ `{value}`" not in text, (t, value)


def test_the_principle_is_stated_once_in_every_prompt():
    for t in P.LABELS:
        text = P.build_instructions(t)
        assert text.count("is it worth investigating and searching on for this case?") == 1
        assert "**When in doubt, `core`.**" in text
        assert "Never rate a passage `supporting` on a guess" in text
        assert "## What the user said" in text


def test_user_words_block_only_when_given():
    from agents.memory.attachment_segmenter.paragraphs import paragraphize

    paras = paragraphize("سطر أول\nسطر ثان", doc_type="memo")
    with_words = P.build_user_message(
        paras, doc_type="memo", title="مذكرة", summary="ملخص", chars=17,
        user_words="### With the file\nانظر الجدول في الصفحة الثالثة",
    )
    assert P.USER_WORDS_HEADING in with_words
    assert with_words.index(P.SUMMARY_HEADING) < with_words.index(P.USER_WORDS_HEADING) < with_words.index(P.PARAGRAPHS_HEADING)
    without = P.build_user_message(paras, doc_type="memo", title="مذكرة", summary="ملخص", chars=17)
    assert P.USER_WORDS_HEADING not in without
    blank = P.build_user_message(paras, doc_type="memo", title="مذكرة", summary="ملخص", chars=17, user_words="  ")
    assert P.USER_WORDS_HEADING not in blank


def test_part_messages_carry_the_user_words_too():
    from agents.memory.attachment_segmenter.paragraphs import paragraphize

    paras = paragraphize("\n".join(f"فقرة {i}" for i in range(1, 41)), doc_type="memo")
    for part, a, b in ((1, 1, 20), (2, 21, 40)):
        msg = P.build_part_message(
            paras, part=part, a=a, b=b, first_end=3, overlap=5, doc_type="memo",
            title="مذكرة", summary="ملخص", chars=400, user_words="### With the file\nسؤال",
        )
        assert P.USER_WORDS_HEADING in msg and "سؤال" in msg
